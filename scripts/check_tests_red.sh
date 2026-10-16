#!/usr/bin/env bash
# Red check: report new tests that already pass on the base branch.
#
# A new test that passes without this branch's source changes does not guard them.
# The script finds files under tests/ that changed since the merge base (committed,
# uncommitted, and untracked; renames and copies included), lists the test
# functions that are new, and runs them in a temporary worktree at the merge base.
# Only the changed files under tests/ are copied over (test modules, conftest.py,
# fixtures and other helpers), so src/ is the base version. For a renamed test
# file, only the tests that the old path did not have count as new.
#
# Each new test lands in one bucket:
#   PASSED on base        - it does not guard the change; the report lists it
#   FAILED on base        - good
#   inconclusive          - error in setup, skipped, or not collected (for example
#                           a helper imports a name that base src/ lacks); the
#                           report names the error
#
# Report-only; always exits 0. The worktree reuses this checkout's .venv.
#
# Usage: scripts/check_tests_red.sh [base_ref]   (default origin/main, else main)
set -euo pipefail
cd "$(dirname "$0")/.."
repo=$(pwd)

base=${1:-}
if [ -z "$base" ]; then
  if git rev-parse --verify --quiet origin/main >/dev/null; then base=origin/main; else base=main; fi
fi
merge_base=$(git merge-base "$base" HEAD)

tmp=$(mktemp -d "${TMPDIR:-/tmp}/red-check.XXXXXX")
worktree="$tmp/base"
# shellcheck disable=SC2329  # invoked by the EXIT trap
cleanup() {
  git -C "$repo" worktree remove --force "$worktree" >/dev/null 2>&1 || true
  git -C "$repo" worktree prune >/dev/null 2>&1 || true
  rm -rf "$tmp"
}
trap cleanup EXIT

# Changed files under tests/, one "new_path<TAB>base_path" line each. The base path
# differs from the new path for a rename or copy. Deletions are left out.
{
  git diff --name-status -M -C --diff-filter=d "$merge_base" -- tests/ |
    awk -F'\t' '{ if (NF == 3) print $3 "\t" $2; else print $2 "\t" $2 }'
  git ls-files --others --exclude-standard -- tests/ | awk '{ print $0 "\t" $0 }'
} | grep -v '/__pycache__/' | sort -u >"$tmp/changed"

test_re='(^|/)test_[^/]*\.py'$'\t'
grep -E "$test_re" "$tmp/changed" >"$tmp/test_files" || true
if [ ! -s "$tmp/test_files" ]; then
  echo "No added, modified, or renamed test files vs $base ($merge_base)."
  exit 0
fi

# Node ids of test functions present now but absent from the base version of the
# file (the old path, for a rename or copy).
uv run --no-sync --quiet python - "$merge_base" "$tmp/test_files" >"$tmp/ids" <<'PY'
import ast
import subprocess
import sys

from scripts.check_test_vacuity import iter_tests


def test_ids(source: str) -> set[str]:
    try:
        return {item[0] for item in iter_tests(ast.parse(source))}
    except SyntaxError:
        return set()


merge_base, listing = sys.argv[1:]
with open(listing, encoding="utf-8") as handle:
    pairs = [line.rstrip("\n").split("\t") for line in handle if line.strip()]
for path, base_path in pairs:
    with open(path, encoding="utf-8") as handle:
        now = test_ids(handle.read())
    shown = subprocess.run(
        ["git", "show", f"{merge_base}:{base_path}"], capture_output=True, text=True
    )
    before = test_ids(shown.stdout) if shown.returncode == 0 else set()
    for test_id in sorted(now - before):
        print(f"{path}::{test_id}")
PY
if [ ! -s "$tmp/ids" ]; then
  echo "Changed test files have no new test functions vs $base."
  exit 0
fi

git worktree add --detach --quiet "$worktree" "$merge_base"

# Copy every changed file under tests/ at its new path.
while IFS=$'\t' read -r f _; do
  mkdir -p "$worktree/$(dirname "$f")"
  cp "$f" "$worktree/$f"
done <"$tmp/changed"
# pytest gets only the files that hold new tests.
sed 's/::.*//' "$tmp/ids" | sort -u >"$tmp/run_files"

echo "Running $(wc -l <"$tmp/ids" | tr -d ' ') new test(s) against $base ($merge_base)..."
# The editable install puts this checkout on sys.path. Drop it, so that src/ and the
# scripts/ namespace package resolve only from the worktree. `-o addopts=` drops
# xdist and the default marker filter. pytest gets the files, not the node ids: an
# id that cannot resolve (its module fails to import) stops the whole run, but a
# file that fails to import only fails its own collection. The plugin keeps the
# new tests, with every parametrized case, and records each outcome and each
# collection error to a JSON file.
run_pytest='
import json, os, sys
repo = os.path.realpath(sys.argv.pop(1))
sys.path[:] = [p for p in sys.path if os.path.realpath(p or ".") != repo]
with open(sys.argv.pop(1), encoding="utf-8") as handle:
    wanted = set(handle.read().split())
