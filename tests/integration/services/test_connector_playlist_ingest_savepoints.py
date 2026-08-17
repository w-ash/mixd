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
same contended index and resolves nothing.
"""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, AsyncTransaction

from src.application.services import connector_playlist_processing_service
from src.application.services.connector_playlist_processing_service import (
    ConnectorPlaylistProcessingService,
)
from src.domain.entities import Artist, ConnectorTrack
from src.domain.repositories.errors import LOCK_NOT_AVAILABLE, postgres_sqlstate
from src.infrastructure.persistence.database.db_models import DBTrack
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from src.infrastructure.persistence.repositories.track.connector import (
    TrackConnectorRepository,
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
    two fallbacks apart: ``[3, 1, 1, 1]`` is the per-track loop, ``[3, 3]`` the
    whole-batch retry.
    """
    real = TrackConnectorRepository.ingest_external_tracks_bulk

    async def fake(self, connector, tracks, *, user_id):
        if calls is not None:
            calls.append(len(tracks))
        if len(tracks) > 1:
            _ = await session.execute(text("SELECT 1 / 0"))
        return await real(self, connector, tracks, user_id=user_id)

    return patch.object(TrackConnectorRepository, "ingest_external_tracks_bulk", fake)


# Arbitrary but stable: this file is the only user of this advisory-lock key.
_CONTENTION_LOCK_KEY = 918_273_645


class _LockHolder:
    """The competing writer — a second session sitting on a lock we want."""

    def __init__(self, trans: AsyncTransaction) -> None:
        self._trans = trans

    async def release(self) -> None:
        """Commit-equivalent: end the transaction, dropping the lock."""
        if self._trans.is_active:
            await self._trans.rollback()


async def _wait_on_the_held_lock(session: AsyncSession) -> None:
    """Queue on the competing writer's lock until ``lock_timeout`` fires.

    Production's statement is an ``INSERT INTO tracks`` blocked on
    ``uq_tracks_user_isrc`` by a concurrent uncommitted writer; what reaches
    the service is the same SQLAlchemy wrapper around
    ``psycopg.errors.LockNotAvailable`` (SQLSTATE 55P03) either way, and an
    advisory lock produces it deterministically instead of racing two real
    ingests inside one test. The transaction is left aborted, as it is in
    production.
    """
    _ = await session.execute(text("SET LOCAL lock_timeout = '200ms'"))
    _ = await session.execute(
        text(f"SELECT pg_advisory_xact_lock({_CONTENTION_LOCK_KEY})")
    )


def _bulk_hits_lock_contention(
    session: AsyncSession,
    calls: list[int],
    *,
    releasing: _LockHolder | None = None,
):
    """Every *batch* ingest blocks on the competing writer's lock and times out.

    With ``releasing`` set, that writer finishes as the first attempt fails —
    the ordinary case the retry exists for. Without it the contention outlives
    both attempts, which is the case that has to fail loudly.
    """
    real = TrackConnectorRepository.ingest_external_tracks_bulk

    async def fake(self, connector, tracks, *, user_id):
        calls.append(len(tracks))
        if len(tracks) > 1:
            try:
                await _wait_on_the_held_lock(session)
            finally:
                if releasing is not None:
                    await releasing.release()
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


