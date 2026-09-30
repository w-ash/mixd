"""Minting canonical artists from a v0.12.1-shaped library, against real tables.

What a unit test cannot show: that a library whose ``track_artists`` rows all
carry ``artist_id = NULL`` — the state migration 061 leaves and no re-import
heals — becomes canonical ``artists``, ``artist_mappings`` and filled credits
once the operation runs; that the run records ``entity_kind='artist'``
resolution events; that a second run is a zero-delta no-op because the filled
credits have left the candidate query; and that ``limit`` stops mid-library so
a following run finishes the rest.

The seed goes through the repository write path with no minter in it:
``save_tracks`` writes credit rows and never assigns ``artist_id``, and
``map_tracks_to_connectors`` stores the payload's credits with the service
artist ids on them.
"""

from uuid import uuid7

from sqlalchemy import func, select

from src.application.use_cases.mint_artists import (
    MintArtistsCommand,
    MintArtistsUseCase,
)
from src.domain.entities import ArtistCredit, ConnectorArtistCredit, Track
from src.domain.repositories.connector import ConnectorMappingSpec
from src.infrastructure.persistence.database.models import (
    DBArtist,
    DBArtistMapping,
    DBResolutionEvent,
    DBTrackArtist,
)
from src.infrastructure.persistence.repositories.factories import get_unit_of_work

# Every track credits this artist first, so the run has one artist to reuse
# across the library and one to create per track.
SHARED = "Shared Headliner"


async def seed_library(db_session, user_id: str, *, size: int) -> list[Track]:
    """A library in the post-061 shape: credits stored, none minted."""
    uow = get_unit_of_work(db_session)
    saved = await uow.get_track_repository().save_tracks([
        Track(
            title=f"Song {index}",
            artists=(
                ArtistCredit(credited_name=SHARED),
                ArtistCredit(credited_name=f"Guest {index}"),
            ),
            user_id=user_id,
        )
        for index in range(size)
    ])
    _ = await uow.get_connector_repository().map_tracks_to_connectors([
        ConnectorMappingSpec(
            track=track,
            connector="spotify",
            connector_id=f"sp-track-{user_id}-{index}",
            match_method="direct",
            confidence=95,
            credits=(
                ConnectorArtistCredit(
                    credited_name=SHARED,
                    connector_artist_identifier=f"sp-shared-{user_id}",
                ),
                ConnectorArtistCredit(
                    credited_name=f"Guest {index}",
                    connector_artist_identifier=f"sp-guest-{user_id}-{index}",
                ),
            ),
            primary=True,
        )
        for index, track in enumerate(saved)
    ])
    return saved


async def count(db_session, model, *criteria) -> int:
    result = await db_session.execute(
        select(func.count()).select_from(model).where(*criteria)
    )
    return result.scalar_one()


async def unlinked_credits(db_session, user_id: str) -> int:
    return await count(
        db_session,
        DBTrackArtist,
        DBTrackArtist.user_id == user_id,
        DBTrackArtist.artist_id.is_(None),
    )


