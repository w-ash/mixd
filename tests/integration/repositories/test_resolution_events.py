"""The resolution ledger and negative cache, against real PostgreSQL.

What these cover is the user-visible promise of v0.10.2: the same wrong match
stops being proposed on every import, an id that flickers is not mistaken for a
dead one, and every identity decision leaves an answer to "why does my library
believe this". None of it can be checked against a mock — the expiry rules live
in partial unique indexes, a ``clock_timestamp()`` column default, and SQL
interval arithmetic over the backoff row, none of which anyone could stub.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.track import Artist, Track
from src.domain.matching.content_digest import DigestSide
from src.domain.repositories.resolution import (
    RejectedPairRow,
    RejectionCandidate,
    ResolutionDecision,
)
from src.domain.services.resolution_retry import (
    DEATH_DEBOUNCE_MIN_SPAN_SECONDS,
)
from src.infrastructure.persistence.database.db_models import (
    DBConnectorTrack,
    DBResolutionEvent,
    DBResolutionNegative,
    DBTrack,
    DBTrackMapping,
)
from src.infrastructure.persistence.database.live_rows import INCLUDE_SUPERSEDED
from src.infrastructure.persistence.repositories.resolution import (
    ResolutionEventRepository,
    ResolutionNegativeRepository,
)
from src.infrastructure.persistence.repositories.track.connector import (
    TrackConnectorRepository,
    TrackMappingRepository,
)
from src.infrastructure.services.resolution_recorder import ResolutionRecorder

_USER = "default"
_OTHER_USER = "resolution-other-user"
_DAY = 86_400


@pytest.fixture
def events(db_session: AsyncSession) -> ResolutionEventRepository:
    return ResolutionEventRepository(db_session)


@pytest.fixture
def negatives(db_session: AsyncSession) -> ResolutionNegativeRepository:
    return ResolutionNegativeRepository(db_session)


@pytest.fixture
def recorder(db_session: AsyncSession) -> ResolutionRecorder:
    return ResolutionRecorder(db_session)


async def _make_track(db_session: AsyncSession, title: str = "Track") -> UUID:
    uid = str(uuid4())[:8]
    row = DBTrack(title=f"{title} {uid}", artists={"names": [f"Artist {uid}"]})
    db_session.add(row)
    await db_session.flush()
    return row.id


async def _make_connector_track(
    db_session: AsyncSession, identifier: str | None = None
) -> tuple[UUID, str]:
    uid = identifier or f"sp_{uuid4().hex[:8]}"
    row = DBConnectorTrack(
        connector_name="spotify",
        connector_track_identifier=uid,
        title=f"CT {uid}",
        artists={"names": ["Someone"]},
        raw_metadata={},
        last_updated=datetime.now(UTC),
    )
    db_session.add(row)
    await db_session.flush()
    return row.id, uid


async def _make_live_mapping(
    db_session: AsyncSession, track_id: UUID, ct_id: UUID, *, user_id: str = _USER
) -> UUID:
    outcome = await TrackMappingRepository(db_session).assert_mappings([
        {
            "user_id": user_id,
            "track_id": track_id,
            "connector_track_id": ct_id,
            "connector_name": "spotify",
            "match_method": "direct",
            "confidence": 100,
        }
    ])
    return outcome.created[0]


def _domain_track(track_id: UUID, title: str = "Candidate") -> Track:
    return Track(id=track_id, title=title, artists=[Artist(name="Someone")])


def _candidate(
    identifier: str, track: Track, *, title: str = "Candidate"
) -> RejectionCandidate:
    return RejectionCandidate(
        connector=DigestSide(
            identifier=identifier, title=title, artists=("Someone",), duration_ms=1000
        ),
        candidate_track=track,
    )


class TestEventLogTemporality:
    """Three instants, three questions — and only one of them is the writer's."""

    async def test_recorded_at_comes_from_the_database_not_the_writer(
        self, db_session: AsyncSession, recorder: ResolutionRecorder
    ):
        track_id = await _make_track(db_session)

        await recorder.record(
            [
                ResolutionDecision(
                    event_type="accepted", connector_name="spotify", track_id=track_id
                )
            ],
            user_id=_USER,
        )

        row = (
            await db_session.execute(
                select(DBResolutionEvent).where(DBResolutionEvent.track_id == track_id)
            )
        ).scalar_one()
        assert row.recorded_at is not None
        # The seam stamps the matcher's clock; the DB stamps its own. Online
        # they agree closely — the split only diverges on a backfill.
        assert row.decided_at is not None
        assert abs((row.recorded_at - row.decided_at).total_seconds()) < 60

    async def test_events_in_one_transaction_are_still_ordered(
        self, db_session: AsyncSession, recorder: ResolutionRecorder
    ):
        """``clock_timestamp()``, not ``now()`` — the latter ties inside a
        transaction, and every consumer of the log's order depends on it not
        tying. "What did mixd believe, and in what order" is the whole point of
        an append-only ledger; a batch of decisions written in one transaction
        that all share an instant cannot answer it.
        """
        track_id = await _make_track(db_session)

        for event_type in ("accepted", "queued"):
            await recorder.record(
                [
                    ResolutionDecision(
                        event_type=event_type,
                        connector_name="spotify",
                        track_id=track_id,
                    )
                ],
                user_id=_USER,
            )

        rows = (
            (
                await db_session.execute(
                    select(DBResolutionEvent)
                    .where(DBResolutionEvent.track_id == track_id)
                    .order_by(DBResolutionEvent.recorded_at)
                )
            )
            .scalars()
            .all()
        )
        assert [row.event_type for row in rows] == ["accepted", "queued"]
        assert rows[0].recorded_at < rows[1].recorded_at

    async def test_every_event_carries_the_matcher_that_made_it(
        self, db_session: AsyncSession, recorder: ResolutionRecorder
    ):
        track_id = await _make_track(db_session)

        await recorder.record(
            [
                ResolutionDecision(
                    event_type="accepted", connector_name="spotify", track_id=track_id
                )
            ],
            user_id=_USER,
        )

        row = (
            await db_session.execute(
                select(DBResolutionEvent).where(DBResolutionEvent.track_id == track_id)
            )
        ).scalar_one()
        assert row.matcher_version == recorder.matcher_version
        assert row.matcher_version != ""

    async def test_events_for_mapping_answers_why_we_believe_this(
        self,
        db_session: AsyncSession,
        events: ResolutionEventRepository,
        recorder: ResolutionRecorder,
    ):
        track_id = await _make_track(db_session)
        ct_id, _ = await _make_connector_track(db_session)
        mapping_id = await _make_live_mapping(db_session, track_id, ct_id)

        await recorder.record(
            [
                ResolutionDecision(
                    event_type="accepted",
                    connector_name="spotify",
                    track_id=track_id,
                    connector_track_id=ct_id,
                    resulting_mapping_id=mapping_id,
                    confidence=95,
                )
            ],
            user_id=_USER,
        )

        found = await events.events_for_mapping(mapping_id, user_id=_USER)
        assert [event.event_type for event in found] == ["accepted"]
        assert found[0].confidence == 95


