"""Integration tests for SyncCheckpointRepository.

Covers get_or_create_sync_checkpoint's contract — returns the persisted row
when one exists, and a fresh *unsaved* checkpoint on miss (row-absence means
"never synced", so creation must not persist) — using the db_session fixture
with testcontainers PostgreSQL.

Also covers the v0.10.1 poll lease. That half needs real SQL twice over: the
claim's atomicity is a property of a single ON CONFLICT statement (a two-phase
find-then-write would pass a single-threaded test and lose the race in
production), and the column-ownership guarantee — that an importer's checkpoint
save cannot clobber polling state — is a property of which keys the shared
upsert helper writes, not of anything visible in the entity.
"""

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities import SyncCheckpoint
from src.infrastructure.persistence.repositories.sync import SyncCheckpointRepository

_NOW = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
_MAX_AGE = timedelta(minutes=30)
_CLAIM_TTL = timedelta(minutes=15)


class TestGetOrCreateSyncCheckpoint:
    """Non-persisting get-or-create semantics."""

    async def test_returns_persisted_checkpoint_when_exists(
        self, db_session: AsyncSession
    ) -> None:
        repo = SyncCheckpointRepository(db_session)
        saved = await repo.save_sync_checkpoint(
            SyncCheckpoint(
                user_id="default",
                service="spotify",
                entity_type="likes",
                last_timestamp=datetime.now(UTC),
                cursor="100",
            )
        )

        result = await repo.get_or_create_sync_checkpoint(
            user_id="default", service="spotify", entity_type="likes"
        )

        assert result.id == saved.id
        assert result.cursor == "100"

    async def test_miss_returns_fresh_checkpoint_with_requested_keys(
        self, db_session: AsyncSession
    ) -> None:
        repo = SyncCheckpointRepository(db_session)

        result = await repo.get_or_create_sync_checkpoint(
            user_id="default", service="lastfm", entity_type="plays"
        )

        assert result.user_id == "default"
        assert result.service == "lastfm"
        assert result.entity_type == "plays"
        assert result.last_timestamp is None
        assert result.cursor is None

    async def test_miss_does_not_persist(self, db_session: AsyncSession) -> None:
        repo = SyncCheckpointRepository(db_session)

        _ = await repo.get_or_create_sync_checkpoint(
            user_id="default", service="lastfm", entity_type="likes"
        )

        # Row-absence is the "never synced" signal — the miss must not write.
        assert (
            await repo.get_sync_checkpoint(
                user_id="default", service="lastfm", entity_type="likes"
            )
            is None
        )


