#!/usr/bin/env python3
"""Flag vacuous tests by V-code and ratchet their counts.

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
from collections import defaultdict
import copy
from dataclasses import asdict, dataclass
from enum import StrEnum
import hashlib
import json
from pathlib import Path
import sys
from typing import cast, override

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
_MOCK_ATTRS = frozenset({
    "called",
    "call_count",
    "call_args",
    "call_args_list",
    "await_count",
    "await_args",
    "await_args_list",
    "awaited",
    "mock_calls",
    "method_calls",
})
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


@dataclass(frozen=True, slots=True)
class Finding:
    """One flagged test and the V-code it violates."""

    file: str
    line: int
    name: str
    cls: str | None
    code: str
    group: int | None = None

    @property
    def key(self) -> str:
        """Line-independent identity for baseline comparison."""
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
    checks: list[Check] = []
    consumed: set[int] = set()
    for node in ast.walk(ast.Module(body=stmts, type_ignores=[])):
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
    return checks


def classify_checks(checks: list[Check]) -> str | None:
    """Return V1, V2, V3, or V5 for the assertion shape, or None when it is sound.

    V2 and V3 need every mock check to be bare. A check of call arguments may be
    the contract, so audit agents judge those tests.
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


class _Normalizer(ast.NodeTransformer):
    """Rewrite names so that equal fingerprints mean equal behavior."""

    def __init__(
        self,
        qualified: dict[str, str],
        self_attrs: dict[str, str],
    ) -> None:
        self.qualified: dict[str, str] = qualified
        self.self_attrs: dict[str, str] = self_attrs

    @override
    def visit_Name(self, node: ast.Name) -> ast.Name:
        node.id = self.qualified.get(node.id, node.id)
        return node

    @override
    def visit_arg(self, node: ast.arg) -> ast.arg:
        node.arg = self.qualified.get(node.arg, node.arg)
        node.annotation = None
        return node

    @override
    def visit_Attribute(self, node: ast.Attribute) -> ast.Attribute:
        if isinstance(node.value, ast.Name) and node.value.id == "self":
            node.attr = self.self_attrs.get(node.attr, node.attr)
            return node
        self.generic_visit(node)
        return node


def fingerprint(
    fn: FuncDef,
    qualified: dict[str, str],
    self_attrs: dict[str, str],
) -> str:
    """Hash the test with its name, docstring, and annotations removed."""
    node = copy.deepcopy(fn)
    node.name = "_"
    node.returns = None
    body = node.body
    if body and isinstance(body[0], ast.Expr) and _str_const(body[0].value) is not None:
        body = body[1:] or [ast.Pass()]
    node.body = body
    # The visitors rewrite nodes in place and return them, so ``node`` is the result.
    _Normalizer(qualified, self_attrs).visit(node)
    dump = ast.dump(node, annotate_fields=False, include_attributes=False)
    return hashlib.sha256(dump.encode()).hexdigest()


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
    """Return names of non-test functions and methods that contain checks."""
    defs: dict[str, FuncDef] = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and not node.name.startswith("test_")
    }
    found: set[str] = set()
    changed = True
    while changed:
        changed = False
        for name, fn in defs.items():
            if name not in found and collect_checks(fn.body, frozenset(found)):
                found.add(name)
                changed = True
    return frozenset(found)


def _iter_tests(tree: ast.Module) -> list[tuple[FuncDef, ast.ClassDef | None]]:
    tests: list[tuple[FuncDef, ast.ClassDef | None]] = []

    def visit(body: list[ast.stmt], cls: ast.ClassDef | None) -> None:
        for stmt in body:
            if isinstance(
                stmt, ast.FunctionDef | ast.AsyncFunctionDef
            ) and stmt.name.startswith("test"):
                tests.append((stmt, cls))
            elif isinstance(stmt, ast.ClassDef) and stmt.name.startswith("Test"):
                visit(stmt.body, stmt)

    visit(tree.body, None)
    return tests


@dataclass(frozen=True, slots=True)
class _Print:
    """One test's DUP fingerprint and its location."""

    digest: str
    file: str
    line: int
    name: str
    cls: str | None


