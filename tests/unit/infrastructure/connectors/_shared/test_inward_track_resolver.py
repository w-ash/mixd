"""Tests for InwardTrackResolver base class.

Validates the shared 'resolve inward' pattern: mapping lookup for existing,
canonical reuse for unresolved, and batch creation for the rest — plus the
planned-write persist pipeline (``WritePlanningResolver``): ISRC planning
arms, ordered write path, claim dedupe, and write-failure accounting.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.constants import MatchMethod
from src.domain.entities import Track
from src.infrastructure.connectors._shared.inward_track_resolver import (
    InwardTrackResolver,
    IsrcCollisionReview,
    PlannedWrite,
    ReuseMetadata,
    TrackResolutionMetrics,
    WritePlanningResolver,
    plan_isrc_write,
)
from src.infrastructure.connectors._shared.successor_resolution import (
    SuccessorAssertion,
)
from tests.fixtures import attach_resolution_recorder, make_track


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


class ReuseHintResolver(InwardTrackResolver):
    """Test double that runs the REAL canonical-reuse step via hint metadata."""

    def __init__(self, hints: dict[str, tuple[str, str]]):
        super().__init__()
        self._hints = hints
        self.create_calls: list[list[str]] = []

    @property
    def connector_name(self) -> str:
        return "fake"

    def _normalize_id(self, raw_id: str) -> str:
        return raw_id.strip().lower()

    def _extract_reuse_metadata(self, identifier: str) -> ReuseMetadata | None:
        hint = self._hints.get(identifier)
        if not hint:
            return None
        artist, title = hint
        return ReuseMetadata(
            artist=artist,
            title=title,
            connector_id=identifier,
            lookup_pair=(title.lower(), artist.lower()),
        )

    async def _create_tracks_batch(
        self,
        missing_ids: list[str],
        uow: object,
        *,
        user_id: str = "default",
    ) -> dict[str, Track]:
        self.create_calls.append(missing_ids)
        return {mid: make_track(99, "Created") for mid in missing_ids}


def _make_reuse_uow(candidates: dict[tuple[str, str], Track]) -> MagicMock:
    """UoW mock whose title+artist search returns ``candidates``."""
    uow = MagicMock()
    attach_resolution_recorder(uow)

    track_repo = AsyncMock()
    track_repo.find_tracks_by_title_artist.return_value = candidates
    uow.get_track_repository.return_value = track_repo

    connector_repo = AsyncMock()
    connector_repo.find_tracks_by_connectors.return_value = {}
    uow.get_connector_repository.return_value = connector_repo
    return uow


class TestPersistBulkWithItemFallback:
    """The shared savepoint-bulk-then-per-item skeleton, tested once.

    Its three consumers (Spotify persist, Last.fm persist, canonical reuse)
    pin their own payloads; this pins the skeleton's contract — savepoint
    counts, fallback isolation, and the hooks' timing.
    """

    @staticmethod
    def _uow():
        from tests.fixtures.mocks import make_mock_uow

        return make_mock_uow()

    @staticmethod
    def _persist(poisoned: set[str]):
        async def _inner(chunk):
            for item in chunk:
                if item in poisoned:
                    raise RuntimeError(f"poisoned: {item}")
            return {item: item.upper() for item in chunk}

        return _inner

    async def test_bulk_success_takes_one_savepoint_and_fires_hooks_once(self):
        from src.infrastructure.connectors._shared.inward_track_resolver import (
            persist_bulk_with_item_fallback,
        )

        uow = self._uow()
        persisted_chunks: list[list[str]] = []

        resolved, failed = await persist_bulk_with_item_fallback(
            ["a", "b"],
            uow,
            persist=self._persist(set()),
            write_key=lambda item: item,
            describe="test writes",
            on_persisted=lambda chunk: persisted_chunks.append(list(chunk)),
        )

        assert resolved == {"a": "A", "b": "B"}
        assert failed == set()
        assert uow.savepoint.call_count == 1
        # The hook fires once, for the whole chunk, after its savepoint.
        assert persisted_chunks == [["a", "b"]]

    async def test_poisoned_item_costs_only_itself(self):
        from src.infrastructure.connectors._shared.inward_track_resolver import (
            persist_bulk_with_item_fallback,
        )

        uow = self._uow()
        persisted_chunks: list[list[str]] = []
        item_failures: list[str] = []

        resolved, failed = await persist_bulk_with_item_fallback(
            ["a", "bad", "c"],
            uow,
            persist=self._persist({"bad"}),
            write_key=lambda item: item,
            describe="test writes",
            on_persisted=lambda chunk: persisted_chunks.append(list(chunk)),
            on_item_failure=lambda item, _e: item_failures.append(item),
        )

        assert resolved == {"a": "A", "c": "C"}
        assert failed == {"bad"}
        # One savepoint for the failed bulk attempt, then one per item.
        assert uow.savepoint.call_count == 4
        # The hook fires per surviving item — never for the poisoned one, and
        # never for the discarded bulk attempt.
        assert persisted_chunks == [["a"], ["c"]]
        assert item_failures == ["bad"]

    async def test_contention_is_reraised_never_split_per_item(self):
        """55P03 is a held key, not a poisoned row — per-item splitting would
        re-block once per item. Re-raise; the poller-level retry reruns it."""
        from src.infrastructure.connectors._shared.inward_track_resolver import (
            persist_bulk_with_item_fallback,
        )

        class _LockNotAvailable(Exception):
            sqlstate = "55P03"

        uow = self._uow()
        calls: list[int] = []

        async def _contended(chunk):
            calls.append(len(chunk))
            raise _LockNotAvailable("key held by concurrent writer")

        with pytest.raises(_LockNotAvailable):
            _ = await persist_bulk_with_item_fallback(
                ["a", "b", "c"],
                uow,
                persist=_contended,
                write_key=lambda item: item,
                describe="test writes",
            )

        assert calls == [3], "one bulk attempt, no per-item re-blocking"
        assert uow.savepoint.call_count == 1

    async def test_empty_writes_touch_nothing(self):
        from src.infrastructure.connectors._shared.inward_track_resolver import (
            persist_bulk_with_item_fallback,
        )

        uow = self._uow()

        resolved, failed = await persist_bulk_with_item_fallback(
            [],
            uow,
            persist=self._persist(set()),
            write_key=lambda item: item,
            describe="test writes",
        )

        assert (resolved, failed) == ({}, set())
        assert uow.savepoint.call_count == 0


class TestReuseWriteFailure:
    """A reuse mapping the savepoint rolled back must not become a new canonical.

    The matcher had already accepted an existing canonical for the identifier,
    so routing it into ``_create_tracks_batch`` would mint a duplicate of a
    recording that exists — the losing side of two concurrent imports racing
    the unique constraint. It counts as failed instead.
    """

    async def test_failed_reuse_mapping_is_kept_out_of_creation(self):
        candidate = make_track(42, title="My Song", artist="Artist")
        uow = _make_reuse_uow({("my song", "artist"): candidate})
        uow.get_connector_repository().map_tracks_to_connectors.side_effect = (
            RuntimeError("duplicate key value violates unique constraint")
        )

        resolver = ReuseHintResolver({"id_a": ("Artist", "My Song")})
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["id_a"], uow, user_id="test-user"
        )

        assert result == {}
        assert resolver.create_calls == []
        assert metrics.reused == 0
        assert metrics.created == 0
        assert metrics.failed == 1

    async def test_unmatched_ids_still_reach_creation(self):
        """Only the failed identifier is withheld — the rest of the pass runs."""
        candidate = make_track(42, title="My Song", artist="Artist")
        uow = _make_reuse_uow({("my song", "artist"): candidate})
        uow.get_connector_repository().map_tracks_to_connectors.side_effect = (
            RuntimeError("boom")
        )

        resolver = ReuseHintResolver({"id_a": ("Artist", "My Song")})
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["id_a", "id_b"], uow, user_id="test-user"
        )

        assert resolver.create_calls == [["id_b"]]
        assert "id_b" in result
        assert "id_a" not in result
        assert metrics.created == 1
        assert metrics.failed == 1

    async def test_failure_does_not_leak_into_the_next_run(self):
        """The set is per-run: a later import must still be able to create."""
        candidate = make_track(42, title="My Song", artist="Artist")
        uow = _make_reuse_uow({("my song", "artist"): candidate})
        connector_repo = uow.get_connector_repository()
        connector_repo.map_tracks_to_connectors.side_effect = RuntimeError("boom")

        resolver = ReuseHintResolver({"id_a": ("Artist", "My Song")})
        _ = await resolver.resolve_to_canonical_tracks(
            ["id_a"], uow, user_id="test-user"
        )

        connector_repo.map_tracks_to_connectors.side_effect = None
        uow.get_track_repository().find_tracks_by_title_artist.return_value = {}
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["id_a"], uow, user_id="test-user"
        )

        assert resolver.create_calls[-1] == ["id_a"]
        assert "id_a" in result
        assert metrics.created == 1


class TestAllExisting:
    """When all IDs are found in mapping lookup, no creation should happen."""

    async def test_all_ids_found_skips_creation(self):
        track_a = make_track(1, "Song A")
        track_b = make_track(2, "Song B")

        uow = MagicMock()

        attach_resolution_recorder(uow)
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

        attach_resolution_recorder(uow)
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

        attach_resolution_recorder(uow)
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

        attach_resolution_recorder(uow)
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
        attach_resolution_recorder(uow)
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

        attach_resolution_recorder(uow)
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

        attach_resolution_recorder(uow)
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

    def test_metrics_defaults_redirects_and_fallbacks_to_zero(self):
        """Connectors without a redirect/fallback concept (e.g. Last.fm) get 0s."""
        metrics = TrackResolutionMetrics(existing=1, created=2, failed=0)
        assert metrics.redirects == 0
        assert metrics.fallbacks == 0

    async def test_base_resolver_leaves_redirects_and_fallbacks_at_zero(self):
        """The base InwardTrackResolver has no concept of redirects/fallbacks —
        only SpotifyInwardResolver's override populates them."""
        track = make_track(1, "Song")

        uow = MagicMock()

        attach_resolution_recorder(uow)
        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {}
        uow.get_connector_repository.return_value = connector_repo

        resolver = FakeInwardResolver(batch_results={"id_a": track})
        _, metrics = await resolver.resolve_to_canonical_tracks(
            ["id_a"], uow, user_id="test-user"
        )

        assert metrics.redirects == 0
        assert metrics.fallbacks == 0


