---
paths:
  - "src/interface/**"
---
# Interface Layer Rules
- All data access goes through `execute_use_case()` from `application/runner.py` — no direct repository access. **Exception (v0.6.5 credential/secret carve-out):** two credential-management surfaces share infrastructure helpers directly, by design, so CLI + web reuse one implementation:
  - **Connector OAuth/token management** — auth-URL, code exchange, `TokenStorage` read/write in `routes/auth.py` + `connectors.py` (the original Shared OAuth Utilities decision; CLI + web reuse one `exchange_code`).
  - **Assistant API-key management** — `routes/assistant.py` + `cli/assistant_commands.py` read/write the acting user's Anthropic key via `infrastructure/chat/credentials`, and validate it via `infrastructure/chat/anthropic_adapter`, without an `execute_use_case()` round-trip. Same shape as OAuth: a write-only per-user secret, not domain data.
  - **In-app OAuth authorization server (v0.9.5)** — the remote-MCP AS (`interface/api/oauth/`) reads/writes its own token machinery (clients, authorization codes, rotating refresh tokens) through `infrastructure/persistence/repositories/oauth_as.py` directly, not via `execute_use_case()`. Same rationale: OAuth codes/tokens are a credential surface, not domain data, and the `/token` endpoint runs with **no session user** (the caller is an OAuth client), so the RLS/`user_context` path a use case assumes doesn't apply. The `oauth_*` tables carry no RLS by design (migration 039) — isolation is explicit `user_id`/PK predicates in the storage helpers.
- CLI uses `run_async()` to bridge sync Typer to async use cases
- API (FastAPI) calls `execute_use_case()` directly — natively async, no bridge needed
- CLI and API are two presentation layers over the same application core — never duplicate use cases
