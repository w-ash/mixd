"""Guard logic of the mistenancy repair script (v0.10.2.9).

The script itself is a one-shot prod repair and is never executed here; these
tests pin the separable pure pieces its safety rests on — the window hard
guard that keeps the pre-incident 'default' residue untouched, the tenancy
posture of the phase-3 still-pointing counts (per-tenant predicated vs the
deliberately unpredicated third-tenant guard), the three refusal message
contracts, and the window-start parsing. Id batching is stdlib
``itertools.batched`` and is not re-tested here.

The phase-3 statements that actually touch rows are pinned in
``tests/integration/test_mistenancy_repair_scripts.py`` — under migration 051's
RESTRICT FKs a delete's *order* is the correctness property, and only a real
database can show it.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from scripts.cleanup_mistenanted_spotify_tracks import (
    DEFAULT_WINDOW_START,
    _foreign_track_play_count_stmt,
    _pointing_count_stmt,
    assert_all_inside_window,
    format_external_supersession_refusal,
    format_foreign_tenant_refusal,
    format_mapping_history_refusal,
    parse_window_start,
)

_WINDOW = datetime(2026, 8, 8, tzinfo=UTC)


class TestWindowGuard:
    """A single pre-window row aborts the run before anything is written."""

    def test_rows_at_or_after_the_window_pass(self):
        assert_all_inside_window(
            [_WINDOW, _WINDOW + timedelta(seconds=1), _WINDOW + timedelta(days=3)],
            _WINDOW,
        )

    def test_an_empty_candidate_set_passes(self):
        assert_all_inside_window([], _WINDOW)

    def test_one_pre_window_row_aborts(self):
        with pytest.raises(RuntimeError, match="1 candidate rows predate"):
            assert_all_inside_window(
                [_WINDOW, _WINDOW - timedelta(microseconds=1)], _WINDOW
            )

    def test_every_stray_row_is_counted_in_the_abort(self):
        stray = [_WINDOW - timedelta(days=n) for n in (1, 2, 3)]
        with pytest.raises(RuntimeError, match="3 candidate rows predate"):
            assert_all_inside_window([*stray, _WINDOW], _WINDOW)


class TestPointingCountTenancy:
    """The phase-3 counts' tenancy posture is the third-tenant fix itself.

    A per-tenant count MUST carry the ``user_id`` predicate (exact under
    RLS); the third-tenant guard MUST NOT (only an unpredicated statement,
    run by the BYPASSRLS owner, can see a tenant the operator never named).
    """

    @staticmethod
    def _sql(tenant: str | None) -> str:
        stmt = _pointing_count_stmt(tenant)([uuid4(), uuid4()])
        return str(stmt.compile(compile_kwargs={"literal_binds": False}))

    def test_per_tenant_count_is_predicated_on_user_id(self):
        assert "connector_plays.user_id" in self._sql("alice")

    def test_third_tenant_guard_count_has_no_user_id_predicate(self):
        sql = self._sql(None)
        assert "connector_plays.user_id" not in sql
        assert "resolved_track_id" in sql


class TestForeignTrackPlayCount:
    """track_plays cascade too, and phase 2 has no pointer to reset there.

    The guard must exclude ``--user``'s own rows (those are the import residue
    the repair means to delete) while staying open to every other tenant.
    """

    @staticmethod
    def _sql(user: str) -> str:
        stmt = _foreign_track_play_count_stmt(user)([uuid4(), uuid4()])
        return str(stmt.compile(compile_kwargs={"literal_binds": False}))

    def test_counts_track_plays_excluding_the_named_user(self):
        sql = self._sql("alice")

        assert "FROM track_plays" in sql
        assert "track_plays.user_id !=" in sql

    def test_is_not_scoped_to_a_single_foreign_tenant(self):
        """Only an ``!=`` predicate can see a tenant the operator never named."""
        assert "track_plays.user_id =" not in self._sql("alice").replace("!=", "")


class TestForeignTenantRefusal:
    """The refusal must name the tenants found and BOTH remedies."""

    def test_names_tenants_count_and_rerun_remedy(self):
        message = format_foreign_tenant_refusal(7, ["bob", "carol"], "alice")

        assert "7 play rows" in message
        assert "['bob', 'carol']" in message
        assert "'alice'" in message
        # Phase 2's by-design scope and the operator's next step.
        assert "only --user's connector_plays pointers by design" in message
        assert "re-run this script with --user <tenant> --apply" in message

    def test_names_the_reown_script_as_the_other_remedy(self):
        """A real user's plays pointing here is finding C3, not residue —
        deleting is wrong and re-owning the track is the fix."""
        message = format_foreign_tenant_refusal(7, ["bob"], "alice")

        assert "scripts/reown_cross_tenant_tracks.py" in message


class TestMappingHistoryRefusal:
    """Deleting mapping history must be consented to, never incidental."""

    def test_states_the_deletion_the_orphaned_events_and_the_flag(self):
        named = [uuid4(), uuid4()]
        message = format_mapping_history_refusal(9, named)

        assert "delete 9 track_mappings rows outright" in message
        assert f"{len(named)} of them are named by a resolution_events" in message
        assert str(named[0]) in message
        assert "--acknowledge-mapping-history-loss" in message

    def test_elides_long_id_lists_without_hiding_the_total(self):
        named = [uuid4() for _ in range(8)]
        message = format_mapping_history_refusal(8, named)

        assert "8 of them are named" in message
        assert "(+3 more)" in message


class TestExternalSupersessionRefusal:
    """A supersession pointer from outside the set is a hard stop.

    Nulling it would turn a real supersession into a
    retirement-with-no-replacement — the ambiguity migration 051 closed.
    """

    def test_names_the_referrers_and_why_nulling_is_not_an_option(self):
        referrers = [uuid4()]
        message = format_external_supersession_refusal(referrers)

        assert "1 track_mappings rows OUTSIDE the candidate set" in message
        assert str(referrers[0]) in message
        assert "retirement with no replacement" in message


class TestDefaultWindowBound:
    """The default bound of a destructive script is a safety property.

    It was moved 2026-08-08 → 2026-07-27 once prod showed the old value sat
    *after* every mistenanted row, so a default run reported "nothing to
    repair" while the damage stood. The margin below is what stops the next
    widening from reaching the 2026-05-10 residue the bound exists to protect.
    """

    def test_the_default_excludes_the_local_dev_residue(self):
        residue_created_at = datetime(2026, 5, 10, tzinfo=UTC)

        assert parse_window_start(DEFAULT_WINDOW_START) > residue_created_at

    def test_the_default_still_reaches_the_mistenanted_rows(self):
        """Prod-verified: the mistenanted output spans 2026-07-27 → 08-06."""
        earliest_mistenanted = datetime(2026, 7, 27, tzinfo=UTC)

        assert parse_window_start(DEFAULT_WINDOW_START) <= earliest_mistenanted


class TestWindowStartParsing:
    def test_zulu_suffix_parses_as_utc(self):
        assert parse_window_start("2026-08-08T00:00:00Z") == _WINDOW

    def test_explicit_offset_is_preserved(self):
        parsed = parse_window_start("2026-08-08T02:00:00+02:00")

        assert parsed == _WINDOW

    def test_a_naive_value_is_read_as_utc(self):
        """tracks.created_at is timezone-aware; a naive bound must not crash
        the comparison nor silently shift the window."""
        assert parse_window_start("2026-08-08T00:00:00") == _WINDOW
