"""Unit tests for ConnectorPlaylistProcessingService.

The regression that matters: a source track that can't be resolved to a
canonical track becomes a first-class UNRESOLVED entry instead of being
silently dropped — so the imported playlist keeps its full count and order.
"""

from unittest.mock import MagicMock, patch

from src.application.services.connector_playlist_processing_service import (
    ConnectorPlaylistProcessingService,
)
from src.domain.entities.track import Artist, ConnectorTrack
from tests.fixtures import (
    make_connector_playlist,
    make_connector_playlist_item,
    make_mock_connector_repo,
    make_mock_uow,
    make_track,
)

_PROCESS = (
    "src.application.use_cases._shared.connector_resolver"
    ".resolve_track_conversion_connector"
)


def _fake_connector() -> MagicMock:
    """A connector whose convert_track_to_connector echoes the source dict."""
    conn = MagicMock()

    def convert(data: dict) -> ConnectorTrack:
        return ConnectorTrack(
            connector_name="spotify",
            connector_track_identifier=data["id"],
            title=data.get("name") or "Untitled",
            artists=[Artist(name=a["name"]) for a in data.get("artists", [])],
        )

    conn.convert_track_to_connector.side_effect = convert
    return conn


def _item(identifier: str, position: int, name: str, artist: str):
    return make_connector_playlist_item(
        identifier,
        position,
        extras={
            "full_track_data": {
                "id": identifier,
                "name": name,
                "artists": [{"name": artist}],
            }
        },
    )


class TestUnresolvedEmission:
    async def test_unmatched_track_becomes_unresolved_entry_not_dropped(self):
        cp = make_connector_playlist(
            items=[
                _item("a", 0, "Track A", "Artist A"),
                _item("b", 1, "Track B", "Artist B"),
                _item("c", 2, "Ghost", "Nobody"),
            ]
        )
        # Ingest resolves only a and b; c is left unmatched (e.g. a local file
        # that produced no canonical track).
        connector_repo = make_mock_connector_repo(
            ingest_external_tracks_bulk=[
                make_track(
                    title="Track A", connector_track_identifiers={"spotify": "a"}
                ),
                make_track(
                    title="Track B", connector_track_identifiers={"spotify": "b"}
                ),
            ]
        )
        uow = make_mock_uow(connector_repo=connector_repo)

        with patch(_PROCESS, return_value=_fake_connector()):
            result = (
                await ConnectorPlaylistProcessingService().process_connector_playlist(
                    cp, uow, user_id="default"
                )
            )

        # Every source position is preserved, in order — never dropped.
        assert len(result.entries) == 3
        assert result.metadata["unresolved_count"] == 1
        assert result.unresolved_count == 1

        # The unmatched position is unresolved, with display data carried.
        ghost = result.entries[2]
        assert ghost.is_resolved is False
        assert ghost.display_title == "Ghost"
        assert ghost.connector_track_ref is not None
        assert ghost.connector_track_ref.connector_track_identifier == "c"
        assert ghost.connector_track_ref.artists == ("Nobody",)

        # Resolved-only view excludes the hole.
        assert len(result.tracks) == 2

    async def test_all_resolved_has_no_unresolved(self):
        cp = make_connector_playlist(items=[_item("a", 0, "Track A", "Artist A")])
        connector_repo = make_mock_connector_repo(
            ingest_external_tracks_bulk=[
                make_track(
                    title="Track A", connector_track_identifiers={"spotify": "a"}
                )
            ]
        )
        uow = make_mock_uow(connector_repo=connector_repo)

        with patch(_PROCESS, return_value=_fake_connector()):
            result = (
                await ConnectorPlaylistProcessingService().process_connector_playlist(
                    cp, uow, user_id="default"
                )
            )

        assert len(result.entries) == 1
        assert result.unresolved_count == 0
        assert result.entries[0].is_resolved is True
