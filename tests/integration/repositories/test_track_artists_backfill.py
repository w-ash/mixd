"""The ``track_artists`` backfill statement, and the FK behavior it relies on.

Migration 061 runs ``backfill_track_artists_sql()`` against production; this
exercises the same string against the ``create_all`` schema, which is the only
schema the integration suite ever builds. What matters here is the statement's
semantics — positions, the null artist, convergence on a re-run — and the
cascade rules the credit row depends on.
"""

import json
from uuid import UUID, uuid7

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.persistence.database.backfills import (
    backfill_track_artists_sql,
)
from tests.fixtures import TEST_USER_ID

_INSERT_TRACK = sa.text(
    "INSERT INTO tracks (id, user_id, title, artists, version, "
    "created_at, updated_at) "
    "VALUES (:id, :user, :title, CAST(:artists AS JSONB), 1, now(), now())"
)

_INSERT_ARTIST = sa.text(
    "INSERT INTO artists (id, user_id, name, created_at, updated_at) "
    "VALUES (:id, :user, :name, now(), now())"
)


async def _seed_track(session: AsyncSession, title: str, names: list[str]) -> UUID:
    track_id = uuid7()
    await session.execute(
        _INSERT_TRACK,
        {
            "id": track_id,
            "user": TEST_USER_ID,
            "title": title,
            "artists": json.dumps({"names": names}),
        },
    )
    return track_id


async def _credits(
    session: AsyncSession, track_id: UUID
) -> list[tuple[int, str, UUID | None]]:
    result = await session.execute(
        sa.text(
            "SELECT position, credited_name, artist_id FROM track_artists "
            "WHERE track_id = :track ORDER BY position"
        ),
        {"track": track_id},
    )
    return [(row[0], row[1], row[2]) for row in result]


class TestBackfillExpandsTheJsonb:
    @pytest.mark.asyncio
    async def test_positions_and_names_are_preserved(
        self, db_session: AsyncSession
    ) -> None:
        track_id = await _seed_track(db_session, "Odessa", ["Caribou", "Koushik"])

        await db_session.execute(sa.text(backfill_track_artists_sql()))

        assert await _credits(db_session, track_id) == [
            (0, "Caribou", None),
            (1, "Koushik", None),
        ]

    @pytest.mark.asyncio
    async def test_the_sentinel_keeps_its_credit_with_a_null_artist(
        self, db_session: AsyncSession
    ) -> None:
        track_id = await _seed_track(db_session, "Birdsong", ["Various Artists"])

        await db_session.execute(sa.text(backfill_track_artists_sql()))

        credits = await _credits(db_session, track_id)
        assert credits == [(0, "Various Artists", None)]

    @pytest.mark.asyncio
    async def test_a_track_with_no_credits_produces_no_rows(
        self, db_session: AsyncSession
    ) -> None:
        track_id = await _seed_track(db_session, "Untitled", [])

        await db_session.execute(sa.text(backfill_track_artists_sql()))

        assert await _credits(db_session, track_id) == []

    @pytest.mark.asyncio
    async def test_rerunning_converges_without_duplicates(
        self, db_session: AsyncSession
    ) -> None:
        track_id = await _seed_track(db_session, "Sun", ["Caribou", "Caribou"])

        await db_session.execute(sa.text(backfill_track_artists_sql()))
        await db_session.execute(sa.text(backfill_track_artists_sql()))

        # The duplicate name is a real credit at its own position; the ON
        # CONFLICT arbiter is (track_id, position), not the name.
        assert await _credits(db_session, track_id) == [
            (0, "Caribou", None),
            (1, "Caribou", None),
        ]

    @pytest.mark.asyncio
    async def test_a_resolved_credit_survives_a_rerun_unchanged(
        self, db_session: AsyncSession
    ) -> None:
        track_id = await _seed_track(db_session, "Bowls", ["Caribou"])
        artist_id = uuid7()
        await db_session.execute(
            _INSERT_ARTIST,
            {"id": artist_id, "user": TEST_USER_ID, "name": "Caribou"},
        )
        await db_session.execute(sa.text(backfill_track_artists_sql()))
        await db_session.execute(
            sa.text(
                "UPDATE track_artists SET artist_id = :artist WHERE track_id = :track"
            ),
            {"artist": artist_id, "track": track_id},
        )

        await db_session.execute(sa.text(backfill_track_artists_sql()))

        assert await _credits(db_session, track_id) == [(0, "Caribou", artist_id)]


class TestCreditRowLifetime:
    @pytest.mark.asyncio
    async def test_deleting_the_track_removes_its_credits(
        self, db_session: AsyncSession
    ) -> None:
        track_id = await _seed_track(db_session, "Melody Day", ["Caribou"])
        await db_session.execute(sa.text(backfill_track_artists_sql()))

        await db_session.execute(
            sa.text("DELETE FROM tracks WHERE id = :id"), {"id": track_id}
        )

        assert await _credits(db_session, track_id) == []

    @pytest.mark.asyncio
    async def test_deleting_the_artist_keeps_the_credit_and_nulls_the_link(
        self, db_session: AsyncSession
    ) -> None:
        track_id = await _seed_track(db_session, "Leave House", ["Caribou"])
        artist_id = uuid7()
        await db_session.execute(
            _INSERT_ARTIST,
            {"id": artist_id, "user": TEST_USER_ID, "name": "Caribou"},
        )
        await db_session.execute(sa.text(backfill_track_artists_sql()))
        await db_session.execute(
            sa.text(
                "UPDATE track_artists SET artist_id = :artist WHERE track_id = :track"
            ),
            {"artist": artist_id, "track": track_id},
        )

        await db_session.execute(
            sa.text("DELETE FROM artists WHERE id = :id"), {"id": artist_id}
        )

        assert await _credits(db_session, track_id) == [(0, "Caribou", None)]

    @pytest.mark.asyncio
    async def test_deleting_the_artist_removes_its_favorite(
        self, db_session: AsyncSession
    ) -> None:
        artist_id = uuid7()
        await db_session.execute(
            _INSERT_ARTIST,
            {"id": artist_id, "user": TEST_USER_ID, "name": "Four Tet"},
        )
        await db_session.execute(
            sa.text(
                "INSERT INTO artist_favorites "
                "(user_id, artist_id, favorited_at, created_at, updated_at) "
                "VALUES (:user, :artist, now(), now(), now())"
            ),
            {"user": TEST_USER_ID, "artist": artist_id},
        )

        await db_session.execute(
            sa.text("DELETE FROM artists WHERE id = :id"), {"id": artist_id}
        )

        remaining = await db_session.execute(
            sa.text("SELECT count(*) FROM artist_favorites WHERE artist_id = :artist"),
            {"artist": artist_id},
        )
        assert remaining.scalar_one() == 0
