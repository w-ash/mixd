"""Import mints artists from the ids it already holds (v0.12.1).

A Spotify-shaped ingest through ``TrackResolutionService`` leaves behind
canonical ``artists``, primary ``artist_mappings`` with an ``entity_kind =
'artist'`` accept event, and ``track_artists.artist_id`` filled on the
credits that named them — in the same transaction as the track write and
with no network. A re-ingest is zero-delta; a second track by the same
Spotify artist reuses it; a payload without ids (Apple's ``[None]``) leaves
its credits pending for the enrichment operation.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid7

from sqlalchemy import func, select

from src.application.services.track_resolution import TrackResolutionService
from src.domain.entities import ArtistCredit, ConnectorTrack
from src.infrastructure.persistence.database.models import (
    DBArtist,
    DBArtistMapping,
    DBConnectorArtist,
    DBResolutionEvent,
    DBTrackArtist,
)
from src.infrastructure.persistence.repositories.factories import get_unit_of_work


def _user() -> str:
    return f"artist-mint-{uuid7()}"


def _spotify_track(
    identifier: str, title: str, *artists: tuple[str, str | None]
) -> ConnectorTrack:
    """A payload shaped like ``convert_spotify_track_to_connector``'s output."""
    return ConnectorTrack(
        connector_name="spotify",
        connector_track_identifier=identifier,
        title=title,
        artists=[ArtistCredit(credited_name=name) for name, _ in artists],
        duration_ms=240_000,
        raw_metadata={
            "id": identifier,
            "name": title,
            "artists": [
                {"id": artist_id, "name": name, "type": "artist"}
                for name, artist_id in artists
            ],
            "artist_ids": [artist_id for _, artist_id in artists],
        },
        last_updated=datetime.now(UTC),
    )


def _apple_track(identifier: str, title: str, artist: str) -> ConnectorTrack:
    return ConnectorTrack(
        connector_name="apple",
        connector_track_identifier=identifier,
        title=title,
        artists=[ArtistCredit(credited_name=artist)],
        duration_ms=200_000,
        raw_metadata={"id": identifier, "artistName": artist, "artist_ids": [None]},
        last_updated=datetime.now(UTC),
    )


async def _artists(session, user_id: str) -> dict[str, UUID]:
    rows = await session.execute(
        select(DBArtist.name, DBArtist.id).where(DBArtist.user_id == user_id)
    )
    return dict(rows.tuples().all())


async def _mappings(
    session, user_id: str
) -> list[tuple[UUID, str, str, bool, datetime]]:
    rows = await session.execute(
        select(
            DBArtistMapping.artist_id,
            DBConnectorArtist.connector_artist_identifier,
            DBArtistMapping.match_method,
            DBArtistMapping.is_primary,
            DBArtistMapping.last_seen_at,
        )
        .join(
            DBConnectorArtist,
            DBConnectorArtist.id == DBArtistMapping.connector_artist_id,
        )
        .where(DBArtistMapping.user_id == user_id)
        .order_by(DBConnectorArtist.connector_artist_identifier)
    )
    return [tuple(row) for row in rows.tuples()]


async def _credits(session, track_id: UUID) -> list[tuple[int, str, UUID | None]]:
    rows = await session.execute(
        select(
            DBTrackArtist.position, DBTrackArtist.credited_name, DBTrackArtist.artist_id
        )
        .where(DBTrackArtist.track_id == track_id)
        .order_by(DBTrackArtist.position)
    )
    return [tuple(row) for row in rows.tuples()]


async def _artist_events(session, user_id: str) -> int:
    return (
        await session.execute(
            select(func.count())
            .select_from(DBResolutionEvent)
            .where(
                DBResolutionEvent.user_id == user_id,
                DBResolutionEvent.entity_kind == "artist",
                DBResolutionEvent.event_type == "accepted",
            )
        )
    ).scalar_one()


