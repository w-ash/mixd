"""Import a batch of Spotify playlists into Mixd.

The user clicks "Import" in the browser dialog; we pull each selected
playlist's full tracks, resolve them against the local tracks table,
create a canonical ``Playlist`` + ``PlaylistLink`` per playlist, and
persist ``snapshot_id`` so re-imports can short-circuit.

Batch semantics are **non-atomic** at the fetch boundary — one failing
Spotify fetch does not abort the others. The fetch-successful subset
then goes through bulk DB writes as a single batch, in the spirit of
"design for collections, single items are degenerate cases." One-item
imports hit the same bulk path — just with a one-element list.

Unresolved tracks (Spotify tracks that don't exist in the local DB) are
counted per playlist but do NOT fail the import — the user may not have
imported their full library yet.
"""

from collections.abc import Sequence
from uuid import UUID

from attrs import define

from src.application.use_cases._shared import resolve_playlist_connector
from src.config import get_logger
from src.domain.entities import ConnectorPlaylist, Playlist
from src.domain.entities.playlist import SPOTIFY_CONNECTOR, PlaylistEntry
from src.domain.entities.playlist_link import PlaylistLink, SyncDirection, SyncStatus
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class ImportOutcome:
    """One successfully-imported playlist."""

    connector_playlist_identifier: str
    canonical_playlist_id: UUID
    resolved: int
    unresolved: int


@define(frozen=True, slots=True)
class ImportFailure:
    """One playlist that errored during import."""

    connector_playlist_identifier: str
    message: str


@define(frozen=True, slots=True)
class ImportSpotifyPlaylistsCommand:
    user_id: str
    connector_playlist_ids: Sequence[str]
    sync_direction: SyncDirection = SyncDirection.PULL


@define(frozen=True, slots=True)
class ImportSpotifyPlaylistsResult:
    """Per-playlist outcomes grouped by disposition.

    - ``succeeded``: new canonical playlists created this call.
    - ``skipped_unchanged``: link already existed AND cached snapshot was
      not NULL (i.e. we know it's up to date). Zero API calls spent.
    - ``failed``: fetch raised; captured as ImportFailure for the toast.
    """

    succeeded: Sequence[ImportOutcome]
    skipped_unchanged: Sequence[str]
    failed: Sequence[ImportFailure]


def _already_imported(
    connector_id: str,
    existing_links_by_id: dict[str, PlaylistLink],
    cached_by_id: dict[str, ConnectorPlaylist],
) -> bool:
    """Short-circuit: link exists AND we have a non-NULL cached snapshot_id.

    NULL snapshot means the cache predates snapshot tracking — we MUST
    re-fetch to be safe (see migration note on
    ``connector_playlists.snapshot_id``).
    """
    if connector_id not in existing_links_by_id:
        return False
    cached = cached_by_id.get(connector_id)
    return cached is not None and cached.snapshot_id is not None


