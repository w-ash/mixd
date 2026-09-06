"""Unit tests for the typed SSE event payloads.

Two things are pinned: the registry covers every wire event name, and every
emitter's payload validates against the model registered for its event. The
second is what makes the schemas a contract rather than documentation — an
emitter that grows a field the model does not declare fails here.
"""

import asyncio
import inspect
from typing import get_args
from uuid import uuid4

import pytest
import structlog.testing

from src.application.use_cases.import_connector_playlist_as_canonical import (
    ImportConnectorPlaylistsAsCanonicalUseCase,
)
from src.application.workflows.engine.observers import PreviewNodeObserver
from src.config.constants import SubOperationOutcome, WorkflowConstants
from src.domain.entities.progress import (
    OperationStatus,
    ProgressOperation,
    ProgressStatus,
    create_progress_event,
)
from src.domain.entities.workflow import NodeExecutionEvent, RunStatus, WorkflowTaskDef
from src.interface.api.schemas.cache_tags import touches_for
from src.interface.api.schemas.sse_events import (
    SSE_EVENT_SCHEMAS,
    SseFinalStatus,
    SseNodeStatusEvent,
    SseOperationStatus,
    SseOperationTerminalEvent,
    SseProgressStatus,
    SseSubOperationOutcome,
    sse_frame,
)
from src.interface.api.services.progress import (
    _VALID_OUTCOMES,
    SSEOperationRegistry,
    SSEProgressSubscriber,
    _as_outcome,
)
from src.interface.api.services.sse_operations import build_terminal_event

_EVENT_NAMES = frozenset({
    getattr(WorkflowConstants, name)
    for name in dir(WorkflowConstants)
    if name.startswith("SSE_EVENT_") and name != "SSE_EVENT_ID_RUN_ACCEPTED"
})


def _validated(frame: dict[str, object]) -> dict[str, object]:
    """Re-validate a queued frame's payload against its registered model."""
    event = frame["event"]
    assert isinstance(event, str)
    schema = SSE_EVENT_SCHEMAS[event]
    data = frame["data"]
    assert isinstance(data, dict)
    _ = schema.model_validate(data)
    return data


class TestSchemaRegistry:
    def test_covers_every_wire_event_name(self) -> None:
        assert set(SSE_EVENT_SCHEMAS) == _EVENT_NAMES

    def test_complete_and_error_share_one_model(self) -> None:
        assert (
            SSE_EVENT_SCHEMAS[WorkflowConstants.SSE_EVENT_COMPLETE]
            is SSE_EVENT_SCHEMAS[WorkflowConstants.SSE_EVENT_ERROR]
        )

    def test_frame_omits_fields_the_producer_never_set(self) -> None:
        # exclude_unset is what keeps a shared model from adding null keys to a
        # producer that has no counts / no run to name.
        frame = sse_frame(
            "evt_1",
            WorkflowConstants.SSE_EVENT_COMPLETE,
            SseOperationTerminalEvent(operation_id="op-1", final_status="completed"),
        )
        assert frame["data"] == {"operation_id": "op-1", "final_status": "completed"}