class TestSpotifyIngestMints:
    async def test_ingest_creates_artists_mappings_and_fills_credits(self, db_session):
        user_id = _user()
        service = TrackResolutionService()

        (track,) = await service.ingest(
            "spotify",
            [
                _spotify_track(
                    "sp-t1", "Odessa", ("Caribou", "sp-a1"), ("Koushik", "sp-a2")
                )
            ],
            get_unit_of_work(db_session),
            user_id=user_id,
        )

        artists = await _artists(db_session, user_id)
        assert set(artists) == {"Caribou", "Koushik"}
        assert await _mappings(db_session, user_id) == [
            (artists["Caribou"], "sp-a1", "direct", True, _any_datetime()),
            (artists["Koushik"], "sp-a2", "direct", True, _any_datetime()),
        ]
        assert await _credits(db_session, track.id) == [
            (0, "Caribou", artists["Caribou"]),
            (1, "Koushik", artists["Koushik"]),
        ]
        assert await _artist_events(db_session, user_id) == 2

    async def test_reingest_is_zero_delta(self, db_session):
        user_id = _user()
        service = TrackResolutionService()
        payload = _spotify_track("sp-t1", "Odessa", ("Caribou", "sp-a1"))

        _ = await service.ingest(
            "spotify", [payload], get_unit_of_work(db_session), user_id=user_id
        )
        before_artists = await _artists(db_session, user_id)
        before_mappings = await _mappings(db_session, user_id)
        before_events = await _artist_events(db_session, user_id)

        (track,) = await service.ingest(
            "spotify", [payload], get_unit_of_work(db_session), user_id=user_id
        )

        assert await _artists(db_session, user_id) == before_artists
        # Every credit already carries its artist id, so the fast path never
        # opens the minting pass: no row moves, ``last_seen_at`` included.
        assert await _mappings(db_session, user_id) == before_mappings
        assert await _artist_events(db_session, user_id) == before_events
        assert await _credits(db_session, track.id) == [
            (0, "Caribou", before_artists["Caribou"])
        ]

    async def test_a_second_track_by_the_same_artist_reuses_it(self, db_session):
        user_id = _user()
        service = TrackResolutionService()
        _ = await service.ingest(
            "spotify",
            [_spotify_track("sp-t1", "Odessa", ("Caribou", "sp-a1"))],
            get_unit_of_work(db_session),
            user_id=user_id,
        )

        (second,) = await service.ingest(
            "spotify",
            [_spotify_track("sp-t2", "Sun", ("Caribou", "sp-a1"))],
            get_unit_of_work(db_session),
            user_id=user_id,
        )

        artists = await _artists(db_session, user_id)
        assert list(artists) == ["Caribou"]
        assert len(await _mappings(db_session, user_id)) == 1
        assert await _credits(db_session, second.id) == [
            (0, "Caribou", artists["Caribou"])
        ]
        assert await _artist_events(db_session, user_id) == 1

    async def test_a_reencounter_heals_a_credit_left_without_an_id(self, db_session):
        """The backfill's rows carry no ids; the next import fills them."""
        user_id = _user()
        service = TrackResolutionService()
        payload = _spotify_track("sp-t1", "Odessa", ("Caribou", "sp-a1"))
        (track,) = await service.ingest(
            "spotify", [payload], get_unit_of_work(db_session), user_id=user_id
        )
        _ = await db_session.execute(
            DBTrackArtist.__table__
            .update()
            .where(DBTrackArtist.track_id == track.id)
            .values(artist_id=None)
        )

        _ = await service.ingest(
            "spotify", [payload], get_unit_of_work(db_session), user_id=user_id
        )

        artists = await _artists(db_session, user_id)
        assert await _credits(db_session, track.id) == [
            (0, "Caribou", artists["Caribou"])
        ]


class TestPayloadsWithoutIds:
    async def test_apple_credits_stay_pending(self, db_session):
        user_id = _user()

        (track,) = await TrackResolutionService().ingest(
            "apple",
            [_apple_track("ap-1", "Awake", "Tycho")],
            get_unit_of_work(db_session),
            user_id=user_id,
        )

        assert await _artists(db_session, user_id) == {}
        assert await _mappings(db_session, user_id) == []
        assert await _credits(db_session, track.id) == [(0, "Tycho", None)]

    async def test_various_artists_is_never_minted(self, db_session):
        user_id = _user()

        (track,) = await TrackResolutionService().ingest(
            "spotify",
            [
                _spotify_track(
                    "sp-t1", "Mix", ("Various Artists", "sp-va"), ("Tycho", "sp-a3")
                )
            ],
            get_unit_of_work(db_session),
            user_id=user_id,
        )

        artists = await _artists(db_session, user_id)
        assert list(artists) == ["Tycho"]
        assert await _credits(db_session, track.id) == [
            (0, "Various Artists", None),
            (1, "Tycho", artists["Tycho"]),
        ]


class _AnyDatetime:
    def __eq__(self, other: object) -> bool:
        return isinstance(other, datetime)

    def __hash__(self) -> int:
        return hash(_AnyDatetime)


def _any_datetime() -> _AnyDatetime:
    return _AnyDatetime()
