"""Tests for BaseMatchingProvider template method pattern.

This test suite validates the base class workflow orchestration without
testing business logic (which stays in domain layer).
"""

from uuid import UUID

import pytest

from src.domain.entities import Artist, Track
from src.domain.matching.types import (
    MatchFailure,
    MatchFailureReason,
    ProviderMatchResult,
    RawProviderMatch,
)
from src.infrastructure.connectors._shared.matching_provider import (
    BaseMatchingProvider,
)


# Test implementation of BaseMatchingProvider
class ConcreteProvider(BaseMatchingProvider):
    """Concrete provider for testing base class behavior."""

    def __init__(self) -> None:
        """Initialize with test data storage."""
        self.isrc_calls: list[list[Track]] = []
        self.artist_title_calls: list[list[Track]] = []
        self.isrc_results: dict[UUID, RawProviderMatch] = {}
        self.artist_title_results: dict[UUID, RawProviderMatch] = {}
        self.isrc_failures: list[MatchFailure] = []
        self.artist_title_failures: list[MatchFailure] = []

    @property
    def service_name(self) -> str:
        """Return test service name."""
        return "test_service"

    async def _match_by_isrc(
        self, tracks: list[Track]
    ) -> tuple[dict[UUID, RawProviderMatch], list[MatchFailure]]:
        """Record ISRC method call and return configured results."""
        self.isrc_calls.append(tracks)
        return self.isrc_results, self.isrc_failures

    async def _match_by_artist_title(
        self, tracks: list[Track]
    ) -> tuple[dict[UUID, RawProviderMatch], list[MatchFailure]]:
        """Record artist/title method call and return configured results."""
        self.artist_title_calls.append(tracks)
        return self.artist_title_results, self.artist_title_failures


class TestBaseMatchingProviderAbstractEnforcement:
    """Test that abstract methods are enforced."""

    def test_cannot_instantiate_base_class_directly(self):
        """BaseMatchingProvider cannot be instantiated without implementing abstract methods."""
        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            BaseMatchingProvider()  # type: ignore[abstract]

    def test_subclass_must_implement_match_by_isrc(self):
        """Subclass must implement _match_by_isrc abstract method."""

        class IncompleteProvider(BaseMatchingProvider):
            @property
            def service_name(self) -> str:
                return "test"

            async def _match_by_artist_title(self, tracks):
                return {}, []

        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            IncompleteProvider()  # type: ignore[abstract]

    def test_subclass_must_implement_match_by_artist_title(self):
        """Subclass must implement _match_by_artist_title abstract method."""

        class IncompleteProvider(BaseMatchingProvider):
            @property
            def service_name(self) -> str:
                return "test"

            async def _match_by_isrc(self, tracks):
                return {}, []

        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            IncompleteProvider()  # type: ignore[abstract]


