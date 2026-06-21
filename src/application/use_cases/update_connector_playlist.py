"""Push a track collection to an external playlist (Spotify/Apple Music).

A thin wrapper over the shared ``push_tracklist_to_connector`` primitive (fetch
the real external state → diff/append → execute → metadata). Kept as a use case
so the workflow destination node's DI wiring and Command/Result contract stay
stable; all the diff/execute/persist logic — and the fix for the old bug that
diffed the canonical against itself — lives in ``connector_push``.
"""

from attrs import define, field
from attrs.validators import min_len

from src.application.services.connector_push import push_tracklist_to_connector
from src.application.use_cases._shared.command_validators import (
    non_empty_string,
    validate_tracklist_has_tracks,
)
from src.config import get_logger
from src.domain.entities.shared import ConnectorPlaylistIdentifier
from src.domain.entities.track import TrackList
from src.domain.repositories.uow import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class UpdateConnectorPlaylistCommand:
    """Input for pushing a track collection to an external playlist."""

    user_id: str
    connector_playlist_identifier: ConnectorPlaylistIdentifier = field(
        validator=min_len(1)
    )
    new_tracklist: TrackList = field(validator=validate_tracklist_has_tracks)
    connector: str = field(validator=non_empty_string)  # "spotify", "apple_music", …
    append_mode: bool = False  # True=append new tracks, False=overwrite to match
    playlist_name: str | None = None  # optional name update
    playlist_description: str | None = None  # optional description update


@define(frozen=True, slots=True)
class UpdateConnectorPlaylistResult:
    """Push outcome with operation counts + per-track change evidence."""

    connector_playlist_identifier: ConnectorPlaylistIdentifier
    connector: str
    operations_performed: int = 0
    tracks_added: int = 0
    tracks_removed: int = 0
    tracks_moved: int = 0
    playlist_changes: dict[str, object] = field(factory=dict)


@define(slots=True)
class UpdateConnectorPlaylistUseCase:
    """Push a track collection to an external playlist via the shared primitive."""

    async def execute(
        self, command: UpdateConnectorPlaylistCommand, uow: UnitOfWorkProtocol
    ) -> UpdateConnectorPlaylistResult:
        async with uow:
            push = await push_tracklist_to_connector(
                command.connector,
                command.connector_playlist_identifier,
                command.new_tracklist,
                uow,
                user_id=command.user_id,
                append_mode=command.append_mode,
                name=command.playlist_name,
                description=command.playlist_description,
            )
            await uow.commit()

        logger.info(
            "Connector playlist update completed",
            connector=command.connector,
            connector_playlist_identifier=command.connector_playlist_identifier,
            append_mode=command.append_mode,
            tracks_added=push.tracks_added,
            tracks_removed=push.tracks_removed,
            tracks_moved=push.tracks_moved,
        )
        return UpdateConnectorPlaylistResult(
            connector_playlist_identifier=command.connector_playlist_identifier,
            connector=command.connector,
            operations_performed=push.tracks_added
            + push.tracks_removed
            + push.tracks_moved,
            tracks_added=push.tracks_added,
            tracks_removed=push.tracks_removed,
            tracks_moved=push.tracks_moved,
            playlist_changes=push.playlist_changes,
        )
