"""The playlist ingest's continue-on-error loop must not poison its transaction.

A workflow ``source.playlist`` node died on
``InFailedSqlTransaction ... [SQL: SAVEPOINT sa_savepoint_67]``, which is never
the fault: it is what a savepoint request looks like once PostgreSQL has
already aborted the transaction. ``_resolve_and_ingest_tracks`` caught the
bulk ingest's failure, logged one line, and carried on issuing statements —
one retry per new track, then ``save_playlist`` — every one of which could
only fail the same way. The first error, the only one that named a cause, was
pushed out of the retained log by its own consequences.

v0.10.2.2 fixed exactly this for the inward resolvers' item loops and left
this loop uncovered. These tests pin the same contract here: the tolerated
ingest runs inside ``uow.savepoint()``, so one failure costs the batch attempt
and nothing else.

The second half of the file pins what the savepoint fix left open: *which*
fallback a failure earns. Per-track retry answers "one row in this batch is
bad", and the production failure was the opposite — a concurrent transaction
holding an identity key, where splitting the batch just queues N times on the
same contended index and resolves nothing. That race is now serialized at the
source by the per-user advisory lock in ``track/ingest_lock.py``: the two
canonical-track writers queue at the lock instead of inside
``uq_tracks_user_isrc``, and contention that survives it fails the run.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
import time
from unittest.mock import patch

import pytest
from pytest_asyncio import fixture as async_fixture
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    AsyncTransaction,
    async_sessionmaker,
    create_async_engine,
)

from src.application.services.connector_playlist_processing_service import (
    ConnectorPlaylistProcessingService,
)
from src.domain.entities import Artist, ConnectorTrack
from src.domain.entities.track import Track
from src.domain.repositories.errors import LOCK_NOT_AVAILABLE, postgres_sqlstate
from src.infrastructure.persistence.database.db_models import DBTrack
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from src.infrastructure.persistence.repositories.track.connector import (
    TrackConnectorRepository,
)
from src.infrastructure.persistence.repositories.track.core import TrackRepository
from src.infrastructure.persistence.repositories.track.ingest_lock import (
    ingest_lock_keys,
)

USER = "default"
CONNECTOR = "spotify"


def _connector_track(identifier: str, title: str) -> ConnectorTrack:
    return ConnectorTrack(
        connector_name=CONNECTOR,
        connector_track_identifier=identifier,
        title=title,
        artists=[Artist(name="Mount Kimbie")],
        album="Cold Spring Fault Less Youth",
        duration_ms=216399,
        isrc=f"GBBPW13{identifier[-5:]}",
        raw_metadata={},
        last_updated=datetime.now(UTC),
    )


BATCH = [
    _connector_track("sp_aaaaa", "You Took Your Time"),
    _connector_track("sp_bbbbb", "Made to Stray"),
    _connector_track("sp_ccccc", "Blood and Form"),
]


def _bulk_fails_batch_succeeds_singly(
    session: AsyncSession, calls: list[int] | None = None
):
    """Stand-in for the real first error: a genuine DB abort on the bulk call.

    The batch call executes a statement PostgreSQL rejects — leaving the
    transaction aborted exactly as a unique violation, lock timeout or
    statement timeout would — and then raises. Single-track calls run the real
    implementation, which is what the retry loop exists to reach.

    Deliberately not a bare ``RuntimeError``: a Python-side exception leaves
    the transaction perfectly usable, so it would not reproduce the bug at all.

    ``calls`` collects the size of every ingest attempt, so a test can tell the
    fallbacks apart: ``[3, 1, 1, 1]`` is the per-track loop, ``[3]`` a batch
    that failed outright.
    """
    real = TrackConnectorRepository.ingest_external_tracks_bulk

    async def fake(self, connector, tracks, *, user_id):
        if calls is not None:
            calls.append(len(tracks))
        if len(tracks) > 1:
            _ = await session.execute(text("SELECT 1 / 0"))
        return await real(self, connector, tracks, user_id=user_id)

    return patch.object(TrackConnectorRepository, "ingest_external_tracks_bulk", fake)


class _LockHolder:
    """The competing writer — a second session sitting on a lock we want."""

    def __init__(self, trans: AsyncTransaction) -> None:
        self._trans = trans

    async def release(self) -> None:
        """Commit-equivalent: end the transaction, dropping the lock."""
        if self._trans.is_active:
            await self._trans.rollback()


@pytest.fixture
def probe_engine(_test_engine: AsyncEngine) -> AsyncEngine:
    """The session engine, under a name a test may take as a parameter.

    Tests cannot inject ``_test_engine`` directly (ruff PT019 bans
    underscore-prefixed fixture parameters); the lock probe needs a connection
    of its own, so it borrows the engine through here.
    """
    return _test_engine


async def _ingest_lock_has_a_waiter(engine: AsyncEngine) -> bool:
    """Whether someone is queued on the ingest-lock class right now.

    Read from ``pg_locks`` rather than inferred from elapsed time: the ingest
    takes a few hundred milliseconds of its own, so a clock comparison cannot
    tell "waited for the lock" from "was simply slow".
    """
    class_id, _ = ingest_lock_keys(USER)
    async with engine.connect() as conn:
        return bool(
            (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                        "AND NOT granted AND classid::bigint = :class_id"
                    ),
                    {"class_id": class_id},
                )
            ).scalar_one()
        )


def _recording_ingest(calls: list[int]):
    """Run the real bulk ingest, noting the size of every attempt.

    No behaviour is faked: the contention comes from a second connection
    genuinely holding the user's ingest lock, which is what the implementation
    now waits on. ``calls`` is only how a test tells one batch attempt apart
    from a per-track split.
    """
    real = TrackConnectorRepository.ingest_external_tracks_bulk

    async def fake(self, connector, tracks, *, user_id):
        calls.append(len(tracks))
        return await real(self, connector, tracks, user_id=user_id)

    return patch.object(TrackConnectorRepository, "ingest_external_tracks_bulk", fake)


class TestBulkIngestFailureIsContained:
    async def test_per_track_retry_recovers_every_track(self, db_session: AsyncSession):
        """The retry loop's whole purpose — reachable only if the transaction
        survived the bulk failure."""
        uow = get_unit_of_work(db_session)
        service = ConnectorPlaylistProcessingService()

        with _bulk_fails_batch_succeeds_singly(db_session):
            resolved = await service._resolve_and_ingest_tracks(
                BATCH, CONNECTOR, uow, user_id=USER
            )

        assert sorted(resolved) == ["sp_aaaaa", "sp_bbbbb", "sp_ccccc"]

    async def test_the_recovered_tracks_are_really_persisted(
        self, db_session: AsyncSession
    ):
        """Not just returned: the savepoint rolled back the failed attempt and
        nothing else, so the individually-ingested rows are in the database."""
        uow = get_unit_of_work(db_session)
        service = ConnectorPlaylistProcessingService()
        before = (
            await db_session.execute(select(func.count()).select_from(DBTrack))
        ).scalar_one()

        with _bulk_fails_batch_succeeds_singly(db_session):
            _ = await service._resolve_and_ingest_tracks(
                BATCH, CONNECTOR, uow, user_id=USER
            )

        after = (
            await db_session.execute(select(func.count()).select_from(DBTrack))
        ).scalar_one()
        assert after == before + len(BATCH)

    async def test_the_transaction_is_still_usable_afterwards(
        self, db_session: AsyncSession
    ):
        """``save_playlist`` runs next on this same transaction. Before the fix
        it opened a savepoint on an aborted connection and that — not the real
        error — is what the workflow run reported."""
        uow = get_unit_of_work(db_session)
        service = ConnectorPlaylistProcessingService()

        with _bulk_fails_batch_succeeds_singly(db_session):
            _ = await service._resolve_and_ingest_tracks(
                BATCH, CONNECTOR, uow, user_id=USER
            )

        async with uow.savepoint():
            assert (await db_session.execute(text("SELECT 1"))).scalar_one() == 1

    async def test_the_first_error_is_logged_with_its_traceback(
        self, db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
    ):
        """The first error is the only one that explains anything, so it is an
        ERROR carrying its traceback — and no cascade line follows it.

        Asserted via capsys, not caplog: structlog renders straight to stdout
        here (same reason as ``test_track_repository_integration``).
        """
        uow = get_unit_of_work(db_session)
        service = ConnectorPlaylistProcessingService()

        with _bulk_fails_batch_succeeds_singly(db_session):
            _ = await service._resolve_and_ingest_tracks(
                BATCH, CONNECTOR, uow, user_id=USER
            )

        out = capsys.readouterr().out
        assert "Bulk ingest of 3 spotify tracks failed" in out
        assert "[error" in out, "the bulk failure must be logged at ERROR"
        assert "division by zero" in out, "the traceback has to name the cause"
        # The signature of the cascade this fix removes.
        assert "InFailedSqlTransaction" not in out
        assert "current transaction is aborted" not in out

    async def test_a_bad_batch_still_takes_the_per_track_path(
        self, db_session: AsyncSession
    ):
        """The contention branch must not swallow the ordinary case: an error
        that isn't contention is still answered one track at a time."""
        uow = get_unit_of_work(db_session)
        service = ConnectorPlaylistProcessingService()
        calls: list[int] = []

        with _bulk_fails_batch_succeeds_singly(db_session, calls):
            resolved = await service._resolve_and_ingest_tracks(
                BATCH, CONNECTOR, uow, user_id=USER
            )

        assert sorted(resolved) == ["sp_aaaaa", "sp_bbbbb", "sp_ccccc"]
        assert calls == [3, 1, 1, 1], (
            "a non-contention failure must still be isolated per track"
        )


