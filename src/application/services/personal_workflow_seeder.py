"""Seeds user-owned workflow JSON definitions from ``definitions/personal/`` for a dev user.

Every seeded row is a normal, editable, user-owned workflow. Useful in alpha
development where the database is occasionally recreated and re-clicking through
the workflow builder for personal workflows is friction.

Idempotent: if a user already has a workflow with the same ``definition.id``,
the existing row's definition is updated in place rather than duplicated.
"""

from attrs import evolve

from src.application.workflows.workflow_loader import (
    get_definitions_dir,
    list_workflow_defs,
)
from src.config.logging import get_logger
from src.domain.entities.workflow import Workflow
from src.domain.repositories.interfaces import UnitOfWorkProtocol

logger = get_logger(__name__)


async def seed_personal_workflows(uow: UnitOfWorkProtocol, user_id: str) -> int:
    """Load JSONs from ``definitions/personal/`` and upsert them for ``user_id``.

    Returns the number of personal workflows created or updated.
    """
    workflow_defs = list_workflow_defs(get_definitions_dir() / "personal")
    if not workflow_defs:
        logger.info("No personal workflow definitions found to seed")
        return 0

    count = 0
    async with uow:
        repo = uow.get_workflow_repository()
        existing_by_slug = {
            wf.definition.id: wf for wf in await repo.list_workflows(user_id=user_id)
        }

        for wf_def in workflow_defs:
            existing = existing_by_slug.get(wf_def.id)
            workflow = (
                evolve(existing, definition=wf_def)
                if existing
                else Workflow(user_id=user_id, definition=wf_def)
            )
            await repo.save_workflow(workflow)
            count += 1

        logger.info("Seeded personal workflows", count=count, user_id=user_id)

    return count
