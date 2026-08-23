"""Apple Music-specific inward track resolver — conservative, ISRC-only.

Apple Music Track ID Resolution Strategy
========================================
Catalog ids are asked about in chunks via ``GET /catalog/{sf}/songs?ids=``
(the client owns chunking), which yields four outcomes:

1. ANSWERED with ISRC: the song is minted or deduped through the ISRC arms
   (reuse the canonical holding the ISRC, defer a suspect collision to
   review, or create a plain new canonical).
2. ANSWERED without ISRC: deliberately NOT minted. The conservative contract
   is ISRC-or-nothing — an ISRC-less song gets a no-match backoff entry
   (present-but-unresolvable; the same clock the v0.13.0 re-resolution
   drain reads) and counts as failed.
3. SUCCESSOR ID via ``playParams.catalogId``: Apple's own assertion that the
   requested id is stale. Dual mapping — primary on the successor id,
   secondary on the requested id (``DIRECT_IMPORT_STALE_ID``) — plus a
   ``substituted`` event, mirroring Spotify's relink shape without the
   search fallback or chunk folding. Dormant in practice: catalog and
   recent-played playParams are ``{id, kind}`` only — ``catalogId`` arrives
   with v0.13 library objects, so this arm waits armed but untraveled until
   then.
4. ABSENT from the response: the same no-match backoff as (2) — truly absent.

Unlike Spotify there is no artist/title search fallback of any kind: an id
Apple cannot account for stays unresolved (``resolved_track_id = NULL`` in
the plays ledger) until a later import or the re-resolution drain retries it.
"""

from collections.abc import Mapping, Sequence
from typing import override

from attrs import define, evolve

from src.config import get_logger
from src.config.constants import MatchMethod
from src.config.telemetry import phase
from src.domain.entities import Track
from src.domain.entities.shared import JsonValue
from src.domain.matching.content_digest import DigestSide
from src.domain.matching.evaluation_service import TrackMatchEvaluationService
from src.domain.matching.isrc_validation import (
    assess_isrc_match_reliability,
    compute_duration_diff_ms,
)
from src.domain.repositories.connector import ConnectorMappingSpec, IsrcCollisionSpec
from src.domain.repositories.uow import UnitOfWorkProtocol
from src.infrastructure.connectors._shared.inward_track_resolver import (
    InwardTrackResolver,
    persist_bulk_with_item_fallback,
)
from src.infrastructure.connectors._shared.successor_resolution import (
    SuccessorAssertion,
    record_substitutions,
)
from src.infrastructure.connectors.apple_music.client import AppleMusicAPIClient
from src.infrastructure.connectors.apple_music.conversions import (
    create_track_from_apple_song,
    normalized_apple_isrc,
)
from src.infrastructure.connectors.apple_music.models import AppleMusicSong
from src.infrastructure.connectors.apple_music.storefront import resolve_storefront

logger = get_logger(__name__)


def _current_id(song: AppleMusicSong) -> str:
    """The id Apple considers current for this song.

    ``playParams.catalogId`` outranks the resource id when present and
    different — on library-flavored resources it carries the catalog
    identity, and on relinked catalog songs it names the successor.
    """
    play_params = song.attributes.play_params
    catalog_id = play_params.catalog_id if play_params else None
    if catalog_id and catalog_id != song.id:
        return catalog_id
    return song.id


@define(frozen=True, slots=True)
class _IsrcCollisionReview:
    """A suspect ISRC collision to queue against the canonical that owns it."""

    owner: Track
    service_data: dict[str, JsonValue]


