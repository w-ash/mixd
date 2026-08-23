"""Unit tests for connector status probes.

``get_spotify_status`` scope read-back (v0.10.1): a stored grant narrower
than ``SPOTIFY_SCOPES`` surfaces as ``auth_error="scope_missing"`` while the
connection stays usable (``connected=True``), and ``refresh_failed`` keeps
precedence.

``get_apple_music_status`` (v0.11.x P4): honest token-storage-only probe —
no network calls. An expired or reauth-marked Music User Token stays
``connected=True`` with ``auth_error="reauth_required"`` so the UI derives
``needs_reauth`` (one-click fix), never ``expired``.

``fetch_spotify_profile`` (v0.11.2 P S4): parses ``GET /me`` into
``(display_name, account_id)``. Development-mode payloads never carry
``email``/``country``/``product`` (removed 2026-05) — parsing must not
depend on them — and ``account_id`` (added 2026-05) may still be absent on
an older captured payload shape, in which case it resolves to ``None``
rather than raising.
"""

import time
from unittest.mock import AsyncMock, patch

import httpx2

from src.domain.entities.connector import derive_status_state
from src.infrastructure.connectors._shared.connector_status import (
    fetch_spotify_profile,
    get_apple_music_status,
    get_spotify_status,
)
from src.infrastructure.connectors._shared.token_storage import StoredToken
from src.infrastructure.connectors.spotify.auth import (
    SPOTIFY_SCOPES,
    SpotifyTokenManager,
)

FULL_SCOPE = " ".join(SPOTIFY_SCOPES)

_SVC = "src.infrastructure.connectors._shared.connector_status"

# Live-verified /me shape (2026-08-22 probe): email/country/product are
# absent in development mode; account_id is Spotify's designated key for
# external linkage (added 2026-05).
_LIVE_ME_PAYLOAD: dict[str, object] = {
    "account_id": "31l6exampleacct",
    "display_name": "Test User",
    "id": "spotifyuserid123",
    "external_urls": {"spotify": "https://open.spotify.com/user/spotifyuserid123"},
    "followers": {"href": None, "total": 0},
    "href": "https://api.spotify.com/v1/me",
    "images": [],
    "type": "user",
    "uri": "spotify:user:spotifyuserid123",
}


def _mock_me_client(payload: dict[str, object]) -> httpx2.AsyncClient:
    transport = httpx2.MockTransport(
        lambda _request: httpx2.Response(200, json=payload)
    )
    return httpx2.AsyncClient(transport=transport)


class TestFetchSpotifyProfile:
    async def test_parses_live_shaped_payload_without_email_country_product(
        self,
    ) -> None:
        with patch(
            f"{_SVC}.httpx2.AsyncClient",
            return_value=_mock_me_client(_LIVE_ME_PAYLOAD),
        ):
            display_name, account_id = await fetch_spotify_profile("token")

        assert display_name == "Test User"
        assert account_id == "31l6exampleacct"

    async def test_missing_account_id_resolves_to_none(self) -> None:
        payload = {k: v for k, v in _LIVE_ME_PAYLOAD.items() if k != "account_id"}
        with patch(f"{_SVC}.httpx2.AsyncClient", return_value=_mock_me_client(payload)):
            display_name, account_id = await fetch_spotify_profile("token")

        assert display_name == "Test User"
        assert account_id is None

    async def test_http_failure_returns_both_none(self) -> None:
        transport = httpx2.MockTransport(
            lambda _request: httpx2.Response(500, json={"error": "boom"})
        )
        with patch(
            f"{_SVC}.httpx2.AsyncClient",
            return_value=httpx2.AsyncClient(transport=transport),
        ):
            display_name, account_id = await fetch_spotify_profile("token")

        assert display_name is None
        assert account_id is None


def make_storage(token: StoredToken | None) -> AsyncMock:
    storage = AsyncMock()
    storage.load_token = AsyncMock(return_value=token)
    storage.save_token = AsyncMock()
    return storage


def make_token(
    *, expires_at: int | None = None, scope: str | None = None
) -> StoredToken:
    token = StoredToken(
        access_token="access",
        refresh_token="refresh",
        expires_at=expires_at if expires_at is not None else int(time.time()) + 3600,
        account_name="testuser",
    )
    if scope is not None:
        token["scope"] = scope
    return token


class TestScopeGapDetection:
    async def test_stale_scope_reports_scope_missing_but_stays_connected(self) -> None:
        token = make_token(scope="user-library-read playlist-read-private")
        status = await get_spotify_status("u1", storage=make_storage(token))

        assert status.auth_error == "scope_missing"
        assert status.connected is True
        assert status.account_name == "testuser"

    async def test_legacy_token_without_scope_key_reports_scope_missing(self) -> None:
        status = await get_spotify_status("u1", storage=make_storage(make_token()))

        assert status.auth_error == "scope_missing"
        assert status.connected is True

    async def test_full_scope_token_is_clean(self) -> None:
        token = make_token(scope=FULL_SCOPE)
        status = await get_spotify_status("u1", storage=make_storage(token))

        assert status.auth_error is None
        assert status.connected is True

    async def test_no_token_is_disconnected_without_error(self) -> None:
        status = await get_spotify_status("u1", storage=make_storage(None))

        assert status.connected is False
        assert status.auth_error is None


