"""The generic mapping repository against a table that is not ``track_mappings``.

``test_mapping_supersession.py`` proves the track instantiation; this proves
the *generic* — an artist-shaped mapping table with its own column names, a
plain (not partial) live key, and **no** supersession columns — round-trips
assert, election, the manual-override filter and event recording through the
same code. What is pinned: a re-assert with the same decision is a touch and
never a new row; a changed decision rewrites in place (there is no history
column to retire into) and clears primacy exactly as a retirement does; the
election's vacancy guard and reset mode behave as they do for tracks; and the
event lands in ``resolution_events`` saying ``entity_kind='artist'``.

The probe table is declared on its own ``DeclarativeBase`` (own registry, own
``MetaData``), created inside the test's savepoint and dropped again — it is
never on ``DatabaseModel.metadata``, so the schema gates never see it.
"""

from collections.abc import AsyncIterator
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid7

import pytest
from pytest_asyncio import fixture as async_fixture
from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    MetaData,
    String,
    UniqueConstraint,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from src.domain.entities.shared import JsonDict
from src.domain.repositories.mapping import PrimaryCandidate
from src.infrastructure.persistence.database.models import DBResolutionEvent
from src.infrastructure.persistence.database.models.base import convention
from src.infrastructure.persistence.repositories._shared.mapping import (
    MappingRepository,
    MappingShape,
)
from src.infrastructure.persistence.repositories.mappers import BaseModelMapper

_USER = "artist-probe"
_OTHER_USER = "artist-probe-other"


class _ProbeBase(DeclarativeBase):
    """A registry and metadata of its own — never ``DatabaseModel``'s."""

    metadata = MetaData(naming_convention=convention)


class DBArtistMappingProbe(_ProbeBase):
    """An artist-shaped mapping table: no supersession, plain live key."""

    __tablename__ = "artist_mappings_probe"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid7
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    user_id: Mapped[str] = mapped_column(String())
    artist_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    connector_artist_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    connector_name: Mapped[str] = mapped_column(String(32))
    match_method: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[int]
    confidence_evidence: Mapped[JsonDict | None] = mapped_column(JSONB)
    origin: Mapped[str] = mapped_column(String(20))
    is_primary: Mapped[bool] = mapped_column(Boolean)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "user_id", "connector_artist_id", name="uq_artist_mappings_probe_live"
        ),
        Index(
            "uq_artist_mappings_probe_primary",
            "user_id",
            "artist_id",
            "connector_name",
            unique=True,
            postgresql_where=text("is_primary = TRUE"),
        ),
    )


class _ProbeMapper(BaseModelMapper[DBArtistMappingProbe, DBArtistMappingProbe]):
    """The repository's reads are column selects; the mapper is never invoked."""


ARTIST_SHAPE = MappingShape(
    entity_kind="artist",
    owner_id_col="artist_id",
    connector_id_col="connector_artist_id",
    live_key=("user_id", "connector_artist_id"),
    supersession=False,
)


class ArtistMappingProbeRepository(
    MappingRepository[DBArtistMappingProbe, DBArtistMappingProbe]
):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(
            session=session,
            model_class=DBArtistMappingProbe,
            mapper=_ProbeMapper(),
            shape=ARTIST_SHAPE,
        )


@async_fixture
async def repo(db_session: AsyncSession) -> AsyncIterator[ArtistMappingProbeRepository]:
    conn = await db_session.connection()
    await conn.run_sync(_ProbeBase.metadata.create_all)
    try:
        yield ArtistMappingProbeRepository(db_session)
    finally:
        # The savepoint rollback undoes the DDL too; the explicit drop is for
        # the case where it does not run (a transaction left aborted mid-test).
        with suppress(Exception):
            conn = await db_session.connection()
            await conn.run_sync(_ProbeBase.metadata.drop_all)