class TestRejectedPairsAreNotReProposed:
    """The user's core complaint: the same wrong match, every single import."""

    async def test_a_recorded_rejection_suppresses_the_pair(
        self, db_session: AsyncSession, recorder: ResolutionRecorder
    ):
        track_id = await _make_track(db_session)
        _, identifier = await _make_connector_track(db_session)
        track = _domain_track(track_id)
        candidate = _candidate(identifier, track)

        await recorder.remember_rejections(
            [candidate], user_id=_USER, connector_name="spotify"
        )
        active = await recorder.active_rejections(
            [candidate], user_id=_USER, connector_name="spotify"
        )

        assert active == frozenset({(identifier, track_id)})

    async def test_a_matcher_version_bump_re_proposes_it(
        self, db_session: AsyncSession, recorder: ResolutionRecorder
    ):
        """A new matcher never saw this pair; its old verdict says nothing.

        The bump is simulated on the *stored* row rather than on the recorder:
        ``matcher_version`` is a read-only property derived from the matching
        code and config, and a test able to assign it would be asserting a
        capability production code does not have.
        """
        track_id = await _make_track(db_session)
        ct_id, identifier = await _make_connector_track(db_session)
        candidate = _candidate(identifier, _domain_track(track_id))

        await recorder.remember_rejections(
            [candidate], user_id=_USER, connector_name="spotify"
        )
        _ = await db_session.execute(
            update(DBResolutionNegative)
            .where(DBResolutionNegative.connector_track_id == ct_id)
            .values(matcher_version="matcher-from-a-previous-release")
        )

        active = await recorder.active_rejections(
            [candidate], user_id=_USER, connector_name="spotify"
        )
        assert active == frozenset()

    async def test_editing_the_candidate_track_re_proposes_it(
        self, db_session: AsyncSession, recorder: ResolutionRecorder
    ):
        """The rejection was about *that* data; the data has changed."""
        track_id = await _make_track(db_session)
        _, identifier = await _make_connector_track(db_session)
        track = _domain_track(track_id)

        await recorder.remember_rejections(
            [_candidate(identifier, track)], user_id=_USER, connector_name="spotify"
        )

        edited = _candidate(
            identifier, _domain_track(track_id, title="Candidate (Remaster)")
        )
        active = await recorder.active_rejections(
            [edited], user_id=_USER, connector_name="spotify"
        )
        assert active == frozenset()

    async def test_editing_the_connector_track_re_proposes_it(
        self, db_session: AsyncSession, recorder: ResolutionRecorder
    ):
        """The connector side of record is the persisted row, not the caller's view.

        Both rejection producers describe the same connector track differently
        — the matching pipeline holds a full provider payload, the reuse gate
        holds a title and an artist off a play record and no duration — so the
        digest keys on the stored row instead. A provider refresh updating that
        row is exactly the signal that the metadata the rejection was computed
        from has moved.
        """
        track_id = await _make_track(db_session)
        ct_id, identifier = await _make_connector_track(db_session)
        candidate = _candidate(identifier, _domain_track(track_id))

        await recorder.remember_rejections(
            [candidate], user_id=_USER, connector_name="spotify"
        )
        _ = await db_session.execute(
            update(DBConnectorTrack)
            .where(DBConnectorTrack.id == ct_id)
            .values(title="Corrected Title")
        )

        active = await recorder.active_rejections(
            [candidate], user_id=_USER, connector_name="spotify"
        )
        assert active == frozenset()

    async def test_un_rejecting_withdraws_the_constraint(
        self,
        db_session: AsyncSession,
        recorder: ResolutionRecorder,
        negatives: ResolutionNegativeRepository,
    ):
        track_id = await _make_track(db_session)
        ct_id, identifier = await _make_connector_track(db_session)
        candidate = _candidate(identifier, _domain_track(track_id))

        await recorder.remember_rejections(
            [candidate], user_id=_USER, connector_name="spotify"
        )
        withdrawn = await negatives.unreject(
            user_id=_USER, connector_track_id=ct_id, candidate_track_id=track_id
        )

        assert withdrawn is True
        active = await recorder.active_rejections(
            [candidate], user_id=_USER, connector_name="spotify"
        )
        assert active == frozenset()

    async def test_un_rejecting_twice_reports_nothing_left_to_withdraw(
        self,
        db_session: AsyncSession,
        recorder: ResolutionRecorder,
        negatives: ResolutionNegativeRepository,
    ):
        track_id = await _make_track(db_session)
        ct_id, identifier = await _make_connector_track(db_session)
        await recorder.remember_rejections(
            [_candidate(identifier, _domain_track(track_id))],
            user_id=_USER,
            connector_name="spotify",
        )

        await negatives.unreject(
            user_id=_USER, connector_track_id=ct_id, candidate_track_id=track_id
        )
        again = await negatives.unreject(
            user_id=_USER, connector_track_id=ct_id, candidate_track_id=track_id
        )
        assert again is False

    async def test_rejecting_a_candidate_materializes_its_connector_track(
        self, db_session: AsyncSession, recorder: ResolutionRecorder
    ):
        """Only accepted matches were ever persisted; a rejection needs a key."""
        track_id = await _make_track(db_session)
        identifier = f"sp_new_{uuid4().hex[:8]}"

        await recorder.remember_rejections(
            [_candidate(identifier, _domain_track(track_id))],
            user_id=_USER,
            connector_name="spotify",
        )

        created = await db_session.scalar(
            select(DBConnectorTrack.id).where(
                DBConnectorTrack.connector_track_identifier == identifier
            )
        )
        assert created is not None


