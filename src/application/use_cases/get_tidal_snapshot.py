"""Read-only snapshot of the user's Tidal favorites (v0.11.3).

The favorites relationship (``/userCollectionTracks/me/relationships/items``,
``sort=-addedAt``) serves identifiers + ``addedAt`` only, so the snapshot
splits its work: the count comes from the first page's ``meta.total`` when
Tidal serves one (the spec's "when available, may be approximate" — then one
page answers everything, the Discogs posture), and otherwise from walking
the cursor chain and summing — capped at ``_MAX_COUNT_PAGES`` (mirroring the
client's runaway guard), past which the count is a floor, not exact. The
recent rows cost one per-track lookup each, bounded by ``recent_limit``.

Zero canonical writes by design (raw display; import-with-matching is
v0.13.x's job): the only persistence is a best-effort refresh of the cached
``favorites_count`` the status probe renders, and a failure there never
fails the read (the profile-backfill posture). The empty collection is a
normal result — total 0, no recent items — not an error.

Connector lifecycle is owned by the UoW: connectors resolved through the
provider are cached on the UoW and closed by its ``__aexit__``.
"""

from collections.abc import Mapping, Sequence
from typing import Final

from attrs import define

from src.application.connector_protocols import TidalFavoritesConnector
from src.application.use_cases._shared.connector_resolver import (
    resolve_tidal_favorites_connector,
)
from src.config import get_logger
from src.domain.entities.shared import JsonValue, json_str
from src.domain.exceptions import ConnectorSyncError
from src.domain.repositories.uow import UnitOfWorkProtocol

logger = get_logger(__name__)

# Page ceiling for the count walk when Tidal serves no ``meta.total`` —
# mirrors the client's MAX_CURSOR_PAGES runaway guard. A walk that hits it
# yields a floor (logged), never an exception: the display survives a
# collection larger than the cap.
_MAX_COUNT_PAGES: Final = 50

_FETCH_FAILED_MESSAGE: Final = (
    "could not fetch the favorites from Tidal — try again in a moment"
)

# Ceiling on recent rows, enforced in the use case itself (not just at the
# interface edge): every recent row costs one Tidal request, so an oversized
# limit from any caller must not fan out into an unbounded request burst.
_MAX_RECENT_LIMIT: Final = 25


@define(frozen=True, slots=True)
class GetTidalSnapshotCommand:
    """Snapshot request: whose favorites, and how many recent items."""

    user_id: str
    recent_limit: int = 10


@define(frozen=True, slots=True)
class TidalSnapshotItem:
    """One recently favorited track, rendered for display."""

    title: str
    artists: str
    added_at: str


@define(frozen=True, slots=True)
class GetTidalSnapshotResult:
    """The favorites acknowledged: how many, and the latest additions.

    No username — Tidal's ``collection.read`` scope carries no identity, so
    display headers use the service name ("TIDAL"). ``total_items`` is
    Tidal's ``meta.total`` when served (possibly approximate), else a
    page-walk sum (a floor when the walk hit ``_MAX_COUNT_PAGES``).
    """

    total_items: int
    recent: tuple[TidalSnapshotItem, ...]


def _mappings(value: JsonValue) -> list[Mapping[str, JsonValue]]:
    """Narrow a JSON list-of-objects; anything else yields an empty list."""
    if not isinstance(value, Sequence) or isinstance(value, str):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _artists_display(artists: JsonValue) -> str:
    """Comma-join the ordered artist names from a track display mapping."""
    if not isinstance(artists, Sequence) or isinstance(artists, str):
        return ""
    return ", ".join(name for name in artists if isinstance(name, str) and name)


