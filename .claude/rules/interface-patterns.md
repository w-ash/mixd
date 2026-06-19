---
paths:
  - "src/interface/**"
---
# Interface Layer Rules
- All data access goes through `execute_use_case()` from `application/runner.py` — no direct repository access. **Exception:** connector OAuth/token management (auth-URL, code exchange, `TokenStorage` read/write in `routes/auth.py` + `connectors.py`) shares infrastructure helpers directly by design (the v0.6.5 Shared OAuth Utilities decision, so CLI + web reuse one `exchange_code`).
- CLI uses `run_async()` to bridge sync Typer to async use cases
- API (FastAPI) calls `execute_use_case()` directly — natively async, no bridge needed
- CLI and API are two presentation layers over the same application core — never duplicate use cases