class TestCanonicalReuseHook:
    """Canonical reuse matches existing tracks before creating new ones."""

    async def test_reused_tracks_skip_creation(self):
        """When canonical reuse finds existing tracks, track creation is skipped."""
        reused_track = make_track(10, "Reused Song")

        uow = MagicMock()

        attach_resolution_recorder(uow)
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

        attach_resolution_recorder(uow)
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

        attach_resolution_recorder(uow)
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

        attach_resolution_recorder(uow)
        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {}
        uow.get_connector_repository.return_value = connector_repo

        resolver = FakeInwardResolver(batch_results={"id_a": track})
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["id_a"], uow, user_id="test-user"
        )

        assert metrics.reused == 0
        assert metrics.created == 1


class PipelineResolver(WritePlanningResolver[str]):
    """Minimal planned-write pipeline implementor; payload is a bare string."""

    def __init__(self, writes: list[PlannedWrite[str]] | None = None):
        super().__init__()
        self.writes = writes or []
        self.persisted_batches: list[list[str]] = []

    @property
    def connector_name(self) -> str:
        return "fake"

    def _normalize_id(self, raw_id: str) -> str:
        return raw_id

    async def _create_tracks_batch(
        self,
        missing_ids: list[str],
        uow,
        *,
        user_id: str,
    ) -> dict[str, Track]:
        result, _failed = await self._persist_planned_writes(
            self.writes, uow, user_id=user_id
        )
        return result

    def _canonical_payload(self, write: PlannedWrite[str], *, user_id: str) -> Track:
        return make_track(1, title=write.payload)

    def _mapping_metadata(self, write: PlannedWrite[str]) -> dict[str, object]:
        return {"payload": write.payload}

    def _successor_assertion(
        self, write: PlannedWrite[str], track: Track
    ) -> SuccessorAssertion | None:
        if not (write.creates_canonical and write.requested_id_is_stale):
            return None
        return SuccessorAssertion(
            requested_id=write.requested_id,
            returned_id=write.current_id,
            detection="fake_pointer",
            track_id=track.id,
        )

    def _on_writes_persisted(self, writes) -> None:
        self.persisted_batches.append([write.requested_id for write in writes])


