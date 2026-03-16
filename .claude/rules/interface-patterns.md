---
paths:
  - "src/interface/**"
---
# Interface Layer Rules
- All data access goes through `execute_use_case()` from `application/runner.py` — no direct repository access
- CLI uses `run_async()` to bridge sync Typer to async use cases
- API (FastAPI) calls `execute_use_case()` directly — natively async, no bridge needed
- CLI and API are two presentation layers over the same application core — never duplicate use cases
