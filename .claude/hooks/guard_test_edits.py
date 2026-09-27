#!/usr/bin/env python3
"""Ask for confirmation when an edit weakens a test file.

Claude Code PreToolUse hook for Edit, Write, and MultiEdit. It reads the hook input
JSON from stdin and computes the file text before and after the edit. It counts
assertions, test functions, and skip markers. Python assertions and tests are
counted with the AST vocabulary of ``scripts/check_test_vacuity.py``; regexes count
them when a version does not parse, and count TypeScript and skip markers. When
assertions or tests decrease, or skip markers increase, it prints an "ask" decision.
Otherwise it prints nothing.
Any error exits 0 silently, so the hook never blocks a tool call.
The hook stays silent on ``test-audit/*`` branches, where verifier agents prove each
deletion and the user reviews each wave PR.

See ``.claude/rules/test-value.md``, section "Existing tests".
"""

import ast
from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import cast

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AUDIT_BRANCH_PREFIX = "test-audit/"
_PY_TEST_PATH = re.compile(r"^tests/.+\.py$")
_TS_TEST_PATH = re.compile(r"^web/src/.+\.test\.tsx?$|^web/e2e/.+\.ts$")

_PY_ASSERTIONS = (
    re.compile(r"^\s*assert\b", re.MULTILINE),
    re.compile(r"\.assert_\w*\("),
    re.compile(r"\bpytest\.raises\b"),
)
_PY_TESTS = re.compile(r"^\s*(?:async\s+)?def test_", re.MULTILINE)
_TS_ASSERTIONS = re.compile(r"\bexpect\(")
_TS_TESTS = re.compile(r"(?<![\w.])(?:it|test)\s*\(")
_SKIPS = (
    re.compile(r"\bpytest\.mark\.skip"),
    re.compile(r"\bpytest\.mark\.xfail\b"),
    re.compile(r"\.skip\("),
    re.compile(r"\.only\("),
    re.compile(r"\.todo\("),
)


@dataclass(frozen=True, slots=True)
class Counts:
    """Signal counts for one version of a test file."""

    assertions: int
    tests: int
    skips: int


def _count(patterns: tuple[re.Pattern[str], ...], text: str) -> int:
    return sum(len(p.findall(text)) for p in patterns)


def _regex_counts(text: str, *, python: bool) -> Counts:
    if python:
        assertions = _count(_PY_ASSERTIONS, text)
        tests = len(_PY_TESTS.findall(text))
    else:
        assertions = len(_TS_ASSERTIONS.findall(text))
        tests = len(_TS_TESTS.findall(text))
    return Counts(assertions, tests, _count(_SKIPS, text))


def _ast_counts(text: str) -> Counts:
    """Count checks and tests as the vacuity checker does. Raise SyntaxError."""
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    from scripts.check_test_vacuity import collect_checks, iter_tests

    tree = ast.parse(text)
    return Counts(
        assertions=len(collect_checks(tree.body, frozenset())),
        tests=len(iter_tests(tree)),
        skips=_count(_SKIPS, text),
    )


def count_pair(before: str, after: str, *, python: bool) -> tuple[Counts, Counts]:
    """Count assertions, tests, and skip markers in both versions of the file.

    Both versions use the same vocabulary: the AST when both parse, else regexes.
    """
    if python:
        try:
            return _ast_counts(before), _ast_counts(after)
        except SyntaxError:
            pass
    return _regex_counts(before, python=python), _regex_counts(after, python=python)


def relative_path(file_path: str, project_dir: str | None) -> str:
    """Return the path relative to the project, in POSIX form."""
    path = Path(file_path)
    if project_dir and path.is_absolute():
        try:
            return path.resolve().relative_to(Path(project_dir).resolve()).as_posix()
        except ValueError:
            return path.as_posix()
    return path.as_posix()


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


def on_audit_branch(directory: Path) -> bool:
    """Return True when the git branch at the directory starts with test-audit/."""
    try:
        result = subprocess.run(
            ["git", "-C", str(directory), "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
    except OSError, subprocess.SubprocessError:
        return False
    return result.stdout.strip().startswith(_AUDIT_BRANCH_PREFIX)


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
    if not isinstance(file_path, str):
        return None
    base = project_dir or cast("str | None", payload.get("cwd"))
    rel = relative_path(file_path, base)
    python = bool(_PY_TEST_PATH.match(rel))
    if not python and not _TS_TEST_PATH.match(rel):
        return None
    path = Path(file_path) if Path(file_path).is_absolute() else Path(base or ".") / rel
    current = path.read_text(encoding="utf-8") if path.is_file() else None
    texts = before_after(tool_name, tool_input, current)
    if texts is None:
        return None
    before, after = count_pair(*texts, python=python)
    if (
        after.assertions >= before.assertions
        and after.tests >= before.tests
        and after.skips <= before.skips
    ):
        return None
    if on_audit_branch(path.parent):
        return None
    return (
        f"This edit weakens {rel}: "
        f"assertions {before.assertions}→{after.assertions}, "
        f"tests {before.tests}→{after.tests}, "
        f"skip markers {before.skips}→{after.skips}. "
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
