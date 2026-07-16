# Using mixd from an MCP client

Mixd ships a [Model Context Protocol](https://modelcontextprotocol.io) server, so
any MCP-aware client — Claude Desktop, Cursor, Claude Code — can read from and act
on your mixd library through the same tools the in-app assistant uses. "Anything
you can do in the app, your agent can do" — via one shared tool registry, so the
two surfaces never drift.

Two ways to connect:

- **Local (stdio):** the client launches `mixd mcp serve` as a subprocess against
  your local database. No HTTP, no OAuth — identity comes from the environment,
  exactly like every other `mixd` CLI command. Best for a self-hosted setup where
  the client and the database live on the same machine.
- **Remote (production, v0.9.5):** the client connects over authenticated HTTPS to
  your **hosted** library at `https://mixd.me/mcp`, so any MCP client on any
  machine can act on your real account. Identity comes from a one-time browser
  OAuth consent. See [Remote (production) connection](#remote-production-connection).

> **Status (v0.9.5):** read tools and synchronous write tools are exposed over both
> transports. Long-running operations (imports, workflow runs, playlist syncs) are
> **not yet** exposed over MCP — they arrive with the Tasks-extension epic once the
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

## Remote (production) connection

The local stdio server reaches only a database on the same machine. The **remote
transport** makes your hosted library — the one on the production deployment
(Fly.io + Neon) — reachable from any MCP client, anywhere, over authenticated
HTTPS. The exact same read + confirmable-write tools; one registry, three
transports (in-app chat, local stdio, remote HTTP).

### Connect a client

```bash
mixd mcp install --remote --client cursor         # HTTP-transport snippet for Cursor
mixd mcp install --remote --client claude-code    # prints the `claude mcp add` command
mixd mcp install --remote --client claude-desktop # guidance (Desktop configures remote
                                                  #  connectors in its UI, not a file)
mixd mcp install --remote --print                 # raw JSON config on stdout
```

The remote entry is a URL, not a subprocess — `{"mcpServers": {"mixd": {"url":
"https://mixd.me/mcp"}}}`. Override the URL with `--url` if you self-host under a
different domain.

### The one-time OAuth consent

On first use the client opens your browser and walks the OAuth 2.1 handshake
against mixd's own authorization server (issuer `https://mixd.me`):

1. The client discovers the server, registers (CIMD or dynamic registration), and
   redirects you to a **consent page** in the mixd web app.
2. You're already signed in there (the same account you use for the web UI), so you
   just **Approve** — the authorization code is bound server-side to *your* account.
3. The client exchanges the code for an access token and stores it in its own token
   store. mixd writes no new secret to your disk.

The token is **audience-bound** to `https://mixd.me/mcp` and identifies *you* — every
tool call is row-level-security-scoped to your rows, exactly like the web UI. Only
accounts on the deployment's `ALLOWED_EMAILS` allowlist can complete consent. To
revoke access, disconnect the server in your client (which discards its token);
refresh tokens rotate on every use, so a leaked-and-replayed one revokes its whole
family.

### Security model (for operators)

- mixd is an OAuth **resource server** *and* a minimal **authorization server** in
  one process — it validates its own audience-bound EdDSA JWTs locally (no per-call
  auth round-trip) and serves its public key at `/.well-known/jwks.json`.
- **No token passthrough:** the MCP access token authenticates you to *mixd* only.
  Downstream Spotify/Last.fm calls keep using mixd's own per-user stored connector
  credentials — the MCP token is never forwarded to a third party.
- **Neon Auth stays authentication-only.** It cannot act as the OAuth AS (no
  authorize endpoint, no dynamic registration, fixed token audience), so mixd hosts
  the minimal AS itself; Neon Auth still owns the login/session the consent step
  rides on.
- Deployment config (signing key, canonical resource URI) lives in
  [deployment.md](../deployment.md#optional--remote-mcp-v095). Remote MCP is **off
  unless a signing key is configured** — a deployment without `MCP_OAUTH_SIGNING_KEY`
  serves no `/mcp`, `/authorize`, or `/token`.

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
- **Wrong user's data (local).** Set `MIXD_USER_ID` in the server entry's `env`.
- **Remote: 401 / consent loop.** The client couldn't get a valid token. Make sure
  you completed the browser consent on the right mixd account, and that the account
  is on the deployment's `ALLOWED_EMAILS` allowlist (a disallowed account is
  rejected at consent). If the client cached a stale token, disconnect and
  reconnect the server to re-run consent.
- **Remote: `401` with an audience error.** The token's audience doesn't match the
  server's canonical `MCP_RESOURCE_URI`. This usually means the client connected to
  a host alias (e.g. `mixd.fly.dev`) — tokens are always bound to the canonical
  `https://mixd.me/mcp` regardless of which host served the request. Point the
  client at the canonical URL.
- **Remote: browser-based MCP Inspector is blocked by CORS.** Native clients don't
  preflight; the Inspector does. Add its origin to the deployment's `CORS_ORIGINS`
  for testing.
