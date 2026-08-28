"""Unit tests for the Discogs connector status probe.

``get_discogs_status`` (v0.11.1): storage-only probe — never a network
call. The ``detail`` suffix renders the cached collection count ("1,204
releases"); a count of 0 still renders ("0 releases" — the zero-state
hook, not an error) and a missing count yields ``detail=None`` (format
cases covered by the ``stored_token_status`` primitive tests).
"""

from unittest.mock import AsyncMock, patch

from src.domain.entities.connector import derive_status_state
from src.infrastructure.connectors._shared.token_storage import StoredToken
from src.infrastructure.connectors.discogs.status import get_discogs_status


def make_storage(token: StoredToken | None) -> AsyncMock:
    storage = AsyncMock()
    storage.load_token = AsyncMock(return_value=token)
    return storage


def _discogs_token(extra_data: dict[str, object] | None) -> StoredToken:
    token = StoredToken(
        access_token="discogs-pat",
        token_type="personal_token",
        account_name="wash",
    )
    if extra_data is not None:
        token["extra_data"] = extra_data
    return token


class TestGetDiscogsStatus:
    async def test_no_token_disconnected(self) -> None:
        status = await get_discogs_status("u1", make_storage(None))

        assert status.name == "discogs"
        assert status.auth_method == "token"
        assert status.connected is False
        assert status.detail is None
        assert derive_status_state(status) == "disconnected"

    async def test_connected_reads_identity_from_token(self) -> None:
        storage = make_storage(_discogs_token({"collection_count": 1204}))
        status = await get_discogs_status("u1", storage)

        assert status.connected is True
        assert status.account_name == "wash"
        assert status.detail == "1,204 releases"
        assert derive_status_state(status) == "connected"

    async def test_pat_never_ages_into_reauth(self) -> None:
        # A personal access token has no expiry — a stray expires_at or
        # marker must never fabricate a reauth prompt.
        token = _discogs_token({"reauth_required": True})
        token["expires_at"] = 1
        status = await get_discogs_status("u1", make_storage(token))

        assert status.connected is True
        assert status.auth_error is None

    async def test_probe_is_storage_only(self) -> None:
        # The probe must never construct a Discogs client — status polls fire
        # on every Integrations render and would burn the shared per-IP
        # Discogs budget. Patched at the construction seams themselves
        # (attrs post-init + the pooled-client factory), so ANY client
        # build-up on any import path fails the test — a spy on this
        # module's httpx2 binding would only see clients built *here*.
        from src.infrastructure.connectors.discogs.client import DiscogsAPIClient

        storage = make_storage(_discogs_token({"collection_count": 3}))
        with (
            patch.object(
                DiscogsAPIClient,
                "__attrs_post_init__",
                side_effect=AssertionError("status probe constructed a client"),
            ),
            patch(
                "src.infrastructure.connectors._shared.http_client.make_discogs_client",
                side_effect=AssertionError("status probe built an HTTP client"),
            ),
        ):
            status = await get_discogs_status("u1", storage)

        assert status.detail == "3 releases"
