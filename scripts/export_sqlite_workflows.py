"""Export user-created workflows from an old SQLite database to JSON files.

One-time migration script for v0.5.3 containerization. Produces JSON files
compatible with `narada workflow create --file <path>`.

Usage:
    python scripts/export_sqlite_workflows.py [path/to/narada.db] [output_dir]

Templates are excluded — they auto-seed on startup.
"""

import json
from pathlib import Path
import sqlite3
import sys


def main() -> None:
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/db/narada.db")
    output_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path()

    if not db_path.exists():
        print(f"Database not found: {db_path}")
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT name, definition FROM workflows WHERE is_template = 0",
    ).fetchall()
    conn.close()

    if not rows:
        print("No user-created workflows found.")
        return

    for name, definition_json in rows:
        definition = json.loads(definition_json)
        slug = definition.get("id", name)
        out_file = output_dir / f"{slug}.json"
        out_file.write_text(json.dumps(definition, indent=2) + "\n")
        print(f"Exported: {out_file}")

    print(f"\nDone — {len(rows)} workflow(s) exported.")
    print("Import on new instance: narada workflow create --file <path>")


if __name__ == "__main__":
    main()
