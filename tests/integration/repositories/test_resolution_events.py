"""The resolution ledger and negative cache, against real PostgreSQL.

What these cover is the user-visible promise of v0.10.2: the same wrong match
stops being proposed on every import, an id that flickers is not mistaken for a
dead one, and every identity decision leaves an answer to "why does my library
believe this". None of it can be checked against a mock — the expiry rules live
in partial unique indexes and a ``clock_timestamp()`` column default, and the debounce is a
query over event timestamps rather than a counter anyone could stub.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text, update
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
        tying. The suspect-streak window asks for events *after* the last
        success; with one shared instant per transaction, a success and the
        suspect that followed it are simultaneous and the streak never
        truncates.
        """
        track_id = await _make_track(db_session)

        for event_type in ("accepted", "suspect"):
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
        assert [row.event_type for row in rows] == ["accepted", "suspect"]
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

    async def test_first_miss_schedules_one_day(
        self, db_session: AsyncSession, negatives: ResolutionNegativeRepository
    ):
        ct_id, _ = await _make_connector_track(db_session)
        row = await negatives.upsert_no_match(
            user_id=_USER,
            connector_name="spotify",
            connector_track_id=ct_id,
            matcher_version="v1",
        )
        assert row.consecutive_misses == 1
        assert 0.8 < self._days_until(row.check_again) < 1.2

    async def test_second_miss_doubles_it(
        self, db_session: AsyncSession, negatives: ResolutionNegativeRepository
    ):
        ct_id, _ = await _make_connector_track(db_session)
        for _ in range(2):
            row = await negatives.upsert_no_match(
                user_id=_USER,
                connector_name="spotify",
                connector_track_id=ct_id,
                matcher_version="v1",
            )
        assert row.consecutive_misses == 2
        assert 1.7 < self._days_until(row.check_again) < 2.3

    async def test_the_curve_caps_at_thirty_two_days(
        self, db_session: AsyncSession, negatives: ResolutionNegativeRepository
    ):
        """Six misses would be 32 days; sixty would still be 32 days."""
        ct_id, _ = await _make_connector_track(db_session)
        for _ in range(8):
            row = await negatives.upsert_no_match(
                user_id=_USER,
                connector_name="spotify",
                connector_track_id=ct_id,
                matcher_version="v1",
            )
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
            row = await negatives.upsert_no_match(
                user_id=_USER,
                connector_name="spotify",
                connector_track_id=ct_id,
                matcher_version="v1",
            )
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


