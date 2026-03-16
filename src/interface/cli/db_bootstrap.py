"""CLI database bootstrap — seeds workflow templates on first use.

Mirrors the FastAPI lifespan template seeding (src/interface/api/app.py)
so CLI and API share the same database-backed workflow definitions.
Called lazily from workflow commands, not on every CLI invocation.
"""

from src.config.logging import get_logger

logger = get_logger(__name__)

_db_ready = False


async def ensure_cli_db_ready() -> None:
    """Seed workflow templates into the database (idempotent).

    Guarded by a per-process flag so repeated calls within the same
    CLI session are no-ops. Gracefully handles missing migrations.
    """
    global _db_ready
    if _db_ready:
        return

    try:
        from src.application.services.workflow_template_seeder import (
            seed_workflow_templates,
        )
        from src.infrastructure.persistence.database.db_connection import get_session
        from src.infrastructure.persistence.repositories.factories import (
            get_unit_of_work,
        )

        async with get_session() as session:
            uow = get_unit_of_work(session)
            await seed_workflow_templates(uow)
    except Exception as e:
        logger.warning("Failed to seed workflow templates", error=str(e))

    _db_ready = True
