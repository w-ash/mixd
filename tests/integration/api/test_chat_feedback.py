"""Integration tests for POST /api/v1/chat/feedback."""

import httpx

_BODY = {
    "prompt": "build me a chill weekend playlist",
    "generated_workflow_def": {
        "id": "chill-weekend",
        "name": "Chill Weekend",
        "tasks": [{"id": "src", "type": "source.liked_tracks", "config": {}}],
    },
    "signal": "positive",
}


async def test_feedback_persists_with_full_context(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/chat/feedback", json={**_BODY, "signal": "negative", "note": "meh"}
    )

    assert resp.status_code == 201
    assert resp.json()["id"]


async def test_feedback_without_note_is_valid(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/v1/chat/feedback", json=_BODY)

    assert resp.status_code == 201


async def test_bad_signal_rejected(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/chat/feedback", json={**_BODY, "signal": "amazing"}
    )

    assert resp.status_code == 422
