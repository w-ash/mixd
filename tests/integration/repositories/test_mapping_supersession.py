"""Integration tests for append-only track mappings (v0.10.2 migration 044).

Covers the three ``assert_mappings`` outcomes (insert / freshness touch /
supersede), the live-by-default reader filter and its ``include_superseded``
opt-out, the chain walk with its depth and cycle guards, and the identity-map
staleness rule that Core-DML supersession imposes on same-session ORM reads.

Everything here needs real PostgreSQL: the partial unique index, ON CONFLICT
arbiter inference against that index, and ``IS DISTINCT FROM`` over a row
constructor have no meaningful mock.
"""

from datetime import UTC, datetime
from typing import NamedTuple
from uuid import UUID, uuid4, uuid7

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.config.constants import MappingOrigin, MatchMethod
from src.domain.repositories.connector import ConnectorMappingSpec
from src.infrastructure.persistence.database.db_models import (
    DBConnectorTrack,
    DBTrack,
    DBTrackMapping,
)
from src.infrastructure.persistence.database.live_rows import INCLUDE_SUPERSEDED
from src.infrastructure.persistence.repositories.track.connector import (
    TrackConnectorRepository,
    TrackMappingRepository,
)
from src.infrastructure.persistence.repositories.track.core import (
    MappingHistoryLossError,
    TrackRepository,
)

_USER = "default"


@pytest.fixture
def mapping_repo(db_session: AsyncSession) -> TrackMappingRepository:
    return TrackMappingRepository(db_session)


@pytest.fixture
def connector_repo(db_session: AsyncSession) -> TrackConnectorRepository:
    return TrackConnectorRepository(db_session)


@pytest.fixture
def track_repo(db_session: AsyncSession) -> TrackRepository:
    return TrackRepository(db_session)


async def _make_track(db_session: AsyncSession, title: str = "Track") -> UUID:
    uid = str(uuid4())[:8]
    track = DBTrack(title=f"{title} {uid}", artists={"names": [f"Artist {uid}"]})
    db_session.add(track)
    await db_session.flush()
    return track.id


async def _make_connector_track(db_session: AsyncSession) -> UUID:
    uid = str(uuid4())[:8]
    ct = DBConnectorTrack(
        connector_name="spotify",
        connector_track_identifier=f"sp_{uid}",
        title=f"CT {uid}",
        artists={"names": [f"Artist {uid}"]},
        raw_metadata={},
        last_updated=datetime.now(UTC),
    )
    db_session.add(ct)
    await db_session.flush()
    return ct.id


def _row(
    track_id: UUID, ct_id: UUID, *, confidence: int = 90, method: str = "isrc"
) -> dict[str, object]:
    return {
        "user_id": _USER,
        "track_id": track_id,
        "connector_track_id": ct_id,
        "connector_name": "spotify",
        "match_method": method,
        "confidence": confidence,
    }


async def _all_rows(db_session: AsyncSession, ct_id: UUID) -> list[DBTrackMapping]:
    """Every mapping row for a connector track, superseded included."""
    result = await db_session.execute(
        select(DBTrackMapping)
        .where(DBTrackMapping.connector_track_id == ct_id)
        .order_by(DBTrackMapping.created_at, DBTrackMapping.id)
        .execution_options(**{INCLUDE_SUPERSEDED: True}, populate_existing=True)
    )
    return list(result.scalars().all())


async def _track_rows(db_session: AsyncSession, track_id: UUID) -> list[DBTrackMapping]:
    """Every mapping row on a canonical track, superseded included."""
    result = await db_session.execute(
        select(DBTrackMapping)
        .where(DBTrackMapping.track_id == track_id)
        .order_by(DBTrackMapping.created_at, DBTrackMapping.id)
        .execution_options(**{INCLUDE_SUPERSEDED: True}, populate_existing=True)
    )
    return list(result.scalars().all())


async def _connector_track_id(db_session: AsyncSession, identifier: str) -> UUID:
    """The internal id of the connector track carrying this external identifier."""
    result = await db_session.execute(
        select(DBConnectorTrack.id).where(
            DBConnectorTrack.connector_track_identifier == identifier
        )
    )
    return result.scalar_one()


async def _spotify_id(db_session: AsyncSession, track_id: UUID) -> str | None:
    """The denormalized fast-path column, read past the identity map."""
    # Column select, not the identity map: Core DML wrote these rows.
    result = await db_session.execute(
        select(DBTrack.spotify_id).where(DBTrack.id == track_id)
    )
    return result.scalar_one()


class _CrossTrackMove(NamedTuple):
    """The two tracks and two external ids a cross-track re-assertion involves."""

    source: UUID
    destination: UUID
    incumbent_identifier: str
    moving_identifier: str


