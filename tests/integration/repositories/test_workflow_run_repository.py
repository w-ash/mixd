"""Integration tests for WorkflowRunRepository with real database operations.

Tests CRUD, pagination, cascade delete, node status updates, and batch latest-run queries.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid7

import pytest

from src.config.constants import WorkflowConstants
from src.domain.entities.workflow import (
    Workflow,
    WorkflowDef,
    WorkflowRun,
    WorkflowRunNode,
    WorkflowTaskDef,
)
from src.domain.exceptions import NotFoundError, WorkflowAlreadyRunningError
from src.infrastructure.persistence.repositories.workflow.core import WorkflowRepository
from src.infrastructure.persistence.repositories.workflow.runs import (
    WorkflowRunRepository,
)


def _make_def(wf_id: str = "test", name: str = "Test") -> WorkflowDef:
    return WorkflowDef(
        id=wf_id,
        name=name,
        tasks=[
            WorkflowTaskDef(id="source_1", type="source.liked_tracks"),
            WorkflowTaskDef(
                id="filter_1", type="filter.by_metric", upstream=["source_1"]
            ),
        ],
    )


async def _create_workflow(db_session) -> Workflow:
    """Helper to create a workflow that runs can reference via FK."""
    wf_repo = WorkflowRepository(db_session)
    return await wf_repo.save_workflow(
        Workflow(user_id="default", definition=_make_def())
    )


def _make_run(workflow_id: UUID, *, status: str = "pending") -> WorkflowRun:
    """Build a domain WorkflowRun with pre-created node records."""
    wf_def = _make_def()
    nodes = [
        WorkflowRunNode(
            node_id=task.id,
            node_type=task.type,
            execution_order=i + 1,
        )
        for i, task in enumerate(wf_def.tasks)
    ]
    return WorkflowRun(
        workflow_id=workflow_id,
        status=status,
        definition_snapshot=wf_def,
        nodes=nodes,
    )


class TestWorkflowRunCRUD:
    """Create, retrieve, and update workflow runs."""

    async def test_create_and_retrieve(self, db_session) -> None:
        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)

        run = _make_run(workflow.id)
        saved = await repo.create_run(run)

        assert saved.id is not None
        assert saved.workflow_id == workflow.id
        assert saved.status == "pending"
        assert saved.definition_snapshot.name == "Test"
        assert len(saved.nodes) == 2

        retrieved = await repo.get_run_by_id(saved.id)
        assert retrieved.id == saved.id
        assert len(retrieved.nodes) == 2
        assert retrieved.nodes[0].node_id == "source_1"
        assert retrieved.nodes[1].node_id == "filter_1"

    async def test_update_run_status_to_running(self, db_session) -> None:
        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)
        saved = await repo.create_run(_make_run(workflow.id))

        now = datetime.now(UTC)
        await repo.update_run_status(saved.id, "running", started_at=now)

        retrieved = await repo.get_run_by_id(saved.id)
        assert retrieved.status == "running"
        assert retrieved.started_at is not None

    async def test_update_run_status_to_completed(self, db_session) -> None:
        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)
        saved = await repo.create_run(_make_run(workflow.id))

        now = datetime.now(UTC)
        await repo.update_run_status(
            saved.id,
            "completed",
            completed_at=now,
            duration_ms=1500,
            output_track_count=42,
        )

        retrieved = await repo.get_run_by_id(saved.id)
        assert retrieved.status == "completed"
        assert retrieved.duration_ms == 1500

    async def test_update_run_status_to_failed(self, db_session) -> None:
        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)
        saved = await repo.create_run(_make_run(workflow.id))

        await repo.update_run_status(saved.id, "failed", error_message="API timeout")

        retrieved = await repo.get_run_by_id(saved.id)
        assert retrieved.status == "failed"
        assert retrieved.error_message == "API timeout"

    async def test_terminal_write_is_first_writer_wins(self, db_session) -> None:
        """Once a run is terminal, a second terminal write is a silent no-op.

        The completion path and the sweeper can both try to record an outcome
        on the same row. The guard (`WHERE status NOT IN terminal`) lets the
        first terminal write win; the loser must not raise and must not corrupt
        the recorded fields. A single guarded UPDATE is atomic in Postgres, so
        this sequential check exercises the same guard a real race would hit.
        """
        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)
        saved = await repo.create_run(_make_run(workflow.id))
        await repo.update_run_status(saved.id, "running", started_at=datetime.now(UTC))

        # First terminal write wins → reports True (transitioned a row).
        won = await repo.update_run_status(
            saved.id, "completed", duration_ms=1500, output_track_count=42
        )
        assert won is True
        # Second terminal write (e.g. the sweeper) must no-op, not raise, and
        # report False so counting callers don't over-report.
        lost = await repo.update_run_status(
            saved.id,
            WorkflowConstants.RUN_STATUS_CRASHED,
            duration_ms=999,
            error_message="watchdog: heartbeat went silent",
        )
        assert lost is False

        retrieved = await repo.get_run_by_id(saved.id)
        # Fields stay self-consistent with the first (winning) write.
        assert retrieved.status == "completed"
        assert retrieved.duration_ms == 1500
        assert retrieved.error_message is None

    async def test_crashed_terminal_write_blocks_later_writes(self, db_session) -> None:
        """A crashed run is terminal — a later completed write can't overwrite it."""
        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)
        saved = await repo.create_run(_make_run(workflow.id))
        await repo.update_run_status(saved.id, "running", started_at=datetime.now(UTC))

        await repo.update_run_status(
            saved.id, WorkflowConstants.RUN_STATUS_CRASHED, error_message="worker died"
        )
        await repo.update_run_status(saved.id, "completed", duration_ms=10)

        retrieved = await repo.get_run_by_id(saved.id)
        assert retrieved.status == WorkflowConstants.RUN_STATUS_CRASHED

    async def test_terminal_write_to_missing_run_is_silent(self, db_session) -> None:
        """A terminal write to a missing row no-ops (the run already has an
        outcome or never existed) — only non-terminal writes raise NotFound."""
        repo = WorkflowRunRepository(db_session)
        await repo.update_run_status(uuid7(), "completed")  # no raise

    async def test_update_nonexistent_run_raises(self, db_session) -> None:
        repo = WorkflowRunRepository(db_session)

        with pytest.raises(NotFoundError):
            await repo.update_run_status(uuid7(), "running")

    async def test_get_nonexistent_run_raises(self, db_session) -> None:
        repo = WorkflowRunRepository(db_session)

        with pytest.raises(NotFoundError):
            await repo.get_run_by_id(uuid7())


