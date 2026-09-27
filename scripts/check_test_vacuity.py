#!/usr/bin/env python3
"""Flag vacuous tests by V-code and ratchet them against a baseline.

Parse every ``tests/**/test_*.py`` file with ``ast``. Classify each test function by
the banned patterns of ``.claude/rules/test-value.md`` that syntax can decide: V1, V2,
V3, V5, V7, V9, and DUP (identical normalized test bodies). V4, V6, V8, and V10 need
judgment and stay with audit agents. The default mode fails when a flagged test is not
listed for its code in ``tests/.vacuity_baseline.json``.

Usage:
    uv run python scripts/check_test_vacuity.py                    # ratchet
    uv run python scripts/check_test_vacuity.py --report [--json]  # list every flag
    uv run python scripts/check_test_vacuity.py --update-baseline
"""

import argparse
import ast
from collections import ChainMap, defaultdict
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
import hashlib
import json
from pathlib import Path
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
_TIMING_SUFFIXES = ("_ms", "_time", "duration", "elapsed")


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


def _is_helper_assert_call(call: ast.Call) -> bool:
    name = _call_name(call.func)
    return name is not None and name.startswith(_HELPER_PREFIXES)


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


def _is_existence(expr: ast.expr) -> bool:
    """Return True for existence/type checks that pin no value (V3)."""
    match expr:
        case ast.Name():
            return True
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
            return all(_is_existence(v) for v in values)
        case _:
            return False


def _is_enum_pin(expr: ast.expr) -> bool:
    """Return True for ``X.Y.value == <str/int literal>`` (V5)."""
    match expr:
        case ast.Compare(left=left, ops=[ast.Eq()], comparators=[right]):
            pass
        case _:
            return False
    for member, literal in ((left, right), (right, left)):
        match member, literal:
            case (
                ast.Attribute(
                    attr="value",
                    value=ast.Attribute(value=ast.Name() | ast.Attribute()),
                ),
                ast.Constant(value=str() | int() as value),
            ) if not isinstance(value, bool):
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


def _classify_assert(expr: ast.expr) -> Check:
    if _is_timing_bound(expr):
        return Check.TIMING
    if _references_mock_attr(expr):
        return Check.MOCK_ARGS if _checks_mock_args(expr) else Check.MOCK
    if _is_enum_pin(expr):
        return Check.ENUM_PIN
    if _is_existence(expr):
        return Check.EXISTENCE
    return Check.REAL


