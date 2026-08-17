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
"""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.connector_playlist_processing_service import (
    ConnectorPlaylistProcessingService,
)
from src.domain.entities import Artist, ConnectorTrack
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


def _bulk_fails_batch_succeeds_singly(session: AsyncSession):
    """Stand-in for the real first error: a genuine DB abort on the bulk call.

    The batch call executes a statement PostgreSQL rejects — leaving the
    transaction aborted exactly as a unique violation, lock timeout or
    statement timeout would — and then raises. Single-track calls run the real
    implementation, which is what the retry loop exists to reach.

    Deliberately not a bare ``RuntimeError``: a Python-side exception leaves
    the transaction perfectly usable, so it would not reproduce the bug at all.
    """
    real = TrackConnectorRepository.ingest_external_tracks_bulk

    async def fake(self, connector, tracks, *, user_id):
        if len(tracks) > 1:
            _ = await session.execute(text("SELECT 1 / 0"))
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