_SERIALIZATION_USER = "ingest-lock-serialization-probe"


class TestTheIngestLockAbsorbsContention:
    """The two canonical-track writers queue at the lock, not in the index.

    ``RunWorkflowUseCase`` starts a play import and deliberately doesn't await
    it, so an inward resolver's ``save_tracks`` routinely holds uncommitted
    ``tracks`` rows while this ingest runs. Both writers now take the per-user
    advisory lock before any row lock, so the loser waits at the lock and then
    proceeds. Only a holder that outlives ``lock_timeout`` still reaches the
    service, and that is a loud failure — never a per-track split, which would
    queue on the same lock once per track and resolve nothing.
    """

    @pytest.fixture
    async def lock_holder(self, _test_engine: AsyncEngine):
        """A second connection holding *this user's* ingest lock.

        The same (class_id, obj_id) pair the implementation derives — so this
        is the real contention the ingest was built to wait on, not a stand-in
        key. A fixture rather than an inline context manager because
        ``db_session`` is savepoint-isolated: the holder has to be a separate
        connection to block anything.
        """
        class_id, obj_id = ingest_lock_keys(USER)
        async with _test_engine.connect() as conn:
            trans = await conn.begin()
            _ = await conn.exec_driver_sql(
                f"SELECT pg_advisory_xact_lock({class_id}, {obj_id})"
            )
            holder = _LockHolder(trans)
            try:
                yield holder
            finally:
                await holder.release()

    @staticmethod
    async def _release_once_the_ingest_queues(
        engine: AsyncEngine, holder: _LockHolder, seen: list[bool]
    ) -> None:
        """Wait for the ingest to appear at the lock, then let it through."""
        deadline = time.perf_counter() + 5.0
        while time.perf_counter() < deadline:
            if await _ingest_lock_has_a_waiter(engine):
                seen.append(True)
                break
            await asyncio.sleep(0.01)
        await holder.release()

    async def test_the_ingest_waits_for_the_lock_and_then_succeeds(
        self,
        db_session: AsyncSession,
        lock_holder: _LockHolder,
        probe_engine: AsyncEngine,
    ):
        """The ordinary case: a holder that finishes inside the lock budget
        costs the ingest a wait, not a failure."""
        uow = get_unit_of_work(db_session)
        service = ConnectorPlaylistProcessingService()
        calls: list[int] = []
        queued: list[bool] = []
        _ = await db_session.execute(text("SET LOCAL lock_timeout = '10s'"))

        with _recording_ingest(calls):
            async with asyncio.TaskGroup() as tg:
                _ = tg.create_task(
                    self._release_once_the_ingest_queues(
                        probe_engine, lock_holder, queued
                    )
                )
                resolved = await service._resolve_and_ingest_tracks(
                    BATCH, CONNECTOR, uow, user_id=USER
                )

        assert sorted(resolved) == ["sp_aaaaa", "sp_bbbbb", "sp_ccccc"]
        assert queued == [True], "the ingest must have queued on the held lock"
        assert calls == [3], "one batch, no retry and no per-track split"

    async def test_the_waited_out_tracks_are_really_persisted(
        self,
        db_session: AsyncSession,
        lock_holder: _LockHolder,
        probe_engine: AsyncEngine,
    ):
        """Not just returned: the rows are in the database behind the wait."""
        uow = get_unit_of_work(db_session)
        service = ConnectorPlaylistProcessingService()
        _ = await db_session.execute(text("SET LOCAL lock_timeout = '10s'"))
        before = (
            await db_session.execute(select(func.count()).select_from(DBTrack))
        ).scalar_one()

        async with asyncio.TaskGroup() as tg:
            _ = tg.create_task(
                self._release_once_the_ingest_queues(probe_engine, lock_holder, [])
            )
            _ = await service._resolve_and_ingest_tracks(
                BATCH, CONNECTOR, uow, user_id=USER
            )

        after = (
            await db_session.execute(select(func.count()).select_from(DBTrack))
        ).scalar_one()
        assert after == before + len(BATCH)

    async def test_a_holder_outliving_the_lock_timeout_fails_the_run(
        self, db_session: AsyncSession, lock_holder: _LockHolder
    ):
        """No retry, no split, no empty map. An empty map would reach the user
        as a green run whose every entry reads "Couldn't match"."""
        _ = lock_holder  # held for the whole test
        uow = get_unit_of_work(db_session)
        service = ConnectorPlaylistProcessingService()
        calls: list[int] = []
        _ = await db_session.execute(text("SET LOCAL lock_timeout = '200ms'"))

        with _recording_ingest(calls):
            with pytest.raises(DBAPIError) as raised:
                _ = await service._resolve_and_ingest_tracks(
                    BATCH, CONNECTOR, uow, user_id=USER
                )

        assert postgres_sqlstate(raised.value) == LOCK_NOT_AVAILABLE
        assert calls == [3], "one attempt, then give up — not a loop, not per track"

    async def test_the_giving_up_names_the_sqlstate(
        self,
        db_session: AsyncSession,
        lock_holder: _LockHolder,
        capsys: pytest.CaptureFixture[str],
    ):
        """The log has to say *contention*, or the next reader diagnoses the
        wrong thing — as happened when this arrived as 32 ingest failures."""
        _ = lock_holder
        uow = get_unit_of_work(db_session)
        service = ConnectorPlaylistProcessingService()
        _ = await db_session.execute(text("SET LOCAL lock_timeout = '200ms'"))

        with pytest.raises(DBAPIError):
            _ = await service._resolve_and_ingest_tracks(
                BATCH, CONNECTOR, uow, user_id=USER
            )

        out = capsys.readouterr().out
        assert "transient database contention" in out
        assert LOCK_NOT_AVAILABLE in out, "the real SQLSTATE has to be named"
        assert "retrying them one at a time" not in out

    async def test_the_transaction_survives_a_contended_ingest(
        self, db_session: AsyncSession, lock_holder: _LockHolder
    ):
        """The attempt ran in a savepoint, so the caller's transaction can
        still record the failure it is about to report."""
        _ = lock_holder
        uow = get_unit_of_work(db_session)
        service = ConnectorPlaylistProcessingService()
        _ = await db_session.execute(text("SET LOCAL lock_timeout = '200ms'"))

        with pytest.raises(DBAPIError):
            _ = await service._resolve_and_ingest_tracks(
                BATCH, CONNECTOR, uow, user_id=USER
            )

        async with uow.savepoint():
            assert (await db_session.execute(text("SELECT 1"))).scalar_one() == 1