class TestSubscriberPayloads:
    """Every frame ``SSEProgressSubscriber`` queues validates against its model."""

    @staticmethod
    async def _drain(queue: asyncio.Queue[object]) -> list[dict[str, object]]:
        frames: list[dict[str, object]] = []
        while not queue.empty():
            frame = queue.get_nowait()
            assert isinstance(frame, dict)
            frames.append(frame)
        return frames

    async def test_started_and_progress_payloads(self) -> None:
        registry = SSEOperationRegistry()
        subscriber = SSEProgressSubscriber(registry)
        queue = await registry.register("op-1")

        await subscriber.on_operation_started(
            ProgressOperation(
                operation_id="op-1",
                description="Import",
                total_items=10,
                status=OperationStatus.RUNNING,
            )
        )
        await subscriber.on_progress_event(
            create_progress_event(
                "op-1", current=5, total=10, message="Halfway", items_per_second=2.5
            )
        )

        started, progress = await self._drain(queue)
        assert _validated(started)["status"] == "running"
        assert _validated(progress)["items_per_second"] == 2.5

    async def test_sub_operation_payloads_carry_routing_ids(self) -> None:
        registry = SSEOperationRegistry()
        subscriber = SSEProgressSubscriber(registry)
        queue = await registry.register("op-1")
        await subscriber.on_operation_started(
            ProgressOperation(operation_id="op-1", status=OperationStatus.RUNNING)
        )
        _ = queue.get_nowait()

        await subscriber.on_operation_started(
            ProgressOperation(
                operation_id="sub-1",
                description="Fetching",
                total_items=5,
                status=OperationStatus.RUNNING,
                metadata={
                    "parent_operation_id": "op-1",
                    "phase": "fetch",
                    "node_type": "enricher",
                    "connector_playlist_identifier": "spotify-id",
                    "playlist_name": "Mix",
                },
            )
        )
        await subscriber.on_progress_event(
            create_progress_event(
                "sub-1",
                current=1,
                total=5,
                message="Working",
                phase="resolve",
                outcome="succeeded",
                resolved=3,
                unresolved=1,
                canonical_playlist_id="canonical-id",
            )
        )
        await subscriber.on_operation_completed("sub-1", OperationStatus.COMPLETED)

        for frame in await self._drain(queue):
            data = _validated(frame)
            assert data["parent_operation_id"] == "op-1"
            assert data["item_operation_id"] == "sub-1"

    async def test_unknown_metadata_types_are_dropped_not_fatal(self) -> None:
        # Metadata is caller-supplied JSON: a wrong-typed value must cost that
        # one field, never the whole event.
        registry = SSEOperationRegistry()
        subscriber = SSEProgressSubscriber(registry)
        queue = await registry.register("op-1")
        await subscriber.on_operation_started(
            ProgressOperation(operation_id="op-1", status=OperationStatus.RUNNING)
        )
        await subscriber.on_operation_started(
            ProgressOperation(
                operation_id="sub-1",
                status=OperationStatus.RUNNING,
                metadata={"parent_operation_id": "op-1"},
            )
        )
        _ = await self._drain(queue)

        await subscriber.on_progress_event(
            create_progress_event(
                "sub-1",
                current=1,
                total=5,
                message="Working",
                phase=17,
                outcome="not-a-real-outcome",
                resolved="many",
            )
        )

        (frame,) = await self._drain(queue)
        data = _validated(frame)
        assert data["phase"] is None
        assert data["outcome"] is None
        assert data["resolved"] is None


class TestBuildTerminalEvent:
    def test_long_operation_complete_payload(self) -> None:
        run_id = uuid4()
        frame = build_terminal_event(
            "evt_final",
            WorkflowConstants.SSE_EVENT_COMPLETE,
            "op-1",
            "completed",
            run_id=run_id,
            operation_type="import_lastfm_history",
            counts={"track_plays": 12},
        )

        data = _validated(frame)
        assert data["run_id"] == str(run_id)
        assert data["counts"] == {"track_plays": 12}
        assert data["touched"] == list(touches_for("import_lastfm_history"))

    def test_preview_complete_payload(self) -> None:
        frame = build_terminal_event(
            "evt_final",
            WorkflowConstants.SSE_EVENT_PREVIEW_COMPLETE,
            "op-1",
            "completed",
            operation_type="workflow_preview",
            output_tracks=[{"id": "1", "title": "Song"}],
            total_track_count=1,
            metric_columns=["lastfm_user_playcount"],
            node_summaries=[
                {
                    "node_id": "n1",
                    "node_type": "source.playlist",
                    "track_count": 1,
                    "sample_titles": ["Song"],
                }
            ],
            duration_ms=42,
        )

        data = _validated(frame)
        assert data["node_summaries"] == [
            {
                "node_id": "n1",
                "node_type": "source.playlist",
                "track_count": 1,
                "sample_titles": ["Song"],
            }
        ]

    def test_unknown_field_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="not_a_field"):
            _ = build_terminal_event(
                "evt_final",
                WorkflowConstants.SSE_EVENT_COMPLETE,
                "op-1",
                "completed",
                not_a_field=1,
            )

    def test_unregistered_event_name_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="No SSE payload schema"):
            _ = build_terminal_event("evt_final", "not_an_event", "op-1", "completed")


