"""``TokenStorageGrantProvider`` — scopes out, secrets never."""

from src.infrastructure.connectors._shared.token_storage import (
    StoredToken,
    TokenStorageGrantProvider,
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
