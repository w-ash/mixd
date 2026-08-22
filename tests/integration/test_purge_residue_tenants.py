"""``purge_residue_tenants.py``'s refusals, against a real database.

Every property this script has is a property of the schema, not of its own
logic, so a mocked test would assert that the mock was written correctly:

- the cascade blast check turns on ``playlist_tracks`` having no ``user_id``
  and both its FKs being ``ON DELETE CASCADE``. That is a fact that exists only
  in PostgreSQL, and it is the fact the condemned first draft got wrong.
- the backup's completeness turns on which rows CASCADE actually takes.
- the delete order turns on ``track_mappings.track_id`` being ``RESTRICT``
  (migration 051, finding C8) and on ``superseded_by_id`` being a self-reference
  that RESTRICT evaluates row-at-a-time.

The first class below asserts those premises directly, so a schema divergence
surfaces as a named failure rather than as every other test quietly passing.

The script runs on a raw psycopg connection rather than a SQLAlchemy session,
so these tests open their own connection to the same container and roll it back
at the end of each test instead of using the ``db_session`` savepoint fixture.
"""

from collections.abc import AsyncGenerator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.rows import DictRow, dict_row
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
import typer

from scripts.purge_residue_tenants import (
    Predicates,
    PurgeRefusedError,
    Schema,
    _run,
    check_liveness,
    closure_tables,
    count_closure,
    delete_order,
    delete_tenant,
    dump_backup,
    find_blocking_references,
    find_by_value_references,
    find_cascade_blast,
    load_schema,
    main,
)

# A unique name rather than the literal ``default``: these tests share a
# container with the rest of the suite, and "did every row go?" must not be
# answerable by somebody else's fixtures.
_TENANT = "TEST_residue_4c1f8e2a"
_OTHER = "TEST_user_9f1c33ab"
# The real allowlist entry, for the refusals that never reach a database.
_ALLOWLISTED = "default"
_OLD = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)

type Conn = psycopg.Connection[DictRow]


@pytest.fixture
async def conn(_test_engine: AsyncEngine) -> AsyncGenerator[Conn]:
    """A raw psycopg connection on the test container, rolled back after."""
    url = _test_engine.url.render_as_string(hide_password=False).replace(
        "postgresql+psycopg://", "postgresql://", 1
    )
    connection = psycopg.connect(url, row_factory=dict_row, autocommit=False)
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


@pytest.fixture
def schema(conn: Conn) -> Schema:
    return load_schema(conn)


@pytest.fixture
def predicates(schema: Schema) -> Predicates:
    return Predicates(schema)


@pytest.fixture
def tables(schema: Schema, predicates: Predicates) -> tuple[str, ...]:
    return closure_tables(schema, predicates)


# ----------------------------------------------------------------- seeds ----


def _track(conn: Conn, *, user_id: str, title: str = "TEST Track") -> UUID:
    track_id = uuid4()
    conn.execute(
        "INSERT INTO tracks (id, user_id, title, artists, isrc, created_at, updated_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (
            track_id,
            user_id,
            title,
            json.dumps({"names": ["TEST Artist"]}),
            None,
            _OLD,
            _OLD,
        ),
    )
    return track_id


def _playlist(conn: Conn, *, user_id: str, name: str = "TEST Playlist") -> UUID:
    playlist_id = uuid4()
    conn.execute(
        "INSERT INTO playlists (id, user_id, name, track_count, created_at, updated_at)"
        " VALUES (%s, %s, %s, 0, %s, %s)",
        (playlist_id, user_id, name, _OLD, _OLD),
    )
    return playlist_id


def _playlist_track(conn: Conn, *, playlist_id: UUID, track_id: UUID) -> UUID:
    row_id = uuid4()
    conn.execute(
        "INSERT INTO playlist_tracks (id, playlist_id, track_id, sort_key,"
        " created_at, updated_at) VALUES (%s, %s, %s, 'a0', %s, %s)",
        (row_id, playlist_id, track_id, _OLD, _OLD),
    )
    return row_id


def _workflow(conn: Conn, *, user_id: str, name: str = "TEST Workflow") -> UUID:
    workflow_id = uuid4()
    conn.execute(
        "INSERT INTO workflows (id, user_id, name, definition, definition_version,"
        " created_at, updated_at) VALUES (%s, %s, %s, %s, 1, %s, %s)",
        (workflow_id, user_id, name, json.dumps({"nodes": []}), _OLD, _OLD),
    )
    return workflow_id


