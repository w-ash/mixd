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

from scripts.check_test_vacuity import iter_tests


def test_ids(source: str) -> set[str]:
    try:
        return {node_id for node_id, _, _ in iter_tests(ast.parse(source))}
    except SyntaxError:
        return set()


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
# Every conftest.py; and the package markers of new test directories, which
# need them to import at all.
while IFS= read -r f; do
  case "$f" in
    */conftest.py) copy "$f" ;;
    *) [ -e "$worktree/$f" ] || copy "$f" ;;
  esac
done < <(find tests \( -name conftest.py -o -name __init__.py \) -not -path '*/__pycache__/*')

echo "Running $(echo "$new_ids" | wc -l | tr -d ' ') new test(s) against $base ($merge_base)..."
# The editable install puts this checkout on sys.path. Drop it, so that src/ and the
# scripts/ namespace package resolve only from the worktree. `-o addopts=` drops
# xdist and the default marker filter. pytest gets the files, not the node ids: an
# id that cannot resolve (its module fails to import) stops the whole run, but a
# file that fails to import only fails its own tests. The plugin then keeps the
# new tests, with every parametrized case.
run_pytest='
import os, sys
repo = os.path.realpath(sys.argv.pop(1))
sys.path[:] = [p for p in sys.path if os.path.realpath(p or ".") != repo]
with open(sys.argv.pop(1), encoding="utf-8") as handle:
    wanted = set(handle.read().split())
import pytest

class OnlyNew:
    def pytest_collection_modifyitems(self, items):
        items[:] = [i for i in items if i.nodeid.partition("[")[0] in wanted]

sys.exit(pytest.main(sys.argv[1:], plugins=[OnlyNew()]))
'
echo "$new_ids" >"$tmp/ids"
# shellcheck disable=SC2086  # word-splitting the file list is intended
(
  cd "$worktree"
  UV_PROJECT_ENVIRONMENT="$repo/.venv" uv run --no-sync --quiet \
    python -c "$run_pytest" "$repo" "$tmp/ids" $changed \
    -p no:randomly -p no:cacheprovider -q -rp -o addopts= \
    --continue-on-collection-errors
) >"$tmp/pytest.log" 2>&1 || true
echo "  $(tail -n 1 "$tmp/pytest.log")"
passed=$(grep -E '^PASSED ' "$tmp/pytest.log" | sed 's/^PASSED //' || true)
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
