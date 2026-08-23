"""Tests for OAuth auth route helper functions (PKCE).

CSRF state tests moved to integration tests since state is now DB-backed (v0.6.3).
"""

import secrets
from unittest.mock import AsyncMock, patch

from src.infrastructure.connectors._shared.oauth import compute_pkce_challenge
from src.infrastructure.connectors.spotify.auth import SpotifyTokenManager


class TestPKCE:
    """Tests for PKCE (RFC 7636) support in Spotify web OAuth."""

    def test_pkce_challenge_is_s256(self):
        """S256 challenge is 43 base64url chars (no padding) for any verifier."""
        verifier = secrets.token_urlsafe(64)
        challenge = compute_pkce_challenge(verifier)
        # SHA-256 → 32 bytes → 43 base64url chars without padding
        assert len(challenge) == 43
        assert "=" not in challenge
        assert "+" not in challenge
        assert "/" not in challenge

    def test_pkce_challenge_is_deterministic(self):
        challenge1 = compute_pkce_challenge("fixed_verifier")
        challenge2 = compute_pkce_challenge("fixed_verifier")
        assert challenge1 == challenge2

    def test_different_verifiers_produce_different_challenges(self):
        c1 = compute_pkce_challenge("verifier_a")
        c2 = compute_pkce_challenge("verifier_b")
        assert c1 != c2


class TestCompleteSpotifyAuthAccountId:
    """Grant-time account_id stamping (v0.11.2 P S4)."""

    async def test_stamps_account_id_into_extra_data_at_grant_time(self) -> None:
        from src.interface.api.routes.auth import _complete_spotify_auth

        token_info = {
            "access_token": "access-tok",
            "refresh_token": "refresh-tok",
            "expires_at": 1_700_003_600,
            "scope": "user-library-read",
            "extra_data": {"authorized_at": 1_700_000_000},
        }
        storage = AsyncMock()

        with (
            patch.object(
                SpotifyTokenManager,
                "exchange_code",
                AsyncMock(return_value=token_info),
            ),
            patch(
                "src.interface.api.routes.auth.get_token_storage",
                return_value=storage,
            ),
            patch(
                "src.interface.api.routes.auth.fetch_spotify_profile",
                AsyncMock(return_value=("Real Name", "acct-web-123")),
            ),
            patch(
                "src.interface.api.routes.auth.sync_play_polling_after_auth",
                AsyncMock(),
            ),
        ):
            response = await _complete_spotify_auth("code", None, "user-1")

        assert "status=success" in response.headers["location"]
        storage.save_token.assert_awaited_once()
        service, user_id, saved = storage.save_token.await_args.args
        assert service == "spotify"
        assert user_id == "user-1"
        assert saved["account_name"] == "Real Name"
        assert saved["extra_data"]["account_id"] == "acct-web-123"
        # authorized_at (stamped by exchange_code) survives the account_id merge.
        assert saved["extra_data"]["authorized_at"] == 1_700_000_000
