"""Unit tests for ConnectorPlaylistMapper JSONB-item narrowing.

``ConnectorPlaylistMapper.to_domain`` is pure (it reads a transient DB model's
columns — no I/O), so it is tested as a unit. Its ``items`` column is a JSONB
``list[JsonDict]`` where every field is a ``JsonValue`` union; the mapper narrows
each item defensively at the boundary. These tests pin that narrowing: malformed
items are dropped, and optional fields fall back to ``None`` / ``{}``.
"""

from datetime import UTC, datetime
from uuid import uuid7

from src.infrastructure.persistence.database.db_models import DBConnectorPlaylist
from src.infrastructure.persistence.repositories.playlist.mapper import (
    ConnectorPlaylistMapper,
)


def _db_connector_playlist(items: list[dict[str, object]]) -> DBConnectorPlaylist:
    """Transient DBConnectorPlaylist with the given JSONB items list."""
    return DBConnectorPlaylist(
        id=uuid7(),
        connector_name="spotify",
        connector_playlist_identifier="pl_123",
        name="Test",
        description=None,
        owner=None,
        owner_id=None,
        is_public=True,
        collaborative=False,
        follower_count=None,
        items=items,
        raw_metadata={},
        snapshot_id=None,
        last_updated=datetime(2024, 1, 1, tzinfo=UTC),
    )


class TestConnectorPlaylistMapperItemNarrowing:
    async def test_valid_item_round_trips(self):
        db = _db_connector_playlist([
            {
                "connector_track_identifier": "t1",
                "position": 0,
                "added_at": "2024-01-01T00:00:00Z",
                "added_by_id": "u1",
                "extras": {"source": "import"},
            }
        ])
        result = await ConnectorPlaylistMapper.to_domain(db)
        assert len(result.items) == 1
        item = result.items[0]
        assert item.connector_track_identifier == "t1"
        assert item.position == 0
        assert item.added_at == "2024-01-01T00:00:00Z"
        assert item.added_by_id == "u1"
        assert item.extras == {"source": "import"}

    async def test_non_string_identifier_dropped(self):
        db = _db_connector_playlist([
            {"connector_track_identifier": 123, "position": 0},
        ])
        result = await ConnectorPlaylistMapper.to_domain(db)
        assert result.items == []

    async def test_non_int_position_dropped(self):
        db = _db_connector_playlist([
            {"connector_track_identifier": "t1", "position": "first"},
        ])
        result = await ConnectorPlaylistMapper.to_domain(db)
        assert result.items == []

    async def test_optional_fields_fall_back(self):
        # added_at / added_by_id of the wrong type -> None; extras non-dict -> {}.
        db = _db_connector_playlist([
            {
                "connector_track_identifier": "t1",
                "position": 2,
                "added_at": 999,
                "added_by_id": None,
                "extras": "not-a-dict",
            }
        ])
        result = await ConnectorPlaylistMapper.to_domain(db)
        assert len(result.items) == 1
        item = result.items[0]
        assert item.added_at is None
        assert item.added_by_id is None
        assert item.extras == {}

    async def test_mixed_valid_and_invalid_keeps_only_valid(self):
        db = _db_connector_playlist([
            {"connector_track_identifier": "good1", "position": 0},
            {"connector_track_identifier": 5, "position": 1},  # bad id
            {"connector_track_identifier": "bad", "position": None},  # bad pos
            {"connector_track_identifier": "good2", "position": 3},
        ])
        result = await ConnectorPlaylistMapper.to_domain(db)
        assert [i.connector_track_identifier for i in result.items] == [
            "good1",
            "good2",
        ]