class TestActiveRunGuard:
    """The uq_workflow_runs_active partial unique index enforces at most one
    active (pending/running) run per workflow — the DB-backed concurrency guard.

    Postgres checks the unique index against flushed-but-uncommitted rows within
    the same transaction, so these sequential creates exercise the same conflict
    a real cross-instance race would hit, without needing separate connections.
    """

    async def test_second_active_run_rejected(self, db_session) -> None:
        """A second pending run for the same workflow trips the guard."""
        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)

        await repo.create_run(_make_run(workflow.id))

        with pytest.raises(WorkflowAlreadyRunningError) as excinfo:
            await repo.create_run(_make_run(workflow.id))
        assert excinfo.value.workflow_id == str(workflow.id)

    async def test_running_run_also_blocks(self, db_session) -> None:
        """The guard covers 'running', not just 'pending'."""
        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)

        first = await repo.create_run(_make_run(workflow.id))
        await repo.update_run_status(first.id, "running", started_at=datetime.now(UTC))

        with pytest.raises(WorkflowAlreadyRunningError):
            await repo.create_run(_make_run(workflow.id))

    async def test_terminal_status_frees_the_slot(self, db_session) -> None:
        """Once the active run reaches a terminal status it leaves the partial
        index, so a fresh run for the same workflow can be created."""
        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)

        first = await repo.create_run(_make_run(workflow.id))
        await repo.update_run_status(
            first.id, "completed", completed_at=datetime.now(UTC)
        )

        # Slot freed — second create succeeds.
        second = await repo.create_run(_make_run(workflow.id))
        assert second.id != first.id

    async def test_different_workflows_run_concurrently(self, db_session) -> None:
        """The guard is per-workflow: distinct workflows are unaffected."""
        wf_repo = WorkflowRepository(db_session)
        repo = WorkflowRunRepository(db_session)
        wf1 = await wf_repo.save_workflow(
            Workflow(user_id="default", definition=_make_def("wf1", "WF1"))
        )
        wf2 = await wf_repo.save_workflow(
            Workflow(user_id="default", definition=_make_def("wf2", "WF2"))
        )

        run1 = await repo.create_run(_make_run(wf1.id))
        run2 = await repo.create_run(_make_run(wf2.id))
        assert run1.workflow_id == wf1.id
        assert run2.workflow_id == wf2.id


