"""Read-only snapshot of the user's Discogs collection (v0.11.1).

One page answers everything the snapshot shows: ``pagination.items`` is the
authoritative total and the page's releases are the recent items — no walk,
so a snapshot normally spends exactly one request of the instance-wide
Discogs budget (a second only when the stored token is missing its cached
username and identity must be re-derived live).
Zero canonical writes by design (raw display; import-with-matching
is v0.13.1's job): the only persistence is a best-effort refresh of the
cached ``collection_count`` the status probe renders, and a failure there
never fails the read (the profile-backfill posture). The empty collection
is a normal result — total 0, no recent items — not an error.

Connector lifecycle is owned by the UoW: connectors resolved through the
provider are cached on the UoW and closed by its ``__aexit__``.
"""

from collections.abc import Mapping, Sequence

from attrs import define

from src.application.use_cases._shared.connector_resolver import (
    resolve_discogs_collection_connector,
)
from src.config import get_logger
from src.domain.entities.shared import JsonValue, json_int, json_str
from src.domain.exceptions import ConnectorSyncError
from src.domain.repositories.uow import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class GetDiscogsSnapshotCommand:
    """Snapshot request: whose collection, and how many recent items."""

    user_id: str
    recent_limit: int = 10


@define(frozen=True, slots=True)
class DiscogsSnapshotItem:
    """One recently added collection item, rendered for display."""

    title: str
    artists: str
    year: int | None
    formats: str
    date_added: str


@define(frozen=True, slots=True)
class GetDiscogsSnapshotResult:
    """The collection acknowledged: who, how many, and the latest additions."""

    username: str
    total_items: int
    recent: tuple[DiscogsSnapshotItem, ...]


def _mappings(value: JsonValue) -> list[Mapping[str, JsonValue]]:
    """Narrow a JSON list-of-objects; anything else yields an empty list."""
    if not isinstance(value, Sequence) or isinstance(value, str):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _displayable(releases: JsonValue) -> list[Mapping[str, JsonValue]]:
    """Collection items that carry a ``basic_information`` block.

    The block is observed on every real capture but not owed to us (the
    boundary model allows None): an item without it has nothing to display,
    so it is skipped with a warning — the total still comes from pagination,
    which counts the whole collection either way.
    """
    items = _mappings(releases)
    displayable = [i for i in items if isinstance(i.get("basic_information"), Mapping)]
    if len(displayable) < len(items):
        logger.warning(
            "Skipping Discogs collection items without basic_information",
            skipped=len(items) - len(displayable),
        )
    return displayable


def _artists_display(artists: JsonValue) -> str:
    """Thread artist credits with Discogs join phrases ("A & B", "X feat. Y").

    The credited spelling (``anv``) wins over the canonical name when Discogs
    records one — the snapshot shows what the sleeve says.
    """
    parts: list[str] = []
    for artist in _mappings(artists):
        name = json_str(artist.get("anv")) or json_str(artist.get("name"))
        if not name:
            continue
        parts.append(name)
        join = json_str(artist.get("join"))
        if join:
            parts.append(join)
    return " ".join(parts)


def _formats_display(formats: JsonValue) -> str:
    """Render format entries like ``2x Vinyl (LP, Album)``, comma-joined."""
    rendered: list[str] = []
    for fmt in _mappings(formats):
        name = json_str(fmt.get("name"))
        if not name:
            continue
        qty = json_str(fmt.get("qty"))
        label = f"{qty}x {name}" if qty not in ("", "1") else name
        descriptions = fmt.get("descriptions")
        descs = (
            ", ".join(d for d in descriptions if isinstance(d, str))
            if isinstance(descriptions, Sequence) and not isinstance(descriptions, str)
            else ""
        )
        rendered.append(f"{label} ({descs})" if descs else label)
    return ", ".join(rendered)


def _map_item(release: Mapping[str, JsonValue]) -> DiscogsSnapshotItem:
    """Project one raw collection release into the display item."""
    basic_raw = release.get("basic_information")
    basic: Mapping[str, JsonValue] = basic_raw if isinstance(basic_raw, Mapping) else {}
    year = json_int(basic.get("year"))
    return DiscogsSnapshotItem(
        title=json_str(basic.get("title")),
        artists=_artists_display(basic.get("artists")),
        # Discogs uses 0 for "year unknown"; render that as absent.
        year=year or None,
        formats=_formats_display(basic.get("formats")),
        date_added=json_str(release.get("date_added")),
    )


@define(slots=True)
class GetDiscogsSnapshotUseCase:
    """Fetch the collection count and recent items — raw, unmatched, read-only."""

    async def execute(
        self, command: GetDiscogsSnapshotCommand, uow: UnitOfWorkProtocol
    ) -> GetDiscogsSnapshotResult:
        """Execute the snapshot read.

        Raises:
            DiscogsAuthRequiredError: Discogs has never been connected (no
                stored token), or the stored token is rejected.
            ConnectorSyncError: the collection page (or the fallback identity
                probe) could not be fetched.
        """
        # No aclose here: the connector is UoW-cached, and the UoW's
        # __aexit__ owns closing it — a second aclose would be a double-close.
        async with uow:
            connector = resolve_discogs_collection_connector(uow)
            username = await connector.get_stored_username()
            if username is None:
                # The status probe reports "connected" whenever a token row
                # exists, so a row merely missing account_name must not read
                # as "not connected": re-derive the username live (raises
                # auth-required itself when no usable token is stored).
                username = await connector.fetch_username()
            if username is None:
                raise ConnectorSyncError(
                    "discogs",
                    "could not resolve the Discogs username — try again in a moment",
                )
            page = await connector.get_collection_page_data(
                username, page=1, per_page=max(command.recent_limit, 1)
            )
            if page is None:
                raise ConnectorSyncError(
                    "discogs",
                    "could not fetch the collection from Discogs — "
                    "try again in a moment",
                )
            pagination_raw = page.get("pagination")
            pagination: Mapping[str, JsonValue] = (
                pagination_raw if isinstance(pagination_raw, Mapping) else {}
            )
            total_items = json_int(pagination.get("items"))
            recent = tuple(
                _map_item(release)
                for release in _displayable(page.get("releases"))[
                    : command.recent_limit
                ]
            )
            # Best-effort cache refresh (profile-backfill posture): the
            # snapshot just paid for the real count, so write it back for
            # the status probe — but never fail the read on a save failure.
            try:
                await connector.save_collection_count(total_items)
            except Exception:
                logger.warning(
                    "Failed to refresh cached Discogs collection count",
                    exc_info=True,
                )
            return GetDiscogsSnapshotResult(
                username=username, total_items=total_items, recent=recent
            )


async def run_get_discogs_snapshot(
    user_id: str, recent_limit: int = 10
) -> GetDiscogsSnapshotResult:
    """Fetch the Discogs collection snapshot for ``user_id``."""
    from src.application.runner import execute_use_case

    command = GetDiscogsSnapshotCommand(user_id=user_id, recent_limit=recent_limit)
    return await execute_use_case(
        lambda uow: GetDiscogsSnapshotUseCase().execute(command, uow),
        user_id=user_id,
    )