class TestMintFromStoredCredits:
    async def test_seeded_library_mints_artists_mappings_and_credit_fills(
        self, db_session
    ):
        user_id = f"mint-{uuid7()}"
        tracks = await seed_library(db_session, user_id, size=3)

        # The state the operation exists for: credits stored, nothing minted.
        assert await unlinked_credits(db_session, user_id) == 6
        assert await count(db_session, DBArtist, DBArtist.user_id == user_id) == 0

        result = await MintArtistsUseCase().execute(
            MintArtistsCommand(user_id=user_id), get_unit_of_work(db_session)
        )

        metrics = result.result.summary_metrics
        assert metrics.get("tracks_processed") == 3
        # One shared artist plus one guest per track.
        assert metrics.get("artists_created") == 4
        assert metrics.get("credits_linked") == 6

        assert await count(db_session, DBArtist, DBArtist.user_id == user_id) == 4
        assert (
            await count(db_session, DBArtistMapping, DBArtistMapping.user_id == user_id)
            == 4
        )
        assert await unlinked_credits(db_session, user_id) == 0

        # Every credit points at an artist whose name it is credited under.
        names = await db_session.execute(
            select(DBTrackArtist.credited_name, DBArtist.name)
            .join(DBArtist, DBArtist.id == DBTrackArtist.artist_id)
            .where(DBTrackArtist.user_id == user_id)
        )
        assert all(credited == artist for credited, artist in names.tuples())

        # The decisions are auditable as artist-side resolution events.
        events = await count(
            db_session,
            DBResolutionEvent,
            DBResolutionEvent.user_id == user_id,
            DBResolutionEvent.entity_kind == "artist",
        )
        assert events == 4

        # The seeded tracks are the ones that moved.
        assert {t.id for t in tracks} == set(
            (
                await db_session.execute(
                    select(DBTrackArtist.track_id).where(
                        DBTrackArtist.user_id == user_id
                    )
                )
            )
            .scalars()
            .all()
        )

    async def test_second_run_is_zero_delta(self, db_session):
        user_id = f"mint-{uuid7()}"
        _ = await seed_library(db_session, user_id, size=2)
        uow = get_unit_of_work(db_session)

        _ = await MintArtistsUseCase().execute(MintArtistsCommand(user_id=user_id), uow)
        before = (
            await count(db_session, DBArtist, DBArtist.user_id == user_id),
            await count(
                db_session, DBArtistMapping, DBArtistMapping.user_id == user_id
            ),
            await unlinked_credits(db_session, user_id),
        )

        again = await MintArtistsUseCase().execute(
            MintArtistsCommand(user_id=user_id), get_unit_of_work(db_session)
        )

        # Nothing is left to page: the filled credits left the candidate query.
        assert again.result.summary_metrics.get("tracks_processed") == 0
        assert again.result.summary_metrics.get("artists_created") == 0
        assert before == (
            await count(db_session, DBArtist, DBArtist.user_id == user_id),
            await count(
                db_session, DBArtistMapping, DBArtistMapping.user_id == user_id
            ),
            await unlinked_credits(db_session, user_id),
        )

    async def test_limit_stops_short_and_the_next_run_finishes(self, db_session):
        user_id = f"mint-{uuid7()}"
        _ = await seed_library(db_session, user_id, size=4)

        capped = await MintArtistsUseCase().execute(
            MintArtistsCommand(user_id=user_id, limit=2), get_unit_of_work(db_session)
        )
        assert capped.result.summary_metrics.get("tracks_processed") == 2
        # Two tracks' worth of credits remain — one guest each, plus the shared
        # credit on each of them.
        assert await unlinked_credits(db_session, user_id) == 4

        rest = await MintArtistsUseCase().execute(
            MintArtistsCommand(user_id=user_id), get_unit_of_work(db_session)
        )
        assert rest.result.summary_metrics.get("tracks_processed") == 2
        # The shared artist was minted by the first run and reused by this one.
        assert rest.result.summary_metrics.get("artists_created") == 2
        assert rest.result.summary_metrics.get("artists_reused") == 1
        assert await unlinked_credits(db_session, user_id) == 0
        assert await count(db_session, DBArtist, DBArtist.user_id == user_id) == 5

    async def test_dry_run_writes_nothing(self, db_session):
        user_id = f"mint-{uuid7()}"
        _ = await seed_library(db_session, user_id, size=2)

        result = await MintArtistsUseCase().execute(
            MintArtistsCommand(user_id=user_id, dry_run=True),
            get_unit_of_work(db_session),
        )

        assert result.result.summary_metrics.get("tracks_processed") == 2
        assert result.result.summary_metrics.get("credits_available") == 4
        assert await count(db_session, DBArtist, DBArtist.user_id == user_id) == 0
        assert await unlinked_credits(db_session, user_id) == 4

    async def test_a_library_with_no_payload_ids_is_skipped(self, db_session):
        """A credit with no service id is the enrichment pass's problem, not this one."""
        user_id = f"mint-{uuid7()}"
        uow = get_unit_of_work(db_session)
        saved = await uow.get_track_repository().save_tracks([
            Track(
                title="Nameless",
                artists=(ArtistCredit(credited_name="Anonymous"),),
                user_id=user_id,
            )
        ])
        _ = await uow.get_connector_repository().map_tracks_to_connectors([
            ConnectorMappingSpec(
                track=saved[0],
                connector="apple_music",
                connector_id=f"am-{user_id}",
                match_method="direct",
                confidence=95,
                credits=(ConnectorArtistCredit(credited_name="Anonymous"),),
                primary=True,
            )
        ])

        result = await MintArtistsUseCase().execute(
            MintArtistsCommand(user_id=user_id), get_unit_of_work(db_session)
        )

        assert result.result.summary_metrics.get("tracks_processed") == 0
        assert await count(db_session, DBArtist, DBArtist.user_id == user_id) == 0
