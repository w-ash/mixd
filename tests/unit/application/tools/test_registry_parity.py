"""The D4 bidirectional-parity contract, enforced in CI.

Every ``*UseCase`` class in ``src/application/use_cases`` must be classified in
exactly one bucket: covered by a ToolSpec, blacklisted (human-only),
mechanically excluded (no chat channel), internal plumbing, or not-yet-covered.
A new use case that lands in none of these buckets fails here — so "anything a
human can do, the agent can do" can't silently rot as features ship.

The test also guards the registry's own quality invariants (unique names,
when-to-call descriptions, valid input schemas, write-tool confirmation).
"""

import ast
from pathlib import Path
import re

from src.application.tools import registry
from src.application.tools.registry import (
    BLACKLISTED_USE_CASES,
    INTERNAL_USE_CASES,
    MECHANICALLY_EXCLUDED_USE_CASES,
    NOT_YET_COVERED,
    TOOLS,
)
import src.application.use_cases as use_cases_pkg

_WHEN_TO_CALL = re.compile(r"^(Call|Use|When)\b")


def _discover_use_case_classes() -> set[str]:
    """AST-scan the use_cases package for ``*UseCase`` class definitions.

    Uses ``ast`` rather than importing so discovery has no import side effects
    and matches exactly what a maintainer sees when grepping for the classes.
    """
    root = Path(use_cases_pkg.__file__).parent
    names: set[str] = set()
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name.endswith("UseCase"):
                names.add(node.name)
    return names


def _covered_use_cases() -> set[str]:
    return {uc for spec in TOOLS for uc in spec.use_cases}


# --- Parity contract -------------------------------------------------------


def test_every_use_case_is_classified_exactly_once() -> None:
    discovered = _discover_use_case_classes()
    covered = _covered_use_cases()
    buckets = {
        "covered": covered,
        "blacklisted": set(BLACKLISTED_USE_CASES),
        "mechanically_excluded": set(MECHANICALLY_EXCLUDED_USE_CASES),
        "internal": set(INTERNAL_USE_CASES),
        "not_yet_covered": set(NOT_YET_COVERED),
    }

    # Every discovered use case is classified.
    classified = set().union(*buckets.values())
    unclassified = discovered - classified
    assert not unclassified, f"Unclassified use cases (add to a bucket): {unclassified}"

    # No bucket references a use case that no longer exists.
    stale = classified - discovered
    assert not stale, f"Stale entries — no such use case: {stale}"

    # Buckets are pairwise disjoint: exactly one classification each.
    names = list(buckets)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            overlap = buckets[a] & buckets[b]
            assert not overlap, f"Use case in both {a} and {b}: {overlap}"


def test_not_yet_covered_is_empty() -> None:
    # v0.9.1 closed the parity contract: every use case is covered or explicitly
    # excluded. NOT_YET_COVERED must stay empty — a new use case that needs a
    # tool must grow one (or an exclusion), not sit in a backlog set.
    assert frozenset() == NOT_YET_COVERED, (
        f"Parity is closed — cover these or add them to an exclusion bucket: "
        f"{set(NOT_YET_COVERED)}"
    )


# --- Registry quality invariants -------------------------------------------


def test_tool_names_are_unique() -> None:
    names = [spec.name for spec in TOOLS]
    assert len(names) == len(set(names)), f"Duplicate tool names: {names}"


def test_descriptions_lead_with_a_when_to_call_sentence() -> None:
    for spec in TOOLS:
        assert len(spec.description) >= 50, f"{spec.name}: description too short"
        assert _WHEN_TO_CALL.match(spec.description), (
            f"{spec.name}: description must lead with a when-to-call sentence "
            f"(Call/Use/When…), got: {spec.description[:40]!r}"
        )


def test_input_schemas_are_valid_object_schemas() -> None:
    for spec in TOOLS:
        schema = spec.input_schema
        assert schema.get("type") == "object", f"{spec.name}: not an object schema"
        assert schema.get("additionalProperties") is False, (
            f"{spec.name}: input_schema must set additionalProperties: false"
        )


def test_non_agentic_tools_have_a_dispatcher() -> None:
    for spec in TOOLS:
        if spec.kind == "agentic":
            continue
        assert spec.dispatch is not None, f"{spec.name}: missing dispatcher"


def test_write_tools_carry_a_confirmation_path() -> None:
    # Every write commits after confirmation via exactly one path: a synchronous
    # ``executor`` or a long-running ``launches_operation`` (interface launcher).
    for spec in TOOLS:
        if spec.kind == "write":
            assert (spec.executor is not None) ^ spec.launches_operation, (
                f"{spec.name}: a write tool needs exactly one of executor / "
                "launches_operation"
            )


def test_build_tools_stamps_one_cache_breakpoint() -> None:
    tools = registry.build_tools()
    breakpoints = [t for t in tools if "cache_control" in t]
    assert len(breakpoints) == 1, "exactly one ephemeral cache breakpoint expected"
    assert all({"name", "description", "input_schema"} <= t.keys() for t in tools)
