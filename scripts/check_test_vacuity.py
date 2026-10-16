#!/usr/bin/env python3
"""Flag vacuous tests by V-code and ratchet them against a baseline.

Parse every ``tests/**/test_*.py`` file with ``ast``. Classify each test function by
the banned patterns of ``.claude/rules/test-value.md`` that syntax can decide: V1, V2,
V3, V5, V7, V9, and DUP (identical normalized test bodies). V4, V6, V8, and V10 need
judgment and stay with audit agents. The default mode fails when a flagged test is not
listed for its code in ``tests/.vacuity_baseline.json``. With ``--base-ref REF`` it also
fails when that file gains an id, relative to the file committed at ``REF``, for a test
file that changed since ``REF``: a branch cannot hide its own new offenders with
``--update-baseline``. An id in an unchanged test file may enter the baseline (a
sharper checker finds an old offender); it is printed as a note.

Usage:
    uv run python scripts/check_test_vacuity.py                    # ratchet
    uv run python scripts/check_test_vacuity.py --base-ref main    # + no growth in changed files
    uv run python scripts/check_test_vacuity.py --report [--json]  # list every flag
    uv run python scripts/check_test_vacuity.py --update-baseline
"""

import argparse
import ast
from collections import ChainMap, defaultdict
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from enum import StrEnum
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import cast

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_NAME = "tests/.vacuity_baseline.json"
CODES = ("V1", "V2", "V3", "V5", "V7", "V9", "DUP")

_MOCK_ASSERT_PREFIXES = (
    "assert_called",
    "assert_awaited",
    "assert_not_called",
    "assert_not_awaited",
    "assert_any_call",
    "assert_any_await",
    "assert_has_calls",
    "assert_has_awaits",
)
_MOCK_ARG_ASSERTS = frozenset({
    "assert_called_with",
    "assert_called_once_with",
    "assert_awaited_with",
    "assert_awaited_once_with",
    "assert_any_call",
    "assert_any_await",
    "assert_has_calls",
    "assert_has_awaits",
})
_MOCK_ARG_ATTRS = frozenset({
    "call_args",
    "call_args_list",
    "await_args",
    "await_args_list",
    "mock_calls",
    "method_calls",
})
_MOCK_ATTRS = _MOCK_ARG_ATTRS | {"called", "call_count", "await_count", "awaited"}
_HELPER_PREFIXES = (
    "assert",
    "_assert",
    "check_",
    "_check_",
    "expect_",
    "_expect_",
    "verify",
    "_verify",
)
_PYTEST_CHECKS = frozenset({"raises", "warns", "deprecated_call", "fail"})
_FROZEN_ERRORS = frozenset({"FrozenInstanceError", "AttributeError"})
_TIMING_WORDS = frozenset({"elapsed", "took", "latency", "runtime"})
_TIMING_RECEIVERS = frozenset({
    "result",
    "event",
    "run",
    "timing",
    "timer",
    "stats",
    "metrics",
})
# Boundaries that test-value.md allows to mock: clock, randomness, HTTP transport.
_BOUNDARY_NAMES = frozenset({
    "datetime",
    "date",
    "time",
    "monotonic",
    "perf_counter",
    "sleep",
    "uuid4",
    "uuid7",
    "random",
    "randint",
    "choice",
    "shuffle",
})
_BOUNDARY_PREFIXES = (
    "_api_call",
    "_api_request",
    "_request",
    "_client",
    "_http",
    "_session",
    "_transport",
)


class Check(StrEnum):
    """Kind of one check inside a test."""

    REAL = "real"
    MOCK = "mock"
    MOCK_ARGS = "mock_args"
    EXISTENCE = "existence"
    ENUM_PIN = "enum_pin"
    FROZEN = "frozen"
    TIMING = "timing"


@dataclass(frozen=True, slots=True)
class Finding:
    """One flagged test and the V-code it violates.

    ``cls`` is the class path, ``Outer::Inner`` for a nested class.
    """

    file: str
    line: int
    name: str
    cls: str | None
    code: str
    group: int | None = None

    @property
    def key(self) -> str:
        """Line-independent identity for baseline comparison: the pytest node id."""
        parts = [self.file, *([self.cls] if self.cls else []), self.name]
        return "::".join(parts)

    def to_json(self) -> dict[str, object]:
        """Return the machine-readable form, with ``class`` as the key."""
        data: dict[str, object] = asdict(self)
        data["class"] = data.pop("cls")
        if self.group is None:
            del data["group"]
        return data


type FuncDef = ast.FunctionDef | ast.AsyncFunctionDef


# --- small AST helpers -------------------------------------------------------


