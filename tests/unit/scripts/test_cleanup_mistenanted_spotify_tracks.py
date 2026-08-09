"""Guard logic of the mistenancy repair script (v0.10.2.9).

The script itself is a one-shot prod repair and is never executed here; these
tests pin the separable pure pieces its safety rests on — the window hard
guard that keeps the pre-incident 'default' residue untouched, the tenancy
posture of the phase-3 still-pointing counts (per-tenant predicated vs the
deliberately unpredicated third-tenant guard), the refusal message contract,
and the window-start parsing. Id batching is stdlib ``itertools.batched`` and
is not re-tested here.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from scripts.cleanup_mistenanted_spotify_tracks import (
    _pointing_count_stmt,
    assert_all_inside_window,
    format_foreign_tenant_refusal,
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


class TestForeignTenantRefusal:
    """The refusal must name the tenants found and the per-tenant remedy."""

    def test_names_tenants_count_and_rerun_remedy(self):
        message = format_foreign_tenant_refusal(7, ["bob", "carol"], "alice")

        assert "7 connector_plays" in message
        assert "['bob', 'carol']" in message
        assert "'alice'" in message
        # Phase 2's by-design scope and the operator's next step.
        assert "only --user's pointers by design" in message
        assert "Re-run this script with --user <tenant>" in message


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
