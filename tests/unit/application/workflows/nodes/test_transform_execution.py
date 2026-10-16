"""Tests for transform execution through the workflow registry.

Tests that TRANSFORM_REGISTRY entries in application/workflows/transform_definitions.py
correctly wire domain sorting functions. These are application-layer wiring tests,
not domain unit tests.
"""

from collections.abc import MutableSequence
from datetime import UTC, datetime
import random

import pytest

from src.application.workflows.nodes.transform_definitions import TRANSFORM_REGISTRY
from src.domain.entities.track import ArtistCredit, Track, TrackList
import src.domain.transforms.shuffle as shuffle_module
from tests.fixtures import TEST_USER_ID


class TestTrackAttributeSorting:
    """Test sorting by track attributes (title, artist, etc.) vs external metrics."""

    def test_sort_by_title_attribute_directly(self):
        """Test sorting by track title using track attribute directly."""
        # Arrange: Create tracks with different titles
        t1 = Track(
            title="Zebra",
            artists=[ArtistCredit(credited_name="Artist1")],
            user_id=TEST_USER_ID,
        )
        t2 = Track(
            title="Apple",
            artists=[ArtistCredit(credited_name="Artist2")],
            user_id=TEST_USER_ID,
        )
        t3 = Track(
            title="Banana",
            artists=[ArtistCredit(credited_name="Artist3")],
            user_id=TEST_USER_ID,
        )
        tracks = [t1, t2, t3]
        tracklist = TrackList(tracks=tracks)

        # Act: Sort by title using transform definitions
        sorter_fn = TRANSFORM_REGISTRY["sorter"]["by_metric"].factory(
            _ctx=None, cfg={"metric_name": "title", "reverse": False}
        )
        sorted_tracklist = sorter_fn(tracklist)

        # Assert: Tracks should be sorted alphabetically by title
        assert len(sorted_tracklist.tracks) == 3
        assert sorted_tracklist.tracks[0].title == "Apple"
        assert sorted_tracklist.tracks[1].title == "Banana"
        assert sorted_tracklist.tracks[2].title == "Zebra"

        # Assert: Metrics should be populated in tracklist metadata
        title_metrics = sorted_tracklist.metadata["metrics"]["title"]
        assert title_metrics[t1.id] == "Zebra"
        assert title_metrics[t2.id] == "Apple"
        assert title_metrics[t3.id] == "Banana"

    def test_sort_by_title_with_string_reverse_false_is_ascending(self):
        """The editor persists reverse as the string "false"; sort ascends."""
        tracks = [
            Track(
                title="Zebra",
                artists=[ArtistCredit(credited_name="Artist1")],
                user_id=TEST_USER_ID,
            ),
            Track(
                title="Apple",
                artists=[ArtistCredit(credited_name="Artist2")],
                user_id=TEST_USER_ID,
            ),
            Track(
                title="Banana",
                artists=[ArtistCredit(credited_name="Artist3")],
                user_id=TEST_USER_ID,
            ),
        ]
        tracklist = TrackList(tracks=tracks)

        sorter_fn = TRANSFORM_REGISTRY["sorter"]["by_metric"].factory(
            _ctx=None, cfg={"metric_name": "title", "reverse": "false"}
        )
        sorted_tracklist = sorter_fn(tracklist)

        assert [t.title for t in sorted_tracklist.tracks] == [
            "Apple",
            "Banana",
            "Zebra",
        ]

    def test_sort_by_title_with_string_reverse_true_is_descending(self):
        """The string "true" sorts descending, matching the bool form."""
        tracks = [
            Track(
                title="Apple",
                artists=[ArtistCredit(credited_name="Artist1")],
                user_id=TEST_USER_ID,
            ),
            Track(
                title="Zebra",
                artists=[ArtistCredit(credited_name="Artist2")],
                user_id=TEST_USER_ID,
            ),
        ]
        tracklist = TrackList(tracks=tracks)

        sorter_fn = TRANSFORM_REGISTRY["sorter"]["by_metric"].factory(
            _ctx=None, cfg={"metric_name": "title", "reverse": "true"}
        )
        sorted_tracklist = sorter_fn(tracklist)

        assert [t.title for t in sorted_tracklist.tracks] == ["Zebra", "Apple"]

    def test_sort_by_artist_attribute_directly(self):
        """Test sorting by primary artist name using track attribute directly."""
        # Arrange: Create tracks with different artists
        tracks = [
            Track(
                title="Song1",
                artists=[ArtistCredit(credited_name="Zebra Band")],
                user_id=TEST_USER_ID,
            ),
            Track(
                title="Song2",
                artists=[ArtistCredit(credited_name="Apple Band")],
                user_id=TEST_USER_ID,
            ),
            Track(
                title="Song3",
                artists=[ArtistCredit(credited_name="Banana Band")],
                user_id=TEST_USER_ID,
            ),
        ]
        tracklist = TrackList(tracks=tracks)

        # Act: Sort by artist using transform definitions
        sorter_fn = TRANSFORM_REGISTRY["sorter"]["by_metric"].factory(
            _ctx=None, cfg={"metric_name": "artist", "reverse": False}
        )
        sorted_tracklist = sorter_fn(tracklist)

        # Assert: Tracks should be sorted alphabetically by artist
        assert len(sorted_tracklist.tracks) == 3
        assert sorted_tracklist.tracks[0].artists[0].credited_name == "Apple Band"
        assert sorted_tracklist.tracks[1].artists[0].credited_name == "Banana Band"
        assert sorted_tracklist.tracks[2].artists[0].credited_name == "Zebra Band"

    def test_sort_by_release_date_attribute_directly(self):
        """Test sorting by release date using track attribute directly."""
        # Arrange: Create tracks with different release dates
        date1 = datetime(2020, 1, 1, tzinfo=UTC)
        date2 = datetime(2021, 1, 1, tzinfo=UTC)
        date3 = datetime(2019, 1, 1, tzinfo=UTC)

        tracks = [
            Track(
                title="Song1",
                artists=[ArtistCredit(credited_name="Artist1")],
                release_date=date1,
                user_id=TEST_USER_ID,
            ),
            Track(
                title="Song2",
                artists=[ArtistCredit(credited_name="Artist2")],
                release_date=date2,
                user_id=TEST_USER_ID,
            ),
            Track(
                title="Song3",
                artists=[ArtistCredit(credited_name="Artist3")],
                release_date=date3,
                user_id=TEST_USER_ID,
            ),
        ]
        tracklist = TrackList(tracks=tracks)

        # Act: Sort by release date using transform definitions
        sorter_fn = TRANSFORM_REGISTRY["sorter"]["by_metric"].factory(
            _ctx=None, cfg={"metric_name": "release_date", "reverse": False}
        )
        sorted_tracklist = sorter_fn(tracklist)

        # Assert: Tracks should be sorted chronologically
        assert len(sorted_tracklist.tracks) == 3
        assert sorted_tracklist.tracks[0].release_date == date3  # 2019
        assert sorted_tracklist.tracks[1].release_date == date1  # 2020
        assert sorted_tracklist.tracks[2].release_date == date2  # 2021

    def test_sort_by_external_metric_from_metadata(self):
        """Test sorting by external metric (existing behavior should work)."""
        # Arrange: Create tracks with external metric values in metadata
        t1 = Track(
            title="Song1",
            artists=[ArtistCredit(credited_name="Artist1")],
            user_id=TEST_USER_ID,
        )
        t2 = Track(
            title="Song2",
            artists=[ArtistCredit(credited_name="Artist2")],
            user_id=TEST_USER_ID,
        )
        t3 = Track(
            title="Song3",
            artists=[ArtistCredit(credited_name="Artist3")],
            user_id=TEST_USER_ID,
        )
        tracks = [t1, t2, t3]

        # Add external metrics to tracklist metadata
        external_metrics = {
            "lastfm_user_playcount": {
                t1.id: 50,  # track 1 has 50 plays
                t2.id: 100,  # track 2 has 100 plays
                t3.id: 25,  # track 3 has 25 plays
            }
        }
        tracklist = TrackList(tracks=tracks, metadata={"metrics": external_metrics})

        # Act: Sort by external metric using transform definitions
        sorter_fn = TRANSFORM_REGISTRY["sorter"]["by_metric"].factory(
            _ctx=None, cfg={"metric_name": "lastfm_user_playcount", "reverse": True}
        )
        sorted_tracklist = sorter_fn(tracklist)

        # Assert: Tracks should be sorted by play count (highest first)
        assert len(sorted_tracklist.tracks) == 3
        assert sorted_tracklist.tracks[0].id == t2.id  # 100 plays
        assert sorted_tracklist.tracks[1].id == t1.id  # 50 plays
        assert sorted_tracklist.tracks[2].id == t3.id  # 25 plays