class TestNodeStatusPayload:
    """``node_status`` is built in the application layer, which cannot import
    this module — the observer's payload shape is pinned here instead."""

    async def test_observer_payload_validates(self) -> None:
        queue: asyncio.Queue[object] = asyncio.Queue()
        observer = PreviewNodeObserver(sse_queue=queue)
        await observer.on_node_starting(
            NodeExecutionEvent(
                task_def=WorkflowTaskDef(id="n1", type="source.playlist"),
                execution_order=1,
                total_nodes=3,
                duration_ms=12,
                output_track_count=4,
            )
        )

        frame = queue.get_nowait()
        assert isinstance(frame, dict)
        assert frame["event"] == WorkflowConstants.SSE_EVENT_NODE_STATUS
        data = frame["data"]
        assert isinstance(data, dict)
        event = SseNodeStatusEvent.model_validate(data)
        assert event.status == "running"


class TestSubOperationOutcomeVocabulary:
    """``outcome`` crosses three modules that cannot import each other's
    literals — the application producer, the API-layer narrower, and the wire
    schema. They share one alias in ``config.constants``; this pins that none of
    them restates it."""

    def test_wire_alias_is_the_shared_vocabulary(self) -> None:
        assert SseSubOperationOutcome.__value__ is SubOperationOutcome

    def test_the_narrower_derives_its_accepted_set_from_the_shared_alias(
        self,
    ) -> None:
        assert frozenset(get_args(SubOperationOutcome.__value__)) == _VALID_OUTCOMES

    def test_the_producer_is_typed_with_the_shared_alias(self) -> None:
        producer = ImportConnectorPlaylistsAsCanonicalUseCase._emit_sub_outcome
        assert inspect.get_annotations(producer)["outcome"] is SubOperationOutcome

    @pytest.mark.parametrize("outcome", get_args(SubOperationOutcome.__value__))
    def test_every_declared_outcome_survives_the_narrower(self, outcome: str) -> None:
        assert _as_outcome(outcome) == outcome

    def test_absent_outcome_is_not_reported_as_a_drop(self) -> None:
        # Most sub-progress ticks carry no outcome at all; warning on those
        # would bury the one case worth reading.
        with structlog.testing.capture_logs() as logs:
            assert _as_outcome(None) is None

        assert logs == []

    def test_unknown_outcome_is_dropped_with_a_warning(self) -> None:
        with structlog.testing.capture_logs() as logs:
            assert _as_outcome("not-a-real-outcome") is None

        assert [(e["log_level"], e["outcome"]) for e in logs] == [
            ("warning", "not-a-real-outcome")
        ]


class TestFinalStatusVocabulary:
    """``SseFinalStatus`` used to restate ``RunStatus``'s six members by hand;
    it now aliases it directly, so the two can never drift apart."""

    def test_wire_alias_is_the_run_status_vocabulary(self) -> None:
        # ``RunStatus`` itself is a plain ``Literal`` alias (not a PEP 695
        # ``type`` statement), so ``SseFinalStatus.__value__`` resolves straight
        # to it — the same check ``export_openapi`` effectively performs.
        assert SseFinalStatus.__value__ is RunStatus

    def test_openapi_would_emit_the_same_enum(self) -> None:
        assert get_args(SseFinalStatus.__value__) == get_args(RunStatus)


class TestStatusEnumsPinnedToLiterals:
    """``SseOperationStatus`` / ``SseProgressStatus`` restate the domain
    ``OperationStatus`` / ``ProgressStatus`` enums as wire literals. Pinned both
    directions, like the ``SyncTarget`` literal is pinned to its registry in
    ``test_cache_tags.py``: an enum member missing from the literal is a type
    error already; a literal member with no enum backing it is not, and would
    ship an OpenAPI value the server can never produce."""

    def test_operation_status_literal_matches_the_enum(self) -> None:
        assert set(get_args(SseOperationStatus.__value__)) == {
            member.value for member in OperationStatus
        }

    def test_progress_status_literal_matches_the_enum(self) -> None:
        assert set(get_args(SseProgressStatus.__value__)) == {
            member.value for member in ProgressStatus
        }
