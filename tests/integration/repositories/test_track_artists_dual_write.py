"""The ``track_artists`` dual write, and the reader it feeds.

Every ``tracks`` writer keeps the credit rows current — they are what the
mapper reads back; the JSONB column serves only search and the first-artist
sort. What is pinned here: positions
follow the credit order; a re-save from a connector payload (whose credits
carry no ``artist_id``) keeps the ids a minter assigned; a changed name at a
position drops that id; a shorter credit list shrinks the rows; and
``TrackMapper`` reads the credits back — ``artist_id`` included — instead of
the JSON names.
"""

from uuid import UUID, uuid7

from attrs import evolve
from sqlalchemy import select

from src.domain.entities.artist import Artist
from src.domain.entities.track import ArtistCredit, Track
from src.infrastructure.persistence.database.models import DBTrackArtist
from src.infrastructure.persistence.repositories.factories import get_unit_of_work


def _user() -> str:
    return f"dual-write-{uuid7()}"


def _track(user_id: str, *credits: ArtistCredit, title: str = "Odessa") -> Track:
    return Track(id=None, user_id=user_id, title=title, artists=list(credits))


async def _credits(
    session, track_id: UUID
) -> list[tuple[int, str, UUID | None, str | None]]:
    result = await session.execute(
        select(
            DBTrackArtist.position,
            DBTrackArtist.credited_name,
            DBTrackArtist.artist_id,
            DBTrackArtist.join_phrase,
        )
        .where(DBTrackArtist.track_id == track_id)
        .order_by(DBTrackArtist.position)
    )
    return [tuple(row) for row in result.tuples()]


class TestSaveWritesCredits:
    async def test_save_tracks_writes_one_row_per_credit_in_order(self, db_session):
        track_repo = get_unit_of_work(db_session).get_track_repository()
        user_id = _user()

        (saved,) = await track_repo.save_tracks([
            _track(
                user_id,
                ArtistCredit(credited_name="Caribou", join_phrase=" & "),
                ArtistCredit(credited_name="Koushik"),
            )
        ])

        assert await _credits(db_session, saved.id) == [
            (0, "Caribou", None, " & "),
            (1, "Koushik", None, None),
        ]

    async def test_save_track_update_branch_rewrites_the_credits(self, db_session):
        track_repo = get_unit_of_work(db_session).get_track_repository()
        user_id = _user()
        (saved,) = await track_repo.save_tracks([
            _track(user_id, ArtistCredit(credited_name="Caribou"))
        ])

        await track_repo.save_track(
            evolve(
                saved,
                artists=[
                    ArtistCredit(credited_name="Caribou"),
                    ArtistCredit(credited_name="Koushik"),
                ],
            )
        )

        assert [name for _, name, _, _ in await _credits(db_session, saved.id)] == [
            "Caribou",
            "Koushik",
        ]

    async def test_fewer_credits_shrink_the_rows(self, db_session):
        track_repo = get_unit_of_work(db_session).get_track_repository()
        user_id = _user()
        (saved,) = await track_repo.save_tracks([
            _track(
                user_id,
                ArtistCredit(credited_name="Caribou"),
                ArtistCredit(credited_name="Koushik"),
                ArtistCredit(credited_name="Guest"),
            )
        ])

        await track_repo.save_track(
            evolve(saved, artists=[ArtistCredit(credited_name="Caribou")])
        )

        assert await _credits(db_session, saved.id) == [(0, "Caribou", None, None)]


class TestAssignedIdsSurviveARewrite:
    async def test_resaving_with_id_less_credits_keeps_the_assigned_id(
        self, db_session
    ):
        uow = get_unit_of_work(db_session)
        track_repo, artist_repo = (
            uow.get_track_repository(),
            uow.get_artist_repository(),
        )
        user_id = _user()
        (artist,) = await artist_repo.save_artists([
            Artist(name="Caribou", user_id=user_id)
        ])
        (saved,) = await track_repo.save_tracks([
            _track(
                user_id,
                ArtistCredit(credited_name="Caribou", artist_id=artist.id),
            )
        ])

        # A Track rebuilt from a connector payload carries no artist ids.
        await track_repo.save_track(
            evolve(saved, artists=[ArtistCredit(credited_name="Caribou")])
        )

        assert await _credits(db_session, saved.id) == [(0, "Caribou", artist.id, None)]

    async def test_an_incoming_id_on_a_conflict_is_not_taken(self, db_session):
        # The writer never assigns: ``set_credit_artist_ids`` is the one path.
        uow = get_unit_of_work(db_session)
        track_repo, artist_repo = (
            uow.get_track_repository(),
            uow.get_artist_repository(),
        )
        user_id = _user()
        (artist,) = await artist_repo.save_artists([
            Artist(name="Caribou", user_id=user_id)
        ])
        (saved,) = await track_repo.save_tracks([
            _track(user_id, ArtistCredit(credited_name="Caribou"))
        ])

        await track_repo.save_track(
            evolve(
                saved,
                artists=[ArtistCredit(credited_name="Caribou", artist_id=artist.id)],
            )
        )

        assert await _credits(db_session, saved.id) == [(0, "Caribou", None, None)]

    async def test_the_batched_update_path_rewrites_every_tracks_credits(
        self, db_session
    ):
        # ``save_tracks`` over already-known rows: one credit write for the
        # batch, and each track reads back its own new line-up.
        uow = get_unit_of_work(db_session)
        track_repo, artist_repo = (
            uow.get_track_repository(),
            uow.get_artist_repository(),
        )
        user_id = _user()
        (artist,) = await artist_repo.save_artists([
            Artist(name="Caribou", user_id=user_id)
        ])
        first, second = await track_repo.save_tracks([
            _track(user_id, ArtistCredit(credited_name="Caribou", artist_id=artist.id)),
            _track(
                user_id,
                ArtistCredit(credited_name="Bonobo"),
                ArtistCredit(credited_name="Kiara"),
                title="Kiara",
            ),
        ])

        resaved = await track_repo.save_tracks([
            evolve(first, artists=[ArtistCredit(credited_name="Caribou")]),
            evolve(second, artists=[ArtistCredit(credited_name="Bonobo")]),
        ])

        assert [t.version for t in resaved] == [2, 2]
        assert [c.artist_id for c in resaved[0].artists] == [artist.id]
        assert [c.credited_name for c in resaved[1].artists] == ["Bonobo"]
        assert await _credits(db_session, first.id) == [(0, "Caribou", artist.id, None)]
        assert await _credits(db_session, second.id) == [(0, "Bonobo", None, None)]

    async def test_a_changed_name_at_a_position_drops_the_id(self, db_session):
        uow = get_unit_of_work(db_session)
        track_repo, artist_repo = (
            uow.get_track_repository(),
            uow.get_artist_repository(),
        )
        user_id = _user()
        (artist,) = await artist_repo.save_artists([
            Artist(name="Caribou", user_id=user_id)
        ])
        (saved,) = await track_repo.save_tracks([
            _track(user_id, ArtistCredit(credited_name="Caribou", artist_id=artist.id))
        ])

        await track_repo.save_track(
            evolve(saved, artists=[ArtistCredit(credited_name="Manitoba")])
        )

        assert await _credits(db_session, saved.id) == [(0, "Manitoba", None, None)]


