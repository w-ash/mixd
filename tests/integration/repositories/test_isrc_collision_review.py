"""Integration tests for queue_isrc_collision_review (v0.8.18 epic 3).

Suspect ISRC collisions route to the review queue instead of merging; the
any-status dedupe keeps playlist re-syncs from resurrecting rejected reviews.
"""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.constants import MatchMethod
from src.domain.entities import Artist, Track
from src.infrastructure.persistence.database.db_models import DBMatchReview
from src.infrastructure.persistence.repositories.factories import get_unit_of_work


async def _seed_isrc_owner(uow) -> Track:
    return await uow.get_track_repository().save_track(
        Track(
            id=None,
            title="Gold Rush",
            artists=[Artist(name="Neon Priest")],
            album="Debut",
            duration_ms=200_000,
            isrc="USNP12400001",
        )
    )


_REMASTER_DATA = {
    "title": "Gold Rush (2024 Remaster)",
    "artist": "Neon Priest",
    "artists": ["Neon Priest"],
    "duration_ms": 215_000,  # 15s off — suspect
    "isrc": "USNP12400001",
}


class TestQueueIsrcCollisionReview:
    async def test_queues_pending_isrc_suspect_review(self, db_session: AsyncSession):
        uow = get_unit_of_work(db_session)
        owner = await _seed_isrc_owner(uow)

        queued = await uow.get_connector_repository().queue_isrc_collision_review(
            owner, "spotify", "sp_remaster_001", _REMASTER_DATA, user_id="default"
        )

        assert queued is True
        row = (
            await db_session.execute(
                select(DBMatchReview).where(DBMatchReview.track_id == owner.id)
            )
        ).scalar_one()
        assert row.match_method == MatchMethod.ISRC_SUSPECT
        assert row.status == "pending"
        assert row.confidence_evidence is not None
        # The engine's own suspect check fired with real durations.
        assert row.confidence_evidence["isrc_suspect"] is True

    async def test_any_status_dedupe_blocks_requeue(self, db_session: AsyncSession):
        """A rejected review must not be resurrected by the next re-sync."""
        uow = get_unit_of_work(db_session)
        connector_repo = uow.get_connector_repository()
        owner = await _seed_isrc_owner(uow)

        first = await connector_repo.queue_isrc_collision_review(
            owner, "spotify", "sp_remaster_001", _REMASTER_DATA, user_id="default"
        )
        assert first is True

        # Same pair again while pending: skipped.
        second = await connector_repo.queue_isrc_collision_review(
            owner, "spotify", "sp_remaster_001", _REMASTER_DATA, user_id="default"
        )
        assert second is False

        # Reject the review; the pair must STILL be skipped.
        await db_session.execute(
            update(DBMatchReview)
            .where(DBMatchReview.track_id == owner.id)
            .values(status="rejected")
        )
        await db_session.flush()
        third = await connector_repo.queue_isrc_collision_review(
            owner, "spotify", "sp_remaster_001", _REMASTER_DATA, user_id="default"
        )
        assert third is False

        reviews = (
            (
                await db_session.execute(
                    select(DBMatchReview.status).where(
                        DBMatchReview.track_id == owner.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert reviews == ["rejected"]
