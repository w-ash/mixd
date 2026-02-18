---
globs: src/interface/**
---
# Interface Layer Rules
- NEVER access repositories directly — call `execute_use_case()` from `application/runner.py`
- NEVER put business logic here — delegate to application use cases
- CLI uses `run_async()` to bridge sync Typer to async use cases
