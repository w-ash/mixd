"""Investigation tests for pylast library blocking behavior.

These tests investigate the root cause of slow API calls by examining
pylast library internals and network behavior patterns.
"""

import asyncio
import threading
import time
from unittest.mock import MagicMock, patch

import pylast
import pytest

from src.infrastructure.connectors.lastfm.client import LastFMAPIClient


@pytest.mark.diagnostic
class TestPylastBlockingBehavior:
    """Tests to investigate pylast library blocking patterns."""

    @pytest.fixture
    def mock_pylast_with_delays(self):
        """Mock pylast that simulates various delay patterns."""
        mock_network = MagicMock(spec=pylast.LastFMNetwork)

        def simulate_network_delays(*args, **kwargs):
            """Simulate different network delay patterns."""
            # Random delays to simulate real network conditions
            import random

            delay = random.uniform(0.1, 3.0)  # 100ms to 3s
            time.sleep(delay)

            mock_track = MagicMock(spec=pylast.Track)
            mock_track.get_title.return_value = "Test Track"
            return mock_track

        mock_network.get_track.side_effect = simulate_network_delays
        return mock_network

    @pytest.mark.asyncio
    async def test_asyncio_to_thread_behavior_under_load(self):
        """Test asyncio.to_thread behavior with blocking calls."""

        def blocking_call(call_id: int, duration: float):
            """Simulate blocking network call."""
            start = time.time()
            time.sleep(duration)  # Simulate network delay
            actual_duration = time.time() - start
            return {
                "call_id": call_id,
                "requested_duration": duration,
                "actual_duration": actual_duration,
                "thread_id": threading.get_ident(),
            }

        # Test multiple concurrent blocking calls
        call_durations = [0.5, 1.0, 0.3, 1.5, 0.8]  # Various delays

        start_time = time.time()
        tasks = [
            asyncio.create_task(asyncio.to_thread(blocking_call, i, duration))
            for i, duration in enumerate(call_durations)
        ]

        results = await asyncio.gather(*tasks)
        total_time = time.time() - start_time

        # Analyze results
        thread_ids = [r["thread_id"] for r in results]
        unique_threads = len(set(thread_ids))

        max_individual_duration = max(r["actual_duration"] for r in results)

        print("\n🔍 asyncio.to_thread Analysis:")
        print(f"   Total time: {total_time:.3f}s")
        print(f"   Max individual: {max_individual_duration:.3f}s")
        print(f"   Unique threads: {unique_threads}")
        print(f"   Expected sequential: {sum(call_durations):.3f}s")

        # Should use multiple threads
        assert unique_threads >= 2, f"Not using multiple threads: {unique_threads}"

        # Should complete faster than sequential
        sequential_time = sum(call_durations)
        assert total_time < sequential_time * 0.8, (
            f"Not truly concurrent: {total_time:.3f}s vs {sequential_time:.3f}s"
        )

        # Total time should be close to longest individual call
        assert total_time < max_individual_duration + 0.3, (
            f"Unexpected total time: {total_time:.3f}s"
        )

    @pytest.mark.asyncio
    async def test_timeout_wrapper_effectiveness(self):
        """Test timeout wrapper for slow pylast calls."""

        def very_slow_call():
            """Simulate very slow network call."""
            time.sleep(5.0)  # 5 seconds
            return "Should not reach this"

        def medium_call():
            """Simulate medium speed call."""
            time.sleep(0.5)  # 500ms
            return "Medium result"

        # Test timeout wrapper
        start_time = time.time()

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(very_slow_call),
                timeout=1.0,  # 1 second timeout
            )
            pytest.fail("Should have timed out")

        except TimeoutError:
            timeout_duration = time.time() - start_time
            print(f"✓ Timeout caught after {timeout_duration:.3f}s")
            assert 0.9 < timeout_duration < 1.2, (
                f"Timeout timing off: {timeout_duration:.3f}s"
            )

        # Test successful completion within timeout
        start_time = time.time()
        result = await asyncio.wait_for(asyncio.to_thread(medium_call), timeout=2.0)
        success_duration = time.time() - start_time

        assert result == "Medium result"
        assert 0.4 < success_duration < 0.7, (
            f"Medium call timing: {success_duration:.3f}s"
        )

    def test_pylast_library_internals_investigation(self):
        """Investigate pylast library for potential blocking issues."""

        # Test if we can mock/inspect pylast internals
        original_network_init = pylast.LastFMNetwork.__init__
        init_calls = []

        def track_init(self, *args, **kwargs):
            init_calls.append({"args": args, "kwargs": kwargs, "time": time.time()})
            return original_network_init(self, *args, **kwargs)

        with patch.object(pylast.LastFMNetwork, "__init__", track_init):
            # Create network instance
            try:
                network = pylast.LastFMNetwork("test_key")
                assert len(init_calls) == 1, "Network init not tracked"

                # Check if network has any obvious blocking patterns
                # Look for session/connection pooling attributes
                attrs_to_check = [
                    "session",  # HTTP session
                    "_session",  # Private session
                    "timeout",  # Timeout settings
                    "_timeout",  # Private timeout
                    "pool",  # Connection pool
                    "_pool",  # Private pool
                ]

                network_attrs = {}
                for attr in attrs_to_check:
                    if hasattr(network, attr):
                        network_attrs[attr] = type(getattr(network, attr)).__name__

                print("\n🔬 Pylast Network Attributes:")
                for attr, attr_type in network_attrs.items():
                    print(f"   {attr}: {attr_type}")

                if not network_attrs:
                    print("   No obvious HTTP session/timeout attributes found")

            except Exception as e:
                print(f"Failed to create pylast network: {e}")

    @pytest.mark.asyncio
    async def test_http_session_behavior_simulation(self):
        """Test HTTP session behavior that might cause blocking."""

        # Simulate the pattern that pylast might use

        async def simulate_pylast_pattern():
            """Simulate how pylast might make HTTP requests."""
            # pylast likely does something like this internally

            def sync_http_request(url: str):
                """Simulate synchronous HTTP request like pylast does."""
                import random
                import time

                # Simulate DNS resolution delay
                time.sleep(random.uniform(0.1, 0.3))

                # Simulate connection establishment
                time.sleep(random.uniform(0.05, 0.15))

                # Simulate data transfer
                time.sleep(random.uniform(0.1, 2.0))

                return f"Response from {url}"

            # Test concurrent sync requests via asyncio.to_thread
            urls = [f"https://ws.audioscrobbler.com/2.0/?track={i}" for i in range(5)]

            start_time = time.time()
            tasks = [
                asyncio.create_task(asyncio.to_thread(sync_http_request, url))
                for url in urls
            ]
            results = await asyncio.gather(*tasks)
            total_time = time.time() - start_time

            return {
                "results": results,
                "total_time": total_time,
                "num_requests": len(urls),
            }

        simulation_result = await simulate_pylast_pattern()

        print("\n🌐 HTTP Session Simulation:")
        print(f"   Requests: {simulation_result['num_requests']}")
        print(f"   Total time: {simulation_result['total_time']:.3f}s")
        print(
            f"   Avg per request: {simulation_result['total_time'] / simulation_result['num_requests']:.3f}s"
        )

        # Should complete multiple requests concurrently
        assert simulation_result["total_time"] < 5.0, "Simulated requests too slow"
        assert len(simulation_result["results"]) == 5, "Not all requests completed"

    @pytest.mark.asyncio
    async def test_thread_pool_exhaustion_detection(self):
        """Test if thread pool exhaustion could cause slowdowns."""

        def cpu_bound_task(task_id: int):
            """CPU-bound task that might exhaust thread pool."""
            start = time.time()
            # Simulate CPU work
            count = 0
            while time.time() - start < 0.1:  # 100ms of CPU work
                count += 1
            return {"task_id": task_id, "count": count}

        def io_bound_task(task_id: int):
            """IO-bound task (network simulation)."""
            time.sleep(0.5)  # 500ms network delay
            return {"task_id": task_id, "type": "io"}

        # Create many mixed tasks
        cpu_tasks = [
            asyncio.create_task(asyncio.to_thread(cpu_bound_task, i)) for i in range(10)
        ]
        io_tasks = [
            asyncio.create_task(asyncio.to_thread(io_bound_task, i))
            for i in range(10, 20)
        ]

        all_tasks = cpu_tasks + io_tasks

        start_time = time.time()
        results = await asyncio.gather(*all_tasks)
        total_time = time.time() - start_time

        cpu_results = results[:10]
        io_results = results[10:]

        print("\n🧵 Thread Pool Analysis:")
        print(f"   Total tasks: {len(all_tasks)}")
        print(f"   Total time: {total_time:.3f}s")
        print(f"   CPU tasks: {len(cpu_results)}")
        print(f"   IO tasks: {len(io_results)}")

        # Should complete within reasonable time
        # If thread pool is exhausted, this would take much longer
        expected_time = 0.5 + 0.2  # IO time + some CPU time
        assert total_time < expected_time * 2, (
            f"Possible thread pool exhaustion: {total_time:.3f}s"
        )

    @pytest.mark.asyncio
    async def test_lastfm_client_with_timeout_wrapper(self):
        """Test LastFM client with timeout wrapper implementation."""

        # Create a client with timeout wrapper
        with patch("src.config.settings") as mock_settings:
            mock_settings.credentials.lastfm_key = "test_key"
            mock_settings.credentials.lastfm_secret.get_secret_value.return_value = (
                "test_secret"
            )
            mock_settings.credentials.lastfm_username = "test_user"
            mock_settings.credentials.lastfm_password.get_secret_value.return_value = (
                "test_pass"
            )
            mock_settings.api.lastfm_rate_limit = 10.0

            client = LastFMAPIClient()

            # Mock pylast.LastFMNetwork to simulate slow response
            def slow_get_track(*args, **kwargs):
                time.sleep(2.0)  # 2 second delay
                mock_track = MagicMock()
                mock_track.get_title.return_value = "Slow Track"
                return mock_track

            with patch("pylast.LastFMNetwork") as mock_network_class:
                slow_mock = MagicMock()
                slow_mock.get_track.side_effect = slow_get_track
                mock_network_class.return_value = slow_mock

                # Test with timeout wrapper
                start_time = time.time()

                try:
                    # This should timeout
                    result = await asyncio.wait_for(
                        client.get_track("Artist", "Track"),
                        timeout=1.0,  # 1 second timeout
                    )
                    pytest.fail("Should have timed out")

                except TimeoutError:
                    timeout_duration = time.time() - start_time
                    print(f"✓ Client call timed out after {timeout_duration:.3f}s")
                    assert 0.9 < timeout_duration < 1.2, "Timeout not working correctly"

            # Test successful fast call
            def fast_get_track(*args, **kwargs):
                time.sleep(0.1)  # 100ms delay
                mock_track = MagicMock()
                mock_track.get_title.return_value = "Fast Track"
                return mock_track

            with patch("pylast.LastFMNetwork") as mock_fast_network_class:
                fast_mock = MagicMock()
                fast_mock.get_track.side_effect = fast_get_track
                mock_fast_network_class.return_value = fast_mock

                start_time = time.time()
                result = await asyncio.wait_for(
                    client.get_track("Artist", "Track"), timeout=2.0
                )
                fast_duration = time.time() - start_time

                print(f"✓ Fast call completed in {fast_duration:.3f}s")
                assert result is not None
                assert 0.08 < fast_duration < 0.3, "Fast call timing unexpected"