def _pipeline_uow():
    """UoW mock with track/connector repos and a permissive recorder wired."""
    uow = MagicMock()
    recorder = attach_resolution_recorder(uow)

    track_repo = AsyncMock()
    track_repo.find_tracks_by_isrcs.return_value = {}
    track_repo.save_track.return_value = make_track(1)

    async def _save_tracks(tracks):
        return [await track_repo.save_track(track) for track in tracks]

    track_repo.save_tracks.side_effect = _save_tracks
    uow.get_track_repository.return_value = track_repo

    connector_repo = AsyncMock()
    connector_repo.find_tracks_by_connectors.return_value = {}
    uow.get_connector_repository.return_value = connector_repo
    return uow, track_repo, connector_repo, recorder


def _create_write(
    requested_id: str, current_id: str | None = None
) -> PlannedWrite[str]:
    return PlannedWrite(
        requested_id=requested_id,
        current_id=current_id or requested_id,
        payload=f"payload:{requested_id}",
        match_method=MatchMethod.DIRECT_IMPORT,
        confidence=MatchMethod.DIRECT_IMPORT_CONFIDENCE,
    )


class TestPlanIsrcWrite:
    """The shared three-outcome ISRC arm: reuse / suspect review / create."""

    @staticmethod
    def _plan(existing_by_isrc, duration_ms=200_000):
        return plan_isrc_write(
            connector="fake",
            requested_id="id-1",
            current_id="id-1",
            payload="payload",
            duration_ms=duration_ms,
            isrc="USUM72309818",
            existing_by_isrc=existing_by_isrc,
            service_data={"title": "T", "isrc": "USUM72309818"},
        )

    def test_unclaimed_isrc_plans_a_plain_creation(self):
        write = self._plan({})

        assert write.creates_canonical
        assert write.review is None
        assert write.match_method == MatchMethod.DIRECT_IMPORT
        assert write.confidence == MatchMethod.DIRECT_IMPORT_CONFIDENCE
        assert write.primary is True

    def test_claimed_isrc_with_agreeing_duration_plans_a_reuse(self):
        owner = make_track(7, duration_ms=200_000)
        write = self._plan({"USUM72309818": owner})

        assert write.reuse_track is owner
        assert write.review is None
        assert write.match_method == MatchMethod.ISRC_MATCH
        assert write.confidence == MatchMethod.ISRC_MATCH_CONFIDENCE

    def test_suspect_duration_plans_a_review_creation(self):
        owner = make_track(7, duration_ms=200_000)
        write = self._plan({"USUM72309818": owner}, duration_ms=260_000)

        assert write.creates_canonical
        assert write.review is not None
        assert write.review.owner is owner
        assert write.review.service_data["isrc"] == "USUM72309818"
        assert write.match_method == MatchMethod.DIRECT_IMPORT
        assert write.confidence == MatchMethod.DIRECT_IMPORT_CONFIDENCE