def make_apple_token(
    *,
    expires_at: int | None = None,
    extra_data: dict[str, object] | None = None,
) -> StoredToken:
    token = StoredToken(
        access_token="fake-music-user-token",
        token_type="music_user_token",
        expires_at=expires_at if expires_at is not None else int(time.time()) + 3600,
    )
    if extra_data is not None:
        token["extra_data"] = extra_data
    return token


class TestAppleMusicStatus:
    async def test_no_token_is_disconnected(self) -> None:
        status = await get_apple_music_status("u1", make_storage(None))

        assert status.name == "apple_music"
        assert status.auth_method == "browser_bridge"
        assert status.connected is False
        assert status.auth_error is None
        assert derive_status_state(status) == "disconnected"

    async def test_fresh_token_is_connected_with_expiry(self) -> None:
        expires = int(time.time()) + 3600
        token = make_apple_token(expires_at=expires)
        status = await get_apple_music_status("u1", make_storage(token))

        assert status.connected is True
        assert status.token_expires_at == expires
        assert status.auth_error is None
        # Apple has no profile endpoint — no account name to show.
        assert status.account_name is None
        assert derive_status_state(status) == "connected"

    async def test_expired_token_needs_reauth_not_expired(self) -> None:
        token = make_apple_token(expires_at=int(time.time()) - 60)
        status = await get_apple_music_status("u1", make_storage(token))

        # MUTs can't refresh — expiry is expected credential aging, fixed by
        # one click. It must derive to needs_reauth, never "expired".
        assert status.connected is True
        assert status.auth_error == "reauth_required"
        assert derive_status_state(status) == "needs_reauth"

    async def test_reauth_marker_needs_reauth(self) -> None:
        token = make_apple_token(
            expires_at=int(time.time()) + 3600,
            extra_data={"reauth_required": True},
        )
        status = await get_apple_music_status("u1", make_storage(token))

        assert status.connected is True
        assert status.auth_error == "reauth_required"
        assert derive_status_state(status) == "needs_reauth"

    async def test_probe_makes_no_network_calls(self) -> None:
        # The probe's only I/O is the storage read — one load_token call.
        storage = make_storage(make_apple_token())
        _ = await get_apple_music_status("u1", storage)

        storage.load_token.assert_awaited_once_with("apple_music", "u1")


class TestRefreshInteraction:
    async def test_expired_grant_surfaces_reauth_required(self) -> None:
        # Spotify's 6-month refresh grant aged out: the silent refresh raises
        # SpotifyReauthRequiredError (token already deleted at the detection
        # site). Mirrors Apple Music's convention for expected credential
        # aging — connected=True + reauth_required derives to needs_reauth
        # ("one click to fix"), never refresh_failed or "expired".
        from src.domain.exceptions import SpotifyReauthRequiredError

        token = make_token(expires_at=int(time.time()) - 3600, scope=FULL_SCOPE)
        with patch.object(
            SpotifyTokenManager,
            "try_silent_refresh",
            AsyncMock(side_effect=SpotifyReauthRequiredError()),
        ):
            status = await get_spotify_status("u1", storage=make_storage(token))

        assert status.auth_error == "reauth_required"
        assert status.connected is True
        assert derive_status_state(status) == "needs_reauth"

    async def test_refresh_failure_takes_precedence_over_scope_gap(self) -> None:
        token = make_token(expires_at=int(time.time()) - 3600, scope="old-scope")
        with patch.object(
            SpotifyTokenManager, "try_silent_refresh", AsyncMock(return_value=None)
        ):
            status = await get_spotify_status("u1", storage=make_storage(token))

        assert status.auth_error == "refresh_failed"
        assert status.connected is False

    async def test_refreshed_scope_is_authoritative_for_gap_check(self) -> None:
        # Stored token has a stale scope, but Spotify echoes the real grant
        # on refresh — the refreshed scope must win the comparison.
        token = make_token(expires_at=int(time.time()) - 3600, scope="old-scope")
        refreshed = {
            "access_token": "new-access",
            "refresh_token": "refresh",
            "expires_at": int(time.time()) + 3600,
            "scope": FULL_SCOPE,
        }
        with patch.object(
            SpotifyTokenManager, "try_silent_refresh", AsyncMock(return_value=refreshed)
        ):
            status = await get_spotify_status("u1", storage=make_storage(token))

        assert status.auth_error is None
        assert status.connected is True


