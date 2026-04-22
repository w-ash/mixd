"""Tests for OAuth auth route helper functions (PKCE).

CSRF state tests moved to integration tests since state is now DB-backed (v0.6.3).
"""

import secrets

from src.infrastructure.connectors.spotify.auth import _compute_pkce_challenge


class TestPKCE:
    """Tests for PKCE (RFC 7636) support in Spotify web OAuth."""

    def test_pkce_challenge_is_s256(self):
        """S256 challenge is 43 base64url chars (no padding) for any verifier."""
        verifier = secrets.token_urlsafe(64)
        challenge = _compute_pkce_challenge(verifier)
        # SHA-256 → 32 bytes → 43 base64url chars without padding
        assert len(challenge) == 43
        assert "=" not in challenge
        assert "+" not in challenge
        assert "/" not in challenge

    def test_pkce_challenge_is_deterministic(self):
        challenge1 = _compute_pkce_challenge("fixed_verifier")
        challenge2 = _compute_pkce_challenge("fixed_verifier")
        assert challenge1 == challenge2

    def test_different_verifiers_produce_different_challenges(self):
        c1 = _compute_pkce_challenge("verifier_a")
        c2 = _compute_pkce_challenge("verifier_b")
        assert c1 != c2