class TestPlannedWritePipeline:
    """The shared ordered write path and its claim dedupe."""

    async def test_write_path_order_reviews_saves_mappings_events(self):
        order: list[str] = []
        review_write = PlannedWrite(
            requested_id="sus",
            current_id="sus",
            payload="payload:sus",
            match_method=MatchMethod.DIRECT_IMPORT,
            confidence=MatchMethod.DIRECT_IMPORT_CONFIDENCE,
            review=IsrcCollisionReview(owner=make_track(7), service_data={"isrc": "X"}),
        )
        substituting_write = _create_write("old", "new")
        resolver = PipelineResolver([review_write, substituting_write])
        uow, track_repo, connector_repo, recorder = _pipeline_uow()

        connector_repo.queue_isrc_collision_reviews.side_effect = lambda *a, **k: (
            order.append("reviews")
        )

        async def _save_tracks(tracks):
            order.append("save_tracks")
            return [make_track(i + 1, title=t.title) for i, t in enumerate(tracks)]

        track_repo.save_tracks.side_effect = _save_tracks
        connector_repo.map_tracks_to_connectors.side_effect = lambda *a, **k: (
            order.append("mappings")
        )
        recorder.record.side_effect = lambda *a, **k: order.append("events")

        result, _ = await resolver._persist_planned_writes(
            resolver.writes, uow, user_id="test-user"
        )

        assert order == ["reviews", "save_tracks", "mappings", "events"]
        assert set(result) == {"sus", "old"}
        # The collision review names the current id.
        collisions, service = (
            connector_repo.queue_isrc_collision_reviews.await_args.args[:2]
        )
        assert service == "fake"
        assert [c.connector_id for c in collisions] == ["sus"]
        # Post-savepoint bookkeeping saw the whole chunk, once.
        assert resolver.persisted_batches == [["sus", "old"]]

    async def test_two_writes_sharing_a_successor_fan_in_to_one_canonical(self):
        resolver = PipelineResolver([
            _create_write("old1", "new"),
            _create_write("old2", "new"),
        ])
        uow, track_repo, connector_repo, _ = _pipeline_uow()

        result, failed = await resolver._persist_planned_writes(
            resolver.writes, uow, user_id="test-user"
        )

        assert failed == set()
        # One create for the shared successor — both requested ids fan in.
        [payloads] = track_repo.save_tracks.await_args.args
        assert len(payloads) == 1
        assert result["old1"] is result["old2"]

        [specs] = connector_repo.map_tracks_to_connectors.await_args.args
        primaries = [s for s in specs if s.primary]
        assert [s.connector_id for s in primaries] == ["new"]
        stale_ids = sorted(s.connector_id for s in specs if not s.primary)
        assert stale_ids == ["old1", "old2"]
        assert all(
            s.match_method == MatchMethod.DIRECT_IMPORT_STALE_ID
            for s in specs
            if not s.primary
        )

    async def test_reuse_write_maps_requested_id_and_creates_nothing(self):
        held = make_track(7, title="Held Song")
        reuse_write = PlannedWrite(
            requested_id="id-1",
            current_id="id-1",
            payload="payload:id-1",
            match_method=MatchMethod.ISRC_MATCH,
            confidence=MatchMethod.ISRC_MATCH_CONFIDENCE,
            reuse_track=held,
        )
        resolver = PipelineResolver([reuse_write])
        uow, track_repo, connector_repo, recorder = _pipeline_uow()

        result, _ = await resolver._persist_planned_writes(
            resolver.writes, uow, user_id="test-user"
        )

        assert result["id-1"] is held
        [saved] = track_repo.save_tracks.await_args.args
        assert saved == []
        [specs] = connector_repo.map_tracks_to_connectors.await_args.args
        assert [s.connector_id for s in specs] == ["id-1"]
        assert specs[0].primary is True
        recorder.record.assert_not_awaited()


