"""Workflow repository mapper for domain-persistence conversions."""

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: JSON column produces heterogeneous dicts

from typing import override

import attrs
from attrs import define

from src.domain.entities.workflow import Workflow, parse_workflow_def
from src.infrastructure.persistence.database.db_models import DBWorkflow
from src.infrastructure.persistence.repositories.base_repo import BaseModelMapper


@define(frozen=True, slots=True)
class WorkflowMapper(BaseModelMapper[DBWorkflow, Workflow]):
    """Bidirectional mapper between DBWorkflow and domain Workflow."""

    @override
    @staticmethod
    def get_default_relationships() -> list[str]:
        return []

    @override
    @staticmethod
    async def to_domain(db_model: DBWorkflow) -> Workflow:
        definition = parse_workflow_def(db_model.definition)
        return Workflow(
            id=db_model.id,
            definition=definition,
            is_template=db_model.is_template,
            source_template=db_model.source_template,
            created_at=db_model.created_at,
            updated_at=db_model.updated_at,
        )

    @override
    @staticmethod
    def to_db(domain_model: Workflow) -> DBWorkflow:
        definition_dict = attrs.asdict(domain_model.definition)
        return DBWorkflow(
            name=domain_model.definition.name,
            description=domain_model.definition.description or None,
            definition=definition_dict,
            is_template=domain_model.is_template,
            source_template=domain_model.source_template,
        )
