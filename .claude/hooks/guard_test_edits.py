#!/usr/bin/env python3
"""Ask for confirmation when an edit weakens a test file.

Claude Code PreToolUse hook for Edit, Write, and MultiEdit. It reads the hook input
JSON from stdin and computes the file text before and after the edit. It counts
these signals in both versions:

- assertions: every check. Python uses the AST vocabulary of
  ``scripts/check_test_vacuity.py``; TypeScript counts ``expect(``.
- strong checks: checks that pin a value. Python counts real checks and checks of
  mock arguments. TypeScript counts ``expect(`` without a weak matcher.
- weak checks: checks that pass for almost any value. Python counts constant-true
  asserts and ``pytest.raises(Exception)``; TypeScript counts ``toBeDefined``,
  ``toBeTruthy``, ``toBeFalsy``, ``toBeUndefined``, and ``not.toBeNull``.
- raises with match: ``pytest.raises(..., match=...)`` calls.
- tests and skip markers.

Regexes count Python when a version does not parse. When a count decreases, or weak
checks or skip markers increase, the hook prints an "ask" decision. Otherwise it
prints nothing. Any error exits 0 silently, so the hook never blocks a tool call.

The test-path patterns apply to the path relative to the git toplevel of the file,
so worktrees and sessions started in a subdirectory are guarded. Without git, the
path is relative to ``CLAUDE_PROJECT_DIR``. The hook stays silent on ``test-audit/*``
branches, where verifier agents prove each deletion and the user reviews each wave
PR. It runs git only when an edit would ask.

The hook needs the project venv (Python 3.14). The command in
``.claude/settings.json`` runs it only for test paths and exits 0 when the venv or
``uv`` is not available.

See ``.claude/rules/test-value.md``, section "Existing tests".
"""

import ast
from collections.abc import Mapping
from dataclasses import astuple, dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import cast

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AUDIT_BRANCH_PREFIX = "test-audit/"
_CANDIDATE_PATH = re.compile(
    r"(?:^|/)(?:tests/.+\.py|web/src/.+\.test\.tsx?|web/e2e/.+\.ts)$"
)
_PY_TEST_PATH = re.compile(r"^tests/.+\.py$")
_TS_TEST_PATH = re.compile(r"^web/src/.+\.test\.tsx?$|^web/e2e/.+\.ts$")

_PY_ASSERTIONS = (
    re.compile(r"^\s*assert\b", re.MULTILINE),
    re.compile(r"\.assert_\w*\("),
    re.compile(r"\bpytest\.raises\b"),
)
_PY_TESTS = re.compile(r"^\s*(?:async\s+)?def test_", re.MULTILINE)
_TS_ASSERTIONS = re.compile(r"\bexpect\(")
_TS_WEAK = re.compile(
    r"\.(?:toBeDefined|toBeTruthy|toBeFalsy|toBeUndefined|not\.toBeNull)\("
)
_TS_TESTS = re.compile(r"(?<![\w.])(?:it|test)\s*\(")
_SKIPS = (
    re.compile(r"\bpytest\.mark\.skip"),
    re.compile(r"\bpytest\.mark\.xfail\b"),
    re.compile(r"\.skip\("),
    re.compile(r"\.only\("),
    re.compile(r"\.todo\("),
)
_BROAD_ERRORS = frozenset({"Exception", "BaseException"})
_GIT_REV_PARSE = ("rev-parse", "--show-toplevel", "--abbrev-ref", "HEAD")


@dataclass(frozen=True, slots=True)
class Counts:
    """Signal counts for one version of a test file."""

    assertions: int
    strong: int
    weak: int
    matched_raises: int
    tests: int
    skips: int


_LABELS = (
    "assertions",
    "strong checks",
    "weak checks",
    "raises with match",
    "tests",
    "skip markers",
)
_INCREASE_WEAKENS = frozenset({"weak checks", "skip markers"})


def _count(patterns: tuple[re.Pattern[str], ...], text: str) -> int:
    return sum(len(p.findall(text)) for p in patterns)


def _regex_counts(text: str, *, python: bool) -> Counts:
    skips = _count(_SKIPS, text)
    if python:
        assertions = _count(_PY_ASSERTIONS, text)
        return Counts(assertions, assertions, 0, 0, len(_PY_TESTS.findall(text)), skips)
    assertions = len(_TS_ASSERTIONS.findall(text))
    weak = len(_TS_WEAK.findall(text))
    return Counts(
        assertions, assertions - weak, weak, 0, len(_TS_TESTS.findall(text)), skips
    )


def _is_raises(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "raises"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "pytest"
    )


def _is_weak(node: ast.AST) -> bool:
    """Return True for a check the vacuity checker calls real that pins nothing."""
    if isinstance(node, ast.Assert):
        return isinstance(node.test, ast.Constant) and bool(node.test.value)
    if not _is_raises(node):
        return False
    call = cast("ast.Call", node)
    has_match = any(kw.arg == "match" for kw in call.keywords)
    first = call.args[0] if call.args else None
    return not has_match and isinstance(first, ast.Name) and first.id in _BROAD_ERRORS


