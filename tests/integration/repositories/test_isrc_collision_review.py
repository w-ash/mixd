"""Integration tests for the suspect-ISRC review flow (v0.8.18 epic 3).

Suspect ISRC collisions route to the review queue instead of merging; the
any-status dedupe keeps playlist re-syncs from resurrecting rejected reviews;
review-accept folds the deferred canonical back into the owner.
"""

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.use_cases.resolve_match_review import (
    ResolveMatchReviewCommand,
    ResolveMatchReviewUseCase,
)
from src.config.constants import MatchMethod
from src.domain.entities import Artist, ConnectorTrack, Track
from src.infrastructure.persistence.database.db_models import DBMatchReview, DBTrack
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

    async def test_queues_review_for_non_default_user(self, db_session: AsyncSession):
        """create_review must persist the review under the REAL owner, not the
        server_default 'default' user_id (v0.8.18 review, finding #4).

        create_review previously omitted user_id from its upsert, so a
        non-'default' tenant's review took the server_default 'default' — making
        it invisible to that user's pending list / drift panel (and, under prod
        RLS WITH CHECK, rejected outright). All other tests use 'default', which
        masked it.
        """
        uow = get_unit_of_work(db_session)
        owner = await uow.get_track_repository().save_track(
            Track(
                id=None,
                title="Gold Rush",
                artists=[Artist(name="Neon Priest")],
                album="Debut",
                duration_ms=200_000,
                isrc="USNP12400001",
                user_id="alice",
            )
        )

        queued = await uow.get_connector_repository().queue_isrc_collision_review(
            owner, "spotify", "sp_remaster_alice", _REMASTER_DATA, user_id="alice"
        )
        assert queued is True

        row = (
            await db_session.execute(
                select(DBMatchReview).where(DBMatchReview.track_id == owner.id)
            )
        ).scalar_one()
        # Owned by alice, not the server_default 'default'.
        assert row.user_id == "alice"

        # ...so it surfaces in alice's pending list (dedupe + drift filter by
        # user_id; a 'default'-owned row would be invisible to her).
        reviews, total = await uow.get_match_review_repository().list_pending_reviews(
            user_id="alice"
        )
        assert total == 1
        assert reviews[0].track_id == owner.id

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


def _remaster_connector_track(identifier: str = "sp_remaster_001") -> ConnectorTrack:
    return ConnectorTrack(
        connector_name="spotify",
        connector_track_identifier=identifier,
        title="Gold Rush (2024 Remaster)",
        artists=[Artist(name="Neon Priest")],
        album="Remaster Compilation",
        duration_ms=215_000,  # 15s off the owner — suspect
        isrc="USNP12400001",
        raw_metadata={},
        last_updated=datetime.now(UTC),
    )


class TestIngestSuspectIsrcRouting:
    """Playlist import hitting a suspect ISRC: review + distinct canonical."""

    async def test_import_defers_and_queues_review(self, db_session: AsyncSession):
        uow = get_unit_of_work(db_session)
        owner = await _seed_isrc_owner(uow)

        imported = await uow.get_connector_repository().ingest_external_tracks_bulk(
            "spotify", [_remaster_connector_track()], user_id="default"
        )

        # A distinct canonical without the contested ISRC — owner untouched.
        assert imported[0].id != owner.id
        assert imported[0].isrc is None
        owner_row = (
            await db_session.execute(
                select(DBTrack.title, DBTrack.duration_ms).where(DBTrack.id == owner.id)
            )
        ).one()
        assert owner_row.title == "Gold Rush"
        assert owner_row.duration_ms == 200_000

        # And an isrc_suspect review against the owner.
        review = (
            await db_session.execute(
                select(DBMatchReview).where(DBMatchReview.track_id == owner.id)
            )
        ).scalar_one()
        assert review.match_method == MatchMethod.ISRC_SUSPECT
        assert review.status == "pending"

    async def test_reimport_does_not_requeue_after_reject(
        self, db_session: AsyncSession
    ):
        uow = get_unit_of_work(db_session)
        connector_repo = uow.get_connector_repository()
        owner = await _seed_isrc_owner(uow)

        _ = await connector_repo.ingest_external_tracks_bulk(
            "spotify", [_remaster_connector_track()], user_id="default"
        )
        await db_session.execute(
            update(DBMatchReview)
            .where(DBMatchReview.track_id == owner.id)
            .values(status="rejected")
        )
        await db_session.flush()

        # Weekly re-sync: the mapping fast path resolves the track; no new review.
        _ = await connector_repo.ingest_external_tracks_bulk(
            "spotify", [_remaster_connector_track()], user_id="default"
        )
        statuses = (
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
        assert statuses == ["rejected"]


class TestReviewAcceptMergesDeferredCanonical:
    async def test_accept_folds_deferred_canonical_into_owner(
        self, db_session: AsyncSession
    ):
        uow = get_unit_of_work(db_session)
        owner = await _seed_isrc_owner(uow)

        imported = await uow.get_connector_repository().ingest_external_tracks_bulk(
            "spotify", [_remaster_connector_track()], user_id="default"
        )
        deferred = imported[0]
        review_id = (
            await db_session.execute(
                select(DBMatchReview.id).where(DBMatchReview.track_id == owner.id)
            )
        ).scalar_one()

        result = await ResolveMatchReviewUseCase().execute(
            ResolveMatchReviewCommand(
                user_id="default", review_id=review_id, action="accept"
            ),
            uow,
        )

        assert result.mapping_created is True
        # The deferred canonical was merged away...
        remaining = (
            await db_session.execute(
                select(DBTrack.id).where(DBTrack.id == deferred.id)
            )
        ).first()
        assert remaining is None
        # ...and the spotify mapping now lives on the owner.
        details = await uow.get_connector_repository().get_primary_mapping_details(
            [owner.id], "spotify"
        )
        assert details[owner.id].connector_id == "sp_remaster_001"
