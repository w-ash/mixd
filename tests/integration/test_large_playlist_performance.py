"""Performance tests for large playlists (5k-10k tracks).

Validates that the LIS-based diff engine and execution strategies maintain
efficiency and correctness at scale, meeting Spotify's maximum playlist limits.
"""

import time

import pytest

from src.domain.entities.playlist import Playlist
from src.domain.entities.track import Artist, Track, TrackList
from src.domain.playlist.diff_engine import calculate_playlist_diff
from src.domain.playlist.execution_strategies import (
    APIExecutionStrategy,
    CanonicalExecutionStrategy,
)


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.performance
class TestLargePlaylistPerformance:
    """Test performance and correctness with large playlists.

    Integration tests that validate performance characteristics
    of the playlist diff engine with realistic data sizes.
    Uses proper database isolation to prevent production data pollution.
    """

    @pytest.fixture
    def large_playlist_5k(self):
        """Create playlist with 5,000 tracks."""
        tracks = [
            Track(
                id=i, title=f"Track {i:04d}", artists=[Artist(name=f"Artist {i:04d}")]
            )
            for i in range(5000)
        ]
        return Playlist.from_tracklist(name="5K Test Playlist", tracklist=tracks)

    @pytest.fixture
    def large_playlist_10k(self):
        """Create playlist with 10,000 tracks (Spotify's maximum)."""
        tracks = [
            Track(
                id=i, title=f"Track {i:05d}", artists=[Artist(name=f"Artist {i:05d}")]
            )
            for i in range(10000)
        ]
        return Playlist.from_tracklist(name="10K Test Playlist", tracklist=tracks)

    @pytest.mark.asyncio
    async def test_5k_playlist_idempotency_performance(
        self, large_playlist_5k, db_session, test_data_tracker
    ):
        """Test that 5K unchanged playlist generates 0 operations quickly."""
        target_tracklist = TrackList(tracks=large_playlist_5k.tracks.copy())

        start_time = time.time()
        diff = calculate_playlist_diff(large_playlist_5k, target_tracklist)
        execution_time = time.time() - start_time

        # Should be idempotent (no operations)
        assert not diff.has_changes
        assert len(diff.operations) == 0
        assert diff.confidence_score == 1.0

        # Should complete efficiently for integration test
        assert execution_time < 2.0
        print(f"5K playlist idempotency check: {execution_time:.3f}s")

    @pytest.mark.asyncio
    async def test_10k_playlist_idempotency_performance(
        self, large_playlist_10k, db_session, test_data_tracker
    ):
        """Test that 10K unchanged playlist generates 0 operations quickly."""
        target_tracklist = TrackList(tracks=large_playlist_10k.tracks.copy())

        start_time = time.time()
        diff = calculate_playlist_diff(large_playlist_10k, target_tracklist)
        execution_time = time.time() - start_time

        # Should be idempotent (no operations)
        assert not diff.has_changes
        assert len(diff.operations) == 0
        assert diff.confidence_score == 1.0

        # Should complete in under 3 seconds (acceptable for maximum size)
        assert execution_time < 3.0
        print(f"10K playlist idempotency check: {execution_time:.3f}s")

    @pytest.mark.asyncio
    async def test_5k_playlist_complete_reversal(
        self, large_playlist_5k, db_session, test_data_tracker
    ):
        """Test complete reversal of 5K playlist (worst case reordering)."""
        target_tracks = list(reversed(large_playlist_5k.tracks))
        target_tracklist = TrackList(tracks=target_tracks)

        start_time = time.time()
        diff = calculate_playlist_diff(large_playlist_5k, target_tracklist)
        execution_time = time.time() - start_time

        # Should have many move operations
        assert diff.has_changes
        move_ops = [op for op in diff.operations if op.operation_type.value == "move"]

        # LIS optimization should reduce moves significantly
        # For complete reversal, should need n-1 moves where LIS = 1
        expected_moves = 4999  # All but one track needs to move
        assert len(move_ops) == expected_moves

        # Should complete efficiently for integration test (worst-case 5K reversal)
        assert execution_time < 5.0
        print(
            f"5K playlist complete reversal: {execution_time:.3f}s, {len(move_ops)} moves"
        )

    @pytest.mark.asyncio
    async def test_5k_playlist_partial_reorder(
        self, large_playlist_5k, db_session, test_data_tracker
    ):
        """Test partial reordering that should demonstrate LIS optimization."""
        # Move every 10th track to create a pattern that LIS can optimize
        tracks = large_playlist_5k.tracks.copy()
        target_tracks = []

        # First, add tracks that aren't multiples of 10 (these should stay in place)
        for i, track in enumerate(tracks):
            if i % 10 != 0:
                target_tracks.append(track)

        # Then add every 10th track at the end
        for i, track in enumerate(tracks):
            if i % 10 == 0:
                target_tracks.append(track)

        target_tracklist = TrackList(tracks=target_tracks)

        start_time = time.time()
        diff = calculate_playlist_diff(large_playlist_5k, target_tracklist)
        execution_time = time.time() - start_time

        # Should have move operations only for every 10th track
        assert diff.has_changes
        move_ops = [op for op in diff.operations if op.operation_type.value == "move"]

        # Should need to move about 500 tracks (every 10th)
        # LIS optimization should keep most tracks in place
        assert 450 <= len(move_ops) <= 550  # Some tolerance for LIS optimization

        # Should complete efficiently for integration test
        assert execution_time < 2.0
        print(
            f"5K playlist partial reorder: {execution_time:.3f}s, {len(move_ops)} moves"
        )

    @pytest.mark.asyncio
    async def test_api_strategy_large_playlist(
        self, large_playlist_5k, db_session, test_data_tracker
    ):
        """Test API execution strategy with large playlist."""
        # Create a reordering scenario
        target_tracks = large_playlist_5k.tracks[100:] + large_playlist_5k.tracks[:100]
        target_tracklist = TrackList(tracks=target_tracks)

        diff = calculate_playlist_diff(large_playlist_5k, target_tracklist)

        start_time = time.time()
        api_strategy = APIExecutionStrategy()
        execution_plan = api_strategy.plan_operations(diff)
        execution_time = time.time() - start_time

        # Should complete planning efficiently
        assert execution_time < 2.0

        # Should include position shift simulation
        assert execution_plan.execution_metadata["position_shift_simulation"] is True

        # Operations should be properly sequenced
        assert len(execution_plan.operations) == len(diff.operations)

        print(f"API strategy planning for 5K playlist: {execution_time:.3f}s")

    @pytest.mark.asyncio
    async def test_canonical_strategy_large_playlist(
        self, large_playlist_5k, db_session, test_data_tracker
    ):
        """Test canonical execution strategy with large playlist."""
        # Create a reordering scenario
        target_tracks = large_playlist_5k.tracks[200:] + large_playlist_5k.tracks[:200]
        target_tracklist = TrackList(tracks=target_tracks)

        diff = calculate_playlist_diff(large_playlist_5k, target_tracklist)

        start_time = time.time()
        canonical_strategy = CanonicalExecutionStrategy()
        execution_plan = canonical_strategy.plan_operations(diff)
        execution_time = time.time() - start_time

        # Should complete planning very efficiently (atomic reordering)
        assert execution_time < 1.0

        # Should prefer atomic reordering
        assert execution_plan.use_atomic_reorder is True

        print(f"Canonical strategy planning for 5K playlist: {execution_time:.3f}s")

    @pytest.mark.asyncio
    async def test_duplicate_heavy_playlist_performance(
        self, db_session, test_data_tracker
    ):
        """Test performance with playlist containing many duplicates."""
        # Create playlist where same track appears multiple times
        base_tracks = [
            Track(id=i, title=f"Track {i}", artists=[Artist(name=f"Artist {i}")])
            for i in range(100)  # Only 100 unique tracks
        ]

        # Repeat each track 50 times to create 5K playlist with heavy duplicates
        tracks = []
        for _ in range(50):
            tracks.extend(base_tracks)

        playlist = Playlist.from_tracklist(name="Duplicate Heavy", tracklist=tracks)

        # Reverse the entire playlist
        target_tracks = list(reversed(tracks))
        target_tracklist = TrackList(tracks=target_tracks)

        start_time = time.time()
        diff = calculate_playlist_diff(playlist, target_tracklist)
        execution_time = time.time() - start_time

        # Should handle duplicates correctly
        assert diff.has_changes

        # Should complete efficiently despite duplicates
        assert execution_time < 3.0

        print(f"5K duplicate-heavy playlist reversal: {execution_time:.3f}s")

    @pytest.mark.asyncio
    async def test_memory_efficiency_large_playlist(
        self, large_playlist_10k, db_session, test_data_tracker
    ):
        """Test that large playlist operations don't consume excessive memory."""
        import os

        import psutil

        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss / 1024 / 1024  # MB

        # Perform a complex operation
        target_tracks = (
            large_playlist_10k.tracks[1000:] + large_playlist_10k.tracks[:1000]
        )
        target_tracklist = TrackList(tracks=target_tracks)

        diff = calculate_playlist_diff(large_playlist_10k, target_tracklist)

        # Test both strategies
        api_strategy = APIExecutionStrategy()
        canonical_strategy = CanonicalExecutionStrategy()

        # Exercise both strategy types for memory testing
        api_strategy.plan_operations(diff)
        canonical_strategy.plan_operations(diff)

        final_memory = process.memory_info().rss / 1024 / 1024  # MB
        memory_increase = final_memory - initial_memory

        # Should not consume more than 100MB additional memory
        assert memory_increase < 100

        print(f"Memory usage for 10K playlist operations: +{memory_increase:.1f}MB")

    @pytest.mark.asyncio
    async def test_concurrent_large_playlist_operations(
        self, large_playlist_5k, db_session, test_data_tracker
    ):
        """Test that multiple large playlist operations can be performed concurrently."""
        import asyncio

        async def calculate_diff_async(playlist, target_tracks):
            """Async wrapper for diff calculation."""
            target_tracklist = TrackList(tracks=target_tracks)
            return calculate_playlist_diff(playlist, target_tracklist)

        async def test_concurrent():
            # Create different reordering scenarios
            scenarios = [
                list(reversed(large_playlist_5k.tracks)),  # Complete reversal
                large_playlist_5k.tracks[100:]
                + large_playlist_5k.tracks[:100],  # Rotation
                large_playlist_5k.tracks[::2]
                + large_playlist_5k.tracks[1::2],  # Interleave
            ]

            start_time = time.time()

            # Run all scenarios concurrently
            tasks = [
                calculate_diff_async(large_playlist_5k, target_tracks)
                for target_tracks in scenarios
            ]

            results = await asyncio.gather(*tasks)
            execution_time = time.time() - start_time

            # All should complete successfully
            assert len(results) == 3
            assert all(result.has_changes for result in results)

            # Should complete efficiently even with concurrent operations (allow 6s for concurrent tests)
            assert execution_time < 6.0

            print(f"Concurrent 5K playlist operations: {execution_time:.3f}s")

            return results

        # Run the concurrent test (we're already in an async context)
        results = await test_concurrent()
        assert len(results) == 3

    @pytest.mark.skip(reason="Only run for stress testing - takes significant time")
    @pytest.mark.asyncio
    async def test_stress_test_10k_worst_case(
        self, large_playlist_10k, db_session, test_data_tracker
    ):
        """Stress test with 10K playlist worst-case scenario."""
        # Complete reversal - absolute worst case for reordering
        target_tracks = list(reversed(large_playlist_10k.tracks))
        target_tracklist = TrackList(tracks=target_tracks)

        start_time = time.time()
        diff = calculate_playlist_diff(large_playlist_10k, target_tracklist)

        # Test both strategies
        api_strategy = APIExecutionStrategy()
        canonical_strategy = CanonicalExecutionStrategy()

        # Exercise both strategy types for memory testing
        api_strategy.plan_operations(diff)
        canonical_strategy.plan_operations(diff)

        execution_time = time.time() - start_time

        # Should complete within reasonable time even for worst case
        assert execution_time < 10.0

        # Should generate expected number of operations
        move_ops = [op for op in diff.operations if op.operation_type.value == "move"]
        assert len(move_ops) == 9999  # All but one track needs to move

        print(f"10K playlist worst-case stress test: {execution_time:.3f}s")
