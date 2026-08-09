"""COPY-based bulk insert for the connector-play ledger.

Covers ``ConnectorTrackPlayRepository.bulk_insert_connector_plays``, which
streams rows through ``COPY`` into a per-transaction staging table and then
moves them across with ``INSERT ... SELECT ... ON CONFLICT DO NOTHING``.

What is deliberately *not* covered here: Row-Level Security enforcement.
testcontainers connects as a superuser and the test schema comes from
``metadata.create_all()``, which never emits ``CREATE POLICY`` — so no policy
exists to enforce. What these tests do prove is the input to that policy: every
row lands stamped with its own tenant's ``user_id``, and user-scoped reads see
only their own. (RLS rejection of a cross-tenant row through this exact code
path was verified separately against a real policy and a non-superuser role.)
"""

from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import uuid7
import weakref

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from src.domain.entities import ConnectorTrackPlay
from src.infrastructure.persistence.database.db_models import DBConnectorPlay
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from tests.fixtures import make_track

USER_A: Final = "copy-tenant-a"
USER_B: Final = "copy-tenant-b"

_EPOCH: Final = datetime(2019, 3, 1, 12, 0, 0, tzinfo=UTC)

# 14 ledger columns × 4,682 rows exceeds PostgreSQL's 65535 bind parameters, so
# this many rows is unreachable for the multi-VALUES path COPY replaces.
_OVER_PARAMETER_CEILING: Final = 5_000


def _play(
    index: int, *, user_id: str = USER_A, batch: str = "COPY_BATCH"
) -> ConnectorTrackPlay:
    """A distinct ledger observation — ``played_at`` carries the identity."""
    return ConnectorTrackPlay(
        service="spotify",
        artist_name=f"TEST_CopyArtist {index}",
        track_name=f"TEST_CopyTrack {index}",
        album_name=f"TEST_CopyAlbum {index}",
        played_at=_EPOCH + timedelta(seconds=index * 37),
        ms_played=180_000 + index,
        service_metadata={"track_uri": f"spotify:track:TEST{index}"},
        user_id=user_id,
        import_timestamp=datetime.now(UTC),
        import_source="spotify_export",
        import_batch_id=batch,
    )


async def _stored(db_session, *, user_id: str) -> list[DBConnectorPlay]:
    stmt = (
        sa
        .select(DBConnectorPlay)
        .where(DBConnectorPlay.user_id == user_id)
        .order_by(DBConnectorPlay.played_at)
    )
    return list((await db_session.execute(stmt)).scalars().all())


class TestCopyBulkInsertHappyPath:
    """Distinct rows stream in and are reported as inserted."""

    async def test_distinct_plays_all_insert(self, db_session):
        repo = get_unit_of_work(db_session).get_connector_play_repository()

        result = await repo.bulk_insert_connector_plays([_play(i) for i in range(25)])

        assert result == (25, 0)
        assert len(await _stored(db_session, user_id=USER_A)) == 25

    async def test_entity_fields_round_trip_through_staging(self, db_session):
        """COPY + INSERT ... SELECT must not lose or mangle any column."""
        repo = get_unit_of_work(db_session).get_connector_play_repository()
        play = _play(7)

        _ = await repo.bulk_insert_connector_plays([play])

        (stored,) = await _stored(db_session, user_id=USER_A)
        # The entity's own id is persisted rather than a fresh column default —
        # first-import ledger rows stay correlatable with the in-memory entity.
        assert stored.id == play.id
        assert stored.connector_name == "spotify"
        assert stored.connector_track_identifier == "spotify:track:TEST7"
        assert stored.played_at == play.played_at
        assert stored.ms_played == play.ms_played
        assert stored.import_source == "spotify_export"
        assert stored.import_batch_id == "COPY_BATCH"
        assert stored.resolved_track_id is None
        assert stored.raw_metadata["artist_name"] == "TEST_CopyArtist 7"
        assert stored.raw_metadata["album_name"] == "TEST_CopyAlbum 7"
        assert stored.raw_metadata["service_metadata"] == {
            "track_uri": "spotify:track:TEST7"
        }
        assert stored.created_at is not None
        assert stored.updated_at is not None

    async def test_batch_beyond_multi_values_parameter_ceiling(self, db_session):
        """The regression this path exists for.

        A single multi-VALUES ``INSERT`` of 14-column rows dies at 4,681 rows
        with "number of parameters must be between 0 and 65535". COPY carries no
        parameter list, so batch size is bounded only by the wire.
        """
        repo = get_unit_of_work(db_session).get_connector_play_repository()

        inserted, duplicates = await repo.bulk_insert_connector_plays(
            _play(i) for i in range(_OVER_PARAMETER_CEILING)
        )

        assert (inserted, duplicates) == (_OVER_PARAMETER_CEILING, 0)


