"""Integration tests for POST /api/v1/chat/feedback."""

from uuid import UUID

import httpx2
from sqlalchemy import select

from src.infrastructure.persistence.database.db_connection import get_session
from src.infrastructure.persistence.database.models import DBChatFeedback

_BODY = {
    "prompt": "build me a chill weekend playlist",
    "generated_workflow_def": {
        "id": "chill-weekend",
        "name": "Chill Weekend",
        "tasks": [{"id": "src", "type": "source.liked_tracks", "config": {}}],
    },
    "signal": "positive",
}


async def test_feedback_persists_with_full_context(client: httpx2.AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/chat/feedback", json={**_BODY, "signal": "negative", "note": "meh"}
    )

    assert resp.status_code == 201
    feedback_id = UUID(resp.json()["id"])
    async with get_session() as session:
        row = (
            await session.execute(
                select(DBChatFeedback).where(DBChatFeedback.id == feedback_id)
            )
        ).scalar_one()
    assert row.user_id == "default"
    assert row.prompt == "build me a chill weekend playlist"
    assert row.generated_workflow_def == _BODY["generated_workflow_def"]
    assert row.signal == "negative"
    assert row.note == "meh"


async def test_feedback_without_note_is_valid(client: httpx2.AsyncClient) -> None:
    resp = await client.post("/api/v1/chat/feedback", json=_BODY)

    assert resp.status_code == 201


async def test_bad_signal_rejected(client: httpx2.AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/chat/feedback", json={**_BODY, "signal": "amazing"}
    )

    assert resp.status_code == 422
