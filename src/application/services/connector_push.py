"""Shared connector-side playlist push primitives.

The single place playlist changes reach an external connector. Both the
link-sync engine and the workflow destination push through here, so there is one
push implementation — and one fix for the old bug where "the external playlist's
current state" was sourced from the canonical playlist itself (a self-join that
made every push a no-op and the destructive guard dead).

- ``external_as_playlist`` — resolve a freshly-fetched ConnectorPlaylist to a
  domain Playlist, read-only (unmatched tracks become unresolved entries).
- ``execute_connector_operations`` — apply sequenced ops; fail loud on a
  partial/failed push (ConnectorSyncError → never a silent SYNCED).
- ``overwrite_external_playlist`` — diff a target against the current external
  and execute the minimal ops. Used by the engine (push) and the workflow
  overwrite path.
- ``push_tracklist_to_connector`` — the workflow-destination push: fetch fresh
  external, then overwrite or append, then apply metadata.
"""

from collections.abc import Mapping, Sequence

from attrs import define, field

from src.application.services.connector_playlist_sync_service import (
    sync_connector_playlist,
)
from src.application.use_cases._shared import build_playlist_changes
from src.application.use_cases._shared.connector_resolver import (
    resolve_playlist_connector,
)
from src.application.use_cases._shared.operation_counters import count_operation_types
from src.config import get_logger
from src.domain.entities.playlist import (
    ConnectorPlaylist,
    ConnectorTrackRef,
    Playlist,
    PlaylistEntry,
)
from src.domain.entities.shared import ConnectorPlaylistIdentifier, JsonValue
from src.domain.entities.track import Track, TrackList
from src.domain.exceptions import ConnectorSyncError
from src.domain.playlist.diff_engine import (
    PlaylistOperation,
    PlaylistOpsOutcome,
    calculate_playlist_diff,
)
from src.domain.playlist.execution_strategies import plan_api_operations
from src.domain.repositories.uow import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class PushResult:
    """Outcome of pushing a target to an external playlist."""

    tracks_added: int = 0
    tracks_removed: int = 0
    tracks_moved: int = 0
    tracks_dropped: int = field(default=0)  # tracks with no connector match
    snapshot_id: str | None = None
    # Lightweight per-track change evidence for workflow run history.
    playlist_changes: dict[str, object] = field(factory=dict)


async def external_as_playlist(
    remote_cp: ConnectorPlaylist,
    uow: UnitOfWorkProtocol,
    *,
    user_id: str,
) -> Playlist:
    """A fetched ConnectorPlaylist as a domain Playlist, resolved read-only.

    Resolves each remote track to an EXISTING canonical track (no ingest);
    unmatched remote tracks become unresolved entries (excluded from ``.tracks``,
    so they are never touched by a diff). Shared by preview, push, and the
    workflow destination so the diff always reflects the real external state.
    """
    name = remote_cp.connector_name
    connector_repo = uow.get_connector_repository()
    tuples = [(name, item.connector_track_identifier) for item in remote_cp.items]
    resolved = (
        await connector_repo.find_tracks_by_connectors(tuples, user_id=user_id)
        if tuples
        else {}
    )
    entries: list[PlaylistEntry] = []
    for item in remote_cp.items:
        track = resolved.get((name, item.connector_track_identifier))
        if track is not None:
            entries.append(PlaylistEntry(track=track))
        else:
            entries.append(
                PlaylistEntry(
                    track=None,
                    connector_track_ref=ConnectorTrackRef(
                        connector_name=name,
                        connector_track_identifier=item.connector_track_identifier,
                        title=_item_title(item.extras),
                    ),
                )
            )
    return Playlist(name=remote_cp.name, entries=entries, user_id=user_id)


async def execute_connector_operations(
    connector_name: str,
    connector_playlist_identifier: ConnectorPlaylistIdentifier,
    operations: Sequence[PlaylistOperation],
    uow: UnitOfWorkProtocol,
) -> PlaylistOpsOutcome:
    """Apply sequenced add/remove/move ops to the external playlist.

    Raises ``ConnectorSyncError`` on a partial/failed push so callers route it to
    ERROR — never a silent SYNCED. The snapshot id is optimistic-concurrency
    metadata (legitimately None on no-op paths), so success keys on
    ``fully_applied``, not the snapshot.
    """
    connector = resolve_playlist_connector(connector_name, uow)
    outcome = await connector.execute_playlist_operations(
        connector_playlist_identifier,
        list(operations),
        track_repo=uow.get_track_repository(),
    )
    if not outcome.fully_applied:
        raise ConnectorSyncError(
            connector_name,
            f"push only partially applied: {outcome.failed} of "
            f"{outcome.requested} operations failed",
        )
    return outcome


