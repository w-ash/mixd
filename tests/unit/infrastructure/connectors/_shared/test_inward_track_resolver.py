"""Tests for InwardTrackResolver base class.

Validates the shared 'resolve inward' pattern: mapping lookup for existing,
canonical reuse for unresolved, and batch creation for the rest.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.entities import Track
from src.infrastructure.connectors._shared.inward_track_resolver import (
    InwardTrackResolver,
    TrackResolutionMetrics,
)
from tests.fixtures import make_track


class FakeInwardResolver(InwardTrackResolver):
    """Test double that records calls and returns configured results."""

    def __init__(
        self,
        batch_results: dict[str, Track] | None = None,
        reuse_results: dict[str, Track] | None = None,
    ):
        super().__init__()
        self._batch_results = batch_results or {}
        self._reuse_results = reuse_results or {}
        self.create_calls: list[list[str]] = []
        self.reuse_calls: list[list[str]] = []

    @property
    def connector_name(self) -> str:
        return "fake"

    def _normalize_id(self, raw_id: str) -> str:
        return raw_id.strip().lower()

    async def _reuse_existing_canonical_tracks(
        self,
        missing_ids: list[str],
        uow: object,
        *,
        user_id: str,
    ) -> dict[str, Track]:
        self.reuse_calls.append(missing_ids)
        return {
            mid: self._reuse_results[mid]
            for mid in missing_ids
            if mid in self._reuse_results
        }

    async def _create_tracks_batch(
        self,
        missing_ids: list[str],
        uow: object,
        *,
        user_id: str = "default",
    ) -> dict[str, Track]:
        self.create_calls.append(missing_ids)
        return {
            mid: self._batch_results[mid]
            for mid in missing_ids
            if mid in self._batch_results
        }


class TestAllExisting:
    """When all IDs are found in mapping lookup, no creation should happen."""

    async def test_all_ids_found_skips_creation(self):
        track_a = make_track(1, "Song A")
        track_b = make_track(2, "Song B")

        uow = MagicMock()
        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {
            ("fake", "id_a"): track_a,
            ("fake", "id_b"): track_b,
        }
        uow.get_connector_repository.return_value = connector_repo

        resolver = FakeInwardResolver()
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["id_a", "id_b"], uow, user_id="test-user"
        )

        assert result == {"id_a": track_a, "id_b": track_b}
        assert metrics.existing == 2
        assert metrics.created == 0
        assert metrics.failed == 0
        assert resolver.create_calls == []


class TestAllMissing:
    """When no IDs are found, all should be passed to batch creation."""

    async def test_all_ids_missing_triggers_creation(self):
        track_a = make_track(1, "Song A")
        track_b = make_track(2, "Song B")

        uow = MagicMock()
        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {}
        uow.get_connector_repository.return_value = connector_repo

        resolver = FakeInwardResolver(batch_results={"id_a": track_a, "id_b": track_b})
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["id_a", "id_b"], uow, user_id="test-user"
        )

        assert result == {"id_a": track_a, "id_b": track_b}
        assert metrics.existing == 0
        assert metrics.created == 2
        assert len(resolver.create_calls) == 1
        assert set(resolver.create_calls[0]) == {"id_a", "id_b"}


class TestMixed:
    """Some IDs exist, some need creation."""

    async def test_only_missing_ids_passed_to_creation(self):
        existing_track = make_track(1, "Existing")
        new_track = make_track(2, "New")

        uow = MagicMock()
        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {
            ("fake", "existing_id"): existing_track,
        }
        uow.get_connector_repository.return_value = connector_repo

        resolver = FakeInwardResolver(batch_results={"new_id": new_track})
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["existing_id", "new_id"], uow, user_id="test-user"
        )

        assert result == {"existing_id": existing_track, "new_id": new_track}
        assert metrics.existing == 1
        assert metrics.created == 1
        assert resolver.create_calls == [["new_id"]]


class TestCreationFailure:
    """Batch creation returns partial results; metrics reflect failures."""

    async def test_partial_creation_reports_failures(self):
        track_a = make_track(1, "A")
        # id_b intentionally not in batch_results → failure

        uow = MagicMock()
        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {}
        uow.get_connector_repository.return_value = connector_repo

        resolver = FakeInwardResolver(batch_results={"id_a": track_a})
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["id_a", "id_b"], uow, user_id="test-user"
        )

        assert result == {"id_a": track_a}
        assert "id_b" not in result
        assert metrics.created == 1
        assert metrics.failed == 1


class TestEmptyInput:
    """Empty input returns empty dict and zero metrics."""

    async def test_empty_input_returns_empty(self):
        uow = MagicMock()
        resolver = FakeInwardResolver()
        result, metrics = await resolver.resolve_to_canonical_tracks(
            [], uow, user_id="test-user"
        )

        assert result == {}
        assert metrics.existing == 0
        assert metrics.created == 0
        assert metrics.failed == 0


class TestDeduplication:
    """Duplicate IDs in input should produce single lookup + single creation."""

    async def test_duplicate_ids_deduplicated(self):
        track = make_track(1, "Song")

        uow = MagicMock()
        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {}
        uow.get_connector_repository.return_value = connector_repo

        resolver = FakeInwardResolver(batch_results={"id_a": track})
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["id_a", "id_a", "ID_A"],
            uow,  # ID_A normalizes to id_a
            user_id="test-user",
        )

        # Only one lookup connection
        call_args = connector_repo.find_tracks_by_connectors.call_args
        connections = call_args.args[0]
        assert len(connections) == 1
        assert connections[0] == ("fake", "id_a")

        # Only one creation call with one ID
        assert len(resolver.create_calls) == 1
        assert resolver.create_calls[0] == ["id_a"]

        # Result maps all original IDs to the same track
        assert result == {"id_a": track}
        assert metrics.created == 1


class TestNormalization:
    """IDs are normalized before lookup and creation."""

    async def test_normalized_ids_used_for_lookup(self):
        track = make_track(1)

        uow = MagicMock()
        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {
            ("fake", "id_a"): track,
        }
        uow.get_connector_repository.return_value = connector_repo

        resolver = FakeInwardResolver()
        result, _ = await resolver.resolve_to_canonical_tracks(
            ["  ID_A  "], uow, user_id="test-user"
        )

        # Normalized to "id_a" for lookup
        call_args = connector_repo.find_tracks_by_connectors.call_args
        connections = call_args.args[0]
        assert connections[0] == ("fake", "id_a")
        assert "id_a" in result


class TestTrackResolutionMetrics:
    """TrackResolutionMetrics is a frozen attrs class."""

    def test_metrics_frozen(self):
        metrics = TrackResolutionMetrics(existing=1, reused=0, created=2, failed=3)
        with pytest.raises(AttributeError):
            metrics.existing = 5  # type: ignore[misc]

    def test_metrics_total(self):
        metrics = TrackResolutionMetrics(existing=10, reused=3, created=5, failed=2)
        assert metrics.total == 20

    def test_metrics_total_with_reused(self):
        metrics = TrackResolutionMetrics(existing=5, reused=10, created=2, failed=1)
        assert metrics.total == 18
        assert metrics.reused == 10

    def test_metrics_defaults_to_zero_reused(self):
        metrics = TrackResolutionMetrics(existing=1, created=2, failed=0)
        assert metrics.reused == 0
        assert metrics.total == 3


class TestCanonicalReuseHook:
    """Canonical reuse matches existing tracks before creating new ones."""

    async def test_reused_tracks_skip_creation(self):
        """When canonical reuse finds existing tracks, track creation is skipped."""
        reused_track = make_track(10, "Reused Song")

        uow = MagicMock()
        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {}
        uow.get_connector_repository.return_value = connector_repo

        resolver = FakeInwardResolver(reuse_results={"id_a": reused_track})
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["id_a"], uow, user_id="test-user"
        )

        assert result == {"id_a": reused_track}
        assert metrics.reused == 1
        assert metrics.created == 0
        assert metrics.failed == 0
        # Track creation should not have been called (no remaining missing IDs)
        assert resolver.create_calls == []

    async def test_mixed_reuse_and_create(self):
        """Canonical reuse handles some IDs, track creation creates the rest."""
        reused_track = make_track(10, "Reused")
        created_track = make_track(20, "Created")

        uow = MagicMock()
        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {}
        uow.get_connector_repository.return_value = connector_repo

        resolver = FakeInwardResolver(
            reuse_results={"id_a": reused_track},
            batch_results={"id_b": created_track},
        )
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["id_a", "id_b"], uow, user_id="test-user"
        )

        assert result == {"id_a": reused_track, "id_b": created_track}
        assert metrics.reused == 1
        assert metrics.created == 1
        # Only id_b should have been passed to track creation
        assert len(resolver.create_calls) == 1
        assert resolver.create_calls[0] == ["id_b"]

    async def test_all_three_steps(self):
        """Mapping lookup, canonical reuse, and track creation all resolve different IDs."""
        existing_track = make_track(1, "Existing")
        reused_track = make_track(10, "Reused")
        created_track = make_track(20, "Created")

        uow = MagicMock()
        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {
            ("fake", "id_existing"): existing_track,
        }
        uow.get_connector_repository.return_value = connector_repo

        resolver = FakeInwardResolver(
            reuse_results={"id_reused": reused_track},
            batch_results={"id_new": created_track},
        )
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["id_existing", "id_reused", "id_new"], uow, user_id="test-user"
        )

        assert result["id_existing"] == existing_track
        assert result["id_reused"] == reused_track
        assert result["id_new"] == created_track
        assert metrics.existing == 1
        assert metrics.reused == 1
        assert metrics.created == 1
        assert metrics.failed == 0
        assert metrics.total == 3

    async def test_default_reuse_returns_empty(self):
        """Base class default returns empty — no reuse without override."""
        # Use a resolver WITHOUT reuse_results configured
        track = make_track(1, "New")

        uow = MagicMock()
        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {}
        uow.get_connector_repository.return_value = connector_repo

        resolver = FakeInwardResolver(batch_results={"id_a": track})
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["id_a"], uow, user_id="test-user"
        )

        assert metrics.reused == 0
        assert metrics.created == 1