class _ReversingRng(random.Random):
    """Deterministic stand-in for the shuffle RNG: a full shuffle reverses."""

    def shuffle(self, x: MutableSequence[object]) -> None:
        x.reverse()


class TestWeightedShuffleSorting:
    """Test the weighted shuffle sorter functionality."""

    @pytest.mark.parametrize(
        ("strength", "expected"),
        [
            (0.0, ["First", "Second", "Third"]),
            (1.0, ["Third", "Second", "First"]),
        ],
    )
    def test_shuffle_strength_config_reaches_the_domain_shuffle(
        self, monkeypatch: pytest.MonkeyPatch, strength: float, expected: list[str]
    ):
        """shuffle_strength selects identity at 0.0 and a full RNG shuffle at 1.0.

        The RNG boundary is replaced so the full shuffle is deterministic.
        """
        monkeypatch.setattr(shuffle_module, "_DEFAULT_RNG", _ReversingRng())
        tracklist = TrackList(
            tracks=[
                Track(
                    title=title,
                    artists=[ArtistCredit(credited_name=f"Artist {title}")],
                    user_id=TEST_USER_ID,
                )
                for title in ("First", "Second", "Third")
            ]
        )

        sorter_fn = TRANSFORM_REGISTRY["sorter"]["weighted_shuffle"].factory(
            _ctx=None, cfg={"shuffle_strength": strength}
        )
        result = sorter_fn(tracklist)

        assert [t.title for t in result.tracks] == expected

    def test_weighted_shuffle_invalid_bounds(self):
        """Test weighted shuffle rejects invalid strength values."""
        tracks = [
            Track(
                title="Test",
                artists=[ArtistCredit(credited_name="Test")],
                user_id=TEST_USER_ID,
            )
        ]
        tracklist = TrackList(tracks=tracks)

        for invalid_strength in [-0.1, 1.1]:
            with pytest.raises(ValueError):  # ruff:ignore[pytest-raises-with-multiple-statements]
                sorter_fn = TRANSFORM_REGISTRY["sorter"]["weighted_shuffle"].factory(
                    _ctx=None, cfg={"shuffle_strength": invalid_strength}
                )
                sorter_fn(tracklist)