class TestWorkflowRunNodeStatus:
    """Node-level status updates within a run."""

    async def test_update_node_to_running(self, db_session) -> None:
        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)
        saved = await repo.create_run(_make_run(workflow.id))

        now = datetime.now(UTC)
        await repo.update_node_status(saved.id, "source_1", "running", started_at=now)

        retrieved = await repo.get_run_by_id(saved.id)
        source_node = next(n for n in retrieved.nodes if n.node_id == "source_1")
        assert source_node.status == "running"
        assert source_node.started_at is not None

    async def test_update_node_to_completed_with_metrics(self, db_session) -> None:
        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)
        saved = await repo.create_run(_make_run(workflow.id))

        now = datetime.now(UTC)
        await repo.update_node_status(
            saved.id,
            "filter_1",
            "completed",
            completed_at=now,
            duration_ms=800,
            input_track_count=100,
            output_track_count=42,
        )

        retrieved = await repo.get_run_by_id(saved.id)
        filter_node = next(n for n in retrieved.nodes if n.node_id == "filter_1")
        assert filter_node.status == "completed"
        assert filter_node.duration_ms == 800
        assert filter_node.input_track_count == 100
        assert filter_node.output_track_count == 42

    async def test_update_nonexistent_node_raises(self, db_session) -> None:
        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)
        saved = await repo.create_run(_make_run(workflow.id))

        with pytest.raises(NotFoundError, match="not found"):
            await repo.update_node_status(saved.id, "no_such_node", "running")

    async def test_save_additional_node_record(self, db_session) -> None:
        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)
        saved = await repo.create_run(_make_run(workflow.id))

        new_node = WorkflowRunNode(
            run_id=saved.id,
            node_id="extra_step",
            node_type="sink.playlist",
            execution_order=3,
        )
        saved_node = await repo.save_node_record(new_node)

        assert saved_node.id is not None
        assert saved_node.node_id == "extra_step"

        retrieved = await repo.get_run_by_id(saved.id)
        assert len(retrieved.nodes) == 3


class TestWorkflowRunPagination:
    """Listing runs with pagination and ordering."""

    async def test_returns_runs_ordered_by_created_desc(self, db_session) -> None:
        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)

        # Terminal runs: a workflow accumulates many completed runs over its
        # life. (Multiple *active* runs are forbidden by uq_workflow_runs_active.)
        for _ in range(3):
            await repo.create_run(_make_run(workflow.id, status="completed"))

        runs, total = await repo.get_runs_for_workflow(workflow.id)
        assert total == 3
        assert len(runs) == 3
        # Runs should NOT include nodes (summary mode)
        assert runs[0].nodes == []

    async def test_pagination_limit_and_offset(self, db_session) -> None:
        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)

        for _ in range(5):
            await repo.create_run(_make_run(workflow.id, status="completed"))

        page1, total = await repo.get_runs_for_workflow(workflow.id, limit=2, offset=0)
        assert total == 5
        assert len(page1) == 2

        page2, _ = await repo.get_runs_for_workflow(workflow.id, limit=2, offset=2)
        assert len(page2) == 2

        # No overlap
        page1_ids = {r.id for r in page1}
        page2_ids = {r.id for r in page2}
        assert page1_ids.isdisjoint(page2_ids)

    async def test_empty_runs_list(self, db_session) -> None:
        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)

        runs, total = await repo.get_runs_for_workflow(workflow.id)
        assert total == 0
        assert runs == []