class TestWriteFailedAccounting:
    """Rolled-back writes are reported as ``write_failed``, not dead ids."""

    async def test_persist_failure_fills_write_failed_from_the_base(self):
        resolver = PipelineResolver([_create_write("id-1")])
        uow, _, connector_repo, _ = _pipeline_uow()
        connector_repo.map_tracks_to_connectors.side_effect = RuntimeError("boom")

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["id-1"], uow, user_id="test-user"
        )

        assert result == {}
        assert metrics.failed == 1
        assert metrics.write_failed == 1
        assert metrics.degraded_persists == 1

    async def test_write_failed_stays_zero_on_success(self):
        resolver = PipelineResolver([_create_write("id-1")])
        uow, _, _, _ = _pipeline_uow()

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["id-1"], uow, user_id="test-user"
        )

        assert set(result) == {"id-1"}
        assert metrics.created == 1
        assert metrics.write_failed == 0


class TestResolutionHintsSeam:
    """The base hint seam: hints flow to ``_begin_resolution``, inert by default."""

    async def test_hints_are_handed_to_begin_resolution(self):
        captured: dict[str, object] = {}

        class HintedResolver(FakeInwardResolver):
            def _begin_resolution(self, hints) -> None:
                captured.update(hints)

        track = make_track(1)
        uow = MagicMock()
        attach_resolution_recorder(uow)
        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {}
        uow.get_connector_repository.return_value = connector_repo

        resolver = HintedResolver(batch_results={"id_a": track})
        _ = await resolver.resolve_to_canonical_tracks(
            ["id_a"], uow, user_id="test-user", hints={"id_a": "evidence"}
        )

        assert captured == {"id_a": "evidence"}
