"""Unit tests for the shared connector_push primitives.

These pin the fail-loud contract that used to live (untested for ~1k lines) in
update_connector_playlist: a failed/partial push RAISES ConnectorSyncError so the
caller routes it to ERROR — never a silent SYNCED — while a fully-applied push
with no snapshot id is a success.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.application.services.connector_push import (
    _append_new_tracks,
    _update_metadata,
    execute_connector_operations,
    external_as_playlist,
    overwrite_external_playlist,
    push_tracklist_to_connector,
)
from src.domain.entities.playlist import Playlist, PlaylistEntry
from src.domain.entities.shared import ConnectorPlaylistIdentifier
from src.domain.entities.track import TrackList
from src.domain.exceptions import ConnectorSyncError
from src.domain.playlist.diff_engine import PlaylistOpsOutcome
from tests.fixtures import (
    make_connector_playlist,
    make_connector_playlist_item,
    make_mock_uow,
    make_track,
)

_PUSH_MOD = "src.application.services.connector_push"
_ID = ConnectorPlaylistIdentifier("sp-1")


def _uow_with_connector(connector) -> MagicMock:
    uow = MagicMock()
    uow.get_track_repository = MagicMock(return_value=MagicMock())
    return uow


class TestExecuteConnectorOperations:
    async def test_partial_push_raises(self):
        connector = MagicMock()
        connector.execute_playlist_operations = AsyncMock(
            return_value=PlaylistOpsOutcome(snapshot_id="s", requested=2, failed=1)
        )
        with patch(f"{_PUSH_MOD}.resolve_playlist_connector", return_value=connector):
            with pytest.raises(ConnectorSyncError):
                await execute_connector_operations(
                    "spotify",
                    _ID,
                    [MagicMock(), MagicMock()],
                    _uow_with_connector(connector),
                )

    async def test_fully_applied_without_snapshot_is_success(self):
        connector = MagicMock()
        connector.execute_playlist_operations = AsyncMock(
            return_value=PlaylistOpsOutcome(snapshot_id=None, requested=2, failed=0)
        )
        with patch(f"{_PUSH_MOD}.resolve_playlist_connector", return_value=connector):
            outcome = await execute_connector_operations(
                "spotify",
                _ID,
                [MagicMock(), MagicMock()],
                _uow_with_connector(connector),
            )
        assert outcome.fully_applied is True
        assert outcome.snapshot_id is None


class TestOverwriteExternalPlaylist:
    async def test_no_changes_is_noop_without_connector_call(self):
        track = make_track(title="A")
        same = Playlist(name="P", entries=[PlaylistEntry(track=track)])
        connector = MagicMock()
        connector.execute_playlist_operations = AsyncMock()
        with patch(f"{_PUSH_MOD}.resolve_playlist_connector", return_value=connector):
            result = await overwrite_external_playlist(
                "spotify", _ID, same, same, _uow_with_connector(connector)
            )
        assert result.tracks_added == 0
        assert result.tracks_removed == 0
        connector.execute_playlist_operations.assert_not_called()

    async def test_overwrite_executes_and_reports_counts(self):
        current = Playlist(
            name="P", entries=[PlaylistEntry(track=make_track(title="A"))]
        )
        target = Playlist(
            name="P",
            entries=[
                *current.entries,
                PlaylistEntry(track=make_track(title="B")),
            ],
        )
        connector = MagicMock()
        connector.execute_playlist_operations = AsyncMock(
            return_value=PlaylistOpsOutcome(snapshot_id="new", requested=1, failed=0)
        )
        with patch(f"{_PUSH_MOD}.resolve_playlist_connector", return_value=connector):
            result = await overwrite_external_playlist(
                "spotify", _ID, current, target, _uow_with_connector(connector)
            )
        connector.execute_playlist_operations.assert_awaited_once()
        assert result.tracks_added == 1
        assert result.snapshot_id == "new"


def _uow_resolving(resolve_map: dict) -> MagicMock:
    uow = make_mock_uow()
    uow.get_connector_repository().find_tracks_by_connectors = AsyncMock(
        return_value=resolve_map
    )
    return uow


class TestExternalAsPlaylist:
    """Resolving a fetched remote read-only: matched tracks become resolved entries,
    unmatched become unresolved refs that .tracks excludes (so the diff never touches
    a track that isn't in the canonical) — the safety invariant for partial overwrite.
    """

    async def test_resolved_and_unresolved_split(self):
        track = make_track(title="A", connector_track_identifiers={"spotify": "sA"})
        remote = make_connector_playlist(
            items=[
                make_connector_playlist_item("sA", 0),
                make_connector_playlist_item("sUNKNOWN", 1),
            ]
        )
        playlist = await external_as_playlist(
            remote, _uow_resolving({("spotify", "sA"): track}), user_id="u"
        )
        assert len(playlist.entries) == 2  # every source position preserved
        assert playlist.tracks == [track]  # only the resolved one
        assert playlist.unresolved_count == 1
        assert (
            playlist.unresolved_entries[
                0
            ].connector_track_ref.connector_track_identifier
            == "sUNKNOWN"
        )

    async def test_empty_remote(self):
        playlist = await external_as_playlist(
            make_connector_playlist(items=[]), _uow_resolving({}), user_id="u"
        )
        assert playlist.entries == []


class TestAppendNewTracks:
    async def test_appends_only_tracks_not_already_present(self):
        existing = make_track(title="A")
        current = Playlist(name="P", entries=[PlaylistEntry(track=existing)])
        new = make_track(title="B")
        connector = MagicMock()
        connector.append_tracks_to_playlist = AsyncMock()
        with patch(f"{_PUSH_MOD}.resolve_playlist_connector", return_value=connector):
            result = await _append_new_tracks(
                "spotify",
                _ID,
                current,
                TrackList(tracks=[existing, new]),
                make_mock_uow(),
            )
        assert result.tracks_added == 1
        assert connector.append_tracks_to_playlist.await_args.args[1] == [new]

    async def test_dedups_by_connector_identifier(self):
        # Same spotify track surfacing as a different canonical-id instance.
        existing = make_track(title="A", connector_track_identifiers={"spotify": "sA"})
        dup = make_track(title="A again", connector_track_identifiers={"spotify": "sA"})
        current = Playlist(name="P", entries=[PlaylistEntry(track=existing)])
        connector = MagicMock()
        connector.append_tracks_to_playlist = AsyncMock()
        with patch(f"{_PUSH_MOD}.resolve_playlist_connector", return_value=connector):
            result = await _append_new_tracks(
                "spotify", _ID, current, TrackList(tracks=[dup]), make_mock_uow()
            )
        assert result.tracks_added == 0
        connector.append_tracks_to_playlist.assert_not_called()


class TestUpdateMetadata:
    async def test_no_updates_skips_connector(self):
        connector = MagicMock()
        connector.update_playlist_metadata = AsyncMock()
        with patch(f"{_PUSH_MOD}.resolve_playlist_connector", return_value=connector):
            await _update_metadata("spotify", _ID, None, None, make_mock_uow())
        connector.update_playlist_metadata.assert_not_called()

    async def test_name_only_update(self):
        connector = MagicMock()
        connector.update_playlist_metadata = AsyncMock()
        with patch(f"{_PUSH_MOD}.resolve_playlist_connector", return_value=connector):
            await _update_metadata("spotify", _ID, "New Name", None, make_mock_uow())
        connector.update_playlist_metadata.assert_awaited_once_with(
            _ID, {"name": "New Name"}
        )


class TestPushTracklistToConnector:
    async def test_overwrite_fetches_fresh_remote_then_diffs(self):
        # The workflow-destination no-op fix: fetch the REAL remote ([A]), diff the
        # target ([A, B]) against it, push the single add — not a canonical self-diff.
        track_a = make_track(title="A", connector_track_identifiers={"spotify": "sA"})
        track_b = make_track(title="B", connector_track_identifiers={"spotify": "sB"})
        remote = make_connector_playlist(items=[make_connector_playlist_item("sA", 0)])
        uow = _uow_resolving({("spotify", "sA"): track_a})
        connector = MagicMock()
        connector.execute_playlist_operations = AsyncMock(
            return_value=PlaylistOpsOutcome(snapshot_id="new", requested=1, failed=0)
        )
        with (
            patch(
                f"{_PUSH_MOD}.sync_connector_playlist", AsyncMock(return_value=remote)
            ),
            patch(f"{_PUSH_MOD}.resolve_playlist_connector", return_value=connector),
        ):
            result = await push_tracklist_to_connector(
                "spotify", _ID, TrackList(tracks=[track_a, track_b]), uow, user_id="u"
            )
        connector.execute_playlist_operations.assert_awaited_once()
        assert result.tracks_added == 1
