"""The PreToolUse hook that asks before an edit weakens a test file.

The hook runs as a subprocess with JSON on stdin, exactly as Claude Code runs it.
Each case pins whether the hook asks or stays silent: a missed "ask" lets a test
lose its assertions without review, and a spurious "ask" or a crash blocks every
honest edit.
"""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

HOOK = Path(__file__).resolve().parents[3] / ".claude" / "hooks" / "guard_test_edits.py"

ORIGINAL = """\
def test_total():
    assert total([1, 2]) == 3
    assert total([]) == 0
"""


def _run(project: Path, payload: object) -> subprocess.CompletedProcess[str]:
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=stdin,
        capture_output=True,
        text=True,
        env={"CLAUDE_PROJECT_DIR": str(project), "PATH": os.environ["PATH"]},
        check=False,
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    test_file = tmp_path / "tests" / "unit" / "test_math.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text(ORIGINAL)
    return tmp_path


def _edit(project: Path, old: str, new: str, path: str = "tests/unit/test_math.py"):
    return {
        "tool_name": "Edit",
        "tool_input": {
            "file_path": str(project / path),
            "old_string": old,
            "new_string": new,
        },
    }


def _decision(result: subprocess.CompletedProcess[str]) -> dict[str, str]:
    return json.loads(result.stdout)["hookSpecificOutput"]


def test_removing_an_assert_asks(project: Path) -> None:
    result = _run(project, _edit(project, "    assert total([]) == 0\n", ""))

    assert result.returncode == 0
    decision = _decision(result)
    assert decision["hookEventName"] == "PreToolUse"
    assert decision["permissionDecision"] == "ask"
    assert "assertions 2→1" in decision["permissionDecisionReason"]


def test_removing_a_warns_block_asks(project: Path) -> None:
    test_file = project / "tests" / "unit" / "test_math.py"
    test_file.write_text(
        "import pytest\n\n\ndef test_legacy_warns():\n"
        "    with pytest.warns(DeprecationWarning):\n        legacy()\n"
    )
    old = "    with pytest.warns(DeprecationWarning):\n        legacy()\n"

    result = _run(project, _edit(project, old, "    legacy()\n"))

    assert "assertions 1→0" in _decision(result)["permissionDecisionReason"]


def test_edit_that_breaks_syntax_still_counts_by_text(project: Path) -> None:
    """A half-finished edit does not parse; the regex fallback still sees the loss."""
    result = _run(
        project, _edit(project, "    assert total([]) == 0\n", "    total([\n")
    )

    assert "assertions 2→1" in _decision(result)["permissionDecisionReason"]


def test_adding_an_assert_is_silent(project: Path) -> None:
    extra = "    assert total([]) == 0\n    assert total([5]) == 5\n"
    result = _run(project, _edit(project, "    assert total([]) == 0\n", extra))

    assert (result.returncode, result.stdout) == (0, "")


def test_adding_a_skip_asks(project: Path) -> None:
    skipped = "import pytest\n\n@pytest.mark.skip\ndef test_total():"
    result = _run(project, _edit(project, "def test_total():", skipped))

    assert _decision(result)["permissionDecision"] == "ask"
    assert "skip markers 0→1" in _decision(result)["permissionDecisionReason"]


def test_write_that_drops_a_test_asks(project: Path) -> None:
    payload = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": str(project / "tests/unit/test_math.py"),
            "content": "import math\n",
        },
    }

    reason = _decision(_run(project, payload))["permissionDecisionReason"]

    assert "tests 1→0" in reason


def test_multi_edit_applies_edits_in_order(project: Path) -> None:
    """The second edit only matches the first edit's output; both must apply."""
    payload = {
        "tool_name": "MultiEdit",
        "tool_input": {
            "file_path": str(project / "tests/unit/test_math.py"),
            "edits": [
                {"old_string": "total([]) == 0", "new_string": "PLACEHOLDER"},
                {"old_string": "    assert PLACEHOLDER\n", "new_string": ""},
            ],
        },
    }

    assert (
        "assertions 2→1"
        in _decision(_run(project, payload))["permissionDecisionReason"]
    )