def analyze_source(source: str, rel: str) -> tuple[list[Finding], list[_Print]]:
    """Return per-test findings and DUP fingerprints for one test module."""
    tree = ast.parse(source, filename=rel)
    scope, modules = _module_scope(tree, rel)
    helpers = _asserting_helpers(tree)
    module_fixtures = {
        stmt.name
        for stmt in tree.body
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef)
        and _is_fixture(stmt)
    }
    directory = rel.rsplit("/", 1)[0]
    findings: list[Finding] = []
    prints: list[_Print] = []
    for fn, cls in _iter_tests(tree):
        cls_name = cls.name if cls else None

        def flag(code: str, fn: FuncDef = fn, cls_name: str | None = cls_name) -> None:
            findings.append(Finding(rel, fn.lineno, fn.name, cls_name, code))

        if shape := classify_checks(collect_checks(fn.body, helpers)):
            flag(shape)
        if patches_unit_under_test(fn, modules):
            flag("V7")
        if any(
            isinstance(node, ast.Assert) and _is_timing_bound(node.test)
            for node in ast.walk(fn)
        ):
            flag("V9")

        qualified = dict(scope)
        for arg in fn.args.args:
            if arg.arg not in {"self", "cls"}:
                qualified[arg.arg] = f"{directory}/fixture::{arg.arg}"
        qualified.update({f: f"{rel}::{f}" for f in module_fixtures})
        self_attrs: dict[str, str] = {}
        if cls is not None:
            for stmt in cls.body:
                if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
                    self_attrs[stmt.name] = f"{rel}::{cls.name}.{stmt.name}"
                    if _is_fixture(stmt):
                        qualified[stmt.name] = f"{rel}::{cls.name}.{stmt.name}"
        digest = fingerprint(fn, qualified, self_attrs)
        prints.append(_Print(digest, rel, fn.lineno, fn.name, cls_name))
    return findings, prints


def duplicate_findings(prints: list[_Print]) -> list[Finding]:
    """Return a DUP finding for every test whose fingerprint another test shares."""
    groups: dict[str, list[_Print]] = defaultdict(list)
    for p in prints:
        groups[p.digest].append(p)
    dup_groups = sorted(
        (members for members in groups.values() if len(members) > 1),
        key=lambda members: (members[0].file, members[0].line),
    )
    return [
        Finding(p.file, p.line, p.name, p.cls, "DUP", group=number)
        for number, members in enumerate(dup_groups, start=1)
        for p in members
    ]


def scan(root: Path) -> list[Finding]:
    """Scan every test module under ``root/tests`` and return sorted findings."""
    findings: list[Finding] = []
    prints: list[_Print] = []
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


def build_baseline(findings: list[Finding]) -> dict[str, object]:
    """Return the baseline document: counts plus the flagged test identities."""
    tests: dict[str, list[str]] = {code: [] for code in CODES}
    for finding in findings:
        tests[finding.code].append(finding.key)
    return {
        "counts": count_by_code(findings),
        "tests": {code: sorted(set(keys)) for code, keys in tests.items()},
    }


def _json_dict(value: object) -> dict[str, object]:
    """Return ``value`` as a string-keyed dict, or an empty dict for other JSON."""
    if not isinstance(value, dict):
        return {}
    items = cast("dict[object, object]", value).items()
    return {k: v for k, v in items if isinstance(k, str)}


def load_baseline(path: Path) -> tuple[dict[str, int], dict[str, set[str]]]:
    """Read the baseline counts and identities. A missing file gives empty ones."""
    if not path.exists():
        return {}, {}
    loaded = cast("object", json.loads(path.read_text(encoding="utf-8")))
    raw = _json_dict(loaded)
    counts = {
        code: value
        for code, value in _json_dict(raw.get("counts")).items()
        if isinstance(value, int)
    }
    tests: dict[str, set[str]] = {}
    for code, keys in _json_dict(raw.get("tests")).items():
        if isinstance(keys, list):
            members = cast("list[object]", keys)
            tests[code] = {k for k in members if isinstance(k, str)}
    return counts, tests


def ratchet(findings: list[Finding], baseline_path: Path) -> int:
    """Compare flagged tests to the baseline by identity.

    Return 1 when a flagged test is not listed in the baseline for its code, or
    when a count rises. Otherwise return 0.
    """
    base_counts, base_tests = load_baseline(baseline_path)
    counts = count_by_code(findings)
    failed = False
    fixed = False
    for code in CODES:
        current, allowed = counts[code], base_counts.get(code, 0)
        known = base_tests.get(code, set())
        offenders = [f for f in findings if f.code == code and f.key not in known]
        flagged = {f.key for f in findings if f.code == code}
        fixed = fixed or bool(known - flagged)
        if offenders or current > allowed:
            failed = True
            print(f"VACUITY FAIL: {code} = {current} (baseline {allowed})")
            for f in offenders:
                print(f"  {f.file}:{f.line} {f.name} {f.code}")
        else:
            print(f"ok: {code} = {current} (baseline {allowed})")
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
        "--update-baseline", action="store_true", help="write current counts"
    )
    _ = parser.add_argument("--json", action="store_true", help="JSON with --report")
    _ = parser.add_argument("--root", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv, namespace=_Args())
    findings = scan(args.root)
    baseline_path = args.root / BASELINE_NAME
    if args.update_baseline:
        document = build_baseline(findings)
        _ = baseline_path.write_text(json.dumps(document, indent=2) + "\n")
        print(f"wrote {BASELINE_NAME}: {document['counts']}")
        return 0
    if args.report:
        report(findings, as_json=args.json)
        return 0
    return ratchet(findings, baseline_path)


if __name__ == "__main__":
    sys.exit(main())
