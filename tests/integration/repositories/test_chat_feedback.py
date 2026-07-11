"""Integration tests for ChatFeedbackRepository.

Exercises real SQL behavior the unit suite can't reach: the JSONB round-trip
for ``generated_workflow_def`` and the nullable ``note`` column.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.chat_feedback import ChatFeedback
from src.infrastructure.persistence.database.db_models import DBChatFeedback
from src.infrastructure.persistence.repositories.chat_feedback import (
    ChatFeedbackRepository,
)


class TestChatFeedbackSave:
    """``save`` inserts a row and returns it mapped back to the domain entity."""

    async def test_save_persists_and_returns_entity(
        self, db_session: AsyncSession
    ) -> None:
        repo = ChatFeedbackRepository(db_session)
        feedback = ChatFeedback(
            user_id="user-a",
            prompt="build me a workout playlist",
            generated_workflow_def={
                "nodes": [{"type": "source.playlist", "config": {"limit": 50}}],
                "edges": [],
            },
            signal="positive",
            note="great match",
        )

        saved = await repo.save(feedback)

        assert saved.id == feedback.id
        assert saved.user_id == "user-a"
        assert saved.prompt == "build me a workout playlist"
        assert saved.signal == "positive"
        assert saved.note == "great match"
        assert saved.created_at is not None

    async def test_generated_workflow_def_round_trips_as_jsonb(
        self, db_session: AsyncSession
    ) -> None:
        repo = ChatFeedbackRepository(db_session)
        workflow_def = {
            "nodes": [
                {"type": "filter.tag", "config": {"tags": ["chill", "focus"]}},
                {"type": "sort.recency", "config": {"direction": "desc"}},
            ],
            "edges": [{"from": 0, "to": 1}],
            "meta": {"version": 1, "nested": {"deep": True}},
        }
        feedback = ChatFeedback(
            user_id="user-b",
            prompt="sort by recency",
            generated_workflow_def=workflow_def,
            signal="negative",
        )

        saved = await repo.save(feedback)
        db_session.expire_all()

        result = await db_session.execute(
            select(DBChatFeedback).where(DBChatFeedback.id == saved.id)
        )
        db_row = result.scalar_one()
        assert db_row.generated_workflow_def == workflow_def

    async def test_note_is_nullable(self, db_session: AsyncSession) -> None:
        repo = ChatFeedbackRepository(db_session)
        feedback = ChatFeedback(
            user_id="user-c",
            prompt="no note given",
            generated_workflow_def={"nodes": []},
            signal="positive",
            note=None,
        )

        saved = await repo.save(feedback)

        assert saved.note is None