class TestSetCreditArtistIds:
    async def test_only_null_credits_are_filled(self, db_session):
        uow = get_unit_of_work(db_session)
        track_repo, artist_repo = (
            uow.get_track_repository(),
            uow.get_artist_repository(),
        )
        user_id = _user()
        resolved, proposed = await artist_repo.save_artists([
            Artist(name="Caribou", user_id=user_id),
            Artist(name="Koushik", user_id=user_id),
        ])
        (saved,) = await track_repo.save_tracks([
            _track(
                user_id,
                ArtistCredit(credited_name="Caribou", artist_id=resolved.id),
                ArtistCredit(credited_name="Koushik"),
            )
        ])

        filled = await track_repo.set_credit_artist_ids(
            [(saved.id, 0, proposed.id), (saved.id, 1, proposed.id)],
            user_id=user_id,
        )

        assert filled == 1
        assert await _credits(db_session, saved.id) == [
            (0, "Caribou", resolved.id, None),
            (1, "Koushik", proposed.id, None),
        ]
        # Re-running the same batch fills nothing: every credit now has an id.
        assert (
            await track_repo.set_credit_artist_ids(
                [(saved.id, 1, proposed.id)], user_id=user_id
            )
            == 0
        )

    async def test_another_tenants_credit_is_never_filled(self, db_session):
        uow = get_unit_of_work(db_session)
        track_repo, artist_repo = (
            uow.get_track_repository(),
            uow.get_artist_repository(),
        )
        owner, intruder = _user(), _user()
        (artist,) = await artist_repo.save_artists([
            Artist(name="Caribou", user_id=owner)
        ])
        (saved,) = await track_repo.save_tracks([
            _track(owner, ArtistCredit(credited_name="Caribou"))
        ])

        filled = await track_repo.set_credit_artist_ids(
            [(saved.id, 0, artist.id)], user_id=intruder
        )

        assert filled == 0
        assert await _credits(db_session, saved.id) == [(0, "Caribou", None, None)]

    async def test_an_empty_batch_is_a_no_op(self, db_session):
        track_repo = get_unit_of_work(db_session).get_track_repository()

        assert await track_repo.set_credit_artist_ids([], user_id=_user()) == 0


class TestReaders:
    async def test_the_mapper_round_trips_artist_ids_through_the_credit_rows(
        self, db_session
    ):
        uow = get_unit_of_work(db_session)
        track_repo, artist_repo = (
            uow.get_track_repository(),
            uow.get_artist_repository(),
        )
        user_id = _user()
        (artist,) = await artist_repo.save_artists([
            Artist(name="Caribou", user_id=user_id)
        ])
        (saved,) = await track_repo.save_tracks([
            _track(
                user_id,
                ArtistCredit(
                    credited_name="Caribou", artist_id=artist.id, join_phrase=" & "
                ),
                ArtistCredit(credited_name="Koushik"),
            )
        ])

        read = await track_repo.get_track_by_id(saved.id, user_id=user_id)

        assert [c.credited_name for c in read.artists] == ["Caribou", "Koushik"]
        assert read.artists[0].artist_id == artist.id
        assert read.artists[0].join_phrase == " & "
        assert read.artists[1].artist_id is None

    async def test_list_tracks_filters_by_credited_artist(self, db_session):
        uow = get_unit_of_work(db_session)
        track_repo, artist_repo = (
            uow.get_track_repository(),
            uow.get_artist_repository(),
        )
        user_id = _user()
        (artist,) = await artist_repo.save_artists([
            Artist(name="Caribou", user_id=user_id)
        ])
        (credited,) = await track_repo.save_tracks([
            _track(
                user_id,
                ArtistCredit(credited_name="Someone"),
                ArtistCredit(credited_name="Caribou", artist_id=artist.id),
                title="Odessa",
            )
        ])
        await track_repo.save_tracks([
            _track(user_id, ArtistCredit(credited_name="Nobody"), title="Elsewhere")
        ])

        page = await track_repo.list_tracks(user_id=user_id, artist_id=artist.id)

        assert [t.id for t in page["tracks"]] == [credited.id]
        assert page["total"] == 1
