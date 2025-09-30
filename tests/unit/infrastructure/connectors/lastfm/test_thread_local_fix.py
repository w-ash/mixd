"""Test thread-local pylast client fix for HTTP serialization."""

import asyncio
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from src.infrastructure.connectors.lastfm.client import LastFMAPIClient


class TestThreadLocalFix:
    """Test the thread-local pylast client solution."""

    @pytest.fixture
    def mock_pylast_with_thread_tracking(self):
        """Mock pylast that tracks which instance/thread makes calls."""

        call_log = []

        def tracked_get_track(self, *args, **kwargs):
            """Track calls with instance ID and thread ID."""
            thread_id = threading.get_ident()
            instance_id = id(self)
            call_time = time.time()

            # Simulate HTTP delay
            time.sleep(0.5)

            call_log.append({
                "instance_id": instance_id,
                "thread_id": thread_id,
                "call_time": call_time,
                "args": args,
            })

            mock_track = MagicMock()
            mock_track.get_title.return_value = f"Track_{instance_id}_{thread_id}"
            return mock_track

        with patch("pylast.LastFMNetwork") as mock_network_class:
            # Each call to LastFMNetwork() creates a new mock instance
            def create_mock_instance(*args, **kwargs):
                mock_instance = MagicMock()
                mock_instance.get_track = lambda *a, **kw: tracked_get_track(
                    mock_instance, *a, **kw
                )
                return mock_instance

            mock_network_class.side_effect = create_mock_instance
            yield call_log

    @pytest.mark.asyncio
    async def test_thread_local_clients_created(self, mock_pylast_with_thread_tracking):
        """Test that different threads get different pylast instances."""

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
            mock_settings.api.lastfm_concurrency = 200
            mock_settings.api.lastfm_request_timeout = 10.0

            client = LastFMAPIClient()

        print("\n🧵 Testing thread-local pylast client creation")

        # Make 5 concurrent calls
        num_calls = 5
        overall_start = time.time()

        async def make_api_call(call_id: int):
            """Make API call and return thread info."""
            thread_id = threading.get_ident()
            result = await client.get_track(f"Artist_{call_id}", f"Track_{call_id}")
            return {"call_id": call_id, "thread_id": thread_id, "result": result}

        tasks = [asyncio.create_task(make_api_call(i)) for i in range(num_calls)]
        results = await asyncio.gather(*tasks)

        overall_duration = time.time() - overall_start
        call_log = mock_pylast_with_thread_tracking

        print("\n📊 Thread-Local Results:")
        print(f"   Total time: {overall_duration:.2f}s")
        print("   Expected concurrent: ~0.5s")
        print("   Expected serial: ~2.5s")

        # Analyze thread and instance usage
        unique_threads = len({r["thread_id"] for r in results})
        unique_instances = len({call["instance_id"] for call in call_log})

        print(f"   Unique threads used: {unique_threads}")
        print(f"   Unique pylast instances: {unique_instances}")

        # Check call timing overlaps
        if len(call_log) >= 2:
            start_times = [call["call_time"] for call in call_log]
            min_start = min(start_times)
            max_start = max(start_times)
            start_spread = max_start - min_start
            print(f"   Call start spread: {start_spread:.3f}s")

            if start_spread < 0.1:  # All started within 100ms
                print("   ✅ CONCURRENT STARTS: Calls started simultaneously")
            else:
                print("   ⚠️ STAGGERED STARTS: Some serialization still present")

        # Performance assessment
        if overall_duration < 1.0:
            print("   ✅ EXCELLENT: Thread-local fix enables concurrency!")
            concurrent = True
        elif overall_duration < 1.5:
            print("   ⚡ GOOD: Significant improvement with thread-local clients")
            concurrent = True
        else:
            print("   ❌ STILL SERIALIZED: Thread-local fix didn't help")
            concurrent = False

        # Verification assertions
        assert unique_instances > 1, (
            f"Should create multiple pylast instances: {unique_instances}"
        )
        assert concurrent, f"Should achieve concurrency: {overall_duration:.2f}s"

        return {
            "overall_duration": overall_duration,
            "unique_threads": unique_threads,
            "unique_instances": unique_instances,
            "concurrent": concurrent,
        }

    @pytest.mark.asyncio
    async def test_thread_local_performance_improvement(
        self, mock_pylast_with_thread_tracking
    ):
        """Test performance improvement vs single instance."""

        print("\n⚡ Performance Comparison: Thread-Local vs Single Instance")

        # This will use thread-local instances (our new implementation)
        thread_local_result = await self.test_thread_local_clients_created(
            mock_pylast_with_thread_tracking
        )

        print("\n🏁 Performance Summary:")
        print(
            f"   Thread-local duration: {thread_local_result['overall_duration']:.2f}s"
        )
        print(f"   Unique instances: {thread_local_result['unique_instances']}")
        print(f"   Concurrent execution: {thread_local_result['concurrent']}")

        # Should achieve significant speedup vs theoretical single instance
        theoretical_single_instance_time = 5 * 0.5  # 5 calls × 0.5s each
        speedup = (
            theoretical_single_instance_time / thread_local_result["overall_duration"]
        )

        print(f"   Theoretical speedup: {speedup:.1f}x vs sequential")

        if speedup > 3.0:
            print("   🎯 SUCCESS: Thread-local instances solve the bottleneck!")
        else:
            print("   ⚠️ PARTIAL: Some improvement but not optimal")

        return speedup
