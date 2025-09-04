"""Unit tests for LastFM client performance patterns.

Tests asyncio.to_thread behavior and pylast integration patterns
following pytest best practices from DEVELOPMENT.md.
"""

import asyncio
import time
from unittest.mock import MagicMock, patch

import pylast
import pytest

from src.infrastructure.connectors._shared.request_start_gate import RequestStartGate
from src.infrastructure.connectors.lastfm.client import LastFMAPIClient


class TestLastFMClientPerformance:
    """Fast unit tests (<100ms) for LastFM client performance patterns."""

    @pytest.fixture
    def mock_pylast_network(self):
        """Mock pylast network with realistic timing simulation."""
        mock_network = MagicMock(spec=pylast.LastFMNetwork)
        
        def simulate_api_call(*args, **kwargs):
            """Simulate API call with variable timing."""
            # Simulate network latency
            time.sleep(0.05)  # 50ms baseline
            
            # Create mock track
            mock_track = MagicMock(spec=pylast.Track)
            mock_track.get_title.return_value = "Test Track"
            mock_track.get_artist.return_value.get_name.return_value = "Test Artist"
            return mock_track
        
        mock_network.get_track.side_effect = simulate_api_call
        mock_network.get_track_by_mbid.side_effect = simulate_api_call
        return mock_network

    @pytest.fixture
    def test_gate(self):
        """Fast gate for testing."""
        return RequestStartGate(delay=0.05)  # 50ms for fast tests

    @pytest.fixture
    def client_with_mocked_network(self, mock_pylast_network, test_gate):
        """LastFM client with mocked network for performance testing."""
        with patch('src.config.settings') as mock_settings:
            mock_settings.credentials.lastfm_key = "test_key"
            mock_settings.credentials.lastfm_secret.get_secret_value.return_value = "test_secret"
            mock_settings.credentials.lastfm_username = "test_user"
            mock_settings.credentials.lastfm_password.get_secret_value.return_value = "test_pass"
            mock_settings.api.lastfm_rate_limit = 20.0  # Fast rate for testing
            
            client = LastFMAPIClient(request_gate=test_gate)
            client.client = mock_pylast_network
            return client

    @pytest.mark.asyncio
    async def test_asyncio_to_thread_concurrent_pylast_calls(self, client_with_mocked_network, mock_pylast_network):
        """Test that multiple pylast calls via asyncio.to_thread execute concurrently."""
        call_start_times = []
        call_end_times = []
        
        # Track timing of actual pylast calls
        original_get_track = mock_pylast_network.get_track

        def timed_get_track(*args, **kwargs):
            call_start_times.append(time.time())
            result = original_get_track(*args, **kwargs)
            call_end_times.append(time.time())
            return result
        
        mock_pylast_network.get_track.side_effect = timed_get_track
        
        # Make multiple concurrent API calls
        start_time = time.time()
        tasks = [
            asyncio.create_task(client_with_mocked_network.get_track(f"Artist{i}", f"Track{i}"))
            for i in range(5)
        ]
        
        results = await asyncio.gather(*tasks)
        total_duration = time.time() - start_time
        
        # Verify all calls completed
        assert len(results) == 5
        assert all(r is not None for r in results)
        assert len(call_start_times) == 5
        assert len(call_end_times) == 5
        
        # Check that calls started with proper gate timing (50ms intervals)
        call_gaps = [call_start_times[i] - call_start_times[i - 1] for i in range(1, len(call_start_times))]
        avg_gap = sum(call_gaps) / len(call_gaps)
        assert 0.04 < avg_gap < 0.06, f"Gate timing off: {avg_gap:.3f}s average gap"
        
        # But calls should overlap (concurrency)
        # Total time should be much less than 5 * (gate_delay + api_time)
        sequential_time = 5 * (0.05 + 0.05)  # 5 * (gate + api) = 500ms
        assert total_duration < sequential_time * 0.6, f"Not concurrent: {total_duration:.3f}s"

    @pytest.mark.asyncio  
    async def test_individual_call_performance_breakdown(self, client_with_mocked_network):
        """Test timing breakdown of individual API call components."""
        timing_breakdown = {}
        
        # Patch gate to measure wait time
        original_gate_wait = client_with_mocked_network._request_gate.wait

        async def timed_gate_wait():
            gate_start = time.time()
            await original_gate_wait()
            timing_breakdown['gate_wait'] = time.time() - gate_start
        
        client_with_mocked_network._request_gate.wait = timed_gate_wait
        
        # Measure total call time
        call_start = time.time()
        result = await client_with_mocked_network.get_track("Test Artist", "Test Track")
        timing_breakdown['total_call'] = time.time() - call_start
        
        # Verify result
        assert result is not None
        
        # Analyze timing
        assert 'gate_wait' in timing_breakdown
        assert 'total_call' in timing_breakdown
        
        gate_time = timing_breakdown['gate_wait']
        total_time = timing_breakdown['total_call']
        api_time = total_time - gate_time
        
        # Gate wait should be minimal for first call
        assert gate_time < 0.01, f"First call gate wait too long: {gate_time:.3f}s"
        
        # API time should be reasonable (our mock simulates 50ms)
        assert 0.04 < api_time < 0.07, f"API call time unexpected: {api_time:.3f}s"
        
        # Total time should be gate + API + small overhead
        assert total_time < 0.1, f"Total call time too long: {total_time:.3f}s"

    @pytest.mark.asyncio
    async def test_backoff_retry_performance_impact(self, client_with_mocked_network, mock_pylast_network):
        """Test performance impact of backoff retries."""
        retry_attempts = []
        
        # Mock API to fail first call, succeed on retry
        call_count = 0

        def failing_then_success(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            retry_attempts.append(time.time())
            
            if call_count == 1:
                # First call fails
                raise pylast.WSError("network", "Temporary failure")
            
            # Second call succeeds
            time.sleep(0.05)  # Simulate API time
            mock_track = MagicMock(spec=pylast.Track)
            return mock_track
        
        mock_pylast_network.get_track.side_effect = failing_then_success
        
        # Measure retry performance
        start_time = time.time()
        result = await client_with_mocked_network.get_track("Artist", "Track")
        total_time = time.time() - start_time
        
        # Verify retry occurred
        assert result is not None
        assert call_count == 2
        assert len(retry_attempts) == 2
        
        # Check retry timing
        retry_gap = retry_attempts[1] - retry_attempts[0]
        
        # Should include gate wait for second call + backoff delay
        # Backoff starts at 1.0s, but both calls go through gate
        assert 0.05 < retry_gap < 2.0, f"Retry timing unexpected: {retry_gap:.3f}s"
        
        # Total time should include both attempts
        assert total_time < 3.0, f"Total retry time too long: {total_time:.3f}s"

    @pytest.mark.asyncio
    async def test_high_concurrency_stress_pattern(self, client_with_mocked_network):
        """Test behavior under high concurrency load."""
        completion_times = []
        
        async def tracked_api_call(call_id: int):
            start = time.time()
            result = await client_with_mocked_network.get_track(f"Artist{call_id}", f"Track{call_id}")
            completion_times.append(time.time() - start)
            return result
        
        # Create many concurrent calls
        num_calls = 20
        tasks = [asyncio.create_task(tracked_api_call(i)) for i in range(num_calls)]
        
        start_time = time.time()
        results = await asyncio.gather(*tasks)
        time.time() - start_time
        
        # Verify all calls completed
        assert len(results) == num_calls
        assert all(r is not None for r in results)
        assert len(completion_times) == num_calls
        
        # Analyze performance distribution
        min_time = min(completion_times)
        max_time = max(completion_times) 
        avg_time = sum(completion_times) / len(completion_times)
        
        # First call should be fastest (no gate wait)
        assert min_time < 0.07, f"Fastest call too slow: {min_time:.3f}s"
        
        # Last call should include full gate wait
        expected_max = (num_calls - 1) * 0.05 + 0.05  # Gate waits + API time
        assert max_time < expected_max + 0.1, f"Slowest call too slow: {max_time:.3f}s"
        
        # Average should be reasonable
        expected_avg = (num_calls - 1) * 0.05 / 2 + 0.05  # Average gate wait + API
        assert avg_time < expected_avg + 0.05, f"Average time too high: {avg_time:.3f}s"

    def test_mock_pylast_network_realistic_behavior(self, mock_pylast_network):
        """Test that our mock pylast network behaves realistically."""
        # Test synchronous calls (what pylast actually does)
        start_time = time.time()
        result = mock_pylast_network.get_track("Artist", "Track")
        duration = time.time() - start_time
        
        # Should take ~50ms as configured
        assert 0.04 < duration < 0.07, f"Mock timing unrealistic: {duration:.3f}s"
        assert result is not None
        
        # Test that multiple calls are sequential when called synchronously
        start_time = time.time()
        results = [mock_pylast_network.get_track(f"Artist{i}", f"Track{i}") for i in range(3)]
        duration = time.time() - start_time
        
        # Should be roughly 3 * 50ms = 150ms
        assert 0.12 < duration < 0.18, f"Sequential timing wrong: {duration:.3f}s"
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_gate_disabled_performance_comparison(self, mock_pylast_network):
        """Compare performance with and without RequestStartGate."""
        # Test without gate
        with patch('src.config.settings') as mock_settings:
            mock_settings.credentials.lastfm_key = "test_key"
            mock_settings.credentials.lastfm_secret.get_secret_value.return_value = "test_secret"
            mock_settings.credentials.lastfm_username = "test_user" 
            mock_settings.credentials.lastfm_password.get_secret_value.return_value = "test_pass"
            mock_settings.api.lastfm_rate_limit = 20.0
            
            # Client without gate
            no_gate_client = LastFMAPIClient()
            no_gate_client.client = mock_pylast_network
            no_gate_client._request_gate = None
            
            # Test concurrent calls without gate
            start_time = time.time()
            tasks = [
                asyncio.create_task(no_gate_client.get_track(f"Artist{i}", f"Track{i}"))
                for i in range(5)
            ]
            
            results = await asyncio.gather(*tasks)
            no_gate_duration = time.time() - start_time
            
            assert len(results) == 5
            # Without gate, all calls should start immediately and run concurrently
            # Total time should be ~50ms (one API call duration)
            assert no_gate_duration < 0.08, f"No-gate calls too slow: {no_gate_duration:.3f}s"
        
        # This test shows the gate adds controlled delays but enables proper rate limiting