def _row(
    artist_id: UUID,
    connector_artist_id: UUID,
    *,
    confidence: int = 80,
    origin: str = "automatic",
    user_id: str = _USER,
    evidence: JsonDict | None = None,
) -> dict[str, object]:
    return {
        "user_id": user_id,
        "artist_id": artist_id,
        "connector_artist_id": connector_artist_id,
        "connector_name": "spotify",
        "match_method": "direct",
        "confidence": confidence,
        "confidence_evidence": evidence,
        "origin": origin,
    }


async def _rows(
    db_session: AsyncSession, connector_artist_id: UUID
) -> list[DBArtistMappingProbe]:
    result = await db_session.execute(
        select(DBArtistMappingProbe)
        .where(DBArtistMappingProbe.connector_artist_id == connector_artist_id)
        .execution_options(populate_existing=True)
    )
    return list(result.scalars().all())


class TestAssertWithoutSupersession:
    async def test_new_key_is_created(
        self, db_session: AsyncSession, repo: ArtistMappingProbeRepository
    ) -> None:
        artist, ca = uuid7(), uuid7()

        outcome = await repo.assert_mappings([_row(artist, ca)])

        assert len(outcome.created) == 1
        assert not outcome.touched
        assert not outcome.rewritten
        assert not outcome.superseded
        rows = await _rows(db_session, ca)
        assert [r.id for r in rows] == list(outcome.created)
        assert not hasattr(rows[0], "superseded_at")

    async def test_same_decision_is_a_touch_not_a_row(
        self, db_session: AsyncSession, repo: ArtistMappingProbeRepository
    ) -> None:
        artist, ca = uuid7(), uuid7()
        earlier = datetime.now(UTC) - timedelta(days=1)
        first = await repo.assert_mappings([
            _row(artist, ca) | {"last_seen_at": earlier}
        ])

        outcome = await repo.assert_mappings([
            _row(artist, ca, evidence={"final_score": 0.8})
        ])

        assert outcome.touched == first.created
        assert not outcome.created
        assert not outcome.rewritten
        assert not outcome.written
        (row,) = await _rows(db_session, ca)
        assert row.last_seen_at is not None
        assert row.last_seen_at > earlier
        assert row.confidence_evidence == {"final_score": 0.8}

    async def test_changed_decision_is_rewritten_in_place(
        self, db_session: AsyncSession, repo: ArtistMappingProbeRepository
    ) -> None:
        artist, other, ca = uuid7(), uuid7(), uuid7()
        first = await repo.assert_mappings([_row(artist, ca, confidence=60)])
        _ = await repo.ensure_primaries(
            [PrimaryCandidate(artist, "spotify", ca)], mode="fill"
        )

        outcome = await repo.assert_mappings([_row(other, ca, confidence=95)])

        assert outcome.rewritten == first.created
        assert not outcome.created
        assert not outcome.touched
        assert not outcome.superseded
        (row,) = await _rows(db_session, ca)
        assert row.id == first.created[0]
        assert row.artist_id == other
        assert row.confidence == 95
        # A rewrite clears primacy exactly as a retirement does, and reports
        # both halves of the repair: re-elect on the new owner, heal the old.
        assert row.is_primary is False
        assert outcome.primacy_restorations == (PrimaryCandidate(other, "spotify", ca),)
        assert outcome.vacated_owners == ((artist, "spotify"),)
        # The rewritten row is what the event describes.
        assert [w.id for w in outcome.written] == [first.created[0]]
        assert outcome.written[0].owner_id == other