class TestDeathDebounce:
    """A spurious 404 must not retire an id the user still listens to."""

    @staticmethod
    async def _backdate_suspects(
        db_session: AsyncSession, ct_id: UUID, *, seconds: float
    ) -> None:
        """Push the oldest suspect back so the streak spans a real interval.

        ``recorded_at`` is the DB's clock by design, so a test that needs
        history has to state it explicitly rather than pretend to write it.
        """
        oldest = await db_session.scalar(
            select(DBResolutionEvent.id)
            .where(
                DBResolutionEvent.connector_track_id == ct_id,
                DBResolutionEvent.event_type == "suspect",
            )
            .order_by(DBResolutionEvent.recorded_at.asc())
            .limit(1)
        )
        await db_session.execute(
            update(DBResolutionEvent)
            .where(DBResolutionEvent.id == oldest)
            .values(recorded_at=datetime.now(UTC) - timedelta(seconds=seconds))
        )

    async def test_two_failures_are_not_death(
        self, db_session: AsyncSession, recorder: ResolutionRecorder
    ):
        track_id = await _make_track(db_session)
        ct_id, identifier = await _make_connector_track(db_session)
        mapping_id = await _make_live_mapping(db_session, track_id, ct_id)
        side = DigestSide(identifier=identifier)

        await recorder.note_suspect(side, user_id=_USER, connector_name="spotify")
        await self._backdate_suspects(
            db_session, ct_id, seconds=DEATH_DEBOUNCE_MIN_SPAN_SECONDS + _DAY
        )
        dead = await recorder.note_suspect(
            side, user_id=_USER, connector_name="spotify"
        )

        assert dead is False
        row = await db_session.get(DBTrackMapping, mapping_id)
        assert row is not None
        assert row.superseded_at is None

    async def test_three_failures_inside_a_day_are_not_death_either(
        self, db_session: AsyncSession, recorder: ResolutionRecorder
    ):
        """The count is met, the span is not — a flapping id, not a dead one."""
        track_id = await _make_track(db_session)
        ct_id, identifier = await _make_connector_track(db_session)
        mapping_id = await _make_live_mapping(db_session, track_id, ct_id)
        side = DigestSide(identifier=identifier)

        for _ in range(3):
            dead = await recorder.note_suspect(
                side, user_id=_USER, connector_name="spotify"
            )

        assert dead is False
        row = await db_session.get(DBTrackMapping, mapping_id)
        assert row is not None
        assert row.superseded_at is None

    async def test_three_failures_over_nine_days_retire_the_mapping(
        self, db_session: AsyncSession, recorder: ResolutionRecorder
    ):
        track_id = await _make_track(db_session)
        ct_id, identifier = await _make_connector_track(db_session)
        mapping_id = await _make_live_mapping(db_session, track_id, ct_id)
        side = DigestSide(identifier=identifier)

        for _ in range(2):
            await recorder.note_suspect(side, user_id=_USER, connector_name="spotify")
        await self._backdate_suspects(
            db_session, ct_id, seconds=DEATH_DEBOUNCE_MIN_SPAN_SECONDS + _DAY
        )
        dead = await recorder.note_suspect(
            side, user_id=_USER, connector_name="spotify"
        )

        assert dead is True
        row = (
            await db_session.execute(
                select(DBTrackMapping)
                .where(DBTrackMapping.id == mapping_id)
                .execution_options(**{INCLUDE_SUPERSEDED: True}, populate_existing=True)
            )
        ).scalar_one()
        assert row.superseded_at is not None
        assert row.supersession_reason == "id_dead"
        # Retirement, not replacement: nothing succeeded this mapping.
        assert row.superseded_by_id is None

    async def test_a_retirement_is_explained_by_a_superseded_event(
        self,
        db_session: AsyncSession,
        recorder: ResolutionRecorder,
        events: ResolutionEventRepository,
    ):
        track_id = await _make_track(db_session)
        ct_id, identifier = await _make_connector_track(db_session)
        mapping_id = await _make_live_mapping(db_session, track_id, ct_id)
        side = DigestSide(identifier=identifier)

        for _ in range(2):
            await recorder.note_suspect(side, user_id=_USER, connector_name="spotify")
        await self._backdate_suspects(
            db_session, ct_id, seconds=DEATH_DEBOUNCE_MIN_SPAN_SECONDS + _DAY
        )
        await recorder.note_suspect(side, user_id=_USER, connector_name="spotify")

        found = await events.events_for_mapping(mapping_id, user_id=_USER)
        superseded = [e for e in found if e.event_type == "superseded"]
        assert superseded
        assert superseded[0].payload["reason"] == "id_dead"

    async def test_one_success_resets_the_streak_immediately(
        self, db_session: AsyncSession, recorder: ResolutionRecorder
    ):
        """No counter to clear — the window simply stops at the last success."""
        track_id = await _make_track(db_session)
        ct_id, identifier = await _make_connector_track(db_session)
        mapping_id = await _make_live_mapping(db_session, track_id, ct_id)
        side = DigestSide(identifier=identifier)

        for _ in range(2):
            await recorder.note_suspect(side, user_id=_USER, connector_name="spotify")
        await self._backdate_suspects(
            db_session, ct_id, seconds=DEATH_DEBOUNCE_MIN_SPAN_SECONDS + _DAY
        )
        # The id came back. Everything before this instant is history.
        await recorder.record(
            [
                ResolutionDecision(
                    event_type="accepted",
                    connector_name="spotify",
                    connector_track_id=ct_id,
                    track_id=track_id,
                )
            ],
            user_id=_USER,
        )
        # `now()` is the transaction clock, so the success and the next suspect
        # would otherwise share a timestamp; nudge it back by a second.
        await db_session.execute(
            text(
                "UPDATE resolution_events SET recorded_at = recorded_at - interval "
                "'1 second' WHERE event_type = 'accepted' AND connector_track_id = :ct"
            ),
            {"ct": ct_id},
        )

        dead = await recorder.note_suspect(
            side, user_id=_USER, connector_name="spotify"
        )

        assert dead is False
        row = await db_session.get(DBTrackMapping, mapping_id)
        assert row is not None
        assert row.superseded_at is None


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
            mapping_id, new_track, "manual_override"
        )

        assert successor.track_id == new_track
        assert successor.origin == "manual_override"
        chain = await repo.get_supersession_chain(mapping_id, user_id=_USER)
        assert [link.track_id for link in chain] == [old_track, new_track]
        assert chain[0].supersession_reason == "manual"


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
            user_id=_USER, connector_track_ids=[ct_id], matcher_version="v1"
        )
        theirs = await negatives.active_rejected_pairs(
            user_id=_OTHER_USER, connector_track_ids=[ct_id], matcher_version="v1"
        )

        assert mine == {(ct_id, track_id)}
        assert theirs == set()

    async def test_backoff_state_is_scoped_to_its_user(
        self, db_session: AsyncSession, negatives: ResolutionNegativeRepository
    ):
        """Both users may hold their own no-match row for the same shared id."""
        ct_id, _ = await _make_connector_track(db_session)

        mine = await negatives.upsert_no_match(
            user_id=_USER,
            connector_name="spotify",
            connector_track_id=ct_id,
            matcher_version="v1",
        )
        theirs = await negatives.upsert_no_match(
            user_id=_OTHER_USER,
            connector_name="spotify",
            connector_track_id=ct_id,
            matcher_version="v1",
        )

        assert mine.id != theirs.id
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