class TestBaseMatchingProviderTrackPartitioning:
    """Test track partitioning logic."""

    def test_partition_tracks_with_isrc_only(self):
        """Tracks with ISRC should be partitioned to ISRC group."""
        provider = ConcreteProvider()
        t1 = Track(
            title="Song 1", isrc="USRC11111111", artists=[Artist(name="Artist 1")]
        )
        t2 = Track(
            title="Song 2", isrc="USRC22222222", artists=[Artist(name="Artist 2")]
        )
        tracks = [t1, t2]

        isrc_tracks, artist_title_tracks, unprocessable_tracks = (
            provider._partition_tracks(tracks)
        )

        assert len(isrc_tracks) == 2
        assert len(artist_title_tracks) == 0
        assert len(unprocessable_tracks) == 0
        assert isrc_tracks[0].id == t1.id
        assert isrc_tracks[1].id == t2.id

    def test_partition_tracks_with_artist_title_only(self):
        """Tracks with artist+title but no ISRC should be partitioned to artist/title group."""
        provider = ConcreteProvider()
        t1 = Track(title="Song 1", artists=[Artist(name="Artist 1")])
        t2 = Track(title="Song 2", artists=[Artist(name="Artist 2")])
        tracks = [t1, t2]

        isrc_tracks, artist_title_tracks, unprocessable_tracks = (
            provider._partition_tracks(tracks)
        )

        assert len(isrc_tracks) == 0
        assert len(artist_title_tracks) == 2
        assert len(unprocessable_tracks) == 0
        assert artist_title_tracks[0].id == t1.id
        assert artist_title_tracks[1].id == t2.id

    def test_partition_tracks_missing_title(self):
        """Tracks without title should be partitioned to unprocessable group."""
        provider = ConcreteProvider()
        tracks = [
            Track(title="", artists=[Artist(name="Artist 1")]),
        ]

        isrc_tracks, artist_title_tracks, unprocessable_tracks = (
            provider._partition_tracks(tracks)
        )

        assert len(isrc_tracks) == 0
        assert len(artist_title_tracks) == 0
        assert len(unprocessable_tracks) == 1

    def test_partition_mixed_tracks(self):
        """Mixed tracks should be partitioned to appropriate groups."""
        provider = ConcreteProvider()
        t1 = Track(
            title="Song 1", isrc="USRC11111111", artists=[Artist(name="Artist 1")]
        )  # ISRC
        t2 = Track(title="Song 2", artists=[Artist(name="Artist 2")])  # Artist/title
        t3 = Track(
            title="", artists=[Artist(name="Artist 3")]
        )  # Unprocessable (no title)
        t4 = Track(
            title="Song 4", isrc="USRC44444444", artists=[Artist(name="Artist 4")]
        )  # ISRC
        tracks = [t1, t2, t3, t4]

        isrc_tracks, artist_title_tracks, unprocessable_tracks = (
            provider._partition_tracks(tracks)
        )

        assert len(isrc_tracks) == 2
        assert len(artist_title_tracks) == 1
        assert len(unprocessable_tracks) == 1
        assert isrc_tracks[0].id == t1.id
        assert isrc_tracks[1].id == t4.id
        assert artist_title_tracks[0].id == t2.id
        assert unprocessable_tracks[0].id == t3.id

    def test_partition_empty_list(self):
        """Empty track list should return empty partitions."""
        provider = ConcreteProvider()
        tracks: list[Track] = []

        isrc_tracks, artist_title_tracks, unprocessable_tracks = (
            provider._partition_tracks(tracks)
        )

        assert len(isrc_tracks) == 0
        assert len(artist_title_tracks) == 0
        assert len(unprocessable_tracks) == 0

    def test_partition_isrc_takes_priority(self):
        """Tracks with ISRC should go to ISRC group even if they have artist/title."""
        provider = ConcreteProvider()
        tracks = [
            Track(
                title="Song 1",
                isrc="USRC11111111",
                artists=[Artist(name="Artist 1")],
            ),
        ]

        isrc_tracks, artist_title_tracks, unprocessable_tracks = (
            provider._partition_tracks(tracks)
        )

        assert len(isrc_tracks) == 1
        assert len(artist_title_tracks) == 0
        assert len(unprocessable_tracks) == 0


