"""No mapping statement in the persistence layer may read superseded rows silently.

Migration 044 made ``track_mappings`` append-only. The ORM ``do_orm_execute``
filter covers ORM selects, but Core ``update``/``delete``/textual statements are
outside its reach entirely — and one half-aware reader means a corrected
mapping's ghost resurfaces in the user's library forever.

So this is a source-level guard, not a behavioural one: every statement in the
persistence layer that names ``DBTrackMapping`` inside a ``select`` / ``update``
/ ``delete`` chain must also name :func:`live_only` or opt out explicitly with
``include_superseded``; every textual SQL statement naming ``track_mappings``
must mention ``superseded_at``. It fails on the *next* unscoped query, which is
the point — the cost of finding that one in production is unbounded.
"""

import ast
from pathlib import Path
import re

import pytest

_SRC = Path(__file__).resolve().parents[4] / "src"
# All of ``src``. The guard used to scan two directories that happened to hold
# every mapping statement on the day it was written, which made it a snapshot
# rather than a rule — the next unscoped query would land in a use case, a CLI
# command, or a new service and the build would stay green.
_SCANNED_ROOTS = (_SRC,)

_MODEL = "DBTrackMapping"
_TABLE = "track_mappings"
_BUILDERS = frozenset({"select", "update", "delete"})
_LIVE_TOKENS = ("live_only(", "include_superseded", "INCLUDE_SUPERSEDED")

# An inline opt-out for a SQL statement that genuinely must span both live and
# retired rows. Spelled out in full so it reads as a decision in the diff, and
# it must carry a reason after the marker.
_SQL_EXEMPTION = "live-rows-exempt:"

# Statement nodes to inspect. Compound statements (if/with/for) are excluded
# because their source segment contains their body: a non-conforming inner
# statement would be reported twice, never hidden.
_LEAF_STATEMENTS = (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr, ast.Return)


def _python_sources() -> list[Path]:
    return sorted(path for root in _SCANNED_ROOTS for path in root.rglob("*.py"))


def _mentions_builder_over_model(segment: str) -> bool:
    """True when the statement builds a Core/ORM statement naming the mapping model."""
    if _MODEL not in segment:
        return False
    return any(f"{builder}(" in segment for builder in _BUILDERS)


def _is_docstring(node: ast.stmt) -> bool:
    """True for a bare string expression — a docstring or a block comment."""
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _offending_statements(source: str, tree: ast.Module) -> list[tuple[int, str]]:
    offenders: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, _LEAF_STATEMENTS):
            continue
        if _is_docstring(node):
            # A docstring is prose, not a statement — the same reason the SQL
            # scan looks only inside ``text(...)``. Widening the scan to all of
            # ``src`` made this matter: the modules that *explain* the live-rows
            # rule naturally name the model and the builders in their prose.
            continue
        segment = ast.get_source_segment(source, node)
        if segment is None:
            continue
        if _mentions_builder_over_model(segment) and not any(
            token in segment for token in _LIVE_TOKENS
        ):
            offenders.append((node.lineno, segment.splitlines()[0].strip()))
    return offenders


def _sql_fragments(sql: str) -> list[str]:
    """Split a SQL literal into the pieces that each need their own scoping.

    Statement-level was too coarse. One ``text()`` literal can hold a whole CTE
    chain whose arms write different rows, and a single ``superseded_at``
    anywhere in it exempted every arm — including the one that quietly rewrote
    history. Splitting on statement separators and on CTE-arm boundaries makes
    each writer answer for itself.
    """
    fragments: list[str] = []
    for statement in sql.split(";"):
        # ``AS (`` starts a CTE arm; the split leaves each arm as its own
        # fragment, with the preceding arm's tail attached — harmless, since a
        # fragment only ever gains tokens by being larger, and the arm that
        # names the table is the one whose own text must scope it.
        parts = re.split(r"\bAS\s*\(", statement, flags=re.IGNORECASE)
        fragments.extend(parts if len(parts) > 1 else [statement])
    return [fragment for fragment in fragments if fragment.strip()]


def _offending_textual_sql(source: str, tree: ast.Module) -> list[tuple[int, str]]:
    """Textual SQL touching track_mappings must state its supersession scope.

    Scoped to ``text(...)`` literals rather than every string in the module, so
    a docstring that merely names the table is prose, not an unscoped query —
    and evaluated per *fragment* (see :func:`_sql_fragments`) rather than per
    literal, so a multi-arm CTE cannot smuggle an unscoped writer past one
    scoped sibling.
    """
    offenders: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "text"):
            continue
        for argument in node.args:
            if not (
                isinstance(argument, ast.Constant) and isinstance(argument.value, str)
            ):
                continue
            for fragment in _sql_fragments(argument.value):
                if _TABLE not in fragment:
                    continue
                if "superseded_at" in fragment or _SQL_EXEMPTION in fragment:
                    continue
                offenders.append((
                    argument.lineno,
                    fragment.strip().splitlines()[0][:80],
                ))
    return offenders


@pytest.mark.parametrize("path", _python_sources(), ids=lambda p: p.name)
def test_core_mapping_statements_are_live_scoped(path: Path):
    source = path.read_text(encoding="utf-8")
    offenders = _offending_statements(source, ast.parse(source))
    assert not offenders, (
        f"{path}: statement(s) touching {_MODEL} without live_only() or "
        f"include_superseded: {offenders}"
    )


@pytest.mark.parametrize("path", _python_sources(), ids=lambda p: p.name)
def test_textual_mapping_sql_is_live_scoped(path: Path):
    source = path.read_text(encoding="utf-8")
    offenders = _offending_textual_sql(source, ast.parse(source))
    assert not offenders, (
        f"{path}: raw SQL touching {_TABLE} without a superseded_at predicate: "
        f"{offenders}"
    )


def test_the_guard_can_actually_fail():
    """A scan that never fails is decoration; prove it catches the real shape."""
    unscoped = (
        "from x import DBTrackMapping, select\n"
        "def f(session):\n"
        "    return session.execute(select(DBTrackMapping))\n"
    )
    assert _offending_statements(unscoped, ast.parse(unscoped))

    textual = 'from x import text\nQ = text("UPDATE track_mappings SET x = 1")\n'
    assert _offending_textual_sql(textual, ast.parse(textual))


def test_one_scoped_cte_arm_does_not_exempt_its_siblings():
    """The failure the per-statement check missed: a chain with one blind arm."""
    chain = (
        "from x import text\n"
        'Q = text("""\n'
        "WITH live AS (\n"
        "  SELECT id FROM track_mappings WHERE superseded_at IS NULL\n"
        "),\n"
        "blind AS (\n"
        "  UPDATE track_mappings SET origin = 'manual' WHERE track_id = :t\n"
        "  RETURNING id\n"
        ")\n"
        "SELECT count(*) FROM blind\n"
        '""")\n'
    )
    offenders = _offending_textual_sql(chain, ast.parse(chain))
    assert offenders, "an unscoped CTE arm must be reported"
    assert any("UPDATE track_mappings" in line for _, line in offenders)


def test_an_explicit_exemption_is_honoured():
    """History reads are legitimate — they just have to say so in the SQL."""
    exempted = (
        "from x import text\n"
        'Q = text("""\n'
        "-- live-rows-exempt: chain walk deliberately spans retired rows\n"
        "SELECT id FROM track_mappings WHERE id = :id\n"
        '""")\n'
    )
    assert not _offending_textual_sql(exempted, ast.parse(exempted))
