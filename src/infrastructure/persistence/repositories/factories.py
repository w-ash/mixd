"""Repository factory functions for Clean Architecture compliance.

These factory functions handle session-aware repository creation while keeping
session management concerns in the infrastructure layer. Application layer
use cases depend only on domain protocols, not these factory functions.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.repositories.interfaces import UnitOfWorkProtocol
from src.infrastructure.persistence.unit_of_work import DatabaseUnitOfWork


def get_unit_of_work(session: AsyncSession) -> UnitOfWorkProtocol:
    """Get unit of work for transaction boundary management."""
    return DatabaseUnitOfWork(session)