class TestLatestRunQueries:
    """Latest-run lookup for single and batch queries."""

    async def test_latest_run_for_workflow(self, db_session) -> None:
        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)

        await repo.create_run(_make_run(workflow.id, status="completed"))
        latest_run = await repo.create_run(_make_run(workflow.id, status="completed"))

        result = await repo.get_latest_run_for_workflow(workflow.id)
        assert result is not None
        assert result.id == latest_run.id

    async def test_latest_run_none_when_no_runs(self, db_session) -> None:
        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)

        result = await repo.get_latest_run_for_workflow(workflow.id)
        assert result is None

    async def test_batch_latest_runs(self, db_session) -> None:
        wf_repo = WorkflowRepository(db_session)
        repo = WorkflowRunRepository(db_session)

        wf1 = await wf_repo.save_workflow(
            Workflow(user_id="default", definition=_make_def("wf1", "WF1"))
        )
        wf2 = await wf_repo.save_workflow(
            Workflow(user_id="default", definition=_make_def("wf2", "WF2"))
        )

        await repo.create_run(_make_run(wf1.id, status="completed"))
        wf1_latest = await repo.create_run(_make_run(wf1.id, status="completed"))

        wf2_latest = await repo.create_run(_make_run(wf2.id, status="completed"))

        result = await repo.get_latest_runs_for_workflows([wf1.id, wf2.id])
        assert len(result) == 2
        assert result[wf1.id].id == wf1_latest.id
        assert result[wf2.id].id == wf2_latest.id

    async def test_batch_latest_runs_empty_ids(self, db_session) -> None:
        repo = WorkflowRunRepository(db_session)

        result = await repo.get_latest_runs_for_workflows([])
        assert result == {}

    async def test_batch_latest_runs_missing_workflow(self, db_session) -> None:
        repo = WorkflowRunRepository(db_session)

        result = await repo.get_latest_runs_for_workflows([uuid7()])
        assert result == {}


class TestCascadeDelete:
    """Deleting a workflow cascades to runs and nodes."""

    async def test_deleting_workflow_cascades_to_runs(self, db_session) -> None:
        wf_repo = WorkflowRepository(db_session)
        run_repo = WorkflowRunRepository(db_session)

        workflow = await wf_repo.save_workflow(
            Workflow(user_id="default", definition=_make_def())
        )
        run = await run_repo.create_run(_make_run(workflow.id))

        await wf_repo.delete_workflow(workflow.id, user_id="default")
        await db_session.flush()

        # Run should be gone
        with pytest.raises(NotFoundError):
            await run_repo.get_run_by_id(run.id)


