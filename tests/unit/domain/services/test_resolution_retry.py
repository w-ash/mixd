"""Tests for the resolution negative-cache/debounce constants and helper.

``next_no_match_check`` is a thin wrapper over the shared ``BackoffPolicy`` —
these tests pin the curve's day 0/1/5 values (base, double, cap) rather than
re-testing ``BackoffPolicy`` itself (covered by ``test_backoff.py``).
"""

from src.domain.services.resolution_retry import (
    DEATH_DEBOUNCE_FAILURES,
    DEATH_DEBOUNCE_MIN_SPAN_SECONDS,
    NO_MATCH_BACKOFF,
    SUSPECT_RECHECK_SECONDS,
    next_no_match_check,
)

_ONE_DAY = 86_400


class TestNoMatchBackoffCurve:
    def test_day_zero_is_base(self) -> None:
        # key="" disables jitter (src/domain/services/backoff.py) so these
        # assertions can be exact rather than approximate.
        assert next_no_match_check(0, key="") == _ONE_DAY

    def test_day_one_is_double_the_base(self) -> None:
        assert next_no_match_check(1, key="") == _ONE_DAY * 2

    def test_day_five_hits_the_cap(self) -> None:
        # base * 2**5 == 32 days == the configured cap exactly.
        assert next_no_match_check(5, key="") == 32 * _ONE_DAY

    def test_beyond_five_stays_at_the_cap(self) -> None:
        assert next_no_match_check(20, key="") == 32 * _ONE_DAY

    def test_matches_the_policy_directly(self) -> None:
        assert next_no_match_check(3, key="") == NO_MATCH_BACKOFF.next_interval(
            3, key=""
        )


class TestDebounceConstants:
    def test_death_debounce_requires_three_failures(self) -> None:
        assert DEATH_DEBOUNCE_FAILURES == 3

    def test_death_debounce_spans_nine_days(self) -> None:
        assert DEATH_DEBOUNCE_MIN_SPAN_SECONDS == 9 * _ONE_DAY

    def test_suspect_recheck_is_three_days(self) -> None:
        assert SUSPECT_RECHECK_SECONDS == 3 * _ONE_DAY

    def test_suspect_recheck_is_shorter_than_the_death_debounce_span(self) -> None:
        # Suspicion leans in (recheck soon) while the death debounce still
        # waits out its full span before writing id_dead — module docstring.
        assert SUSPECT_RECHECK_SECONDS < DEATH_DEBOUNCE_MIN_SPAN_SECONDS
