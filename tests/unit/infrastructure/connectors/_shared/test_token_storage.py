"""``TokenStorageGrantProvider`` — scopes out, secrets never — and the
required-access-token loader connectors without a mint-on-demand credential
share (Apple Music's MUT, Discogs's personal access token)."""

import pytest

from src.infrastructure.connectors._shared.token_storage import (
    StoredToken,
    TokenStorageGrantProvider,
    load_required_access_token,
)


class StubTokenStorage:
    """Hand-written stub so the real ``TokenStorage`` shape is exercised."""

    def __init__(self, token: StoredToken | None) -> None:
        self._token = token
        self.calls: list[tuple[str, str]] = []

    async def load_token(self, service: str, user_id: str) -> StoredToken | None:
        self.calls.append((service, user_id))
        return self._token

    async def save_token(
        self, service: str, user_id: str, token_data: StoredToken
    ) -> None:
        raise AssertionError("grant provider must never write tokens")

    async def delete_token(self, service: str, user_id: str) -> None:
        raise AssertionError("grant provider must never delete tokens")


class TestTokenStorageGrantProvider:
    """Reads the scope field off a stored token and nothing else."""

    async def test_token_storage_grant_provider_reads_scope_off_stored_token(
        self,
    ) -> None:
        storage = StubTokenStorage(
            StoredToken(
                access_token="secret-never-returned",
                scope="user-read-recently-played user-library-read",
            )
        )
        provider = TokenStorageGrantProvider(storage)

        scopes = await provider.granted_scopes("spotify", "user-1")

        assert scopes == frozenset({"user-read-recently-played", "user-library-read"})
        assert storage.calls == [("spotify", "user-1")]

    async def test_no_stored_token_grants_nothing(self) -> None:
        """A missing grant permits exactly what a revoked one does: nothing."""
        provider = TokenStorageGrantProvider(StubTokenStorage(None))

        assert await provider.granted_scopes("spotify", "user-1") == frozenset()

    async def test_token_without_scope_key_grants_nothing(self) -> None:
        """Last.fm session keys (and pre-scope-tracking tokens) carry no scope."""
        provider = TokenStorageGrantProvider(
            StubTokenStorage(StoredToken(session_key="abc"))
        )

        assert await provider.granted_scopes("lastfm", "user-1") == frozenset()


class _AuthNeededError(Exception):
    """Stand-in for a connector's auth-required exception."""


class TestLoadRequiredAccessToken:
    """Load-or-raise for credentials that cannot be minted on demand."""

    async def test_returns_the_stored_access_token(self) -> None:
        storage = StubTokenStorage(StoredToken(access_token="the-token"))

        token = await load_required_access_token(
            storage, "apple_music", "user-1", missing_error=_AuthNeededError
        )

        assert token == "the-token"
        assert storage.calls == [("apple_music", "user-1")]

    async def test_no_stored_token_raises_the_missing_error(self) -> None:
        storage = StubTokenStorage(None)

        with pytest.raises(_AuthNeededError):
            _ = await load_required_access_token(
                storage, "discogs", "user-1", missing_error=_AuthNeededError
            )

    async def test_row_without_access_token_raises(self) -> None:
        """A row holding only e.g. a session key is not a usable access token."""
        storage = StubTokenStorage(StoredToken(session_key="abc"))

        with pytest.raises(_AuthNeededError):
            _ = await load_required_access_token(
                storage, "lastfm", "user-1", missing_error=_AuthNeededError
            )

    async def test_exception_instance_is_raised_as_given(self) -> None:
        error = _AuthNeededError("reconnect from the Integrations page")
        storage = StubTokenStorage(None)

        with pytest.raises(_AuthNeededError, match="Integrations page"):
            _ = await load_required_access_token(
                storage, "discogs", "user-1", missing_error=error
            )