@define(slots=True)
class ImportSpotifyPlaylistsUseCase:
    async def execute(
        self,
        command: ImportSpotifyPlaylistsCommand,
        uow: UnitOfWorkProtocol,
    ) -> ImportSpotifyPlaylistsResult:
        async with uow:
            link_repo = uow.get_playlist_link_repository()
            cp_repo = uow.get_connector_playlist_repository()
            connector_repo = uow.get_connector_repository()
            playlist_repo = uow.get_playlist_repository()

            existing_links = await link_repo.list_by_user_connector(
                command.user_id, SPOTIFY_CONNECTOR
            )
            existing_by_id: dict[str, PlaylistLink] = {
                link.connector_playlist_identifier: link for link in existing_links
            }
            cached_by_id: dict[str, ConnectorPlaylist] = {
                cp.connector_playlist_identifier: cp
                for cp in await cp_repo.list_by_connector(SPOTIFY_CONNECTOR)
            }

            connector = resolve_playlist_connector(SPOTIFY_CONNECTOR, uow)

            unique_ids = list(dict.fromkeys(command.connector_playlist_ids))

            # Sequential Spotify fetch — bounded concurrency is a follow-up
            # sized with the "Import all" flow; the DB phases below are bulk.
            skipped: list[str] = []
            failed: list[ImportFailure] = []
            fetched: list[tuple[str, ConnectorPlaylist]] = []

            for connector_id in unique_ids:
                if _already_imported(connector_id, existing_by_id, cached_by_id):
                    skipped.append(connector_id)
                    continue
                try:
                    cp = await connector.get_playlist(connector_id)
                    fetched.append((connector_id, cp))
                except Exception as exc:
                    logger.warning(
                        "Failed to import Spotify playlist",
                        connector_playlist_id=connector_id,
                        exc_info=True,
                    )
                    failed.append(
                        ImportFailure(
                            connector_playlist_identifier=connector_id,
                            message=str(exc),
                        )
                    )

            if not fetched:
                return ImportSpotifyPlaylistsResult(
                    succeeded=[], skipped_unchanged=skipped, failed=failed
                )

            cps_to_upsert = [cp for _, cp in fetched]
            _ = await cp_repo.bulk_upsert_models(cps_to_upsert)

            all_connections = [
                (SPOTIFY_CONNECTOR, item.connector_track_identifier)
                for cp in cps_to_upsert
                for item in cp.items
            ]
            track_by_key = (
                await connector_repo.find_tracks_by_connectors(
                    all_connections, user_id=command.user_id
                )
                if all_connections
                else {}
            )

            canonical_playlists: list[Playlist] = []
            unresolved_by_ident: dict[str, int] = {}
            for connector_id, cp in fetched:
                entries: list[PlaylistEntry] = []
                unresolved = 0
                for item in cp.items:
                    track = track_by_key.get((
                        SPOTIFY_CONNECTOR,
                        item.connector_track_identifier,
                    ))
                    if track is None:
                        unresolved += 1
                        continue
                    entries.append(
                        PlaylistEntry(
                            track=track, added_at=None, added_by=item.added_by_id
                        )
                    )
                canonical_playlists.append(
                    Playlist(
                        name=cp.name,
                        user_id=command.user_id,
                        description=cp.description,
                        entries=entries,
                    )
                )
                unresolved_by_ident[connector_id] = unresolved

            saved_playlists = await playlist_repo.save_playlists_batch(
                canonical_playlists
            )

            links_to_create: list[PlaylistLink] = []
            succeeded: list[ImportOutcome] = []
            for (connector_id, cp), saved in zip(fetched, saved_playlists, strict=True):
                unresolved = unresolved_by_ident[connector_id]
                resolved = len(cp.items) - unresolved
                links_to_create.append(
                    PlaylistLink(
                        playlist_id=saved.id,
                        connector_name=SPOTIFY_CONNECTOR,
                        connector_playlist_identifier=connector_id,
                        connector_playlist_name=cp.name,
                        sync_direction=command.sync_direction,
                        sync_status=SyncStatus.NEVER_SYNCED,
                    )
                )
                succeeded.append(
                    ImportOutcome(
                        connector_playlist_identifier=connector_id,
                        canonical_playlist_id=saved.id,
                        resolved=resolved,
                        unresolved=unresolved,
                    )
                )
                logger.info(
                    "Imported Spotify playlist",
                    playlist_id=saved.id,
                    connector_playlist_id=connector_id,
                    resolved=resolved,
                    unresolved=unresolved,
                    sync_direction=command.sync_direction.value,
                )

            await link_repo.create_links_batch(links_to_create)
            await uow.commit()
            return ImportSpotifyPlaylistsResult(
                succeeded=succeeded,
                skipped_unchanged=skipped,
                failed=failed,
            )


async def run_import_spotify_playlists(
    user_id: str,
    connector_playlist_ids: Sequence[str],
    sync_direction: SyncDirection = SyncDirection.PULL,
) -> ImportSpotifyPlaylistsResult:
    """Convenience wrapper for route handlers."""
    from src.application.runner import execute_use_case

    command = ImportSpotifyPlaylistsCommand(
        user_id=user_id,
        connector_playlist_ids=connector_playlist_ids,
        sync_direction=sync_direction,
    )
    return await execute_use_case(
        lambda uow: ImportSpotifyPlaylistsUseCase().execute(command, uow),
        user_id=user_id,
    )