class TestBaseMatchingProviderTemplateMethod:
    """Test the template method workflow orchestration."""

    async def test_fetch_raw_matches_calls_isrc_method_for_isrc_tracks(self):
        """Template method should call _match_by_isrc for tracks with ISRC."""
        provider = ConcreteProvider()
        t1 = Track(
            title="Song 1", isrc="USRC11111111", artists=[Artist(name="Artist 1")]
        )
        tracks = [t1]

        await provider.fetch_raw_matches_for_tracks(tracks)

        assert len(provider.isrc_calls) == 1
        assert len(provider.isrc_calls[0]) == 1
        assert provider.isrc_calls[0][0].id == t1.id

    async def test_fetch_raw_matches_calls_artist_title_method_for_non_isrc_tracks(
        self,
    ):
        """Template method should call _match_by_artist_title for tracks without ISRC."""
        provider = ConcreteProvider()
        t1 = Track(title="Song 1", artists=[Artist(name="Artist 1")])
        tracks = [t1]

        await provider.fetch_raw_matches_for_tracks(tracks)

        assert len(provider.artist_title_calls) == 1
        assert len(provider.artist_title_calls[0]) == 1
        assert provider.artist_title_calls[0][0].id == t1.id

    async def test_fetch_raw_matches_filters_already_matched_from_artist_title(self):
        """Tracks matched by ISRC should not be sent to artist/title method."""
        provider = ConcreteProvider()
        t1 = Track(
            title="Song 1", isrc="USRC11111111", artists=[Artist(name="Artist 1")]
        )
        t2 = Track(title="Song 2", artists=[Artist(name="Artist 2")])
        tracks = [t1, t2]

        # Configure track 1 to match via ISRC
        provider.isrc_results = {
            t1.id: RawProviderMatch(
                connector_id="spotify:1",
                match_method="isrc",
                service_data={"title": "Song 1"},
            )
        }

        await provider.fetch_raw_matches_for_tracks(tracks)

        # ISRC method should get track 1
        assert len(provider.isrc_calls) == 1
        assert len(provider.isrc_calls[0]) == 1
        assert provider.isrc_calls[0][0].id == t1.id

        # Artist/title method should ONLY get track 2 (track 1 already matched)
        assert len(provider.artist_title_calls) == 1
        assert len(provider.artist_title_calls[0]) == 1
        assert provider.artist_title_calls[0][0].id == t2.id

    async def test_fetch_raw_matches_merges_results_from_both_methods(self):
        """Results from ISRC and artist/title methods should be merged."""
        provider = ConcreteProvider()
        t1 = Track(
            title="Song 1", isrc="USRC11111111", artists=[Artist(name="Artist 1")]
        )
        t2 = Track(title="Song 2", artists=[Artist(name="Artist 2")])
        tracks = [t1, t2]

        # Configure results from both methods
        provider.isrc_results = {
            t1.id: RawProviderMatch(
                connector_id="spotify:1",
                match_method="isrc",
                service_data={"title": "Song 1"},
            )
        }
        provider.artist_title_results = {
            t2.id: RawProviderMatch(
                connector_id="spotify:2",
                match_method="artist_title",
                service_data={"title": "Song 2"},
            )
        }

        result = await provider.fetch_raw_matches_for_tracks(tracks)

        assert len(result.matches) == 2
        assert t1.id in result.matches
        assert t2.id in result.matches
        assert result.matches[t1.id]["match_method"] == "isrc"
        assert result.matches[t2.id]["match_method"] == "artist_title"

    async def test_fetch_raw_matches_merges_failures_from_both_methods(self):
        """Failures from ISRC and artist/title methods should be merged."""
        provider = ConcreteProvider()
        t1 = Track(
            title="Song 1", isrc="USRC11111111", artists=[Artist(name="Artist 1")]
        )
        t2 = Track(title="Song 2", artists=[Artist(name="Artist 2")])
        tracks = [t1, t2]

        # Configure failures from both methods
        provider.isrc_failures = [
            MatchFailure(
                track_id=t1.id,
                reason=MatchFailureReason.NO_RESULTS,
                service="test_service",
                method="isrc",
                details="No results",
            )
        ]
        provider.artist_title_failures = [
            MatchFailure(
                track_id=t2.id,
                reason=MatchFailureReason.NO_RESULTS,
                service="test_service",
                method="artist_title",
                details="No results",
            )
        ]

        result = await provider.fetch_raw_matches_for_tracks(tracks)

        assert len(result.failures) == 2
        assert result.failures[0].track_id == t1.id
        assert result.failures[0].method == "isrc"
        assert result.failures[1].track_id == t2.id
        assert result.failures[1].method == "artist_title"

    async def test_fetch_raw_matches_creates_failures_for_unprocessable_tracks(self):
        """Tracks without ISRC and without title should generate failures."""
        provider = ConcreteProvider()
        t1 = Track(title="", artists=[Artist(name="Artist 1")])  # No title
        tracks = [t1]

        result = await provider.fetch_raw_matches_for_tracks(tracks)

        assert len(result.failures) == 1
        assert result.failures[0].track_id == t1.id
        assert result.failures[0].reason == MatchFailureReason.NO_METADATA
        assert result.failures[0].service == "test_service"
        assert "missing artist or title" in result.failures[0].details.lower()

    async def test_fetch_raw_matches_handles_empty_track_list(self):
        """Empty track list should return empty result."""
        provider = ConcreteProvider()
        tracks: list[Track] = []

        result = await provider.fetch_raw_matches_for_tracks(tracks)

        assert len(result.matches) == 0
        assert len(result.failures) == 0
        assert len(provider.isrc_calls) == 0
        assert len(provider.artist_title_calls) == 0

    async def test_fetch_raw_matches_skips_isrc_if_no_isrc_tracks(self):
        """ISRC method should not be called if no tracks have ISRC."""
        provider = ConcreteProvider()
        tracks = [
            Track(title="Song 1", artists=[Artist(name="Artist 1")]),
        ]

        await provider.fetch_raw_matches_for_tracks(tracks)

        assert len(provider.isrc_calls) == 0
        assert len(provider.artist_title_calls) == 1

    async def test_fetch_raw_matches_skips_artist_title_if_no_eligible_tracks(self):
        """Artist/title method should not be called if all tracks matched by ISRC."""
        provider = ConcreteProvider()
        t1 = Track(
            title="Song 1", isrc="USRC11111111", artists=[Artist(name="Artist 1")]
        )
        tracks = [t1]

        # Configure ISRC to match
        provider.isrc_results = {
            t1.id: RawProviderMatch(
                connector_id="spotify:1",
                match_method="isrc",
                service_data={"title": "Song 1"},
            )
        }

        await provider.fetch_raw_matches_for_tracks(tracks)

        assert len(provider.isrc_calls) == 1
        assert len(provider.artist_title_calls) == 0  # Not called!

    async def test_fetch_raw_matches_returns_provider_match_result(self):
        """Template method should return ProviderMatchResult."""
        provider = ConcreteProvider()
        t1 = Track(
            title="Song 1", isrc="USRC11111111", artists=[Artist(name="Artist 1")]
        )
        tracks = [t1]

        provider.isrc_results = {
            t1.id: RawProviderMatch(
                connector_id="spotify:1",
                match_method="isrc",
                service_data={"title": "Song 1"},
            )
        }

        result = await provider.fetch_raw_matches_for_tracks(tracks)

        assert isinstance(result, ProviderMatchResult)
        assert len(result.matches) == 1
        assert t1.id in result.matches