@define(slots=True)
class GetTidalSnapshotUseCase:
    """Fetch the favorites count and recent items — raw, unmatched, read-only."""

    async def execute(
        self, command: GetTidalSnapshotCommand, uow: UnitOfWorkProtocol
    ) -> GetTidalSnapshotResult:
        """Execute the snapshot read.

        Raises:
            TidalAuthRequiredError: Tidal has never been connected (no stored
                token), or the stored grant is dead.
            ConnectorSyncError: a favorites page could not be fetched.
        """
        # No aclose here: the connector is UoW-cached, and the UoW's
        # __aexit__ owns closing it — a second aclose would be a double-close.
        async with uow:
            connector = resolve_tidal_favorites_connector(uow)
            page = await connector.get_collection_items_page(None)
            if page is None:
                raise ConnectorSyncError("tidal", _FETCH_FAILED_MESSAGE)
            items = _mappings(page.get("items"))
            recent_limit = min(max(command.recent_limit, 0), _MAX_RECENT_LIMIT)
            recent = await self._recent_items(connector, items[:recent_limit])
            total = page.get("total")
            total_items = (
                total
                if isinstance(total, int) and not isinstance(total, bool)
                else await self._count_by_walking(connector, page, len(items))
            )
            # Best-effort cache refresh (profile-backfill posture): the
            # snapshot just paid for the real count, so write it back for
            # the status probe — but never fail the read on a save failure.
            try:
                await connector.save_favorites_count(total_items)
            except Exception:
                logger.warning(
                    "Failed to refresh cached Tidal favorites count",
                    exc_info=True,
                )
            return GetTidalSnapshotResult(total_items=total_items, recent=recent)

    async def _recent_items(
        self,
        connector: TidalFavoritesConnector,
        item_refs: list[Mapping[str, JsonValue]],
    ) -> tuple[TidalSnapshotItem, ...]:
        """Resolve display rows for the newest favorites, one lookup per track.

        The relationship serves identifiers only, so each row costs one
        ``get_track_display_data`` call — bounded by the caller's slice. An
        entry whose track cannot be resolved (gone from the catalog, a
        suppressed transport failure) has nothing to display and is skipped
        with a warning — the total stays authoritative either way.
        """
        rows: list[TidalSnapshotItem] = []
        skipped = 0
        for ref in item_refs:
            track_id = json_str(ref.get("id"))
            display = (
                await connector.get_track_display_data(track_id) if track_id else None
            )
            if display is None:
                skipped += 1
                continue
            rows.append(
                TidalSnapshotItem(
                    title=json_str(display.get("title")),
                    artists=_artists_display(display.get("artists")),
                    added_at=json_str(ref.get("added_at")),
                )
            )
        if skipped:
            logger.warning(
                "Skipping Tidal favorites without a resolvable track",
                skipped=skipped,
            )
        return tuple(rows)

    async def _count_by_walking(
        self,
        connector: TidalFavoritesConnector,
        first_page: Mapping[str, JsonValue],
        first_page_count: int,
    ) -> int:
        """Sum item counts across the cursor chain when ``meta.total`` is absent.

        A page that fails mid-walk raises rather than silently truncating —
        a partial count misread as complete would poison the cached number.
        Hitting ``_MAX_COUNT_PAGES`` with pages remaining logs and returns
        the sum so far (a floor).
        """
        count = first_page_count
        cursor = json_str(first_page.get("next_cursor")) or None
        for _page_number in range(1, _MAX_COUNT_PAGES):
            if cursor is None:
                return count
            page = await connector.get_collection_items_page(cursor)
            if page is None:
                raise ConnectorSyncError("tidal", _FETCH_FAILED_MESSAGE)
            count += len(_mappings(page.get("items")))
            cursor = json_str(page.get("next_cursor")) or None
        if cursor is not None:
            logger.warning(
                "Tidal favorites count walk hit the page cap — count is a floor",
                page_cap=_MAX_COUNT_PAGES,
                counted=count,
            )
        return count


async def run_get_tidal_snapshot(
    user_id: str, recent_limit: int = 10
) -> GetTidalSnapshotResult:
    """Fetch the Tidal favorites snapshot for ``user_id``."""
    from src.application.runner import execute_use_case

    command = GetTidalSnapshotCommand(user_id=user_id, recent_limit=recent_limit)
    return await execute_use_case(
        lambda uow: GetTidalSnapshotUseCase().execute(command, uow),
        user_id=user_id,
    )
