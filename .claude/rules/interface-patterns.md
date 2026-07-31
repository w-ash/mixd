---
paths:
  - "src/interface/**"
---
# Interface Layer Rules

Every exception below is enforced as an `ignore_imports` entry under
`[tool.importlinter]` in `pyproject.toml`, and CI fails on any edge not listed
there. A docstring claiming an exception is not one — amend this file, or grow
a port.

- All data access goes through `execute_use_case()` from `application/runner.py` — no direct repository access. **Exception (v0.6.5 credential/secret carve-out):** credential-management surfaces share infrastructure helpers directly, by design, so CLI + web reuse one implementation:
  - **Connector OAuth/token management** — auth-URL, code exchange, `TokenStorage` read/write in `routes/auth.py` + `connectors.py` (the original Shared OAuth Utilities decision; CLI + web reuse one `exchange_code`). Also covers `cli/connector_commands.py`, `cli/app.py`'s `whoami` (both call `connector_status`, which reads and silently refreshes stored grants), `api/deps.py` (the same surface factored into the FastAPI dependency the OAuth routes depend on), and `api/app.py`'s startup prune of expired CSRF state via `repositories/oauth_states.py`.
  - **Assistant API-key management** — `routes/assistant.py` + `cli/assistant_commands.py` read/write the acting user's Anthropic key via `infrastructure/chat/credentials`, and validate it via `infrastructure/chat/anthropic_adapter`, without an `execute_use_case()` round-trip. Same shape as OAuth: a write-only per-user secret, not domain data.
  - **In-app OAuth authorization server (v0.9.5)** — the remote-MCP AS (`interface/api/oauth/`) reads/writes its own token machinery (clients, authorization codes, rotating refresh tokens) through `infrastructure/persistence/repositories/oauth_as.py` directly, not via `execute_use_case()`. Same rationale: OAuth codes/tokens are a credential surface, not domain data, and the `/token` endpoint runs with **no session user** (the caller is an OAuth client), so the RLS/`user_context` path a use case assumes doesn't apply. The `oauth_*` tables carry no RLS by design (migration 039) — isolation is explicit `user_id`/PK predicates in the storage helpers.
- **Exception (operational probes):** `routes/health.py` reaches `get_engine()` directly for its `SELECT 1` readiness check. A probe routed through `execute_use_case()` would open *and commit* a transaction, and would report the health of the ORM/UoW/RLS stack rather than of the connection — a broader claim than the endpoint makes. The shallow default path deliberately touches no database at all (Neon scale-to-zero + Fly restart loops); only `?deep=true` probes.
- **Exception (composition root):** `api/app.py`'s lifespan may import infrastructure for process lifecycle only — currently `aclose_all_adapters()` to close cached httpx2 pools on shutdown. No session, no request-path data access; nothing in `application/` owns adapter lifetime. This is the interface-side twin of the DI bridges `application-patterns.md` names. It does not extend to data: lifespan work that touches rows goes through a use case, or — if it is a credential surface — through the carve-out above, which is where the startup OAuth-state prune lands. The lifespan must never manufacture a session for someone else to use; that was the shape this exception replaced.
- CLI uses `run_async()` to bridge sync Typer to async use cases
- API (FastAPI) calls `execute_use_case()` directly — natively async, no bridge needed
- CLI and API are two presentation layers over the same application core — never duplicate use cases