class TestRetirementHealsTheDenormalizedFastPath:
    """Retiring a mapping is only half the repair; the fast path follows it."""

    @staticmethod
    async def _backdate(db_session: AsyncSession, ct_id: UUID, *, seconds: int) -> None:
        _ = await db_session.execute(
            update(DBResolutionEvent)
            .where(
                DBResolutionEvent.connector_track_id == ct_id,
                DBResolutionEvent.event_type == "suspect",
            )
            .values(recorded_at=datetime.now(UTC) - timedelta(seconds=seconds))
        )

    async def _debounce_to_death(
        self,
        db_session: AsyncSession,
        recorder: ResolutionRecorder,
        ct_id: UUID,
        identifier: str,
    ) -> bool:
        side = DigestSide(identifier=identifier)
        for _ in range(2):
            await recorder.note_suspect(side, user_id=_USER, connector_name="spotify")
        await self._backdate(
            db_session, ct_id, seconds=DEATH_DEBOUNCE_MIN_SPAN_SECONDS + _DAY
        )
        return await recorder.note_suspect(
            side, user_id=_USER, connector_name="spotify"
        )

    async def test_retiring_the_only_mapping_clears_the_denormalized_id(
        self, db_session: AsyncSession, recorder: ResolutionRecorder
    ):
        """``tracks.spotify_id`` pointing at a dead id is the FM4d disagreement.

        Migration 044 had to repair 366 production rows of exactly this shape,
        so the write that creates it has to heal it rather than leave it for
        the next read.
        """
        track_id = await _make_track(db_session)
        ct_id, identifier = await _make_connector_track(db_session)
        await _make_live_mapping(db_session, track_id, ct_id)
        connector_repo = TrackConnectorRepository(db_session)
        await connector_repo.ensure_primary_for_connector(track_id, "spotify")
        assert (await db_session.get(DBTrack, track_id)).spotify_id == identifier

        assert await self._debounce_to_death(db_session, recorder, ct_id, identifier)

        track = await db_session.get(DBTrack, track_id)
        await db_session.refresh(track)
        assert track.spotify_id is None

    async def test_a_surviving_sibling_is_promoted_instead(
        self, db_session: AsyncSession, recorder: ResolutionRecorder
    ):
        track_id = await _make_track(db_session)
        dead_ct, dead_identifier = await _make_connector_track(db_session)
        live_ct, live_identifier = await _make_connector_track(db_session)
        await _make_live_mapping(db_session, track_id, dead_ct)
        await _make_live_mapping(db_session, track_id, live_ct)
        connector_repo = TrackConnectorRepository(db_session)
        await connector_repo.set_primary_mapping(track_id, "spotify", dead_ct)

        assert await self._debounce_to_death(
            db_session, recorder, dead_ct, dead_identifier
        )

        track = await db_session.get(DBTrack, track_id)
        await db_session.refresh(track)
        assert track.spotify_id == live_identifier
        promoted = (
            await db_session.execute(
                select(DBTrackMapping).where(
                    DBTrackMapping.track_id == track_id,
                    DBTrackMapping.is_primary.is_(True),
                )
            )
        ).scalar_one()
        assert promoted.connector_track_id == live_ct
