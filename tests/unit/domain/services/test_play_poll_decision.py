"""Unit tests for the play-poll decision and its fullness control law.

The ``apply_poll_result`` cases are the data-loss guardrails: each one is a way
the poller could silently stop covering the ~50-play window while still looking
like it was working. They are asserted individually rather than through a single
happy path for that reason.
"""

from datetime import UTC, datetime, timedelta

import pytest

from src.domain.services.play_poll_decision import (
    BASE_INTERVAL_SECONDS,
    DEMAND_MAX_AGE,
    MIN_INTERVAL_SECONDS,
    REDUNDANT_CAP_SECONDS,
    SOLE_OBSERVER_CAP_SECONDS,
    PollDecisionInputs,
    PollState,
    apply_poll_result,
    cap_for,
    decide_poll,
    has_redundant_observer,
    poll_health,
)

_NOW = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
_WINDOW = 50


def _inputs(**overrides: object) -> PollDecisionInputs:
    base: dict[str, object] = {
        "now": _NOW,
        "trigger": "schedule",
        "last_polled_at": None,
        "state": PollState(),
        "has_scope": True,
        "lastfm_last_timestamp": None,
    }
    return PollDecisionInputs(**(base | overrides))  # pyright: ignore[reportArgumentType]


def _apply(state: PollState, **overrides: object):
    base: dict[str, object] = {
        "trigger": "schedule",
        "succeeded": True,
        "raw_plays": 0,
        "imported": 0,
        "window_limit": _WINDOW,
        "cap_seconds": SOLE_OBSERVER_CAP_SECONDS,
    }
    return apply_poll_result(state, **(base | overrides))  # pyright: ignore[reportArgumentType]


class TestRedundancy:
    def test_recent_scrobble_counts_as_a_second_observer(self) -> None:
        assert has_redundant_observer(_NOW - timedelta(days=2), now=_NOW) is True

    def test_stale_scrobble_does_not(self) -> None:
        # A linked-but-silent Last.fm is not a second observer. Treating it as
        # one would relax the cap for a user actually relying on this channel.
        assert has_redundant_observer(_NOW - timedelta(days=30), now=_NOW) is False

    def test_absent_scrobble_does_not(self) -> None:
        assert has_redundant_observer(None, now=_NOW) is False

    def test_cap_follows_redundancy(self) -> None:
        assert cap_for(_inputs()) == SOLE_OBSERVER_CAP_SECONDS
        redundant = _inputs(lastfm_last_timestamp=_NOW - timedelta(days=1))
        assert cap_for(redundant) == REDUNDANT_CAP_SECONDS


class TestDecideVetoes:
    def test_missing_scope_vetoes_every_trigger(self) -> None:
        for trigger in ("schedule", "demand"):
            decision = decide_poll(_inputs(trigger=trigger, has_scope=False))
            assert decision.should_poll is False
            assert decision.veto_reason == "scope_missing"

    def test_schedule_not_yet_due_is_vetoed(self) -> None:
        decision = decide_poll(
            _inputs(last_polled_at=_NOW - timedelta(minutes=5)),
        )
        assert decision.should_poll is False
        assert decision.veto_reason == "not_due"

    def test_schedule_due_polls(self) -> None:
        decision = decide_poll(
            _inputs(last_polled_at=_NOW - timedelta(minutes=31)),
        )
        assert decision.should_poll is True

    def test_never_polled_is_always_due(self) -> None:
        assert decide_poll(_inputs(last_polled_at=None)).should_poll is True


class TestDecideDemand:
    def test_demand_ignores_the_backoff(self) -> None:
        # Stretched to the cap, but someone is asking right now.
        stretched = PollState(interval_seconds=SOLE_OBSERVER_CAP_SECONDS)
        decision = decide_poll(
            _inputs(
                trigger="demand",
                state=stretched,
                last_polled_at=_NOW - timedelta(minutes=30),
            )
        )
        assert decision.should_poll is True

    def test_demand_is_gated_only_by_a_very_recent_poll(self) -> None:
        decision = decide_poll(
            _inputs(trigger="demand", last_polled_at=_NOW - timedelta(minutes=2))
        )
        assert decision.should_poll is False
        assert decision.veto_reason == "recently_polled"

    def test_demand_max_age_travels_to_the_claim(self) -> None:
        # The claim re-checks staleness atomically, so it must be handed the same
        # bound the decision used or the two can disagree under concurrency.
        decision = decide_poll(_inputs(trigger="demand"))
        assert decision.max_age == DEMAND_MAX_AGE


