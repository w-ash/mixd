"""``mixd mcp`` CLI: install snippet generation + serve wiring.

``install`` is a thin shell over ``build_client_config`` (unit-tested directly)
plus rendering; ``serve`` is a thin shell over ``serve_stdio`` (stubbed so the
test never blocks on stdio).
"""

import json

import pytest
from typer.testing import CliRunner

from src.config.constants import BusinessLimits
from src.interface.cli.app import app
from src.interface.mcp import install as install_mod
from src.interface.mcp.install import (
    build_client_config,
    claude_code_command,
    client_location,
    server_entry,
)

runner = CliRunner()


class TestBuildConfig:
    def test_snippet_shape(self) -> None:
        config = build_client_config("default")
        entry = config["mcpServers"]["mixd"]  # type: ignore[index]
        assert entry["command"] == "mixd"
        assert entry["args"] == ["mcp", "serve"]

    def test_default_user_omits_env(self) -> None:
        entry = server_entry(BusinessLimits.DEFAULT_USER_ID)
        assert "env" not in entry

    def test_real_user_pins_env(self) -> None:
        entry = server_entry("alice")
        assert entry["env"] == {"MIXD_USER_ID": "alice"}  # type: ignore[index]

    def test_config_is_json_serialisable(self) -> None:
        # "valid JSON per supported client" — the snippet is client-invariant.
        for client in install_mod.SUPPORTED_CLIENTS:
            assert client in install_mod.CLIENTS
        assert json.loads(json.dumps(build_client_config("alice")))


class TestClaudeCodeGuidance:
    """Claude Code registers via a command, so its env must ride the command line."""

    def test_default_user_omits_env(self) -> None:
        cmd = claude_code_command(BusinessLimits.DEFAULT_USER_ID)
        assert cmd == "claude mcp add mixd -- mixd mcp serve"
        assert "--env" not in cmd

    def test_real_user_pins_env(self) -> None:
        cmd = claude_code_command("alice")
        assert "--env MIXD_USER_ID=alice" in cmd
        # env goes before the `--` separator, not after.
        assert cmd.index("--env") < cmd.index(" -- ")

    def test_client_location_is_user_aware_for_claude_code(self) -> None:
        assert "MIXD_USER_ID=alice" in client_location("claude-code", "alice")
        assert "MIXD_USER_ID" not in client_location(
            "claude-code", BusinessLimits.DEFAULT_USER_ID
        )

    def test_client_location_falls_back_to_file_path_for_others(self) -> None:
        # File-based clients carry env in the JSON snippet, not the location line.
        assert client_location("cursor", "alice") == install_mod.CLIENTS["cursor"][1]


class TestInstallCommand:
    def test_print_emits_valid_json_only(self) -> None:
        result = runner.invoke(app, ["mcp", "install", "--print"])
        assert result.exit_code == 0
        parsed = json.loads(result.stdout)
        assert parsed == build_client_config(BusinessLimits.DEFAULT_USER_ID)

    def test_human_guidance_names_the_client(self) -> None:
        result = runner.invoke(app, ["mcp", "install", "--client", "cursor"])
        assert result.exit_code == 0
        assert "Cursor" in result.stdout

    def test_unsupported_client_rejected(self) -> None:
        result = runner.invoke(app, ["mcp", "install", "--client", "nope"])
        assert result.exit_code != 0
        assert "not supported" in result.output

    def test_claude_code_guidance_carries_non_default_user(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "src.interface.cli.mcp_commands.get_cli_user_id", lambda: "alice"
        )
        result = runner.invoke(app, ["mcp", "install", "--client", "claude-code"])
        assert result.exit_code == 0
        # The `claude mcp add` line must pin the acting user, else it silently
        # registers the default tenant's library.
        assert "MIXD_USER_ID=alice" in result.output


class TestServeCommand:
    def test_serve_runs_server_with_resolved_user(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, str] = {}

        async def _fake_serve(user_id: str) -> None:
            seen["user_id"] = user_id

        monkeypatch.setattr("src.interface.mcp.server.serve_stdio", _fake_serve)
        result = runner.invoke(app, ["mcp", "serve"])
        assert result.exit_code == 0
        assert seen["user_id"] == BusinessLimits.DEFAULT_USER_ID
