"""Playlist/likes ingest reuses a canonical instead of minting its twin.

v0.10.3 finding C4: canonical reuse was keyed on ISRC, which a remaster never
shares with its original, so every release of one song spawned its own
canonical even though their normalized artist+title already collided. The
Spotify inward resolver was fixed first; this covers the other creation path,
``ingest_external_tracks_bulk``, which had no equivalent guard at all.

Both paths now ask ``describes_same_recording`` — normalized *equality* plus
agreeing durations — so the cases here are the ones that predicate has to keep
apart, exercised end to end against real SQL.
"""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.constants import MatchMethod
from src.domain.entities import Artist, ConnectorTrack, Track
from src.infrastructure.persistence.database.db_models import (
    DBMatchReview,
    DBTrack,
    DBTrackMapping,
)
from src.infrastructure.persistence.repositories.factories import get_unit_of_work

ORIGINAL_ISRC = "GBEXH1900012"
REMASTER_ISRC = "USA2B2056087"


def _connector_track(
    identifier: str,
    *,
    title: str = "Ibrik",
    artist: str = "Bonobo",
    duration_ms: int | None = 245_733,
    isrc: str | None = None,
    connector: str = "spotify",
) -> ConnectorTrack:
    return ConnectorTrack(
        connector_name=connector,
        connector_track_identifier=identifier,
        title=title,
        artists=[Artist(name=artist)],
        album="Mixed",
        duration_ms=duration_ms,
        isrc=isrc,
        raw_metadata={},
        last_updated=datetime.now(UTC),
    )


async def _seed_original(
    db_session: AsyncSession,
    *,
    title: str = "Ibrik",
    artist: str = "Bonobo",
    duration_ms: int | None = 245_733,
    isrc: str | None = ORIGINAL_ISRC,
    connector_id: str | None = "sp_original",
) -> Track:
    """A canonical the playlist import will meet again under a different id."""
    uow = get_unit_of_work(db_session)
    if connector_id is None:
        return await uow.get_track_repository().save_track(
            Track(
                title=title,
                artists=[Artist(name=artist)],
                duration_ms=duration_ms,
                isrc=isrc,
            )
        )
    tracks = await uow.get_connector_repository().ingest_external_tracks_bulk(
        "spotify",
        [
            _connector_track(
                connector_id,
                title=title,
                artist=artist,
                duration_ms=duration_ms,
                isrc=isrc,
            )
        ],
        user_id="default",
    )
    return tracks[0]


async def _canonical_count(db_session: AsyncSession) -> int:
    return (
        await db_session.execute(select(func.count()).select_from(DBTrack))
    ).scalar_one()