def _call_name(func: ast.expr) -> str | None:
    """Return the final name of a call target (``a.b.c`` gives ``c``)."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _dotted(expr: ast.expr) -> str:
    """Return ``a.b.c`` for a Name/Attribute chain, else an empty string."""
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        inner = _dotted(expr.value)
        return f"{inner}.{expr.attr}" if inner else ""
    return ""


def _str_const(expr: ast.expr | None) -> str | None:
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    return None


def _is_pytest_check(call: ast.Call) -> bool:
    dotted = _dotted(call.func)
    return dotted.startswith("pytest.") and dotted[7:] in _PYTEST_CHECKS


def _is_mock_assert_call(call: ast.Call) -> bool:
    return isinstance(call.func, ast.Attribute) and call.func.attr.startswith(
        _MOCK_ASSERT_PREFIXES
    )


def _local_call_name(call: ast.Call) -> str | None:
    """Return the name of a call that can reach a function of this module.

    That is a bare ``name(...)`` or a ``self.name(...)``/``cls.name(...)`` method
    call. A call on any other receiver (``subprocess.check_call``) gives None.
    """
    match call.func:
        case ast.Name(id=name):
            return name
        case ast.Attribute(value=ast.Name(id="self" | "cls"), attr=name):
            return name
        case _:
            return None


def _is_int_const(expr: ast.expr, value: int) -> bool:
    return (
        isinstance(expr, ast.Constant)
        and type(expr.value) in {int, float}
        and expr.value == value
    )


def _walk_shallow(nodes: list[ast.stmt]) -> list[ast.AST]:
    """Walk statements without entering nested functions, lambdas, or classes."""
    out: list[ast.AST] = []
    stack: list[ast.AST] = list(reversed(nodes))
    while stack:
        node = stack.pop()
        out.append(node)
        if isinstance(
            node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef
        ):
            continue
        stack.extend(reversed(list(ast.iter_child_nodes(node))))
    return out


# --- assert classification ---------------------------------------------------


def _is_existence(expr: ast.expr, predicates: frozenset[str]) -> bool:
    """Return True for existence/type checks that pin no value (V3).

    A bare ``assert name`` is an existence check, except when ``name`` is in
    ``predicates``: the test binds it to a call, comparison, or boolean result.
    """
    match expr:
        case ast.Name(id=name):
            return name not in predicates
        case ast.Compare(ops=[ast.IsNot()], comparators=[ast.Constant(value=None)]):
            return True
        case ast.Call(func=ast.Name(id="isinstance" | "hasattr" | "callable" | "len")):
            return True
        case ast.Compare(
            left=ast.Call(func=ast.Name(id="len")), ops=[op], comparators=[right]
        ):
            return (
                (isinstance(op, ast.Gt) and _is_int_const(right, 0))
                or (isinstance(op, ast.GtE) and _is_int_const(right, 1))
                or (isinstance(op, ast.NotEq) and _is_int_const(right, 0))
            )
        case ast.BoolOp(op=ast.And(), values=values):
            return all(_is_existence(v, predicates) for v in values)
        case _:
            return False


def _is_enum_member(expr: ast.expr) -> bool:
    """Return True for ``Enum.MEMBER``: a CapWords owner and an UPPER_CASE member."""
    match expr:
        case ast.Attribute(
            attr=member, value=ast.Name(id=owner) | ast.Attribute(attr=owner)
        ):
            return owner[:1].isupper() and not owner.isupper() and member.isupper()
        case _:
            return False


def _is_enum_pin(expr: ast.expr) -> bool:
    """Return True for ``Enum.MEMBER.value == <str/int literal>`` (V5)."""
    match expr:
        case ast.Compare(left=left, ops=[ast.Eq()], comparators=[right]):
            pass
        case _:
            return False
    for member, literal in ((left, right), (right, left)):
        match member, literal:
            case (
                ast.Attribute(attr="value", value=owner),
                ast.Constant(value=str() | int() as value),
            ) if not isinstance(value, bool) and _is_enum_member(owner):
                return True
            case _:
                pass
    return False


def _references_mock_attr(expr: ast.expr) -> bool:
    return any(
        isinstance(node, ast.Attribute) and node.attr in _MOCK_ATTRS
        for node in ast.walk(expr)
    )


def _checks_mock_args(expr: ast.expr) -> bool:
    """Return True when ``expr`` reads recorded call arguments, not only a count.

    ``len(mock.call_args_list)`` is a call count, so it does not count.
    """
    counted = {
        id(node.args[0])
        for node in ast.walk(expr)
        if isinstance(node, ast.Call)
        and _call_name(node.func) == "len"
        and len(node.args) == 1
    }
    return any(
        isinstance(node, ast.Attribute)
        and node.attr in _MOCK_ARG_ATTRS
        and id(node) not in counted
        for node in ast.walk(expr)
    )


def _assert_parts(expr: ast.expr) -> list[ast.expr]:
    """Split an assert into the checks it makes together.

    ``a and b`` gives its operands. ``(a, b) == (x, y)`` gives ``a == x`` and
    ``b == y``. Any other expression is one part.
    """
    match expr:
        case ast.BoolOp(op=ast.And(), values=values):
            return [part for value in values for part in _assert_parts(value)]
        case ast.Compare(
            left=ast.Tuple(elts=lefts) | ast.List(elts=lefts),
            ops=[ast.Eq()],
            comparators=[ast.Tuple(elts=rights) | ast.List(elts=rights)],
        ) if len(lefts) == len(rights):
            return [
                ast.Compare(left=left, ops=[ast.Eq()], comparators=[right])
                for left, right in zip(lefts, rights, strict=True)
            ]
        case _:
            return [expr]


def _classify_assert(expr: ast.expr, predicates: frozenset[str]) -> Check:
    """Return the kind of one assert.

    An assert that makes several checks together is REAL when one of them is.
    """
    parts = _assert_parts(expr)
    if len(parts) > 1 and any(
        _classify_part(part, predicates) is Check.REAL for part in parts
    ):
        return Check.REAL
    return _classify_part(expr, predicates)


def _classify_part(expr: ast.expr, predicates: frozenset[str]) -> Check:
    if _is_timing_bound(expr):
        return Check.TIMING
    if _references_mock_attr(expr):
        return Check.MOCK_ARGS if _checks_mock_args(expr) else Check.MOCK
    if _is_enum_pin(expr):
        return Check.ENUM_PIN
    if _is_existence(expr, predicates):
        return Check.EXISTENCE
    return Check.REAL


def _is_timing_name(subject: ast.expr) -> bool:
    """Return True when ``subject`` names a measured elapsed time.

    ``execution_time``, ``elapsed``, ``took``, ``latency``, and ``runtime`` always
    do. ``duration`` does only on a result or timing receiver (``result.duration_ms``),
    because a track's ``duration_ms`` is data.
    """
    name = _call_name(subject)
    if name is None:
        return False
    words = name.lower().split("_")
    if "execution_time" in name.lower() or _TIMING_WORDS.intersection(words):
        return True
    if "duration" not in words or not isinstance(subject, ast.Attribute):
        return False
    receiver = _call_name(subject.value) or ""
    return receiver in _TIMING_RECEIVERS or receiver.endswith("_result")


def _is_timing_bound(expr: ast.expr) -> bool:
    """Return True for ``<elapsed time> >= 0`` or ``> 0`` (V9)."""
    match expr:
        case ast.Compare(left=left, ops=[op], comparators=[right]):
            pass
        case _:
            return False
    if isinstance(op, ast.Gt | ast.GtE) and _is_int_const(right, 0):
        subject = left
    elif isinstance(op, ast.Lt | ast.LtE) and _is_int_const(left, 0):
        subject = right
    else:
        return False
    return _is_timing_name(subject)


def _is_frozen_raises(item: ast.withitem, body: list[ast.stmt]) -> bool:
    """Return True for ``pytest.raises(<frozen error>)`` around an attribute write."""
    call = item.context_expr
    if not (isinstance(call, ast.Call) and _dotted(call.func) == "pytest.raises"):
        return False
    if not call.args:
        return False
    errors = (
        call.args[0].elts if isinstance(call.args[0], ast.Tuple) else [call.args[0]]
    )
    if not errors or not all(_call_name(e) in _FROZEN_ERRORS for e in errors):
        return False
    for stmt in body:
        match stmt:
            case ast.Assign(targets=targets) if any(
                isinstance(t, ast.Attribute) for t in targets
            ):
                return True
            case ast.AugAssign(target=ast.Attribute()) | ast.Delete():
                return True
            case ast.Expr(value=ast.Call(func=ast.Name(id="setattr" | "delattr"))):
                return True
            case _:
                pass
    return False


def collect_checks(stmts: list[ast.stmt], helpers: frozenset[str]) -> list[Check]:
    """Return the kind of every check found in ``stmts``.

    ``helpers`` names local functions and methods that contain checks. A call to
    one, as ``name(...)`` or ``self.name(...)``, counts as a real check.
    """
    return _scan_checks(stmts, dict.fromkeys(helpers, _REAL_ONLY))[0]


_REAL_ONLY = frozenset({Check.REAL})
_PREDICATE_VALUES = (ast.Call, ast.Compare, ast.BoolOp)


def _predicate_names(nodes: list[ast.AST]) -> frozenset[str]:
    """Return names bound to a call, comparison, or boolean result in ``nodes``."""
    names: set[str] = set()
    for node in nodes:
        match node:
            case ast.Assign(targets=targets, value=value):
                bound = [t.id for t in targets if isinstance(t, ast.Name)]
            case (
                ast.AnnAssign(target=ast.Name(id=name), value=value)
                | ast.NamedExpr(target=ast.Name(id=name), value=value)
            ):
                bound = [name]
            case _:
                continue
        if isinstance(value, ast.Await):
            value = value.value
        if isinstance(value, _PREDICATE_VALUES) or (
            isinstance(value, ast.UnaryOp) and isinstance(value.op, ast.Not)
        ):
            names.update(bound)
    return frozenset(names)


def _scan_checks(
    stmts: list[ast.stmt], helpers: Mapping[str, frozenset[Check]]
) -> tuple[list[Check], set[str]]:
    """Return the checks in ``stmts`` and the local names they call.

    ``helpers`` maps a local function to the check kinds that a call to it adds.
    """
    checks: list[Check] = []
    called: set[str] = set()
    consumed: set[int] = set()
    nodes = list(ast.walk(ast.Module(body=stmts, type_ignores=[])))
    predicates = _predicate_names(nodes)
    for node in nodes:
        local = _local_call_name(node) if isinstance(node, ast.Call) else None
        if local is not None:
            called.add(local)
        match node:
            case ast.Assert(test=test):
                checks.append(_classify_assert(test, predicates))
            case (
                ast.With(items=items, body=body) | ast.AsyncWith(items=items, body=body)
            ):
                for item in items:
                    if _is_frozen_raises(item, body):
                        checks.append(Check.FROZEN)
                        consumed.add(id(item.context_expr))
            case ast.Call() if id(node) not in consumed:
                if _is_mock_assert_call(node):
                    checks.append(
                        Check.MOCK_ARGS
                        if _call_name(node.func) in _MOCK_ARG_ASSERTS
                        else Check.MOCK
                    )
                elif _is_pytest_check(node):
                    checks.append(Check.REAL)
                elif local is not None and local in helpers:
                    checks.extend(helpers[local])
            case ast.Raise(exc=exc) if exc is not None and (
                _call_name(exc.func if isinstance(exc, ast.Call) else exc)
                == "AssertionError"
            ):
                checks.append(Check.REAL)
            case _:
                pass
    return checks, called


def classify_checks(checks: list[Check]) -> str | None:
    """Return V1, V2, V3, or V5 for the assertion shape, or None when it is sound.

    V2 and V3 need every mock check to be bare. A check of call arguments may be
    the contract, so audit agents judge those tests. A timing bound counts as a
    check here; ``analyze_source`` flags it as V9.
    """
    kinds = set(checks)
    if not kinds:
        return "V1"
    if kinds <= {Check.FROZEN, Check.ENUM_PIN}:
        return "V5"
    if kinds == {Check.MOCK}:
        return "V2"
    if Check.EXISTENCE in kinds and kinds <= {Check.EXISTENCE, Check.MOCK}:
        return "V3"
    return None


# --- V7: patching the unit under test ----------------------------------------


@dataclass(frozen=True, slots=True)
class _Patch:
    """One patched attribute and the owner it was patched on.

    ``owner_path`` is the dotted path of the owner for a string target, else empty.
    """

    name: str
    owner: str
    owner_is_module: bool
    owner_path: str = ""


def _is_boundary(name: str) -> bool:
    """Return True for a clock, randomness, or HTTP transport name (a legal mock)."""
    return name in _BOUNDARY_NAMES or name.startswith(_BOUNDARY_PREFIXES)


def _patch_from_call(call: ast.Call, modules: frozenset[str]) -> _Patch | None:
    """Return the patch that ``call`` makes, or None for a boundary or dunder patch."""
    dotted = _dotted(call.func)
    first = call.args[0] if call.args else None
    second = call.args[1] if len(call.args) > 1 else None
    target = _str_const(first)
    patch: _Patch | None = None
    if dotted in {"patch", "mock.patch", "unittest.mock.patch", "mocker.patch"} or (
        dotted == "monkeypatch.setattr" and target is not None
    ):
        if target is None or "." not in target:
            return None
        owner_path, name = target.rsplit(".", 1)
        owner = owner_path.rsplit(".", 1)[-1]
        patch = _Patch(name, owner, not owner[:1].isupper(), owner_path)
    elif dotted.endswith(("patch.object", "monkeypatch.setattr")) and first is not None:
        name = _str_const(second)
        owner = _dotted(first)
        if name is None or not owner or owner in modules:
            return None
        patch = _Patch(name, owner.rsplit(".", 1)[-1], owner_is_module=False)
    if patch is None or patch.name.startswith("__") or _is_boundary(patch.name):
        return None
    return patch


def _constructed_class(
    expr: ast.expr | None, factories: Mapping[str, str]
) -> str | None:
    """Return the class that ``expr`` builds, or None when it builds nothing.

    ``Owner(...)``, ``mod.Owner(...)``, and ``Owner.create(...)`` build ``Owner``.
    A call to a local factory or fixture builds what that function returns.
    """
    if isinstance(expr, ast.Await):
        expr = expr.value
    match expr:
        case ast.Call(func=ast.Name(id=name)):
            return factories.get(name, name)
        case ast.Call(func=ast.Attribute(attr=attr, value=value)):
            if attr[:1].isupper():
                return attr
            if isinstance(value, ast.Name) and value.id[:1].isupper():
                return value.id
            return attr
        case _:
            return None


def _returned_class(
    fn: FuncDef, body: list[ast.AST], factories: Mapping[str, str]
) -> str | None:
    """Return the class that ``fn`` returns or yields, or None when unknown.

    ``body`` is the shallow walk of ``fn``. The value is a constructor call, a
    local name bound to one, or a parameter that names a known fixture.
    """
    local: dict[str, str] = {}
    for node in body:
        match node:
            case ast.Assign(targets=[ast.Name(id=var)], value=value) if (
                cls_name := _constructed_class(value, factories)
            ):
                local[var] = cls_name
            case ast.withitem(context_expr=value, optional_vars=ast.Name(id=var)) if (
                cls_name := _constructed_class(value, factories)
            ):
                local[var] = cls_name
            case _:
                pass
    params = {arg.arg for arg in fn.args.args}
    for node in body:
        match node:
            case ast.Return(value=ast.Name(id=var)) | ast.Yield(value=ast.Name(id=var)):
                found = local.get(var) or (
                    factories.get(var) if var in params else None
                )
            case ast.Return(value=value) | ast.Yield(value=value):
                found = _constructed_class(value, factories)
            case _:
                continue
        if found:
            return found
    return None


def _factories(tree: ast.Module) -> dict[str, str]:
    """Map each local function (fixtures included) to the class it returns."""
    functions: list[tuple[FuncDef, list[ast.AST]]] = []
    for node in ast.walk(tree):
        if isinstance(
            node, ast.FunctionDef | ast.AsyncFunctionDef
        ) and not node.name.startswith("test"):
            body = _walk_shallow(node.body)
            if any(isinstance(n, ast.Return | ast.Yield) for n in body):
                functions.append((node, body))
    factories: dict[str, str] = {}
    changed = True
    while changed:  # a fixture may pass through one defined later
        changed = False
        for fn, body in functions:
            if fn.name not in factories and (
                cls_name := _returned_class(fn, body, factories)
            ):
                factories[fn.name] = cls_name
                changed = True
    return factories


def _self_instances(
    chain: list[tuple[ast.ClassDef, str]], factories: Mapping[str, str]
) -> dict[str, str]:
    """Map ``self.<attr>`` to the class it holds, from class-level or method binds.

    ``chain`` is the test class and its local base classes, most derived first.
    """
    instances: dict[str, str] = {}
    for cls, _path in reversed(chain):
        instances.update(_own_instances(cls, factories))
    return instances


def _own_instances(cls: ast.ClassDef, factories: Mapping[str, str]) -> dict[str, str]:
    instances: dict[str, str] = {}
    class_level = {id(stmt) for stmt in cls.body}
    for node in ast.walk(cls):
        match node:
            case ast.Assign(targets=targets, value=value):
                pass
            case _:
                continue
        if not (cls_name := _constructed_class(value, factories)):
            continue
        for target in targets:
            match target:
                case ast.Attribute(value=ast.Name(id="self"), attr=attr):
                    instances[f"self.{attr}"] = cls_name
                case ast.Name(id=attr) if id(node) in class_level:
                    instances[f"self.{attr}"] = cls_name
                case _:
                    pass
    return instances


def _receiver_matches(receiver: ast.expr, owner: str, bound: Mapping[str, str]) -> bool:
    """Return True when ``receiver`` is ``owner``, ``owner(...)``, or bound to it."""
    if isinstance(receiver, ast.Call):
        return _call_name(receiver.func) == owner
    dotted = _dotted(receiver)
    return bool(dotted) and owner in {
        dotted,
        dotted.rsplit(".", 1)[-1],
        bound.get(dotted),
    }


def patches_unit_under_test(
    fn: FuncDef,
    modules: frozenset[str],
    scope: Mapping[str, str],
    bound: Mapping[str, str],
) -> bool:
    """Return True when the test patches a callable that it then calls directly (V7).

    A module-level string patch ``pkg.mod.name`` matches a bare ``name(...)`` call
    when ``scope`` resolves ``name`` to ``pkg.mod.name``. A class or object patch
    matches a method call on that owner, on ``owner(...)``, or on a name that
    ``bound`` or the test binds to an instance of it. The method name must be the
    patched name, or the patched name must be private: the test stubs out the
    unit's own internals and then drives the unit. Clock, randomness, and HTTP
    transport names are boundaries and never count. ``bound`` maps fixtures,
    local factories, and ``self.<attr>`` to the class they build or hold.
    """
    body = _walk_shallow(fn.body)
    patches: list[_Patch] = []
    patch_calls: set[int] = set()
    for node in [*fn.decorator_list, *body]:
        if isinstance(node, ast.Call) and (patch := _patch_from_call(node, modules)):
            patch_calls.add(id(node))
            imported = scope.get(patch.owner, "")
            if (
                patch.owner_path
                and not patch.owner_is_module
                and imported
                and "::" not in imported
                and imported != patch.owner_path
            ):
                continue  # the test imports a different class of that name
            patches.append(patch)
    if not patches:
        return False
    instances = dict(bound)
    for node in body:
        match node:
            case ast.Assign(targets=[ast.Name(id=var)], value=value):
                pass
            case ast.withitem(context_expr=value, optional_vars=ast.Name(id=var)):
                pass
            case _:
                continue
        if cls_name := _constructed_class(value, bound):
            instances[var] = cls_name
    for node in body:
        if not isinstance(node, ast.Call) or id(node) in patch_calls:
            continue
        for patch in patches:
            match node.func:
                case ast.Name(id=name) if (
                    patch.owner_is_module
                    and name == patch.name
                    and scope.get(name) == f"{patch.owner_path}.{name}"
                ):
                    return True
                case ast.Attribute(attr=attr, value=receiver) if (
                    not patch.owner_is_module
                    and (attr == patch.name or patch.name.startswith("_"))
                    and _receiver_matches(receiver, patch.owner, instances)
                ):
                    return True
                case _:
                    pass
    return False


# --- DUP: normalized body fingerprints ---------------------------------------


def _dump(
    node: object,
    qualified: Mapping[str, str],
    self_attrs: _SelfAttrs,
    out: list[str],
) -> None:
    """Serialize ``node`` into ``out`` with names replaced by their qualified form.

    Argument annotations are left out. The node itself is not changed.
    """
    match node:
        case list():
            out.append("[")
            for item in cast("list[object]", node):
                _dump(item, qualified, self_attrs, out)
            out.append("]")
        case ast.Name(id=name, ctx=ctx):
            out.append(f"Name({qualified.get(name, name)!r},{type(ctx).__name__})")
        case ast.arg(arg=name):
            out.append(f"arg({qualified.get(name, name)!r})")
        case ast.Attribute(value=ast.Name(id="self"), attr=attr, ctx=ctx):
            out.append(f"self.({self_attrs.qualify(attr)!r},{type(ctx).__name__})")
        case ast.AST():
            out.append(f"{type(node).__name__}(")
            for field in ast.iter_fields(node):
                _dump(cast("tuple[str, object]", field)[1], qualified, self_attrs, out)
            out.append(")")
        case _:
            out.append(f"{node!r},")


def fingerprint(
    fn: FuncDef,
    qualified: Mapping[str, str],
    self_attrs: _SelfAttrs,
) -> str:
    """Hash the test with its name, docstring, and annotations removed."""
    body = fn.body
    if body and isinstance(body[0], ast.Expr) and _str_const(body[0].value) is not None:
        body = body[1:] or [ast.Pass()]
    out: list[str] = [type(fn).__name__]
    for part in (fn.args, body, fn.decorator_list, fn.type_params):
        _dump(part, qualified, self_attrs, out)
    return hashlib.sha256("".join(out).encode()).hexdigest()


# --- module analysis ---------------------------------------------------------


def _is_fixture(fn: FuncDef) -> bool:
    for dec in fn.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if _dotted(target) in {"pytest.fixture", "fixture", "pytest_asyncio.fixture"}:
            return True
    return False


def _module_scope(tree: ast.Module, rel: str) -> tuple[dict[str, str], frozenset[str]]:
    """Map module-level names to qualified names; list modules imported whole."""
    names: dict[str, str] = {}
    modules: set[str] = set()
    for stmt in tree.body:
        match stmt:
            case ast.Import(names=aliases):
                for alias in aliases:
                    local = alias.asname or alias.name.split(".")[0]
                    names[local] = alias.name if alias.asname else local
                    modules.add(local)
            case ast.ImportFrom(module=module, names=aliases):
                for alias in aliases:
                    names[alias.asname or alias.name] = f"{module}.{alias.name}"
            case (
                ast.FunctionDef(name=name)
                | ast.AsyncFunctionDef(name=name)
                | ast.ClassDef(name=name)
            ):
                names[name] = f"{rel}::{name}"
            case ast.Assign(targets=targets):
                for target in targets:
                    if isinstance(target, ast.Name):
                        names[target.id] = f"{rel}::{target.id}"
            case ast.AnnAssign(target=ast.Name(id=name)):
                names[name] = f"{rel}::{name}"
            case _:
                pass
    return names, frozenset(modules)


def _helper_contribution(kinds: set[Check]) -> frozenset[Check]:
    """Return what a call to a helper with ``kinds`` adds to the calling test.

    A sound helper adds one real check. A vacuous one (only bare mock calls, only
    existence checks) adds its own kinds, so the test is judged by them.
    """
    return _REAL_ONLY if classify_checks(list(kinds)) is None else frozenset(kinds)


def _asserting_helpers(tree: ast.Module) -> dict[str, frozenset[Check]]:
    """Map each local non-test function that checks to what a call to it adds.

    A function checks directly, calls a local function that checks, or has a
    helper name (``assert*``, ``check_*``, ``expect_*``, ``verify*``). Only local
    functions count: ``subprocess.check_call`` is not a helper.
    """
    kinds: dict[str, set[Check]] = defaultdict(set)
    calls: dict[str, set[str]] = defaultdict(set)
    for node in ast.walk(tree):
        if isinstance(
            node, ast.FunctionDef | ast.AsyncFunctionDef
        ) and not node.name.startswith("test"):
            checks, called = _scan_checks(node.body, {})
            kinds[node.name].update(checks)
            calls[node.name].update(called)
    changed = True
    while changed:
        changed = False
        for name, callees in calls.items():
            for callee in callees & kinds.keys():
                if not kinds[callee]:
                    continue
                added = _helper_contribution(kinds[callee]) - kinds[name]
                if added:
                    kinds[name].update(added)
                    changed = True
    for name, found in kinds.items():
        if not found and name.startswith(_HELPER_PREFIXES):
            found.add(Check.REAL)
    return {name: _helper_contribution(found) for name, found in kinds.items() if found}


type TestItem = tuple[str, FuncDef, ast.ClassDef | None]


def iter_tests(tree: ast.Module) -> list[TestItem]:
    """Return every pytest test function in the module, in source order.

    Each item is the node id within the file (``Outer::Inner::test_x``), the
    function, and its innermost class.
    """
    tests: list[TestItem] = []

    def visit(body: list[ast.stmt], prefix: str, cls: ast.ClassDef | None) -> None:
        for stmt in body:
            if isinstance(
                stmt, ast.FunctionDef | ast.AsyncFunctionDef
            ) and stmt.name.startswith("test"):
                tests.append((prefix + stmt.name, stmt, cls))
            elif isinstance(stmt, ast.ClassDef) and stmt.name.startswith("Test"):
                visit(stmt.body, f"{prefix}{stmt.name}::", stmt)

    visit(tree.body, "", None)
    return tests


@dataclass(slots=True)
class _SelfAttrs:
    """Qualified names of ``self.<attr>``; an unbound attr is qualified by ``fallback``."""

    fallback: str
    names: dict[str, str] = field(default_factory=dict)

    def qualify(self, attr: str) -> str:
        """Return the qualified name of ``self.<attr>``."""
        return self.names.get(attr, f"{self.fallback}.{attr}")


def _class_chain(
    cls: ast.ClassDef | None, cls_path: str | None, classes: Mapping[str, ast.ClassDef]
) -> list[tuple[ast.ClassDef, str]]:
    """Return the test class and its base classes in this module, most derived first."""
    chain: list[tuple[ast.ClassDef, str]] = []
    pending = [(cls, cls_path or "")] if cls is not None else []
    while pending:
        klass, path = pending.pop(0)
        if any(klass is seen for seen, _ in chain):
            continue
        chain.append((klass, path))
        pending.extend(
            (classes[base.id], base.id)
            for base in klass.bases
            if isinstance(base, ast.Name) and base.id in classes
        )
    return chain


def _self_binds(stmt: ast.stmt) -> list[tuple[str, ast.expr]]:
    """Return the ``(attr, value)`` pairs that a class-body statement binds on ``self``.

    A class-level assignment binds its names. A method binds every ``self.<attr>``
    it assigns.
    """
    match stmt:
        case ast.Assign(targets=targets, value=value):
            return [(t.id, value) for t in targets if isinstance(t, ast.Name)]
        case ast.AnnAssign(target=ast.Name(id=name), value=ast.expr() as value):
            return [(name, value)]
        case ast.FunctionDef() | ast.AsyncFunctionDef():
            return [
                (target.attr, node.value)
                for node in ast.walk(stmt)
                if isinstance(node, ast.Assign | ast.AnnAssign)
                and node.value is not None
                for target in (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                if isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ]
        case _:
            return []


def _class_names(
    chain: list[tuple[ast.ClassDef, str]],
    fallback: str,
    rel: str,
    fixtures: Mapping[str, str],
    scope: Mapping[str, str],
) -> tuple[dict[str, str], _SelfAttrs]:
    """Return the fixtures visible in the class and the qualified ``self.`` names.

    A method is qualified by the class that defines it. A data attribute is
    qualified by the normalized values bound to it, so tests of different
    subjects (``subject = SpotifyConnector`` vs ``LastfmConnector``) differ and
    tests that share a base-class attribute match.
    """
    visible = dict(fixtures)
    self_attrs = _SelfAttrs(fallback)
    class_fixtures: set[str] = set()
    for cls, path in chain:
        values: dict[str, list[str]] = defaultdict(list)
        for stmt in cls.body:
            if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
                if stmt.name not in self_attrs.names:
                    self_attrs.names[stmt.name] = f"{rel}::{path}.{stmt.name}"
                if _is_fixture(stmt) and stmt.name not in class_fixtures:
                    class_fixtures.add(stmt.name)
                    visible[stmt.name] = f"{rel}::{path}.{stmt.name}"
            for attr, value in _self_binds(stmt):
                _dump(value, scope, self_attrs, values[attr])
        for attr, dumped in values.items():
            if attr not in self_attrs.names:
                digest = hashlib.sha256("".join(dumped).encode()).hexdigest()[:16]
                self_attrs.names[attr] = f"={digest}"
    return visible, self_attrs


type Print = tuple[str, Finding]


def analyze_source(source: str, rel: str) -> tuple[list[Finding], list[Print]]:
    """Return per-test findings and DUP fingerprints for one test module.

    Each fingerprint comes with a DUP finding for its test, without a group.
    """
    tree = ast.parse(source, filename=rel)
    scope, modules = _module_scope(tree, rel)
    helpers = _asserting_helpers(tree)
    patches = "patch" in source or "setattr" in source
    factories = _factories(tree) if patches else {}
    classes = {stmt.name: stmt for stmt in tree.body if isinstance(stmt, ast.ClassDef)}
    module_fixtures = {
        stmt.name: f"{rel}::{stmt.name}"
        for stmt in tree.body
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef)
        and _is_fixture(stmt)
    }
    directory = rel.rsplit("/", 1)[0]
    class_names: dict[str | None, tuple[dict[str, str], _SelfAttrs, dict[str, str]]]
    class_names = {}
    findings: list[Finding] = []
    prints: list[Print] = []
    for node_id, fn, cls in iter_tests(tree):
        cls_path = node_id.rpartition("::")[0] or None
        if cls_path not in class_names:
            chain = _class_chain(cls, cls_path, classes)
            fixtures, self_attrs = _class_names(
                chain, f"{rel}::{cls_path}", rel, module_fixtures, scope
            )
            bound = {**factories, **_self_instances(chain, factories)}
            class_names[cls_path] = (fixtures, self_attrs, bound)
        fixtures, self_attrs, bound = class_names[cls_path]
        test = Finding(rel, fn.lineno, fn.name, cls_path, "DUP")

        checks = _scan_checks(fn.body, helpers)[0]
        if shape := classify_checks(checks):
            findings.append(replace(test, code=shape))
        if patches and patches_unit_under_test(fn, modules, scope, bound):
            findings.append(replace(test, code="V7"))
        if Check.TIMING in checks:
            findings.append(replace(test, code="V9"))

        args = {
            arg.arg: f"{directory}/fixture::{arg.arg}"
            for arg in fn.args.args
            if arg.arg not in {"self", "cls"}
        }
        qualified = ChainMap(fixtures, args, scope)
        prints.append((fingerprint(fn, qualified, self_attrs), test))
    return findings, prints


def duplicate_findings(prints: list[Print]) -> list[Finding]:
    """Return a DUP finding for every test whose fingerprint another test shares."""
    groups: dict[str, list[Finding]] = defaultdict(list)
    for digest, test in prints:
        groups[digest].append(test)
    dup_groups = sorted(
        (members for members in groups.values() if len(members) > 1),
        key=lambda members: (members[0].file, members[0].line),
    )
    return [
        replace(test, group=number)
        for number, members in enumerate(dup_groups, start=1)
        for test in members
    ]


def scan(root: Path) -> list[Finding]:
    """Scan every test module under ``root/tests`` and return sorted findings."""
    findings: list[Finding] = []
    prints: list[Print] = []
    for path in sorted((root / "tests").rglob("test_*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(root).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
            file_findings, file_prints = analyze_source(source, rel)
        except SyntaxError as exc:
            print(f"skip {rel}: {exc}", file=sys.stderr)
            continue
        findings.extend(file_findings)
        prints.extend(file_prints)
    findings.extend(duplicate_findings(prints))
    return sorted(findings, key=lambda f: (f.file, f.line, f.code))


# --- baseline ratchet --------------------------------------------------------


def count_by_code(findings: list[Finding]) -> dict[str, int]:
    """Return the number of findings per code, with zero for absent codes."""
    counts: dict[str, int] = dict.fromkeys(CODES, 0)
    for finding in findings:
        counts[finding.code] += 1
    return counts


def build_baseline(findings: list[Finding]) -> dict[str, dict[str, list[str]]]:
    """Return the baseline document: the sorted flagged test ids per code."""
    tests: dict[str, set[str]] = {code: set() for code in CODES}
    for finding in findings:
        tests[finding.code].add(finding.key)
    return {"tests": {code: sorted(keys) for code, keys in tests.items()}}


def parse_baseline(text: str) -> dict[str, set[str]]:
    """Return the flagged test ids per code from a baseline document."""
    document = cast("dict[str, dict[str, list[str]]]", json.loads(text))
    return {code: set(ids) for code, ids in document["tests"].items()}


def load_baseline(path: Path) -> dict[str, set[str]]:
    """Read the flagged test ids per code. A missing file gives none."""
    if not path.exists():
        return {}
    return parse_baseline(path.read_text(encoding="utf-8"))


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )


def changed_files(root: Path, ref: str) -> set[str]:
    """Return the paths added, changed, or renamed since ``ref``, untracked tests included.

    The diff covers commits after ``ref`` and uncommitted work. A rename gives
    both its old and new path.
    """
    diff = _git(
        root, "diff", "--name-only", "--no-renames", "--end-of-options", ref, "--"
    )
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "tests/")
    return {line for line in (diff.stdout + untracked.stdout).splitlines() if line}


def check_base_growth(root: Path, ref: str) -> int:
    """Compare the working baseline to the baseline committed at ``ref``.

    Return 1 when the working baseline gains an id, relative to ``ref``, in a
    test file that changed since ``ref``, or when ``ref`` is unknown or starts
    with ``-``. Print an id gained in an unchanged file as a note. When ``ref``
    has no baseline file, print a note and return 0.
    """
    if ref.startswith("-"):
        print(f"VACUITY FAIL: ref may not start with '-': {ref}")
        return 1
    if _git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").returncode:
        print(f"VACUITY FAIL: unknown ref {ref}")
        return 1
    shown = _git(root, "show", f"{ref}:{BASELINE_NAME}")
    if shown.returncode:
        print(f"note: no {BASELINE_NAME} at {ref}; baseline growth not checked")
        return 0
    base = parse_baseline(shown.stdout)
    current = load_baseline(root / BASELINE_NAME)
    added = [
        (code, key)
        for code in CODES
        for key in sorted(current.get(code, set()) - base.get(code, set()))
    ]
    changed: set[str] = changed_files(root, ref) if added else set()
    grown = [(code, key) for code, key in added if key.split("::")[0] in changed]
    for code, key in added:
        if (code, key) not in grown:
            print(f"BASELINE ADDED (untouched file): {code} {key}")
    for code, key in grown:
        print(f"BASELINE GREW: {code} {key}")
    if grown:
        print(f"Fix these tests; files changed since {ref} may not grow the baseline.")
        return 1
    print(f"ok: baseline adds no ids in files changed since {ref}")
    return 0


def ratchet(findings: list[Finding], baseline_path: Path) -> int:
    """Compare flagged tests to the baseline by id.

    Return 1 when a flagged test is not listed in the baseline for its code.
    Otherwise return 0.
    """
    baseline = load_baseline(baseline_path)
    counts = count_by_code(findings)
    failed = False
    fixed = False
    for code in CODES:
        known = baseline.get(code, set())
        flagged = [f for f in findings if f.code == code]
        offenders = [f for f in flagged if f.key not in known]
        fixed = fixed or bool(known - {f.key for f in flagged})
        failed = failed or bool(offenders)
        status = "VACUITY FAIL" if offenders else "ok"
        print(f"{status}: {code} = {counts[code]} (baseline {len(known)})")
        for f in offenders:
            print(f"  {f.file}:{f.line} {f.name} {f.code}")
    if failed:
        print("See .claude/rules/test-value.md for each code and its fix.")
        return 1
    if fixed:
        print(
            "Baseline tests no longer flagged. Lock them in: "
            "uv run python scripts/check_test_vacuity.py --update-baseline"
        )
    return 0


def report(findings: list[Finding], *, as_json: bool) -> None:
    """Print every finding and the summary counts."""
    if as_json:
        print(json.dumps([f.to_json() for f in findings], indent=2))
        return
    for f in findings:
        where = f"{f.cls}." if f.cls else ""
        group = f" (group {f.group})" if f.group is not None else ""
        print(f"{f.file}:{f.line} {where}{f.name} {f.code}{group}")
    counts = count_by_code(findings)
    print("\n" + "  ".join(f"{code}={counts[code]}" for code in CODES))


class _Args(argparse.Namespace):
    """Typed CLI arguments."""

    report: bool = False
    update_baseline: bool = False
    json: bool = False
    base_ref: str | None = None
    root: Path = REPO_ROOT


def main(argv: list[str] | None = None) -> int:
    """Run the CLI. Return the process exit code."""
    parser = argparse.ArgumentParser(description="Flag vacuous tests by V-code.")
    mode = parser.add_mutually_exclusive_group()
    _ = mode.add_argument("--report", action="store_true", help="list every flag")
    _ = mode.add_argument(
        "--update-baseline", action="store_true", help="write the flagged ids"
    )
    _ = parser.add_argument("--json", action="store_true", help="JSON with --report")
    _ = parser.add_argument(
        "--base-ref",
        metavar="REF",
        help="ratchet mode: also fail when a test file changed since REF gains a baseline id",
    )
    _ = parser.add_argument("--root", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv, namespace=_Args())
    findings = scan(args.root)
    baseline_path = args.root / BASELINE_NAME
    if args.update_baseline:
        document = build_baseline(findings)
        _ = baseline_path.write_text(json.dumps(document, indent=2) + "\n")
        print(f"wrote {BASELINE_NAME}: {count_by_code(findings)}")
        return 0
    if args.report:
        report(findings, as_json=args.json)
        return 0
    status = ratchet(findings, baseline_path)
    if args.base_ref is not None:
        status = max(status, check_base_growth(args.root, args.base_ref))
    return status


if __name__ == "__main__":
    sys.exit(main())