def test_non_test_path_is_silent(project: Path) -> None:
    source = project / "src" / "math.py"
    source.parent.mkdir()
    source.write_text("assert True\n")

    result = _run(project, _edit(project, "assert True\n", "", path="src/math.py"))

    assert (result.returncode, result.stdout) == (0, "")


def test_new_test_file_is_silent(project: Path) -> None:
    payload = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": str(project / "tests/unit/test_new.py"),
            "content": "",
        },
    }

    result = _run(project, payload)

    assert (result.returncode, result.stdout) == (0, "")


@pytest.mark.parametrize(
    "payload",
    ["not json", "[]", {"tool_name": "Edit", "tool_input": "oops"}],
    ids=["garbage", "wrong-shape", "bad-tool-input"],
)
def test_malformed_input_exits_zero_silently(project: Path, payload: object) -> None:
    result = _run(project, payload)

    assert (result.returncode, result.stdout) == (0, "")


def _git(project: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True)


def _init_repo(repo: Path, branch: str) -> None:
    _git(repo, "init", "-b", branch)
    _git(repo, "add", ".")
    _git(
        repo,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        "init",
    )


@pytest.mark.parametrize(
    ("branch", "asks"),
    [("test-audit/x", False), ("main", True)],
    ids=["audit-branch-silent", "main-asks"],
)
def test_audit_branch_silences_the_guard(
    project: Path, branch: str, *, asks: bool
) -> None:
    """Auditor worktrees on test-audit/* delete tests under verifier review."""
    _init_repo(project, branch)

    result = _run(project, _edit(project, "    assert total([]) == 0\n", ""))

    assert result.returncode == 0
    assert (result.stdout != "") is asks
    if asks:
        assert _decision(result)["permissionDecision"] == "ask"


def test_worktree_under_the_project_is_guarded(project: Path) -> None:
    """A worktree's tests/ sits below .claude/worktrees/, not at the project root."""
    worktree = project / ".claude" / "worktrees" / "x"
    (worktree / "tests").mkdir(parents=True)
    (worktree / "tests" / "test_a.py").write_text(ORIGINAL)
    _init_repo(worktree, "main")
    payload = _edit(
        project,
        "    assert total([]) == 0\n",
        "",
        path=".claude/worktrees/x/tests/test_a.py",
    )

    reason = _decision(_run(project, payload))["permissionDecisionReason"]

    assert reason.startswith("This edit weakens tests/test_a.py: assertions 2→1")


def test_session_started_in_a_subdirectory_is_guarded(tmp_path: Path) -> None:
    """A session started in web/ has CLAUDE_PROJECT_DIR=web; the repo root decides."""
    spec = tmp_path / "web" / "src" / "total.test.ts"
    spec.parent.mkdir(parents=True)
    spec.write_text(TS_ORIGINAL)
    _init_repo(tmp_path, "main")
    payload = _edit(
        tmp_path, "  expect(total([])).toBe(0);\n", "", path="web/src/total.test.ts"
    )

    reason = _decision(_run(tmp_path / "web", payload))["permissionDecisionReason"]

    assert reason.startswith("This edit weakens web/src/total.test.ts: assertions 2→1")


def test_tests_dir_below_the_repo_root_is_silent(tmp_path: Path) -> None:
    """Only tests/ at the git toplevel holds project tests."""
    vendored = tmp_path / "src" / "pkg" / "tests" / "test_math.py"
    vendored.parent.mkdir(parents=True)
    vendored.write_text(ORIGINAL)
    _init_repo(tmp_path, "main")
    payload = _edit(
        tmp_path,
        "    assert total([]) == 0\n",
        "",
        path="src/pkg/tests/test_math.py",
    )

    result = _run(tmp_path, payload)

    assert (result.returncode, result.stdout) == (0, "")