@define(frozen=True, slots=True)
class _PlannedWrite:
    """One requested id's persist, decided before anything is written."""

    requested_id: str
    song: AppleMusicSong
    match_method: str
    confidence: int
    # An existing canonical already holds this recording's ISRC — map onto it.
    reuse_track: Track | None = None
    # Suspect collision: the ISRC is claimed by an owner whose duration
    # disagrees, so a review is queued and the contested ISRC withheld.
    review: _IsrcCollisionReview | None = None

    @property
    def current_id(self) -> str:
        return _current_id(self.song)

    @property
    def requested_id_is_stale(self) -> bool:
        return self.current_id != self.requested_id

    @property
    def creates_canonical(self) -> bool:
        return self.reuse_track is None

    @property
    def is_substitution(self) -> bool:
        """Did Apple hand back a successor id for the requested one?

        Only creations qualify: a reuse maps the requested id directly onto
        its canonical and records no successor assertion.
        """
        return self.creates_canonical and self.requested_id_is_stale


class AppleMusicInwardResolver(InwardTrackResolver):
    """Resolves Apple Music catalog ids → canonical tracks (ISRC-only)."""

    _client: AppleMusicAPIClient

    def __init__(
        self,
        client: AppleMusicAPIClient,
        match_evaluation_service: TrackMatchEvaluationService | None = None,
    ):
        super().__init__(match_evaluation_service)
        self._client = client

    @property
    @override
    def connector_name(self) -> str:
        # Data-plane service name — mappings, plays, recorder rows, and the
        # play-import registry all key on "apple". The "apple_music" package
        # key stays control-plane (settings, token storage, rate limiter via
        # the client's own package-derived service name).
        return "apple"

    @override
    def _normalize_id(self, raw_id: str) -> str:
        return str(raw_id).strip()

    @override
    async def _create_tracks_batch(
        self,
        missing_ids: list[str],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> dict[str, Track]:
        """Fetch songs from the catalog in batch, mint tracks + mappings.

        ISRC-or-nothing: an answered song without a usable ISRC creates
        nothing and joins the truly-absent ids on the no-match backoff clock.
        """
        storefront = await resolve_storefront(self._client)
        if storefront is None:
            # Our problem (missing authorization / Apple unreachable), not the
            # ids' — no backoff is recorded, the next import retries in full.
            logger.warning(
                f"No Apple Music storefront available — "
                f"{len(missing_ids)} ids left unresolved"
            )
            return {}

        async with phase("api"):
            lookup = await self._client.get_songs_by_ids(storefront, missing_ids)
        # Ids in a failed chunk are UNANSWERED, not absent from the catalog —
        # same doctrine as the storefront arm above: our problem, not the
        # ids'. No backoff is recorded for them; the next import retries.
        unanswered_ids = set(lookup.failed_values)
        if unanswered_ids:
            logger.warning(
                f"{len(unanswered_ids)} Apple Music catalog ids unanswered "
                f"(chunk request failed) — no backoff, retried next import"
            )

        songs_by_id: dict[str, AppleMusicSong] = {}
        for song in lookup.songs:
            songs_by_id.setdefault(song.id, song)

        answered = [aid for aid in missing_ids if aid in songs_by_id]
        with_isrc = {
            aid: isrc
            for aid in answered
            if (isrc := normalized_apple_isrc(songs_by_id[aid])) is not None
        }

        existing_by_isrc: dict[str, Track] = {}
        if with_isrc:
            existing_by_isrc = await uow.get_track_repository().find_tracks_by_isrcs(
                list(dict.fromkeys(with_isrc.values())), user_id=user_id
            )

        writes = [
            self._plan_write(aid, songs_by_id[aid], with_isrc[aid], existing_by_isrc)
            for aid in with_isrc
        ]
        result, failed_ids = await self._persist_writes(writes, uow, user_id=user_id)
        if failed_ids:
            logger.warning(
                f"{len(failed_ids)} Apple Music ids answered but failed to "
                f"persist — retried next import"
            )

        # One backoff clock for both unresolvable shapes: songs Apple answered
        # for but that carry no ISRC (present-but-unresolvable under the
        # conservative contract), and ids ABSENT from a successful chunk's
        # response. Unanswered ids (failed chunks) are excluded — they were
        # never asked-and-denied. The entries are what the v0.13.0
        # re-resolution drain reads.
        no_isrc_ids = [aid for aid in answered if aid not in with_isrc]
        absent_ids = [
            aid
            for aid in missing_ids
            if aid not in songs_by_id and aid not in unanswered_ids
        ]
        unresolvable = no_isrc_ids + absent_ids
        if unresolvable:
            _ = await uow.get_resolution_recorder().remember_no_match(
                [
                    self._no_match_side(aid, songs_by_id.get(aid))
                    for aid in unresolvable
                ],
                user_id=user_id,
                connector_name=self.connector_name,
            )

        if result:
            _ = await uow.get_resolution_recorder().clear_negatives(
                list(result), user_id=user_id, connector_name=self.connector_name
            )

        return result

    def _no_match_side(self, apple_id: str, song: AppleMusicSong | None) -> DigestSide:
        """What is known about an unresolvable id — song metadata if answered."""
        if song is None:
            return DigestSide(identifier=apple_id)
        return DigestSide(
            identifier=apple_id,
            title=song.attributes.name,
            artists=(song.attributes.artist_name,)
            if song.attributes.artist_name
            else (),
            duration_ms=song.attributes.duration_in_millis or None,
        )

    @staticmethod
    def _plan_write(
        apple_id: str,
        song: AppleMusicSong,
        isrc: str,
        existing_by_isrc: Mapping[str, Track],
    ) -> _PlannedWrite:
        """Decide what one answered, ISRC-carrying id persists as. Pure.

        Three outcomes (Spotify's shape minus the relink-lookup arm): reuse
        the canonical that owns this ISRC, defer a suspect collision to
        review and create a distinct canonical without the contested ISRC,
        or create a plain new canonical.
        """
        existing = existing_by_isrc.get(isrc)
        if existing is not None:
            duration_diff_ms = compute_duration_diff_ms(
                song.attributes.duration_in_millis, existing.duration_ms
            )
            if not assess_isrc_match_reliability(duration_diff_ms).suspect:
                return _PlannedWrite(
                    requested_id=apple_id,
                    song=song,
                    match_method=MatchMethod.ISRC_MATCH,
                    confidence=MatchMethod.ISRC_MATCH_CONFIDENCE,
                    reuse_track=existing,
                )

            service_data: dict[str, JsonValue] = {
                "title": song.attributes.name,
                "artist": song.attributes.artist_name,
                "duration_ms": song.attributes.duration_in_millis or None,
                "isrc": isrc,
            }
            logger.info(
                f"ISRC suspect: queueing review for apple:{apple_id} vs canonical "
                f"{existing.id} (ISRC={isrc}, duration_diff_ms={duration_diff_ms})"
            )
            return _PlannedWrite(
                requested_id=apple_id,
                song=song,
                match_method=MatchMethod.DIRECT_IMPORT,
                confidence=100,
                review=_IsrcCollisionReview(owner=existing, service_data=service_data),
            )

        return _PlannedWrite(
            requested_id=apple_id,
            song=song,
            match_method=MatchMethod.DIRECT_IMPORT,
            confidence=100,
        )

    async def _persist_writes(
        self,
        writes: list[_PlannedWrite],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> tuple[dict[str, Track], set[str]]:
        """Write a chunk's resolutions — one savepoint for all, per id on failure."""

        def _log_failed_write(write: _PlannedWrite, e: Exception) -> None:
            logger.error(
                f"Failed to create track for apple:{write.requested_id}: {e}",
                exc_info=e,
            )

        return await persist_bulk_with_item_fallback(
            writes,
            uow,
            persist=lambda chunk: self._persist_writes_bulk(
                chunk, uow, user_id=user_id
            ),
            write_key=lambda write: write.requested_id,
            describe="resolved Apple Music tracks",
            on_item_failure=_log_failed_write,
        )

    def _canonical_payload(self, write: _PlannedWrite, *, user_id: str) -> Track:
        """The canonical this write creates, keyed on the *current* id.

        A suspect ISRC is stripped — the owner keeps it, and the queued
        review decides later whether the two are one recording.
        """
        track = create_track_from_apple_song(
            write.current_id, write.song, user_id=user_id
        )
        if write.review is not None and track.isrc:
            track = evolve(track, isrc=None)
        return track

    async def _persist_writes_bulk(
        self,
        writes: Sequence[_PlannedWrite],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> dict[str, Track]:
        """Write every resolution in the chunk through the batch primitives."""
        connector_repo = uow.get_connector_repository()

        collisions = [
            IsrcCollisionSpec(
                owner=write.review.owner,
                connector_id=write.requested_id,
                service_data=write.review.service_data,
            )
            for write in writes
            if write.review is not None
        ]
        if collisions:
            _ = await connector_repo.queue_isrc_collision_reviews(
                collisions, self.connector_name, user_id=user_id
            )

        created = [write for write in writes if write.creates_canonical]
        saved = await uow.get_track_repository().save_tracks([
            self._canonical_payload(write, user_id=user_id) for write in created
        ])
        canonicals: dict[str, Track] = {
            write.requested_id: track
            for write, track in zip(created, saved, strict=True)
        }
        canonicals.update({
            write.requested_id: write.reuse_track
            for write in writes
            if write.reuse_track is not None
        })

        _ = await connector_repo.map_tracks_to_connectors(
            self._mapping_batch(writes, canonicals)
        )

        await self._record_substitutions(
            [write for write in writes if write.is_substitution],
            canonicals,
            uow,
            user_id=user_id,
        )
        return canonicals

    def _mapping_batch(
        self,
        writes: Sequence[_PlannedWrite],
        canonicals: Mapping[str, Track],
    ) -> list[ConnectorMappingSpec]:
        """Every mapping the chunk owes, each saying whether it holds primacy.

        A creation maps the *current* id with primacy, plus a non-primary
        stale-id mapping on the requested id when they diverge (cache for
        future imports). An ISRC reuse maps the requested id with primacy.
        One spec per connector id — two requested ids can share a successor.
        """
        specs: list[ConnectorMappingSpec] = []
        claimed: set[str] = set()

        def claim(spec: ConnectorMappingSpec) -> None:
            if spec.connector_id in claimed:
                return
            claimed.add(spec.connector_id)
            specs.append(spec)

        for write in writes:
            track = canonicals[write.requested_id]
            claim(
                ConnectorMappingSpec(
                    track=track,
                    connector=self.connector_name,
                    connector_id=(
                        write.requested_id
                        if write.reuse_track is not None
                        else write.current_id
                    ),
                    match_method=write.match_method,
                    confidence=write.confidence,
                    metadata=write.song.model_dump(),
                    primary=True,
                )
            )
            if write.is_substitution:
                claim(
                    ConnectorMappingSpec(
                        track=track,
                        connector=self.connector_name,
                        connector_id=write.requested_id,
                        match_method=MatchMethod.DIRECT_IMPORT_STALE_ID,
                        confidence=write.confidence,
                    )
                )
        return specs

    async def _record_substitutions(
        self,
        writes: list[_PlannedWrite],
        canonicals: Mapping[str, Track],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> None:
        """Record successor-id detections as ``substituted`` events.

        THE single substitution-recording seam for this connector, delegating
        to the shared successor seam
        (``_shared/successor_resolution.record_substitutions``), which owns
        the batching and the streak-reset rationale for keying each event to
        the *requested* id's connector track. Detection stays here: the
        successor arrives as the playParams catalog id of the fetched song,
        hence ``"playparams_catalog_id"``.
        """
        if not writes:
            return
        await record_substitutions(
            uow.get_resolution_recorder(),
            connector_name=self.connector_name,
            assertions=[
                SuccessorAssertion(
                    requested_id=write.requested_id,
                    returned_id=write.current_id,
                    detection="playparams_catalog_id",
                    track_id=canonicals[write.requested_id].id,
                )
                for write in writes
            ],
            user_id=user_id,
        )