class TestFullnessControlLaw:
    def test_failure_holds_the_interval(self) -> None:
        # An outage says nothing about how much the user is listening. Stretching
        # on it would let a transient failure widen the loss window.
        state = PollState(interval_seconds=3600, consecutive_empty=2)
        result = _apply(state, succeeded=False)
        assert result.interval_seconds == 3600
        assert result.consecutive_empty == 2

    def test_empty_poll_stretches(self) -> None:
        result = _apply(PollState(), raw_plays=0, imported=0)
        assert result.consecutive_empty == 1
        assert result.interval_seconds > BASE_INTERVAL_SECONDS

    def test_repeated_empties_stretch_to_the_cap_not_past_it(self) -> None:
        state = PollState()
        for _ in range(20):
            state = _apply(state, raw_plays=0, imported=0)
        assert state.interval_seconds == SOLE_OBSERVER_CAP_SECONDS

    def test_redundant_user_stretches_further(self) -> None:
        state = PollState()
        for _ in range(20):
            state = _apply(
                state, raw_plays=0, imported=0, cap_seconds=REDUNDANT_CAP_SECONDS
            )
        assert state.interval_seconds == REDUNDANT_CAP_SECONDS

    def test_new_plays_reset_to_base(self) -> None:
        state = PollState(
            interval_seconds=SOLE_OBSERVER_CAP_SECONDS, consecutive_empty=5
        )
        result = _apply(state, raw_plays=3, imported=3)
        assert result.interval_seconds == BASE_INTERVAL_SECONDS
        assert result.consecutive_empty == 0

    def test_rows_that_all_conflict_skip_still_stretch(self) -> None:
        # raw_plays > 0 but imported == 0 is a re-read of the boundary play we
        # already hold — not new listening, so it must not reset the backoff.
        state = PollState(interval_seconds=3600, consecutive_empty=2)
        result = _apply(state, raw_plays=1, imported=0)
        assert result.consecutive_empty == 3
        assert result.interval_seconds > 3600

    def test_near_full_window_halves_the_interval(self) -> None:
        state = PollState(interval_seconds=7200)
        result = _apply(state, raw_plays=30, imported=30)
        assert result.interval_seconds == 3600

    def test_clamping_never_goes_below_the_floor(self) -> None:
        state = PollState(interval_seconds=MIN_INTERVAL_SECONDS)
        result = _apply(state, raw_plays=30, imported=30)
        assert result.interval_seconds == MIN_INTERVAL_SECONDS

    def test_full_window_floors_the_interval_and_flags_a_gap(self) -> None:
        # A saturated window may already have discarded plays. Nothing older
        # survives upstream, so the only responses are to tighten and to say so.
        state = PollState(interval_seconds=SOLE_OBSERVER_CAP_SECONDS)
        result = _apply(state, raw_plays=_WINDOW, imported=_WINDOW)
        assert result.interval_seconds == MIN_INTERVAL_SECONDS
        assert result.possible_gap is True

    def test_first_poll_tightens_but_claims_no_gap(self) -> None:
        """Caught by the first real run against a live account.

        A first poll adopts the whole retained window, so 50 of 50 is the
        expected onboarding result, not evidence of loss — there was no previous
        poll for plays to have fallen out between. Flagging it would greet a
        successful connect with a data-loss warning about data that was never
        ours. The cadence still tightens: the user is plainly listening faster
        than the base interval assumes.
        """
        result = _apply(PollState(), raw_plays=_WINDOW, imported=_WINDOW, resumed=False)
        assert result.interval_seconds == MIN_INTERVAL_SECONDS
        assert result.possible_gap is False

    def test_an_unresumed_poll_preserves_a_gap_it_did_not_raise(self) -> None:
        """A first poll must not *erase* a warning, only decline to raise one.

        Reachable when a cursor is cleared (a forced re-read, a reset
        connection) after an earlier poll recorded a gap: the next poll runs
        unresumed, and passing `resumed` straight through would wipe the only
        surface telling the user plays were lost.
        """
        gapped = _apply(PollState(), raw_plays=_WINDOW, imported=_WINDOW)
        assert gapped.possible_gap is True

        unresumed = _apply(gapped, raw_plays=_WINDOW, imported=_WINDOW, resumed=False)
        assert unresumed.possible_gap is True

    def test_gap_flag_is_sticky_until_a_normal_poll(self) -> None:
        gapped = _apply(PollState(), raw_plays=_WINDOW, imported=_WINDOW)
        # A near-full poll is still worrying — the flag must survive it.
        still = _apply(gapped, raw_plays=30, imported=30)
        assert still.possible_gap is True
        # A comfortably-under poll clears it.
        cleared = _apply(still, raw_plays=2, imported=2)
        assert cleared.possible_gap is False

    def test_stretch_is_jittered_per_user(self) -> None:
        """The jitter key must actually reach the backoff policy.

        Regression: the policy supported deterministic per-user jitter while
        ``apply_poll_result`` never passed a key, so every user stretched to the
        identical interval and the decorrelation was dead code. Mid-stretch (not
        at the cap, where values are clipped) is where the spread is visible.
        """
        partly = PollState(consecutive_empty=2)
        spread = {
            _apply(
                partly,
                raw_plays=0,
                imported=0,
                cap_seconds=REDUNDANT_CAP_SECONDS,
                jitter_key=f"user-{i}",
            ).interval_seconds
            for i in range(20)
        }
        assert len(spread) > 1

    def test_skip_heavy_user_is_clamped_within_one_interval(self) -> None:
        """The scenario the control law exists for.

        A user idles (interval stretches to the cap), then starts skipping
        heavily. The first poll after they resume comes back saturated, and the
        cadence must drop to the floor immediately — the exposure is that one
        interval, not the whole listening session.
        """
        state = PollState()
        for _ in range(20):
            state = _apply(state, raw_plays=0, imported=0)
        assert state.interval_seconds == SOLE_OBSERVER_CAP_SECONDS

        resumed = _apply(state, raw_plays=_WINDOW, imported=_WINDOW)
        assert resumed.interval_seconds == MIN_INTERVAL_SECONDS