def _workflow_run(
    conn: Conn, *, workflow_id: UUID, schedule_id: UUID | None = None, nodes: int = 0
) -> UUID:
    run_id = uuid4()
    conn.execute(
        "INSERT INTO workflow_runs (id, workflow_id, status, definition_snapshot,"
        " definition_version, triggered_by_schedule_id, created_at, updated_at)"
        " VALUES (%s, %s, 'completed', %s, 1, %s, %s, %s)",
        (run_id, workflow_id, json.dumps({}), schedule_id, _OLD, _OLD),
    )
    for index in range(nodes):
        conn.execute(
            "INSERT INTO workflow_run_nodes (id, run_id, node_id, node_type, status,"
            " duration_ms, execution_order) VALUES (%s, %s, %s, 'source', 'ok', 1, %s)",
            (uuid4(), run_id, f"n{index}", index),
        )
    return run_id


def _schedule(conn: Conn, *, user_id: str, workflow_id: UUID | None = None) -> UUID:
    schedule_id = uuid4()
    conn.execute(
        "INSERT INTO schedules (id, user_id, workflow_id, hour, minute, next_run_at,"
        " status, created_at, updated_at)"
        " VALUES (%s, %s, %s, 3, 0, %s, 'disabled', %s, %s)",
        (schedule_id, user_id, workflow_id, _OLD, _OLD, _OLD),
    )
    return schedule_id


def _connector_track(conn: Conn) -> UUID:
    connector_track_id = uuid4()
    conn.execute(
        "INSERT INTO connector_tracks (id, connector_name, connector_track_identifier,"
        " title, artists, raw_metadata, last_updated, created_at, updated_at)"
        " VALUES (%s, 'spotify', %s, 'TEST', %s, %s, %s, %s, %s)",
        (
            connector_track_id,
            f"TEST_ct_{uuid4().hex[:12]}",
            json.dumps({"names": ["TEST Artist"]}),
            json.dumps({}),
            _OLD,
            _OLD,
            _OLD,
        ),
    )
    return connector_track_id


def _mapping(
    conn: Conn,
    *,
    user_id: str,
    track_id: UUID,
    superseded_by: UUID | None = None,
) -> UUID:
    mapping_id = uuid4()
    conn.execute(
        "INSERT INTO track_mappings (id, user_id, track_id, connector_track_id,"
        " connector_name, match_method, confidence, is_primary, superseded_by_id,"
        " superseded_at, supersession_reason, created_at, updated_at)"
        " VALUES (%s, %s, %s, %s, 'spotify', 'isrc', 95, %s, %s, %s, %s, %s, %s)",
        (
            mapping_id,
            user_id,
            track_id,
            _connector_track(conn),
            superseded_by is None,
            superseded_by,
            _OLD if superseded_by else None,
            "reassertion" if superseded_by else None,
            _OLD,
            _OLD,
        ),
    )
    return mapping_id


def _resolution_event(conn: Conn, *, user_id: str, track_id: UUID) -> UUID:
    event_id = uuid4()
    conn.execute(
        "INSERT INTO resolution_events (id, user_id, track_id, event_type,"
        " matcher_version, payload, recorded_at, decided_at)"
        " VALUES (%s, %s, %s, 'matched', 'v1', %s, %s, %s)",
        (event_id, user_id, track_id, json.dumps({}), _OLD, _OLD),
    )
    return event_id


def _count(conn: Conn, query: str, params: tuple[object, ...] = ()) -> int:
    row = conn.execute(query, params).fetchone()
    return 0 if row is None else int(next(iter(row.values())))


# ------------------------------------------------------------- premises ----


