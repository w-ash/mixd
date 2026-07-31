"""Integration tests for GET/PATCH /api/v1/settings.

The whole data path here changed when the route moved off a module-level
repository singleton onto ``execute_use_case`` — including where the RLS user
context comes from — so these exercise the real request → use case → repo → DB
cycle rather than mocking the use case.

``user_settings`` is in the API conftest's preserved-table set (so the default
user context survives between tests), which means this module cleans up after
itself instead of relying on the shared truncate.
"""

from collections.abc import AsyncGenerator

import httpx2
import pytest
from sqlalchemy import delete

from src.infrastructure.persistence.database.db_connection import get_session
from src.infrastructure.persistence.database.db_models import DBUserSettings

_URL = "/api/v1/settings"


@pytest.fixture(autouse=True)
async def _clean_settings(client: httpx2.AsyncClient) -> AsyncGenerator[None]:
    """Drop settings rows either side of the test — the shared truncate spares them."""
    async with get_session() as session:
        await session.execute(delete(DBUserSettings))
    yield
    async with get_session() as session:
        await session.execute(delete(DBUserSettings))


class TestGetSettings:
    async def test_returns_defaults_when_never_set(
        self, client: httpx2.AsyncClient
    ) -> None:
        """A brand-new user must get a usable object, not a 404 or empty body."""
        resp = await client.get(_URL)

        assert resp.status_code == 200
        assert resp.json()["theme_mode"] == "dark"


class TestPatchSettings:
    async def test_patch_persists_and_is_readable(
        self, client: httpx2.AsyncClient
    ) -> None:
        patched = await client.patch(_URL, json={"theme_mode": "light"})

        assert patched.status_code == 200
        assert patched.json()["theme_mode"] == "light"

        # A separate request proves the write committed rather than living in
        # the request's own transaction.
        assert (await client.get(_URL)).json()["theme_mode"] == "light"

    async def test_empty_patch_leaves_settings_intact(
        self, client: httpx2.AsyncClient
    ) -> None:
        """``exclude_none`` means an all-null body is a no-op, not a wipe."""
        await client.patch(_URL, json={"theme_mode": "light"})

        resp = await client.patch(_URL, json={})

        assert resp.status_code == 200
        assert resp.json()["theme_mode"] == "light"

    async def test_rejects_wrong_type(self, client: httpx2.AsyncClient) -> None:
        resp = await client.patch(_URL, json={"theme_mode": 42})

        assert resp.status_code == 422
