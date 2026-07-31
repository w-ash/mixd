"""Streaming bulk insert via PostgreSQL ``COPY`` into a per-transaction staging table.

The repository layer's default bulk write is a single multi-VALUES
``INSERT ... ON CONFLICT`` (``BaseRepository.bulk_insert_ignore_conflicts``).
That shape has two hard ceilings a full play-history import walks straight into:

* **65535 bind parameters per statement.** ``connector_plays`` writes 14 columns,
  so one statement tops out at 4,681 rows — a 198k-row Spotify GDPR export
  fails outright with ``number of parameters must be between 0 and 65535``.
* **Whole-batch materialisation.** Every row has to exist as a Python dict, and
  then again as compiled bind parameters, before a single byte reaches the wire.

``COPY`` has neither: psycopg3 streams row-by-row off any iterable, and the
server parses without a parameter list. Measured on 50k synthetic connector
plays against ``postgres:17-alpine``: **0.6s / 13MB peak** here versus
**30s / 182MB** for the multi-VALUES path chunked to fit the parameter cap.

Shape of the operation
----------------------
1. ``CREATE TEMPORARY TABLE ... ON COMMIT DROP`` — column-compatible with the
   target, but with no constraints and no indexes, so ``COPY`` writes at full
   speed and nothing can fail mid-stream.
2. ``COPY <staging> FROM STDIN`` — streamed, counted as it goes.
3. ``INSERT INTO <target> SELECT ... FROM <staging> ON CONFLICT DO NOTHING
   RETURNING id`` — one set-based statement; ``RETURNING`` counts what landed.

Row-Level Security
------------------
Staging is session-private scratch, so it carries no policy and needs none —
nothing else can read it, and it dies with the transaction. The write that
matters is step 3, a plain ``INSERT`` into the RLS-protected target, checked by
exactly the same ``user_isolation`` policy as the statement it replaces. Under a
non-superuser role a row whose ``user_id`` disagrees with ``app.user_id`` is
rejected with "new row violates row-level security policy" — verified against a
real policy, not assumed.

Text format, not binary
-----------------------
``COPY ... FROM STDIN`` defaults to text. Binary is ~25% faster but requires
``AsyncCopy.set_types()`` — a hand-maintained list of PostgreSQL type names that
would silently drift from the model. Without it psycopg infers the wire type
from each *value*: ``ms_played=100`` picks ``int2`` and writes 2 bytes into an
``int4`` column, which fails with ``insufficient data left in message`` for a
short play while a long one works. Text format types per-column at the server
and has no such failure mode; 25% of 0.6s is not worth a latent, data-dependent
crash.
"""

from collections.abc import Iterable, Sequence
from typing import Final, cast

from psycopg import AsyncConnection, sql
from psycopg.rows import TupleRow
from psycopg.types.json import Jsonb
from sqlalchemy import Column, MetaData, Table, select
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.schema import CreateTable, DropTable

from src.config import get_logger

logger = get_logger(__name__)

# One row's values, positionally aligned with the caller's column sequence.
type CopyRow = tuple[object, ...]

# PostgreSQL's alias for the current session's temporary schema. Resolves even
# before the session has one — the schema is created lazily on first use.
_TEMP_SCHEMA: Final = "pg_temp"


async def _psycopg_connection(session: AsyncSession) -> AsyncConnection[TupleRow]:
    """The psycopg3 ``AsyncConnection`` underneath SQLAlchemy's async session.

    SQLAlchemy drives psycopg through a greenlet-adapted DBAPI facade, so
    ``cursor.copy()`` is not reachable from the ORM surface. ``driver_connection``
    on the pool-proxied connection hands back the genuine ``AsyncConnection``,
    which can then be awaited natively from async code. It is the *same*
    connection the session holds, so the COPY joins the session's transaction —
    which is what makes ``ON COMMIT DROP`` staging and the transaction-scoped
    ``app.user_id`` RLS setting both apply to it.

    Typed via ``isinstance`` rather than a bare cast: ``driver_connection`` is
    ``Any`` in SQLAlchemy's stubs, and a wrong driver must fail here with a
    readable message rather than at the first ``copy()`` call.
    """
    sa_connection = await session.connection()
    proxied = await sa_connection.get_raw_connection()
    driver_connection = cast(object, proxied.driver_connection)
    if not isinstance(driver_connection, AsyncConnection):
        raise TypeError(
            "COPY bulk insert requires the psycopg3 async driver, got "
            f"{type(driver_connection).__name__}"
        )
    return cast("AsyncConnection[TupleRow]", driver_connection)