class TestIngestReusesAnExistingCanonical:
    async def test_a_remaster_with_its_own_isrc_reuses_the_original(
        self, db_session: AsyncSession
    ):
        """The C4 case exactly: same normalized artist+title, same length,
        different ISRC — and before this fix, a second canonical."""
        original = await _seed_original(db_session)
        before = await _canonical_count(db_session)

        imported = (
            await get_unit_of_work(db_session)
            .get_connector_repository()
            .ingest_external_tracks_bulk(
                "spotify",
                [_connector_track("sp_remaster", isrc=REMASTER_ISRC)],
                user_id="default",
            )
        )

        assert imported[0].id == original.id
        assert await _canonical_count(db_session) == before

    async def test_the_reuse_mapping_records_how_it_was_decided(
        self, db_session: AsyncSession
    ):
        original = await _seed_original(db_session)

        _ = (
            await get_unit_of_work(db_session)
            .get_connector_repository()
            .ingest_external_tracks_bulk(
                "spotify",
                [_connector_track("sp_remaster", isrc=REMASTER_ISRC)],
                user_id="default",
            )
        )

        methods = (
            (
                await db_session.execute(
                    select(DBTrackMapping.match_method).where(
                        DBTrackMapping.track_id == original.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert sorted(methods) == ["canonical_reuse", "direct"]

    async def test_the_canonicals_existing_primary_is_not_deposed(
        self, db_session: AsyncSession
    ):
        """The reuse mapping is an alias — the id the canonical already
        described itself by keeps primacy."""
        original = await _seed_original(db_session)

        _ = (
            await get_unit_of_work(db_session)
            .get_connector_repository()
            .ingest_external_tracks_bulk(
                "spotify",
                [_connector_track("sp_remaster", isrc=REMASTER_ISRC)],
                user_id="default",
            )
        )

        primaries = (
            (
                await db_session.execute(
                    select(DBTrackMapping.match_method).where(
                        DBTrackMapping.track_id == original.id,
                        DBTrackMapping.is_primary.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert primaries == ["direct"]

    async def test_a_canonical_with_no_mapping_for_this_connector_gains_a_primary(
        self, db_session: AsyncSession
    ):
        """A Last.fm-born canonical a Spotify import has just matched must not
        be left holding a live mapping with no primary."""
        original = await _seed_original(db_session, connector_id=None)

        _ = (
            await get_unit_of_work(db_session)
            .get_connector_repository()
            .ingest_external_tracks_bulk(
                "spotify",
                [_connector_track("sp_remaster", isrc=REMASTER_ISRC)],
                user_id="default",
            )
        )

        primaries = (
            (
                await db_session.execute(
                    select(DBTrackMapping.match_method).where(
                        DBTrackMapping.track_id == original.id,
                        DBTrackMapping.is_primary.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert primaries == [MatchMethod.CANONICAL_REUSE]


class TestIngestStillCreatesWhenItShould:
    async def test_a_different_recording_of_the_same_name_keeps_its_canonical(
        self, db_session: AsyncSession
    ):
        """ "Oh The Sunn!" at 235s and at 138s are two recordings, whatever
        their names normalize to."""
        original = await _seed_original(db_session, duration_ms=235_400)

        imported = (
            await get_unit_of_work(db_session)
            .get_connector_repository()
            .ingest_external_tracks_bulk(
                "spotify",
                [_connector_track("sp_other", duration_ms=138_213, isrc=REMASTER_ISRC)],
                user_id="default",
            )
        )

        assert imported[0].id != original.id

    async def test_a_remix_matched_only_by_parenthetical_stripping_is_refused(
        self, db_session: AsyncSession
    ):
        """``find_tracks_by_title_artist`` proposes this pair; equality on the
        normalized forms is what refuses it, and the 5.8s gap would not."""
        original = await _seed_original(
            db_session, title="Ice Ice Baby", duration_ms=257_000
        )

        imported = (
            await get_unit_of_work(db_session)
            .get_connector_repository()
            .ingest_external_tracks_bulk(
                "spotify",
                [
                    _connector_track(
                        "sp_remix",
                        title="Ice Ice Baby (Wunderbros Dubstep Remix)",
                        duration_ms=251_200,
                        isrc=REMASTER_ISRC,
                    )
                ],
                user_id="default",
            )
        )

        assert imported[0].id != original.id

    async def test_a_canonical_of_unknown_length_is_never_reused_on_names_alone(
        self, db_session: AsyncSession
    ):
        original = await _seed_original(db_session, duration_ms=None)

        imported = (
            await get_unit_of_work(db_session)
            .get_connector_repository()
            .ingest_external_tracks_bulk(
                "spotify",
                [_connector_track("sp_remaster", isrc=REMASTER_ISRC)],
                user_id="default",
            )
        )

        assert imported[0].id != original.id

    async def test_another_users_canonical_is_never_reused(
        self, db_session: AsyncSession
    ):
        """The probe is user-scoped — C7's tenant split must not be healed by
        cross-tenant identity matching."""
        original = await _seed_original(db_session)

        imported = (
            await get_unit_of_work(db_session)
            .get_connector_repository()
            .ingest_external_tracks_bulk(
                "spotify",
                [_connector_track("sp_remaster", isrc=REMASTER_ISRC)],
                user_id="alice",
            )
        )

        assert imported[0].id != original.id
        assert imported[0].user_id == "alice"


class TestTwinsInsideOneBatch:
    async def test_two_pressings_in_one_import_share_the_first_ones_canonical(
        self, db_session: AsyncSession
    ):
        """Neither is in the database yet, so neither can find the other by
        lookup — the leader created earlier in the batch is what the follower
        folds onto."""
        before = await _canonical_count(db_session)

        imported = (
            await get_unit_of_work(db_session)
            .get_connector_repository()
            .ingest_external_tracks_bulk(
                "spotify",
                [
                    _connector_track("sp_original", isrc=ORIGINAL_ISRC),
                    _connector_track("sp_remaster", isrc=REMASTER_ISRC),
                ],
                user_id="default",
            )
        )

        assert imported[0].id == imported[1].id
        assert await _canonical_count(db_session) == before + 1
        # Two live mappings, and the leader's is the one that describes it.
        primaries = (
            (
                await db_session.execute(
                    select(DBTrackMapping.match_method).where(
                        DBTrackMapping.track_id == imported[0].id,
                        DBTrackMapping.is_primary.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert primaries == ["direct"]

    async def test_two_masters_of_different_lengths_each_keep_their_canonical(
        self, db_session: AsyncSession
    ):
        imported = (
            await get_unit_of_work(db_session)
            .get_connector_repository()
            .ingest_external_tracks_bulk(
                "spotify",
                [
                    _connector_track(
                        "sp_original", duration_ms=448_200, isrc=ORIGINAL_ISRC
                    ),
                    _connector_track(
                        "sp_edit", duration_ms=216_000, isrc=REMASTER_ISRC
                    ),
                ],
                user_id="default",
            )
        )

        assert imported[0].id != imported[1].id


class TestIsrcEvidenceStillDecidesFirst:
    async def test_a_suspect_isrc_collision_is_never_folded_away(
        self, db_session: AsyncSession
    ):
        """v0.8.18: a contested ISRC mints a distinct canonical and queues a
        review. A name match must not settle by the back door what the review
        exists to put in front of a person — and here the names are equal, so
        only the ISRC exclusion can be what saves it."""
        owner = await _seed_original(db_session, duration_ms=200_000)

        imported = (
            await get_unit_of_work(db_session)
            .get_connector_repository()
            .ingest_external_tracks_bulk(
                "spotify",
                # Same ISRC as the owner, 15s longer — suspect.
                [
                    _connector_track(
                        "sp_remaster", duration_ms=215_000, isrc=ORIGINAL_ISRC
                    )
                ],
                user_id="default",
            )
        )

        assert imported[0].id != owner.id
        assert imported[0].isrc is None
        review = (
            await db_session.execute(
                select(DBMatchReview).where(DBMatchReview.track_id == owner.id)
            )
        ).scalar_one()
        assert review.match_method == MatchMethod.ISRC_SUSPECT

    async def test_a_trustworthy_isrc_owner_still_wins_the_group(
        self, db_session: AsyncSession
    ):
        """The ISRC decides, so the name probe never gets to nominate a
        different canonical first."""
        owner = await _seed_original(db_session, duration_ms=245_733)

        imported = (
            await get_unit_of_work(db_session)
            .get_connector_repository()
            .ingest_external_tracks_bulk(
                "spotify",
                [_connector_track("sp_same_isrc", isrc=ORIGINAL_ISRC)],
                user_id="default",
            )
        )

        assert imported[0].id == owner.id


class TestAlreadyMappedTracksAreUntouched:
    async def test_a_re_sync_resolves_by_mapping_and_creates_nothing(
        self, db_session: AsyncSession
    ):
        original = await _seed_original(db_session)
        before = await _canonical_count(db_session)

        imported = (
            await get_unit_of_work(db_session)
            .get_connector_repository()
            .ingest_external_tracks_bulk(
                "spotify",
                [_connector_track("sp_original", isrc=ORIGINAL_ISRC)],
                user_id="default",
            )
        )

        assert imported[0].id == original.id
        assert await _canonical_count(db_session) == before
