"""Tests for invariant enforcement and quarantine in node factories.

Verifies that make_node, create_enricher_node, and make_combiner_node
quarantine tracks without database IDs (allowing valid tracks through)
and raise TracklistInvariantError only when ALL tracks are invalid.
"""

import pytest

from src.domain.entities.track import TrackList
from src.domain.exceptions import TracklistInvariantError
from tests.fixtures import make_track


class TestMakeNodeInvariant:
    """make_node quarantines invalid tracks; raises only when all invalid."""

    @pytest.fixture
    def _load_catalog(self):
        """Import node_catalog to register all nodes."""
        import src.application.workflows.node_catalog  # noqa: F401

    @pytest.mark.usefixtures("_load_catalog")
    async def test_raises_when_all_tracks_lack_ids(self):
        from src.application.workflows.node_factories import make_node

        node_fn = make_node("filter", "deduplicate")
        bad_tracklist = TrackList(tracks=[make_track(id=None)])
        context = {"upstream_task_id": "src", "src": {"tracklist": bad_tracklist}}

        with pytest.raises(TracklistInvariantError):
            await node_fn(context, {})

    @pytest.mark.usefixtures("_load_catalog")
    async def test_quarantines_partial_invalid_tracks(self):
        """Mix of valid and invalid tracks: invalid quarantined, valid proceed."""
        from src.application.workflows.node_factories import make_node

        node_fn = make_node("filter", "deduplicate")
        mixed_tracklist = TrackList(
            tracks=[make_track(id=1), make_track(id=None), make_track(id=2)]
        )
        context = {"upstream_task_id": "src", "src": {"tracklist": mixed_tracklist}}

        result = await node_fn(context, {})
        # Only valid tracks (id=1, id=2) proceed
        assert len(result["tracklist"].tracks) == 2

    @pytest.mark.usefixtures("_load_catalog")
    async def test_passes_with_valid_tracks(self):
        from src.application.workflows.node_factories import make_node

        node_fn = make_node("filter", "deduplicate")
        good_tracklist = TrackList(tracks=[make_track(id=1), make_track(id=2)])
        context = {"upstream_task_id": "src", "src": {"tracklist": good_tracklist}}

        result = await node_fn(context, {})
        assert len(result["tracklist"].tracks) == 2


class TestMakeCombinerNodeInvariant:
    """make_combiner_node quarantines invalid tracks per-upstream."""

    @pytest.fixture
    def _load_catalog(self):
        import src.application.workflows.node_catalog  # noqa: F401

    @pytest.mark.usefixtures("_load_catalog")
    async def test_raises_when_all_upstream_tracks_lack_ids(self):
        from src.application.workflows.node_factories import make_combiner_node

        node_fn = make_combiner_node("merge_playlists")
        bad_tracklist = TrackList(tracks=[make_track(id=None)])
        good_tracklist = TrackList(tracks=[make_track(id=1)])
        context = {
            "upstream_task_ids": ["a", "b"],
            "a": {"tracklist": good_tracklist},
            "b": {"tracklist": bad_tracklist},
        }

        with pytest.raises(TracklistInvariantError):
            await node_fn(context, {})

    @pytest.mark.usefixtures("_load_catalog")
    async def test_quarantines_partial_invalid_in_upstream(self):
        """Upstream with mixed valid/invalid: invalid quarantined, valid merged."""
        from src.application.workflows.node_factories import make_combiner_node

        node_fn = make_combiner_node("merge_playlists")
        mixed = TrackList(tracks=[make_track(id=1), make_track(id=None)])
        good = TrackList(tracks=[make_track(id=2)])
        context = {
            "upstream_task_ids": ["a", "b"],
            "a": {"tracklist": mixed},
            "b": {"tracklist": good},
        }

        result = await node_fn(context, {})
        assert len(result["tracklist"].tracks) == 2

    @pytest.mark.usefixtures("_load_catalog")
    async def test_passes_with_valid_upstream(self):
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
        assert len(result["tracklist"].tracks) == 2
