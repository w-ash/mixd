#!/usr/bin/env bash
# Red check: report new tests that already pass on the base branch.
#
# A new test that passes without this branch's source changes does not guard them.
# The script finds test files added or modified since the merge base (committed,
# uncommitted, and untracked), lists the test functions that are new, and runs them
# in a temporary worktree at the merge base. Only the changed test files, the
# tests/fixtures package, and every conftest.py are copied over, so src/ is the base
# version. Collection and import errors count as "fails on base", which is fine.
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

changed=$(
  {
    git diff --name-only --diff-filter=AM "$merge_base" -- tests/
    git ls-files --others --exclude-standard -- tests/
  } | grep -E '(^|/)test_[^/]*\.py$' | sort -u || true
)
if [ -z "$changed" ]; then
  echo "No added or modified test files vs $base ($merge_base)."
  exit 0
fi

# Node ids of test functions present now but absent from the merge-base version.
new_ids=$(
  # shellcheck disable=SC2086  # word-splitting the file list is intended
  uv run --no-sync --quiet python - "$merge_base" $changed <<'PY'
import ast
import subprocess
import sys


def test_ids(source: str) -> set[str]:
    found: set[str] = set()

    def visit(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                if node.name.startswith("test"):
                    found.add(prefix + node.name)
            elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                visit(node.body, f"{prefix}{node.name}::")

    try:
        visit(ast.parse(source).body, "")
    except SyntaxError:
        pass
    return found


merge_base, *files = sys.argv[1:]
for path in files:
    with open(path, encoding="utf-8") as handle:
        now = test_ids(handle.read())
    shown = subprocess.run(
        ["git", "show", f"{merge_base}:{path}"], capture_output=True, text=True
    )
    before = test_ids(shown.stdout) if shown.returncode == 0 else set()
    for test_id in sorted(now - before):
        print(f"{path}::{test_id}")
PY
)
if [ -z "$new_ids" ]; then
  echo "Changed test files have no new test functions vs $base."
  exit 0
fi

tmp=$(mktemp -d "${TMPDIR:-/tmp}/red-check.XXXXXX")
worktree="$tmp/base"
# shellcheck disable=SC2329  # invoked by the EXIT trap
cleanup() {
  git -C "$repo" worktree remove --force "$worktree" >/dev/null 2>&1 || true
  git -C "$repo" worktree prune >/dev/null 2>&1 || true
  rm -rf "$tmp"
}
trap cleanup EXIT

git worktree add --detach --quiet "$worktree" "$merge_base"

copy() { # repo-relative path
  mkdir -p "$worktree/$(dirname "$1")"
  cp "$1" "$worktree/$1"
}
for f in $changed; do copy "$f"; done
rm -rf "$worktree/tests/fixtures"
cp -R tests/fixtures "$worktree/tests/fixtures"
while IFS= read -r f; do copy "$f"; done < <(find tests -name conftest.py -not -path '*/__pycache__/*')
# New test directories need their package markers to import at all.
while IFS= read -r f; do
  [ -e "$worktree/$f" ] || copy "$f"
done < <(find tests -name __init__.py -not -path '*/__pycache__/*')

echo "Running $(echo "$new_ids" | wc -l | tr -d ' ') new test(s) against $base ($merge_base)..."
# The editable install puts this checkout on sys.path. Drop it, so that src/ and the
# scripts/ namespace package resolve only from the worktree. `-o addopts=` drops
# xdist and the default marker filter.
run_pytest='
import os, sys
repo = os.path.realpath(sys.argv.pop(1))
sys.path[:] = [p for p in sys.path if os.path.realpath(p or ".") != repo]
import pytest
sys.exit(pytest.main(sys.argv[1:]))
'
# One pytest run per file: an unresolvable id or import error stops the whole run,
# and must not hide the other files' results.
for f in $changed; do
  file_ids=$(grep -F "$f::" <<<"$new_ids" || true)
  [ -n "$file_ids" ] || continue
  # shellcheck disable=SC2086  # word-splitting the id list is intended
  (
    cd "$worktree"
    UV_PROJECT_ENVIRONMENT="$repo/.venv" uv run --no-sync --quiet \
      python -c "$run_pytest" "$repo" $file_ids \
      -p no:randomly -p no:cacheprovider -q -rp -o addopts=
  ) >"$tmp/pytest.log" 2>&1 || true
  echo "  $f: $(tail -n 1 "$tmp/pytest.log")"
  grep -E '^PASSED ' "$tmp/pytest.log" | sed 's/^PASSED //' >>"$tmp/passed" || true
done
passed=$(cat "$tmp/passed" 2>/dev/null || true)
echo
if [ -n "$passed" ]; then
  echo "New tests that PASS on base (they do not guard this change):"
  awk '{ print "  " $0 }' <<<"$passed"
  echo
  echo "Confirm each one fails without the change, or say why it need not (test-value.md)."
else
  echo "Every new test fails on base."
fi
exit 0