class TestBaseMatchingProviderValidation:
    """Test validation helper methods."""

    def test_has_isrc_returns_true_for_track_with_isrc(self):
        """Track with ISRC should pass ISRC validation."""
        provider = ConcreteProvider()
        track = Track(
            title="Song", isrc="USRC11111111", artists=[Artist(name="Artist")]
        )

        assert provider._has_isrc(track) is True

    def test_has_isrc_returns_false_for_track_without_isrc(self):
        """Track without ISRC should fail ISRC validation."""
        provider = ConcreteProvider()
        track = Track(title="Song", artists=[Artist(name="Artist")])

        assert provider._has_isrc(track) is False

    def test_has_artist_and_title_returns_true_for_valid_track(self):
        """Track with artist and title should pass artist/title validation."""
        provider = ConcreteProvider()
        track = Track(title="Song", artists=[Artist(name="Artist")])

        assert provider._has_artist_and_title(track) is True

    def test_has_artist_and_title_returns_false_without_title(self):
        """Track without title should fail artist/title validation."""
        provider = ConcreteProvider()
        track = Track(title="", artists=[Artist(name="Artist")])

        assert provider._has_artist_and_title(track) is False


class TestBaseMatchingProviderProgressCallback:
    """Test progress_callback invocation during matching phases."""

    async def test_callback_called_after_isrc_phase(self):
        """Progress callback is invoked after ISRC matching completes."""
        from unittest.mock import AsyncMock

        provider = ConcreteProvider()
        callback = AsyncMock()
        t1 = Track(
            title="Song 1", isrc="USRC11111111", artists=[Artist(name="Artist 1")]
        )
        tracks = [t1]

        provider.isrc_results = {
            t1.id: RawProviderMatch(
                connector_id="spotify:1",
                match_method="isrc",
                service_data={"title": "Song 1"},
            )
        }

        await provider.fetch_raw_matches_for_tracks(tracks, progress_callback=callback)

        callback.assert_called()
        args = callback.call_args_list[0].args
        assert args[0] == 1  # completed
        assert args[1] == 1  # total
        assert "ISRC matching complete" in args[2]
        assert "1 matched" in args[2]

    async def test_callback_called_after_artist_title_phase(self):
        """Progress callback is invoked after artist/title matching completes."""
        from unittest.mock import AsyncMock

        provider = ConcreteProvider()
        callback = AsyncMock()
        t1 = Track(title="Song 1", artists=[Artist(name="Artist 1")])
        tracks = [t1]

        provider.artist_title_results = {
            t1.id: RawProviderMatch(
                connector_id="spotify:1",
                match_method="artist_title",
                service_data={"title": "Song 1"},
            )
        }

        await provider.fetch_raw_matches_for_tracks(tracks, progress_callback=callback)

        callback.assert_called_once()
        args = callback.call_args.args
        assert args[0] == 1  # completed
        assert args[1] == 1  # total
        assert "Artist/title matching complete" in args[2]
        assert "1 matched" in args[2]

    async def test_callback_called_for_both_phases(self):
        """Progress callback is invoked after each matching phase."""
        from unittest.mock import AsyncMock

        provider = ConcreteProvider()
        callback = AsyncMock()
        t1 = Track(
            title="Song 1", isrc="USRC11111111", artists=[Artist(name="Artist 1")]
        )
        t2 = Track(title="Song 2", artists=[Artist(name="Artist 2")])
        tracks = [t1, t2]

        provider.isrc_results = {
            t1.id: RawProviderMatch(
                connector_id="spotify:1",
                match_method="isrc",
                service_data={"title": "Song 1"},
            )
        }
        provider.artist_title_results = {
            t2.id: RawProviderMatch(
                connector_id="spotify:2",
                match_method="artist_title",
                service_data={"title": "Song 2"},
            )
        }

        await provider.fetch_raw_matches_for_tracks(tracks, progress_callback=callback)

        assert callback.call_count == 2

        # First call: after ISRC phase (1 ISRC track completed out of 2 total)
        first_args = callback.call_args_list[0].args
        assert first_args[0] == 1  # completed (1 ISRC track)
        assert first_args[1] == 2  # total

        # Second call: after artist/title phase (all tracks completed)
        second_args = callback.call_args_list[1].args
        assert second_args[0] == 2  # completed (all tracks)
        assert second_args[1] == 2  # total

    async def test_no_callback_when_none(self):
        """No error when progress_callback is None."""
        provider = ConcreteProvider()
        t1 = Track(
            title="Song 1", isrc="USRC11111111", artists=[Artist(name="Artist 1")]
        )
        tracks = [t1]

        provider.isrc_results = {
            t1.id: RawProviderMatch(
                connector_id="spotify:1",
                match_method="isrc",
                service_data={"title": "Song 1"},
            )
        }

        # Should not raise - progress_callback=None is the default
        result = await provider.fetch_raw_matches_for_tracks(tracks)
        assert len(result.matches) == 1

    async def test_callback_not_called_for_empty_tracks(self):
        """Progress callback is not invoked when no tracks are provided."""
        from unittest.mock import AsyncMock

        provider = ConcreteProvider()
        callback = AsyncMock()

        await provider.fetch_raw_matches_for_tracks([], progress_callback=callback)

        callback.assert_not_called()
