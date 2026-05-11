"""Unit tests for the shared playlist-results helpers."""

import json

from src.application.use_cases._shared.playlist_results import build_playlist_changes
from src.domain.entities.track import Artist, Track
from src.domain.playlist import (
    PlaylistDiff,
    PlaylistOperation,
    PlaylistOperationType,
)


class TestBuildPlaylistChanges:
    """build_playlist_changes() summarizes a PlaylistDiff for node_details JSONB."""

    def test_result_is_strict_json_serializable(self) -> None:
        """Values in the returned dict must be strict-JSON types so the dict
        can land in the ``workflow_run_nodes.node_details`` JSONB column
        without crashing psycopg's default JSON adapter — which has no
        encoder for ``UUID``.
        """
        added_track = Track(title="Added", artists=[Artist(name="A")])
        removed_track = Track(title="Removed", artists=[Artist(name="R")])
        diff = PlaylistDiff(
            operations=[
                PlaylistOperation(
                    operation_type=PlaylistOperationType.ADD,
                    track=added_track,
                    position=0,
                ),
                PlaylistOperation(
                    operation_type=PlaylistOperationType.REMOVE,
                    track=removed_track,
                    position=1,
                ),
                PlaylistOperation(
                    operation_type=PlaylistOperationType.MOVE,
                    track=added_track,
                    position=2,
                ),
            ]
        )

        result = build_playlist_changes(
            diff, playlist_id="local-playlist-123", connector="spotify"
        )

        json.dumps(result)  # raises if any UUID leaked through

        assert result["tracks_added"][0]["track_id"] == str(added_track.id)
        assert result["tracks_removed"][0]["track_id"] == str(removed_track.id)
        assert result["tracks_added_total"] == 1
        assert result["tracks_removed_total"] == 1
        assert result["tracks_moved"] == 1
