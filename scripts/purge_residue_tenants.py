#!/usr/bin/env python3
"""Delete a residue tenant — a ``user_id`` that is not, and never was, an account.

Prod carries three of these, from three separate accidents:

- ``default`` — the pre-multi-user single-tenant library (2026-05-10), plus a
  July CLI-against-prod invocation that added a schedule, an ``operation_runs``
  row and a Spotify OAuth token.
- ``repro_7d81986c`` / ``repro_d46f7460`` — one track and one play each, written
  2026-07-17 by a reproduction script pointed at prod.

``DEFAULT_USER_ID`` is local-dev scaffolding (``config/constants.py``), so rows
under it on a hosted database are always an accident. As of v0.10.4.1
``set_rls_user_on_begin`` refuses to open a ``default`` transaction against a
non-local database, which closes the door; this script removes what already
came through it.

Why this is not ``DELETE FROM tracks WHERE user_id = 'default'``
---------------------------------------------------------------
Because that is the shape that already cost this project data, and because a
first version of this script was written, audited and condemned before it ever
ran. Its defects are the design of this one.

**Tenancy is not "has a ``user_id`` column".** ``playlist_tracks`` has none: it
is tenanted through ``playlist_id -> playlists.user_id``, and *both* its FKs are
``ON DELETE CASCADE``. A cross-tenant check keyed on the column skips the table
entirely — so the v0.10.3 finding-C3 shape (the real user's playlist holding a
``default``-owned track) reads as "no cross-tenant references" and the delete
proceeds, taking rows nobody named. Same class: ``workflow_runs`` /
``workflow_versions`` / ``workflow_run_nodes`` carry no ``user_id`` either, and
``workflow_runs.triggered_by_schedule_id`` is ``ON DELETE SET NULL`` — deleting
this tenant's schedule silently *edits* another tenant's run row.

So the unit of reasoning here is not the table but the **cascade closure**: the
set of rows a ``DELETE`` would actually remove, computed by walking
``ON DELETE CASCADE`` edges out from the tenant's own rows, transitively,
through intermediary tables that carry no tenancy of their own. Every predicate
in this script — the safety check, the backup, the delete, the after-check — is
derived from that one closure definition, built from ``pg_constraint`` at
runtime rather than from a hand-maintained table list that drifts.

Two questions are then asked of it, and both are refusals:

1. **Is any row inside the closure attributed to another tenant?** Attribution
   is a row's own ``user_id`` when it has one, and otherwise the attribution of
   its FK parents, recursively. The real user's playlist row holding a
   ``default`` track is inside the closure (via ``track_id``) and attributed
   elsewhere (via ``playlist_id``) — a cascade blast, refused.
2. **Does any row outside the closure reference a row inside it through a
   non-CASCADE FK?** ``SET NULL`` would mutate a stranger's row; ``RESTRICT``
   would abort the delete halfway. Both refused.

Together those are exhaustive over the FK graph: anything CASCADE-reachable is
*in* the closure and answered by (1), anything else that points into it is
answered by (2).

**RLS is configured on every tenant table and enforced on none.** v0.10.3.6: a
repair script that leaned on the session for tenant isolation reattached 34 rows
to the wrong account in production. Every statement here carries its own
explicit tenancy predicate; nothing is left to the session. And because that
only holds while this connection can see *all* rows, the script refuses to run
as a role whose row visibility is filtered — a role that sees half the database
would find no cross-tenant references and delete anyway, while the cascade
(which runs as the table owner and ignores RLS) took the rest.

**A count is not an equivalence.** ``default``'s playlists have namesakes under
the real user with *more* tracks (568 vs 430), which is evidence of
supersession, not proof of it. ``--apply`` therefore writes a JSON backup of
every row it is about to delete — **including the ones only the cascade would
have taken**, which is the second half of the condemned draft's backup bug: a
backup that omits the 555 ``playlist_tracks`` rows restores two empty
playlists. Rows are serialized with ``to_jsonb`` in the database rather than
``str()`` in Python, so NULL stays null and the 28 jsonb/array columns come
back as jsonb and arrays instead of Python ``repr``.

**Fail closed, and stay closed for the length of the transaction.** An error
inside a safety check aborts; it is never caught and skipped, because a
statement timeout on a 56k-row join that "found no references" is
indistinguishable from safety. Check, backup and delete run in one
``SERIALIZABLE`` transaction under an advisory lock, so a reference cannot
appear between the check and the delete — prod runs an hourly
``sync:spotify:plays`` poller and two enabled schedules for the real user, and
a play written in that window would otherwise be deleted unexamined. A dry run
additionally opens ``READ ONLY``, so the rehearsal cannot write even if the
code is wrong.

Why raw psycopg rather than the ORM
-----------------------------------
The work is a graph traversal over ``pg_constraint`` producing composed SQL for
tables the models do not enumerate; there is no ORM expression of it. Two
consequences are deliberate:

- The SQLAlchemy ``after_begin`` guard never fires here, so this script does not
  need ``system_context()`` to declare itself cross-tenant maintenance — but it
  *is* that, and the allowlist below is what stands in for the declaration.
- ``app.user_id`` is never set on this connection. Under an RLS-enforcing role
  every policy would evaluate against NULL and the script would see nothing —
  safe, but blind, which is why the visibility check refuses rather than
  reporting an empty database.

Refusals (there is no override flag for any of them)
----------------------------------------------------
- the tenant is not in ``_KNOWN_RESIDUE`` — this script never deletes an
  arbitrary user, and the allowlist is the reason it may run cross-tenant at all
- the connecting role's row visibility is filtered by RLS
- the schema carries a multi-column FK, or a self-referential ``CASCADE``, or an
  FK chain deeper than ``_MAX_DEPTH`` — all three are shapes whose closure this
  code cannot express, and an unexpressible closure is not a safe one
- a ``uuid`` column with no FK constraint is not declared in
  ``_BY_VALUE_REFERENCES`` — an undeclared by-value reference is invisible to
  ``pg_constraint`` and would go unreported
- the tenant has a schedule in any state but ``disabled``/``failed``, or any
  row anywhere with a
  timestamp newer than ``--stale-days`` (which may only be raised above its
  floor — a flag that can lower it is an override flag with a friendlier name)
- a row inside the cascade closure is attributed to another tenant
- a row inside the cascade closure is attributed to nobody
- a row outside the closure references a row inside it through a non-CASCADE FK
- ``--apply`` without ``--backup``, or a ``--backup`` path that already exists
- rows attributed to the tenant survive the delete

Reported but not refused: rows outside the closure that name rows inside it *by
value* (``resolution_events.track_id``, ``workflow_runs.output_playlist_id``,
``schedules.last_run_id``). They have no FK, so nothing cascades and nothing is
mutated; they become dangling ids in append-only history, which is what
``reown_cross_tenant_tracks.py`` already decided about ``resolution_events``.

Dry-run by default — prints the full plan without writing. Pass ``--apply``.

Usage:
    uv run python scripts/purge_residue_tenants.py --tenant default
    uv run python scripts/purge_residue_tenants.py --tenant default \
        --backup ./default-backup.json --apply
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.rows import DictRow, dict_row
import typer

from src.config import get_logger, setup_script_logger

logger = get_logger(__name__)

type Conn = psycopg.Connection[DictRow]
type Params = dict[str, object]

# This script deletes tenants; it will not take a name it was not told about.
_KNOWN_RESIDUE: frozenset[str] = frozenset({
    "default",
    "repro_7d81986c",
    "repro_d46f7460",
})

_DEFAULT_STALE_DAYS: int = 14

# The cascade closure is built by recursion; a chain longer than this is a
# schema this code has never seen and cannot claim to have walked completely.
_MAX_DEPTH: int = 8

# Advisory-lock namespace: 'mxdp' as an int4. The second key is the tenant, so
# two purges of different tenants do not block each other.
_LOCK_NAMESPACE: int = 0x6D786470

# The joins below hit 56k-row tables; the point of a budget is that exhausting
# it raises rather than returning a comfortable zero.
_STATEMENT_TIMEOUT: str = "300s"

# ``uuid`` columns with no FK constraint: invisible to ``pg_constraint``, so
# they are declared here or the script refuses. The value is the tables whose
# ids the column may hold — empty means "not a row reference at all".
#
# ``schedules.last_run_id`` is polymorphic on purpose (see the column comment in
# ``db_models.py``): a workflow schedule stores a ``workflow_runs.id``, a sync
# schedule an ``operation_runs.id``, and no single FK can express that.
_BY_VALUE_REFERENCES: Mapping[tuple[str, str], tuple[str, ...]] = {
    ("resolution_events", "track_id"): ("tracks",),
    ("resolution_events", "connector_track_id"): ("connector_tracks",),
    ("resolution_events", "resulting_mapping_id"): ("track_mappings",),
    # A batch correlation id for an offline re-resolution run, not a row id.
    ("resolution_events", "run_id"): (),
    ("schedules", "last_run_id"): ("workflow_runs", "operation_runs"),
    ("workflow_runs", "output_playlist_id"): ("playlists",),
    # Rotation family, not a row id — every token in a family names it.
    ("oauth_refresh_tokens", "family_id"): (),
}

_LIVE_SCHEDULE_STATES: tuple[str, ...] = ("disabled", "failed")


class PurgeRefusedError(RuntimeError):
    """A condition this script will not proceed through. No override exists."""


# ---------------------------------------------------------------- schema ----


@dataclass(frozen=True, slots=True)
class ForeignKey:
    """One single-column FK edge, child -> parent, with its referential action."""

    name: str
    child: str
    child_column: str
    parent: str
    parent_column: str
    # pg_constraint.confdeltype: a=NO ACTION, r=RESTRICT, c=CASCADE,
    # n=SET NULL, d=SET DEFAULT
    on_delete: str

    @property
    def cascades(self) -> bool:
        return self.on_delete == "c"

    def describe(self) -> str:
        action = {
            "a": "NO ACTION",
            "r": "RESTRICT",
            "c": "CASCADE",
            "n": "SET NULL",
            "d": "SET DEFAULT",
        }.get(self.on_delete, self.on_delete)
        return (
            f"{self.child}.{self.child_column} -> "
            f"{self.parent}.{self.parent_column} ON DELETE {action}"
        )


@dataclass(frozen=True, slots=True)
class Schema:
    """The FK graph and tenancy facts, read from the live catalog."""

    tables: frozenset[str]
    user_scoped: frozenset[str]
    foreign_keys: tuple[ForeignKey, ...]
    timestamp_columns: Mapping[str, tuple[str, ...]]
    primary_key: Mapping[str, str]

    def parents_of(self, table: str) -> tuple[ForeignKey, ...]:
        return tuple(fk for fk in self.foreign_keys if fk.child == table)


def load_schema(conn: Conn) -> Schema:
    """Read the FK graph, refusing every shape whose closure is inexpressible."""
    multi = conn.execute("""
        SELECT con.conname AS name, c.relname AS child
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
        WHERE con.contype = 'f' AND array_length(con.conkey, 1) > 1
    """).fetchall()
    if multi:
        listed = ", ".join(f"{r['child']} ({r['name']})" for r in multi)
        raise PurgeRefusedError(
            f"multi-column foreign key(s) present: {listed}. This script walks "
            f"single-column edges only; a composite edge it silently skipped "
            f"would be a hole in the cascade check, so it stops instead."
        )

    fk_rows = conn.execute("""
        SELECT con.conname AS name,
               c.relname AS child, ca.attname AS child_column,
               p.relname AS parent, pa.attname AS parent_column,
               con.confdeltype AS on_delete
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
        JOIN pg_class p ON p.oid = con.confrelid
        JOIN pg_attribute ca
             ON ca.attrelid = con.conrelid AND ca.attnum = con.conkey[1]
        JOIN pg_attribute pa
             ON pa.attrelid = con.confrelid AND pa.attnum = con.confkey[1]
        WHERE con.contype = 'f'
        ORDER BY 1
    """).fetchall()
    foreign_keys = tuple(
        ForeignKey(
            name=str(r["name"]),
            child=str(r["child"]),
            child_column=str(r["child_column"]),
            parent=str(r["parent"]),
            parent_column=str(r["parent_column"]),
            on_delete=str(r["on_delete"]),
        )
        for r in fk_rows
    )
    for fk in foreign_keys:
        if fk.child == fk.parent and fk.cascades:
            raise PurgeRefusedError(
                f"self-referential CASCADE: {fk.describe()}. Its closure needs a "
                f"recursive walk this script does not implement."
            )

    table_rows = conn.execute("""
        SELECT c.relname AS name
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
        WHERE c.relkind = 'r'
    """).fetchall()
    tables = frozenset(str(r["name"]) for r in table_rows)

    scoped_rows = conn.execute("""
        SELECT table_name AS name FROM information_schema.columns
        WHERE table_schema = 'public' AND column_name = 'user_id'
    """).fetchall()
    user_scoped = frozenset(str(r["name"]) for r in scoped_rows) & tables

    ts_rows = conn.execute("""
        SELECT table_name AS name, column_name AS col
        FROM information_schema.columns
        WHERE table_schema = 'public' AND data_type LIKE 'timestamp%%'
        ORDER BY 1, 2
    """).fetchall()
    timestamps: dict[str, tuple[str, ...]] = {}
    for r in ts_rows:
        name, col = str(r["name"]), str(r["col"])
        timestamps[name] = (*timestamps.get(name, ()), col)

    pk_rows = conn.execute("""
        SELECT c.relname AS name, a.attname AS col
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
        JOIN pg_attribute a
             ON a.attrelid = con.conrelid AND a.attnum = con.conkey[1]
        WHERE con.contype = 'p' AND array_length(con.conkey, 1) = 1
    """).fetchall()
    primary_key = {str(r["name"]): str(r["col"]) for r in pk_rows}

    return Schema(
        tables=tables,
        user_scoped=user_scoped,
        foreign_keys=foreign_keys,
        timestamp_columns=timestamps,
        primary_key=primary_key,
    )


def assert_undeclared_by_value_columns_are_none(conn: Conn) -> None:
    """Refuse if a ``uuid`` column exists that no FK and no declaration covers.

    ``pg_constraint`` cannot see a reference held by value, so an undeclared one
    would be neither cascaded (fine) nor reported (not fine). Rather than guess
    from the column name, the script insists someone decided.
    """
    rows = conn.execute("""
        SELECT c.relname AS tbl, a.attname AS col
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
        JOIN pg_attribute a
             ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
        JOIN pg_type t ON t.oid = a.atttypid AND t.typname = 'uuid'
        WHERE c.relkind = 'r'
          AND NOT EXISTS (
              SELECT 1 FROM pg_constraint k
              WHERE k.conrelid = c.oid
                AND k.contype IN ('f', 'p')
                AND a.attnum = ANY(k.conkey)
          )
        ORDER BY 1, 2
    """).fetchall()
    undeclared = [
        f"{r['tbl']}.{r['col']}"
        for r in rows
        if (str(r["tbl"]), str(r["col"])) not in _BY_VALUE_REFERENCES
    ]
    if undeclared:
        raise PurgeRefusedError(
            f"undeclared uuid column(s) with no foreign key: "
            f"{', '.join(undeclared)}. Add them to _BY_VALUE_REFERENCES — either "
            f"as the tables whose ids they hold, or as () for 'not a row "
            f"reference' — so the report can account for them."
        )


def assert_rows_are_fully_visible(conn: Conn) -> None:
    """Refuse unless this role sees every row of every table in scope.

    RLS is enabled and FORCEd on 24 prod tables, and the pooled connection sets
    no ``app.user_id``. A role without BYPASSRLS therefore sees a *filtered*
    database — and referential actions run as the table owner and ignore RLS
    entirely, so the cascade would take rows the safety check could not see.
    That is the fail-open shape this whole script exists to avoid.
    """
    row = conn.execute("""
        SELECT r.rolbypassrls AS bypass, r.rolsuper AS super,
               current_setting('row_security') AS row_security,
               current_user AS role
        FROM pg_roles r WHERE r.rolname = current_user
    """).fetchone()
    if row is None:  # pragma: no cover — current_user always has a pg_roles row
        raise PurgeRefusedError("cannot resolve the connecting role from pg_roles.")
    if bool(row["bypass"]) or bool(row["super"]):
        return
    if str(row["row_security"]).lower() == "off":
        # Only a BYPASSRLS/superuser role may set this; anyone else gets an
        # error on the first affected query rather than silent filtering.
        return

    hidden = conn.execute("""
        SELECT c.relname AS name
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
        WHERE c.relkind = 'r' AND c.relrowsecurity
          AND (c.relforcerowsecurity OR pg_get_userbyid(c.relowner) <> current_user)
        ORDER BY 1
    """).fetchall()
    if hidden:
        raise PurgeRefusedError(
            f"role {row['role']!r} has row-level security applied to "
            f"{len(hidden)} table(s) (e.g. {', '.join(str(h['name']) for h in hidden[:3])}). "
            f"Its view of the database is filtered, so the cross-tenant check "
            f"would report on rows it cannot see while the cascade — which runs "
            f"as the table owner and ignores RLS — took the rest. Connect as the "
            f"owning role."
        )


# ------------------------------------------------------------ predicates ----


class Predicates:
    """Composed SQL for the three questions asked of every row.

    ``in_delete_set`` — would a ``DELETE`` of this tenant remove this row, by
    its own tenancy or by cascade from something that would be removed?

    ``attributed_elsewhere`` — does this row belong to a tenant other than the
    target, either by its own ``user_id`` or by any FK parent's?

    ``owned_by_tenant`` — does it belong to the target and to nobody else?

    All three take an alias so they can nest inside ``EXISTS``, and all three
    bind the tenant as ``%(tenant)s`` rather than interpolating it.
    """

    def __init__(self, schema: Schema) -> None:
        self._schema = schema
        self._counter = 0
        self._attributable: dict[str, bool] = {}

    def _next_alias(self) -> sql.Identifier:
        self._counter += 1
        return sql.Identifier(f"_p{self._counter}")

    def _tenant(self) -> sql.Placeholder:
        return sql.Placeholder("tenant")

    def _exists(
        self,
        fk: ForeignKey,
        child_alias: sql.Identifier,
        inner: sql.Composable,
        parent_alias: sql.Identifier,
    ) -> sql.Composed:
        return sql.SQL(
            "EXISTS (SELECT 1 FROM {parent} AS {pa} "
            "WHERE {pa}.{pcol} = {ca}.{ccol} AND {inner})"
        ).format(
            parent=sql.Identifier(fk.parent),
            pa=parent_alias,
            pcol=sql.Identifier(fk.parent_column),
            ca=child_alias,
            ccol=sql.Identifier(fk.child_column),
            inner=inner,
        )

    @staticmethod
    def _any_of(terms: Sequence[sql.Composable]) -> sql.Composed:
        return sql.SQL("({})").format(sql.SQL(" OR ").join(terms))

    def is_attributable(self, table: str, path: tuple[str, ...] = ()) -> bool:
        """Can a row of this table be traced to some tenant at all?"""
        if table in self._schema.user_scoped:
            return True
        cached = self._attributable.get(table)
        if cached is not None:
            return cached
        if table in path:
            return False
        result = any(
            self.is_attributable(fk.parent, (*path, table))
            for fk in self._schema.parents_of(table)
            if fk.parent != table
        )
        self._attributable[table] = result
        return result

    def in_delete_set(
        self, table: str, alias: sql.Identifier, path: tuple[str, ...] = ()
    ) -> sql.Composed | None:
        """Rows a delete of this tenant would remove, directly or by cascade."""
        if len(path) > _MAX_DEPTH:
            raise PurgeRefusedError(
                f"cascade chain deeper than {_MAX_DEPTH} at "
                f"{' -> '.join((*path, table))}; the closure cannot be shown to "
                f"be complete."
            )
        terms: list[sql.Composable] = []
        if table in self._schema.user_scoped:
            terms.append(sql.SQL("{a}.user_id = {t}").format(a=alias, t=self._tenant()))
        for fk in self._schema.parents_of(table):
            if not fk.cascades or fk.parent == table:
                continue
            if fk.parent in path or fk.parent == table:
                raise PurgeRefusedError(
                    f"cascade cycle through {fk.describe()}; the closure cannot "
                    f"be expressed as a finite predicate."
                )
            parent_alias = self._next_alias()
            inner = self.in_delete_set(fk.parent, parent_alias, (*path, table))
            if inner is None:
                continue
            terms.append(self._exists(fk, alias, inner, parent_alias))
        return self._any_of(terms) if terms else None

    def attributed_elsewhere(
        self, table: str, alias: sql.Identifier, path: tuple[str, ...] = ()
    ) -> sql.Composed | None:
        """Rows belonging to a tenant that is not the target.

        ``IS DISTINCT FROM`` and not ``<>``: ``workflows.user_id`` is nullable,
        and three-valued logic would drop every NULL-owned row out of the check.
        """
        sources = self.attribution_sources(table, alias, path)
        return self._any_of([expr for _, expr in sources]) if sources else None

    def attribution_sources(
        self, table: str, alias: sql.Identifier, path: tuple[str, ...] = ()
    ) -> list[tuple[str, sql.Composed]]:
        """Each separate way this row can be shown to belong elsewhere.

        Split out from :meth:`attributed_elsewhere` so a refusal can say *which*
        path attributed the row — "555 rows owned via playlist_id -> playlists"
        is actionable where "555 rows" is not.
        """
        if len(path) > _MAX_DEPTH:
            raise PurgeRefusedError(
                f"tenancy chain deeper than {_MAX_DEPTH} at "
                f"{' -> '.join((*path, table))}."
            )
        if table in self._schema.user_scoped:
            return [
                (
                    "user_id",
                    sql.SQL("({a}.user_id IS DISTINCT FROM {t})").format(
                        a=alias, t=self._tenant()
                    ),
                )
            ]
        sources: list[tuple[str, sql.Composed]] = []
        for fk in self._schema.parents_of(table):
            if fk.parent == table or fk.parent in path:
                continue
            if not self.is_attributable(fk.parent):
                continue
            parent_alias = self._next_alias()
            inner = self.attributed_elsewhere(fk.parent, parent_alias, (*path, table))
            if inner is None:
                continue
            sources.append((
                f"{fk.child_column} -> {fk.parent}",
                self._exists(fk, alias, inner, parent_alias),
            ))
        return sources

    def owned_by_tenant(
        self, table: str, alias: sql.Identifier, path: tuple[str, ...] = ()
    ) -> sql.Composed | None:
        """Rows belonging to the target tenant and to no other.

        For a derived table both halves matter: *some* parent must place the row
        under the target, and *no* parent may place it anywhere else. The second
        half is what keeps a delete off the C3 shape even if the check above were
        somehow passed.
        """
        if len(path) > _MAX_DEPTH:
            raise PurgeRefusedError(
                f"ownership chain deeper than {_MAX_DEPTH} at {table}."
            )
        if table in self._schema.user_scoped:
            return sql.SQL("({a}.user_id = {t})").format(a=alias, t=self._tenant())

        mine: list[sql.Composable] = []
        for fk in self._schema.parents_of(table):
            if fk.parent == table or fk.parent in path:
                continue
            if not self.is_attributable(fk.parent):
                continue
            parent_alias = self._next_alias()
            inner = self.owned_by_tenant(fk.parent, parent_alias, (*path, table))
            if inner is None:
                continue
            mine.append(self._exists(fk, alias, inner, parent_alias))
        if not mine:
            return None
        foreign = self.attributed_elsewhere(table, alias, path)
        owned = self._any_of(mine)
        if foreign is None:
            return owned
        return sql.SQL("({owned} AND NOT {foreign})").format(
            owned=owned, foreign=foreign
        )


def closure_tables(schema: Schema, predicates: Predicates) -> tuple[str, ...]:
    """Every table holding at least one row a delete of this tenant would touch."""
    return tuple(
        sorted(
            table
            for table in schema.tables
            if predicates.in_delete_set(table, sql.Identifier("t")) is not None
        )
    )


def delete_order(schema: Schema, tables: Sequence[str]) -> tuple[str, ...]:
    """Children before parents, so no CASCADE ever fires as a surprise.

    Depth over *all* FK edges, not only cascading ones: ``track_mappings.track_id``
    is ``RESTRICT`` (migration 051, finding C8), and getting that order wrong
    fails the transaction rather than losing data — but it fails it after the
    backup was written, which is a bad afternoon.
    """
    depth = dict.fromkeys(tables, 0)
    edges = [
        fk
        for fk in schema.foreign_keys
        if fk.child in depth and fk.parent in depth and fk.child != fk.parent
    ]
    for _ in range(len(depth) + 1):
        changed = False
        for fk in edges:
            if depth[fk.child] <= depth[fk.parent]:
                depth[fk.child] = depth[fk.parent] + 1
                changed = True
        if not changed:
            break
    else:
        raise PurgeRefusedError(
            "the foreign-key graph over the closure did not settle into a "
            "delete order; a cycle would make the delete order arbitrary."
        )
    return tuple(sorted(depth, key=lambda t: (-depth[t], t)))


# ---------------------------------------------------------------- counts ----


def _scalar_count(conn: Conn, query: sql.Composable, params: Params) -> int:
    row = conn.execute(query, params).fetchone()
    return int(row["n"]) if row else 0


def count_closure(
    conn: Conn, predicates: Predicates, tenant: str, tables: Sequence[str]
) -> dict[str, int]:
    """Rows per table inside the cascade closure — what the delete really costs."""
    counts: dict[str, int] = {}
    for table in tables:
        alias = sql.Identifier("t")
        in_set = predicates.in_delete_set(table, alias)
        if in_set is None:
            continue
        n = _scalar_count(
            conn,
            sql.SQL("SELECT count(*) AS n FROM {tbl} AS {a} WHERE {pred}").format(
                tbl=sql.Identifier(table), a=alias, pred=in_set
            ),
            {"tenant": tenant},
        )
        if n:
            counts[table] = n
    return counts


def count_owned_directly(
    conn: Conn, tenant: str, schema: Schema, tables: Sequence[str]
) -> dict[str, int]:
    """Rows the tenant owns through its own ``user_id`` column, for contrast."""
    counts: dict[str, int] = {}
    for table in tables:
        if table not in schema.user_scoped:
            continue
        n = _scalar_count(
            conn,
            sql.SQL(
                "SELECT count(*) AS n FROM {tbl} WHERE user_id = %(tenant)s"
            ).format(tbl=sql.Identifier(table)),
            {"tenant": tenant},
        )
        if n:
            counts[table] = n
    return counts


# ---------------------------------------------------------------- checks ----


@dataclass(frozen=True, slots=True)
class Blocker:
    """One reason not to proceed, phrased for whoever has to act on it."""

    kind: str
    detail: str


def find_cascade_blast(
    conn: Conn, predicates: Predicates, tenant: str, tables: Sequence[str]
) -> list[Blocker]:
    """Rows inside the closure that belong to somebody else — or to nobody.

    This is the check the condemned draft got wrong, and the reason it existed.
    It runs over every table in the closure, including the four that carry no
    ``user_id`` and would have been skipped: ``playlist_tracks``,
    ``workflow_runs``, ``workflow_versions``, ``workflow_run_nodes``.

    No ``except`` clause anywhere near it: a statement timeout here must abort
    the run, not subtract an FK from the check.
    """
    blockers: list[Blocker] = []
    for table in tables:
        alias = sql.Identifier("t")
        in_set = predicates.in_delete_set(table, alias)
        if in_set is None:
            continue
        for label, source in predicates.attribution_sources(table, alias):
            n = _scalar_count(
                conn,
                sql.SQL(
                    "SELECT count(*) AS n FROM {tbl} AS {a} WHERE {in_set} AND {source}"
                ).format(
                    tbl=sql.Identifier(table), a=alias, in_set=in_set, source=source
                ),
                {"tenant": tenant},
            )
            if n:
                blockers.append(
                    Blocker(
                        kind="cascade blast",
                        detail=(
                            f"{n:,} {table} row(s) inside the cascade closure are "
                            f"attributed to another tenant via {label}"
                        ),
                    )
                )

        owned = predicates.owned_by_tenant(table, alias)
        elsewhere = predicates.attributed_elsewhere(table, alias)
        if owned is None or elsewhere is None:
            continue
        n = _scalar_count(
            conn,
            sql.SQL(
                "SELECT count(*) AS n FROM {tbl} AS {a} "
                "WHERE {in_set} AND NOT {owned} AND NOT {elsewhere}"
            ).format(
                tbl=sql.Identifier(table),
                a=alias,
                in_set=in_set,
                owned=owned,
                elsewhere=elsewhere,
            ),
            {"tenant": tenant},
        )
        if n:
            blockers.append(
                Blocker(
                    kind="unattributable",
                    detail=(
                        f"{n:,} {table} row(s) inside the cascade closure belong to "
                        f"no tenant at all — the delete would take rows nobody can "
                        f"be shown to own"
                    ),
                )
            )
    return blockers


def find_blocking_references(
    conn: Conn,
    schema: Schema,
    predicates: Predicates,
    tenant: str,
    tables: Sequence[str],
) -> list[Blocker]:
    """Rows outside the closure pointing into it through a non-CASCADE FK.

    ``SET NULL`` mutates a row belonging to someone else (``workflow_runs`` and
    ``operation_runs`` both hold ``triggered_by_schedule_id`` that way).
    ``RESTRICT`` aborts the delete part-way through. Neither is a cascade, so
    neither is caught by the closure check.
    """
    blockers: list[Blocker] = []
    in_closure = set(tables)
    for fk in schema.foreign_keys:
        if fk.cascades or fk.parent not in in_closure:
            continue
        child_alias = sql.Identifier("c")
        parent_alias = sql.Identifier("p")
        parent_in_set = predicates.in_delete_set(fk.parent, parent_alias)
        if parent_in_set is None:  # pragma: no cover — parent is in the closure
            continue
        child_in_set = predicates.in_delete_set(fk.child, child_alias)
        outside = (
            sql.SQL("TRUE")
            if child_in_set is None
            else sql.SQL("NOT {inner}").format(inner=child_in_set)
        )
        n = _scalar_count(
            conn,
            sql.SQL(
                "SELECT count(*) AS n FROM {child} AS {c} "
                "JOIN {parent} AS {p} ON {p}.{pcol} = {c}.{ccol} "
                "WHERE {parent_in_set} AND {outside}"
            ).format(
                child=sql.Identifier(fk.child),
                c=child_alias,
                parent=sql.Identifier(fk.parent),
                p=parent_alias,
                pcol=sql.Identifier(fk.parent_column),
                ccol=sql.Identifier(fk.child_column),
                parent_in_set=parent_in_set,
                outside=outside,
            ),
            {"tenant": tenant},
        )
        if n:
            consequence = (
                "would be silently rewritten"
                if fk.on_delete in {"n", "d"}
                else "would abort the delete"
            )
            blockers.append(
                Blocker(
                    kind="outside reference",
                    detail=(
                        f"{n:,} row(s) outside the closure reference it via "
                        f"{fk.describe()} — they {consequence}"
                    ),
                )
            )
    return blockers


def find_by_value_references(
    conn: Conn,
    schema: Schema,
    predicates: Predicates,
    tenant: str,
    tables: Sequence[str],
) -> list[str]:
    """Rows outside the closure that name rows inside it by value, with no FK.

    Reported, not refused. Nothing cascades and nothing is mutated; what is left
    behind is a dangling id in append-only history — the same call
    ``reown_cross_tenant_tracks.py`` already made about ``resolution_events``.
    """
    notes: list[str] = []
    in_closure = set(tables)
    for (child, column), targets in sorted(_BY_VALUE_REFERENCES.items()):
        if child not in schema.tables:
            continue
        for target in targets:
            if target not in in_closure or target not in schema.primary_key:
                continue
            child_alias = sql.Identifier("c")
            parent_alias = sql.Identifier("p")
            parent_in_set = predicates.in_delete_set(target, parent_alias)
            if parent_in_set is None:  # pragma: no cover — target is in the closure
                continue
            child_in_set = predicates.in_delete_set(child, child_alias)
            outside = (
                sql.SQL("TRUE")
                if child_in_set is None
                else sql.SQL("NOT {inner}").format(inner=child_in_set)
            )
            n = _scalar_count(
                conn,
                sql.SQL(
                    "SELECT count(*) AS n FROM {child} AS {c} "
                    "JOIN {target} AS {p} ON {p}.{pk} = {c}.{col} "
                    "WHERE {parent_in_set} AND {outside}"
                ).format(
                    child=sql.Identifier(child),
                    c=child_alias,
                    target=sql.Identifier(target),
                    p=parent_alias,
                    pk=sql.Identifier(schema.primary_key[target]),
                    col=sql.Identifier(column),
                    parent_in_set=parent_in_set,
                    outside=outside,
                ),
                {"tenant": tenant},
            )
            if n:
                notes.append(
                    f"{n:,} {child}.{column} value(s) outside the closure name "
                    f"{target} rows inside it (no FK — they will dangle)"
                )
    return notes


def check_liveness(
    conn: Conn,
    schema: Schema,
    predicates: Predicates,
    tenant: str,
    tables: Sequence[str],
    stale_days: int,
) -> tuple[list[Blocker], list[str]]:
    """Every sign this tenant is still in use, not only the two obvious ones.

    Sweeps *every* timestamp column of *every* table in the closure rather than
    two hand-picked ``max()`` calls, so a table nobody thought about is still
    covered. Returns (blockers, observations).
    """
    blockers: list[Blocker] = []
    observations: list[str] = []

    schedule_states = conn.execute(
        sql.SQL(
            "SELECT status, count(*) AS n FROM schedules "
            "WHERE user_id = %(tenant)s GROUP BY status ORDER BY status"
        ),
        {"tenant": tenant},
    ).fetchall()
    for row in schedule_states:
        status = str(row["status"])
        observations.append(f"schedules: {row['n']} in status {status!r}")
        if status not in _LIVE_SCHEDULE_STATES:
            blockers.append(
                Blocker(
                    kind="live",
                    detail=f"{row['n']} schedule(s) in status {status!r} — not disabled",
                )
            )

    now = datetime.now(UTC)
    newest: list[tuple[datetime, str, str]] = []
    for table in tables:
        columns = schema.timestamp_columns.get(table, ())
        if not columns:
            continue
        alias = sql.Identifier("t")
        in_set = predicates.in_delete_set(table, alias)
        if in_set is None:  # pragma: no cover — table came from the closure
            continue
        selected = sql.SQL(", ").join(
            sql.SQL("max({a}.{col}) AS {out}").format(
                a=alias, col=sql.Identifier(col), out=sql.Identifier(col)
            )
            for col in columns
        )
        row = conn.execute(
            sql.SQL("SELECT {selected} FROM {tbl} AS {a} WHERE {pred}").format(
                selected=selected, tbl=sql.Identifier(table), a=alias, pred=in_set
            ),
            {"tenant": tenant},
        ).fetchone()
        if row is None:
            continue
        for col in columns:
            value = row[col]
            if isinstance(value, datetime):
                newest.append((value, table, col))

    newest.sort(reverse=True)
    for value, table, col in newest[:6]:
        when = "in the future" if value > now else f"{(now - value).days}d ago"
        observations.append(f"newest {table}.{col}: {value.isoformat()} ({when})")
    for value, table, col in newest:
        # A future timestamp is an intention, not activity — ``next_run_at`` on a
        # long-disabled schedule is still in the future and says nothing about
        # use. The schedule-status check above is what covers intent.
        if value > now:
            continue
        age = (now - value).days
        if age < stale_days:
            blockers.append(
                Blocker(
                    kind="live",
                    detail=(
                        f"{table}.{col} is {age}d old (< {stale_days}d) — "
                        f"{value.isoformat()}. Something still writes to this "
                        f"tenant; find out what before deleting it."
                    ),
                )
            )
            break

    tokens = _scalar_count(
        conn,
        sql.SQL("SELECT count(*) AS n FROM oauth_tokens WHERE user_id = %(tenant)s"),
        {"tenant": tenant},
    )
    if tokens:
        observations.append(
            f"oauth_tokens: {tokens} provider token(s) will be deleted — revoke "
            f"them at the provider separately; deleting the row does not."
        )
    return blockers, observations


def summarize_supersession(conn: Conn, tenant: str) -> list[str]:
    """What of this tenant's content already exists under another tenant.

    Evidence for the operator, not a gate. A namesake playlist with a different
    track count is a strong hint of supersession and not a proof of equivalence,
    so both counts are printed — the condemned draft promised track counts and
    printed only the number of namesakes.
    """
    lines: list[str] = []

    row = conn.execute(
        """
        SELECT count(*) AS total,
               count(*) FILTER (WHERE t.isrc IS NULL) AS no_isrc,
               count(*) FILTER (WHERE t.isrc IS NOT NULL AND EXISTS (
                   SELECT 1 FROM tracks o
                   WHERE o.user_id IS DISTINCT FROM %(tenant)s AND o.isrc = t.isrc
               )) AS isrc_elsewhere
        FROM tracks t WHERE t.user_id = %(tenant)s
        """,
        {"tenant": tenant},
    ).fetchone()
    if row and int(row["total"]):
        total = int(row["total"])
        elsewhere = int(row["isrc_elsewhere"])
        no_isrc = int(row["no_isrc"])
        lines.append(
            f"tracks: {total:,} total; {elsewhere:,} share an ISRC with a track "
            f"another tenant owns; {no_isrc:,} carry no ISRC to compare; "
            f"{total - elsewhere - no_isrc:,} have an ISRC nobody else holds"
        )

    playlists = conn.execute(
        """
        SELECT p.name AS name,
               (SELECT count(*) FROM playlist_tracks pt WHERE pt.playlist_id = p.id) AS n,
               (SELECT count(*) FROM playlists o
                 WHERE o.user_id IS DISTINCT FROM %(tenant)s AND o.name = p.name) AS namesakes,
               (SELECT max((SELECT count(*) FROM playlist_tracks pt WHERE pt.playlist_id = o.id))
                  FROM playlists o
                 WHERE o.user_id IS DISTINCT FROM %(tenant)s AND o.name = p.name) AS namesake_tracks
        FROM playlists p WHERE p.user_id = %(tenant)s ORDER BY p.name
        """,
        {"tenant": tenant},
    ).fetchall()
    for row in playlists:
        name = str(row["name"])[:46]
        if int(row["namesakes"]):
            lines.append(
                f"playlist {name!r}: {row['n']} tracks here vs "
                f"{row['namesake_tracks']} in the namesake under another tenant"
            )
        else:
            lines.append(
                f"playlist {name!r}: {row['n']} tracks — NO namesake, unique to "
                f"this tenant"
            )

    workflows = conn.execute(
        """
        SELECT w.name AS name,
               (SELECT count(*) FROM workflow_versions v WHERE v.workflow_id = w.id) AS versions,
               (SELECT count(*) FROM workflow_runs r WHERE r.workflow_id = w.id) AS runs,
               (SELECT count(*) FROM workflows o
                 WHERE o.user_id IS DISTINCT FROM %(tenant)s AND o.name = w.name) AS namesakes
        FROM workflows w WHERE w.user_id = %(tenant)s ORDER BY w.name
        """,
        {"tenant": tenant},
    ).fetchall()
    for row in workflows:
        name = str(row["name"])[:46]
        origin = (
            f"namesake under another tenant ({row['namesakes']})"
            if int(row["namesakes"])
            else "NO namesake, unique to this tenant"
        )
        lines.append(
            f"workflow {name!r}: {row['versions']} version(s), {row['runs']} run(s) "
            f"— {origin}"
        )
    return lines


# ---------------------------------------------------------------- writes ----


def dump_backup(
    conn: Conn,
    predicates: Predicates,
    tenant: str,
    tables: Sequence[str],
    path: Path,
    database: str,
) -> int:
    """Write every row inside the cascade closure to JSON. Returns the count.

    The closure, not the tenant's own rows: the 555 ``playlist_tracks``, the
    ``workflow_run`` and its five ``workflow_run_nodes`` are deleted by cascade
    and a backup without them restores two empty playlists.

    ``to_jsonb`` does the serializing in PostgreSQL, so NULL stays ``null``, the
    28 jsonb/array columns keep their structure, and timestamps come out as ISO
    strings in UTC — where ``str()`` turned ``tracks.artists`` into a Python
    ``repr`` with single quotes and NULL into the string ``"None"``.

    Refuses an existing path: three tenants means three invocations, and
    overwriting the first backup with the third is a silent loss.
    """
    payload: dict[str, list[DictRow]] = {}
    total = 0
    for table in tables:
        alias = sql.Identifier("t")
        in_set = predicates.in_delete_set(table, alias)
        if in_set is None:  # pragma: no cover — table came from the closure
            continue
        rows = conn.execute(
            sql.SQL(
                "SELECT to_jsonb({a}) AS row FROM {tbl} AS {a} WHERE {pred}"
            ).format(tbl=sql.Identifier(table), a=alias, pred=in_set),
            {"tenant": tenant},
        ).fetchall()
        if not rows:
            continue
        payload[table] = [r["row"] for r in rows]
        total += len(rows)

    document = {
        "tenant": tenant,
        "database": database,
        "captured_at": datetime.now(UTC).isoformat(),
        "row_count": total,
        "note": (
            "Rows inside the cascade closure of this tenant — what the delete "
            "removed, including rows removed by ON DELETE CASCADE rather than "
            "by an explicit statement. Serialized by PostgreSQL's to_jsonb in "
            "UTC. Restore children-last."
        ),
        "tables": payload,
    }
    with path.open("x", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, sort_keys=True)
    return total


def delete_tenant(
    conn: Conn, predicates: Predicates, tenant: str, ordered: Sequence[str]
) -> dict[str, int]:
    """Delete the tenant's rows, children first, one explicit predicate each.

    Every statement names its own tenancy — ``user_id = :tenant`` where the
    column exists, the derived ownership predicate where it does not. Nothing is
    left to the session, and nothing is left to CASCADE: by the time a parent is
    deleted its children are already gone, which is why the after-check can
    demand an empty closure.

    Self-references need no special handling, which was worth checking rather
    than assuming: ``track_mappings.superseded_by_id`` is ``ON DELETE RESTRICT``
    and points at another ``track_mappings`` row, but RESTRICT is evaluated at
    the end of the *statement*, so one ``DELETE`` covering a whole supersession
    chain succeeds. A foreign row holding the other end is a different matter,
    and ``find_blocking_references`` refuses that before we get here.
    """
    deleted: dict[str, int] = {}
    for table in ordered:
        alias = sql.Identifier("t")
        owned = predicates.owned_by_tenant(table, alias)
        if owned is None:
            raise PurgeRefusedError(
                f"no ownership predicate for {table}; its rows cannot be "
                f"attributed to anyone, so this script will not remove them."
            )
        cursor = conn.execute(
            sql.SQL("DELETE FROM {tbl} AS {a} WHERE {owned}").format(
                tbl=sql.Identifier(table), a=alias, owned=owned
            ),
            {"tenant": tenant},
        )
        if cursor.rowcount > 0:
            deleted[table] = cursor.rowcount
    return deleted


# ------------------------------------------------------------------ main ----


def _print_section(title: str) -> None:
    print(f"\n=== {title} ===")


def _print_counts(counts: Mapping[str, int]) -> None:
    for table, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {table:34s} {n:>10,}")


def _open_transaction(conn: Conn, *, read_only: bool, tenant: str) -> None:
    """One SERIALIZABLE transaction, locked, budgeted, and UTC.

    SERIALIZABLE is the answer to the poller: it runs roughly hourly and the
    real user has two enabled schedules, so a play or a playlist row can appear
    between the safety check and the delete. Under SERIALIZABLE that write
    conflicts with the reads the check performed and one of the two
    transactions aborts — which is the guarantee "check then delete" needs and
    READ COMMITTED does not give.

    The advisory lock is for a second copy of this script, which SERIALIZABLE
    would resolve by aborting one of them halfway rather than by not starting.
    """
    conn.isolation_level = psycopg.IsolationLevel.SERIALIZABLE
    conn.read_only = read_only
    _ = conn.execute(
        sql.SQL("SET LOCAL statement_timeout = {timeout}").format(
            timeout=sql.Literal(_STATEMENT_TIMEOUT)
        )
    )
    _ = conn.execute(sql.SQL("SET LOCAL TimeZone = 'UTC'"))
    row = conn.execute(
        "SELECT pg_try_advisory_xact_lock(%(ns)s, hashtext(%(tenant)s)) AS got",
        {"ns": _LOCK_NAMESPACE, "tenant": tenant},
    ).fetchone()
    if row is None or not bool(row["got"]):
        raise PurgeRefusedError(
            f"another purge of {tenant!r} holds the advisory lock. Wait for it "
            f"rather than racing it."
        )


def _run(tenant: str, backup: Path | None, apply: bool, stale_days: int) -> int:
    from src.config.settings import database_host_and_mode, get_database_url

    if tenant not in _KNOWN_RESIDUE:
        raise PurgeRefusedError(
            f"{tenant!r} is not a known residue tenant. This script deletes only: "
            f"{', '.join(sorted(_KNOWN_RESIDUE))}. Everything else is somebody's "
            f"account."
        )
    if apply and backup is None:
        raise PurgeRefusedError(
            "--apply requires --backup <path>. A delete without one is a guess."
        )
    if stale_days < _DEFAULT_STALE_DAYS:
        # The knob raises the bar; it does not lower it. --stale-days 0 would be
        # an override flag for the liveness refusal wearing a different hat, and
        # this script has no override flags.
        raise PurgeRefusedError(
            f"--stale-days {stale_days} is below the floor of {_DEFAULT_STALE_DAYS}. "
            f"The flag exists to demand *more* quiet, not less — lowering it would "
            f"be an override for the liveness refusal."
        )
    if backup is not None and backup.exists():
        raise PurgeRefusedError(
            f"{backup} already exists. Three tenants means three invocations; "
            f"overwriting the first backup with the third loses it silently."
        )

    db_url = get_database_url()
    if not db_url:
        raise PurgeRefusedError("no database URL resolved — set DATABASE_URL.")
    host, mode = database_host_and_mode(db_url)
    sync_url = db_url.replace("postgresql+psycopg://", "postgresql://", 1)

    print(f"\ntarget database: {host} ({mode})")
    print(f"tenant:          {tenant!r}")
    print(
        f"mode:            {'APPLY — will delete' if apply else 'dry run (READ ONLY)'}"
    )

    with psycopg.connect(sync_url, row_factory=dict_row, autocommit=False) as conn:
        _open_transaction(conn, read_only=not apply, tenant=tenant)

        schema = load_schema(conn)
        assert_rows_are_fully_visible(conn)
        assert_undeclared_by_value_columns_are_none(conn)
        predicates = Predicates(schema)
        tables = closure_tables(schema, predicates)

        closure = count_closure(conn, predicates, tenant, tables)
        direct = count_owned_directly(conn, tenant, schema, tables)
        total = sum(closure.values())

        _print_section(f"cascade closure: {total:,} rows across {len(closure)} tables")
        _print_counts(closure)
        cascade_only = {
            table: n - direct.get(table, 0)
            for table, n in closure.items()
            if n - direct.get(table, 0) > 0
        }
        if cascade_only:
            print("\n  of which reachable only by CASCADE (no user_id of their own):")
            _print_counts(cascade_only)
        if not total:
            print("  (nothing to delete)")
            return 0

        _print_section("supersession evidence")
        for line in summarize_supersession(conn, tenant):
            print(f"  {line}")

        _print_section("liveness")
        live, observations = check_liveness(
            conn, schema, predicates, tenant, tables, stale_days
        )
        for line in observations:
            print(f"  {line}")
        if not live:
            print(f"  dormant — nothing newer than {stale_days}d, no live schedule")

        _print_section("cross-tenant references (cascade blast radius)")
        blast = find_cascade_blast(conn, predicates, tenant, tables)
        outside = find_blocking_references(conn, schema, predicates, tenant, tables)
        for blocker in (*blast, *outside):
            print(f"  REFUSE [{blocker.kind}]: {blocker.detail}")
        if not blast and not outside:
            print(
                "  none — no other tenant's rows are inside or point into the closure"
            )

        _print_section("by-value references (reported, not refused)")
        notes = find_by_value_references(conn, schema, predicates, tenant, tables)
        for note in notes:
            print(f"  NOTE: {note}")
        if not notes:
            print("  none")

        blockers = [*live, *blast, *outside]
        if blockers:
            _print_section(f"REFUSED — {len(blockers)} blocker(s)")
            for blocker in blockers:
                print(f"  [{blocker.kind}] {blocker.detail}")
            print("\nNothing was written. There is no flag to override this.")
            return 1

        ordered = delete_order(schema, tables)
        if not apply:
            _print_section("delete order (children first)")
            print("  " + " -> ".join(ordered))
            print(
                f"\nDry run — the transaction was READ ONLY. Re-run with "
                f"--backup <path> --apply to delete {total:,} rows."
            )
            return 0

        if backup is None:  # pragma: no cover — guarded at the top of _run
            raise PurgeRefusedError("--apply requires --backup <path>.")
        written = dump_backup(conn, predicates, tenant, tables, backup, host)
        print(f"\nbacked up {written:,} rows -> {backup}")
        if written != total:  # pragma: no cover — same predicate, same snapshot
            raise PurgeRefusedError(
                f"backup wrote {written:,} rows but the closure holds {total:,}. "
                f"Refusing to delete rows the backup does not contain."
            )

        deleted = delete_tenant(conn, predicates, tenant, ordered)
        remaining = count_closure(conn, predicates, tenant, tables)
        if remaining:
            raise PurgeRefusedError(
                f"{sum(remaining.values()):,} row(s) still inside the closure after "
                f"the delete: {remaining}. Rolling back."
            )

        conn.commit()
        _print_section(f"deleted {sum(deleted.values()):,} rows")
        _print_counts(deleted)
        print(f"\ntenant {tenant!r} is gone. Backup: {backup}")
        logger.info(
            "Residue tenant purged",
            tenant=tenant,
            database=host,
            rows=sum(deleted.values()),
            backup=str(backup),
        )
    return 0


def main(
    tenant: str = typer.Option(
        ...,
        help=f"The residue tenant to delete. One of: {', '.join(sorted(_KNOWN_RESIDUE))}.",
    ),
    backup: str = typer.Option(
        default="",
        help="Where to write the JSON backup. Required with --apply; refuses to "
        "overwrite an existing file.",
    ),
    apply: bool = typer.Option(
        default=False,
        help="Execute the delete. Without it the transaction is READ ONLY and "
        "only the plan is printed.",
    ),
    stale_days: int = typer.Option(
        default=_DEFAULT_STALE_DAYS,
        help="Refuse if any row in the closure carries a timestamp newer than this.",
    ),
) -> None:
    """Delete a residue tenant and everything the delete would cascade into."""
    setup_script_logger("purge_residue_tenants")
    try:
        code = _run(
            tenant=tenant,
            backup=Path(backup) if backup else None,
            apply=apply,
            stale_days=stale_days,
        )
    except PurgeRefusedError as refusal:
        print(f"\nREFUSED: {refusal}")
        raise typer.Exit(1) from None
    except psycopg.Error as error:
        # Fail closed. The condemned draft swallowed exactly this and deleted
        # anyway: a statement timeout on a 56k-row join looks like safety.
        print(f"\nABORTED: database error — nothing was deleted.\n  {error}")
        raise typer.Exit(3) from None
    raise typer.Exit(code)


if __name__ == "__main__":
    typer.run(main)
