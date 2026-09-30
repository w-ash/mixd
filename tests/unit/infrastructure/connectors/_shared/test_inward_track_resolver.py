"""Tests for InwardTrackResolver base class.

Validates the shared 'resolve inward' pattern: mapping lookup for existing,
canonical reuse for unresolved (the domain planner under names-alone rules,
refusals recorded), and batch creation for the rest — plus the planned-write
pipeline (``WritePlanningResolver``): the probes-and-planner adapter, the
ordered write path, in-chunk folding, claim dedupe, and write-failure
accounting.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import create_matching_config
from src.domain.entities import ConnectorArtistCredit, Track
from src.domain.matching.canonical_resolution import (
    Create,
    DeferToReview,
    Reuse,
    TrackResolutionRules,
)
from src.domain.matching.recording_identity import RecordingDescription
from src.infrastructure.connectors._shared.inward_track_resolver import (
    InwardTrackResolver,
    PlannedWrite,
    ProviderAnswer,
    ReuseMetadata,
    TrackResolutionMetrics,
    WritePlanningResolver,
)
from src.infrastructure.connectors._shared.successor_resolution import (
    SuccessorAssertion,
)
from tests.fixtures import (
    attach_match_review_repo,
    attach_resolution_recorder,
    make_track,
)


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

    def _extract_reuse_metadata(self, identifier: str) -> ReuseMetadata | None:
        return None

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
        return ReuseMetadata(artist=artist, title=title, connector_id=identifier)

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
    """TrackResolutionMetrics' derived total and the base resolver's zero fields."""

    def test_metrics_total(self):
        metrics = TrackResolutionMetrics(existing=10, reused=3, created=5, failed=2)
        assert metrics.total == 20

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


class PipelineResolver(WritePlanningResolver[str]):
    """Minimal planned-write pipeline implementor; payload is a bare string.

    ``answers`` go through the real ``_plan_writes`` when set; ``writes``
    bypass planning and exercise the persist path alone.
    """

    def __init__(
        self,
        writes: list[PlannedWrite[str]] | None = None,
        answers: list[ProviderAnswer[str]] | None = None,
    ):
        super().__init__()
        self.writes = writes or []
        self.answers = answers or []
        self.persisted_batches: list[list[str]] = []

    @property
    def connector_name(self) -> str:
        return "fake"

    def _normalize_id(self, raw_id: str) -> str:
        return raw_id

    def _extract_reuse_metadata(self, identifier: str) -> ReuseMetadata | None:
        return None

    async def _create_tracks_batch(
        self,
        missing_ids: list[str],
        uow,
        *,
        user_id: str,
    ) -> dict[str, Track]:
        writes = self.writes + await self._plan_writes(
            self.answers, uow, user_id=user_id
        )
        result, _failed = await self._persist_planned_writes(
            writes, uow, user_id=user_id
        )
        return result

    def _canonical_payload(self, write: PlannedWrite[str], *, user_id: str) -> Track:
        # Every payload claims an ISRC; the base withholds a deferred one.
        return make_track(1, title=write.payload, isrc="USUM72309818")

    def _mapping_metadata(self, write: PlannedWrite[str]) -> dict[str, object]:
        return {"payload": write.payload}

    def _connector_credits(
        self, write: PlannedWrite[str]
    ) -> tuple[ConnectorArtistCredit, ...]:
        return (ConnectorArtistCredit(credited_name=write.payload),)

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
    review_repo = attach_match_review_repo(uow)

    track_repo = AsyncMock()
    track_repo.find_tracks_by_isrcs.return_value = {}
    track_repo.find_tracks_by_title_artist.return_value = {}
    track_repo.save_track.return_value = make_track(1)

    async def _save_tracks(tracks):
        return [await track_repo.save_track(track) for track in tracks]

    track_repo.save_tracks.side_effect = _save_tracks
    uow.get_track_repository.return_value = track_repo

    connector_repo = AsyncMock()
    connector_repo.find_tracks_by_connectors.return_value = {}
    uow.get_connector_repository.return_value = connector_repo
    return uow, track_repo, connector_repo, recorder, review_repo


_CREATION = TrackResolutionRules(create_matching_config()).creation(
    RecordingDescription("T", "A", 200_000)
)


def _create_write(
    requested_id: str, current_id: str | None = None
) -> PlannedWrite[str]:
    return PlannedWrite(
        requested_id=requested_id,
        current_id=current_id or requested_id,
        payload=f"payload:{requested_id}",
        match_method="direct_import",
        outcome=Create(strong_id=None, evidence=_CREATION),
    )


def _answer(
    requested_id: str,
    *,
    current_id: str | None = None,
    title: str = "T",
    artist: str = "A",
    duration_ms: int | None = 200_000,
    isrc: str | None = None,
    names_decide: bool = True,
) -> ProviderAnswer[str]:
    return ProviderAnswer(
        requested_id=requested_id,
        current_id=current_id or requested_id,
        payload=f"payload:{requested_id}",
        description=RecordingDescription(title, artist, duration_ms),
        isrc=isrc,
        names_decide=names_decide,
    )


class TestPlanWrites:
    """The shared adapter: probes, planner, one write per answer."""

    async def test_unclaimed_isrc_plans_a_plain_creation(self):
        resolver = PipelineResolver()
        uow, _, _, _, _ = _pipeline_uow()

        (write,) = await resolver._plan_writes(
            [_answer("id-1", isrc="USUM72309818")], uow, user_id="u"
        )

        assert write.creates_canonical
        assert not write.defers_to_review
        assert write.match_method == "direct_import"
        assert write.evidence.method == "direct"
        assert write.confidence == write.evidence.confidence
        assert write.primary is True

    async def test_claimed_isrc_with_agreeing_duration_plans_a_reuse(self):
        owner = make_track(7, title="T", artist="A", duration_ms=200_000)
        resolver = PipelineResolver()
        uow, track_repo, _, _, _ = _pipeline_uow()
        track_repo.find_tracks_by_isrcs.return_value = {"USUM72309818": owner}

        (write,) = await resolver._plan_writes(
            [_answer("id-1", isrc="USUM72309818")], uow, user_id="u"
        )

        assert isinstance(write.outcome, Reuse)
        assert write.outcome.canonical is owner
        assert not write.defers_to_review
        assert write.match_method == "isrc_match"
        assert write.evidence.zone == "accept"

    async def test_suspect_duration_plans_a_review_creation(self):
        owner = make_track(7, title="T", artist="A", duration_ms=200_000)
        resolver = PipelineResolver()
        uow, track_repo, _, _, _ = _pipeline_uow()
        track_repo.find_tracks_by_isrcs.return_value = {"USUM72309818": owner}

        (write,) = await resolver._plan_writes(
            [_answer("id-1", isrc="USUM72309818", duration_ms=260_000)],
            uow,
            user_id="u",
        )

        assert write.creates_canonical
        assert isinstance(write.outcome, DeferToReview)
        assert write.outcome.owner is owner
        assert write.outcome.review.method == "isrc_suspect"
        assert write.match_method == "direct_import"

    async def test_two_ids_sharing_one_isrc_fold_onto_a_leader(self):
        """Neither is persisted, so the second reuses the first's canonical."""
        resolver = PipelineResolver()
        uow, _, _, _, _ = _pipeline_uow()

        first, second = await resolver._plan_writes(
            [
                _answer("id-1", isrc="USUM72309818"),
                _answer("id-2", title="T (Remaster)", isrc="USUM72309818"),
            ],
            uow,
            user_id="u",
        )

        assert first.creates_canonical
        assert second.outcome.depends_on == "id-1"
        assert second.match_method == "isrc_match"

    async def test_names_never_decide_where_the_answer_says_so(self):
        owner = make_track(7, title="T", artist="A", duration_ms=200_000)
        resolver = PipelineResolver()
        uow, track_repo, _, _, _ = _pipeline_uow()
        track_repo.find_tracks_by_title_artist.return_value = {("t", "a"): owner}

        (write,) = await resolver._plan_writes(
            [_answer("id-1", names_decide=False)], uow, user_id="u"
        )

        assert write.creates_canonical
        track_repo.find_tracks_by_title_artist.assert_not_awaited()

    async def test_two_requested_ids_answered_by_one_current_id_are_one_answer(
        self,
    ):
        resolver = PipelineResolver()
        uow, _, _, _, _ = _pipeline_uow()

        writes = await resolver._plan_writes(
            [
                _answer("old1", current_id="new", isrc="USUM72309818"),
                _answer("old2", current_id="new", isrc="USUM72309818"),
            ],
            uow,
            user_id="u",
        )

        assert all(write.creates_canonical for write in writes)


