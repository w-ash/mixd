"""Seeds built-in workflow JSON definitions as read-only templates in the database.

Idempotent: checks source_template key before inserting, updates definition
on subsequent runs if the JSON changed. Called from FastAPI lifespan and
available from CLI.
"""

from src.config.logging import get_logger
from src.domain.entities.workflow import Workflow
from src.domain.repositories.interfaces import UnitOfWorkProtocol

logger = get_logger(__name__)


async def seed_workflow_templates(uow: UnitOfWorkProtocol) -> int:
    """Load JSON workflow definitions and upsert them as templates.

    Returns the number of templates created or updated.
    """
    from src.application.workflows.workflow_loader import list_workflow_defs

    workflow_defs = list_workflow_defs()
    if not workflow_defs:
        logger.info("No workflow definitions found to seed")
        return 0

    count = 0
    async with uow:
        repo = uow.get_workflow_repository()

        for wf_def in workflow_defs:
            existing = await repo.get_workflow_by_source_template(wf_def.id)

            if existing:
                # Update the definition if it changed
                updated = Workflow(
                    id=existing.id,
                    definition=wf_def,
                    is_template=True,
                    source_template=wf_def.id,
                    created_at=existing.created_at,
                )
                await repo.save_workflow(updated)
            else:
                # Create new template
                template = Workflow(
                    definition=wf_def,
                    is_template=True,
                    source_template=wf_def.id,
                )
                await repo.save_workflow(template)

            count += 1

        logger.info("Seeded workflow templates", count=count)

    return count
