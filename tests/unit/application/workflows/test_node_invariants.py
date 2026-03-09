"""Tests for strict invariant enforcement in node factories.

Verifies that make_node, create_enricher_node, and make_combiner_node
raise TracklistInvariantError when ANY track lacks a database ID.
Source nodes guarantee persistence, so missing IDs indicate a bug.
"""

import pytest

from src.domain.entities.track import TrackList
from src.domain.exceptions import TracklistInvariantError
from tests.fixtures import make_track


class TestMakeNodeInvariant:
    """make_node raises TracklistInvariantError on any invalid track."""

    @pytest.fixture
    def _load_catalog(self):
        """Import node_catalog to register all nodes."""
        import src.application.workflows.node_catalog  # noqa: F401

    @pytest.mark.usefixtures("_load_catalog")
    async def test_raises_when_any_track_lacks_id(self):
        """Mix of valid and invalid tracks: raises immediately."""
        from src.application.workflows.node_factories import make_node

        node_fn = make_node("filter", "deduplicate")
        mixed_tracklist = TrackList(
            tracks=[make_track(id=1), make_track(id=None), make_track(id=2)]
        )
        context = {"upstream_task_id": "src", "src": {"tracklist": mixed_tracklist}}

        with pytest.raises(TracklistInvariantError, match="1 tracks lack database IDs"):
            await node_fn(context, {})

    @pytest.mark.usefixtures("_load_catalog")
    async def test_passes_with_valid_tracks(self):
        from src.application.workflows.node_factories import make_node

        node_fn = make_node("filter", "deduplicate")
        good_tracklist = TrackList(tracks=[make_track(id=1), make_track(id=2)])
        context = {"upstream_task_id": "src", "src": {"tracklist": good_tracklist}}

        result = await node_fn(context, {})
        assert len(result["tracklist"].tracks) == 2


class TestMakeCombinerNodeInvariant:
    """make_combiner_node raises TracklistInvariantError on any invalid upstream track."""

    @pytest.fixture
    def _load_catalog(self):
        import src.application.workflows.node_catalog  # noqa: F401

    @pytest.mark.usefixtures("_load_catalog")
    async def test_raises_when_any_upstream_track_lacks_id(self):
        """One upstream has a mix of valid/invalid: raises immediately."""
        from src.application.workflows.node_factories import make_combiner_node

        node_fn = make_combiner_node("merge_playlists")
        mixed = TrackList(tracks=[make_track(id=1), make_track(id=None)])
        good = TrackList(tracks=[make_track(id=2)])
        context = {
            "upstream_task_ids": ["a", "b"],
            "a": {"tracklist": mixed},
            "b": {"tracklist": good},
        }

        with pytest.raises(TracklistInvariantError, match="1 tracks lack database IDs"):
            await node_fn(context, {})

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
