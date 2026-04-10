"""Regression tests for base_repo.py core contracts.

Covers the inheritance root utilities that every repository relies on:
- ``_normalize_to_list`` — pure helper for single/list/None normalization.
- ``safe_fetch_relationship`` — defensive awaitable_attrs loader, must never
  propagate exceptions to callers.
- ``ModelMapper`` / ``SessionAwareMapper`` protocol contract via the
  ``has_session_support`` ``TypeIs`` guard.
- ``BaseRepository.find_by`` with both dict and list[ColumnElement] condition
  forms (same public API, different code paths through ``match conditions``).

These tests exist so Phase 3b can tighten the signatures on ``base_repo.py``
(removing the file-level ``# pyright: reportAny=false``) without accidentally
regressing the behaviours downstream repositories depend on. They were added
as greenfield coverage — prior to this file, the only base_repo-adjacent test
was ``test_repo_decorator.py`` which covers the wrapper, not the core.
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
    _normalize_to_list,
    has_session_support,
    safe_fetch_relationship,
)

# =============================================================================
# _normalize_to_list — pure helper, table-driven
# =============================================================================


class TestNormalizeToList:
    """``_normalize_to_list`` is called from ``safe_fetch_relationship`` and
    must correctly normalise the three shapes SQLAlchemy returns for
    ``awaitable_attrs.<rel>``: ``None`` (no relationship), a scalar entity
    (many-to-one / one-to-one), or a list (one-to-many / many-to-many).
    """

    def test_none_returns_empty_list(self):
        assert _normalize_to_list(None) == []

    def test_empty_list_returns_empty_list(self):
        assert _normalize_to_list([]) == []

    def test_populated_list_returns_same_contents(self):
        items = ["a", "b", "c"]
        result = _normalize_to_list(items)
        assert result == items

    def test_single_non_list_entity_wrapped_in_list(self):
        sentinel = object()
        result = _normalize_to_list(sentinel)
        assert result == [sentinel]
        assert result[0] is sentinel

    def test_dict_is_wrapped_not_iterated(self):
        """Dicts are not lists — they must be wrapped, not normalised to keys."""
        data = {"key": "value"}
        result = _normalize_to_list(data)
        assert result == [data]


# =============================================================================
# safe_fetch_relationship — defensive loader, must not propagate exceptions
# =============================================================================


class _FakeDBModelWithFailingAwaitableAttrs:
    """Stub that mimics a DB model with awaitable_attrs but raises on access.

    Used to verify ``safe_fetch_relationship`` catches exceptions and returns
    an empty list instead of propagating them — this is the contract that
    keeps downstream mappers resilient when relationships can't be loaded
    (e.g., greenlet-unsafe contexts).
    """

    class _FailingAttrs:
        def __getattr__(self, name: str) -> object:
            raise RuntimeError(f"simulated greenlet failure for {name}")

    awaitable_attrs = _FailingAttrs()


class _FakeDBModelWithoutAwaitableAttrs:
    """Stub without awaitable_attrs — exercises the ``hasattr`` fallback path."""

    def __init__(self, relationships: dict[str, object]) -> None:
        for name, value in relationships.items():
            setattr(self, name, value)


class TestSafeFetchRelationship:
    """``safe_fetch_relationship`` wraps ``awaitable_attrs`` with defensive
    error handling. The contract is: always return a list, never raise.
    """

    async def test_model_without_awaitable_attrs_uses_hasattr_fallback(self):
        stub = _FakeDBModelWithoutAwaitableAttrs({"likes": ["like1", "like2"]})
        result = await safe_fetch_relationship(stub, "likes")
        assert result == ["like1", "like2"]

    async def test_missing_relationship_returns_empty(self):
        stub = _FakeDBModelWithoutAwaitableAttrs({})
        result = await safe_fetch_relationship(stub, "nonexistent")
        assert result == []

    async def test_failing_awaitable_attrs_does_not_propagate(self):
        stub = _FakeDBModelWithFailingAwaitableAttrs()
        result = await safe_fetch_relationship(stub, "likes")
        assert result == []

    async def test_none_relationship_normalises_to_empty(self):
        stub = _FakeDBModelWithoutAwaitableAttrs({"likes": None})
        result = await safe_fetch_relationship(stub, "likes")
        assert result == []

    async def test_single_entity_relationship_wrapped_in_list(self):
        single_entity = object()
        stub = _FakeDBModelWithoutAwaitableAttrs({"owner": single_entity})
        result = await safe_fetch_relationship(stub, "owner")
        assert result == [single_entity]


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
    """``find_by`` accepts two different condition shapes — dict (simple
    equality) and list[ColumnElement] (full SQLAlchemy expressions). Both
    must return the same results for equivalent queries.
    """

    async def test_find_by_dict_conditions(
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

        results = await track_repo.find_by(
            conditions={"title": f"TEST_find_by_dict_{unique}"}
        )
        assert len(results) == 1

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
