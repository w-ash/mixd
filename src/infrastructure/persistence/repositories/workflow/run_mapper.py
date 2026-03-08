"""Mapper between DBWorkflowRun/DBWorkflowRunNode and domain entities."""

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: JSON column produces heterogeneous dicts

from typing import cast

import attrs
from attrs import define

from src.domain.entities.workflow import (
    RunStatus,
    WorkflowDef,
    WorkflowRun,
    WorkflowRunNode,
    parse_workflow_def,
)
from src.infrastructure.persistence.database.db_models import (
    DBWorkflowRun,
    DBWorkflowRunNode,
)


@define(frozen=True, slots=True)
class WorkflowRunMapper:
    """Bidirectional mapper for workflow run entities."""

    @staticmethod
    def node_to_domain(db: DBWorkflowRunNode) -> WorkflowRunNode:
        return WorkflowRunNode(
            id=db.id,
            run_id=db.run_id,
            node_id=db.node_id,
            node_type=db.node_type,
            status=cast(RunStatus, db.status),
            started_at=db.started_at,
            completed_at=db.completed_at,
            duration_ms=db.duration_ms,
            input_track_count=db.input_track_count,
            output_track_count=db.output_track_count,
            error_message=db.error_message,
            execution_order=db.execution_order,
        )

    @staticmethod
    def node_to_db(node: WorkflowRunNode, run_id: int) -> DBWorkflowRunNode:
        return DBWorkflowRunNode(
            node_id=node.node_id,
            node_type=node.node_type,
            status=node.status,
            started_at=node.started_at,
            completed_at=node.completed_at,
            duration_ms=node.duration_ms,
            input_track_count=node.input_track_count,
            output_track_count=node.output_track_count,
            error_message=node.error_message,
            execution_order=node.execution_order,
            run_id=run_id,
        )

    @staticmethod
    def to_domain(
        db: DBWorkflowRun,
        *,
        include_nodes: bool = False,
        include_definition: bool = True,
    ) -> WorkflowRun:
        # Skip JSON parsing for summary/list views where definition is never used
        definition = (
            parse_workflow_def(db.definition_snapshot)
            if include_definition
            else WorkflowDef(id="", name="")
        )
        nodes: list[WorkflowRunNode] = []
        if include_nodes:
            nodes = [WorkflowRunMapper.node_to_domain(n) for n in db.nodes]
        return WorkflowRun(
            id=db.id,
            workflow_id=db.workflow_id,
            status=cast(RunStatus, db.status),
            definition_snapshot=definition,
            started_at=db.started_at,
            completed_at=db.completed_at,
            duration_ms=db.duration_ms,
            output_track_count=db.output_track_count,
            output_playlist_id=db.output_playlist_id,
            error_message=db.error_message,
            nodes=nodes,
            created_at=db.created_at,
        )

    @staticmethod
    def to_db(run: WorkflowRun) -> DBWorkflowRun:
        definition_dict = attrs.asdict(run.definition_snapshot)
        return DBWorkflowRun(
            workflow_id=run.workflow_id,
            status=run.status,
            definition_snapshot=definition_dict,
            started_at=run.started_at,
            completed_at=run.completed_at,
            duration_ms=run.duration_ms,
            output_track_count=run.output_track_count,
            output_playlist_id=run.output_playlist_id,
            error_message=run.error_message,
        )
