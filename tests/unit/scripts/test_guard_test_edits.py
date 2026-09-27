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


@pytest.mark.parametrize(
    ("branch", "asks"),
    [("test-audit/x", False), ("main", True)],
    ids=["audit-branch-silent", "main-asks"],
)
def test_audit_branch_silences_the_guard(
    project: Path, branch: str, *, asks: bool
) -> None:
    """Auditor worktrees on test-audit/* delete tests under verifier review."""
    _git(project, "init", "-b", branch)
    _git(project, "add", ".")
    _git(
        project,
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

    result = _run(project, _edit(project, "    assert total([]) == 0\n", ""))

    assert result.returncode == 0
    assert (result.stdout != "") is asks
    if asks:
        assert _decision(result)["permissionDecision"] == "ask"