def _is_timing_bound(expr: ast.expr) -> bool:
    """Return True for ``<x_ms|x_time|duration|elapsed> >= 0`` or ``> 0`` (V9)."""
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
    name = _call_name(subject)
    return name is not None and name.endswith(_TIMING_SUFFIXES)


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
    one counts as a real check.
    """
    return _scan_checks(stmts, helpers)[0]


def _scan_checks(
    stmts: list[ast.stmt], helpers: frozenset[str]
) -> tuple[list[Check], set[str]]:
    """Return the checks in ``stmts`` and the names of all functions they call."""
    checks: list[Check] = []
    called: set[str] = set()
    consumed: set[int] = set()
    for node in ast.walk(ast.Module(body=stmts, type_ignores=[])):
        if isinstance(node, ast.Call) and (name := _call_name(node.func)):
            called.add(name)
        match node:
            case ast.Assert(test=test):
                checks.append(_classify_assert(test))
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
                elif (
                    _is_pytest_check(node)
                    or _is_helper_assert_call(node)
                    or _call_name(node.func) in helpers
                ):
                    checks.append(Check.REAL)
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
    """One patched attribute and the owner it was patched on."""

    name: str
    owner: str
    owner_is_module: bool


def _patch_from_call(call: ast.Call, modules: frozenset[str]) -> _Patch | None:
    dotted = _dotted(call.func)
    first = call.args[0] if call.args else None
    second = call.args[1] if len(call.args) > 1 else None
    target = _str_const(first)
    if dotted in {"patch", "mock.patch", "unittest.mock.patch", "mocker.patch"} or (
        dotted == "monkeypatch.setattr" and target is not None
    ):
        if target is None or "." not in target:
            return None
        *path, name = target.split(".")
        owner = path[-1]
        return _Patch(name, owner, owner_is_module=not owner[:1].isupper())
    if dotted.endswith(("patch.object", "monkeypatch.setattr")) and first is not None:
        name = _str_const(second)
        owner = _dotted(first)
        if name is None or not owner or owner in modules:
            return None
        return _Patch(name, owner.rsplit(".", 1)[-1], owner_is_module=False)
    return None


def _receiver_matches(receiver: ast.expr, owner: str, bound: dict[str, str]) -> bool:
    """Return True when ``receiver`` is ``owner``, ``owner(...)``, or bound to it."""
    match receiver:
        case ast.Name(id=name):
            return owner in {name, bound.get(name)}
        case ast.Attribute(attr=attr):
            return attr == owner
        case ast.Call(func=func):
            return _call_name(func) == owner
        case _:
            return False


def patches_unit_under_test(fn: FuncDef, modules: frozenset[str]) -> bool:
    """Return True when the test patches a callable that it then calls directly (V7).

    A module-level patch matches a bare call of the same name. A class or object
    patch matches a method call whose receiver is that owner, an instance built
    from it in the test, or the owner itself. The method name must be the patched
    name, or the patched name must be private (the test stubs out the unit's own
    internals and then drives the unit).
    """
    body = _walk_shallow(fn.body)
    patches: list[_Patch] = []
    patch_calls: set[int] = set()
    for node in [*fn.decorator_list, *body]:
        if isinstance(node, ast.Call) and (patch := _patch_from_call(node, modules)):
            patch_calls.add(id(node))
            if not patch.name.startswith("__"):
                patches.append(patch)
    if not patches:
        return False
    bound: dict[str, str] = {}
    for node in body:
        match node:
            case ast.Assign(targets=[ast.Name(id=var)], value=ast.Call(func=func)):
                if cls_name := _call_name(func):
                    bound[var] = cls_name
            case _:
                pass
    for node in body:
        if not isinstance(node, ast.Call) or id(node) in patch_calls:
            continue
        for patch in patches:
            match node.func:
                case ast.Name(id=name) if patch.owner_is_module and name == patch.name:
                    return True
                case ast.Attribute(attr=attr, value=receiver) if (
                    not patch.owner_is_module
                    and (attr == patch.name or patch.name.startswith("_"))
                    and _receiver_matches(receiver, patch.owner, bound)
                ):
                    return True
                case _:
                    pass
    return False


# --- DUP: normalized body fingerprints ---------------------------------------


def _dump(
    node: object,
    qualified: Mapping[str, str],
    self_attrs: Mapping[str, str],
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
            out.append(f"self.({self_attrs.get(attr, attr)!r},{type(ctx).__name__})")
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
    self_attrs: Mapping[str, str],
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


def _asserting_helpers(tree: ast.Module) -> frozenset[str]:
    """Return names of non-test functions and methods that contain checks.

    A function counts when it checks directly or calls a function that counts.
    """
    direct: set[str] = set()
    callers: dict[str, set[str]] = defaultdict(set)
    for node in ast.walk(tree):
        if isinstance(
            node, ast.FunctionDef | ast.AsyncFunctionDef
        ) and not node.name.startswith("test_"):
            checks, called = _scan_checks(node.body, frozenset())
            if checks:
                direct.add(node.name)
            for name in called:
                callers[name].add(node.name)
    found = set(direct)
    pending = list(direct)
    while pending:
        for caller in callers[pending.pop()] - found:
            found.add(caller)
            pending.append(caller)
    return frozenset(found)


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


def _class_names(
    cls: ast.ClassDef | None, cls_path: str | None, rel: str, fixtures: dict[str, str]
) -> tuple[dict[str, str], dict[str, str]]:
    """Return the fixtures visible in the class and its ``self.`` method names."""
    fixtures = dict(fixtures)
    self_attrs: dict[str, str] = {}
    if cls is not None:
        for stmt in cls.body:
            if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
                self_attrs[stmt.name] = f"{rel}::{cls_path}.{stmt.name}"
                if _is_fixture(stmt):
                    fixtures[stmt.name] = self_attrs[stmt.name]
    return fixtures, self_attrs


type Print = tuple[str, Finding]


def analyze_source(source: str, rel: str) -> tuple[list[Finding], list[Print]]:
    """Return per-test findings and DUP fingerprints for one test module.

    Each fingerprint comes with a DUP finding for its test, without a group.
    """
    tree = ast.parse(source, filename=rel)
    scope, modules = _module_scope(tree, rel)
    helpers = _asserting_helpers(tree)
    module_fixtures = {
        stmt.name: f"{rel}::{stmt.name}"
        for stmt in tree.body
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef)
        and _is_fixture(stmt)
    }
    directory = rel.rsplit("/", 1)[0]
    class_names: dict[str | None, tuple[dict[str, str], dict[str, str]]] = {}
    findings: list[Finding] = []
    prints: list[Print] = []
    for node_id, fn, cls in iter_tests(tree):
        cls_path = node_id.rpartition("::")[0] or None
        if cls_path not in class_names:
            class_names[cls_path] = _class_names(cls, cls_path, rel, module_fixtures)
        fixtures, self_attrs = class_names[cls_path]
        test = Finding(rel, fn.lineno, fn.name, cls_path, "DUP")

        checks = collect_checks(fn.body, helpers)
        if shape := classify_checks(checks):
            findings.append(replace(test, code=shape))
        if patches_unit_under_test(fn, modules):
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


def load_baseline(path: Path) -> dict[str, set[str]]:
    """Read the flagged test ids per code. A missing file gives none."""
    if not path.exists():
        return {}
    document = cast(
        "dict[str, dict[str, list[str]]]",
        json.loads(path.read_text(encoding="utf-8")),
    )
    return {code: set(ids) for code, ids in document["tests"].items()}


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
    return ratchet(findings, baseline_path)


if __name__ == "__main__":
    sys.exit(main())