class TestCopyBulkInsertDeduplication:
    """``uq_connector_plays_deduplication`` decides what is new."""

    async def test_reinserting_the_same_rows_reports_all_as_duplicates(
        self, db_session
    ):
        repo = get_unit_of_work(db_session).get_connector_play_repository()
        plays = [_play(i) for i in range(10)]

        assert await repo.bulk_insert_connector_plays(plays) == (10, 0)
        # Fresh entities on the same natural key — what a re-import produces.
        assert await repo.bulk_insert_connector_plays([
            _play(i, batch="COPY_BATCH_2") for i in range(10)
        ]) == (0, 10)
        assert len(await _stored(db_session, user_id=USER_A)) == 10

    async def test_mixed_batch_splits_new_from_seen(self, db_session):
        repo = get_unit_of_work(db_session).get_connector_play_repository()

        _ = await repo.bulk_insert_connector_plays([_play(i) for i in range(6)])
        # Rows 3-5 overlap the stored set; 6-9 are new.
        inserted, duplicates = await repo.bulk_insert_connector_plays(
            _play(i, batch="COPY_BATCH_2") for i in range(3, 10)
        )

        assert (inserted, duplicates) == (4, 3)
        assert len(await _stored(db_session, user_id=USER_A)) == 10

    async def test_duplicates_within_one_batch_are_skipped_not_fatal(self, db_session):
        """``DO NOTHING`` tolerates a key repeated inside a single statement.

        ``DO UPDATE`` would raise a cardinality violation here, which is why the
        multi-VALUES path pre-deduplicates in Python. This one does not have to.
        """
        repo = get_unit_of_work(db_session).get_connector_play_repository()
        plays = [_play(i) for i in range(4)]

        inserted, duplicates = await repo.bulk_insert_connector_plays([*plays, *plays])

        assert (inserted, duplicates) == (4, 4)
        assert len(await _stored(db_session, user_id=USER_A)) == 4

    async def test_null_ms_played_rows_still_collide(self, db_session):
        """Last.fm scrobbles carry no duration; NULLS NOT DISTINCT must hold."""
        repo = get_unit_of_work(db_session).get_connector_play_repository()

        def scrobble(batch: str) -> ConnectorTrackPlay:
            return ConnectorTrackPlay(
                service="lastfm",
                artist_name="TEST_CopyNullMs",
                track_name="TEST_CopyNullMsTrack",
                played_at=_EPOCH,
                ms_played=None,
                user_id=USER_A,
                import_source="lastfm_api",
                import_batch_id=batch,
            )

        assert await repo.bulk_insert_connector_plays([scrobble("B1")]) == (1, 0)
        assert await repo.bulk_insert_connector_plays([scrobble("B2")]) == (0, 1)


class TestCopyBulkInsertStreaming:
    """The input is an ``Iterable``, consumed lazily and never sized."""

    async def test_generator_input_produces_correct_counts(self, db_session):
        """A generator has no ``len()`` — reaching the right totals proves the
        batch size is counted while streaming."""
        repo = get_unit_of_work(db_session).get_connector_play_repository()

        assert await repo.bulk_insert_connector_plays(_play(i) for i in range(12)) == (
            12,
            0,
        )
        assert await repo.bulk_insert_connector_plays(
            _play(i, batch="COPY_BATCH_2") for i in range(12)
        ) == (0, 12)

    async def test_generator_is_consumed_exactly_once(self, db_session):
        repo = get_unit_of_work(db_session).get_connector_play_repository()
        pulls = 0

        def source():
            nonlocal pulls
            for i in range(8):
                pulls += 1
                yield _play(i)

        assert await repo.bulk_insert_connector_plays(source()) == (8, 0)
        assert pulls == 8

    async def test_generator_input_is_not_materialised(self, db_session):
        """Entities must be released as they stream, not accumulated.

        Each yielded play is tracked by a weak reference. Under CPython
        refcounting a streamed entity dies as soon as the row tuple built from
        it is written, so the live count stays flat regardless of batch size —
        only the entity being produced and the one still bound in the consuming
        frame are reachable. A materialising implementation (``list(...)``, or
        anything that needs ``len()``) would hold all of them and the count
        would climb to the batch size.
        """
        repo = get_unit_of_work(db_session).get_connector_play_repository()
        batch_size = 60
        tracked: list[weakref.ref[ConnectorTrackPlay]] = []
        live_counts: list[int] = []

        def source():
            for i in range(batch_size):
                play = _play(i)
                tracked.append(weakref.ref(play))
                live_counts.append(sum(1 for ref in tracked if ref() is not None))
                yield play

        assert await repo.bulk_insert_connector_plays(source()) == (batch_size, 0)
        assert len(live_counts) == batch_size
        assert max(live_counts) <= 3, (
            f"expected a flat live-entity count while streaming, saw {max(live_counts)} "
            f"— the batch is being materialised"
        )


