"""Tests for FastAPI auth and connector-precondition dependencies.

Covers ``get_current_user_id`` extracting the JWT sub claim from the ASGI
scope (falling back to DEFAULT_USER_ID when auth is disabled or claims are
missing), ``token_access`` — the predicate both the trigger routes' 409 and the
sync-target list's availability flag read — and ``sync_target_access``, which
must judge every target from one token read.
"""

from collections.abc import Collection, Mapping
import subprocess
import sys
from typing import cast

import pytest
from starlette.requests import Request

from src.application.use_cases._shared.sync_targets import SYNC_TARGETS, SyncTarget
from src.config.constants import BusinessLimits
from src.domain.exceptions import (
    ConnectorNotConnectedError,
    ConnectorScopeMissingError,
)
from src.infrastructure.connectors._shared.token_storage import StoredToken
from src.interface.api import deps as deps_module
from src.interface.api.connector_access import token_access
from src.interface.api.deps import (
    get_current_user_id,
    require_sync_target_access,
    sync_target_access,
)


def _make_request(scope_extras: dict | None = None) -> Request:
    """Build a minimal Starlette Request with the given scope additions."""
    scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
    if scope_extras:
        scope.update(scope_extras)
    return Request(scope)


class TestGetCurrentUserId:
    """Extracting user ID from Neon Auth JWT claims on the request scope."""

    def test_returns_sub_from_auth_claims(self):
        request = _make_request({
            "auth_user": {"sub": "usr_abc123", "email": "a@b.com"}
        })
        assert get_current_user_id(request) == "usr_abc123"

    def test_returns_default_when_no_auth_user_in_scope(self):
        request = _make_request()
        assert get_current_user_id(request) == BusinessLimits.DEFAULT_USER_ID

    def test_returns_default_when_claims_lack_sub(self):
        request = _make_request({"auth_user": {"email": "a@b.com"}})
        assert get_current_user_id(request) == BusinessLimits.DEFAULT_USER_ID


class TestTokenAccess:
    """The one predicate behind both the 409 gate and the sync-target list."""

    def test_absent_token_blocks_as_not_connected(self):
        access = token_access(None, ())

        assert access.available is False
        assert access.blocked_reason == "CONNECTOR_NOT_CONNECTED"

    def test_present_token_with_no_scope_demand_is_available(self):
        access = token_access(StoredToken(session_key="sk"), ())

        assert access.available is True
        assert access.blocked_reason is None

    def test_scope_gap_blocks_separately_from_connection(self):
        # scope_missing keeps the connector connected — its other surfaces still
        # work — so the two conditions carry different codes and remedies.
        access = token_access(
            StoredToken(access_token="at", scope="user-library-read"),
            {"user-read-recently-played"},
        )

        assert access.blocked_reason == "CONNECTOR_SCOPE_MISSING"
        assert access.missing_scopes == frozenset({"user-read-recently-played"})

    def test_grant_covering_every_required_scope_is_available(self):
        access = token_access(
            StoredToken(access_token="at", scope="user-library-read a b"),
            {"a", "b"},
        )

        assert access.available is True

    def test_token_stored_without_a_grant_string_reports_every_scope_missing(self):
        # The correct re-consent signal: a token predating scope tracking
        # cannot be assumed to carry anything.
        access = token_access(StoredToken(access_token="at"), {"a", "b"})

        assert access.blocked_reason == "CONNECTOR_SCOPE_MISSING"
        assert access.missing_scopes == frozenset({"a", "b"})


class _RecordingTokenStorage:
    """Serves a fixed per-service map and records how it was asked."""

    def __init__(self, tokens: dict[str, StoredToken] | None = None) -> None:
        self._tokens = tokens or {}
        self.batch_calls: list[tuple[tuple[str, ...], str]] = []
        self.single_calls: list[tuple[str, str]] = []

    async def load_token(self, service: str, user_id: str) -> StoredToken | None:
        self.single_calls.append((service, user_id))
        return self._tokens.get(service)

    async def load_tokens(
        self, services: Collection[str], user_id: str
    ) -> Mapping[str, StoredToken | None]:
        self.batch_calls.append((tuple(services), user_id))
        return {s: self._tokens.get(s) for s in services}