class TestPlannedWritePipeline:
    """The shared ordered write path and its claim dedupe."""

    async def test_write_path_order_saves_mappings_reviews_events(self):
        order: list[str] = []
        owner = make_track(7, title="T", artist="A", duration_ms=200_000)
        resolver = PipelineResolver(
            [_create_write("old", "new")],
            [_answer("sus", isrc="USUM72309818", duration_ms=260_000)],
        )
        uow, track_repo, connector_repo, recorder, review_repo = _pipeline_uow()
        track_repo.find_tracks_by_isrcs.return_value = {"USUM72309818": owner}

        async def _save_tracks(tracks):
            order.append("save_tracks")
            return [make_track(i + 1, title=t.title) for i, t in enumerate(tracks)]

        track_repo.save_tracks.side_effect = _save_tracks
        connector_repo.map_tracks_to_connectors.side_effect = lambda *a, **k: (
            order.append("mappings")
        )
        review_repo.create_reviews_batch.side_effect = lambda reviews: (
            order.append("reviews"),
            reviews,
        )[1]
        recorder.record.side_effect = lambda *a, **k: order.append("events")

        result, _ = await resolver.resolve_to_canonical_tracks(
            ["old", "sus"], uow, user_id="test-user"
        )

        # The review names the connector-track row the mapping upsert writes,
        # so it follows the mappings.
        assert order == ["save_tracks", "mappings", "reviews", "events"]
        assert set(result) == {"sus", "old"}
        # The collision review names the owner and the current id's row.
        (review,) = review_repo.create_reviews_batch.await_args.args[0]
        assert review.track_id == owner.id
        assert review.connector_name == "fake"
        assert review.match_method == "isrc_suspect"
        # The deferred creation withheld the contested ISRC; the plain one kept its own.
        saved = {
            track.title: track.isrc
            for call in track_repo.save_tracks.await_args_list
            for track in call.args[0]
        }
        assert saved == {"payload:old": "USUM72309818", "payload:sus": None}
        # Post-savepoint bookkeeping saw the whole chunk, once.
        assert resolver.persisted_batches == [["old", "sus"]]

    async def test_two_writes_sharing_a_successor_fan_in_to_one_canonical(self):
        resolver = PipelineResolver([
            _create_write("old1", "new"),
            _create_write("old2", "new"),
        ])
        uow, track_repo, connector_repo, _, _ = _pipeline_uow()

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
            s.match_method == "direct_import_stale_id" for s in specs if not s.primary
        )

    async def test_reuse_write_maps_requested_id_and_creates_nothing(self):
        held = make_track(7, title="Held Song")
        reuse_write = PlannedWrite(
            requested_id="id-1",
            current_id="id-1",
            payload="payload:id-1",
            match_method="isrc_match",
            outcome=Reuse(evidence=_CREATION, canonical=held),
        )
        resolver = PipelineResolver([reuse_write])
        uow, track_repo, connector_repo, recorder, _ = _pipeline_uow()

        result, _ = await resolver._persist_planned_writes(
            resolver.writes, uow, user_id="test-user"
        )

        assert result["id-1"] is held
        [saved] = track_repo.save_tracks.await_args.args
        assert saved == []
        [specs] = connector_repo.map_tracks_to_connectors.await_args.args
        assert [s.connector_id for s in specs] == ["id-1"]
        assert specs[0].primary is True
        assert specs[0].confidence == _CREATION.confidence
        assert specs[0].confidence_evidence == _CREATION.evidence
        recorder.record.assert_not_awaited()

    async def test_a_held_write_only_caches_the_requested_id(self):
        """The current id keeps the live mapping it was found by.

        Asserting a second mapping for it would supersede the one that
        already describes the track, so a held write writes exactly one
        spec: the requested id's non-primary cache alias, whose method the
        ``STALE_ID_FOR`` map derives from the write's own.
        """
        held = make_track(7, title="Held Song")
        resolver = PipelineResolver(
            answers=[_answer("old", current_id="new", isrc="USUM72309818")]
        )
        uow, track_repo, connector_repo, _, _ = _pipeline_uow()

        async def _find(connections, *, user_id):
            _ = user_id
            return {(c, cid): held for c, cid in connections if cid == "new"}

        connector_repo.find_tracks_by_connectors.side_effect = _find

        (write,) = await resolver._plan_writes(
            resolver.answers, uow, user_id="test-user"
        )
        assert write.held is True
        assert write.match_method == "direct_import"

        result, _ = await resolver._persist_planned_writes(
            [write], uow, user_id="test-user"
        )

        assert result["old"] is held
        [saved] = track_repo.save_tracks.await_args.args
        assert saved == []
        [specs] = connector_repo.map_tracks_to_connectors.await_args.args
        assert [(s.connector_id, s.match_method) for s in specs] == [
            ("old", "direct_import_stale_id")
        ]
        assert specs[0].primary is False

    async def test_one_chunk_two_ids_one_isrc_is_one_canonical_two_mappings(self):
        """The in-batch twin folds onto its leader before ``save_tracks``
        can refuse the second claim on the same identity key."""
        resolver = PipelineResolver(
            answers=[
                _answer("id-1", isrc="USUM72309818"),
                _answer("id-2", title="T (Remaster)", isrc="USUM72309818"),
            ]
        )
        uow, track_repo, connector_repo, _, _ = _pipeline_uow()
        saved = make_track(9, title="T", artist="A", isrc="USUM72309818")
        track_repo.save_track.return_value = saved

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["id-1", "id-2"], uow, user_id="test-user"
        )

        assert result["id-1"] is saved
        assert result["id-2"] is saved
        assert metrics.created == 2
        assert metrics.failed == 0
        track_repo.save_track.assert_awaited_once()
        [specs] = connector_repo.map_tracks_to_connectors.await_args.args
        assert {s.connector_id: s.match_method for s in specs} == {
            "id-1": "direct_import",
            "id-2": "isrc_match",
        }
        assert all(s.track is saved for s in specs)

    async def test_a_suspect_in_batch_collision_is_reviewed_against_its_leader(
        self,
    ):
        """Neither is persisted when planned, so the review waits for the
        leader's row: the twin creates without the ISRC and asks a person."""
        resolver = PipelineResolver(
            answers=[
                _answer("leader", isrc="USUM72309818", duration_ms=200_000),
                _answer("twin", isrc="USUM72309818", duration_ms=260_000),
            ]
        )
        uow, track_repo, _, _, review_repo = _pipeline_uow()
        leader_track = make_track(1, title="T", isrc="USUM72309818")
        twin_track = make_track(2, title="T")
        track_repo.save_track.side_effect = [leader_track, twin_track]

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["leader", "twin"], uow, user_id="test-user"
        )

        assert result["leader"] is leader_track
        assert result["twin"] is twin_track
        assert metrics.created == 2
        saved = [call.args[0] for call in track_repo.save_track.await_args_list]
        assert [track.isrc for track in saved] == ["USUM72309818", None]
        (review,) = review_repo.create_reviews_batch.await_args.args[0]
        assert review.track_id == leader_track.id
        assert review.match_method == "isrc_suspect"

    async def test_a_follower_fails_when_its_leader_is_rolled_back(self):
        """Creating one for it instead would write the duplicate the fold prevents."""
        resolver = PipelineResolver(
            answers=[
                _answer("leader", isrc="USUM72309818"),
                _answer("follower", isrc="USUM72309818"),
            ]
        )
        uow, _, connector_repo, _, _ = _pipeline_uow()

        async def _refuse_the_creation(specs, **_kwargs):
            if any(spec.match_method == "direct_import" for spec in specs):
                raise RuntimeError("deadlock detected")
            return [spec.track for spec in specs]

        connector_repo.map_tracks_to_connectors.side_effect = _refuse_the_creation

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["leader", "follower"], uow, user_id="test-user"
        )

        assert result == {}
        assert metrics.created == 0
        assert metrics.failed == 2
        assert metrics.write_failed == 2

    async def test_a_contested_creation_fails_when_its_leader_is_rolled_back(
        self,
    ):
        """Persisted alone it would hold neither the ISRC nor the review that
        decides who keeps it — and nothing would ever ask the question."""
        resolver = PipelineResolver(
            answers=[
                _answer("leader", isrc="USUM72309818", duration_ms=200_000),
                _answer("twin", isrc="USUM72309818", duration_ms=260_000),
            ]
        )
        uow, track_repo, _, _, review_repo = _pipeline_uow()

        async def _refuse_the_leader(tracks):
            if any(track.isrc == "USUM72309818" for track in tracks):
                raise RuntimeError("identity key claimed by a concurrent import")
            return [make_track(2, title="T") for _ in tracks]

        track_repo.save_tracks.side_effect = _refuse_the_leader

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["leader", "twin"], uow, user_id="test-user"
        )

        assert result == {}
        assert metrics.created == 0
        assert metrics.failed == 2
        assert metrics.write_failed == 2
        review_repo.create_reviews_batch.assert_not_awaited()
        # The chunk, then the leader alone; the twin's own per-item retry
        # never reached ``save_tracks`` — the guard fails it before an
        # ISRC-less row could land.
        saved = [
            [track.isrc for track in call.args[0]]
            for call in track_repo.save_tracks.await_args_list
        ]
        assert saved == [["USUM72309818", None], ["USUM72309818"]]


