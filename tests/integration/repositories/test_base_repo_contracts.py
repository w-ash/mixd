"""Regression tests for base_repo.py core contracts.

Covers the inheritance root utilities that every repository relies on:
- ``ModelMapper`` / ``SessionAwareMapper`` protocol contract via the
  ``has_session_support`` ``TypeIs`` guard.
- ``BaseRepository.find_by`` with both dict and list[ColumnElement] condition
  forms (same public API, different code paths through ``match conditions``).
"""

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.track import Artist, Track
from src.infrastructure.persistence.database.db_models import DBTrack
from src.infrastructure.persistence.repositories.base_repo import (
    BaseModelMapper,
    BaseRepository,
    ModelMapper,
    has_session_support,
)

# =============================================================================
# ModelMapper / SessionAwareMapper protocol contract
# =============================================================================


class _PlainMapper(BaseModelMapper[DBTrack, Track]):
    """Mapper without session support — should NOT pass has_session_support.

    Overrides ``get_default_relationships`` to return ``[]`` so the contract
    tests don't trigger the nested-relationship loading path inherited from
    ``BaseModelMapper`` (which returns ``["mappings", "mappings.connector_track"]``
    and is specific to the track domain).
    """

    @staticmethod
    async def to_domain(db_model: DBTrack) -> Track:
        return Track(
            id=db_model.id,
            title=db_model.title,
            artists=[Artist(name="test")],
        )

    @staticmethod
    def to_db(domain_model: Track) -> DBTrack:
        return DBTrack(title=domain_model.title)

    @staticmethod
    def get_default_relationships() -> list[str]:
        return []


class _SessionAwareMapperStub(BaseModelMapper[DBTrack, Track]):
    """Mapper with ``to_domain_with_session`` — should pass has_session_support."""

    @staticmethod
    async def to_domain(db_model: DBTrack) -> Track:
        return Track(
            id=db_model.id,
            title=db_model.title,
            artists=[Artist(name="test")],
        )

    @staticmethod
    def to_db(domain_model: Track) -> DBTrack:
        return DBTrack(title=domain_model.title)

    @staticmethod
    def get_default_relationships() -> list[str]:
        return []

    @staticmethod
    async def to_domain_with_session(
        db_model: DBTrack, session: AsyncSession | None = None
    ) -> Track:
        _ = session
        return Track(id=db_model.id, title=db_model.title)


class TestHasSessionSupportTypeGuard:
    """``has_session_support`` is a ``TypeIs`` that narrows ``ModelMapper`` to
    ``SessionAwareMapper`` at the call site. Plain mappers must not pass;
    session-aware mappers must.
    """

    def test_plain_mapper_does_not_support_session(self):
        mapper: ModelMapper[DBTrack, Track] = _PlainMapper()
        assert has_session_support(mapper) is False

    def test_session_aware_mapper_supports_session(self):
        mapper: ModelMapper[DBTrack, Track] = _SessionAwareMapperStub()
        assert has_session_support(mapper) is True


# =============================================================================
# BaseRepository.find_by — dict vs list[ColumnElement] condition forms
# =============================================================================


@pytest.fixture
def track_repo(db_session: AsyncSession) -> BaseRepository[DBTrack, Track]:
    """Minimal BaseRepository wired to DBTrack for contract testing.

    We construct BaseRepository directly (not via UoW) because these tests
    exercise base class behaviours, not the full TrackRepository graph.
    """
    return BaseRepository[DBTrack, Track](
        session=db_session,
        model_class=DBTrack,
        mapper=_PlainMapper(),
    )


class TestFindByConditionForms:
    """``find_by`` takes ``list[ColumnElement]`` expressions; the string-keyed
    ``{field: value}`` form lives on ``find_one_by``/``count`` (and the shared
    ``_apply_conditions`` helper). Both shapes resolve via the same helper.
    """

    async def test_find_one_by_dict_conditions(
        self,
        db_session: AsyncSession,
        track_repo: BaseRepository[DBTrack, Track],
    ):
        unique = str(uuid4())[:8]
        track = DBTrack(
            title=f"TEST_find_by_dict_{unique}",
            artists={"names": ["test"]},
            duration_ms=200000,
        )
        track.mappings = []
        track.metrics = []
        track.likes = []
        track.plays = []
        track.playlist_tracks = []
        db_session.add(track)
        await db_session.flush()

        result = await track_repo.find_one_by(
            conditions={"title": f"TEST_find_by_dict_{unique}"}
        )
        assert result is not None

    async def test_find_by_list_conditions_returns_same_results(
        self,
        db_session: AsyncSession,
        track_repo: BaseRepository[DBTrack, Track],
    ):
        unique = str(uuid4())[:8]
        title = f"TEST_find_by_list_{unique}"
        track = DBTrack(
            title=title,
            artists={"names": ["test"]},
            duration_ms=200000,
        )
        track.mappings = []
        track.metrics = []
        track.likes = []
        track.plays = []
        track.playlist_tracks = []
        db_session.add(track)
        await db_session.flush()

        results = await track_repo.find_by(conditions=[DBTrack.title == title])
        assert len(results) == 1