TS_ORIGINAL = """\
import { expect, it } from "vitest";

it("totals", () => {
  expect(total([1, 2])).toBe(3);
  expect(total([])).toBe(0);
});
"""
RAISES_MATCH = """\
import pytest


def test_total_rejects_none():
    with pytest.raises(TypeError, match="not iterable"):
        total(None)
"""
RAISES_BARE = RAISES_MATCH.replace(', match="not iterable"', "")
EXISTENCE = ORIGINAL.replace("total([]) == 0", "total([]) is not None")
PY_PATH = "tests/unit/test_math.py"
TS_PATH = "web/src/total.test.ts"


def _edit_file(
    tmp_path: Path, path: str, source: str, old: str, new: str
) -> subprocess.CompletedProcess[str]:
    target = tmp_path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source)
    return _run(tmp_path, _edit(tmp_path, old, new, path=path))


@pytest.mark.parametrize(
    ("path", "source", "old", "new", "change"),
    [
        (PY_PATH, ORIGINAL, "== 0", "is not None", "strong checks 2→1"),
        (PY_PATH, ORIGINAL, "total([]) == 0", "True", "strong checks 2→1"),
        (
            PY_PATH,
            RAISES_MATCH,
            ', match="not iterable"',
            "",
            "raises with match 1→0",
        ),
        (PY_PATH, RAISES_BARE, "TypeError", "Exception", "strong checks 1→0"),
        (TS_PATH, TS_ORIGINAL, "toBe(0)", "toBeDefined()", "strong checks 2→1"),
        (TS_PATH, TS_ORIGINAL, "toBe(0)", "toBeTruthy()", "strong checks 2→1"),
        (TS_PATH, TS_ORIGINAL, "toBe(0)", "not.toBeNull()", "strong checks 2→1"),
        (
            TS_PATH,
            TS_ORIGINAL,
            "  expect(total([])).toBe(0);\n",
            "  expect(total([])).toBe(0);\n  expect(total([5])).toBeTruthy();\n",
            "weak checks 0→1",
        ),
    ],
    ids=[
        "py-is-not-none",
        "py-assert-true",
        "py-raises-drops-match",
        "py-raises-broadens",
        "ts-to-be-defined",
        "ts-to-be-truthy",
        "ts-not-to-be-null",
        "ts-adds-weak-matcher",
    ],
)
def test_weakening_with_equal_assertion_count_asks(
    tmp_path: Path, path: str, source: str, old: str, new: str, change: str
) -> None:
    result = _edit_file(tmp_path, path, source, old, new)

    decision = _decision(result)
    assert decision["permissionDecision"] == "ask"
    assert change in decision["permissionDecisionReason"]
    assert "assertions" not in decision["permissionDecisionReason"]


@pytest.mark.parametrize(
    ("path", "source", "old", "new"),
    [
        (PY_PATH, EXISTENCE, "is not None", "== 0"),
        (PY_PATH, RAISES_BARE, "TypeError)", 'TypeError, match="not iterable")'),
        (
            TS_PATH,
            TS_ORIGINAL.replace("toBe(0)", "toBeDefined()"),
            "toBeDefined()",
            "toBe(0)",
        ),
    ],
    ids=["py-pins-value", "py-raises-adds-match", "ts-pins-value"],
)
def test_strengthening_is_silent(
    tmp_path: Path, path: str, source: str, old: str, new: str
) -> None:
    result = _edit_file(tmp_path, path, source, old, new)

    assert (result.returncode, result.stdout) == (0, "")


def test_removing_a_local_assert_helper_call_asks(tmp_path: Path) -> None:
    """A call to a local helper that asserts is a check; dropping it weakens."""
    source = """\
def _assert_total(values, expected):
    assert total(values) == expected


def test_total():
    _assert_total([1, 2], 3)
    _assert_total([], 0)
"""
    result = _edit_file(tmp_path, PY_PATH, source, "    _assert_total([], 0)\n", "")

    assert "assertions 3→2" in _decision(result)["permissionDecisionReason"]