async def _move_a_primary_mapping_onto_the_destination(
    db_session: AsyncSession,
    connector_repo: TrackConnectorRepository,
    *,
    incumbent_origin: str,
    incumbent_is_primary: bool,
) -> _CrossTrackMove:
    """Re-assert a source track's primary spotify mapping onto a destination.

    The destination starts out holding its own live mapping, on a *different*
    connector track — pinned or automatic, primary or not, per the arguments.
    That mapping is the incumbent whose slot the arriving successor is or is
    not entitled to take, which is the whole question these tests ask.

    Shared because the three cases differ only in how the destination's
    incumbent is set up; the arrival itself is identical in all three.
    """
    source = await _make_track(db_session, "Source")
    destination = await _make_track(db_session, "Destination")
    incumbent_identifier = f"sp_incumbent_{uuid4().hex[:8]}"
    moving_identifier = f"sp_moving_{uuid4().hex[:8]}"

    destination_domain = await connector_repo.track_repo.get_by_id(destination)
    await connector_repo.map_tracks_to_connectors([
        ConnectorMappingSpec(
            track=destination_domain,
            connector="spotify",
            connector_id=incumbent_identifier,
            match_method=MatchMethod.ISRC_MATCH,
            confidence=60,
            origin=incumbent_origin,
        )
    ])
    if incumbent_is_primary:
        await connector_repo.ensure_primary_for_connector(destination, "spotify")

    source_domain = await connector_repo.track_repo.get_by_id(source)
    await connector_repo.map_tracks_to_connectors([
        ConnectorMappingSpec(
            track=source_domain,
            connector="spotify",
            connector_id=moving_identifier,
            match_method=MatchMethod.ISRC_MATCH,
            confidence=70,
        )
    ])
    await connector_repo.ensure_primary_for_connector(source, "spotify")

    # The same connector track, re-asserted onto the destination at a higher
    # confidence: a changed decision, so the source's mapping is retired and
    # the successor lands on the destination — which is where restoration
    # tries to re-promote it.
    await connector_repo.map_tracks_to_connectors([
        ConnectorMappingSpec(
            track=destination_domain,
            connector="spotify",
            connector_id=moving_identifier,
            match_method=MatchMethod.ISRC_MATCH,
            confidence=95,
        )
    ])
    return _CrossTrackMove(source, destination, incumbent_identifier, moving_identifier)


async def _live_spotify_mappings_by_connector_track(
    db_session: AsyncSession, track_id: UUID
) -> dict[UUID, DBTrackMapping]:
    """A track's live spotify mappings, keyed by connector track id."""
    return {
        row.connector_track_id: row
        for row in await _track_rows(db_session, track_id)
        if row.superseded_at is None and row.connector_name == "spotify"
    }


