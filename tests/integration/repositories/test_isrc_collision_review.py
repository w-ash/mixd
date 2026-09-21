"""Integration tests for the suspect-ISRC review flow (v0.8.18 epic 3).

Suspect ISRC collisions route to the review queue instead of merging — from
the play-import resolvers (the planner's strong-id arm, the review written
through ``create_reviews_batch``) and from playlist ingest alike; a rejected
review is never resurrected by a re-import; review-accept folds the deferred
canonical back into the owner.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.track_resolution import TrackResolutionService
from src.application.use_cases.resolve_match_review import (
    ResolveMatchReviewCommand,
    ResolveMatchReviewUseCase,
)
from src.domain.entities import (
    ArtistCredit,
    ConnectorArtistCredit,
    ConnectorTrack,
    Track,
)
from src.infrastructure.connectors.spotify.client import SpotifyTracksFetch
from src.infrastructure.connectors.spotify.inward_resolver import SpotifyInwardResolver
from src.infrastructure.connectors.spotify.models import SpotifyExternalIds
from src.infrastructure.persistence.database.models import DBMatchReview, DBTrack
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from tests.fixtures import TEST_USER_ID, make_spotify_track


async def _seed_isrc_owner(uow, *, user_id: str = TEST_USER_ID) -> Track:
    return await uow.get_track_repository().save_track(
        Track(
            id=None,
            title="Gold Rush",
            artists=[ArtistCredit(credited_name="Neon Priest")],
            album="Debut",
            duration_ms=200_000,
            isrc="USNP12400001",
            user_id=user_id,
        )
    )


def _remaster_resolver(
    spotify_id: str = "sp_remaster_001", *, isrc: str = "USNP12400001"
) -> SpotifyInwardResolver:
    """A resolver whose provider answers with a 15s-longer remaster."""
    connector = AsyncMock()
    connector.connector_name = "spotify"
    connector.get_tracks_by_ids.return_value = SpotifyTracksFetch(
        tracks={
            spotify_id: make_spotify_track(
                spotify_id,
                "Gold Rush (2024 Remaster)",
                "Neon Priest",
                duration_ms=215_000,  # 15s off — suspect
                external_ids=SpotifyExternalIds(isrc=isrc),
            )
        }
    )
    return SpotifyInwardResolver(spotify_connector=connector)


class TestInwardResolverSuspectIsrcRouting:
    """Play import hitting a suspect ISRC: review + distinct canonical."""

    async def test_queues_pending_isrc_suspect_review(self, db_session: AsyncSession):
        uow = get_unit_of_work(db_session)
        owner = await _seed_isrc_owner(uow)

        resolver = _remaster_resolver()
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["sp_remaster_001"], uow, user_id="default"
        )

        assert metrics.created == 1
        assert resolver.isrc_suspect_deferred_ids == {"sp_remaster_001"}
        # A distinct canonical without the contested ISRC — owner untouched.
        assert result["sp_remaster_001"].id != owner.id
        assert result["sp_remaster_001"].isrc is None
        row = (
            await db_session.execute(
                select(DBMatchReview).where(DBMatchReview.track_id == owner.id)
            )
        ).scalar_one()
        assert row.match_method == "isrc_suspect"
        assert row.status == "pending"
        assert row.confidence_evidence is not None
        # The engine's own suspect check fired with real durations.
        assert row.confidence_evidence["isrc_suspect"] is True

    async def test_queues_review_for_non_default_user(self, db_session: AsyncSession):
        """The review is persisted under the REAL owner, not the server_default
        'default' user_id — otherwise it is invisible to that user's pending
        list (and, under prod RLS WITH CHECK, rejected outright)."""
        uow = get_unit_of_work(db_session)
        owner = await _seed_isrc_owner(uow, user_id="alice")

        _ = await _remaster_resolver("sp_remaster_alice").resolve_to_canonical_tracks(
            ["sp_remaster_alice"], uow, user_id="alice"
        )

        row = (
            await db_session.execute(
                select(DBMatchReview).where(DBMatchReview.track_id == owner.id)
            )
        ).scalar_one()
        assert row.user_id == "alice"
        reviews, total = await uow.get_match_review_repository().list_pending_reviews(
            user_id="alice"
        )
        assert total == 1
        assert reviews[0].track_id == owner.id

    async def test_reimport_does_not_requeue_after_reject(
        self, db_session: AsyncSession
    ):
        """A rejected review must not be resurrected by the next import."""
        uow = get_unit_of_work(db_session)
        owner = await _seed_isrc_owner(uow)

        _ = await _remaster_resolver().resolve_to_canonical_tracks(
            ["sp_remaster_001"], uow, user_id="default"
        )
        await db_session.execute(
            update(DBMatchReview)
            .where(DBMatchReview.track_id == owner.id)
            .values(status="rejected")
        )
        await db_session.flush()

        # The next import resolves the id at the mapping lookup; no new review.
        result, metrics = await _remaster_resolver().resolve_to_canonical_tracks(
            ["sp_remaster_001"], uow, user_id="default"
        )

        assert metrics.existing == 1
        assert result["sp_remaster_001"].id != owner.id
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


def _remaster_connector_track(identifier: str = "sp_remaster_001") -> ConnectorTrack:
    return ConnectorTrack(
        connector_name="spotify",
        connector_track_identifier=identifier,
        title="Gold Rush (2024 Remaster)",
        artists=[ConnectorArtistCredit(credited_name="Neon Priest")],
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

        imported = await TrackResolutionService().ingest(
            "spotify", [_remaster_connector_track()], uow, user_id="default"
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
        assert review.match_method == "isrc_suspect"
        assert review.status == "pending"

    async def test_reimport_does_not_requeue_after_reject(
        self, db_session: AsyncSession
    ):
        uow = get_unit_of_work(db_session)
        connector_repo = uow.get_connector_repository()
        owner = await _seed_isrc_owner(uow)

        _ = await TrackResolutionService().ingest(
            "spotify", [_remaster_connector_track()], uow, user_id="default"
        )
        await db_session.execute(
            update(DBMatchReview)
            .where(DBMatchReview.track_id == owner.id)
            .values(status="rejected")
        )
        await db_session.flush()

        # Weekly re-sync: the mapping fast path resolves the track; no new review.
        _ = await TrackResolutionService().ingest(
            "spotify", [_remaster_connector_track()], uow, user_id="default"
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

        imported = await TrackResolutionService().ingest(
            "spotify", [_remaster_connector_track()], uow, user_id="default"
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
