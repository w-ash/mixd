"""Unit tests for the `mixd tidal` CLI group (v0.11.3 T6).

``auth`` runs the device-code flow first (Tidal's device_authorization
endpoint is undocumented — backlog decision), auto-falls back to the
localhost browser redirect on ``DeviceCodeUnsupportedError`` with a message
saying so, and takes ``--browser`` to force the fallback. Flow functions
are patched at their source module (the command imports them lazily inside
the body, mirroring the discogs group's test pattern).
"""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from typer.testing import CliRunner

import src.application.use_cases.get_tidal_snapshot as snapshot_mod
from src.application.use_cases.get_tidal_snapshot import (
    GetTidalSnapshotResult,
    TidalSnapshotItem,
)
from src.domain.exceptions import TidalAuthRequiredError
import src.infrastructure.connectors._shared.token_storage as storage_mod
from src.infrastructure.connectors._shared.token_storage import StoredToken
import src.infrastructure.connectors.tidal.device_auth as tidal_auth_mod
from src.infrastructure.connectors.tidal.device_auth import (
    DeviceAuthorization,
    DeviceCodeExpiredError,
    DeviceCodeUnsupportedError,
)
from src.interface.cli.app import app

runner = CliRunner()

_GRANT = DeviceAuthorization(
    device_code="dc-1",
    user_code="ABCDE",
    verification_uri="link.tidal.com",
    verification_uri_complete="link.tidal.com/ABCDE",
    expires_in=300,
    interval=5,
)


def _stored() -> StoredToken:
    return StoredToken(
        access_token="at-1",
        refresh_token="rt-1",
        token_type="Bearer",
        expires_at=int(time.time()) + 43200,
        extra_data={"authorized_at": int(time.time())},
    )


@pytest.fixture(autouse=True)
def mock_storage(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    storage = MagicMock()
    monkeypatch.setattr(storage_mod, "get_token_storage", lambda: storage)
    return storage


class TestTidalAuth:
    def test_device_flow_success_prints_code_and_connects(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_device_auth(storage, user_id, *, on_verification=None):
            # The real flow hands the verification code to the CLI's
            # renderer mid-poll — exercise that handoff.
            if on_verification is not None:
                on_verification(_GRANT)
            return _stored()

        device = AsyncMock(side_effect=fake_device_auth)
        browser = AsyncMock()
        monkeypatch.setattr(tidal_auth_mod, "run_device_auth", device)
        monkeypatch.setattr(tidal_auth_mod, "run_browser_auth", browser)

        result = runner.invoke(app, ["tidal", "auth"])

        assert result.exit_code == 0
        assert "ABCDE" in result.output
        assert "link.tidal.com" in result.output
        assert "connected" in result.output.lower()
        device.assert_awaited_once()
        browser.assert_not_awaited()
        assert "Traceback" not in result.output

    def test_unsupported_endpoint_auto_falls_back_with_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        device = AsyncMock(side_effect=DeviceCodeUnsupportedError())
        browser = AsyncMock(return_value=_stored())
        monkeypatch.setattr(tidal_auth_mod, "run_device_auth", device)
        monkeypatch.setattr(tidal_auth_mod, "run_browser_auth", browser)

        result = runner.invoke(app, ["tidal", "auth"])

        assert result.exit_code == 0
        # The fallback must announce itself — a silent switch would leave
        # the T7 live probe blind to which flow actually ran.
        assert "falling back" in result.output.lower()
        assert "connected" in result.output.lower()
        device.assert_awaited_once()
        browser.assert_awaited_once()
        assert "Traceback" not in result.output

    def test_browser_flag_forces_fallback_without_device_attempt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        device = AsyncMock()
        browser = AsyncMock(return_value=_stored())
        monkeypatch.setattr(tidal_auth_mod, "run_device_auth", device)
        monkeypatch.setattr(tidal_auth_mod, "run_browser_auth", browser)

        result = runner.invoke(app, ["tidal", "auth", "--browser"])

        assert result.exit_code == 0
        device.assert_not_awaited()
        browser.assert_awaited_once()
        assert "connected" in result.output.lower()

    def test_expired_device_code_exits_nonzero_with_rerun_hint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        device = AsyncMock(side_effect=DeviceCodeExpiredError())
        monkeypatch.setattr(tidal_auth_mod, "run_device_auth", device)

        result = runner.invoke(app, ["tidal", "auth"])

        assert result.exit_code == 1
        assert "expired" in result.output.lower()
        assert "again" in result.output.lower()
        assert "Traceback" not in result.output

    def test_other_failure_exits_nonzero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        device = AsyncMock(side_effect=RuntimeError("token endpoint melted"))
        monkeypatch.setattr(tidal_auth_mod, "run_device_auth", device)

        result = runner.invoke(app, ["tidal", "auth"])

        assert result.exit_code != 0
        assert "Traceback" not in result.output


def _snapshot_result(
    total: int, recent: tuple[TidalSnapshotItem, ...] = ()
) -> GetTidalSnapshotResult:
    return GetTidalSnapshotResult(total_items=total, recent=recent)


class TestSnapshot:
    """``mixd tidal snapshot`` renders the favorites snapshot use-case result
    (patched at its source module, mirroring the discogs group): a Rich table
    of recent favorites under a ``TIDAL · N favorites`` header, an inviting
    zero-state for the empty collection, and an actionable nonzero exit when
    Tidal is not connected."""

    def test_renders_table_with_items(self, monkeypatch: pytest.MonkeyPatch) -> None:
        item = TidalSnapshotItem(
            title="Rio",
            artists="Duran Duran",
            added_at="2026-08-01T12:34:56+00:00",
        )
        run = AsyncMock(return_value=_snapshot_result(42, (item,)))
        monkeypatch.setattr(snapshot_mod, "run_get_tidal_snapshot", run)

        result = runner.invoke(app, ["tidal", "snapshot"])

        assert result.exit_code == 0
        assert "TIDAL" in result.output
        assert "42" in result.output
        assert "Rio" in result.output
        assert "Duran Duran" in result.output
        assert "Traceback" not in result.output

    def test_empty_collection_prints_inviting_zero_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:

        run = AsyncMock(return_value=_snapshot_result(0))
        monkeypatch.setattr(snapshot_mod, "run_get_tidal_snapshot", run)

        result = runner.invoke(app, ["tidal", "snapshot"])

        assert result.exit_code == 0
        assert "no" in result.output.lower()
        assert "favorites" in result.output.lower()
        # An invitation, not an error.
        assert "error" not in result.output.lower()
        assert "Traceback" not in result.output

    def test_disconnected_exits_nonzero_with_connect_hint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:

        run = AsyncMock(side_effect=TidalAuthRequiredError())
        monkeypatch.setattr(snapshot_mod, "run_get_tidal_snapshot", run)

        result = runner.invoke(app, ["tidal", "snapshot"])

        assert result.exit_code != 0
        assert "mixd tidal auth" in result.output
        assert "Traceback" not in result.output