class TestTransientContentionIsRetriedWholesale:
    """55P03 is not a bad row — it is someone else holding the identity key.

    ``RunWorkflowUseCase`` starts a play import and deliberately doesn't await
    it, so an inward resolver's ``save_tracks`` routinely holds uncommitted
    ``tracks`` rows while this ingest runs. Answering that with the per-track
    loop makes it worse in the exact way that hurts: each retry queues on the
    same contended index in turn, so the batch spends N times ``lock_timeout``
    (measured: 33.7s at a 1s timeout, ~5.5 minutes at production's 10s) and
    resolves *nothing* — every position silently recorded UNRESOLVED, no error
    anywhere.
    """

    @pytest.fixture
    async def competing_writer(self, _test_engine: AsyncEngine):
        """A second connection holding a lock ours will queue on.

        A fixture rather than an inline context manager because ``db_session``
        is savepoint-isolated: the contention has to come from a *separate*
        connection to be real, and this is the same shape the repositories'
        claim-race tests use.
        """
        async with _test_engine.connect() as conn:
            trans = await conn.begin()
            _ = await conn.exec_driver_sql(
                f"SELECT pg_advisory_xact_lock({_CONTENTION_LOCK_KEY})"
            )
            holder = _LockHolder(trans)
            try:
                yield holder
            finally:
                await holder.release()

    @pytest.fixture(autouse=True)
    def _shorten_the_backoff(self):
        """Elide the real pause. Its length is a production judgement about how
        long the competing writer takes to commit, not behaviour these tests
        pin — and a second of sleep per test would push each of them over the
        project's ``slow`` threshold and out of the default run."""
        with patch.object(
            connector_playlist_processing_service,
            "CONTENTION_RETRY_DELAY_SECONDS",
            0.05,
        ):
            yield

    async def test_contention_retries_the_batch_and_never_splits_it(
        self, db_session: AsyncSession, competing_writer: _LockHolder
    ):
        """The competing writer commits while we back off; the retry lands."""
        uow = get_unit_of_work(db_session)
        service = ConnectorPlaylistProcessingService()
        calls: list[int] = []

        with _bulk_hits_lock_contention(db_session, calls, releasing=competing_writer):
            resolved = await service._resolve_and_ingest_tracks(
                BATCH, CONNECTOR, uow, user_id=USER
            )

        assert sorted(resolved) == ["sp_aaaaa", "sp_bbbbb", "sp_ccccc"]
        assert calls == [3, 3], (
            "contention must be retried as one batch — a per-track loop would "
            "read [3, 1, 1, 1] and queue on the same lock three more times"
        )

    async def test_the_retried_tracks_are_really_persisted(
        self, db_session: AsyncSession, competing_writer: _LockHolder
    ):
        """The failed attempt's savepoint rolled back; the retry's writes stand."""
        uow = get_unit_of_work(db_session)
        service = ConnectorPlaylistProcessingService()
        before = (
            await db_session.execute(select(func.count()).select_from(DBTrack))
        ).scalar_one()

        with _bulk_hits_lock_contention(db_session, [], releasing=competing_writer):
            _ = await service._resolve_and_ingest_tracks(
                BATCH, CONNECTOR, uow, user_id=USER
            )

        after = (
            await db_session.execute(select(func.count()).select_from(DBTrack))
        ).scalar_one()
        assert after == before + len(BATCH)

    async def test_contention_that_outlives_the_retry_fails_the_run(
        self, db_session: AsyncSession, competing_writer: _LockHolder
    ):
        """Two attempts, then raise. Returning an empty map instead would reach
        the user as a green run whose every entry reads "Couldn't match"."""
        uow = get_unit_of_work(db_session)
        service = ConnectorPlaylistProcessingService()
        calls: list[int] = []

        with _bulk_hits_lock_contention(db_session, calls):
            with pytest.raises(DBAPIError) as raised:
                _ = await service._resolve_and_ingest_tracks(
                    BATCH, CONNECTOR, uow, user_id=USER
                )

        assert postgres_sqlstate(raised.value) == LOCK_NOT_AVAILABLE
        assert calls == [3, 3], "one retry, then give up — not a loop, not per track"

    async def test_a_non_contention_retry_failure_takes_the_per_track_path(
        self, db_session: AsyncSession, competing_writer: _LockHolder
    ):
        """First failure is contention; the retry hits an ordinary error (the
        competitor committed our key → e.g. 23505). That is the per-track
        loop's case — not a wholesale run failure mislabeled as contention."""
        uow = get_unit_of_work(db_session)
        service = ConnectorPlaylistProcessingService()
        calls: list[int] = []
        real = TrackConnectorRepository.ingest_external_tracks_bulk

        async def fake(self, connector, tracks, *, user_id):
            calls.append(len(tracks))
            if len(tracks) > 1:
                if len(calls) == 1:
                    try:
                        await _wait_on_the_held_lock(db_session)
                    finally:
                        await competing_writer.release()
                else:
                    _ = await db_session.execute(text("SELECT 1 / 0"))
            return await real(self, connector, tracks, user_id=user_id)

        with patch.object(
            TrackConnectorRepository, "ingest_external_tracks_bulk", fake
        ):
            resolved = await service._resolve_and_ingest_tracks(
                BATCH, CONNECTOR, uow, user_id=USER
            )

        assert sorted(resolved) == ["sp_aaaaa", "sp_bbbbb", "sp_ccccc"]
        assert calls == [3, 3, 1, 1, 1], (
            "contention retry, then a non-contention failure isolated per track"
        )

    async def test_the_giving_up_names_the_sqlstate(
        self,
        db_session: AsyncSession,
        competing_writer: _LockHolder,
        capsys: pytest.CaptureFixture[str],
    ):
        """The log has to say *contention*, or the next reader diagnoses the
        wrong thing — as happened when this arrived as 32 ingest failures."""
        uow = get_unit_of_work(db_session)
        service = ConnectorPlaylistProcessingService()

        with _bulk_hits_lock_contention(db_session, []):
            with pytest.raises(DBAPIError):
                _ = await service._resolve_and_ingest_tracks(
                    BATCH, CONNECTOR, uow, user_id=USER
                )

        out = capsys.readouterr().out
        assert "transient database contention" in out
        assert LOCK_NOT_AVAILABLE in out, "the real SQLSTATE has to be named"
        assert "retrying them one at a time" not in out

    async def test_the_transaction_survives_a_contended_ingest(
        self, db_session: AsyncSession, competing_writer: _LockHolder
    ):
        """Both attempts ran in savepoints, so the caller's transaction can
        still record the failure it is about to report."""
        uow = get_unit_of_work(db_session)
        service = ConnectorPlaylistProcessingService()

        with _bulk_hits_lock_contention(db_session, []):
            with pytest.raises(DBAPIError):
                _ = await service._resolve_and_ingest_tracks(
                    BATCH, CONNECTOR, uow, user_id=USER
                )

        async with uow.savepoint():
            assert (await db_session.execute(text("SELECT 1"))).scalar_one() == 1
