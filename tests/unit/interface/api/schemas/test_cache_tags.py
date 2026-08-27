"""The cache-tag coverage contract, enforced in CI (v0.11.4).

Every operation that can be launched must declare which resource families it
stales, so the web client invalidates from what the server says rather than from
a key list each trigger callsite guessed. An import route that ships untagged
fails here — that is the whole guard, since ``touches_for`` is deliberately
silent on an unknown type.
"""

import ast
from pathlib import Path
from typing import get_args

from src.application.use_cases._shared.sync_targets import SYNC_TARGETS, SyncTarget
import src.interface.api as api_pkg
from src.interface.api.routes.operation_runs import _IMPORT_LIKE_TYPES
from src.interface.api.schemas.cache_tags import (
    OPERATION_TOUCHES,
    CacheTag,
    touches_for,
)

# The calls that turn an ``operation_type`` into cache tags. ``launch_sse_operation``
# starts a tracked run; ``build_terminal_event`` is the other door — the workflow
# and import-queue terminals reach the client without ever launching one.
_TAGGED_CALLS = frozenset({"launch_sse_operation", "build_terminal_event"})


def _discover_launched_operation_types() -> set[str]:
    """AST-scan the API package for ``operation_type=`` on tag-bearing calls.

    Uses ``ast`` rather than importing so discovery has no import side effects
    and matches exactly what a maintainer sees when grepping for the callsites.

    A callsite that names its operation type through a constant rather than a
    literal is resolved from that module's own assignments. Anything the scan
    still cannot read raises: silently skipping it would let the next
    indirection ship untagged behind a green test, which is the one failure this
    guard exists to prevent.
    """
    root = Path(api_pkg.__file__).parent
    found: set[str] = set()
    for path in root.rglob("*.py"):
        # The module defining both helpers only ever forwards its own parameter:
        # it declares no operation type, so scanning it would trip the guard.
        if path.name == "sse_operations.py":
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        # Module-level ``NAME = "literal"`` bindings, so a callsite may name its
        # operation type through a constant.
        consts: dict[str, str] = {
            target.id: node.value.value
            for node in tree.body
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
            for target in node.targets
            if isinstance(target, ast.Name) and isinstance(node.value.value, str)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else getattr(func, "id", None)
            )
            if name not in _TAGGED_CALLS:
                continue
            for kw in node.keywords:
                if kw.arg != "operation_type":
                    continue
                if isinstance(kw.value, ast.Constant):
                    found.add(str(kw.value.value))
                elif isinstance(kw.value, ast.Name) and kw.value.id in consts:
                    found.add(consts[kw.value.id])
                else:
                    raise AssertionError(
                        f"{path}:{kw.value.lineno} names an operation whose "
                        "type this scan cannot resolve statically. Bind it to a "
                        "module-level string constant, or the cache-tag guard "
                        "cannot see it."
                    )
    return found


def test_every_launched_operation_type_has_tags() -> None:
    launched = _discover_launched_operation_types()
    assert launched, "AST scan found no tagged callsites — scan is broken"
    missing = launched - OPERATION_TOUCHES.keys()
    assert not missing, (
        f"Operations launched with no cache tags: {sorted(missing)}. "
        "Add a row to OPERATION_TOUCHES naming what the operation writes."
    )


def test_every_sync_target_has_a_scheduled_row() -> None:
    """Adding a connector should be a one-file edit, not a silent gap.

    Scheduled runs never touch ``launch_sse_operation``, so the scan above can't
    see them — but they do write run-log rows the client reads.
    """
    expected = {f"sync:{target}" for target in SYNC_TARGETS}
    missing = expected - OPERATION_TOUCHES.keys()
    assert not missing, f"Sync targets with no cache tags: {sorted(missing)}"


def test_every_sync_target_operation_type_has_a_launched_row() -> None:
    """A scheduled row derives from the work its target names.

    Derived with ``.get``, so a target whose ``operation_type`` has no row would
    otherwise ship a ``("schedules",)``-only row that looks tagged — and, before
    that, took the API down at import. This is the message that should fire.
    """
    missing = sorted(
        f"{target} → {spec.operation_type}"
        for target, spec in SYNC_TARGETS.items()
        if spec.operation_type not in OPERATION_TOUCHES
    )
    assert not missing, (
        f"Sync targets whose operation type has no tags: {missing}. "
        "Add a row to OPERATION_TOUCHES naming what the operation writes."
    )


def test_every_tag_used_is_declared() -> None:
    declared = set(get_args(CacheTag))
    used = {tag for tags in OPERATION_TOUCHES.values() for tag in tags}
    assert used <= declared, f"Undeclared tags in use: {sorted(used - declared)}"


def test_unknown_operation_type_returns_empty() -> None:
    assert touches_for("no_such_operation") == ()


def test_no_row_is_empty() -> None:
    """An empty row reads as 'nothing to refresh', which no real run means.

    One assertion over the whole table rather than a case per row: the failure
    should name every offender at once, and the row count grows per connector.
    """
    empty = sorted(key for key, tags in OPERATION_TOUCHES.items() if not tags)
    assert not empty, f"Operation types with no cache tags: {empty}"


def test_sync_target_literal_matches_the_registry() -> None:
    """The wire enum and the dispatch table name the same set, both directions.

    A registry key missing from the literal is already a type error. The other
    direction is not: a literal member with no registry entry ships as an
    OpenAPI enum value the server cannot dispatch — the mirror this milestone
    exists to delete, reintroduced inside the backend.
    """
    assert set(get_args(SyncTarget)) == set(SYNC_TARGETS)


def test_import_history_filter_is_a_subset_of_the_tag_table() -> None:
    """Import History's default filter cannot name an operation nothing tags.

    ``_IMPORT_LIKE_TYPES`` is maintained by hand next to the route. Without this,
    a new import route can be correctly tagged yet still vanish from the UI.
    """
    unknown = set(_IMPORT_LIKE_TYPES) - OPERATION_TOUCHES.keys()
    assert not unknown, (
        f"Import-history types absent from OPERATION_TOUCHES: {sorted(unknown)}"
    )


# Families the client uses but no server operation can emit: they are dirtied by
# user writes (settings, assistant key, reviews) or never dirtied at all (the
# static workflow catalog). Pinned so a fifth is a deliberate edit rather than a
# row someone forgot to tag.
_CLIENT_ONLY_TAGS = {
    "assistant",
    "reviews",
    "settings",
    "workflow-catalog",
}


def test_declared_tags_no_operation_emits_are_the_known_client_only_set() -> None:
    emitted = {tag for tags in OPERATION_TOUCHES.values() for tag in tags}
    assert set(get_args(CacheTag)) - emitted == _CLIENT_ONLY_TAGS
