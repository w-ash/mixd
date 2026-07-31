"""Last.fm play checkpoints must round-trip through a real RLS-bound session.

``sync_checkpoints`` is FORCE ROW LEVEL SECURITY with a USING-only
``user_isolation`` policy (migrations 007 + 011), so Postgres reuses the USING
expression as the INSERT check: a row written under any key other than the
transaction's ``app.user_id`` is *rejected*, and a row read under any other key
is invisible. The importer used to key both on the Last.fm username, so every
write was refused and every read came back empty — the incremental import
re-scanned the same 30-day default window forever.

The mock-UoW unit tests cannot see that: a mock accepts any key. These tests run
the real repository against Postgres under a role that cannot bypass RLS.

Two things the shared ``db_session`` fixture does NOT provide, both supplied here:
- ``init_db()`` builds the schema with ``metadata.create_all``, which creates no
  policies at all. The fixture below applies the 007 + 011 DDL verbatim.
- The testcontainer's own role is a superuser, and superusers bypass RLS even
  under FORCE. The fixture connects as a dedicated ``NOSUPERUSER NOBYPASSRLS``
  role instead.

``test_username_keyed_write_is_rejected_by_rls`` is the proof both took effect:
it performs exactly the write the old code did and asserts Postgres refuses it.
"""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, time, timedelta
from typing import override
from unittest.mock import AsyncMock, Mock

import pytest
from pytest_asyncio import fixture as async_fixture
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.domain.entities import PlayRecord, SyncCheckpoint
from src.domain.repositories.play import LastfmImportParams
from src.infrastructure.connectors.lastfm.play_importer import LastfmPlayImporter
from src.infrastructure.persistence.database.db_connection import create_session_factory
from src.infrastructure.persistence.database.user_context import user_context
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from src.infrastructure.persistence.repositories.sync import SyncCheckpointRepository

_RLS_ROLE = "rls_probe"
_RLS_PASSWORD = "rls_probe_pw"
_TABLE = "sync_checkpoints"
_MIXD_USER = "mixd-user-rls-probe"
_LASTFM_ACCOUNT = "lastfm_account_rls_probe"
_DEFAULT_WINDOW_DAYS = 30

# Mirrors alembic 007 (ENABLE + USING-only policy) and 011 (FORCE).
_ENABLE_RLS = (
    f"DROP POLICY IF EXISTS user_isolation ON {_TABLE}",
    f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY",
    f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY",
    (
        f"CREATE POLICY user_isolation ON {_TABLE} "
        f"FOR ALL USING (user_id = current_setting('app.user_id', TRUE))"
    ),
)
_DISABLE_RLS = (
    f"DROP POLICY IF EXISTS user_isolation ON {_TABLE}",
    f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY",
    f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY",
)
_GRANT_ROLE = (
    f"GRANT USAGE ON SCHEMA public TO {_RLS_ROLE}",
    f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLE} TO {_RLS_ROLE}",
)
# The role outlives the fixture (dropping it would mean revoking every grant),
# so creating it is idempotent.
_CREATE_ROLE = f"""
DO $$
BEGIN
    CREATE ROLE {_RLS_ROLE} LOGIN PASSWORD '{_RLS_PASSWORD}'
        NOSUPERUSER NOBYPASSRLS;
EXCEPTION WHEN duplicate_object THEN
    NULL;
END
$$
"""
_DELETE_TEST_ROWS = "DELETE FROM sync_checkpoints WHERE user_id = :uid"