class TestPollLease:
    """The single-flight claim, on one connection (predicates, not concurrency)."""

    async def test_first_claim_inserts_a_row_that_still_reads_as_never_synced(
        self, db_session: AsyncSession
    ) -> None:
        repo = SyncCheckpointRepository(db_session)

        won = await repo.try_claim_poll(
            "u1", "spotify", "plays", now=_NOW, max_age=_MAX_AGE, claim_ttl=_CLAIM_TTL
        )

        assert won is True
        # Claiming necessarily inserts a row before any sync has happened. That
        # must not read as "already synced", or the importer would resume from a
        # cursor that was never established.
        row = await repo.get_sync_checkpoint(
            user_id="u1", service="spotify", entity_type="plays"
        )
        assert row is not None
        assert row.last_timestamp is None

    async def test_second_claim_while_lease_is_live_loses(
        self, db_session: AsyncSession
    ) -> None:
        repo = SyncCheckpointRepository(db_session)
        await repo.try_claim_poll(
            "u1", "spotify", "plays", now=_NOW, max_age=_MAX_AGE, claim_ttl=_CLAIM_TTL
        )

        second = await repo.try_claim_poll(
            "u1",
            "spotify",
            "plays",
            now=_NOW + timedelta(minutes=1),
            max_age=_MAX_AGE,
            claim_ttl=_CLAIM_TTL,
        )

        assert second is False

    async def test_expired_lease_is_reclaimable(self, db_session: AsyncSession) -> None:
        repo = SyncCheckpointRepository(db_session)
        await repo.try_claim_poll(
            "u1", "spotify", "plays", now=_NOW, max_age=_MAX_AGE, claim_ttl=_CLAIM_TTL
        )

        # The holder's process died without calling finish_poll. Past the TTL the
        # lease must be reclaimable, or one crash would wedge polling forever.
        reclaimed = await repo.try_claim_poll(
            "u1",
            "spotify",
            "plays",
            now=_NOW + _CLAIM_TTL + timedelta(minutes=1),
            max_age=_MAX_AGE,
            claim_ttl=_CLAIM_TTL,
        )

        assert reclaimed is True

    async def test_recently_polled_checkpoint_is_not_claimable(
        self, db_session: AsyncSession
    ) -> None:
        repo = SyncCheckpointRepository(db_session)
        await repo.try_claim_poll(
            "u1", "spotify", "plays", now=_NOW, max_age=_MAX_AGE, claim_ttl=_CLAIM_TTL
        )
        await repo.finish_poll(
            "u1",
            "spotify",
            "plays",
            polled_at=_NOW,
            poll_state={"consecutive_empty": 0},
        )

        # Lease is free, but the checkpoint was polled 5 minutes ago and max_age
        # is 30 — the staleness half of the predicate must still reject it.
        too_soon = await repo.try_claim_poll(
            "u1",
            "spotify",
            "plays",
            now=_NOW + timedelta(minutes=5),
            max_age=_MAX_AGE,
            claim_ttl=_CLAIM_TTL,
        )

        assert too_soon is False

    async def test_claimable_again_once_stale(self, db_session: AsyncSession) -> None:
        repo = SyncCheckpointRepository(db_session)
        await repo.try_claim_poll(
            "u1", "spotify", "plays", now=_NOW, max_age=_MAX_AGE, claim_ttl=_CLAIM_TTL
        )
        await repo.finish_poll("u1", "spotify", "plays", polled_at=_NOW, poll_state={})

        stale = await repo.try_claim_poll(
            "u1",
            "spotify",
            "plays",
            now=_NOW + _MAX_AGE + timedelta(minutes=1),
            max_age=_MAX_AGE,
            claim_ttl=_CLAIM_TTL,
        )

        assert stale is True

    async def test_finish_records_state_and_releases_the_lease(
        self, db_session: AsyncSession
    ) -> None:
        repo = SyncCheckpointRepository(db_session)
        await repo.try_claim_poll(
            "u1", "spotify", "plays", now=_NOW, max_age=_MAX_AGE, claim_ttl=_CLAIM_TTL
        )

        await repo.finish_poll(
            "u1",
            "spotify",
            "plays",
            polled_at=_NOW,
            poll_state={"consecutive_empty": 3},
        )

        row = await repo.get_sync_checkpoint(
            user_id="u1", service="spotify", entity_type="plays"
        )
        assert row is not None
        assert row.last_polled_at == _NOW
        assert row.poll_state == {"consecutive_empty": 3}

    async def test_failed_poll_records_state_without_advancing_the_gate(
        self, db_session: AsyncSession
    ) -> None:
        repo = SyncCheckpointRepository(db_session)
        await repo.try_claim_poll(
            "u1", "spotify", "plays", now=_NOW, max_age=_MAX_AGE, claim_ttl=_CLAIM_TTL
        )

        # polled_at=None is how the runner reports a failure. Advancing the
        # freshness gate here would make a persistently broken connector look
        # recently checked — silencing the surface meant to reveal it.
        await repo.finish_poll(
            "u1",
            "spotify",
            "plays",
            polled_at=None,
            poll_state={"consecutive_empty": 1},
        )

        row = await repo.get_sync_checkpoint(
            user_id="u1", service="spotify", entity_type="plays"
        )
        assert row is not None
        assert row.last_polled_at is None
        assert row.poll_state == {"consecutive_empty": 1}
        # The lease is still released, so the next trigger can retry immediately.
        assert (
            await repo.try_claim_poll(
                "u1",
                "spotify",
                "plays",
                now=_NOW + timedelta(seconds=1),
                max_age=_MAX_AGE,
                claim_ttl=_CLAIM_TTL,
            )
            is True
        )

    async def test_importer_save_preserves_poll_columns(
        self, db_session: AsyncSession
    ) -> None:
        """The column-ownership guarantee, stated as a test.

        ``save_sync_checkpoint``'s ``create_attrs`` names only the three cursor
        fields, and the shared upsert's UPDATE arm writes exactly those keys. If
        a poll column were ever added there, every import would silently reset
        the backoff — so this pins the boundary rather than trusting the comment.
        """
        repo = SyncCheckpointRepository(db_session)
        await repo.try_claim_poll(
            "u1", "spotify", "plays", now=_NOW, max_age=_MAX_AGE, claim_ttl=_CLAIM_TTL
        )
        await repo.finish_poll(
            "u1",
            "spotify",
            "plays",
            polled_at=_NOW,
            poll_state={"consecutive_empty": 4},
        )

        existing = await repo.get_sync_checkpoint(
            user_id="u1", service="spotify", entity_type="plays"
        )
        assert existing is not None
        await repo.save_sync_checkpoint(
            existing.with_update(timestamp=_NOW, cursor="9999")
        )

        after = await repo.get_sync_checkpoint(
            user_id="u1", service="spotify", entity_type="plays"
        )
        assert after is not None
        assert after.cursor == "9999"
        assert after.last_polled_at == _NOW
        assert after.poll_state == {"consecutive_empty": 4}