class TestHeartbeatAndStaleSweep:
    """Heartbeat bumps and stalled-run discovery for the sweeper."""

    async def test_bump_heartbeat_sets_timestamp(self, db_session) -> None:
        from datetime import timedelta

        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)

        before = await repo.create_run(_make_run(workflow.id, status="running"))
        assert before.heartbeat_at is None

        await repo.bump_heartbeat(before.id)
        await db_session.flush()
        after = await repo.get_run_by_id(before.id)

        assert after.heartbeat_at is not None
        assert after.heartbeat_at > datetime.now(UTC) - timedelta(seconds=5)

    async def test_list_stalled_runs_excludes_recent_heartbeat(
        self, db_session
    ) -> None:
        from datetime import timedelta

        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)

        # Create one run, set started_at well in the past, bump heartbeat to now
        run = await repo.create_run(_make_run(workflow.id, status="running"))
        await repo.update_run_status(
            run.id,
            "running",
            started_at=datetime.now(UTC) - timedelta(seconds=600),
        )
        await repo.bump_heartbeat(run.id)
        await db_session.flush()

        stalled = await repo.list_stalled_runs(stale_threshold_seconds=60)
        assert run.id not in [r.id for r in stalled]

    async def test_list_stalled_runs_finds_cold_start_and_silent_runs(
        self, db_session
    ) -> None:
        from datetime import timedelta

        repo = WorkflowRunRepository(db_session)
        # Two distinct workflows — only one active run per workflow is allowed
        # (uq_workflow_runs_active), so the two concurrently-running stalled runs
        # must belong to different workflows.
        wf_repo = WorkflowRepository(db_session)
        wf_cold = await wf_repo.save_workflow(
            Workflow(user_id="default", definition=_make_def("cold", "Cold"))
        )
        wf_silent = await wf_repo.save_workflow(
            Workflow(user_id="default", definition=_make_def("silent", "Silent"))
        )

        # Cold-start: started 5min ago, heartbeat NEVER set
        cold = await repo.create_run(_make_run(wf_cold.id, status="running"))
        await repo.update_run_status(
            cold.id,
            "running",
            started_at=datetime.now(UTC) - timedelta(seconds=300),
        )

        # Silent: started 10min ago, heartbeat is also stale
        silent = await repo.create_run(_make_run(wf_silent.id, status="running"))
        await repo.update_run_status(
            silent.id,
            "running",
            started_at=datetime.now(UTC) - timedelta(seconds=600),
        )
        await repo.bump_heartbeat(silent.id)
        # Force the heartbeat back in time via raw SQL — bump always uses now()
        from sqlalchemy import text

        await db_session.execute(
            text("UPDATE workflow_runs SET heartbeat_at = :stale WHERE id = :id"),
            {
                "stale": datetime.now(UTC) - timedelta(seconds=300),
                "id": silent.id,
            },
        )
        await db_session.flush()

        stalled = await repo.list_stalled_runs(stale_threshold_seconds=60)
        ids = {r.id for r in stalled}
        assert cold.id in ids
        assert silent.id in ids

        # Confirm the classifier inputs are right
        cold_row = next(r for r in stalled if r.id == cold.id)
        assert cold_row.heartbeat_at is None
        silent_row = next(r for r in stalled if r.id == silent.id)
        assert silent_row.heartbeat_at is not None

    async def test_list_stalled_runs_skips_completed(self, db_session) -> None:
        from datetime import timedelta

        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)

        completed = await repo.create_run(_make_run(workflow.id, status="completed"))
        await repo.update_run_status(
            completed.id,
            "completed",
            started_at=datetime.now(UTC) - timedelta(seconds=600),
            completed_at=datetime.now(UTC) - timedelta(seconds=500),
        )
        await db_session.flush()

        stalled = await repo.list_stalled_runs(stale_threshold_seconds=60)
        assert completed.id not in [r.id for r in stalled]


