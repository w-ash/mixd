"""Tests for combiner audit trail (TrackDecision generation).

Verifies that combiner nodes now produce per-track decisions
so the audit trail has no gap when tracks enter/leave combiners.
"""

import pytest

from src.domain.entities.track import TrackList
from tests.fixtures import make_track


@pytest.fixture(autouse=True)
def _load_catalog():
    """Import node_catalog to register all nodes."""
    import src.application.workflows.node_catalog  # noqa: F401


class TestCombinerDecisions:
    async def test_concatenate_generates_added_decisions(self):
        from src.application.workflows.node_factories import make_combiner_node

        node_fn = make_combiner_node("merge_playlists")
        tl1 = TrackList(tracks=[make_track(id=1)])
        tl2 = TrackList(tracks=[make_track(id=2)])
        context = {
            "upstream_task_ids": ["a", "b"],
            "a": {"tracklist": tl1},
            "b": {"tracklist": tl2},
        }

        result = await node_fn(context, {})

        assert "track_decisions" in result
        decisions = result["track_decisions"]
        assert len(decisions) == 2
        assert all(d.decision == "added" for d in decisions)
        assert all("merge_playlists" in d.reason for d in decisions)

    async def test_intersect_generates_kept_and_removed_decisions(self):
        from src.application.workflows.node_factories import make_combiner_node

        node_fn = make_combiner_node("intersect_playlists")
        tl1 = TrackList(tracks=[make_track(id=1), make_track(id=2)])
        tl2 = TrackList(tracks=[make_track(id=2), make_track(id=3)])
        context = {
            "upstream_task_ids": ["a", "b"],
            "a": {"tracklist": tl1},
            "b": {"tracklist": tl2},
        }

        result = await node_fn(context, {})

        decisions = result["track_decisions"]
        kept = [d for d in decisions if d.decision == "kept"]
        removed = [d for d in decisions if d.decision == "removed"]

        # Track 2 is in both, tracks 1 and 3 are not
        assert len(kept) == 1
        assert kept[0].track_id == 2
        assert len(removed) == 2
        removed_ids = {d.track_id for d in removed}
        assert removed_ids == {1, 3}
