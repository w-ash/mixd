"""Repository for chat feedback persistence.

Write-once: feedback is recorded by the thumbs UI and never updated, so
``save`` is the only method — there is no update/delete path.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.domain.entities.chat_feedback import ChatFeedback
from src.infrastructure.persistence.database.db_models import DBChatFeedback
from src.infrastructure.persistence.repositories.base_repo import BaseRepository
from src.infrastructure.persistence.repositories.mappers import SimpleMapperFactory
from src.infrastructure.persistence.repositories.repo_decorator import db_operation

logger = get_logger(__name__)

ChatFeedbackMapper = SimpleMapperFactory.create(DBChatFeedback, ChatFeedback)


class ChatFeedbackRepository(BaseRepository[DBChatFeedback, ChatFeedback]):
    """Persistence for ``chat_feedback`` rows — insert-only, no RLS on this table."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(
            session=session,
            model_class=DBChatFeedback,
            mapper=ChatFeedbackMapper(),
        )

    @db_operation("save_chat_feedback")
    async def save(self, feedback: ChatFeedback) -> ChatFeedback:
        """Insert a new feedback row and return it as persisted.

        The entity carries ``user_id`` itself — there is no separate
        ``user_id`` keyword because feedback is written once and never
        looked up or mutated by id, so there is no cross-tenant access
        surface to guard here (unlike ``schedules``' CRUD methods).
        """
        db_row = DBChatFeedback(
            id=feedback.id,
            user_id=feedback.user_id,
            prompt=feedback.prompt,
            generated_workflow_def=dict(feedback.generated_workflow_def),
            signal=feedback.signal,
            note=feedback.note,
        )
        self.session.add(db_row)
        await self.session.flush()
        return await ChatFeedbackMapper.to_domain(db_row)


__all__ = ["ChatFeedbackRepository"]