class TestWorkflowRunJsonbWrites:
    """End-to-end coverage of the two JSONB write paths exercised by
    real workflow runs:

    - ``workflow_run_nodes.node_details`` via ``update_node_status``
    - ``workflow_runs.output_tracks`` via ``update_run_status``

    Unit tests for the builders (``test_workflow_runs.py`` and
    ``test_playlist_results.py``) confirm in-process dict shape. These
    tests confirm the full UPDATE → SELECT round-trip with realistic
    builder output, then exercise the orjson driver-level encoder by
    submitting raw UUID / datetime values that bypass the builder
    contract — the encoder must serialize them rather than crash.
    """

    async def test_node_details_round_trips_realistic_playlist_changes(
        self, db_session
    ) -> None:
        """``build_playlist_changes`` output persists and reads back intact
        through ``update_node_status`` — the path destination nodes use.
        """
        from src.application.use_cases._shared.playlist_results import (
            build_playlist_changes,
        )
        from src.domain.entities.track import Artist, Track
        from src.domain.playlist import (
            PlaylistDiff,
            PlaylistOperation,
            PlaylistOperationType,
        )

        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)
        saved = await repo.create_run(_make_run(workflow.id))

        added_track = Track(title="Added", artists=[Artist(name="ArtistA")])
        removed_track = Track(title="Removed", artists=[Artist(name="ArtistR")])
        diff = PlaylistDiff(
            operations=[
                PlaylistOperation(
                    operation_type=PlaylistOperationType.ADD,
                    track=added_track,
                    position=0,
                ),
                PlaylistOperation(
                    operation_type=PlaylistOperationType.REMOVE,
                    track=removed_track,
                    position=1,
                ),
                PlaylistOperation(
                    operation_type=PlaylistOperationType.MOVE,
                    track=added_track,
                    position=2,
                ),
            ]
        )
        playlist_changes = build_playlist_changes(
            diff, playlist_id="pl-local-1", connector="spotify"
        )
        node_details: dict[str, object] = {"playlist_changes": playlist_changes}

        await repo.update_node_status(
            saved.id,
            "filter_1",
            "completed",
            completed_at=datetime.now(UTC),
            duration_ms=200,
            input_track_count=2,
            output_track_count=2,
            node_details=node_details,
        )
        await db_session.flush()

        retrieved = await repo.get_run_by_id(saved.id)
        node = next(n for n in retrieved.nodes if n.node_id == "filter_1")
        assert node.node_details is not None
        changes = node.node_details["playlist_changes"]
        assert isinstance(changes, dict)
        assert changes["tracks_added"][0]["track_id"] == str(added_track.id)
        assert changes["tracks_removed"][0]["track_id"] == str(removed_track.id)
        assert changes["tracks_added_total"] == 1
        assert changes["tracks_removed_total"] == 1
        assert changes["tracks_moved"] == 1
        assert changes["playlist_id"] == "pl-local-1"
        assert changes["connector"] == "spotify"

    async def test_node_details_accepts_raw_uuid_via_orjson_encoder(
        self, db_session
    ) -> None:
        """Regression guard for the bug shape that shipped pre-fix:
        a payload containing raw ``uuid.UUID`` and ``datetime`` reaches
        the JSONB column directly. The orjson encoder must serialize
        these at the driver layer; if it doesn't, psycopg crashes on
        flush with ``TypeError: Object of type UUID is not JSON serializable``.
        """
        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)
        saved = await repo.create_run(_make_run(workflow.id))

        raw_uuid = uuid7()
        raw_dt = datetime.now(UTC)
        node_details: dict[str, object] = {
            "track_uuid": raw_uuid,
            "captured_at": raw_dt,
            "nested": {"inner_track_id": raw_uuid, "logged_at": raw_dt},
        }

        await repo.update_node_status(
            saved.id,
            "source_1",
            "completed",
            completed_at=datetime.now(UTC),
            node_details=node_details,
        )
        await db_session.flush()

        retrieved = await repo.get_run_by_id(saved.id)
        node = next(n for n in retrieved.nodes if n.node_id == "source_1")
        details = node.node_details
        assert details is not None
        assert details["track_uuid"] == str(raw_uuid)
        assert details["captured_at"] == raw_dt.isoformat()
        nested = details["nested"]
        assert isinstance(nested, dict)
        assert nested["inner_track_id"] == str(raw_uuid)
        assert nested["logged_at"] == raw_dt.isoformat()

    async def test_output_tracks_round_trips_realistic_serialize_output(
        self, db_session
    ) -> None:
        """``serialize_output_tracks`` output persists and reads back intact
        through ``update_run_status`` — the path the CLI history observer uses.
        """
        from src.application.use_cases.workflow_runs import serialize_output_tracks
        from tests.fixtures import make_tracks

        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)
        saved = await repo.create_run(_make_run(workflow.id))

        tracks = make_tracks(count=3)
        metrics = {
            "playcount": {tracks[0].id: 42, tracks[1].id: 7, tracks[2].id: 18},
            "last_played": {
                tracks[0].id: datetime(2026, 5, 10, 12, 0, tzinfo=UTC),
                tracks[1].id: None,
                tracks[2].id: datetime(2026, 5, 9, 9, 30, tzinfo=UTC),
            },
        }
        output_tracks, columns = serialize_output_tracks(tracks, metrics=metrics)

        await repo.update_run_status(
            saved.id,
            "completed",
            completed_at=datetime.now(UTC),
            duration_ms=1000,
            output_track_count=len(tracks),
            output_tracks=output_tracks,
        )
        await db_session.flush()

        retrieved = await repo.get_run_by_id(saved.id)
        assert retrieved.output_tracks is not None
        assert len(retrieved.output_tracks) == 3
        assert columns == ["last_played", "playcount"]

        first = retrieved.output_tracks[0]
        assert first["track_id"] == str(tracks[0].id)
        assert first["rank"] == 1
        first_metrics = first["metrics"]
        assert isinstance(first_metrics, dict)
        assert first_metrics["playcount"] == 42
        assert first_metrics["last_played"] == "2026-05-10T12:00:00+00:00"

        second_metrics = retrieved.output_tracks[1]["metrics"]
        assert isinstance(second_metrics, dict)
        assert second_metrics["last_played"] is None

    async def test_output_tracks_accepts_raw_uuid_via_orjson_encoder(
        self, db_session
    ) -> None:
        """Regression guard: ``output_tracks`` containing raw UUID and
        datetime values reaches the JSONB column. orjson must serialize
        them at the driver layer; otherwise psycopg crashes on flush.
        """
        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)
        saved = await repo.create_run(_make_run(workflow.id))

        raw_uuid = uuid7()
        raw_dt = datetime(2026, 5, 10, tzinfo=UTC)
        output_tracks: list[dict[str, object]] = [
            {
                "track_id": raw_uuid,
                "title": "Untitled",
                "rank": 1,
                "metrics": {"played_at": raw_dt, "playcount": 5},
            },
        ]

        await repo.update_run_status(
            saved.id,
            "completed",
            completed_at=datetime.now(UTC),
            output_tracks=output_tracks,
        )
        await db_session.flush()

        retrieved = await repo.get_run_by_id(saved.id)
        assert retrieved.output_tracks is not None
        first = retrieved.output_tracks[0]
        assert first["track_id"] == str(raw_uuid)
        metrics = first["metrics"]
        assert isinstance(metrics, dict)
        assert metrics["played_at"] == raw_dt.isoformat()
        assert metrics["playcount"] == 5