def _staging_table(target: Table, name: str) -> Table:
    """A temporary twin of ``target``: same columns, nothing else.

    Constraints, defaults and indexes are deliberately dropped. Staging exists
    to absorb bytes as fast as the socket delivers them; every rule the data has
    to satisfy is enforced by the target table on the ``INSERT ... SELECT``.

    Explicitly schema-qualified to ``pg_temp`` so the ``DROP TABLE IF EXISTS``
    below can only ever reach this session's own scratch table, never a
    same-named permanent one that drifted into the search path.
    """
    return Table(
        name,
        MetaData(),
        *(Column(column.name, column.type) for column in target.columns),
        schema=_TEMP_SCHEMA,
        prefixes=["TEMPORARY"],
        postgresql_on_commit="DROP",
    )


def _jsonb_positions(table: Table, columns: Sequence[str]) -> frozenset[int]:
    """Indexes in ``columns`` that address a JSONB column.

    psycopg has no dumper for a bare ``dict`` — it must be wrapped in ``Jsonb``
    or the COPY fails with "Type is not JSON serializable". Resolving that from
    the table metadata keeps callers building plain Python tuples instead of
    importing driver types, and means a new JSONB column cannot be forgotten.
    """
    return frozenset(
        index
        for index, name in enumerate(columns)
        if isinstance(table.columns[name].type, JSONB)
    )


def _wrap_jsonb(row: CopyRow, positions: frozenset[int]) -> CopyRow:
    """Wrap this row's JSONB values for psycopg. NULLs pass through untouched."""
    return tuple(
        Jsonb(value) if index in positions and value is not None else value
        for index, value in enumerate(row)
    )


async def copy_insert_ignore_conflicts(
    session: AsyncSession,
    *,
    target: Table,
    columns: Sequence[str],
    conflict_columns: Sequence[str],
    rows: Iterable[CopyRow],
    staging_name: str,
) -> tuple[int, int]:
    """Stream ``rows`` into ``target``, skipping unique-constraint conflicts.

    Args:
        session: Session whose transaction the COPY and the INSERT both join.
        target: Destination ``Table`` (``SomeModel.__table__``).
        columns: Column names, positionally aligned with each row's values.
        conflict_columns: Arbiter index columns for ``ON CONFLICT DO NOTHING``.
        rows: Any iterable of value tuples — a generator is consumed lazily and
            never materialised, which is the whole point of this path.
        staging_name: Temporary table name, unique per logical operation.

    Returns:
        ``(streamed, inserted)`` — ``streamed`` is counted during the COPY, so
        callers get a total without needing ``len()`` on the input. Rows the
        conflict arbiter skipped are ``streamed - inserted``, and that includes
        duplicates *within* the batch: ``DO NOTHING`` tolerates a key repeated
        inside one statement (unlike ``DO UPDATE``, which raises a cardinality
        violation), so no Python-side pre-deduplication is needed.
    """
    staging = _staging_table(target, staging_name)
    jsonb_positions = _jsonb_positions(target, columns)
    connection = await _psycopg_connection(session)

    # IF EXISTS, then create: ON COMMIT DROP only fires at COMMIT, so a second
    # call inside one transaction would otherwise collide with its own leftover.
    _ = await session.execute(DropTable(staging, if_exists=True))
    _ = await session.execute(CreateTable(staging))

    copy_statement = sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.Identifier(_TEMP_SCHEMA, staging_name),
        sql.SQL(", ").join(sql.Identifier(name) for name in columns),
    )

    streamed = 0
    async with (
        connection.cursor() as cursor,
        cursor.copy(copy_statement) as copy,
    ):
        for row in rows:
            await copy.write_row(_wrap_jsonb(row, jsonb_positions))
            streamed += 1

    if streamed == 0:
        _ = await session.execute(DropTable(staging, if_exists=True))
        return (0, 0)

    column_list = list(columns)
    insert_statement = (
        pg_insert(target)
        .from_select(
            column_list, select(*(staging.columns[name] for name in column_list))
        )
        .on_conflict_do_nothing(index_elements=list(conflict_columns))
        .returning(target.columns["id"])
    )
    result = await session.execute(insert_statement)
    inserted = len(result.scalars().all())

    # Freeing staging now keeps a long-lived import transaction from carrying
    # every batch's scratch rows to COMMIT.
    _ = await session.execute(DropTable(staging, if_exists=True))

    logger.debug(
        f"COPY bulk insert into {target.name}: {inserted}/{streamed} rows written",
        table=target.name,
        streamed=streamed,
        inserted=inserted,
    )
    return (streamed, inserted)
