"""Tests for RequestStartGate - rate limiting request starts without limiting concurrency."""

import asyncio
import time

import pytest

from src.infrastructure.connectors._shared.request_start_gate import RequestStartGate


class TestRequestStartGate:
    """Test the RequestStartGate rate limiting implementation."""

    @pytest.fixture
    def gate(self):
        """Create a gate with 200ms delay (5 requests/second)."""
        return RequestStartGate(delay=0.2)

    @pytest.mark.asyncio
    async def test_first_request_passes_immediately(self, gate):
        """First request should pass through without delay."""
        start_time = time.time()
        await gate.wait()
        elapsed = time.time() - start_time
        
        # Should be nearly instantaneous (< 10ms)
        assert elapsed < 0.01

    @pytest.mark.asyncio
    async def test_second_request_waits_for_delay(self, gate):
        """Second request should wait for the configured delay."""
        # First request
        await gate.wait()
        
        # Second request should wait ~200ms
        start_time = time.time()
        await gate.wait()
        elapsed = time.time() - start_time
        
        # Should be close to 200ms (allow 50ms tolerance for test timing)
        assert 0.15 < elapsed < 0.25

    @pytest.mark.asyncio
    async def test_concurrent_requests_each_wait_appropriate_time(self, gate):
        """Multiple concurrent requests should each wait their turn."""
        start_time = time.time()
        
        # Create 3 concurrent requests
        tasks = [
            asyncio.create_task(gate.wait()),
            asyncio.create_task(gate.wait()),
            asyncio.create_task(gate.wait()),
        ]
        
        # Wait for all to complete
        await asyncio.gather(*tasks)
        total_elapsed = time.time() - start_time
        
        # Should take ~400ms total (0ms + 200ms + 200ms)
        # Allow tolerance for test timing variability
        assert 0.35 < total_elapsed < 0.45

    @pytest.mark.asyncio
    async def test_requests_can_run_concurrently_after_start_approval(self, gate):
        """After gate approval, requests should run concurrently."""
        results = []
        
        async def mock_api_call(request_id: int):
            """Mock API call that takes 100ms."""
            await gate.wait()  # Wait for start approval
            await asyncio.sleep(0.1)  # Simulate API call duration
            results.append(request_id)
            return f"result_{request_id}"
        
        start_time = time.time()
        
        # Start 3 concurrent "API calls"
        tasks = [
            asyncio.create_task(mock_api_call(1)),
            asyncio.create_task(mock_api_call(2)),
            asyncio.create_task(mock_api_call(3)),
        ]
        
        await asyncio.gather(*tasks)
        total_elapsed = time.time() - start_time
        
        # Should take ~500ms: 0ms + 200ms + 200ms start delays + 100ms max concurrent execution
        # Not 700ms (sequential execution would be 200ms + 200ms + 300ms)
        assert 0.45 < total_elapsed < 0.55
        
        # All requests should complete
        assert len(results) == 3
        assert set(results) == {1, 2, 3}

    @pytest.mark.asyncio
    async def test_different_delay_intervals(self):
        """Test gate with different delay intervals."""
        fast_gate = RequestStartGate(delay=0.1)  # 10 requests/second
        
        start_time = time.time()
        await fast_gate.wait()  # First passes immediately
        await fast_gate.wait()  # Second waits 100ms
        elapsed = time.time() - start_time
        
        # Should be close to 100ms
        assert 0.08 < elapsed < 0.12

    @pytest.mark.asyncio
    async def test_gate_tracks_next_request_time_accurately(self, gate):
        """Gate should accurately track when next request can start."""
        # First request
        first_start = time.time()
        await gate.wait()
        time.time()
        
        # Wait 100ms (less than delay)
        await asyncio.sleep(0.1)
        
        # Second request should still wait remaining ~100ms
        time.time()
        await gate.wait()
        second_end = time.time()
        
        # Total time from first request start to second request end should be ~200ms
        total_elapsed = second_end - first_start
        
        # Should be close to 200ms total (allow some timing tolerance)
        assert 0.18 < total_elapsed < 0.22

    @pytest.mark.asyncio
    async def test_gate_allows_requests_after_delay_expires(self, gate):
        """If enough time passes, next request should not wait."""
        # First request
        await gate.wait()
        
        # Wait longer than delay
        await asyncio.sleep(0.3)
        
        # Second request should pass immediately
        start_time = time.time()
        await gate.wait()
        elapsed = time.time() - start_time
        
        # Should be nearly instantaneous
        assert elapsed < 0.01

    @pytest.mark.asyncio
    async def test_gate_is_thread_safe_with_lock(self, gate):
        """Gate should handle concurrent access safely."""
        # This tests the internal lock mechanism
        async def rapid_requests():
            await gate.wait()
            return time.time()
        
        # Create many concurrent requests
        tasks = [asyncio.create_task(rapid_requests()) for _ in range(5)]
        timestamps = await asyncio.gather(*tasks)
        
        # Timestamps should be in ascending order (each waits its turn)
        sorted_timestamps = sorted(timestamps)
        assert timestamps == sorted_timestamps
        
        # Each request should be ~200ms apart (with some tolerance)
        for i in range(1, len(timestamps)):
            gap = timestamps[i] - timestamps[i - 1]
            assert 0.15 < gap < 0.25