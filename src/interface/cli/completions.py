"""Shell completion callbacks for CLI arguments.

Completion callbacks must be synchronous (Click limitation). Uses raw psycopg
for fast local reads without importing the async stack. Returns [] on any error
so completion never crashes the shell.
"""

import contextlib
from typing import cast


def _get_sync_db_url() -> str:
    """Get a sync-compatible database URL for completions.

    Returns empty string if URL cannot be resolved.
    """
    with contextlib.suppress(Exception):
        from src.config import get_sync_database_url

        return get_sync_database_url()
    return ""


def complete_workflow_id(incomplete: str) -> list[tuple[str, str]]:
    """Complete workflow IDs and slugs from the local database."""
    completions: list[tuple[str, str]] = []
    with contextlib.suppress(Exception):
        import psycopg

        db_url = _get_sync_db_url()
        if not db_url:
            return []

        conn = psycopg.connect(db_url)
        try:
            rows = conn.execute(
                "SELECT id, name, definition->>'id' FROM workflows"
            ).fetchall()
        finally:
            conn.close()

        for row in rows:
            typed_row = cast("tuple[int, str, str | None]", row)
            row_id = str(typed_row[0])
            name = typed_row[1]
            slug = typed_row[2] or ""
            # Add numeric ID completion
            if row_id.startswith(incomplete):
                completions.append((row_id, name))

            # Add slug completion from definition JSON
            if slug and slug.startswith(incomplete):
                completions.append((slug, name))

    return completions