class TestAssertMappingsSemantics:
    """The three outcomes: fresh insert, freshness touch, supersession."""

    async def test_new_key_inserts_one_live_row(
        self, db_session: AsyncSession, mapping_repo: TrackMappingRepository
    ):
        track_id = await _make_track(db_session)
        ct_id = await _make_connector_track(db_session)

        outcome = await mapping_repo.assert_mappings([_row(track_id, ct_id)])

        assert len(outcome.created) == 1
        assert not outcome.touched
        assert not outcome.superseded
        rows = await _all_rows(db_session, ct_id)
        assert len(rows) == 1
        assert rows[0].superseded_at is None

    async def test_unchanged_decision_touches_freshness_without_churn(
        self, db_session: AsyncSession, mapping_repo: TrackMappingRepository
    ):
        """The no-churn rule: re-asserting the same decision grows nothing."""
        track_id = await _make_track(db_session)
        ct_id = await _make_connector_track(db_session)
        first = await mapping_repo.assert_mappings([_row(track_id, ct_id)])
        before = (await _all_rows(db_session, ct_id))[0].last_seen_at

        outcome = await mapping_repo.assert_mappings([_row(track_id, ct_id)])

        assert outcome.touched == first.created
        assert not outcome.created
        assert not outcome.superseded
        rows = await _all_rows(db_session, ct_id)
        assert len(rows) == 1
        assert rows[0].superseded_at is None
        assert before is not None
        assert rows[0].last_seen_at is not None
        assert rows[0].last_seen_at >= before

    async def test_changed_decision_supersedes_instead_of_overwriting(
        self, db_session: AsyncSession, mapping_repo: TrackMappingRepository
    ):
        track_id = await _make_track(db_session)
        ct_id = await _make_connector_track(db_session)
        first = await mapping_repo.assert_mappings([
            _row(track_id, ct_id, confidence=70)
        ])
        predecessor = first.created[0]

        outcome = await mapping_repo.assert_mappings(
            [_row(track_id, ct_id, confidence=95)], reason="rematch"
        )

        successor = outcome.superseded[predecessor]
        assert not outcome.created
        assert not outcome.touched
        rows = {row.id: row for row in await _all_rows(db_session, ct_id)}
        assert len(rows) == 2
        # The predecessor keeps its own payload — history, not a mutation.
        assert rows[predecessor].confidence == 70
        assert rows[predecessor].superseded_at is not None
        assert rows[predecessor].supersession_reason == "rematch"
        assert rows[predecessor].superseded_by_id == successor
        assert rows[successor].confidence == 95
        assert rows[successor].superseded_at is None

    async def test_reason_is_carried_through(
        self, db_session: AsyncSession, mapping_repo: TrackMappingRepository
    ):
        track_id = await _make_track(db_session)
        other_track_id = await _make_track(db_session, "Other")
        ct_id = await _make_connector_track(db_session)
        first = await mapping_repo.assert_mappings([_row(track_id, ct_id)])

        await mapping_repo.assert_mappings(
            [_row(other_track_id, ct_id)], reason="conflation"
        )

        rows = {row.id: row for row in await _all_rows(db_session, ct_id)}
        assert rows[first.created[0]].supersession_reason == "conflation"

    async def test_matching_the_superseded_row_still_supersedes_the_live_one(
        self, db_session: AsyncSession, mapping_repo: TrackMappingRepository
    ):
        """``differs`` compares against the LIVE row only.

        Re-asserting a decision that equals an already-superseded ancestor but
        differs from the incumbent is a change, so it supersedes again — the
        chain records the flip-flop rather than hiding it.
        """
        track_id = await _make_track(db_session)
        ct_id = await _make_connector_track(db_session)
        first = await mapping_repo.assert_mappings([
            _row(track_id, ct_id, confidence=70)
        ])
        second = await mapping_repo.assert_mappings([
            _row(track_id, ct_id, confidence=95)
        ])

        third = await mapping_repo.assert_mappings([
            _row(track_id, ct_id, confidence=70)
        ])

        assert list(third.superseded) == list(second.superseded.values())
        rows = await _all_rows(db_session, ct_id)
        assert len(rows) == 3
        assert sum(row.superseded_at is None for row in rows) == 1
        assert first.created[0] in {row.id for row in rows}

    async def test_intra_batch_duplicates_collapse_before_the_insert(
        self, db_session: AsyncSession, mapping_repo: TrackMappingRepository
    ):
        """ON CONFLICT DO UPDATE cannot touch one row twice in a statement."""
        track_id = await _make_track(db_session)
        ct_id = await _make_connector_track(db_session)

        outcome = await mapping_repo.assert_mappings([
            _row(track_id, ct_id, confidence=50),
            _row(track_id, ct_id, confidence=95),
        ])

        assert len(outcome.created) == 1
        rows = await _all_rows(db_session, ct_id)
        assert len(rows) == 1
        assert rows[0].confidence == 95  # last occurrence wins


class TestCoherenceConstraint:
    """``superseded_at`` and ``supersession_reason`` move as a unit."""

    async def test_retirement_without_a_reason_is_rejected(
        self, db_session: AsyncSession, mapping_repo: TrackMappingRepository
    ):
        track_id = await _make_track(db_session)
        ct_id = await _make_connector_track(db_session)
        created = (await mapping_repo.assert_mappings([_row(track_id, ct_id)])).created[
            0
        ]

        with pytest.raises(IntegrityError, match="supersession_coherent"):
            async with db_session.begin_nested():
                await db_session.execute(
                    update(DBTrackMapping)
                    .where(DBTrackMapping.id == created)
                    .values(superseded_at=datetime.now(UTC))
                )

    async def test_retirement_without_a_successor_is_allowed(
        self, db_session: AsyncSession, mapping_repo: TrackMappingRepository
    ):
        """``id_dead`` with nothing to relink to is a legal end of chain."""
        track_id = await _make_track(db_session)
        ct_id = await _make_connector_track(db_session)
        created = (await mapping_repo.assert_mappings([_row(track_id, ct_id)])).created[
            0
        ]

        await db_session.execute(
            update(DBTrackMapping)
            .where(DBTrackMapping.id == created)
            .values(superseded_at=datetime.now(UTC), supersession_reason="id_dead")
        )

        rows = await _all_rows(db_session, ct_id)
        assert rows[0].superseded_by_id is None


