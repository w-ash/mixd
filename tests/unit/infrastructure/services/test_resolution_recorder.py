"""Unit tests for the resolution recorder's event stamping.

The integration suite (``tests/integration/repositories/test_resolution_events.py``)
proves the rows land; this pins what the recorder stamps on a decision before
it does, with the session and repositories mocked. In particular a
``superseded`` event carries its edge's ``entity_kind`` — every typed mapping
table supersedes through the same seam, and an artist supersession recorded
as ``'track'`` would point the reader at the wrong table.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid7

from src.domain.entities.resolution_event import ResolutionEvent
from src.domain.repositories.resolution import SupersessionEdge
from src.infrastructure.services.resolution_recorder import ResolutionRecorder


def _recorder() -> tuple[ResolutionRecorder, AsyncMock]:
    events = AsyncMock()
    events.append_events.return_value = 1
    recorder = ResolutionRecorder(MagicMock(), events=events, negatives=AsyncMock())
    return recorder, events


def _appended(events: AsyncMock) -> list[ResolutionEvent]:
    appended: list[ResolutionEvent] = []
    for call in events.append_events.call_args_list:
        appended.extend(call.args[0])
    return appended


class TestRecordSupersessions:
    async def test_the_event_carries_the_edges_entity_kind(self):
        recorder, events = _recorder()
        artist_edge = SupersessionEdge(
            predecessor_id=uuid7(),
            successor_id=uuid7(),
            user_id="u1",
            connector_name="spotify",
            entity_kind="artist",
        )
        track_edge = SupersessionEdge(
            predecessor_id=uuid7(),
            successor_id=uuid7(),
            user_id="u1",
            connector_name="spotify",
        )

        with patch.object(
            ResolutionRecorder, "_mapping_rows", AsyncMock(return_value={})
        ):
            total = await recorder.record_supersessions(
                [artist_edge, track_edge], reason="rematch"
            )

        assert total == 1
        by_successor = {
            event.resulting_mapping_id: event for event in _appended(events)
        }
        assert by_successor[artist_edge.successor_id].entity_kind == "artist"
        assert by_successor[track_edge.successor_id].entity_kind == "track"
        assert by_successor[artist_edge.successor_id].event_type == "superseded"
        assert by_successor[artist_edge.successor_id].payload == {
            "superseded_mapping_id": str(artist_edge.predecessor_id),
            "reason": "rematch",
        }

    async def test_no_edges_records_nothing(self):
        recorder, events = _recorder()

        assert await recorder.record_supersessions([]) == 0
        events.append_events.assert_not_called()
