"""Config-snippet generation for ``mixd mcp install``.

Every supported client wires an stdio MCP server the same way — a ``mcpServers``
map of ``{command, args, env?}`` — so one snippet builder serves all three; only
the human guidance (where the config lives) differs per client. Kept pure and
side-effect-free so it is unit-testable and so ``--print`` never touches the
filesystem.
"""

import shutil

from src.config import get_logger
from src.config.constants import BusinessLimits
from src.domain.entities.shared import JsonDict

logger = get_logger(__name__)

# client key -> (display label, config file path). Claude Code registers via a
# command, not a file, so its guidance is rendered by ``client_location`` (which
# is user-aware) rather than read from here.
CLIENTS: dict[str, tuple[str, str]] = {
    "claude-desktop": (
        "Claude Desktop",
        "~/Library/Application Support/Claude/claude_desktop_config.json",
    ),
    "cursor": ("Cursor", "~/.cursor/mcp.json"),
    "claude-code": (
        "Claude Code",
        "registered via the `claude mcp add` command below",
    ),
}
SUPPORTED_CLIENTS: tuple[str, ...] = tuple(CLIENTS)


def claude_code_command(user_id: str | None) -> str:
    """The ``claude mcp add`` command, pinning ``MIXD_USER_ID`` for a real user.

    Claude Code registers mixd via a command, not a JSON file — so unlike the
    Desktop/Cursor snippet (where the user id rides in the JSON ``env`` block),
    the id must be passed as ``--env`` here or the client resolves
    ``DEFAULT_USER_ID`` and acts on the wrong tenant's library.
    """
    env = ""
    if user_id and user_id != BusinessLimits.DEFAULT_USER_ID:
        env = f"--env MIXD_USER_ID={user_id} "
    return f"claude mcp add mixd {env}-- mixd mcp serve"


def client_location(client: str, user_id: str | None) -> str:
    """Human guidance for where/how to register mixd with ``client``.

    Claude Code's is a user-aware command (env must travel on the command line);
    the file-based clients carry env in the printed JSON snippet instead.
    """
    if client == "claude-code":
        return f"run: {claude_code_command(user_id)}"
    return CLIENTS[client][1]


def _resolve_mixd_command() -> str:
    """Absolute path to the ``mixd`` executable, or the bare name as a fallback.

    GUI clients (Claude Desktop, Cursor) spawn the server with launchd's minimal
    PATH, which usually excludes the venv/pipx bin where ``mixd`` lives — a bare
    ``{"command": "mixd"}`` then fails ENOENT. ``install`` runs in the user's own
    shell, so ``which()`` here sees the real PATH; emit the absolute path it
    finds. If it can't be resolved (unlikely — install ran ``mixd``), fall back
    to the bare name and warn.
    """
    resolved = shutil.which("mixd")
    if resolved is None:
        logger.warning(
            "mcp_install_mixd_not_on_path",
            detail="Emitting bare 'mixd'; a GUI client with a minimal PATH may "
            "fail to launch it. Use an absolute path if so.",
        )
        return "mixd"
    return resolved


def server_entry(user_id: str | None) -> JsonDict:
    """The single mixd server entry: launch ``mixd mcp serve`` over stdio.

    ``MIXD_USER_ID`` is pinned into ``env`` only for a real (non-default) user —
    a local single-user install resolves ``DEFAULT_USER_ID`` on its own, so
    hardcoding it would be noise.
    """
    entry: JsonDict = {"command": _resolve_mixd_command(), "args": ["mcp", "serve"]}
    if user_id and user_id != BusinessLimits.DEFAULT_USER_ID:
        entry["env"] = {"MIXD_USER_ID": user_id}
    return entry


def build_client_config(user_id: str | None) -> JsonDict:
    """The ``mcpServers`` snippet a client merges into its config."""
    return {"mcpServers": {"mixd": server_entry(user_id)}}