class TestReuseRefusalEvents:
    """A priced-but-refused reuse candidate leaves a ``rejected`` event."""

    async def test_a_candidate_under_the_title_floor_is_recorded(self):
        candidate = make_track(42, title="My Song (Live at Wembley)", artist="Artist")
        uow = _make_reuse_uow({("my song", "artist"): candidate})

        resolver = ReuseHintResolver({"id_a": ("Artist", "My Song")})
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["id_a"], uow, user_id="test-user"
        )

        assert metrics.reused == 0
        assert "id_a" in result
        (call,) = uow.get_resolution_recorder().record.await_args_list
        (event,) = call.args[0]
        assert event.event_type == "rejected"
        assert event.connector_name == "fake"
        assert event.track_id == candidate.id
        assert event.confidence == 100
        assert event.score == 100
        assert event.zone == "reject"
        assert event.payload == {
            "connector_id": "id_a",
            "title_similarity": 0.6,
            "title_threshold": 0.9,
        }

    async def test_a_fold_onto_an_in_chunk_leader_still_records_its_refusal(self):
        """Two ids with one name and one near-miss candidate: the second folds
        onto the first, and each still leaves its own ``rejected`` event."""
        candidate = make_track(42, title="My Song (Live at Wembley)", artist="Artist")
        uow = _make_reuse_uow({("my song", "artist"): candidate})

        resolver = ReuseHintResolver({
            "id_a": ("Artist", "My Song"),
            "id_b": ("Artist", "My Song"),
        })
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["id_a", "id_b"], uow, user_id="test-user"
        )

        assert metrics.reused == 0
        assert set(result) == {"id_a", "id_b"}
        (call,) = uow.get_resolution_recorder().record.await_args_list
        events = call.args[0]
        assert [event.event_type for event in events] == ["rejected", "rejected"]
        assert {event.track_id for event in events} == {candidate.id}
        assert {event.payload["connector_id"] for event in events} == {"id_a", "id_b"}

    async def test_an_accepted_reuse_leaves_no_event(self):
        candidate = make_track(42, title="My Songs", artist="Artist")
        uow = _make_reuse_uow({("my song", "artist"): candidate})

        resolver = ReuseHintResolver({"id_a": ("Artist", "My Song")})
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["id_a"], uow, user_id="test-user"
        )

        assert result["id_a"] is candidate
        assert metrics.reused == 1
        uow.get_resolution_recorder().record.assert_not_awaited()


class TestWriteFailedAccounting:
    """Rolled-back writes are reported as ``write_failed``, not dead ids."""

    async def test_persist_failure_fills_write_failed_from_the_base(self):
        resolver = PipelineResolver([_create_write("id-1")])
        uow, _, connector_repo, _, _ = _pipeline_uow()
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
        uow, _, _, _, _ = _pipeline_uow()

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
