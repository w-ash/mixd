"""Unit tests for the `mixd assistant` CLI group (v0.9.0.1).

The infra credential/validation calls are patched at their source modules
(imported inside the command bodies), so ``run_async`` runs the real command
coroutine without touching the DB or the network.
"""

from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner

import src.infrastructure.chat.anthropic_adapter as adapter_mod
import src.infrastructure.chat.credentials as creds_mod
from src.interface.cli.app import app

runner = CliRunner()

_VALID_KEY = "sk-ant-api03-test0000000000000000000000"


class TestConnect:
    def test_connect_valid_key_stores(self, monkeypatch: pytest.MonkeyPatch) -> None:
        save = AsyncMock()
        monkeypatch.setattr(
            adapter_mod, "validate_anthropic_key", AsyncMock(return_value=True)
        )
        monkeypatch.setattr(creds_mod, "save_user_anthropic_key", save)

        result = runner.invoke(app, ["assistant", "connect", "--key", _VALID_KEY])

        assert result.exit_code == 0
        assert "connected" in result.output.lower()
        assert save.await_count == 1
        assert "Traceback" not in result.output

    def test_connect_rejected_key_exits_nonzero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        save = AsyncMock()
        monkeypatch.setattr(
            adapter_mod, "validate_anthropic_key", AsyncMock(return_value=False)
        )
        monkeypatch.setattr(creds_mod, "save_user_anthropic_key", save)

        result = runner.invoke(app, ["assistant", "connect", "--key", _VALID_KEY])

        assert result.exit_code == 1
        assert "rejected" in result.output.lower()
        assert save.await_count == 0  # never stored
        assert "Traceback" not in result.output

    def test_connect_malformed_key_never_calls_anthropic(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        validate = AsyncMock(return_value=True)
        monkeypatch.setattr(adapter_mod, "validate_anthropic_key", validate)
        monkeypatch.setattr(creds_mod, "save_user_anthropic_key", AsyncMock())

        result = runner.invoke(app, ["assistant", "connect", "--key", "nope"])

        assert result.exit_code == 1
        assert validate.await_count == 0
        assert "Traceback" not in result.output


class TestTest:
    def test_test_valid_stored_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            creds_mod, "load_user_anthropic_key", AsyncMock(return_value=_VALID_KEY)
        )
        monkeypatch.setattr(
            adapter_mod, "validate_anthropic_key", AsyncMock(return_value=True)
        )

        result = runner.invoke(app, ["assistant", "test"])

        assert result.exit_code == 0
        assert "valid" in result.output.lower()

    def test_test_no_stored_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            creds_mod, "load_user_anthropic_key", AsyncMock(return_value=None)
        )

        result = runner.invoke(app, ["assistant", "test"])

        assert result.exit_code == 1
        assert "no api key" in result.output.lower()


class TestDisconnect:
    def test_disconnect_deletes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        delete = AsyncMock()
        monkeypatch.setattr(creds_mod, "delete_user_anthropic_key", delete)

        result = runner.invoke(app, ["assistant", "disconnect"])

        assert result.exit_code == 0
        assert "disconnected" in result.output.lower()
        assert delete.await_count == 1
