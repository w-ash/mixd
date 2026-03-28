"""Tests for node factory behavior with UUID-based tracks.

Verifies that make_node and make_combiner_node process tracks
correctly now that all tracks have UUIDs from birth.
"""

import pytest

from src.domain.entities.track import TrackList
from tests.fixtures import make_track


class TestMakeNodeInvariant:
    """make_node processes tracks with UUIDs."""

    @pytest.fixture
    def _load_catalog(self):
        """Import node_catalog to register all nodes."""
        import src.application.workflows.node_catalog  # noqa: F401

    @pytest.mark.usefixtures("_load_catalog")
    async def test_passes_with_valid_tracks(self):
        from src.application.workflows.node_factories import make_node

        node_fn = make_node("filter", "deduplicate")
        good_tracklist = TrackList(tracks=[make_track(), make_track()])
        context = {"upstream_task_id": "src", "src": {"tracklist": good_tracklist}}

        result = await node_fn(context, {})
        assert len(result["tracklist"].tracks) == 2


class TestMakeCombinerNodeInvariant:
    """make_combiner_node processes tracks with UUIDs."""

    @pytest.fixture
    def _load_catalog(self):
        import src.application.workflows.node_catalog  # noqa: F401

    @pytest.mark.usefixtures("_load_catalog")
    async def test_passes_with_valid_upstream(self):
        from src.application.workflows.node_factories import make_combiner_node

        node_fn = make_combiner_node("merge_playlists")
        tl1 = TrackList(tracks=[make_track()])
        tl2 = TrackList(tracks=[make_track()])
        context = {
            "upstream_task_ids": ["a", "b"],
            "a": {"tracklist": tl1},
            "b": {"tracklist": tl2},
        }

        result = await node_fn(context, {})
        assert len(result["tracklist"].tracks) == 2