class TestActiveRunsForUser:
    """get_active_runs_for_user — cross-workflow, user-scoped in-flight runs."""

    async def _workflow_for(self, db_session, user_id: str) -> UUID:
        wf_repo = WorkflowRepository(db_session)
        wf = await wf_repo.save_workflow(
            Workflow(user_id=user_id, definition=_make_def())
        )
        return wf.id

    async def test_returns_pending_and_running_excludes_terminal(
        self, db_session
    ) -> None:
        repo = WorkflowRunRepository(db_session)
        # One active run per workflow (uq_workflow_runs_active), so spread the
        # pending + running + completed runs across three workflows.
        pending_wf = await self._workflow_for(db_session, "user-a")
        running_wf = await self._workflow_for(db_session, "user-a")
        done_wf = await self._workflow_for(db_session, "user-a")
        await repo.create_run(_make_run(pending_wf, status="pending"))
        await repo.create_run(_make_run(running_wf, status="running"))
        await repo.create_run(_make_run(done_wf, status="completed"))
        await db_session.flush()

        runs, total = await repo.get_active_runs_for_user("user-a")

        assert total == 2
        statuses = {r.status for r in runs}
        assert statuses == {"pending", "running"}

    async def test_scoped_to_user(self, db_session) -> None:
        repo = WorkflowRunRepository(db_session)
        mine = await self._workflow_for(db_session, "me")
        theirs = await self._workflow_for(db_session, "someone-else")
        await repo.create_run(_make_run(mine, status="running"))
        await repo.create_run(_make_run(theirs, status="running"))
        await db_session.flush()

        runs, total = await repo.get_active_runs_for_user("me")

        assert total == 1
        assert runs[0].workflow_id == mine

    async def test_preserves_operation_id(self, db_session) -> None:
        repo = WorkflowRunRepository(db_session)
        wf = await self._workflow_for(db_session, "user-a")
        run = _make_run(wf, status="running")
        run = WorkflowRun(
            workflow_id=run.workflow_id,
            status=run.status,
            definition_snapshot=run.definition_snapshot,
            nodes=run.nodes,
            operation_id="op-1234",
        )
        await repo.create_run(run)
        await db_session.flush()

        runs, _ = await repo.get_active_runs_for_user("user-a")

        assert runs[0].operation_id == "op-1234"

    async def test_empty_when_no_active_runs(self, db_session) -> None:
        repo = WorkflowRunRepository(db_session)
        done_wf = await self._workflow_for(db_session, "user-a")
        await repo.create_run(_make_run(done_wf, status="completed"))
        await db_session.flush()

        runs, total = await repo.get_active_runs_for_user("user-a")

        assert runs == []
        assert total == 0
