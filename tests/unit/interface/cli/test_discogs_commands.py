"""Unit tests for the `mixd discogs` CLI group (v0.11.1 BYO token).

``connect`` validates through the shared ``token_service`` (patched at its
source module — the command imports it lazily inside the body) and persists
via token storage. The token is prompted with hidden input when ``--token``
is omitted; disconnect is the generic ``mixd connectors disconnect discogs``,
so no per-connector disconnect lives here.

``snapshot`` renders the collection snapshot use-case result (patched at its
source module, mirroring the connect tests): a Rich table of recent items
under a ``{username} · N releases`` header, an inviting zero-state for the
empty collection (the live account's actual state), and an actionable
nonzero exit when Discogs is not connected.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from typer.testing import CliRunner

import src.application.use_cases.get_discogs_snapshot as snapshot_mod
from src.application.use_cases.get_discogs_snapshot import (
    DiscogsSnapshotItem,
    GetDiscogsSnapshotResult,
)
from src.domain.exceptions import DiscogsAuthRequiredError
import src.infrastructure.connectors._shared.token_storage as storage_mod
from src.infrastructure.connectors._shared.token_storage import StoredToken
import src.infrastructure.connectors.discogs.token_service as token_service_mod
from src.interface.cli.app import app

runner = CliRunner()

_TOKEN = "discogs-pat-123"


def _stored() -> StoredToken:
    return StoredToken(
        access_token=_TOKEN,
        token_type="personal_token",
        account_name="wash",
        extra_data={"collection_count": 42, "validated_at": 1_755_000_000},
    )


def _mock_storage(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    save = AsyncMock()
    storage = MagicMock()
    storage.save_token = save
    monkeypatch.setattr(storage_mod, "get_token_storage", lambda: storage)
    return save


class TestConnect:
    def test_connect_with_flag_validates_and_saves(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        save = _mock_storage(monkeypatch)
        validate = AsyncMock(return_value=_stored())
        monkeypatch.setattr(token_service_mod, "validate_and_build_token", validate)

        result = runner.invoke(app, ["discogs", "connect", "--token", _TOKEN])

        assert result.exit_code == 0
        assert "wash" in result.output
        validate.assert_awaited_once_with(_TOKEN)
        save.assert_awaited_once()
        service, _user_id, stored = save.await_args.args
        assert service == "discogs"
        assert stored["access_token"] == _TOKEN
        assert "Traceback" not in result.output

    def test_connect_prompts_hidden_when_flag_omitted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        save = _mock_storage(monkeypatch)
        validate = AsyncMock(return_value=_stored())
        monkeypatch.setattr(token_service_mod, "validate_and_build_token", validate)

        result = runner.invoke(app, ["discogs", "connect"], input=f"{_TOKEN}\n")

        assert result.exit_code == 0
        validate.assert_awaited_once_with(_TOKEN)
        save.assert_awaited_once()
        # Hidden input must never echo the secret back to the terminal.
        assert _TOKEN not in result.output

    def test_connect_rejected_token_exits_nonzero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        save = _mock_storage(monkeypatch)
        validate = AsyncMock(
            side_effect=DiscogsAuthRequiredError(
                "Discogs rejected that personal access token — check it was "
                "copied in full."
            )
        )
        monkeypatch.setattr(token_service_mod, "validate_and_build_token", validate)

        result = runner.invoke(app, ["discogs", "connect", "--token", "bad"])

        assert result.exit_code == 1
        assert "rejected" in result.output.lower()
        save.assert_not_awaited()
        assert "Traceback" not in result.output


def _snapshot_result(
    total: int, recent: tuple[DiscogsSnapshotItem, ...] = ()
) -> GetDiscogsSnapshotResult:
    return GetDiscogsSnapshotResult(
        username="attritus", total_items=total, recent=recent
    )


class TestSnapshot:
    def test_renders_table_with_items(self, monkeypatch: pytest.MonkeyPatch) -> None:
        item = DiscogsSnapshotItem(
            title="Rio",
            artists="Duran Duran",
            year=1982,
            formats="Vinyl (LP, Album)",
            date_added="2026-08-01T10:00:00-07:00",
        )
        run = AsyncMock(return_value=_snapshot_result(42, (item,)))
        monkeypatch.setattr(snapshot_mod, "run_get_discogs_snapshot", run)

        result = runner.invoke(app, ["discogs", "snapshot"])

        assert result.exit_code == 0
        assert "attritus" in result.output
        assert "42" in result.output
        assert "Rio" in result.output
        assert "Duran Duran" in result.output
        assert "1982" in result.output
        assert "Traceback" not in result.output

    def test_empty_collection_prints_inviting_zero_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = AsyncMock(return_value=_snapshot_result(0))
        monkeypatch.setattr(snapshot_mod, "run_get_discogs_snapshot", run)

        result = runner.invoke(app, ["discogs", "snapshot"])

        assert result.exit_code == 0
        assert "empty" in result.output.lower()
        assert "discogs.com" in result.output
        # An invitation, not an error.
        assert "error" not in result.output.lower()
        assert "Traceback" not in result.output

    def test_disconnected_exits_nonzero_with_connect_hint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = AsyncMock(side_effect=DiscogsAuthRequiredError())
        monkeypatch.setattr(snapshot_mod, "run_get_discogs_snapshot", run)

        result = runner.invoke(app, ["discogs", "snapshot"])

        assert result.exit_code != 0
        assert "connect" in result.output.lower()
        assert "Traceback" not in result.output