class TestElection:
    async def test_fill_promotes_into_a_vacancy_and_respects_an_incumbent(
        self, db_session: AsyncSession, repo: ArtistMappingProbeRepository
    ) -> None:
        artist, ca_a, ca_b = uuid7(), uuid7(), uuid7()
        _ = await repo.assert_mappings([
            _row(artist, ca_a, confidence=70),
            _row(artist, ca_b, confidence=90),
        ])

        promoted = await repo.ensure_primaries(
            [PrimaryCandidate(artist, "spotify", ca_a)], mode="fill"
        )
        assert promoted == 1
        assert (await _rows(db_session, ca_a))[0].is_primary is True

        # Occupied: the higher-confidence sibling is not a mandate.
        assert (
            await repo.ensure_primaries(
                [PrimaryCandidate(artist, "spotify", ca_b)], mode="fill"
            )
            == 0
        )
        assert (await _rows(db_session, ca_b))[0].is_primary is False

    async def test_reset_deposes_and_elects(
        self, db_session: AsyncSession, repo: ArtistMappingProbeRepository
    ) -> None:
        artist, ca_a, ca_b = uuid7(), uuid7(), uuid7()
        _ = await repo.assert_mappings([_row(artist, ca_a), _row(artist, ca_b)])
        _ = await repo.ensure_primaries(
            [PrimaryCandidate(artist, "spotify", ca_a)], mode="fill"
        )

        promoted = await repo.ensure_primaries(
            [PrimaryCandidate(artist, "spotify", ca_b)], mode="reset"
        )

        assert promoted == 1
        assert (await _rows(db_session, ca_a))[0].is_primary is False
        assert (await _rows(db_session, ca_b))[0].is_primary is True

    async def test_repair_fills_every_vacancy_highest_confidence_first(
        self, db_session: AsyncSession, repo: ArtistMappingProbeRepository
    ) -> None:
        artist, ca_low, ca_high = uuid7(), uuid7(), uuid7()
        _ = await repo.assert_mappings([
            _row(artist, ca_low, confidence=50),
            _row(artist, ca_high, confidence=99),
        ])

        repaired = await repo.repair_missing_primaries(user_id=_USER)

        assert [(r.owner_id, r.connector_id) for r in repaired] == [(artist, ca_high)]
        assert (await _rows(db_session, ca_high))[0].is_primary is True
        assert await repo.repair_missing_primaries(user_id=_USER) == []


class TestManualOverride:
    async def test_pinned_connector_row_is_dropped_for_that_user_only(
        self, repo: ArtistMappingProbeRepository
    ) -> None:
        artist, ca = uuid7(), uuid7()
        _ = await repo.assert_mappings([_row(artist, ca, origin="manual_override")])

        kept = await repo.filter_manual_overrides([
            _row(uuid7(), ca),
            _row(uuid7(), ca, user_id=_OTHER_USER),
        ])

        assert [row["user_id"] for row in kept] == [_OTHER_USER]


class TestEvents:
    async def test_assertion_records_an_artist_event(
        self, db_session: AsyncSession, repo: ArtistMappingProbeRepository
    ) -> None:
        artist, ca = uuid7(), uuid7()
        outcome = await repo.assert_mappings([
            _row(artist, ca, confidence=88, evidence={"final_score": 0.88})
        ])

        await repo.record_assertion(outcome)

        result = await db_session.execute(
            select(DBResolutionEvent).where(
                DBResolutionEvent.user_id == _USER,
                DBResolutionEvent.resulting_mapping_id == outcome.created[0],
            )
        )
        (event,) = result.scalars().all()
        assert event.entity_kind == "artist"
        assert event.event_type == "accepted"
        assert event.track_id == artist
        assert event.connector_track_id == ca
        assert event.confidence == 88
        assert event.score == pytest.approx(0.88)
        assert event.zone == "accept"

    async def test_a_touch_records_nothing_and_a_rewrite_records_the_same_id(
        self, db_session: AsyncSession, repo: ArtistMappingProbeRepository
    ) -> None:
        artist, ca = uuid7(), uuid7()
        first = await repo.assert_mappings([_row(artist, ca, confidence=60)])
        await repo.record_assertion(first)

        await repo.record_assertion(
            await repo.assert_mappings([_row(artist, ca, confidence=60)])
        )
        await repo.record_assertion(
            await repo.assert_mappings([_row(artist, ca, confidence=70)])
        )

        result = await db_session.execute(
            select(DBResolutionEvent.confidence)
            .where(
                DBResolutionEvent.user_id == _USER,
                DBResolutionEvent.resulting_mapping_id == first.created[0],
            )
            .order_by(DBResolutionEvent.recorded_at)
        )
        assert list(result.scalars().all()) == [60, 70]