class TestNoMatchBackoff:
    """Absence backs off: nothing changed, so looking again buys only quota."""

    @staticmethod
    def _days_until(check_again: datetime | None) -> float:
        assert check_again is not None
        return (check_again - datetime.now(UTC)).total_seconds() / _DAY

    @staticmethod
    async def _miss(
        db_session: AsyncSession,
        negatives: ResolutionNegativeRepository,
        ct_id: UUID,
        *,
        user_id: str = _USER,
    ) -> DBResolutionNegative:
        """Record one miss for one id, then read back the row it wrote.

        The upsert is batch-shaped and returns a count, so these assertions go
        to the row — which is the more honest check anyway: the counter and the
        clock are both computed in SQL, so what matters is what the statement
        left behind, not what Python believed it sent.
        """
        _ = await negatives.upsert_no_match(
            user_id=user_id,
            connector_name="spotify",
            connector_track_ids=[ct_id],
            matcher_version="v1",
        )
        return (
            await db_session.execute(
                select(DBResolutionNegative)
                .where(
                    DBResolutionNegative.user_id == user_id,
                    DBResolutionNegative.connector_track_id == ct_id,
                    DBResolutionNegative.kind == "no_match",
                )
                .execution_options(populate_existing=True)
            )
        ).scalar_one()

    async def test_one_statement_backs_off_a_whole_batch(
        self, db_session: AsyncSession, negatives: ResolutionNegativeRepository
    ):
        """An import with 300 absent ids should not issue 300 round-trips.

        Each row still gets its own jittered interval — a batch that missed
        together must not all come due in the same second — so the batching is
        in the statement, not in the schedule.
        """
        ct_ids = [(await _make_connector_track(db_session))[0] for _ in range(4)]

        written = await negatives.upsert_no_match(
            user_id=_USER,
            connector_name="spotify",
            connector_track_ids=ct_ids,
            matcher_version="v1",
        )

        assert written == 4
        rows = (
            (
                await db_session.execute(
                    select(DBResolutionNegative).where(
                        DBResolutionNegative.connector_track_id.in_(ct_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        assert {row.connector_track_id for row in rows} == set(ct_ids)
        assert all(row.consecutive_misses == 1 for row in rows)
        assert len({row.check_again for row in rows}) > 1, (
            "each id carries its own jitter, so the batch does not come due together"
        )

    async def test_a_repeated_id_in_one_batch_is_collapsed(
        self, db_session: AsyncSession, negatives: ResolutionNegativeRepository
    ):
        """Two copies of one id in a single statement is a cardinality error."""
        ct_id, _ = await _make_connector_track(db_session)

        written = await negatives.upsert_no_match(
            user_id=_USER,
            connector_name="spotify",
            connector_track_ids=[ct_id, ct_id, ct_id],
            matcher_version="v1",
        )

        assert written == 1

    async def test_first_miss_schedules_one_day(
        self, db_session: AsyncSession, negatives: ResolutionNegativeRepository
    ):
        ct_id, _ = await _make_connector_track(db_session)
        row = await self._miss(db_session, negatives, ct_id)
        assert row.consecutive_misses == 1
        assert 0.8 < self._days_until(row.check_again) < 1.2

    async def test_second_miss_doubles_it(
        self, db_session: AsyncSession, negatives: ResolutionNegativeRepository
    ):
        ct_id, _ = await _make_connector_track(db_session)
        for _ in range(2):
            row = await self._miss(db_session, negatives, ct_id)
        assert row.consecutive_misses == 2
        assert 1.7 < self._days_until(row.check_again) < 2.3

    async def test_the_curve_caps_at_thirty_two_days(
        self, db_session: AsyncSession, negatives: ResolutionNegativeRepository
    ):
        """Six misses would be 32 days; sixty would still be 32 days."""
        ct_id, _ = await _make_connector_track(db_session)
        for _ in range(8):
            row = await self._miss(db_session, negatives, ct_id)
        assert 28 < self._days_until(row.check_again) < 36

    async def test_the_whole_curve_doubles_in_sql(
        self, db_session: AsyncSession, negatives: ResolutionNegativeRepository
    ):
        """1d → 2d → 4d → 8d → 16d → 32d → 32d, with the counter in step.

        The doubling moved into the upsert statement itself (no read-modify-
        write, so two importers cannot lose a miss between them), which means
        each interval is derived from the row's own previous span rather than
        recomputed from a counter. This walks the whole curve to prove the two
        formulations agree.
        """
        ct_id, _ = await _make_connector_track(db_session)
        expected = [1, 2, 4, 8, 16, 32, 32]

        for miss, days in enumerate(expected, start=1):
            row = await self._miss(db_session, negatives, ct_id)
            assert row.consecutive_misses == miss
            # ±10% deterministic jitter on the base interval, inherited by
            # every doubling; the cap is exact.
            assert days * 0.85 < self._days_until(row.check_again) < days * 1.15

    async def test_an_id_inside_its_window_is_not_re_requested(
        self, db_session: AsyncSession, recorder: ResolutionRecorder
    ):
        """The reader for ``check_again``: without it the clock was decoration."""
        ct_id, identifier = await _make_connector_track(db_session)
        await recorder.remember_no_match(
            [DigestSide(identifier=identifier)],
            user_id=_USER,
            connector_name="spotify",
        )

        assert await recorder.backoff_suppressed(
            [identifier], user_id=_USER, connector_name="spotify"
        ) == frozenset({identifier})

        # Wind the clock past the window — the id becomes askable again.
        _ = await db_session.execute(
            update(DBResolutionNegative)
            .where(DBResolutionNegative.connector_track_id == ct_id)
            .values(check_again=datetime.now(UTC) - timedelta(minutes=1))
        )
        assert (
            await recorder.backoff_suppressed(
                [identifier], user_id=_USER, connector_name="spotify"
            )
            == frozenset()
        )

    async def test_an_id_with_no_backoff_row_is_never_suppressed(
        self, db_session: AsyncSession, recorder: ResolutionRecorder
    ):
        """The bias is toward asking: an unknown id is not a remembered miss."""
        _, identifier = await _make_connector_track(db_session)
        assert (
            await recorder.backoff_suppressed(
                [identifier], user_id=_USER, connector_name="spotify"
            )
            == frozenset()
        )

    async def test_a_success_clears_the_clock_entirely(
        self, db_session: AsyncSession, recorder: ResolutionRecorder
    ):
        ct_id, identifier = await _make_connector_track(db_session)
        await recorder.remember_no_match(
            [DigestSide(identifier=identifier)],
            user_id=_USER,
            connector_name="spotify",
        )

        cleared = await recorder.clear_negatives(
            [identifier], user_id=_USER, connector_name="spotify"
        )

        assert cleared == 1
        remaining = await db_session.scalar(
            select(DBResolutionNegative.id).where(
                DBResolutionNegative.connector_track_id == ct_id,
                DBResolutionNegative.kind == "no_match",
            )
        )
        assert remaining is None

    async def test_a_success_leaves_rejections_alone(
        self, db_session: AsyncSession, recorder: ResolutionRecorder
    ):
        """The two mechanisms expire on different signals and must not share one."""
        track_id = await _make_track(db_session)
        _, identifier = await _make_connector_track(db_session)
        candidate = _candidate(identifier, _domain_track(track_id))
        await recorder.remember_rejections(
            [candidate], user_id=_USER, connector_name="spotify"
        )

        await recorder.clear_negatives(
            [identifier], user_id=_USER, connector_name="spotify"
        )

        active = await recorder.active_rejections(
            [candidate], user_id=_USER, connector_name="spotify"
        )
        assert active == frozenset({(identifier, track_id)})


class TestRetirementReplacesDeletion:
    """Unlinking is a correction the ledger has to keep, not an erasure."""

    async def test_delete_mapping_retires_rather_than_deletes(
        self, db_session: AsyncSession
    ):
        track_id = await _make_track(db_session)
        ct_id, _ = await _make_connector_track(db_session)
        mapping_id = await _make_live_mapping(db_session, track_id, ct_id)

        returned = await TrackConnectorRepository(db_session).delete_mapping(
            mapping_id, user_id=_USER
        )

        assert returned.id == mapping_id
        row = (
            await db_session.execute(
                select(DBTrackMapping)
                .where(DBTrackMapping.id == mapping_id)
                .execution_options(**{INCLUDE_SUPERSEDED: True}, populate_existing=True)
            )
        ).scalar_one()
        assert row.superseded_at is not None
        assert row.supersession_reason == "manual"
        assert row.superseded_by_id is None

    async def test_a_retired_mapping_reads_as_absent(self, db_session: AsyncSession):
        track_id = await _make_track(db_session)
        ct_id, _ = await _make_connector_track(db_session)
        mapping_id = await _make_live_mapping(db_session, track_id, ct_id)
        repo = TrackConnectorRepository(db_session)

        await repo.delete_mapping(mapping_id, user_id=_USER)

        assert await repo.get_mapping_by_id(mapping_id, user_id=_USER) is None
        assert await repo.count_mappings_for_connector_track(ct_id) == 0

    async def test_relink_supersedes_instead_of_flipping_in_place(
        self, db_session: AsyncSession
    ):
        old_track = await _make_track(db_session, "Old")
        new_track = await _make_track(db_session, "New")
        ct_id, _ = await _make_connector_track(db_session)
        mapping_id = await _make_live_mapping(db_session, old_track, ct_id)
        repo = TrackConnectorRepository(db_session)

        successor = await repo.update_mapping_track(
            mapping_id, new_track, "manual_override", user_id=_USER
        )

        assert successor.track_id == new_track
        assert successor.origin == "manual_override"
        chain = await repo.get_supersession_chain(mapping_id, user_id=_USER)
        assert [link.track_id for link in chain] == [old_track, new_track]
        assert chain[0].supersession_reason == "manual"

    async def test_relink_supersession_event_carries_manual_reason(
        self, db_session: AsyncSession, events: ResolutionEventRepository
    ):
        """The event has to agree with the column the test above checks.

        Emission used to take the recorder's default, so every supersession
        event read ``rematch`` — including a relink whose row said ``manual``.
        A log that contradicts the data it exists to explain is worse than no
        log: "why does my library believe this" gets a confident wrong answer.
        """
        old_track = await _make_track(db_session, "Old")
        new_track = await _make_track(db_session, "New")
        ct_id, _ = await _make_connector_track(db_session)
        mapping_id = await _make_live_mapping(db_session, old_track, ct_id)

        successor = await TrackConnectorRepository(db_session).update_mapping_track(
            mapping_id, new_track, "manual_override", user_id=_USER
        )

        recorded = await events.events_for_mapping(successor.id, user_id=_USER)
        superseded = [event for event in recorded if event.event_type == "superseded"]
        assert len(superseded) == 1
        assert superseded[0].payload["superseded_mapping_id"] == str(mapping_id)
        assert superseded[0].payload["reason"] == "manual"


class TestTenantIsolation:
    """One user's identity decisions are invisible to another."""

    async def test_events_are_scoped_to_their_user(
        self, db_session: AsyncSession, events: ResolutionEventRepository
    ):
        track_id = await _make_track(db_session)
        ct_id, _ = await _make_connector_track(db_session)
        mapping_id = await _make_live_mapping(db_session, track_id, ct_id)
        recorder = ResolutionRecorder(db_session)

        await recorder.record(
            [
                ResolutionDecision(
                    event_type="accepted",
                    connector_name="spotify",
                    track_id=track_id,
                    resulting_mapping_id=mapping_id,
                )
            ],
            user_id=_USER,
        )

        assert await events.events_for_mapping(mapping_id, user_id=_USER)
        assert await events.events_for_mapping(mapping_id, user_id=_OTHER_USER) == []

    async def test_rejections_are_scoped_to_their_user(
        self, db_session: AsyncSession, negatives: ResolutionNegativeRepository
    ):
        track_id = await _make_track(db_session)
        ct_id, _ = await _make_connector_track(db_session)
        await negatives.record_rejected_pairs(
            [
                RejectedPairRow(
                    connector_track_id=ct_id,
                    candidate_track_id=track_id,
                    connector_name="spotify",
                    content_digest="abc",
                )
            ],
            user_id=_USER,
            matcher_version="v1",
        )

        mine = await negatives.active_rejected_pairs(
            user_id=_USER,
            connector_track_ids=[ct_id],
            matcher_version="v1",
        )
        theirs = await negatives.active_rejected_pairs(
            user_id=_OTHER_USER,
            connector_track_ids=[ct_id],
            matcher_version="v1",
        )

        assert mine == {(ct_id, track_id)}
        assert theirs == set()

    async def test_backoff_state_is_scoped_to_its_user(
        self, db_session: AsyncSession, negatives: ResolutionNegativeRepository
    ):
        """Both users may hold their own no-match row for the same shared id."""
        ct_id, _ = await _make_connector_track(db_session)

        for owner in (_USER, _OTHER_USER):
            assert (
                await negatives.upsert_no_match(
                    user_id=owner,
                    connector_name="spotify",
                    connector_track_ids=[ct_id],
                    matcher_version="v1",
                )
                == 1
            ), "each owner gets their own row for the same shared connector track"

        cleared = await negatives.clear_no_match(
            user_id=_OTHER_USER, connector_name="spotify", connector_track_ids=[ct_id]
        )
        assert cleared == 1
        still_mine = await db_session.scalar(
            select(DBResolutionNegative.id).where(
                DBResolutionNegative.connector_track_id == ct_id,
                DBResolutionNegative.user_id == _USER,
            )
        )
        assert still_mine is not None


class TestNegativeCacheWriteMechanics:
    """The upsert has to *be* an upsert — a fallback that works is still a bug."""

    async def test_rejections_upsert_in_one_statement_without_falling_back(
        self,
        db_session: AsyncSession,
        negatives: ResolutionNegativeRepository,
        caplog: pytest.LogCaptureFixture,
    ):
        """``uq_resolution_negatives_pair`` is partial, so ON CONFLICT needs its
        predicate to infer an arbiter. Without it every re-assertion raised
        23505 and ``bulk_upsert`` quietly degraded to one statement per row —
        correct output, N round trips, and a warning nobody was reading.
        """
        track_id = await _make_track(db_session)
        ct_id, _ = await _make_connector_track(db_session)
        pairs = [
            RejectedPairRow(
                connector_track_id=ct_id,
                candidate_track_id=track_id,
                connector_name="spotify",
                content_digest="first",
            )
        ]

        await negatives.record_rejected_pairs(
            pairs, user_id=_USER, matcher_version="v1"
        )
        with caplog.at_level("WARNING"):
            written = await negatives.record_rejected_pairs(
                [
                    RejectedPairRow(
                        connector_track_id=ct_id,
                        candidate_track_id=track_id,
                        connector_name="spotify",
                        content_digest="second",
                    )
                ],
                user_id=_USER,
                matcher_version="v1",
            )

        assert written == 1
        assert "Bulk upsert failed" not in caplog.text
        digest = await db_session.scalar(
            select(DBResolutionNegative.content_digest).where(
                DBResolutionNegative.connector_track_id == ct_id,
                DBResolutionNegative.kind == "rejected_pair",
            )
        )
        assert digest == "second"

    async def test_a_no_match_row_may_not_name_a_candidate(
        self, db_session: AsyncSession
    ):
        """The CHECK behind the two partial key spaces.

        A ``no_match`` row with a candidate would sit in neither unique index
        and duplicate without limit, which is the pathology the split exists to
        prevent — so the split is a constraint, not a convention.
        """
        track_id = await _make_track(db_session)
        ct_id, _ = await _make_connector_track(db_session)
        db_session.add(
            DBResolutionNegative(
                user_id=_USER,
                kind="no_match",
                connector_name="spotify",
                connector_track_id=ct_id,
                candidate_track_id=track_id,
                matcher_version="v1",
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_a_rejected_pair_must_name_a_candidate(
        self, db_session: AsyncSession
    ):
        ct_id, _ = await _make_connector_track(db_session)
        db_session.add(
            DBResolutionNegative(
                user_id=_USER,
                kind="rejected_pair",
                connector_name="spotify",
                connector_track_id=ct_id,
                candidate_track_id=None,
                matcher_version="v1",
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()


class TestNegativeCacheSizeIsMonitored:
    """A store with no clock-based expiry only grows; watch it grow."""

    async def test_counts_active_rejections_and_pending_backoffs(
        self,
        db_session: AsyncSession,
        recorder: ResolutionRecorder,
        negatives: ResolutionNegativeRepository,
    ):
        track_id = await _make_track(db_session)
        _, rejected_identifier = await _make_connector_track(db_session)
        _, missing_identifier = await _make_connector_track(db_session)

        await recorder.remember_rejections(
            [_candidate(rejected_identifier, _domain_track(track_id))],
            user_id=_USER,
            connector_name="spotify",
        )
        await recorder.remember_no_match(
            [DigestSide(identifier=missing_identifier)],
            user_id=_USER,
            connector_name="spotify",
        )

        size = await negatives.count_active_negatives(user_id=_USER)
        assert size.rejected_pairs_active == 1
        assert size.no_match_pending == 1

    async def test_a_withdrawn_rejection_and_an_expired_clock_stop_counting(
        self,
        db_session: AsyncSession,
        recorder: ResolutionRecorder,
        negatives: ResolutionNegativeRepository,
    ):
        """ "Active" means currently suppressing something, not merely stored."""
        track_id = await _make_track(db_session)
        rejected_ct, rejected_identifier = await _make_connector_track(db_session)
        missing_ct, missing_identifier = await _make_connector_track(db_session)
        await recorder.remember_rejections(
            [_candidate(rejected_identifier, _domain_track(track_id))],
            user_id=_USER,
            connector_name="spotify",
        )
        await recorder.remember_no_match(
            [DigestSide(identifier=missing_identifier)],
            user_id=_USER,
            connector_name="spotify",
        )

        await negatives.unreject(
            user_id=_USER,
            connector_track_id=rejected_ct,
            candidate_track_id=track_id,
        )
        _ = await db_session.execute(
            update(DBResolutionNegative)
            .where(DBResolutionNegative.connector_track_id == missing_ct)
            .values(check_again=datetime.now(UTC) - timedelta(days=1))
        )

        size = await negatives.count_active_negatives(user_id=_USER)
        assert size.rejected_pairs_active == 0
        assert size.no_match_pending == 0


class TestIdsThatLookDead:
    """Death is derived from the backoff row, not from a second counter.

    The row the miss already wrote records how many times in a row an id has
    failed and when the run began — which is the whole debounce. Scanning the
    event log for a ``suspect`` streak was a second count of the same failures,
    written in the same call, that only one connector ever fed.

    It is reported, never acted on. Spotify relinks and MusicBrainz merges
    rather than deleting, so this number sitting near zero is the expected
    shape; a number that climbs is a question for a person.
    """

    @staticmethod
    async def _age_the_row(
        db_session: AsyncSession,
        ct_id: UUID,
        *,
        misses: int,
        days_old: float,
        span_days: float | None = None,
    ) -> None:
        """Backdate the run of misses.

        Both ends move, because the span the debounce reads is
        ``last_checked_at - created_at`` and moving only one end cannot tell a
        run of failures spread over weeks from a burst that happened weeks ago
        and stopped. ``span_days`` defaults to the row's full age (a run still
        going as of now); pass a smaller value for a burst that went quiet.
        """
        started = datetime.now(UTC) - timedelta(days=days_old)
        last = started + timedelta(days=days_old if span_days is None else span_days)
        _ = await db_session.execute(
            update(DBResolutionNegative)
            .where(DBResolutionNegative.connector_track_id == ct_id)
            .values(
                consecutive_misses=misses,
                created_at=started,
                last_checked_at=last,
            )
        )

    async def _missed_id(
        self,
        db_session: AsyncSession,
        recorder: ResolutionRecorder,
        *,
        misses: int,
        days_old: float,
        span_days: float | None = None,
    ) -> UUID:
        ct_id, identifier = await _make_connector_track(db_session)
        await recorder.remember_no_match(
            [DigestSide(identifier=identifier)],
            user_id=_USER,
            connector_name="spotify",
        )
        await self._age_the_row(
            db_session,
            ct_id,
            misses=misses,
            days_old=days_old,
            span_days=span_days,
        )
        return ct_id

    async def test_enough_misses_over_enough_time_looks_dead(
        self,
        db_session: AsyncSession,
        recorder: ResolutionRecorder,
        negatives: ResolutionNegativeRepository,
    ):
        _ = await self._missed_id(
            db_session,
            recorder,
            misses=3,
            days_old=DEATH_DEBOUNCE_MIN_SPAN_SECONDS / _DAY + 1,
        )

        size = await negatives.count_active_negatives(user_id=_USER)
        assert size.dead_id_candidates == 1

    async def test_a_flapping_id_inside_one_run_does_not(
        self,
        db_session: AsyncSession,
        recorder: ResolutionRecorder,
        negatives: ResolutionNegativeRepository,
    ):
        """Three failures in an afternoon is a bad afternoon, not a dead id."""
        _ = await self._missed_id(db_session, recorder, misses=3, days_old=0)

        size = await negatives.count_active_negatives(user_id=_USER)
        assert size.dead_id_candidates == 0

    async def test_that_afternoon_does_not_become_a_death_by_growing_old(
        self,
        db_session: AsyncSession,
        recorder: ResolutionRecorder,
        negatives: ResolutionNegativeRepository,
    ):
        """The same burst, a month later, with nothing re-checked since.

        The row has not changed — no new failure, no new evidence — so a
        threshold measured from ``created_at`` to *now* would condemn the id
        purely for having sat still long enough. The span has to be bounded by
        the last observation, not by the clock.
        """
        _ = await self._missed_id(
            db_session,
            recorder,
            misses=3,
            days_old=DEATH_DEBOUNCE_MIN_SPAN_SECONDS / _DAY + 30,
            span_days=0,
        )

        size = await negatives.count_active_negatives(user_id=_USER)
        assert size.dead_id_candidates == 0

    async def test_one_old_failure_does_not(
        self,
        db_session: AsyncSession,
        recorder: ResolutionRecorder,
        negatives: ResolutionNegativeRepository,
    ):
        """A single miss that happened to be long ago proves nothing."""
        _ = await self._missed_id(
            db_session,
            recorder,
            misses=1,
            days_old=DEATH_DEBOUNCE_MIN_SPAN_SECONDS / _DAY + 1,
        )

        size = await negatives.count_active_negatives(user_id=_USER)
        assert size.dead_id_candidates == 0

    async def test_a_success_clears_it_with_no_counter_to_reset(
        self,
        db_session: AsyncSession,
        recorder: ResolutionRecorder,
        negatives: ResolutionNegativeRepository,
    ):
        """Recovery has no hysteresis: the row *is* the streak, and it is gone.

        This is what the consolidation buys. The old streak had to enumerate
        every event type that counts as a success, and missing one —
        ``manual_override``, emitted for exactly the mappings a user pinned by
        hand — meant an id a human had vouched for still counted down.
        """
        ct_id, identifier = await _make_connector_track(db_session)
        await recorder.remember_no_match(
            [DigestSide(identifier=identifier)],
            user_id=_USER,
            connector_name="spotify",
        )
        await self._age_the_row(
            db_session,
            ct_id,
            misses=9,
            days_old=DEATH_DEBOUNCE_MIN_SPAN_SECONDS / _DAY + 30,
        )
        assert (
            await negatives.count_active_negatives(user_id=_USER)
        ).dead_id_candidates == 1

        _ = await recorder.clear_negatives(
            [identifier], user_id=_USER, connector_name="spotify"
        )

        size = await negatives.count_active_negatives(user_id=_USER)
        assert size.dead_id_candidates == 0
        assert size.no_match_pending == 0


class TestOneRefusalOneRow:
    """Every reader of this store honours everything in it.

    That is why the pair key has no asserter in it: a second row on the same
    pair could say nothing the first does not already say, and the column that
    let two rows exist was only ever there to contain refusals the canonical
    reuse gate should not have been writing at all.
    """

    async def test_re_asserting_a_pair_refreshes_rather_than_duplicates(
        self, db_session: AsyncSession, negatives: ResolutionNegativeRepository
    ):
        track_id = await _make_track(db_session)
        ct_id, _ = await _make_connector_track(db_session)

        def _pair(digest: str) -> RejectedPairRow:
            return RejectedPairRow(
                connector_track_id=ct_id,
                candidate_track_id=track_id,
                connector_name="spotify",
                content_digest=digest,
            )

        _ = await negatives.record_rejected_pairs(
            [_pair("first")], user_id=_USER, matcher_version="v1"
        )
        _ = await negatives.record_rejected_pairs(
            [_pair("second")], user_id=_USER, matcher_version="v1"
        )

        rows = (
            (
                await db_session.execute(
                    select(DBResolutionNegative).where(
                        DBResolutionNegative.connector_track_id == ct_id,
                        DBResolutionNegative.kind == "rejected_pair",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1, "one refusal per pair"
        assert rows[0].content_digest == "second", "the re-assertion won"


class TestListNegatives:
    """One listing over both halves of the cache, identity joined in.

    ``rejected`` is the discovery step ``unreject`` depends on — without it a
    human (or the ``manage_track_matches`` agent tool) would need to already
    know a connector-track id to find a candidate worth withdrawing.
    ``dead_id`` is the drill-down behind ``dead_id_candidates``, which is a
    bare count and cannot be acted on without one.
    """

    async def test_lists_with_identities_joined_in(
        self,
        db_session: AsyncSession,
        recorder: ResolutionRecorder,
        negatives: ResolutionNegativeRepository,
    ):
        track_id = await _make_track(db_session, "Rejected Candidate")
        ct_id, identifier = await _make_connector_track(db_session)
        candidate = _candidate(identifier, _domain_track(track_id))

        await recorder.remember_rejections(
            [candidate], user_id=_USER, connector_name="spotify"
        )

        listings = await negatives.list_negatives(user_id=_USER, kind="rejected")

        assert len(listings) == 1
        listing = listings[0]
        assert listing.connector_track_id == ct_id
        assert listing.connector_name == "spotify"
        assert listing.connector_track_identifier == identifier
        assert listing.track_id == track_id
        assert "Rejected Candidate" in (listing.track_title or "")

    async def test_narrows_to_given_connector_track_ids(
        self,
        db_session: AsyncSession,
        recorder: ResolutionRecorder,
        negatives: ResolutionNegativeRepository,
    ):
        """One statement covers a batch of connector tracks, not one per id."""
        track_id = await _make_track(db_session)
        ct_id_a, identifier_a = await _make_connector_track(db_session)
        ct_id_b, identifier_b = await _make_connector_track(db_session)
        await recorder.remember_rejections(
            [_candidate(identifier_a, _domain_track(track_id))],
            user_id=_USER,
            connector_name="spotify",
        )
        await recorder.remember_rejections(
            [_candidate(identifier_b, _domain_track(track_id))],
            user_id=_USER,
            connector_name="spotify",
        )

        narrowed = await negatives.list_negatives(
            user_id=_USER, kind="rejected", connector_track_ids=[ct_id_a]
        )

        assert {listing.connector_track_id for listing in narrowed} == {ct_id_a}

    async def test_an_empty_connector_track_id_filter_returns_nothing(
        self,
        db_session: AsyncSession,
        recorder: ResolutionRecorder,
        negatives: ResolutionNegativeRepository,
    ):
        track_id = await _make_track(db_session)
        _, identifier = await _make_connector_track(db_session)
        await recorder.remember_rejections(
            [_candidate(identifier, _domain_track(track_id))],
            user_id=_USER,
            connector_name="spotify",
        )

        assert (
            await negatives.list_negatives(
                user_id=_USER, kind="rejected", connector_track_ids=[]
            )
            == []
        )

    async def test_a_withdrawn_rejection_does_not_appear(
        self,
        db_session: AsyncSession,
        recorder: ResolutionRecorder,
        negatives: ResolutionNegativeRepository,
    ):
        track_id = await _make_track(db_session)
        ct_id, identifier = await _make_connector_track(db_session)
        await recorder.remember_rejections(
            [_candidate(identifier, _domain_track(track_id))],
            user_id=_USER,
            connector_name="spotify",
        )

        await negatives.unreject(
            user_id=_USER, connector_track_id=ct_id, candidate_track_id=track_id
        )

        assert await negatives.list_negatives(user_id=_USER, kind="rejected") == []

    async def test_scoped_to_its_user(
        self,
        db_session: AsyncSession,
        recorder: ResolutionRecorder,
        negatives: ResolutionNegativeRepository,
    ):
        track_id = await _make_track(db_session)
        _, identifier = await _make_connector_track(db_session)
        await recorder.remember_rejections(
            [_candidate(identifier, _domain_track(track_id))],
            user_id=_USER,
            connector_name="spotify",
        )

        assert await negatives.list_negatives(user_id=_USER, kind="rejected")
        assert (
            await negatives.list_negatives(user_id=_OTHER_USER, kind="rejected") == []
        )

    async def test_dead_ids_name_the_track_still_mapped_to_them(
        self,
        db_session: AsyncSession,
        recorder: ResolutionRecorder,
        negatives: ResolutionNegativeRepository,
    ):
        """A dead id names no candidate, so the track comes via its mapping.

        That is the whole reason one listing serves both kinds: "which of my
        tracks is this about" is the question either way, and answering it
        needs a different join per kind rather than a different listing.
        """
        ct_id, identifier = await _make_connector_track(db_session)
        track_id = await _make_track(db_session, "Still Mapped")
        db_session.add(
            DBTrackMapping(
                user_id=_USER,
                track_id=track_id,
                connector_track_id=ct_id,
                connector_name="spotify",
                match_method="isrc",
                confidence=90,
            )
        )
        await db_session.flush()

        await recorder.remember_no_match(
            [DigestSide(identifier=identifier)],
            user_id=_USER,
            connector_name="spotify",
        )
        await TestIdsThatLookDead._age_the_row(
            db_session,
            ct_id,
            misses=5,
            days_old=DEATH_DEBOUNCE_MIN_SPAN_SECONDS / _DAY + 1,
        )

        listings = await negatives.list_negatives(user_id=_USER, kind="dead_id")

        assert len(listings) == 1
        listing = listings[0]
        assert listing.connector_track_identifier == identifier
        assert listing.track_id == track_id
        assert (listing.track_title or "").startswith("Still Mapped")
        assert listing.consecutive_misses == 5

    async def test_a_rejection_does_not_appear_among_dead_ids(
        self,
        db_session: AsyncSession,
        recorder: ResolutionRecorder,
        negatives: ResolutionNegativeRepository,
    ):
        """The two halves expire on different triggers and never share a view."""
        track_id = await _make_track(db_session)
        _, identifier = await _make_connector_track(db_session)
        await recorder.remember_rejections(
            [_candidate(identifier, _domain_track(track_id))],
            user_id=_USER,
            connector_name="spotify",
        )

        assert await negatives.list_negatives(user_id=_USER, kind="dead_id") == []
