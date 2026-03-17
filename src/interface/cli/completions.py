"""Shell completion callbacks for CLI arguments.

Completion callbacks must be synchronous (Click limitation). Uses raw sqlite3
for fast local reads without importing the async stack. Returns [] on any error
so completion never crashes the shell.
"""

# pyright: reportAny=false
# Legitimate Any: raw sqlite3 cursor rows are untyped

import contextlib
from pathlib import Path
import sqlite3

_DEFAULT_DB_PATH = "data/db/narada.db"


def _get_db_path() -> Path:
    """Extract SQLite file path from DATABASE_URL, falling back to default.

    Parses SQLAlchemy-style URLs (sqlite+aiosqlite:///path) into bare file paths.
    Returns the default path if DATABASE_URL is unset or non-SQLite.
    """
    from src.config import get_database_url

    db_url = get_database_url()
    if db_url and "sqlite" in db_url:
        # Strip scheme: "sqlite+aiosqlite:///data/db/narada.db" → "data/db/narada.db"
        _, _, path = db_url.partition("///")
        if path:
            return Path(path)
    return Path(_DEFAULT_DB_PATH)


def complete_workflow_id(incomplete: str) -> list[tuple[str, str]]:
    """Complete workflow IDs and slugs from the local database."""
    completions: list[tuple[str, str]] = []
    with contextlib.suppress(Exception):
        conn = sqlite3.connect(_get_db_path())
        try:
            rows = conn.execute(
                "SELECT id, name, json_extract(definition, '$.id') FROM workflows"
            ).fetchall()
        finally:
            conn.close()

        for row_id, name, slug in rows:
            # Add numeric ID completion
            id_str = str(row_id)
            if id_str.startswith(incomplete):
                completions.append((id_str, name))

            # Add slug completion from definition JSON
            if slug and slug.startswith(incomplete):
                completions.append((slug, name))

    return completions