class TestCopyBulkInsertEdgeCases:
    """Degenerate inputs and repeated use inside one transaction."""

    async def test_empty_list_is_a_no_op(self, db_session):
        repo = get_unit_of_work(db_session).get_connector_play_repository()

        assert await repo.bulk_insert_connector_plays([]) == (0, 0)
        assert await _stored(db_session, user_id=USER_A) == []

    async def test_empty_generator_is_a_no_op(self, db_session):
        repo = get_unit_of_work(db_session).get_connector_play_repository()

        assert await repo.bulk_insert_connector_plays(p for p in ()) == (0, 0)

    async def test_repeated_calls_reuse_the_staging_name(self, db_session):
        """Staging is ``ON COMMIT DROP``, which never fires mid-transaction —
        a second call must not trip over its own leftover table."""
        repo = get_unit_of_work(db_session).get_connector_play_repository()

        for offset in range(4):
            inserted, duplicates = await repo.bulk_insert_connector_plays([
                _play(offset * 10 + i) for i in range(3)
            ])
            assert (inserted, duplicates) == (3, 0)

        assert len(await _stored(db_session, user_id=USER_A)) == 12

    async def test_empty_batch_between_populated_ones(self, db_session):
        repo = get_unit_of_work(db_session).get_connector_play_repository()

        assert await repo.bulk_insert_connector_plays([_play(1)]) == (1, 0)
        assert await repo.bulk_insert_connector_plays([]) == (0, 0)
        assert await repo.bulk_insert_connector_plays([_play(2)]) == (1, 0)

    async def test_failed_batch_leaves_the_transaction_usable(self, db_session):
        """The savepoint contains the damage — including the staging table.

        A ``resolved_track_id`` pointing at no track violates the ledger's FK on
        the ``INSERT ... SELECT``. Without the savepoint that aborts the caller's
        entire import transaction; with it, the next batch just works.
        """
        repo = get_unit_of_work(db_session).get_connector_play_repository()
        dangling = ConnectorTrackPlay(
            service="spotify",
            artist_name="TEST_CopyDangling",
            track_name="TEST_CopyDanglingTrack",
            played_at=_EPOCH,
            ms_played=1_000,
            user_id=USER_A,
            resolved_track_id=uuid7(),
        )

        with pytest.raises(IntegrityError):
            _ = await repo.bulk_insert_connector_plays([dangling])

        assert await repo.bulk_insert_connector_plays([_play(1)]) == (1, 0)
        assert len(await _stored(db_session, user_id=USER_A)) == 1


class TestCopyBulkInsertTenancy:
    """Every streamed row keeps the tenant stamp its entity carried."""

    async def test_rows_land_under_their_own_user_id(self, db_session):
        repo = get_unit_of_work(db_session).get_connector_play_repository()

        _ = await repo.bulk_insert_connector_plays([
            _play(i, user_id=USER_A) for i in range(5)
        ])
        _ = await repo.bulk_insert_connector_plays([
            _play(i, user_id=USER_B) for i in range(3)
        ])

        rows_a = await _stored(db_session, user_id=USER_A)
        rows_b = await _stored(db_session, user_id=USER_B)
        assert len(rows_a) == 5
        assert len(rows_b) == 3
        assert {row.user_id for row in rows_a} == {USER_A}
        assert {row.user_id for row in rows_b} == {USER_B}

    async def test_mixed_tenant_batch_stamps_each_row_individually(self, db_session):
        repo = get_unit_of_work(db_session).get_connector_play_repository()

        _ = await repo.bulk_insert_connector_plays([
            _play(0, user_id=USER_A),
            _play(1, user_id=USER_B),
            _play(2, user_id=USER_A),
        ])

        assert len(await _stored(db_session, user_id=USER_A)) == 2
        assert len(await _stored(db_session, user_id=USER_B)) == 1

    async def test_user_id_participates_in_the_dedup_key(self, db_session):
        """The same observation under two tenants is two rows, not a conflict."""
        repo = get_unit_of_work(db_session).get_connector_play_repository()

        assert await repo.bulk_insert_connector_plays([_play(0, user_id=USER_A)]) == (
            1,
            0,
        )
        assert await repo.bulk_insert_connector_plays([_play(0, user_id=USER_B)]) == (
            1,
            0,
        )
        assert len(await _stored(db_session, user_id=USER_A)) == 1
        assert len(await _stored(db_session, user_id=USER_B)) == 1

    async def test_user_scoped_reads_isolate_copy_written_rows(self, db_session):
        """Downstream repository reads treat COPY-written rows like any other."""
        uow = get_unit_of_work(db_session)
        repo = uow.get_connector_play_repository()
        track = await uow.get_track_repository().save_track(
            make_track(
                title="TEST_CopyResolved",
                artist="TEST_CopyResolvedArtist",
                connector_track_identifiers={},
                user_id=USER_A,
            )
        )

        plays_a = [_play(i, user_id=USER_A) for i in range(4)]
        _ = await repo.bulk_insert_connector_plays(plays_a)
        _ = await repo.bulk_insert_connector_plays([_play(0, user_id=USER_B)])

        resolved_at = datetime.now(UTC)
        updated = await repo.bulk_update_resolution(
            [(play, track.id) for play in plays_a], resolved_at=resolved_at
        )
        assert updated == 4

        window = (_EPOCH, _EPOCH + timedelta(days=1))
        assert len(await repo.find_resolved_in_window(*window, user_id=USER_A)) == 4
        assert await repo.find_resolved_in_window(*window, user_id=USER_B) == []
        assert await repo.get_resolved_played_at_days(user_id=USER_B) == []