class TestTheSchemaThisScriptReasonsAbout:
    """The facts the whole design rests on. If these move, the script is wrong."""

    async def test_playlist_tracks_is_tenanted_only_through_its_parents(
        self, schema: Schema
    ):
        """No ``user_id`` column, and both FKs CASCADE.

        This one table is the reason the condemned draft's check reported "no
        cross-tenant references" on a database that had 555 of them.
        """
        assert "playlist_tracks" not in schema.user_scoped
        actions = {
            fk.child_column: fk.on_delete for fk in schema.parents_of("playlist_tracks")
        }
        assert actions["playlist_id"] == "c"
        assert actions["track_id"] == "c"

    async def test_the_workflow_tables_carry_no_tenancy_either(self, schema: Schema):
        for table in ("workflow_runs", "workflow_versions", "workflow_run_nodes"):
            assert table not in schema.user_scoped

    async def test_a_schedule_delete_would_rewrite_a_run_row(self, schema: Schema):
        """``SET NULL``, not CASCADE — the row survives, edited."""
        actions = {
            fk.child_column: fk.on_delete for fk in schema.parents_of("workflow_runs")
        }
        assert actions["triggered_by_schedule_id"] == "n"

    async def test_the_closure_reaches_tables_with_no_user_id(
        self, tables: tuple[str, ...]
    ):
        for table in (
            "playlist_tracks",
            "workflow_runs",
            "workflow_versions",
            "workflow_run_nodes",
        ):
            assert table in tables

    async def test_children_are_deleted_before_their_parents(
        self, schema: Schema, tables: tuple[str, ...]
    ):
        order = delete_order(schema, tables)
        position = {table: index for index, table in enumerate(order)}
        for pair in (
            ("playlist_tracks", "playlists"),
            ("playlist_tracks", "tracks"),
            ("workflow_run_nodes", "workflow_runs"),
            ("workflow_runs", "workflows"),
            ("schedules", "workflows"),
            ("track_mappings", "tracks"),
            ("play_sources", "track_plays"),
        ):
            assert position[pair[0]] < position[pair[1]], pair


# ------------------------------------------------------- cascade blast ----


class TestTheCascadeBlastRefusal:
    async def test_a_foreign_playlist_holding_the_tenants_track_is_refused(
        self, conn: Conn, predicates: Predicates, tables: tuple[str, ...]
    ):
        """Finding C3, exactly: the real user's playlist, the residue's track.

        The row is inside the closure (via ``track_id`` CASCADE) and attributed
        elsewhere (via ``playlist_id``). The condemned draft skipped the table
        because it has no ``user_id`` and deleted the row.
        """
        track_id = _track(conn, user_id=_TENANT)
        foreign_playlist = _playlist(conn, user_id=_OTHER)
        _ = _playlist_track(conn, playlist_id=foreign_playlist, track_id=track_id)

        blockers = find_cascade_blast(conn, predicates, _TENANT, tables)

        assert [b.kind for b in blockers] == ["cascade blast"]
        assert "playlist_tracks" in blockers[0].detail
        assert "playlist_id -> playlists" in blockers[0].detail

    async def test_the_row_is_inside_the_closure_even_though_it_has_no_user_id(
        self, conn: Conn, predicates: Predicates, tables: tuple[str, ...]
    ):
        """The count the operator is shown must include what CASCADE takes."""
        track_id = _track(conn, user_id=_TENANT)
        foreign_playlist = _playlist(conn, user_id=_OTHER)
        _ = _playlist_track(conn, playlist_id=foreign_playlist, track_id=track_id)

        counts = count_closure(conn, predicates, _TENANT, tables)

        assert counts["playlist_tracks"] == 1

    async def test_the_tenants_own_playlist_holding_its_own_track_is_fine(
        self, conn: Conn, predicates: Predicates, tables: tuple[str, ...]
    ):
        """The check has to be a check, not a refusal to ever run."""
        track_id = _track(conn, user_id=_TENANT)
        playlist_id = _playlist(conn, user_id=_TENANT)
        _ = _playlist_track(conn, playlist_id=playlist_id, track_id=track_id)

        assert find_cascade_blast(conn, predicates, _TENANT, tables) == []

    async def test_a_foreign_play_on_the_tenants_track_is_refused(
        self, conn: Conn, predicates: Predicates, tables: tuple[str, ...]
    ):
        """The original C3 population: another tenant's plays on this one's track."""
        track_id = _track(conn, user_id=_TENANT)
        conn.execute(
            "INSERT INTO track_plays (id, user_id, track_id, service, played_at,"
            " created_at, updated_at) VALUES (%s, %s, %s, 'spotify', %s, %s, %s)",
            (uuid4(), _OTHER, track_id, _OLD, _OLD, _OLD),
        )

        blockers = find_cascade_blast(conn, predicates, _TENANT, tables)

        assert any("track_plays" in b.detail for b in blockers)

    async def test_a_run_node_under_a_foreign_workflow_is_refused_two_hops_out(
        self,
        conn: Conn,
        schema: Schema,
        predicates: Predicates,
        tables: tuple[str, ...],
    ):
        """Tenancy derived through two tables that have none of their own.

        ``workflow_run_nodes -> workflow_runs -> workflows.user_id``. The node is
        pulled into the closure by the schedule this tenant owns, and attributed
        to the other tenant only by walking two FK hops.
        """
        schedule_id = _schedule(conn, user_id=_TENANT)
        foreign_workflow = _workflow(conn, user_id=_OTHER)
        _ = _workflow_run(
            conn, workflow_id=foreign_workflow, schedule_id=schedule_id, nodes=2
        )

        blockers = find_blocking_references(conn, schema, predicates, _TENANT, tables)

        assert any("triggered_by_schedule_id" in b.detail for b in blockers)
        assert any("silently rewritten" in b.detail for b in blockers)

    async def test_a_foreign_mapping_on_the_tenants_track_is_refused(
        self,
        conn: Conn,
        schema: Schema,
        predicates: Predicates,
        tables: tuple[str, ...],
    ):
        """RESTRICT does not lose data — it aborts the delete after the backup."""
        track_id = _track(conn, user_id=_TENANT)
        _ = _mapping(conn, user_id=_OTHER, track_id=track_id)

        blockers = find_blocking_references(conn, schema, predicates, _TENANT, tables)

        assert any("RESTRICT" in b.detail for b in blockers)
        assert any("abort the delete" in b.detail for b in blockers)

    async def test_a_by_value_reference_is_reported_and_not_refused(
        self,
        conn: Conn,
        schema: Schema,
        predicates: Predicates,
        tables: tuple[str, ...],
    ):
        """``resolution_events.track_id`` has no FK — nothing cascades, so it is
        a note rather than a blocker, but it must not go unmentioned."""
        track_id = _track(conn, user_id=_TENANT)
        _ = _resolution_event(conn, user_id=_OTHER, track_id=track_id)

        notes = find_by_value_references(conn, schema, predicates, _TENANT, tables)

        assert any("resolution_events.track_id" in note for note in notes)
        assert find_cascade_blast(conn, predicates, _TENANT, tables) == []


