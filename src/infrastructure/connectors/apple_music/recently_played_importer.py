"""Apple Music recently-played importer — the ``apple_api`` play channel.

Polls ``GET /v1/me/recent/played/tracks`` and writes ledger rows on the
``("apple", "apple_api")`` channel. Unlike every other play source, Apple's
feed reports WHAT played but never WHEN — no timestamps, no listened
durations — so this importer's job is honest timestamping:

- **Prefix-diff dedupe.** The checkpoint stores a fingerprint of the previous
  window's ordered song ids. On each poll, the new plays are the items in
  front of the first position where that fingerprint's head sequence
  re-appears. The FIRST poll seeds the fingerprint and emits ZERO
  observations — inventing timestamps for historical items would be
  dishonest.
- **Midpoint stamping.** Every new item's ``played_at`` is the midpoint of
  the interval it must have happened in: between the previous poll and this
  one. All items in one poll share the midpoint (the feed gives no order in
  time within the window); the channel's wide ``tolerance_override`` in
  ``CHANNEL_SPECS`` is what lets the projection pair these guesses with
  observed timestamps from other channels.
- **No ms_played, ever.** Survivorship takes the first non-null ms_played,
  so a synthetic stand-in would win merges and corrupt listening-time stats.

API behavior this module depends on:

- Page limit is 30 (``limit=50`` → 400 with code ``"40005"``); paging is
  offset-based via ``next`` (e.g. ``/v1/me/recent/played/tracks?offset=30
  &types=songs``). The window is at least 60 items deep.
- The window holds UNIQUE songs only: Apple collapses repeats, so a
  re-listen MOVES an item to the head instead of prepending a duplicate.
  When the fingerprint is never re-found within ``MAX_TURNOVER_PAGES``
  (full turnover), at most the FIRST page ingests — items deeper than page
  1 are near-certainly old window content the lost boundary can no longer
  prove old — and any item whose song id appears in the previous
  fingerprint is conflict-skipped rather than re-ingested at a fresh
  midpoint. The honest floor: a true re-listen of one of the ~30
  fingerprinted songs may be missed on turnover until it ages out of the
  fingerprint, and a genuinely new play buried past page 1 of a turned-over
  window is dropped — but no moved or deep item can mint phantom permanent
  plays. Duplicate ids across polls on the normal prefix-diff path are
  tolerated by design — the ledger's dedup constraint conflict-skips exact
  repeats only.
- An empty window response while a fingerprint is established is treated as
  anomalous: the previous fingerprint is kept (an empty overwrite would make
  the next poll read the whole window as new) and nothing ingests.
"""

from datetime import datetime
import json
from typing import Final, override

from attrs import evolve
from pydantic import AwareDatetime, BaseModel, ValidationError

from src.config import get_logger
from src.domain.entities import ConnectorTrackPlay
from src.domain.entities.progress import ProgressEmitter
from src.domain.entities.shared import JsonValue
from src.domain.repositories.play import (
    AppleRecentImportParams,
    PlayImporterProtocol,
)
from src.domain.repositories.uow import UnitOfWorkProtocol
from src.infrastructure.connectors.apple_music.client import AppleMusicAPIClient
from src.infrastructure.connectors.apple_music.models import AppleMusicSong
from src.infrastructure.services.base_play_importer import BasePlayImporter

logger = get_logger(__name__).bind(service="apple_recently_played")

_IMPORT_SOURCE: Final = "apple_api"

# The stored fingerprint is capped at one page — the window the next poll's
# first page can be diffed against.
FINGERPRINT_LENGTH: Final = 30

# How many pages to walk looking for the fingerprint before declaring full
# turnover. 3 pages of 30 cover up to 90 items of a window at least 60 deep,
# and the window's unique-songs semantics (a repeat moves, never duplicates)
# keep genuine turnover slow.
MAX_TURNOVER_PAGES: Final = 3

