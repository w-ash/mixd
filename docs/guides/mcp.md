# Using mixd from an MCP client

Mixd ships a [Model Context Protocol](https://modelcontextprotocol.io) server, so
any MCP-aware client — Claude Desktop, Cursor, Claude Code — can read from and act
on your mixd library through the same tools the in-app assistant uses. "Anything
you can do in the app, your agent can do" — via one shared tool registry, so the
two surfaces never drift.

The server runs locally over **stdio**: the client launches `mixd mcp serve` as a
subprocess. There's no HTTP, no OAuth, no new credential — identity comes from the
environment, exactly like every other `mixd` CLI command.

> **Status (v0.9.3):** read tools and synchronous write tools are exposed.
> Long-running operations (imports, workflow runs, playlist syncs) are **not yet**
> exposed over MCP — they arrive with the Tasks-extension epic once the
> MCP 2026-07-28 spec + stable SDK settle. See the
> [capability matrix](../web-ui/capability-matrix.md) `MCP` column for the exact,
> generated coverage list.

## Install

Print the config snippet for your client and merge it into the client's config:

```bash
mixd mcp install --client claude-desktop   # human-friendly: snippet + file path
mixd mcp install --print                    # raw JSON on stdout, for scripting
```

Supported `--client` values: `claude-desktop`, `cursor`, `claude-code`.

The snippet is the same `mcpServers` shape every client understands. `install`
resolves the **absolute path** to the `mixd` executable (via `which mixd` in your
shell) and emits it as the `command`, so a GUI client launched with a minimal
`PATH` can still find it:

```json
{
  "mcpServers": {
    "mixd": {
      "command": "/Users/you/.local/bin/mixd",
      "args": ["mcp", "serve"]
    }
  }
}
```

Config file locations:

| Client | Where the config lives |
| --- | --- |
| Claude Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Cursor | `~/.cursor/mcp.json` |
| Claude Code | `claude mcp add mixd -- mixd mcp serve` (registers via command, not a file) |

Notes:

- **`command`** is the absolute path `install` resolved for the `mixd` console
  script — GUI clients (Claude Desktop, Cursor) spawn the server with launchd's
  minimal `PATH`, which usually excludes the venv/pipx bin, so a bare `"mixd"`
  would fail to launch. If `which mixd` can't resolve at install time, the
  snippet falls back to a bare `"mixd"` and prints a warning; substitute the
  absolute path (or `uv run mixd`) yourself in that case.
- **Multi-user / non-default user:** set `MIXD_USER_ID` in the entry's `env` (the
  install snippet adds it automatically when your local `MIXD_USER_ID` is set to a
  non-default value). Every tool call is scoped to that user via the same
  row-level-security path the web UI uses. For **Claude Code**, which registers via
  a command rather than a JSON file, pass it on the command line —
  `claude mcp add mixd --env MIXD_USER_ID=<id> -- mixd mcp serve`; `mixd mcp install
  --client claude-code` prints this form for you when `MIXD_USER_ID` is set.

After merging, restart the client. mixd's tools appear in the client's tool
palette.

## How mutations work: preview, then confirm

Nothing mutates silently. Every write tool is two-phase and in-band:

1. **Call the write tool normally.** It does **not** commit — it returns
   `{"status": "needs_confirmation", "confirm_token": "…", "preview": {…}}` with a
   human-readable summary of exactly what will change.
2. **Call the same tool again** with the same arguments plus `confirm: true` and
   the `confirm_token` from step 1. Only then does it commit.

Confirm tokens are single-use and expire after 5 minutes. If the token has expired
you get a fresh preview back instead of a stale commit; if the arguments changed
between preview and confirm, the call is rejected (re-preview to proceed).

The tool annotations (`readOnlyHint`, `destructiveHint`, …) are advisory hints for
the client — mixd's confirmation gate is enforced server-side regardless, so a
misbehaving client can never commit a write without the second confirmed call.

## Worked examples

Phrase these to your MCP client in natural language; it selects and sequences the
tools.

**1. Read your library**
> "What's in my Revival playlist, and how many tracks does it have?"

The client calls a read tool (`query_playlists` / `query_library`) scoped to your
user and answers from the result. No confirmation needed — reads never mutate.

**2. Tag tracks (a confirmed write)**
> "Tag every track in my Revival playlist with `context:sunday`."

The client calls `manage_tags`, gets a preview ("Add tag `context:sunday` to N
tracks"), shows it to you, and only commits when you approve — which it does by
re-calling `manage_tags` with `confirm: true` and the token.

**3. Set preferences in batch**
> "Mark these five tracks as loved."

`set_preferences` previews the five changes; confirm to commit. Batch-first: the
tool takes a list of track ids in one call.

**4. Build a workflow**
> "Build me a workflow that collects upbeat tracks I haven't played in six months."

The client calls `generate_workflow_def` (read — it just produces a definition),
then `save_workflow` (write — preview + confirm) to persist it. The saved workflow
shows up in the mixd web UI like any other.

## What's not exposed (and why)

- **Agentic tools** (`code_execution`, `delegate_analysis`, tool search) — these
  are the in-app assistant's own agentic machinery. An MCP client brings its own
  agent loop, so exposing them would be redundant.
- **Long-running operations** (imports, workflow *runs*, playlist syncs) — exposed
  in a later revision via the MCP Tasks extension, which models observable,
  cancellable background work. Until then, launch these from the web UI or CLI.
- **Human-only capabilities** — connector OAuth, account management, admin
  reset — are blacklisted from the agent surface entirely, by design.

## Troubleshooting

- **The client shows no mixd tools.** Confirm `mixd mcp serve` runs by hand
  (`mixd mcp serve` should start and wait on stdin). Check the client's MCP logs;
  a wrong `command` path is the usual cause.
- **Protocol errors / garbled output.** stdout is the JSON-RPC channel — mixd
  routes all its logging to stderr and a log file precisely so it can't corrupt
  the stream. If you wrapped `mixd` in a script, make sure that script writes
  nothing to stdout.
- **Wrong user's data.** Set `MIXD_USER_ID` in the server entry's `env`.
