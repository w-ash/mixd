---
globs: tests/**
---
# Test Rules
- **ALWAYS** use `db_session` fixture, NEVER `get_session()` — each test gets isolated transaction
- Use `poetry run` prefix for pytest, ruff, basedpyright
- No `--timeout` flag configured; don't pass it
- Markers: `slow` (>1s), `performance` (>5s), `diagnostic` — skipped by default
