---
paths:
  - "scripts/**"
---
# One-Off Script Rules

`scripts/` holds diagnostic, smoke-test, and data-cleanup scripts. They get less ceremony than `src/` but follow shared conventions for discoverability and safety.

## Naming

Prefix the filename by intent — readable in `ls`:

- `diagnose_*` — exercise an external API, print raw responses (`diagnose_lastfm.py`, `diagnose_spotify.py`).
- `check_*` — read-only data-integrity scan (`check_orphaned_records.py`).
- `find_*` — read-only discovery (`find_duplicate_tracks.py`).
- `cleanup_*` / `delete_*` — destructive; require explicit `--apply` or `--fix` flag, default to dry-run.
- `smoke_test_*` — end-to-end exercise of a feature against real data.
- `investigate_*` / `debug_*` — interactive exploration, often short-lived.
- `export_*` — produce an artifact for downstream tooling (e.g., `export_openapi.py`).

## File template

```python
#!/usr/bin/env python3
"""<one-line summary>.

<paragraph: what it does and when to run it>.

Usage:
    uv run python scripts/<name>.py [--apply]
"""

import asyncio

from src.config import get_logger
from src.infrastructure.persistence.database import get_session

logger = get_logger(__name__)
```

- Logging via `get_logger(__name__)` (or `setup_script_logger` for stand-alone scripts that bypass the app's logging config).
- `print()` is fine for raw API dumps in `diagnose_*` scripts; use `logger.*` for everything else.
- Run via `uv run python scripts/<name>.py` so the venv is loaded.

## Conventions

- **Async DB access**: open a session via `get_session()` / `db_session_context()`. Wrap long ops with `asyncio.wait_for(coro, timeout=...)`.
- **Destructive ops**: default to dry-run; require an explicit `--apply` / `--fix` / `--delete` flag to mutate. Print the plan before executing.
- **Args**: use `typer` for more than one flag; bare `sys.argv` for a single flag is acceptable.
- **No tests required** — scripts are write-to-be-deleted artifacts. When a diagnostic graduates to a recurring need, promote it to `src/`.
- **Dependency direction**: scripts import from `src/`, never the reverse.