async def overwrite_external_playlist(
    connector_name: str,
    connector_playlist_identifier: ConnectorPlaylistIdentifier,
    current: Playlist,
    target: Playlist | TrackList,
    uow: UnitOfWorkProtocol,
    *,
    include_changes: bool = False,
) -> PushResult:
    """Execute the minimal ops to make the external playlist match ``target``.

    Diffs ``target`` against ``current`` (the real, freshly-fetched external
    state) — the single overwrite-push implementation behind both the link-sync
    engine and the workflow destination. ``include_changes`` builds the
    per-track ``playlist_changes`` evidence (only the workflow run-history path
    reads it); the link-sync engine leaves it off to skip the wasted dict.
    """
    diff = calculate_playlist_diff(current, target)
    if not diff.has_changes:
        return PushResult()
    operations = plan_api_operations(diff)
    outcome = await execute_connector_operations(
        connector_name, connector_playlist_identifier, operations, uow
    )
    counts = count_operation_types(operations)
    return PushResult(
        tracks_added=counts.added,
        tracks_removed=counts.removed,
        tracks_moved=counts.moved,
        tracks_dropped=outcome.dropped,
        snapshot_id=outcome.snapshot_id,
        playlist_changes=build_playlist_changes(
            diff, connector_playlist_identifier, connector_name
        )
        if include_changes
        else {},
    )


async def push_tracklist_to_connector(
    connector_name: str,
    connector_playlist_identifier: ConnectorPlaylistIdentifier,
    target: TrackList,
    uow: UnitOfWorkProtocol,
    *,
    user_id: str,
    append_mode: bool = False,
    name: str | None = None,
    description: str | None = None,
) -> PushResult:
    """Make an external playlist reflect ``target`` (the workflow-destination push).

    Fetches the REAL current external state, then overwrites (diff) or appends,
    then applies optional name/description. Replaces the old path that diffed the
    canonical against itself and never actually pushed.
    """
    remote_cp = await sync_connector_playlist(
        connector_name, connector_playlist_identifier, uow
    )
    current = await external_as_playlist(remote_cp, uow, user_id=user_id)

    if append_mode:
        result = await _append_new_tracks(
            connector_name, connector_playlist_identifier, current, target, uow
        )
    else:
        result = await overwrite_external_playlist(
            connector_name,
            connector_playlist_identifier,
            current,
            target,
            uow,
            include_changes=True,
        )

    await _update_metadata(
        connector_name, connector_playlist_identifier, name, description, uow
    )
    return result


async def _append_new_tracks(
    connector_name: str,
    connector_playlist_identifier: ConnectorPlaylistIdentifier,
    current: Playlist,
    target: TrackList,
    uow: UnitOfWorkProtocol,
) -> PushResult:
    """Append target tracks not already present — preserves existing content.

    Presence is matched by canonical id, falling back to the connector's own track
    identifier — so the same connector track surfacing as a different canonical-id
    instance (e.g. after re-resolution) isn't double-added on a repeated append.
    """
    existing_ids = {track.id for track in current.tracks}
    existing_cids = {
        cid
        for track in current.tracks
        if (cid := track.connector_track_identifiers.get(connector_name))
    }

    def _present(track: Track) -> bool:
        if track.id in existing_ids:
            return True
        cid = track.connector_track_identifiers.get(connector_name)
        return cid is not None and cid in existing_cids

    new_tracks = [track for track in target.tracks if not _present(track)]
    if not new_tracks:
        return PushResult()
    connector = resolve_playlist_connector(connector_name, uow)
    await connector.append_tracks_to_playlist(connector_playlist_identifier, new_tracks)
    return PushResult(tracks_added=len(new_tracks))


async def _update_metadata(
    connector_name: str,
    connector_playlist_identifier: ConnectorPlaylistIdentifier,
    name: str | None,
    description: str | None,
    uow: UnitOfWorkProtocol,
) -> None:
    """Update the external playlist's name/description if requested."""
    updates: dict[str, str] = {}
    if name:
        updates["name"] = name
    if description:
        updates["description"] = description
    if not updates:
        return
    connector = resolve_playlist_connector(connector_name, uow)
    await connector.update_playlist_metadata(connector_playlist_identifier, updates)


def _item_title(extras: Mapping[str, JsonValue]) -> str | None:
    """Best-effort display title from a connector item's extras."""
    full = extras.get("full_track_data")
    if isinstance(full, dict):
        name = full.get("name")
        if isinstance(name, str):
            return name
    track_name = extras.get("track_name")
    return track_name if isinstance(track_name, str) else None
