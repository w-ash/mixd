"""Regression tests: credential-surface sessions under a *remote* database.

The v0.10.4.1 default-user guard fires at transaction BEGIN whenever the RLS
contextvar still reads ``DEFAULT_USER_ID`` against a hosted database. Local
dev and the test containers are exempt (mode == "local"), which is exactly
how a bare ``get_session()`` on the web-auth path shipped: every test passed
locally while ``GET /api/v1/connectors/{service}/auth-url`` 500'd in prod
with ``DefaultUserOnRemoteDatabaseError``.

These tests arm the guard by forcing ``database_host_and_mode`` to report
remote, then exercise every credential-surface session that a web auth flow
opens: state creation, state validation, the auth-url routes end-to-end, the
Apple Music token POST, and the in-app OAuth AS storage helpers.
"""

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch
import urllib.parse

import httpx2
import pytest

from src.config.constants import BusinessLimits
from src.infrastructure.connectors.apple_music.client import AppleMusicAPIClient
from src.infrastructure.persistence.database.user_context import (
    DefaultUserOnRemoteDatabaseError,
)
from src.infrastructure.persistence.repositories.token_storage import (
    DatabaseTokenStorage,
)
from src.interface.api.routes.auth import _create_state, validate_state
from tests.integration.api.conftest import (
    _stub_launch_background,
    _test_db_env,
    _truncate_all_tables,
)

REAL_USER = "neon-auth-sub-remote-regression"


@pytest.fixture
def remote_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the default-user guard treat the test database as hosted.

    ``_refuse_default_user_on_remote`` resolves ``database_host_and_mode``
    from ``src.config.settings`` at call time, so patching the module
    attribute arms the guard without touching the (testcontainer) URL.
    """
    import importlib

    settings_module = importlib.import_module("src.config.settings")

    monkeypatch.setattr(
        settings_module,
        "database_host_and_mode",
        lambda _url: ("ep-prod-regression.neon.tech", "remote"),
    )


@pytest.fixture
async def user_client(
    postgres_url: str,
    _init_test_schema: None,
) -> AsyncGenerator[httpx2.AsyncClient]:
    """The conftest ``client``, but authenticated as a real (non-default) user.

    ``get_current_user_id`` falls back to ``DEFAULT_USER_ID`` when auth is
    unconfigured, which under remote mode is precisely the accident the guard
    refuses — so the end-to-end tests need a real ``sub`` on the request.
    """
    from src.interface.api.app import create_app
    from src.interface.api.deps import get_current_user_id
    import src.interface.api.routes.workflows as _workflows_mod
    import src.interface.api.services.import_queue as _import_queue_mod
    import src.interface.api.services.sse_operations as _sse_operations_mod
    import src.interface.api.services.workflow_execution as _workflow_execution_mod

    with _test_db_env(postgres_url):
        await _truncate_all_tables()

        with _stub_launch_background(
            _sse_operations_mod,
            _workflows_mod,
            _workflow_execution_mod,
            _import_queue_mod,
        ):
            app = create_app()
            app.dependency_overrides[get_current_user_id] = lambda: REAL_USER
            transport = httpx2.ASGITransport(app=app)
            async with httpx2.AsyncClient(
                transport=transport, base_url="http://test"
            ) as c:
                yield c

        await _truncate_all_tables()


class TestCreateState:
    """``_create_state`` opens its session as the initiating user."""

    async def test_state_row_carries_the_initiating_user(
        self, client: httpx2.AsyncClient, remote_mode: None
    ) -> None:
        state = await _create_state(REAL_USER, "spotify", code_verifier="pkce-v")

        valid, code_verifier, user_id = await validate_state(state, "spotify")
        assert (valid, code_verifier, user_id) == (True, "pkce-v", REAL_USER)

    async def test_default_user_is_still_refused(
        self, client: httpx2.AsyncClient, remote_mode: None
    ) -> None:
        """The fix must scope the session to the user, not disarm the guard."""
        with pytest.raises(DefaultUserOnRemoteDatabaseError):
            await _create_state(BusinessLimits.DEFAULT_USER_ID, "spotify")


class TestValidateState:
    """``validate_state`` is cross-tenant by necessity — the token is the credential."""

    async def test_lookup_succeeds_under_remote_mode(
        self, client: httpx2.AsyncClient, remote_mode: None
    ) -> None:
        state = await _create_state(REAL_USER, "tidal", code_verifier="v")

        valid, _, user_id = await validate_state(state, "tidal")
        assert (valid, user_id) == (True, REAL_USER)

    async def test_miss_returns_invalid_instead_of_raising(
        self, client: httpx2.AsyncClient, remote_mode: None
    ) -> None:
        """Even a miss opens a transaction — the guard must not turn an
        invalid callback state into a 500."""
        assert await validate_state("no-such-state", "spotify") == (False, None, None)


class TestAuthUrlRoutesEndToEnd:
    """The prod symptom: GET /api/v1/connectors/{service}/auth-url 500'd."""

    @pytest.mark.parametrize("service", ["spotify", "apple_music"])
    async def test_auth_url_returns_200_under_remote_mode(
        self, user_client: httpx2.AsyncClient, remote_mode: None, service: str
    ) -> None:
        resp = await user_client.get(f"/api/v1/connectors/{service}/auth-url")

        assert resp.status_code == 200, resp.text
        assert resp.json()["auth_url"]


class TestAppleTokenPostEndToEnd:
    """POST /api/v1/connectors/apple_music/token stores the MUT under the
    state row's user — several sessions deep, all of them guarded."""

    async def test_token_post_returns_204_and_binds_token_to_user(
        self, user_client: httpx2.AsyncClient, remote_mode: None
    ) -> None:
        storage = DatabaseTokenStorage()
        await storage.delete_token("apple_music", REAL_USER)
        try:
            resp = await user_client.get("/api/v1/connectors/apple_music/auth-url")
            assert resp.status_code == 200, resp.text
            query = urllib.parse.urlparse(resp.json()["auth_url"]).query
            state = urllib.parse.parse_qs(query)["state"][0]

            with patch.object(
                AppleMusicAPIClient, "get_storefront", AsyncMock(return_value=None)
            ):
                resp = await user_client.post(
                    "/api/v1/connectors/apple_music/token",
                    json={"music_user_token": "fake-mut", "state": state},
                )

            assert resp.status_code == 204, resp.text
            stored = await storage.load_token("apple_music", REAL_USER)
            assert stored is not None
            assert stored["access_token"] == "fake-mut"
        finally:
            await storage.delete_token("apple_music", REAL_USER)


class TestOAuthASStorageRemoteMode:
    """The in-app AS helpers run with no session user, on purpose — they must
    declare that instead of riding the default contextvar."""

    async def test_client_upsert_and_read_run_user_less(
        self, client: httpx2.AsyncClient, remote_mode: None
    ) -> None:
        from src.infrastructure.persistence.repositories.oauth_as import (
            get_client,
            upsert_client,
        )

        await upsert_client("cid-remote", "dynamic", {"client_name": "t"})
        stored = await get_client("cid-remote")
        assert stored is not None
        assert stored.client_id == "cid-remote"
