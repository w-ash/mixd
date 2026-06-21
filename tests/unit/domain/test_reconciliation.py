"""Unit tests for the unified reconciliation model (SyncPlan + build_sync_plan).

Covers the connector-identifier orientation (the fix over the old UUID diff that
skipped a pull's not-yet-ingested tracks), the destructive-removal confirmation
gate, ordered no-op detection (reorders are real changes), and counts.
"""

from src.domain.entities.playlist_link import SyncDirection
from src.domain.playlist.reconciliation import SyncPlan, build_sync_plan


class TestBuildSyncPlanCounts:
    def test_push_adds_canonical_only_ids(self):
        # PUSH: current = remote (A, B), target = canonical (A, B, C) → add C.
        plan = build_sync_plan(
            direction=SyncDirection.PUSH,
            current_ids=["A", "B"],
            target_ids=["A", "B", "C"],
        )
        assert plan.direction == SyncDirection.PUSH
        assert plan.tracks_to_add == 1
        assert plan.tracks_to_remove == 0
        assert plan.tracks_unchanged == 2

    def test_pull_counts_remote_only_ids(self):
        # PULL: current = canonical (A), target = remote (A, B) → add B.
        plan = build_sync_plan(
            direction=SyncDirection.PULL,
            current_ids=["A"],
            target_ids=["A", "B"],
        )
        assert plan.tracks_to_add == 1
        assert plan.tracks_to_remove == 0

    def test_removes_count_current_only_ids(self):
        plan = build_sync_plan(
            direction=SyncDirection.PUSH,
            current_ids=["A", "B", "C"],
            target_ids=["A"],
        )
        assert plan.tracks_to_remove == 2


class TestNoop:
    def test_identical_ordered_lists_are_noop(self):
        plan = build_sync_plan(
            direction=SyncDirection.PULL,
            current_ids=["A", "B"],
            target_ids=["A", "B"],
        )
        assert plan.is_noop is True

    def test_reorder_is_not_a_noop(self):
        # Same set, different order — a reorder is a real change.
        plan = build_sync_plan(
            direction=SyncDirection.PULL,
            current_ids=["A", "B"],
            target_ids=["B", "A"],
        )
        assert plan.is_noop is False
        assert plan.tracks_to_add == 0
        assert plan.tracks_to_remove == 0


class TestSafetyGate:
    def test_destructive_removal_flags(self):
        # 20 → 2: >50% and >10 removals → flagged.
        plan = build_sync_plan(
            direction=SyncDirection.PUSH,
            current_ids=[f"t{i}" for i in range(20)],
            target_ids=["t0", "t1"],
        )
        assert plan.requires_confirmation is True
        assert plan.safety.flagged is True

    def test_small_removal_not_destructive(self):
        plan = build_sync_plan(
            direction=SyncDirection.PUSH,
            current_ids=[f"t{i}" for i in range(5)],
            target_ids=["t0", "t1", "t2"],
        )
        assert plan.requires_confirmation is False


class TestDuplicateIds:
    """Counts + the safety gate are multiset-based: duplicate tracks can't hide a
    destructive removal behind set-deduplication (the gate-bypass regression)."""

    def test_duplicate_wipe_is_flagged_and_counted(self):
        # PUSH: remote [A, B×11] → canonical [A] removes all 11 B copies. With
        # set() math this looked like 1-of-2 (under threshold); the multiset gate
        # sees 11-of-12 and requires confirmation.
        plan = build_sync_plan(
            direction=SyncDirection.PUSH,
            current_ids=["A", *(["B"] * 11)],
            target_ids=["A"],
        )
        assert plan.tracks_to_remove == 11
        assert plan.safety.total_current == 12
        assert plan.requires_confirmation is True

    def test_duplicate_additions_counted_per_occurrence(self):
        plan = build_sync_plan(
            direction=SyncDirection.PULL,
            current_ids=["A"],
            target_ids=["A", "B", "B"],
        )
        assert plan.tracks_to_add == 2
        assert plan.tracks_unchanged == 1


class TestDefaults:
    def test_default_plan_is_safe_and_empty(self):
        plan = SyncPlan(direction=SyncDirection.PULL)
        assert plan.is_noop is True
        assert plan.requires_confirmation is False
        assert plan.tracks_to_add == 0
        assert plan.tracks_to_remove == 0