class TestSyncTargetAccess:
    """Every sync target judged from a single batched token read."""

    @pytest.fixture
    def storage(self, monkeypatch: pytest.MonkeyPatch) -> _RecordingTokenStorage:
        recorder = _RecordingTokenStorage()
        monkeypatch.setattr(deps_module, "get_token_storage", lambda: recorder)
        return recorder

    async def test_reads_every_service_once_in_one_batch(
        self, storage: _RecordingTokenStorage
    ) -> None:
        # One read, not one per connector — this runs on every Sync page load.
        _ = await sync_target_access("usr-1")

        assert storage.single_calls == []
        assert len(storage.batch_calls) == 1
        services, user_id = storage.batch_calls[0]
        assert set(services) == {spec.service for spec in SYNC_TARGETS.values()}
        assert user_id == "usr-1"

    async def test_every_target_is_blocked_without_a_token(
        self, storage: _RecordingTokenStorage
    ) -> None:
        access = await sync_target_access("usr-1")

        assert set(access) == set(SYNC_TARGETS)
        assert all(
            verdict.blocked_reason == "CONNECTOR_NOT_CONNECTED"
            for verdict in access.values()
        )

    async def test_targets_sharing_a_connector_are_judged_per_scope(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A connected token satisfies the targets that demand nothing extra and
        # still blocks one whose surface needs a scope the grant never carried.
        services = {spec.service for spec in SYNC_TARGETS.values()}
        recorder = _RecordingTokenStorage({
            service: StoredToken(access_token="at") for service in services
        })
        monkeypatch.setattr(deps_module, "get_token_storage", lambda: recorder)

        access = await sync_target_access("usr-1")

        for target, spec in SYNC_TARGETS.items():
            expected = None if not spec.required_scopes else "CONNECTOR_SCOPE_MISSING"
            assert access[target].blocked_reason == expected


async def _gate_verdict(target: str, user_id: str) -> str | None:
    """The blocked_reason ``require_sync_target_access`` implies for ``target``."""
    dependency = require_sync_target_access(cast("SyncTarget", target))
    try:
        await dependency(user_id)
    except ConnectorNotConnectedError:
        return "CONNECTOR_NOT_CONNECTED"
    except ConnectorScopeMissingError:
        return "CONNECTOR_SCOPE_MISSING"
    return None


class TestRequireSyncTargetAccess:
    """The trigger gate and the list's availability flag read the same row.

    Both derive from ``SYNC_TARGETS[target].service`` + ``required_scopes``, so
    a target can never advertise itself as runnable and then be refused — which
    is what a route restating its own (service, scopes) pair could drift into.
    """

    @pytest.mark.parametrize("connected", [False, True])
    async def test_gate_verdict_matches_the_list_verdict_for_every_target(
        self, monkeypatch: pytest.MonkeyPatch, connected: bool
    ) -> None:
        services = {spec.service for spec in SYNC_TARGETS.values()}
        recorder = _RecordingTokenStorage(
            {service: StoredToken(access_token="at") for service in services}
            if connected
            else None
        )
        monkeypatch.setattr(deps_module, "get_token_storage", lambda: recorder)

        listed = await sync_target_access("usr-1")

        for target in SYNC_TARGETS:
            assert await _gate_verdict(target, "usr-1") == listed[target].blocked_reason

    async def test_gate_reads_the_registry_service_not_the_target_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``apple:plays`` runs on the ``apple_music`` credential — the 409 has
        to name the connector the user reconnects, not the target's prefix."""
        recorder = _RecordingTokenStorage()
        monkeypatch.setattr(deps_module, "get_token_storage", lambda: recorder)

        with pytest.raises(ConnectorNotConnectedError) as excinfo:
            await require_sync_target_access("apple:plays")("usr-1")

        assert "apple_music" in str(excinfo.value).lower()
        assert recorder.single_calls == [("apple_music", "usr-1")]


class TestConnectorAccessDependencyDirection:
    """Response schemas name the verdict types, so those types must not live
    behind FastAPI or the token-storage seam."""

    @pytest.mark.parametrize(
        "module",
        [
            "src.interface.api.connector_access",
            "src.interface.api.schemas.schedules",
        ],
    )
    def test_importing_it_does_not_pull_in_fastapi(self, module: str) -> None:
        # A subprocess, because the suite has already imported the whole app.
        probe = (
            "import importlib, sys;"
            f"importlib.import_module({module!r});"
            "print('fastapi' in sys.modules)"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "False"