# How many of the previous fingerprint's head ids must re-appear contiguously
# to count as "the previous window resumes here". Longer than 1 so a single
# repeated song cannot fake the boundary; short enough that the tail of the
# previous window aging out does not defeat the match.
_OVERLAP_CONFIRM_LENGTH: Final = 3

_FINGERPRINT_KEY: Final = "fingerprint"
_POLLED_AT_KEY: Final = "polled_at"


def _find_boundary(window_ids: list[str], fingerprint: list[str]) -> int | None:
    """Index where the previous window's head sequence re-appears, or None.

    The items before that index are this poll's new plays. An empty
    fingerprint (a previously-empty window) has no head sequence to find —
    None here routes the caller into its no-boundary arm (first page new,
    fingerprinted ids conflict-skipped).
    """
    if not fingerprint:
        return None
    head = fingerprint[: min(len(fingerprint), _OVERLAP_CONFIRM_LENGTH)]
    for i in range(len(window_ids) - len(head) + 1):
        if window_ids[i : i + len(head)] == head:
            return i
    return None


class AppleMusicRecentlyPlayedImporter(
    BasePlayImporter[AppleMusicSong, AppleRecentImportParams],
    PlayImporterProtocol,
):
    """Imports the trailing window of Apple Music plays as ``apple_api`` rows."""

    operation_name: str

    # import_plays comes from BasePlayImporter's shared shell: it narrows
    # params to this type, runs the pipeline, and closes an owned client.
    # AppleMusicAuthRequiredError is raised through the client when no Music
    # User Token is stored or Apple rejected it — there is no cheaper
    # precheck than the call itself (MUTs carry no scopes).
    _params_type = AppleRecentImportParams

    def __init__(self, client: AppleMusicAPIClient | None = None) -> None:
        """Initialize with an optional injected client (tests supply a double)."""
        self.operation_name = "Apple Music Recently Played Import"
        self._client = self._adopt_client(client, AppleMusicAPIClient)
        # Per-run fetch state, read by _process_data / _handle_checkpoints.
        # Safe as instance state: the registry mints a fresh importer per
        # import, and _fetch_data resets all three at its top.
        self._poll_time: datetime | None = None
        self._played_at: datetime | None = None
        self._pending_fingerprint: list[str] | None = None

    @override
    async def _fetch_data(
        self,
        params: AppleRecentImportParams,
        *,
        uow: UnitOfWorkProtocol,
        user_id: str,
        batch_id: str,
        import_timestamp: datetime,
        progress_emitter: ProgressEmitter | None = None,
        operation_id: str | None = None,
    ) -> list[AppleMusicSong]:
        """Fetch the window and prefix-diff it against the stored fingerprint.

        ``import_timestamp`` is the run's "now" and therefore this poll's
        time — reusing it (rather than a second clock read) keeps the
        midpoint, the fingerprint's ``polled_at``, and the rows'
        ``import_timestamp`` one consistent instant.

        Returns only the NEW items; a first poll (or a ``force`` re-seed)
        returns nothing and merely seeds the fingerprint.
        """
        _ = batch_id, progress_emitter, operation_id
        self._poll_time = import_timestamp
        self._played_at = None
        self._pending_fingerprint = None

        previous = None if params.force else await self._read_poll_state(user_id, uow)

        window: list[AppleMusicSong] = []
        new_items: list[AppleMusicSong] = []
        boundary_found = False
        first_page_size = 0
        offset = 0
        for page in range(MAX_TURNOVER_PAGES):
            response = await self._client.get_recently_played(offset=offset)
            if response is None:
                # _SUPPRESS_ERRORS turns transport/status failures into None,
                # so this is the only place a buried failure can be caught.
                # Reading it as "nothing new" would advance the poll time over
                # an interval nothing was actually observed in.
                raise RuntimeError(
                    "Apple Music returned no recently-played response — the "
                    "connection may need re-authorizing, or the API is "
                    "unavailable."
                )
            if page == 0:
                first_page_size = len(response.data)
            window.extend(response.data)
            if previous is None:
                # Seeding: the fingerprint is one page deep by definition —
                # walking further would only fetch items it cannot hold.
                break
            boundary = _find_boundary(
                [song.id for song in window], previous.fingerprint
            )
            if boundary is not None:
                new_items = window[:boundary]
                boundary_found = True
                break
            if not response.data or response.next is None:
                # The feed is exhausted without a boundary — the no-boundary
                # cap below decides what ingests.
                break
            offset += len(response.data)

        if previous is not None and previous.fingerprint and not window:
            # A 200 with no items while a fingerprint is established is
            # anomalous — Apple's window does not empty out; overwriting the
            # fingerprint with [] would make the NEXT poll ingest the whole
            # returned window as new plays. Keep the previous fingerprint,
            # advance the poll time, ingest nothing.
            logger.warning(
                "Apple Music returned an empty recently-played window despite "
                "an established fingerprint — keeping the previous fingerprint"
            )
            self._pending_fingerprint = list(previous.fingerprint)
            return []

        if previous is not None and not boundary_found:
            # No boundary anywhere in the fetched pages (full window
            # turnover): ingest at most the FIRST page. Items deeper than
            # page 1 are near-certainly old window content the lost boundary
            # can no longer prove old — the bounded honest floor.
            new_items = window[:first_page_size]

        if previous is not None and not boundary_found and new_items:
            # Turnover hardening: a fingerprinted id resurfacing here is a
            # reordered/moved repeat as far as this dedupe can tell, and
            # re-ingesting it would mint a phantom permanent play at a fresh
            # midpoint. Conflict-skip it — a true re-listen of a
            # recently-fingerprinted song may be missed on turnover, the
            # honest floor. Genuinely new songs still ingest.
            fingerprinted = set(previous.fingerprint)
            new_items = [song for song in new_items if song.id not in fingerprinted]

        self._pending_fingerprint = [song.id for song in window[:FINGERPRINT_LENGTH]]
        if previous is None:
            logger.info(
                "Seeded Apple Music window fingerprint",
                window=len(window),
                forced=params.force,
            )
            return []

        self._played_at = (
            previous.polled_at + (import_timestamp - previous.polled_at) / 2
        )
        logger.info(
            "Fetched Apple Music recently-played window",
            window=len(window),
            new_plays=len(new_items),
        )
        return new_items

    async def _read_poll_state(
        self, user_id: str, uow: UnitOfWorkProtocol
    ) -> _PollState | None:
        """The previous poll's fingerprint + time, or None to re-seed."""
        checkpoint = await uow.get_checkpoint_repository().get_sync_checkpoint(
            user_id=user_id, service="apple", entity_type="plays"
        )
        if checkpoint is None or checkpoint.cursor is None:
            return None
        state = _PollState.parse(checkpoint.cursor)
        if state is None:
            logger.warning(
                "Ignoring unparseable Apple Music poll state; re-seeding",
                cursor=checkpoint.cursor,
            )
        return state

    @override
    async def _process_data(
        self,
        raw_data: list[AppleMusicSong],
        *,
        user_id: str,
        batch_id: str,
        import_timestamp: datetime,
    ) -> list[ConnectorTrackPlay]:
        """Convert new window items to ledger rows on the ``apple_api`` channel."""
        played_at = self._played_at
        if played_at is None:
            # _fetch_data returns [] on every seeding path, so the pipeline
            # never reaches here without a midpoint. Fail loud rather than
            # stamp a row with a time nothing supports.
            raise RuntimeError(
                "Apple Music importer has no poll midpoint — _process_data "
                "called outside the fetch pipeline."
            )
        return [
            ConnectorTrackPlay(
                artist_name=song.attributes.artist_name,
                track_name=song.attributes.name,
                played_at=played_at,
                service="apple",
                user_id=user_id,
                album_name=song.attributes.album_name or None,
                # The feed observes no listening duration; survivorship takes
                # the first non-null ms_played, so a synthetic stand-in would
                # win merges and corrupt listening-time stats.
                ms_played=None,
                service_metadata=self._service_metadata(song),
                import_timestamp=import_timestamp,
                import_source=_IMPORT_SOURCE,
                import_batch_id=batch_id,
            )
            for song in raw_data
        ]

    @staticmethod
    def _service_metadata(song: AppleMusicSong) -> dict[str, JsonValue]:
        """Channel-native metadata.

        ``song_id`` is load-bearing twice over: ``ConnectorTrackPlay``'s
        "apple" branch derives the ledger identifier from it, and the play
        resolver reads it back to resolve the catalog song. ``duration_ms``
        is the track's length — data on the ledger, never a stand-in for
        ``ms_played``.
        """
        return {
            "song_id": song.id,
            "duration_ms": song.attributes.duration_in_millis or None,
            "isrc": song.attributes.isrc,
            "release_date": song.attributes.release_date,
        }

    @override
    async def _handle_checkpoints(
        self,
        raw_data: list[AppleMusicSong],
        params: AppleRecentImportParams,
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> None:
        """Persist the new fingerprint and poll time — on EVERY poll.

        Unlike cursor-advancing importers this cannot no-op on an empty poll:
        an unchanged window still narrows the next midpoint's interval, and a
        seeding poll must store the fingerprint it exists to produce. The
        rows themselves were saved before this runs (pipeline step 4), so the
        checkpoint never leads the persisted plays.
        """
        _ = params
        if self._pending_fingerprint is None or self._poll_time is None:
            return  # fetch never ran — nothing honest to record

        state = _PollState(
            fingerprint=self._pending_fingerprint, polled_at=self._poll_time
        )
        checkpoint_repo = uow.get_checkpoint_repository()
        checkpoint = await checkpoint_repo.get_or_create_sync_checkpoint(
            user_id=user_id, service="apple", entity_type="plays"
        )
        # `evolve`, not `with_update`: a seeding/empty poll has no listen
        # timestamp to record, and with_update requires one. evolve keeps the
        # same contract — poll-policy fields carry through untouched; only
        # the importer-owned cursor (and, when plays landed, last_timestamp)
        # change. The JSON-in-cursor encoding is deliberate: the checkpoint
        # row offers exactly one scalar cursor per (service, entity), and the
        # fingerprint + poll time must travel together or the midpoint and
        # the diff base could desynchronize.
        _ = await checkpoint_repo.save_sync_checkpoint(
            evolve(
                checkpoint,
                cursor=state.serialize(),
                last_timestamp=(
                    self._played_at
                    if raw_data and self._played_at is not None
                    else checkpoint.last_timestamp
                ),
            )
        )
        logger.info(
            "Advanced Apple Music window fingerprint",
            fingerprint_length=len(state.fingerprint),
            polled_at=state.polled_at.isoformat(),
            new_plays=len(raw_data),
        )


class _PollState(BaseModel):
    """The checkpoint cursor's decoded shape: window fingerprint + poll time.

    A Pydantic model per the boundary-validation convention — the cursor is a
    stored string whose shape nothing else guarantees, and any malformation
    must read as "re-seed", never as a fabricated diff base.
    """

    fingerprint: list[str]
    # Aware, or the midpoint arithmetic against tz-aware run timestamps throws.
    polled_at: AwareDatetime

    def serialize(self) -> str:
        return json.dumps({
            _FINGERPRINT_KEY: self.fingerprint[:FINGERPRINT_LENGTH],
            _POLLED_AT_KEY: self.polled_at.isoformat(),
        })

    @classmethod
    def parse(cls, cursor: str) -> _PollState | None:
        """Decode a stored cursor; None for anything malformed (re-seed)."""
        try:
            return cls.model_validate_json(cursor)
        except ValidationError:
            return None
