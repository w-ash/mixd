---
paths:
  - "src/interface/**"
---
# Interface Layer Rules
- All data access goes through `execute_use_case()` from `application/runner.py` — no direct repository access. **Exception (v0.6.5 credential/secret carve-out):** two credential-management surfaces share infrastructure helpers directly, by design, so CLI + web reuse one implementation:
  - **Connector OAuth/token management** — auth-URL, code exchange, `TokenStorage` read/write in `routes/auth.py` + `connectors.py` (the original Shared OAuth Utilities decision; CLI + web reuse one `exchange_code`).
  - **Assistant API-key management** — `routes/assistant.py` + `cli/assistant_commands.py` read/write the acting user's Anthropic key via `infrastructure/chat/credentials`, and validate it via `infrastructure/chat/anthropic_adapter`, without an `execute_use_case()` round-trip. Same shape as OAuth: a write-only per-user secret, not domain data.
- CLI uses `run_async()` to bridge sync Typer to async use cases
- API (FastAPI) calls `execute_use_case()` directly — natively async, no bridge needed
- CLI and API are two presentation layers over the same application core — never duplicate use cases