# ------------------------------------------------------------- fail closed ----


class TestAnErrorInTheCheckAborts:
    async def test_a_broken_check_query_raises_instead_of_returning_empty(
        self, conn: Conn, schema: Schema, tables: tuple[str, ...]
    ):
        """The condemned draft had ``except psycopg.Error: continue`` here.

        A statement timeout on the 56k-row join then subtracted an FK from the
        check and the script deleted anyway. Whatever the cause — timeout,
        permission, a table that moved — it has to come out as an exception.
        """
        broken = replace(
            schema,
            tables=schema.tables | {"table_that_does_not_exist"},
            user_scoped=schema.user_scoped | {"table_that_does_not_exist"},
        )
        predicates = Predicates(broken)
        scope = (*tables, "table_that_does_not_exist")

        with pytest.raises(psycopg.Error):
            _ = find_cascade_blast(conn, predicates, _TENANT, scope)

    async def test_a_statement_timeout_is_not_swallowed(
        self, conn: Conn, predicates: Predicates, tables: tuple[str, ...]
    ):
        """The specific failure the draft turned into a clean bill of health."""
        _ = _track(conn, user_id=_TENANT)
        _ = conn.execute("SET LOCAL statement_timeout = '1ms'")

        with pytest.raises(psycopg.errors.QueryCanceled):
            _ = find_cascade_blast(conn, predicates, _TENANT, tables)

    async def test_the_cli_turns_a_database_error_into_a_nonzero_exit(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        def _boom(**_: object) -> int:
            raise psycopg.OperationalError("connection lost mid-check")

        monkeypatch.setattr("scripts.purge_residue_tenants._run", _boom)

        with pytest.raises(typer.Exit) as caught:
            main(tenant=_ALLOWLISTED, backup="", apply=False, stale_days=14)

        assert caught.value.exit_code == 3


# --------------------------------------------------------------- backup ----


class TestTheBackupCoversWhatCascadeTakes:
    async def test_cascade_reachable_rows_are_in_the_backup(
        self,
        conn: Conn,
        predicates: Predicates,
        tables: tuple[str, ...],
        tmp_path: Path,
    ):
        """Restoring a backup without these reconstructs empty playlists."""
        track_id = _track(conn, user_id=_TENANT)
        playlist_id = _playlist(conn, user_id=_TENANT)
        _ = _playlist_track(conn, playlist_id=playlist_id, track_id=track_id)
        workflow_id = _workflow(conn, user_id=_TENANT)
        _ = _workflow_run(conn, workflow_id=workflow_id, nodes=3)

        path = tmp_path / "backup.json"
        written = dump_backup(conn, predicates, _TENANT, tables, path, "test")

        document = json.loads(path.read_text())
        assert len(document["tables"]["playlist_tracks"]) == 1
        assert len(document["tables"]["workflow_run_nodes"]) == 3
        assert len(document["tables"]["workflow_runs"]) == 1
        assert document["row_count"] == written
        assert written == sum(count_closure(conn, predicates, _TENANT, tables).values())

    async def test_jsonb_and_null_round_trip_instead_of_becoming_strings(
        self,
        conn: Conn,
        predicates: Predicates,
        tables: tuple[str, ...],
        tmp_path: Path,
    ):
        """``str()`` turned ``artists`` into a Python repr and NULL into "None"."""
        _ = _track(conn, user_id=_TENANT)

        path = tmp_path / "backup.json"
        _ = dump_backup(conn, predicates, _TENANT, tables, path, "test")

        track = json.loads(path.read_text())["tables"]["tracks"][0]
        assert track["artists"] == {"names": ["TEST Artist"]}
        assert track["isrc"] is None
        assert track["title"] == "TEST Track"

    async def test_an_existing_backup_file_is_never_overwritten(
        self,
        conn: Conn,
        predicates: Predicates,
        tables: tuple[str, ...],
        tmp_path: Path,
    ):
        """Three tenants, three invocations, one careless filename."""
        _ = _track(conn, user_id=_TENANT)
        path = tmp_path / "backup.json"
        _ = path.write_text("the first tenant's backup")

        with pytest.raises(FileExistsError):
            _ = dump_backup(conn, predicates, _TENANT, tables, path, "test")

        assert path.read_text() == "the first tenant's backup"

    async def test_the_cli_refuses_an_existing_backup_before_connecting(
        self, tmp_path: Path
    ):
        path = tmp_path / "backup.json"
        _ = path.write_text("{}")

        with pytest.raises(PurgeRefusedError, match="already exists"):
            _ = _run(tenant=_ALLOWLISTED, backup=path, apply=True, stale_days=14)


# --------------------------------------------------------------- refuse ----


class TestTheAllowlistAndTheFlags:
    async def test_an_arbitrary_user_is_never_deleted(self):
        with pytest.raises(PurgeRefusedError, match="not a known residue tenant"):
            _ = _run(tenant=_OTHER, backup=None, apply=True, stale_days=14)

    async def test_apply_without_a_backup_is_refused(self):
        with pytest.raises(PurgeRefusedError, match="requires --backup"):
            _ = _run(tenant=_ALLOWLISTED, backup=None, apply=True, stale_days=14)

    async def test_stale_days_cannot_be_lowered_into_an_override(self, tmp_path: Path):
        with pytest.raises(PurgeRefusedError, match="below the floor"):
            _ = _run(
                tenant=_ALLOWLISTED,
                backup=tmp_path / "b.json",
                apply=True,
                stale_days=0,
            )


class TestLiveness:
    async def test_a_row_written_yesterday_stops_the_purge(
        self,
        conn: Conn,
        schema: Schema,
        predicates: Predicates,
        tables: tuple[str, ...],
    ):
        track_id = _track(conn, user_id=_TENANT)
        conn.execute(
            "UPDATE tracks SET updated_at = %s WHERE id = %s",
            (datetime.now(UTC) - timedelta(days=1), track_id),
        )

        blockers, _ = check_liveness(conn, schema, predicates, _TENANT, tables, 14)

        assert [b.kind for b in blockers] == ["live"]
        assert "tracks.updated_at" in blockers[0].detail

    async def test_a_schedule_that_is_not_disabled_stops_the_purge(
        self,
        conn: Conn,
        schema: Schema,
        predicates: Predicates,
        tables: tuple[str, ...],
    ):
        schedule_id = _schedule(conn, user_id=_TENANT)
        conn.execute(
            "UPDATE schedules SET status = 'active' WHERE id = %s", (schedule_id,)
        )

        blockers, _ = check_liveness(conn, schema, predicates, _TENANT, tables, 14)

        assert any("not disabled" in b.detail for b in blockers)

    async def test_a_future_next_run_at_on_a_dead_schedule_is_not_liveness(
        self,
        conn: Conn,
        schema: Schema,
        predicates: Predicates,
        tables: tuple[str, ...],
    ):
        """Prod's residue schedule has ``next_run_at`` in the future and
        ``run_count`` 0. An intention is not activity."""
        schedule_id = _schedule(conn, user_id=_TENANT)
        conn.execute(
            "UPDATE schedules SET next_run_at = %s WHERE id = %s",
            (datetime.now(UTC) + timedelta(days=30), schedule_id),
        )

        blockers, observations = check_liveness(
            conn, schema, predicates, _TENANT, tables, 14
        )

        assert blockers == []
        assert any("in the future" in line for line in observations)


# --------------------------------------------------------------- delete ----


class TestACleanTenantDeletesFully:
    async def test_nothing_of_the_tenants_survives_and_nothing_else_moves(
        self,
        conn: Conn,
        schema: Schema,
        predicates: Predicates,
        tables: tuple[str, ...],
    ):
        track_id = _track(conn, user_id=_TENANT)
        playlist_id = _playlist(conn, user_id=_TENANT)
        _ = _playlist_track(conn, playlist_id=playlist_id, track_id=track_id)
        workflow_id = _workflow(conn, user_id=_TENANT)
        schedule_id = _schedule(conn, user_id=_TENANT, workflow_id=workflow_id)
        _ = _workflow_run(
            conn, workflow_id=workflow_id, schedule_id=schedule_id, nodes=2
        )
        _ = _mapping(conn, user_id=_TENANT, track_id=track_id)

        other_track = _track(conn, user_id=_OTHER)
        other_playlist = _playlist(conn, user_id=_OTHER)
        other_row = _playlist_track(
            conn, playlist_id=other_playlist, track_id=other_track
        )

        assert find_cascade_blast(conn, predicates, _TENANT, tables) == []
        deleted = delete_tenant(conn, predicates, _TENANT, delete_order(schema, tables))

        assert count_closure(conn, predicates, _TENANT, tables) == {}
        assert deleted["playlist_tracks"] == 1
        assert deleted["workflow_run_nodes"] == 2
        assert (
            _count(
                conn, "SELECT count(*) FROM playlist_tracks WHERE id = %s", (other_row,)
            )
            == 1
        )
        assert (
            _count(conn, "SELECT count(*) FROM tracks WHERE id = %s", (other_track,))
            == 1
        )

    async def test_a_supersession_chain_deletes_in_one_statement(
        self,
        conn: Conn,
        schema: Schema,
        predicates: Predicates,
        tables: tuple[str, ...],
    ):
        """Why the script has no chain-breaking step, checked rather than assumed.

        ``track_mappings.superseded_by_id`` is a self-FK with ``ON DELETE
        RESTRICT`` (migration 051), which reads like it should refuse a DELETE
        that removes both ends. It does not: RESTRICT is evaluated at the end of
        the statement, so one ``DELETE ... WHERE user_id = :tenant`` over a whole
        chain succeeds. A first draft of this script NULLed the column first;
        this test is what removed that code.
        """
        track_id = _track(conn, user_id=_TENANT)
        live = _mapping(conn, user_id=_TENANT, track_id=track_id)
        _ = _mapping(conn, user_id=_TENANT, track_id=track_id, superseded_by=live)

        deleted = delete_tenant(conn, predicates, _TENANT, delete_order(schema, tables))

        assert deleted["track_mappings"] == 2
        assert count_closure(conn, predicates, _TENANT, tables) == {}

    async def test_a_foreign_row_holding_the_other_end_of_a_chain_is_refused(
        self,
        conn: Conn,
        schema: Schema,
        predicates: Predicates,
        tables: tuple[str, ...],
    ):
        """The case RESTRICT does catch — and the check catches it first."""
        track_id = _track(conn, user_id=_TENANT)
        live = _mapping(conn, user_id=_TENANT, track_id=track_id)
        _ = _mapping(
            conn,
            user_id=_OTHER,
            track_id=_track(conn, user_id=_OTHER),
            superseded_by=live,
        )

        blockers = find_blocking_references(conn, schema, predicates, _TENANT, tables)

        assert any("superseded_by_id" in b.detail for b in blockers)

    async def test_the_delete_predicate_names_the_tenant_on_every_statement(
        self, predicates: Predicates
    ):
        """RLS is enforced on none of these tables; the predicate is the isolation."""
        for table in ("tracks", "playlist_tracks", "workflow_run_nodes"):
            owned = predicates.owned_by_tenant(table, sql.Identifier("t"))
            assert owned is not None
            assert "%(tenant)s" in owned.as_string()