class TestAccountIdBackfill:
    """The status probe writes ``extra_data.account_id`` back onto the stored
    token when it's missing — mirrors the Apple storefront best-effort
    write-back pattern. It only fetches when something is actually missing."""

    async def test_backfills_account_id_on_a_token_with_no_cached_name(self) -> None:
        token = StoredToken(
            access_token="access",
            refresh_token="refresh",
            expires_at=int(time.time()) + 3600,
        )
        storage = make_storage(token)
        with patch(
            f"{_SVC}.fetch_spotify_profile",
            AsyncMock(return_value=("Fresh Name", "acct-999")),
        ):
            status = await get_spotify_status("u1", storage=storage)

        assert status.account_name == "Fresh Name"
        storage.save_token.assert_awaited_once()
        _, _, saved = storage.save_token.await_args.args
        assert saved["extra_data"]["account_id"] == "acct-999"

    async def test_backfills_account_id_when_name_already_cached(self) -> None:
        # A pre-v0.11.2 token already has account_name cached — only
        # account_id is missing from extra_data.
        token = StoredToken(
            access_token="access",
            refresh_token="refresh",
            expires_at=int(time.time()) + 3600,
            account_name="testuser",
        )
        storage = make_storage(token)
        with patch(
            f"{_SVC}.fetch_spotify_profile",
            AsyncMock(return_value=("testuser", "acct-abc")),
        ) as mock_fetch:
            status = await get_spotify_status("u1", storage=storage)

        mock_fetch.assert_awaited_once_with("access")
        assert status.account_name == "testuser"
        storage.save_token.assert_awaited_once()
        _, _, saved = storage.save_token.await_args.args
        assert saved["extra_data"]["account_id"] == "acct-abc"
        assert saved["account_name"] == "testuser"

    async def test_no_fetch_when_name_and_account_id_already_cached(self) -> None:
        token = StoredToken(
            access_token="access",
            refresh_token="refresh",
            expires_at=int(time.time()) + 3600,
            account_name="testuser",
            extra_data={"account_id": "acct-already-there"},
        )
        storage = make_storage(token)
        with patch(f"{_SVC}.fetch_spotify_profile", AsyncMock()) as mock_fetch:
            status = await get_spotify_status("u1", storage=storage)

        mock_fetch.assert_not_awaited()
        storage.save_token.assert_not_awaited()
        assert status.account_name == "testuser"

    async def test_marker_short_circuits_probe_with_cached_name(self) -> None:
        # A prior probe already asked /me and got no account_id back — the
        # unavailable marker must stop every later probe from re-fetching
        # (and re-upserting) on a token that will never yield one.
        token = StoredToken(
            access_token="access",
            refresh_token="refresh",
            expires_at=int(time.time()) + 3600,
            account_name="testuser",
            extra_data={"account_id_unavailable": True},
        )
        storage = make_storage(token)
        with patch(f"{_SVC}.fetch_spotify_profile", AsyncMock()) as mock_fetch:
            status = await get_spotify_status("u1", storage=storage)

        mock_fetch.assert_not_awaited()
        storage.save_token.assert_not_awaited()
        assert status.account_name == "testuser"

    async def test_fetch_without_account_id_stamps_unavailable_marker(self) -> None:
        # /me answered but carried no account_id (older payload shape): stamp
        # the marker in the one save so the next probe short-circuits.
        token = StoredToken(
            access_token="access",
            refresh_token="refresh",
            expires_at=int(time.time()) + 3600,
            account_name="testuser",
        )
        storage = make_storage(token)
        with patch(
            f"{_SVC}.fetch_spotify_profile",
            AsyncMock(return_value=("testuser", None)),
        ):
            _ = await get_spotify_status("u1", storage=storage)

        storage.save_token.assert_awaited_once()
        _, _, saved = storage.save_token.await_args.args
        assert saved["extra_data"]["account_id_unavailable"] is True

    async def test_failed_fetch_saves_nothing(self) -> None:
        # A network failure teaches nothing — no marker, no upsert; the next
        # probe simply tries again.
        token = StoredToken(
            access_token="access",
            refresh_token="refresh",
            expires_at=int(time.time()) + 3600,
        )
        storage = make_storage(token)
        with patch(
            f"{_SVC}.fetch_spotify_profile",
            AsyncMock(return_value=(None, None)),
        ) as mock_fetch:
            status = await get_spotify_status("u1", storage=storage)

        mock_fetch.assert_awaited_once()
        storage.save_token.assert_not_awaited()
        assert status.account_name is None

    async def test_backfill_preserves_other_extra_data_keys(self) -> None:
        token = StoredToken(
            access_token="access",
            refresh_token="refresh",
            expires_at=int(time.time()) + 3600,
            account_name="testuser",
            extra_data={"authorized_at": 1_700_000_000},
        )
        storage = make_storage(token)
        with patch(
            f"{_SVC}.fetch_spotify_profile",
            AsyncMock(return_value=("testuser", "acct-xyz")),
        ):
            _ = await get_spotify_status("u1", storage=storage)

        _, _, saved = storage.save_token.await_args.args
        assert saved["extra_data"]["authorized_at"] == 1_700_000_000
        assert saved["extra_data"]["account_id"] == "acct-xyz"