class TestConcurrentPollClaim:
    """Atomicity under genuine concurrency — the reason this is one statement."""

    @pytest.fixture
    async def two_db_sessions(
        self, _test_engine
    ) -> AsyncGenerator[tuple[AsyncSession, AsyncSession]]:
        """Two committed sessions on SEPARATE connections + TEST_%% cleanup.

        The savepoint-isolated ``db_session`` keeps writes in one uncommitted
        transaction, invisible to a second connection — so the race can't happen
        there and the test would pass vacuously. Mirrors the schedule
        repository's claim-race fixture.
        """
        conn1 = await _test_engine.connect()
        conn2 = await _test_engine.connect()
        s1 = AsyncSession(bind=conn1, expire_on_commit=False, autoflush=False)
        s2 = AsyncSession(bind=conn2, expire_on_commit=False, autoflush=False)
        try:
            yield s1, s2
        finally:
            await s1.rollback()
            await s2.rollback()
            await s1.close()
            await s2.close()
            async with _test_engine.begin() as cleanup:
                await cleanup.execute(
                    text("DELETE FROM sync_checkpoints WHERE user_id LIKE 'TEST_%'")
                )
            await conn1.close()
            await conn2.close()

    async def test_exactly_one_concurrent_claimer_wins_on_first_claim(
        self, two_db_sessions: tuple[AsyncSession, AsyncSession]
    ) -> None:
        s1, s2 = two_db_sessions
        user = f"TEST_{uuid4().hex[:8]}"
        repo1, repo2 = SyncCheckpointRepository(s1), SyncCheckpointRepository(s2)

        async def claim(repo: SyncCheckpointRepository, session: AsyncSession) -> bool:
            won = await repo.try_claim_poll(
                user,
                "spotify",
                "plays",
                now=_NOW,
                max_age=_MAX_AGE,
                claim_ttl=_CLAIM_TTL,
            )
            await session.commit()
            return won

        results = await asyncio.gather(claim(repo1, s1), claim(repo2, s2))

        # Strict: one True and one False, no exceptions. The loser must be turned
        # away by the conflict predicate, not by a unique violation that happens
        # to look like single-flight while actually being an error path.
        assert sorted(results) == [False, True], f"got {results}"

    async def test_exactly_one_concurrent_claimer_wins_on_existing_row(
        self, two_db_sessions: tuple[AsyncSession, AsyncSession]
    ) -> None:
        """The production-common race: the checkpoint row already exists.

        Distinct from the first-claim race, which the unique index alone would
        serialise. Here both callers take the DO UPDATE arm, so only the
        conflict predicate stands between them and a double poll — this is the
        case a two-phase find-then-write would lose.
        """
        s1, s2 = two_db_sessions
        user = f"TEST_{uuid4().hex[:8]}"
        repo1, repo2 = SyncCheckpointRepository(s1), SyncCheckpointRepository(s2)

        # Establish a committed row with a released lease and a stale gate.
        await repo1.try_claim_poll(
            user, "spotify", "plays", now=_NOW, max_age=_MAX_AGE, claim_ttl=_CLAIM_TTL
        )
        await repo1.finish_poll(user, "spotify", "plays", polled_at=None, poll_state={})
        await s1.commit()

        async def claim(repo: SyncCheckpointRepository, session: AsyncSession) -> bool:
            won = await repo.try_claim_poll(
                user,
                "spotify",
                "plays",
                now=_NOW,
                max_age=_MAX_AGE,
                claim_ttl=_CLAIM_TTL,
            )
            await session.commit()
            return won

        results = await asyncio.gather(claim(repo1, s1), claim(repo2, s2))

        assert sorted(results) == [False, True], f"got {results}"