class TestLiveRowsFilter:
    """Superseded rows are invisible unless the reader asks for them."""

    async def test_default_read_hides_superseded_rows(
        self, db_session: AsyncSession, mapping_repo: TrackMappingRepository
    ):
        track_id = await _make_track(db_session)
        ct_id = await _make_connector_track(db_session)
        first = await mapping_repo.assert_mappings([
            _row(track_id, ct_id, confidence=70)
        ])
        await mapping_repo.assert_mappings([_row(track_id, ct_id, confidence=95)])

        visible = (
            (
                await db_session.execute(
                    select(DBTrackMapping)
                    .where(DBTrackMapping.connector_track_id == ct_id)
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )

        assert [row.id for row in visible] != [first.created[0]]
        assert len(visible) == 1
        assert visible[0].superseded_at is None

    async def test_include_superseded_opt_out_shows_them(
        self, db_session: AsyncSession, mapping_repo: TrackMappingRepository
    ):
        track_id = await _make_track(db_session)
        ct_id = await _make_connector_track(db_session)
        await mapping_repo.assert_mappings([_row(track_id, ct_id, confidence=70)])
        await mapping_repo.assert_mappings([_row(track_id, ct_id, confidence=95)])

        assert len(await _all_rows(db_session, ct_id)) == 2

    async def test_relationship_load_is_filtered_too(
        self, db_session: AsyncSession, mapping_repo: TrackMappingRepository
    ):
        """``propagate_to_loaders`` is what stops a ghost resurfacing via a track."""
        track_id = await _make_track(db_session)
        ct_id = await _make_connector_track(db_session)
        await mapping_repo.assert_mappings([_row(track_id, ct_id, confidence=70)])
        await mapping_repo.assert_mappings([_row(track_id, ct_id, confidence=95)])

        result = await db_session.execute(
            select(DBTrack)
            .where(DBTrack.id == track_id)
            .options(selectinload(DBTrack.mappings))
            .execution_options(populate_existing=True)
        )
        track = result.scalar_one()

        assert [m.superseded_at for m in track.mappings] == [None]

    async def test_column_select_is_filtered_too(
        self, db_session: AsyncSession, mapping_repo: TrackMappingRepository
    ):
        """Column selects are ORM statements — the criteria must reach them."""
        track_id = await _make_track(db_session)
        ct_id = await _make_connector_track(db_session)
        await mapping_repo.assert_mappings([_row(track_id, ct_id, confidence=70)])
        await mapping_repo.assert_mappings([_row(track_id, ct_id, confidence=95)])

        confidences = (
            (
                await db_session.execute(
                    select(DBTrackMapping.confidence).where(
                        DBTrackMapping.connector_track_id == ct_id
                    )
                )
            )
            .scalars()
            .all()
        )

        assert list(confidences) == [95]

    async def test_core_supersession_is_visible_to_a_same_session_orm_read(
        self, db_session: AsyncSession, mapping_repo: TrackMappingRepository
    ):
        """The staleness regression, now closed at the write.

        ``assert_mappings`` supersedes through ON CONFLICT DO UPDATE, which
        never synchronises the session — so a caller still holding the entity
        used to keep reading its pre-flip state until some later read happened
        to ask for ``populate_existing``. v0.10.0's projection hit exactly this.
        The writer now expires the rows it touched, so no reader has to know.
        """
        track_id = await _make_track(db_session)
        ct_id = await _make_connector_track(db_session)
        created = (
            await mapping_repo.assert_mappings([_row(track_id, ct_id, confidence=70)])
        ).created[0]
        # Warm the identity map and KEEP the reference alive.
        warmed = (
            await db_session.execute(
                select(DBTrackMapping).where(DBTrackMapping.id == created)
            )
        ).scalar_one()
        assert warmed.superseded_at is None

        await mapping_repo.assert_mappings([_row(track_id, ct_id, confidence=95)])

        reread = (
            await db_session.execute(
                select(DBTrackMapping)
                .where(DBTrackMapping.id == created)
                .execution_options(**{INCLUDE_SUPERSEDED: True})
            )
        ).scalar_one()
        assert reread is warmed
        assert reread.superseded_at is not None

    async def test_a_loaded_track_collection_serves_the_successor(
        self, db_session: AsyncSession, mapping_repo: TrackMappingRepository
    ):
        """The leak the mapping-level expiry alone would not have closed.

        A ``DBTrack`` whose ``mappings`` collection was already loaded keeps
        serving the *retired* row for the rest of the transaction: the
        collection itself is cached, so the live-rows loader criteria never get
        a chance to run. Expiring the parent's collection is what makes the
        successor visible without forcing ``populate_existing`` on every hot
        track read.
        """
        track_id = await _make_track(db_session)
        ct_id = await _make_connector_track(db_session)
        first = (
            await mapping_repo.assert_mappings([_row(track_id, ct_id, confidence=70)])
        ).created[0]
        track = (
            await db_session.execute(
                select(DBTrack)
                .where(DBTrack.id == track_id)
                .options(selectinload(DBTrack.mappings))
            )
        ).scalar_one()
        assert [m.id for m in track.mappings] == [first]

        outcome = await mapping_repo.assert_mappings([
            _row(track_id, ct_id, confidence=95)
        ])
        successor = next(iter(outcome.superseded.values()))

        reread = (
            await db_session.execute(
                select(DBTrack)
                .where(DBTrack.id == track_id)
                .options(selectinload(DBTrack.mappings))
            )
        ).scalar_one()
        assert reread is track
        assert [m.id for m in reread.mappings] == [successor]


class TestSupersessionChain:
    """The chain *is* the history: walkable, depth-capped, cycle-guarded."""

    async def test_walks_a_three_link_chain(
        self,
        db_session: AsyncSession,
        mapping_repo: TrackMappingRepository,
        connector_repo: TrackConnectorRepository,
    ):
        track_id = await _make_track(db_session)
        ct_id = await _make_connector_track(db_session)
        first = await mapping_repo.assert_mappings([
            _row(track_id, ct_id, confidence=50)
        ])
        second = await mapping_repo.assert_mappings([
            _row(track_id, ct_id, confidence=70)
        ])
        third = await mapping_repo.assert_mappings([
            _row(track_id, ct_id, confidence=95)
        ])

        chain = await connector_repo.get_supersession_chain(
            first.created[0], user_id=_USER
        )

        assert [m.id for m in chain] == [
            first.created[0],
            *second.superseded.values(),
            *third.superseded.values(),
        ]
        assert [m.confidence for m in chain] == [50, 70, 95]
        assert chain[-1].superseded_at is None

    async def test_live_mapping_walks_to_a_single_link(
        self,
        db_session: AsyncSession,
        mapping_repo: TrackMappingRepository,
        connector_repo: TrackConnectorRepository,
    ):
        track_id = await _make_track(db_session)
        ct_id = await _make_connector_track(db_session)
        created = (await mapping_repo.assert_mappings([_row(track_id, ct_id)])).created[
            0
        ]

        chain = await connector_repo.get_supersession_chain(created, user_id=_USER)

        assert [m.id for m in chain] == [created]

    async def test_unknown_id_returns_empty(
        self, connector_repo: TrackConnectorRepository
    ):
        assert await connector_repo.get_supersession_chain(uuid7(), user_id=_USER) == []

    async def test_depth_cap_bounds_the_walk(
        self,
        db_session: AsyncSession,
        mapping_repo: TrackMappingRepository,
        connector_repo: TrackConnectorRepository,
    ):
        track_id = await _make_track(db_session)
        ct_id = await _make_connector_track(db_session)
        first = await mapping_repo.assert_mappings([
            _row(track_id, ct_id, confidence=10)
        ])
        for confidence in (20, 30, 40):
            await mapping_repo.assert_mappings([
                _row(track_id, ct_id, confidence=confidence)
            ])

        chain = await connector_repo.get_supersession_chain(
            first.created[0], user_id=_USER, max_depth=2
        )

        assert len(chain) == 2

    async def test_cycle_guard_terminates_a_corrupted_chain(
        self,
        db_session: AsyncSession,
        mapping_repo: TrackMappingRepository,
        connector_repo: TrackConnectorRepository,
    ):
        """No constraint forbids a loop, so the walker must survive one."""
        track_id = await _make_track(db_session)
        ct_id = await _make_connector_track(db_session)
        first = await mapping_repo.assert_mappings([
            _row(track_id, ct_id, confidence=50)
        ])
        second = await mapping_repo.assert_mappings([
            _row(track_id, ct_id, confidence=95)
        ])
        head = first.created[0]
        tail = next(iter(second.superseded.values()))
        # Bend the live tail back onto its predecessor.
        await db_session.execute(
            update(DBTrackMapping)
            .where(DBTrackMapping.id == tail)
            .values(
                superseded_at=datetime.now(UTC),
                supersession_reason="manual",
                superseded_by_id=head,
            )
        )

        chain = await connector_repo.get_supersession_chain(head, user_id=_USER)

        assert [m.id for m in chain] == [head, tail]


class TestSupersessionAndPrimacy:
    """Primacy is a property of the *live* row, and exactly one may hold it."""

    async def test_re_scoring_a_primary_leaves_one_live_primary(
        self,
        db_session: AsyncSession,
        connector_repo: TrackConnectorRepository,
        mapping_repo: TrackMappingRepository,
    ):
        """A retired row keeping ``is_primary`` is a ghost the fast path follows.

        It also collides with its own successor under ``uq_primary_mapping``,
        and clearing it without re-promoting leaves the track with a live
        mapping and no primary at all — so ``spotify_id`` stops resolving.
        """
        track_id = await _make_track(db_session)
        ct_id = await _make_connector_track(db_session)
        first = await mapping_repo.assert_mappings([
            {**_row(track_id, ct_id, confidence=70), "is_primary": True}
        ])
        assert len(first.created) == 1

        outcome = await mapping_repo.assert_mappings([
            _row(track_id, ct_id, confidence=95)
        ])

        assert len(outcome.superseded) == 1
        assert len(outcome.primacy_restorations) == 1
        rows = await _all_rows(db_session, ct_id)
        retired = [row for row in rows if row.superseded_at is not None]
        live = [row for row in rows if row.superseded_at is None]
        assert len(live) == 1
        assert len(retired) == 1
        assert retired[0].is_primary is False

    async def test_the_successor_inherits_primacy_through_the_mapping_path(
        self, db_session: AsyncSession, connector_repo: TrackConnectorRepository
    ):
        """The whole path: ``map_tracks_to_connectors`` re-promotes after a re-score."""

        track_id = await _make_track(db_session)
        domain_track = await connector_repo.track_repo.get_by_id(track_id)
        spec_kwargs = {
            "track": domain_track,
            "connector": "spotify",
            "connector_id": f"sp_primacy_{uuid4().hex[:8]}",
            "match_method": MatchMethod.ISRC_MATCH,
        }
        await connector_repo.map_tracks_to_connectors([
            ConnectorMappingSpec(**spec_kwargs, confidence=70)
        ])
        # Primacy is assigned separately from the bulk map (by the inward
        # resolvers, review-accept, or read-path healing) — grant it here so
        # the re-score below has something to inherit.
        await connector_repo.ensure_primary_for_connector(track_id, "spotify")

        await connector_repo.map_tracks_to_connectors([
            ConnectorMappingSpec(**spec_kwargs, confidence=95)
        ])

        result = await db_session.execute(
            select(DBTrackMapping)
            .where(DBTrackMapping.track_id == track_id)
            .execution_options(**{INCLUDE_SUPERSEDED: True}, populate_existing=True)
        )
        rows = list(result.scalars().all())
        live = [row for row in rows if row.superseded_at is None]
        assert len(live) == 1
        assert live[0].confidence == 95
        assert live[0].is_primary is True
        assert all(row.is_primary is False for row in rows if row.superseded_at)

    async def test_the_track_a_mapping_departs_is_healed_too(
        self, db_session: AsyncSession, connector_repo: TrackConnectorRepository
    ):
        """Re-asserting onto another track vacates the first one's primary.

        Restoration re-promotes on the *successor's* track. The track the
        mapping left keeps a denormalized ``spotify_id`` pointing at an
        identifier that now belongs to someone else, and no primary of its own
        — the FM4d drift migration 044's pre-pass had to repair on 366
        production rows, reintroduced one supersession at a time.
        """

        departed = await _make_track(db_session, "Departed")
        arrived = await _make_track(db_session, "Arrived")
        connector_id = f"sp_moved_{uuid4().hex[:8]}"

        departed_domain = await connector_repo.track_repo.get_by_id(departed)
        await connector_repo.map_tracks_to_connectors([
            ConnectorMappingSpec(
                track=departed_domain,
                connector="spotify",
                connector_id=connector_id,
                match_method=MatchMethod.ISRC_MATCH,
                confidence=70,
            )
        ])
        await connector_repo.ensure_primary_for_connector(departed, "spotify")

        # The same connector track, re-asserted onto a different canonical.
        arrived_domain = await connector_repo.track_repo.get_by_id(arrived)
        await connector_repo.map_tracks_to_connectors([
            ConnectorMappingSpec(
                track=arrived_domain,
                connector="spotify",
                connector_id=connector_id,
                match_method=MatchMethod.ISRC_MATCH,
                confidence=95,
            )
        ])

        # Column select, not the identity map: Core DML wrote these rows.
        stale = await db_session.execute(
            select(DBTrack.spotify_id).where(DBTrack.id == departed)
        )
        assert stale.scalar_one() is None, (
            "the departed track must not keep an identifier it no longer owns"
        )
        assert await connector_repo.count_stale_denormalized_ids(user_id=_USER) == 0

    async def test_cross_track_successor_does_not_depose_a_pinned_destination_primary(
        self, db_session: AsyncSession, connector_repo: TrackConnectorRepository
    ):
        """A user-pinned primary is not something an arriving successor may take.

        Restoration exists to refill the slot supersession emptied on the
        successor's *own* track. When the successor lands somewhere else, the
        destination's slot was never emptied — and clearing it to make room
        silently discarded a ``manual_override`` the user had chosen, the one
        decision the whole origin column exists to protect.
        """
        move = await _move_a_primary_mapping_onto_the_destination(
            db_session,
            connector_repo,
            incumbent_origin=MappingOrigin.MANUAL_OVERRIDE,
            incumbent_is_primary=True,
        )
        incumbent_ct = await _connector_track_id(db_session, move.incumbent_identifier)
        moving_ct = await _connector_track_id(db_session, move.moving_identifier)

        live = await _live_spotify_mappings_by_connector_track(
            db_session, move.destination
        )
        assert live[incumbent_ct].is_primary is True
        assert live[moving_ct].is_primary is False, (
            "the arrival is live, but the slot was occupied"
        )
        assert await _spotify_id(db_session, move.destination) == (
            move.incumbent_identifier
        )
        assert sum(1 for row in live.values() if row.is_primary) == 1

    async def test_cross_track_successor_does_not_depose_an_automatic_destination_primary(
        self, db_session: AsyncSession, connector_repo: TrackConnectorRepository
    ):
        """Not just pinned ones: no live primary is deposed, ever.

        This is the choice the pinned case alone would leave ambiguous — the
        arrival scores higher (95 against 60) and still does not win. Promotion
        here is vacancy-fill; changing which live mapping holds primacy is the
        explicit ``set_primary`` path's decision to make, not a side effect of
        re-asserting a mapping somewhere else.
        """
        move = await _move_a_primary_mapping_onto_the_destination(
            db_session,
            connector_repo,
            incumbent_origin=MappingOrigin.AUTOMATIC,
            incumbent_is_primary=True,
        )
        incumbent_ct = await _connector_track_id(db_session, move.incumbent_identifier)
        moving_ct = await _connector_track_id(db_session, move.moving_identifier)

        live = await _live_spotify_mappings_by_connector_track(
            db_session, move.destination
        )
        assert live[incumbent_ct].is_primary is True
        assert live[moving_ct].is_primary is False
        assert await _spotify_id(db_session, move.destination) == (
            move.incumbent_identifier
        )
        assert sum(1 for row in live.values() if row.is_primary) == 1

    async def test_cross_track_successor_fills_a_vacant_destination_primary(
        self, db_session: AsyncSession, connector_repo: TrackConnectorRepository
    ):
        """Never deposing must not become never promoting.

        A destination holding live mappings but no primary is exactly the FM4d
        drift the restoration path exists to drain, so the arrival takes the
        empty slot and the denormalized column follows it.
        """
        move = await _move_a_primary_mapping_onto_the_destination(
            db_session,
            connector_repo,
            incumbent_origin=MappingOrigin.AUTOMATIC,
            incumbent_is_primary=False,
        )
        incumbent_ct = await _connector_track_id(db_session, move.incumbent_identifier)
        moving_ct = await _connector_track_id(db_session, move.moving_identifier)

        live = await _live_spotify_mappings_by_connector_track(
            db_session, move.destination
        )
        assert live[moving_ct].is_primary is True
        assert live[incumbent_ct].is_primary is False
        assert await _spotify_id(db_session, move.destination) == move.moving_identifier
        assert sum(1 for row in live.values() if row.is_primary) == 1


class TestEvidenceIsDriftNotDecision:
    """Evidence explains a decision; refreshing it must not restate one."""

    async def test_new_evidence_refreshes_the_live_row_without_superseding(
        self, db_session: AsyncSession, mapping_repo: TrackMappingRepository
    ):
        """Freezing it left a mapping explained by the first run that ever scored it."""
        track_id = await _make_track(db_session)
        ct_id = await _make_connector_track(db_session)
        await mapping_repo.assert_mappings([
            {**_row(track_id, ct_id), "confidence_evidence": {"final_score": 70.0}}
        ])

        outcome = await mapping_repo.assert_mappings([
            {**_row(track_id, ct_id), "confidence_evidence": {"final_score": 71.5}}
        ])

        assert not outcome.superseded
        assert len(outcome.touched) == 1
        rows = await _all_rows(db_session, ct_id)
        assert len(rows) == 1
        assert rows[0].confidence_evidence == {"final_score": 71.5}

    async def test_a_retired_row_keeps_its_own_evidence(
        self, db_session: AsyncSession, mapping_repo: TrackMappingRepository
    ):
        """Refreshing stops at the point the row becomes history.

        A retired row's evidence is the answer to "why did my library believe
        *that*", and overwriting it with the score that replaced it makes the
        chain explain every past decision with the newest one's reasoning —
        the provenance is not merely lost, it is confidently wrong. The
        successor carries the new evidence; the predecessor keeps its own.
        """
        track_id = await _make_track(db_session)
        ct_id = await _make_connector_track(db_session)
        await mapping_repo.assert_mappings([
            {
                **_row(track_id, ct_id, confidence=70),
                "confidence_evidence": {"final_score": 70.0},
            }
        ])

        outcome = await mapping_repo.assert_mappings([
            {
                **_row(track_id, ct_id, confidence=95),
                "confidence_evidence": {"final_score": 95.0},
            }
        ])

        assert len(outcome.superseded) == 1
        rows = await _all_rows(db_session, ct_id)
        retired = [row for row in rows if row.superseded_at is not None]
        live = [row for row in rows if row.superseded_at is None]
        assert len(retired) == 1
        assert len(live) == 1
        assert retired[0].confidence_evidence == {"final_score": 70.0}
        assert live[0].confidence_evidence == {"final_score": 95.0}


class TestCascadeCannotOutliveTheLog:
    """A track delete must not take mapping history with it.

    Production reached the state this class forbids: ``resolution_events`` held
    a ``superseded`` event naming a superseding mapping id that
    ``track_mappings`` no longer had. ``resolution_events`` references mappings
    by value, so the ``ON DELETE CASCADE`` from ``tracks`` removed the artifact
    and left the log describing it.
    """

    async def test_a_track_holding_a_live_mapping_refuses_to_be_deleted(
        self,
        db_session: AsyncSession,
        mapping_repo: TrackMappingRepository,
        track_repo: TrackRepository,
    ):
        """A live mapping is a future predecessor, so it is guarded from the start."""
        track_id = await _make_track(db_session)
        ct_id = await _make_connector_track(db_session)
        await mapping_repo.assert_mappings([_row(track_id, ct_id)])

        with pytest.raises(MappingHistoryLossError) as caught:
            await track_repo.hard_delete_track(track_id)

        assert caught.value.track_id == track_id
        assert len(caught.value.mapping_ids) == 1
        assert await _track_rows(db_session, track_id), "the mapping is still there"

    async def test_a_track_holding_retired_history_refuses_to_be_deleted(
        self,
        db_session: AsyncSession,
        mapping_repo: TrackMappingRepository,
        track_repo: TrackRepository,
    ):
        """The exact production shape: an event's predecessor is a retired row."""
        track_id = await _make_track(db_session)
        ct_id = await _make_connector_track(db_session)
        await mapping_repo.assert_mappings([_row(track_id, ct_id, confidence=70)])
        outcome = await mapping_repo.assert_mappings([
            _row(track_id, ct_id, confidence=95)
        ])
        assert len(outcome.superseded) == 1

        with pytest.raises(MappingHistoryLossError) as caught:
            await track_repo.hard_delete_track(track_id)

        # Both the retired row and its successor — the guard counts the cascade,
        # which is neither live-scoped nor tenant-scoped.
        assert len(caught.value.mapping_ids) == 2
        assert len(await _track_rows(db_session, track_id)) == 2

    async def test_another_tenants_mapping_is_guarded_too(
        self,
        db_session: AsyncSession,
        mapping_repo: TrackMappingRepository,
        track_repo: TrackRepository,
    ):
        """The FK cascade ignores ``user_id``, so the guard must not honour it.

        PDR-002 records the Neon owner role as BYPASSRLS, so a tenant-scoped
        guard would not merely mis-count — it would wave through exactly the
        rows the deleting tenant has no standing to destroy.
        """
        track_id = await _make_track(db_session)
        ct_id = await _make_connector_track(db_session)
        await mapping_repo.assert_mappings([
            {**_row(track_id, ct_id), "user_id": "someone-else"}
        ])

        with pytest.raises(MappingHistoryLossError):
            await track_repo.hard_delete_track(track_id)

    async def test_a_track_with_no_mappings_still_deletes(
        self, db_session: AsyncSession, track_repo: TrackRepository
    ):
        """The guard is about history, not about deletion — an empty track goes."""
        track_id = await _make_track(db_session)

        await track_repo.hard_delete_track(track_id)

        result = await db_session.execute(
            select(DBTrack.id).where(DBTrack.id == track_id)
        )
        assert result.scalar_one_or_none() is None

    async def test_a_merge_still_deletes_its_loser(
        self,
        db_session: AsyncSession,
        mapping_repo: TrackMappingRepository,
        track_repo: TrackRepository,
    ):
        """The one legitimate caller moves every mapping off first, so it passes.

        Without this, the guard would read as "tracks can no longer be merged"
        rather than "history cannot be cascaded away".
        """
        winner = await _make_track(db_session, "Winner")
        loser = await _make_track(db_session, "Loser")
        ct_id = await _make_connector_track(db_session)
        await mapping_repo.assert_mappings([_row(loser, ct_id, confidence=70)])
        _ = await mapping_repo.assert_mappings([_row(loser, ct_id, confidence=95)])

        _ = await track_repo.merge_mappings_to_track(loser, winner)
        await track_repo.hard_delete_track(loser)

        assert len(await _track_rows(db_session, winner)) == 2, (
            "both the retired row and its successor survived on the winner"
        )