out = sys.argv.pop(1)
import pytest

class OnlyNew:
    def __init__(self):
        self.collected = False
        self.outcomes = {}
        self.collect_errors = {}

    def pytest_collectreport(self, report):
        if report.failed:
            self.collect_errors[report.nodeid] = str(report.longrepr)

    def pytest_collection_modifyitems(self, items):
        items[:] = [i for i in items if i.nodeid.partition("[")[0] in wanted]

    def pytest_collection_finish(self, session):
        self.collected = True

    def pytest_runtest_logreport(self, report):
        if report.when == "setup" and report.failed:
            self.outcomes[report.nodeid] = ["ERROR", str(report.longrepr)]
        elif report.skipped and report.when in ("setup", "call"):
            self.outcomes[report.nodeid] = ["SKIPPED", str(report.longrepr)]
        elif report.when == "call":
            self.outcomes[report.nodeid] = [report.outcome.upper(), ""]

plugin = OnlyNew()
code = pytest.main(sys.argv[1:], plugins=[plugin])
with open(out, "w", encoding="utf-8") as handle:
    json.dump(vars(plugin), handle)
sys.exit(code)
'
run_files=()
while IFS= read -r f; do run_files+=("$f"); done <"$tmp/run_files"
(
  cd "$worktree"
  UV_PROJECT_ENVIRONMENT="$repo/.venv" uv run --no-sync --quiet \
    python -c "$run_pytest" "$repo" "$tmp/ids" "$tmp/results.json" "${run_files[@]}" \
    -p no:randomly -p no:cacheprovider -q -o addopts= --continue-on-collection-errors
) >"$tmp/pytest.log" 2>&1 || true
echo "  $(tail -n 1 "$tmp/pytest.log")"
echo

uv run --no-sync --quiet python - "$tmp" <<'PY'
import json
import os
import re
import sys

tmp = sys.argv[1]
with open(os.path.join(tmp, "ids"), encoding="utf-8") as handle:
    wanted = handle.read().split()
with open(os.path.join(tmp, "pytest.log"), encoding="utf-8", errors="replace") as handle:
    log = handle.read()
try:
    with open(os.path.join(tmp, "results.json"), encoding="utf-8") as handle:
        results = json.load(handle)
except (OSError, ValueError):
    results = {"collected": False, "outcomes": {}, "collect_errors": {}}


def headline(text: str) -> str:
    """Return the exception line of a pytest error report."""
    exception = re.compile(r"[\w.]+(Error|Exception|Exit)\b")
    errors = [line[1:].strip() for line in text.splitlines() if line.startswith("E ")]
    for line in errors:
        if exception.match(line):
            return line
    if errors:
        return errors[-1]
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    raised = [line for line in lines if exception.match(line)]
    return raised[-1] if raised else lines[0] if lines else "no detail"


passed: list[str] = []
failed: list[str] = []
inconclusive: list[str] = []
seen: set[str] = set()
for node_id, (outcome, detail) in sorted(results["outcomes"].items()):
    seen.add(node_id.partition("[")[0])
    if outcome == "PASSED":
        passed.append(node_id)
    elif outcome == "FAILED":
        failed.append(node_id)
    else:
        inconclusive.append(f"{node_id}: {outcome} on base - {headline(detail)}")

# Tests that never ran, grouped by file with the collection error that stopped them.
missing: dict[str, int] = {}
for node_id in wanted:
    if node_id not in seen:
        path = node_id.partition("::")[0]
        missing[path] = missing.get(path, 0) + 1
for path, count in sorted(missing.items()):
    if not results["collected"]:
        reason = f"pytest stopped before collection: {headline(log)}"
    else:
        reason = next(
            (
                headline(text)
                for node, text in results["collect_errors"].items()
                if node and (path == node or path.startswith(node.rstrip("/") + "/"))
            ),
            "not collected",
        )
    inconclusive.append(f"{path}: {count} test(s) not collected - {reason}")

if passed:
    print(f"PASSED on base ({len(passed)}) - these do not guard this change:")
    print("\n".join(f"  {node_id}" for node_id in passed))
    print("  Confirm each one fails without the change, or say why it need not (test-value.md).")
    print()
if inconclusive:
    print(f"Inconclusive on base ({len(inconclusive)}) - no verdict, check the error:")
    print("\n".join(f"  {line}" for line in inconclusive))
    print()
if failed:
    print(f"FAILED on base ({len(failed)}) - good.")
if failed and not passed and not inconclusive:
    print("Every new test fails on base.")
PY
exit 0