class TestPollStateJson:
    def test_round_trips(self) -> None:
        state = PollState(
            consecutive_empty=3,
            interval_seconds=7200,
            last_trigger="demand",
            possible_gap=True,
        )
        assert PollState.from_json(state.to_json()) == state

    @pytest.mark.parametrize(
        "raw",
        [
            None,
            {},
            {"consecutive_empty": "three"},
            {"consecutive_empty": -1},
            {"consecutive_empty": True},
            {"interval_seconds": None},
            {"last_trigger": "telepathy"},
            {"unknown_key": 1},
        ],
    )
    def test_tolerates_anything(self, raw: dict[str, object] | None) -> None:
        # The column is schemaless so the policy can evolve without a migration.
        # That only pays off if a stale or malformed shape degrades to defaults
        # instead of raising inside a background task nobody is watching.
        state = PollState.from_json(raw)  # pyright: ignore[reportArgumentType]
        assert state.consecutive_empty == 0
        assert state.effective_interval_seconds(cap_seconds=REDUNDANT_CAP_SECONDS) == (
            BASE_INTERVAL_SECONDS
        )

    def test_stored_interval_is_clamped_to_the_live_cap(self) -> None:
        # A user who loses Last.fm redundancy carries a 24h interval in a world
        # where 2h is now the ceiling; the read must clamp rather than trust it.
        state = PollState(interval_seconds=REDUNDANT_CAP_SECONDS)
        assert (
            state.effective_interval_seconds(cap_seconds=SOLE_OBSERVER_CAP_SECONDS)
            == SOLE_OBSERVER_CAP_SECONDS
        )


class TestPollHealth:
    def test_never_polled_is_unknown(self) -> None:
        assert (
            poll_health(now=_NOW, last_polled_at=None, effective_interval_seconds=1800)
            is None
        )

    def test_within_slack_is_healthy(self) -> None:
        assert (
            poll_health(
                now=_NOW,
                last_polled_at=_NOW - timedelta(minutes=59),
                effective_interval_seconds=1800,
            )
            == "healthy"
        )

    def test_past_slack_is_overdue(self) -> None:
        assert (
            poll_health(
                now=_NOW,
                last_polled_at=_NOW - timedelta(minutes=61),
                effective_interval_seconds=1800,
            )
            == "overdue"
        )

    def test_health_is_relative_to_the_channel_interval(self) -> None:
        """Why the interval has to travel with the status payload.

        The same 20-hour-old check is healthy under the daily floor and broken
        under the sole-observer cap, so no client can derive this from a
        timestamp alone.
        """
        twenty_hours_ago = _NOW - timedelta(hours=20)
        assert (
            poll_health(
                now=_NOW,
                last_polled_at=twenty_hours_ago,
                effective_interval_seconds=REDUNDANT_CAP_SECONDS,
            )
            == "healthy"
        )
        assert (
            poll_health(
                now=_NOW,
                last_polled_at=twenty_hours_ago,
                effective_interval_seconds=SOLE_OBSERVER_CAP_SECONDS,
            )
            == "overdue"
        )