class TestTheTwoWritersSerialize:
    """The real race, run for real: ``save_tracks`` against the bulk ingest.

    Both writers claim ``uq_tracks_user_isrc`` for the same user. Left to
    race, the second blocks inside the index on the first's uncommitted row
    until ``lock_timeout``. With the lock, it blocks at the lock instead and
    proceeds the moment the first transaction ends — the difference being that
    the wait is bounded by the writer, not by the timeout.

    Needs an engine of its own: ``db_session`` is one savepoint-wrapped
    session, and two genuinely separate connections is the whole point (the
    ``test_bulk_upsert_contention.py`` pattern).
    """

    _ISRC = "GBBPW1300777"

    @async_fixture
    async def concurrent_sessions(
        self, postgres_url: str, _init_test_schema: None
    ) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
        """A factory whose sessions really do run in separate transactions."""
        engine = create_async_engine(postgres_url)
        try:
            yield async_sessionmaker(engine, expire_on_commit=False)
        finally:
            # Only writer A commits; sweep its rows either way.
            async with engine.begin() as conn:
                _ = await conn.execute(
                    text("DELETE FROM tracks WHERE user_id = :uid"),
                    {"uid": _SERIALIZATION_USER},
                )
            await engine.dispose()

    async def test_the_bulk_ingest_waits_for_an_open_save_tracks(
        self, concurrent_sessions: async_sessionmaker[AsyncSession]
    ):
        """B cannot get past the lock while A's transaction is open, and lands
        as soon as it commits."""
        order: list[str] = []

        async def writer_a() -> None:
            async with concurrent_sessions() as session:
                _ = await TrackRepository(session).save_tracks([
                    Track(
                        title="You Took Your Time",
                        artists=[Artist(name="Mount Kimbie")],
                        isrc=self._ISRC,
                        user_id=_SERIALIZATION_USER,
                    )
                ])
                order.append("a-wrote")
                # Hold the transaction — and the lock — open.
                await asyncio.sleep(0.3)
                await session.commit()
                order.append("a-committed")

        async def writer_b() -> list[Track]:
            await asyncio.sleep(0.05)  # let A take the lock first
            async with concurrent_sessions() as session:
                # Bounded, so a regression that never releases fails instead
                # of hanging the suite.
                _ = await session.execute(text("SET lock_timeout = '10s'"))
                ingested = await TrackConnectorRepository(
                    session
                ).ingest_external_tracks_bulk(
                    CONNECTOR,
                    [
                        ConnectorTrack(
                            connector_name=CONNECTOR,
                            connector_track_identifier="sp_serial1",
                            title="You Took Your Time",
                            artists=[Artist(name="Mount Kimbie")],
                            album="Cold Spring Fault Less Youth",
                            duration_ms=216399,
                            isrc=self._ISRC,
                            raw_metadata={},
                            last_updated=datetime.now(UTC),
                        )
                    ],
                    user_id=_SERIALIZATION_USER,
                )
                order.append("b-ingested")
                await session.rollback()
                return ingested

        async with asyncio.TaskGroup() as tg:
            _ = tg.create_task(writer_a())
            ingest = tg.create_task(writer_b())

        assert order == ["a-wrote", "a-committed", "b-ingested"], (
            "the ingest must not enter while save_tracks holds the lock"
        )
        assert len(ingest.result()) == 1, "and it must still succeed afterwards"
