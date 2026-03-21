"""Tests for OAuth auth route helper functions (CSRF state, PKCE)."""

import time

from src.interface.api.routes.auth import (
    _create_pkce_challenge,
    _create_state,
    _csrf_states,
    _validate_state,
)


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
        valid, _ = _validate_state(state)
        assert valid is True
        # Second call should fail — state consumed
        valid, _ = _validate_state(state)
        assert valid is False

    def test_validate_rejects_unknown_state(self):
        valid, verifier = _validate_state("nonexistent-state")
        assert valid is False
        assert verifier is None

    def test_validate_rejects_expired_state(self):
        state = _create_state()
        # Manually expire the state
        _csrf_states[state] = (time.time() - 1, None)
        valid, _ = _validate_state(state)
        assert valid is False

    def test_create_prunes_expired_states(self):
        # Create an expired state
        _csrf_states["old-state"] = (time.time() - 1, None)
        assert len(_csrf_states) == 1

        # Creating a new state should prune the expired one
        _create_state()
        assert "old-state" not in _csrf_states


class TestPKCE:
    """Tests for PKCE (RFC 7636) support in Spotify web OAuth."""

    def setup_method(self):
        _csrf_states.clear()

    def teardown_method(self):
        _csrf_states.clear()

    def test_state_stores_and_returns_code_verifier(self):
        state = _create_state(code_verifier="test_verifier_abc")
        valid, verifier = _validate_state(state)
        assert valid is True
        assert verifier == "test_verifier_abc"

    def test_state_without_verifier_returns_none(self):
        state = _create_state()
        valid, verifier = _validate_state(state)
        assert valid is True
        assert verifier is None

    def test_replay_returns_invalid_and_no_verifier(self):
        state = _create_state(code_verifier="v")
        _validate_state(state)  # consume
        valid, verifier = _validate_state(state)  # replay attempt
        assert valid is False
        assert verifier is None

    def test_pkce_challenge_is_s256(self):
        """S256 challenge is 43 base64url chars (no padding) for any verifier."""
        import secrets

        verifier = secrets.token_urlsafe(64)
        challenge = _create_pkce_challenge(verifier)
        # SHA-256 → 32 bytes → 43 base64url chars without padding
        assert len(challenge) == 43
        assert "=" not in challenge
        assert "+" not in challenge
        assert "/" not in challenge

    def test_pkce_challenge_is_deterministic(self):
        challenge1 = _create_pkce_challenge("fixed_verifier")
        challenge2 = _create_pkce_challenge("fixed_verifier")
        assert challenge1 == challenge2

    def test_different_verifiers_produce_different_challenges(self):
        c1 = _create_pkce_challenge("verifier_a")
        c2 = _create_pkce_challenge("verifier_b")
        assert c1 != c2