def _ast_counts(text: str) -> Counts:
    """Count checks and tests as the vacuity checker does. Raise SyntaxError."""
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    from scripts.check_test_vacuity import (
        Check,
        _asserting_helpers,
        collect_checks,
        iter_tests,
    )

    tree = ast.parse(text)
    checks = collect_checks(tree.body, frozenset(_asserting_helpers(tree)))
    nodes = list(ast.walk(tree))
    weak = sum(map(_is_weak, nodes))
    real = sum(c in {Check.REAL, Check.MOCK_ARGS} for c in checks)
    matched = sum(
        _is_raises(n) and any(kw.arg == "match" for kw in cast("ast.Call", n).keywords)
        for n in nodes
    )
    return Counts(
        assertions=len(checks),
        strong=real - weak,
        weak=weak,
        matched_raises=matched,
        tests=len(iter_tests(tree)),
        skips=_count(_SKIPS, text),
    )


def count_pair(before: str, after: str, *, python: bool) -> tuple[Counts, Counts]:
    """Count the signals in both versions of the file.

    Both versions use the same vocabulary: the AST when both parse, else regexes.
    """
    if python:
        try:
            return _ast_counts(before), _ast_counts(after)
        except SyntaxError:
            pass
    return _regex_counts(before, python=python), _regex_counts(after, python=python)


def weakened(before: Counts, after: Counts) -> list[str]:
    """Return ``label b→a`` for each signal that changed in the weakening direction."""
    changes: list[str] = []
    for label, b, a in zip(_LABELS, astuple(before), astuple(after), strict=True):
        worse = a > b if label in _INCREASE_WEAKENS else a < b
        if worse:
            changes.append(f"{label} {b}→{a}")
    return changes


def relative_path(file_path: str, project_dir: str | None) -> str:
    """Return the path relative to the project, in POSIX form."""
    path = Path(file_path)
    if project_dir and path.is_absolute():
        try:
            return path.resolve().relative_to(Path(project_dir).resolve()).as_posix()
        except ValueError:
            return path.as_posix()
    return path.as_posix()


def git_context(directory: Path) -> tuple[Path, str] | None:
    """Return the git toplevel and branch of the directory, or None without git.

    The branch is ``HEAD`` when detached or unborn.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(directory), *_GIT_REV_PARSE],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except OSError, subprocess.SubprocessError:
        return None
    lines = result.stdout.splitlines()
    if not lines:
        return None
    branch = lines[1] if len(lines) > 1 and result.returncode == 0 else "HEAD"
    return Path(lines[0]), branch


def _apply(text: str, edit: Mapping[str, object]) -> str:
    old = edit.get("old_string")
    new = edit.get("new_string")
    if not isinstance(old, str) or not isinstance(new, str):
        return text
    return (
        text.replace(old, new) if edit.get("replace_all") else text.replace(old, new, 1)
    )


def before_after(
    tool_name: str, tool_input: Mapping[str, object], current: str | None
) -> tuple[str, str] | None:
    """Return the file text before and after the edit, or None for a new file."""
    if current is None:
        return None
    if tool_name == "Write":
        content = tool_input.get("content")
        return (current, content) if isinstance(content, str) else None
    edits: list[Mapping[str, object]]
    if tool_name == "MultiEdit":
        raw = tool_input.get("edits")
        items = cast("list[object]", raw) if isinstance(raw, list) else []
        edits = [cast("Mapping[str, object]", e) for e in items if isinstance(e, dict)]
    else:
        edits = [tool_input]
    after = current
    for edit in edits:
        after = _apply(after, edit)
    return current, after


def decide(payload: Mapping[str, object], project_dir: str | None) -> str | None:
    """Return the reason to ask for confirmation, or None to stay silent."""
    tool_name = payload.get("tool_name")
    raw_input = payload.get("tool_input")
    if tool_name not in {"Edit", "Write", "MultiEdit"} or not isinstance(
        raw_input, dict
    ):
        return None
    tool_input = cast("Mapping[str, object]", raw_input)
    file_path = tool_input.get("file_path")
    if not isinstance(file_path, str) or not _CANDIDATE_PATH.search(file_path):
        return None
    base = project_dir or cast("str | None", payload.get("cwd"))
    path = Path(file_path)
    if not path.is_absolute():
        path = Path(base or ".") / path
    if not path.is_file():
        return None
    python = path.suffix == ".py"
    texts = before_after(tool_name, tool_input, path.read_text(encoding="utf-8"))
    if texts is None:
        return None
    changes = weakened(*count_pair(*texts, python=python))
    if not changes:
        return None
    context = git_context(path.parent)
    if context is None:
        rel = relative_path(str(path), base)
    else:
        toplevel, branch = context
        if branch.startswith(_AUDIT_BRANCH_PREFIX):
            return None
        rel = relative_path(str(path), str(toplevel))
    if not (_PY_TEST_PATH if python else _TS_TEST_PATH).match(rel):
        return None
    return (
        f"This edit weakens {rel}: {', '.join(changes)}. "
        "test-value.md 'Existing tests': never weaken, skip, delete, or loosen an "
        "assertion to get green. If the test is wrong, say so explicitly in the "
        "reply and the commit message."
    )


def main() -> int:
    """Read the hook input from stdin and print an ask decision when needed."""
    try:
        payload = cast("object", json.load(sys.stdin))
        if not isinstance(payload, dict):
            return 0
        reason = decide(
            cast("Mapping[str, object]", payload), os.environ.get("CLAUDE_PROJECT_DIR")
        )
    except Exception:
        # A guard failure must never block the edit it guards.
        return 0
    if reason:
        print(
            json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": reason,
                }
            })
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
