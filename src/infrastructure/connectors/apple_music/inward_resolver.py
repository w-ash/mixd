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

Persistence runs through the shared planned-write pipeline
(``WritePlanningResolver``); this module owns payload extraction and the
``playparams_catalog_id`` detection label only.
"""

from collections.abc import Mapping
from typing import override

from attrs import evolve

from src.config import get_logger
from src.config.telemetry import phase
from src.domain.entities import Track
from src.domain.entities.shared import JsonValue
from src.domain.matching.content_digest import DigestSide
from src.domain.matching.evaluation_service import TrackMatchEvaluationService
from src.domain.repositories.uow import UnitOfWorkProtocol
from src.infrastructure.connectors._shared.inward_track_resolver import (
    PlannedWrite,
    WritePlanningResolver,
    plan_isrc_write,
)
from src.infrastructure.connectors._shared.successor_resolution import (
    SuccessorAssertion,
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


class AppleMusicInwardResolver(WritePlanningResolver[AppleMusicSong]):
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
        result, failed_ids = await self._persist_planned_writes(
            writes, uow, user_id=user_id
        )
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

    def _plan_write(
        self,
        apple_id: str,
        song: AppleMusicSong,
        isrc: str,
        existing_by_isrc: Mapping[str, Track],
    ) -> PlannedWrite[AppleMusicSong]:
        """One answered, ISRC-carrying id's persist — the shared ISRC arms."""
        service_data: dict[str, JsonValue] = {
            "title": song.attributes.name,
            "artist": song.attributes.artist_name,
            "duration_ms": song.attributes.duration_in_millis or None,
            "isrc": isrc,
        }
        return plan_isrc_write(
            connector=self.connector_name,
            requested_id=apple_id,
            current_id=_current_id(song),
            payload=song,
            duration_ms=song.attributes.duration_in_millis,
            isrc=isrc,
            existing_by_isrc=existing_by_isrc,
            service_data=service_data,
        )

    @override
    def _canonical_payload(
        self, write: PlannedWrite[AppleMusicSong], *, user_id: str
    ) -> Track:
        """The canonical this write creates, keyed on the *current* id.

        A suspect ISRC is stripped — the owner keeps it, and the queued
        review decides later whether the two are one recording.
        """
        track = create_track_from_apple_song(
            write.current_id, write.payload, user_id=user_id
        )
        if write.review is not None and track.isrc:
            track = evolve(track, isrc=None)
        return track

    @override
    def _mapping_metadata(
        self, write: PlannedWrite[AppleMusicSong]
    ) -> dict[str, object]:
        return write.payload.model_dump()

    @override
    def _successor_assertion(
        self, write: PlannedWrite[AppleMusicSong], track: Track
    ) -> SuccessorAssertion | None:
        """Apple's successor assertion — creations only.

        A reuse maps the requested id directly onto its canonical and
        records no successor. The successor arrives as the playParams
        catalog id of the fetched song, hence ``"playparams_catalog_id"``;
        the shared seam owns the batching and the streak-reset rationale
        for keying each event to the *requested* id's connector track.
        """
        if not (write.creates_canonical and write.requested_id_is_stale):
            return None
        return SuccessorAssertion(
            requested_id=write.requested_id,
            returned_id=write.current_id,
            detection="playparams_catalog_id",
            track_id=track.id,
        )
