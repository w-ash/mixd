#!/usr/bin/env python3
"""Backlog hygiene gate — part of the version-bump bar.

Usage:
    uv run python scripts/check_backlog.py

Enforces the lifecycle conventions in ``.claude/rules/backlog-format.md``:

- every relative markdown link under ``docs/backlog/`` resolves
  (errors in active files; warnings inside ``completed/`` — archives are
  frozen history and their internal links may predate later moves)
- ``completed/README.md`` indexes every file that exists in ``completed/``
- every version file in the backlog root appears in the README
  (matrix or narrative), so no milestone is invisible on the roadmap
- root version files whose matrix rows are all ``✅ Completed`` are
  flagged as ready to archive
- one-off records (handoffs, findings, migration notes) in the backlog
  root carry a ``Status:`` header
- no references to retired conventions (``docs/user-flows.md``,
  ``US-AREA-``) outside explicit "retired" annotations
- ``CHANGELOG.md`` has an entry for the current ``pyproject.toml`` version

Exit code 1 on errors; warnings are printed but do not fail the gate.
"""

from pathlib import Path
import re
import sys
import tomllib

REPO = Path(__file__).resolve().parent.parent
BACKLOG = REPO / "docs" / "backlog"
COMPLETED = BACKLOG / "completed"
CHANGELOG = REPO / "CHANGELOG.md"

MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
MATRIX_ROW = re.compile(
    r"^\|\s*\*\*(v[\w.]+)\*\*\s*\|.*\|\s*([^|]*?)\s*\|\s*\[details\]\(([^)#]+)"
)
# Milestone files are named with digits/dots/x/hyphens only (v0.7.0-1.md,
# v0.9.x.md); anything else v-prefixed (v0.8.8-handoff.md) is a record.
VERSION_FILE = re.compile(r"^v[\d.x-]+\.md$")
RETIRED_REFS = ("docs/user-flows.md", "US-AREA-")

errors: list[str] = []
warnings: list[str] = []


def check_links() -> None:
    for md in sorted(BACKLOG.rglob("*.md")):
        in_archive = COMPLETED in md.parents
        for lineno, line in enumerate(md.read_text().splitlines(), 1):
            for target in MD_LINK.findall(line):
                if target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                path = (md.parent / target.split("#")[0]).resolve()
                if not path.exists():
                    msg = f"{md.relative_to(REPO)}:{lineno} broken link → {target}"
                    (warnings if in_archive else errors).append(msg)


def check_completed_index() -> None:
    index = COMPLETED / "README.md"
    if not index.exists():
        errors.append(f"{index.relative_to(REPO)} missing")
        return
    indexed = index.read_text()
    errors.extend(
        f"completed/{md.name} exists but is absent from completed/README.md"
        for md in sorted(COMPLETED.glob("*.md"))
        if md.name != "README.md" and md.name not in indexed
    )


def check_matrix() -> None:
    readme = (BACKLOG / "README.md").read_text()
    statuses: dict[str, list[str]] = {}
    for line in readme.splitlines():
        if m := MATRIX_ROW.match(line):
            statuses.setdefault(m.group(3), []).append(m.group(2))

    for md in sorted(BACKLOG.glob("v*.md")):
        if not VERSION_FILE.match(md.name):
            continue
        if md.name not in readme:
            errors.append(f"{md.name} is in the backlog root but absent from README.md")
        rows = statuses.get(md.name, [])
        if rows and all("✅" in s for s in rows):
            warnings.append(
                f"{md.name}: all matrix rows ✅ Completed — ready to archive"
            )


def check_records() -> None:
    for md in sorted(BACKLOG.glob("*.md")):
        if md.name in ("README.md", "unscheduled.md") or VERSION_FILE.match(md.name):
            continue
        head = "\n".join(md.read_text().splitlines()[:15])
        if not re.search(r"\*\*Status\*\*:|Status:", head):
            warnings.append(f"{md.name}: one-off record missing a Status: header")


def check_retired_refs() -> None:
    for md in sorted(BACKLOG.rglob("*.md")):
        sink = warnings if COMPLETED in md.parents else errors
        for lineno, line in enumerate(md.read_text().splitlines(), 1):
            if "retired" in line.lower():
                continue
            sink.extend(
                f"{md.relative_to(REPO)}:{lineno} references retired convention {ref!r}"
                for ref in RETIRED_REFS
                if ref in line
            )


def check_changelog() -> None:
    with (REPO / "pyproject.toml").open("rb") as f:
        version = tomllib.load(f)["project"]["version"]
    if not CHANGELOG.exists():
        errors.append("CHANGELOG.md missing")
    elif f"[{version}]" not in CHANGELOG.read_text():
        errors.append(f"CHANGELOG.md has no entry for current version {version}")


def main() -> int:
    for check in (
        check_links,
        check_completed_index,
        check_matrix,
        check_records,
        check_retired_refs,
        check_changelog,
    ):
        check()

    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")
    print(f"\ncheck_backlog: {len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