@async_fixture
async def rls_sessions(
    postgres_url: str,
    _init_test_schema: None,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Session factory bound to a role that cannot bypass RLS on sync_checkpoints.

    Deliberately not the shared ``db_session``: that one is a superuser on a
    policy-free schema, where every assertion in this module would pass vacuously.

    The factory is built by production ``create_session_factory``, so the real
    ``after_begin`` hook issues ``SET LOCAL app.user_id`` from the ``user_context``
    contextvar — the same binding ``execute_use_case`` gives a request. That hook
    is registered on the shared sync ``Session`` class, exactly as the application
    registers it at startup, so it is left in place on teardown.
    """
    admin = create_async_engine(postgres_url)
    async with admin.begin() as conn:
        _ = await conn.execute(sa.text(_CREATE_ROLE))
        for statement in (*_GRANT_ROLE, *_ENABLE_RLS):
            _ = await conn.execute(sa.text(statement))

    role_url = (
        make_url(postgres_url)
        .set(username=_RLS_ROLE, password=_RLS_PASSWORD)
        .render_as_string(hide_password=False)
    )
    engine = create_async_engine(role_url)

    try:
        yield create_session_factory(engine)
    finally:
        await engine.dispose()
        # These tests COMMIT, so savepoint rollback cannot undo them: clean up as
        # the superuser and restore the table for the rest of the worker's tests.
        async with admin.begin() as conn:
            _ = await conn.execute(
                sa.text(_DELETE_TEST_ROWS),
                {"uid": _MIXD_USER},
            )
            for statement in _DISABLE_RLS:
                _ = await conn.execute(sa.text(statement))
        await admin.dispose()


class _RecordingImporter(LastfmPlayImporter):
    """Importer whose per-day fetch is stubbed and records the days requested.

    Subclassed rather than monkeypatched so the real ``_fetch_data`` →
    ``_resolve_checkpoint`` → ``_fetch_date_range_strategy`` →
    ``_save_day_checkpoint`` path runs untouched; only the HTTP call is replaced.
    """

    fetched: list[date]

    def __init__(self) -> None:
        super().__init__(lastfm_connector=Mock(), token_storage=AsyncMock())
        self.fetched = []

    @override
    async def _fetch_day_records(
        self,
        username: str,
        day_start: datetime,
        day_end: datetime,
        current_date: date,
    ) -> list[PlayRecord]:
        _ = username, day_start, day_end
        self.fetched.append(current_date)
        return [
            PlayRecord(
                artist_name="Test Artist",
                track_name="Test Track",
                played_at=datetime.combine(current_date, time(12, 0), UTC),
                service="lastfm",
            )
        ]


class TestCheckpointRls:
    """The checkpoint must survive a real COMMIT under the user_isolation policy."""

    async def test_username_keyed_write_is_rejected_by_rls(
        self, rls_sessions: async_sessionmaker[AsyncSession]
    ) -> None:
        """The pre-fix key is refused — which is what makes the rest meaningful.

        If RLS were inactive (no policy, or a bypassing role) this write would
        succeed and this module would be asserting nothing.
        """
        with user_context(_MIXD_USER):
            async with rls_sessions() as session:
                repo = SyncCheckpointRepository(session)

                with pytest.raises(DBAPIError) as excinfo:
                    _ = await repo.save_sync_checkpoint(
                        SyncCheckpoint(
                            user_id=_LASTFM_ACCOUNT,  # the pre-fix key
                            service="lastfm",
                            entity_type="plays",
                            last_timestamp=datetime.now(UTC),
                            cursor="2026-07-01",
                        )
                    )

        assert "row-level security" in str(excinfo.value).lower()

    async def test_incremental_run_resumes_from_persisted_checkpoint(
        self, rls_sessions: async_sessionmaker[AsyncSession]
    ) -> None:
        """The second run starts at the checkpoint day, not the 30-day default.

        Run 1 imports an explicit three-day window, committing a checkpoint per
        day. Run 2 asks for no dates at all: if the checkpoint round-tripped it
        re-processes the last completed day and catches up to today; if it did
        not, ``_determine_date_range`` falls back to ``now - 30 days``.
        """
        today = datetime.now(UTC).date()
        window_start = today - timedelta(days=5)
        window_end = today - timedelta(days=3)

        first_run = await self._run_import(
            rls_sessions,
            LastfmImportParams(
                username=_LASTFM_ACCOUNT,
                from_date=datetime.combine(window_start, time.min, UTC),
                to_date=datetime.combine(window_end, time.max, UTC),
            ),
        )

        assert first_run == [window_start + timedelta(days=n) for n in range(3)]

        second_run = await self._run_import(
            rls_sessions, LastfmImportParams(username=_LASTFM_ACCOUNT)
        )

        # window_end is re-processed (to catch late scrobbles), then today.
        assert second_run == [window_end + timedelta(days=n) for n in range(4)]

    async def test_checkpoint_from_another_account_is_not_resumed(
        self, rls_sessions: async_sessionmaker[AsyncSession]
    ) -> None:
        """Switching Last.fm account falls back to the default window.

        Nothing clears the checkpoint when the connected account changes, so
        without the cursor's account tag the new account would silently resume
        the old one's position and never see its earlier history.
        """
        today = datetime.now(UTC).date()
        stale_day = today - timedelta(days=3)

        with user_context(_MIXD_USER):
            async with rls_sessions() as session:
                await _RecordingImporter()._save_day_checkpoint(
                    user_id=_MIXD_USER,
                    account="the_old_account",
                    completed_date=stale_day,
                    day_end=datetime.combine(stale_day, time.max, UTC),
                    uow=get_unit_of_work(session),
                )
                await session.commit()

        fetched = await self._run_import(
            rls_sessions, LastfmImportParams(username="a_different_account")
        )

        assert min(fetched) == today - timedelta(days=_DEFAULT_WINDOW_DAYS)

    @staticmethod
    async def _run_import(
        sessions: async_sessionmaker[AsyncSession], params: LastfmImportParams
    ) -> list[date]:
        """Run one ``_fetch_data`` pass in its own RLS-bound session, committed."""
        importer = _RecordingImporter()
        with user_context(_MIXD_USER):
            async with sessions() as session:
                _ = await importer._fetch_data(
                    params,
                    uow=get_unit_of_work(session),
                    user_id=_MIXD_USER,
                    batch_id="rls-probe-batch",
                    import_timestamp=datetime.now(UTC),
                )
                await session.commit()
        return importer.fetched
