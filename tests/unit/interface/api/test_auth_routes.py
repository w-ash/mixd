"""Tests for OAuth auth route helper functions (CSRF state management)."""

import time

from src.interface.api.routes.auth import _create_state, _csrf_states, _validate_state


class TestCsrfStateManagement:
    """Tests for CSRF state creation and validation."""

    def setup_method(self):
        _csrf_states.clear()

    def teardown_method(self):
        _csrf_states.clear()

    def test_create_state_returns_unique_tokens(self):
        state1 = _create_state()
        state2 = _create_state()
        assert state1 != state2
        assert len(_csrf_states) == 2

    def test_validate_consumes_state(self):
        state = _create_state()
        assert _validate_state(state) is True
        # Second call should fail — state consumed
        assert _validate_state(state) is False

    def test_validate_rejects_unknown_state(self):
        assert _validate_state("nonexistent-state") is False

    def test_validate_rejects_expired_state(self):
        state = _create_state()
        # Manually expire the state
        _csrf_states[state] = time.time() - 1
        assert _validate_state(state) is False

    def test_create_prunes_expired_states(self):
        # Create an expired state
        _csrf_states["old-state"] = time.time() - 1
        assert len(_csrf_states) == 1

        # Creating a new state should prune the expired one
        _create_state()
        assert "old-state" not in _csrf_states
