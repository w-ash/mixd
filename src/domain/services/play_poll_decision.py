"""Should we poll the recently-played API right now, and when again after that?

**This is not a freshness mechanism.** Demand triggers own freshness: a play
surface being read, an agent asking, a workflow about to filter on play history.
This module governs the *coverage heartbeat*, whose only job is stopping plays
ageing out of Spotify's ~50-item window between sessions — the window has no
paging, so anything that falls off is gone for good.

Every constant below follows from that framing, and from how fast the window
fills rather than from any staleness target. Only plays over 30s are recorded,
so 50 slots is ~3.3h of album listening but only ~37min of skip-heavy listening:

    continuous, 4-min tracks   200 min
    mixed, ~2-min average      100 min
    skip-heavy, just past 30s   37 min
    every track dropped at 31s  25 min   (theoretical worst case)

No single interval serves that spread, which is why ``apply_poll_result`` reads
how *full* each response came back rather than merely whether it was empty. A
user who starts skipping heavily is detected on the very next poll and clamped to
the floor, so the exposure is one interval rather than an entire idle stretch.

The other half of the framing is cost. A scale-to-zero database suspends after
5 minutes of query silence, so a compute woken every ``T`` bills ``min(5min, T)``
of every ``T``: ~17% at 30 minutes, ~4% at 2 hours, ~0.3% at 24 hours. The
stretch-on-empty is therefore a spending decision as much as a politeness one —
which only holds because the resulting interval is written back to the schedule
row, letting the scheduler sleep through it instead of waking to be told no.
"""

from datetime import datetime, timedelta
from typing import Final, Literal

from attrs import define

from src.domain.entities.operations import PollHealth
from src.domain.entities.shared import JsonDict
from src.domain.services.backoff import BackoffPolicy

# --- Cadence (starting points; revisit against real telemetry) ---------------

# Steady-state cadence while the user is actively listening — any productive poll
# returns here. 30 minutes is ~6.7x headroom for album listening and slightly
# under 1x for the pathological skip case; the fullness clamp covers the latter.
BASE_INTERVAL_SECONDS: Final = 30 * 60
# Never poll faster than this, even fully clamped. Matches the floor Android's
# WorkManager imposes on periodic background work, and keeps the duty cycle
# bounded at 33% in the worst case.
MIN_INTERVAL_SECONDS: Final = 15 * 60
# Ceiling when this channel is the user's ONLY live observer: a missed window is
# permanent loss, so the cap stays inside the window-turnover bound.
SOLE_OBSERVER_CAP_SECONDS: Final = 2 * 60 * 60
# Ceiling when Last.fm is also scrobbling. A missed window is recoverable from
# the other observer, so the poll only needs to keep the cursor warm and catch a
# day with no mixd activity at all. Lands on the same daily default that Merge
# and Plaid use for third-party data sync.
REDUNDANT_CAP_SECONDS: Final = 24 * 60 * 60

# How recently Last.fm must have scrobbled for us to trust it as a second
# observer. Generous: the question is "is this account still scrobbling at all",
# not "did they listen today".
REDUNDANCY_WINDOW: Final = timedelta(days=7)

# --- Demand + lease ----------------------------------------------------------

# A demand trigger polls only if the last check is older than this. Short enough
# that "open the app and see today's plays" works, long enough that a burst of
# page views doesn't become a burst of API calls.
DEMAND_MAX_AGE: Final = timedelta(minutes=10)
# How long a poll may hold the lease before another caller may reclaim it. Well
# above a normal poll (one API call plus resolution) and well below the base
# interval, so a dead process costs at most one skipped cycle.
CLAIM_TTL: Final = timedelta(minutes=15)

# --- Fullness control law ----------------------------------------------------

# Fraction of the window that counts as "near overflow" and halves the interval.
NEAR_FULL_RATIO: Final = 0.6

# --- Health ------------------------------------------------------------------

# Multiple of the effective interval past which a checkpoint reads as overdue.
# Slack rather than an exact deadline: a poll that lands a little late is normal
# (the scheduler batches, the API retries), and flagging that would train the
# user to ignore the signal.
HEALTH_SLACK_FACTOR: Final = 2

_POLICY: Final = BackoffPolicy(
    base_seconds=BASE_INTERVAL_SECONDS,
    cap_seconds=REDUNDANT_CAP_SECONDS,
)

type PollTrigger = Literal["schedule", "demand"]
# "claimed_elsewhere" is raised by the application-layer lease rather than by
# decide_poll, but it is the most frequent veto in practice and belongs in the
# same vocabulary — a consumer matching exhaustively must see it.
type VetoReason = Literal[
    "scope_missing", "not_due", "recently_polled", "claimed_elsewhere"
]


@define(frozen=True, slots=True)
class PollState:
    """Backoff state persisted on the checkpoint's ``poll_state`` JSONB.

    Read back through :meth:`from_json`, which tolerates anything: the column is
    schemaless so the policy can evolve without a migration, which only pays off
    if an older or malformed shape degrades to defaults instead of raising in a
    background task nobody is watching.
    """

    consecutive_empty: int = 0
    # Effective interval in seconds, carried forward so a stretch survives a
    # restart. Zero means "unset" — treated as the base interval.
    interval_seconds: int = 0
    last_trigger: PollTrigger | None = None
    # Set when a poll returned a completely full window, meaning plays may
    # already have aged out unrecoverably. Sticky until a non-full poll clears
    # it, so a gap that happened overnight is still visible in the morning.
    possible_gap: bool = False

    @classmethod
    def from_json(cls, raw: JsonDict | None) -> PollState:
        """Build from the stored JSONB, falling back to defaults field by field."""
        if not raw:
            return cls()
        return cls(
            consecutive_empty=_as_int(raw.get("consecutive_empty")),
            interval_seconds=_as_int(raw.get("interval_seconds")),
            last_trigger=_as_trigger(raw.get("last_trigger")),
            possible_gap=bool(raw.get("possible_gap", False)),
        )

    def to_json(self) -> JsonDict:
        """Serialise for the ``poll_state`` column."""
        return {
            "consecutive_empty": self.consecutive_empty,
            "interval_seconds": self.interval_seconds,
            "last_trigger": self.last_trigger,
            "possible_gap": self.possible_gap,
        }

    def effective_interval_seconds(self, *, cap_seconds: int) -> int:
        """The live cadence, clamped to the caller's redundancy-aware cap."""
        stored = self.interval_seconds or BASE_INTERVAL_SECONDS
        return max(MIN_INTERVAL_SECONDS, min(stored, cap_seconds))


def _as_int(value: object) -> int:
    """Non-negative int from untrusted JSON, else 0."""
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else 0
    )


def _as_trigger(value: object) -> PollTrigger | None:
    return value if value in ("schedule", "demand") else None


@define(frozen=True, slots=True)
class PollDecisionInputs:
    """Everything the decision needs, gathered in one read."""

    now: datetime
    trigger: PollTrigger
    # None when this channel has never been polled.
    last_polled_at: datetime | None
    state: PollState
    # False when the stored OAuth grant lacks the recently-played scope.
    has_scope: bool
    # Newest Last.fm scrobble we know of, or None if that channel is unused.
    lastfm_last_timestamp: datetime | None = None


@define(frozen=True, slots=True)
class PollDecision:
    """Whether to poll, and the parameters the claim must be made under."""

    should_poll: bool
    # Passed to try_claim_poll so the claim re-checks staleness atomically,
    # closing the gap between deciding and claiming.
    max_age: timedelta
    effective_interval_seconds: int
    # The redundancy-aware ceiling this decision was made under. Carried so the
    # caller folds the result against the same cap rather than calling `cap_for`
    # a second time — two derivations of one rule can drift apart.
    cap_seconds: int
    veto_reason: VetoReason | None = None


def has_redundant_observer(
    lastfm_last_timestamp: datetime | None, *, now: datetime
) -> bool:
    """True if Last.fm is scrobbling recently enough to cover a missed window.

    Keyed on an actual recent scrobble, not on the connector being linked: a
    token that exists but hasn't scrobbled in months is not a second observer,
    and treating it as one would quietly relax the cap on a user who is in fact
    relying on this channel alone.
    """
    if lastfm_last_timestamp is None:
        return False
    return now - lastfm_last_timestamp <= REDUNDANCY_WINDOW


def cap_for(inputs: PollDecisionInputs) -> int:
    """The redundancy-aware ceiling on this user's poll interval."""
    return (
        REDUNDANT_CAP_SECONDS
        if has_redundant_observer(inputs.lastfm_last_timestamp, now=inputs.now)
        else SOLE_OBSERVER_CAP_SECONDS
    )


def decide_poll(inputs: PollDecisionInputs) -> PollDecision:
    """Decide whether this trigger should poll now.

    Vetoes are silent by design. A missing scope already surfaces as a
    "re-connect needed" prompt on the connector card; turning every heartbeat
    into a failed run as well would bury the run log in noise describing one
    condition the user has already been told about.
    """
    cap = cap_for(inputs)
    interval = inputs.state.effective_interval_seconds(cap_seconds=cap)

    if not inputs.has_scope:
        return PollDecision(
            should_poll=False,
            max_age=DEMAND_MAX_AGE,
            effective_interval_seconds=interval,
            cap_seconds=cap,
            veto_reason="scope_missing",
        )

    if inputs.trigger == "demand":
        # Demand ignores the backoff entirely — someone is asking *now*. The only
        # gate is "did we just check", which stops a page of parallel reads
        # becoming a page of API calls.
        fresh = (
            inputs.last_polled_at is not None
            and inputs.now - inputs.last_polled_at < DEMAND_MAX_AGE
        )
        if fresh:
            return PollDecision(
                should_poll=False,
                max_age=DEMAND_MAX_AGE,
                effective_interval_seconds=interval,
                cap_seconds=cap,
                veto_reason="recently_polled",
            )
        return PollDecision(
            should_poll=True,
            max_age=DEMAND_MAX_AGE,
            effective_interval_seconds=interval,
            cap_seconds=cap,
        )

    max_age = timedelta(seconds=interval)
    if (
        inputs.last_polled_at is not None
        and inputs.now - inputs.last_polled_at < max_age
    ):
        return PollDecision(
            should_poll=False,
            max_age=max_age,
            effective_interval_seconds=interval,
            cap_seconds=cap,
            veto_reason="not_due",
        )
    return PollDecision(
        should_poll=True,
        max_age=max_age,
        effective_interval_seconds=interval,
        cap_seconds=cap,
    )


def apply_poll_result(
    state: PollState,
    *,
    trigger: PollTrigger,
    succeeded: bool,
    raw_plays: int,
    imported: int,
    window_limit: int,
    cap_seconds: int,
    resumed: bool = True,
    jitter_key: str = "",
) -> PollState:
    """Fold one poll's outcome into the next interval.

    Two counts answer two different questions, and conflating them is a real bug
    in both directions:

    - ``imported`` answers *was there new listening?* Rows that all conflict-skip
      are a re-read of the boundary play we already have, not fresh activity, so
      they must not reset the backoff.
    - ``raw_plays`` answers *how close to overflow was the window?* That is the
      data-loss signal, and it is meaningful even when every row was a duplicate.

    A failure holds the interval rather than stretching it: an outage says
    nothing about how much the user is listening, and stretching on it would let
    a transient failure quietly widen the window through which plays are lost.

    ``resumed`` is False for a first poll, one with no stored cursor to continue
    from. That case still tightens the cadence on a full window, but raises no
    gap: "plays fell out since we last looked" is meaningless when there was no
    last look, and a fresh connection legitimately finds the whole retained
    window new. Warning there would greet a successful first connect with a
    data-loss notice about data that was never ours to lose.

    ``jitter_key`` should be the user id. It only affects the stretch path, which
    is the one where every idle user converges on the same cap and would
    otherwise wake the database in lockstep.
    """
    current = state.effective_interval_seconds(cap_seconds=cap_seconds)

    if not succeeded:
        return _with_interval(state, current, trigger=trigger, cap_seconds=cap_seconds)

    near_full = raw_plays >= max(1, round(window_limit * NEAR_FULL_RATIO))
    saturated = raw_plays >= window_limit

    if saturated:
        # The window came back completely full: on a resumed poll it may already
        # have discarded plays before we read it, and nothing older survives
        # upstream, so re-polling cannot recover them — all we can do is tighten
        # the cadence and say so. A first poll tightens too (the user is clearly
        # listening faster than the base cadence assumes) but claims no gap.
        return _with_interval(
            state,
            MIN_INTERVAL_SECONDS,
            trigger=trigger,
            cap_seconds=cap_seconds,
            consecutive_empty=0,
            # None preserves; only a resumed poll may *raise* the flag. Passing
            # ``resumed`` directly would let an unresumed poll clear a gap an
            # earlier poll recorded — the flag is meant to survive until a
            # comfortably-under-capacity poll shows the backlog has drained.
            possible_gap=True if resumed else None,
        )

    if near_full:
        halved = max(MIN_INTERVAL_SECONDS, current // 2)
        return _with_interval(
            state, halved, trigger=trigger, cap_seconds=cap_seconds, consecutive_empty=0
        )

    if imported > 0:
        return _with_interval(
            state,
            BASE_INTERVAL_SECONDS,
            trigger=trigger,
            cap_seconds=cap_seconds,
            consecutive_empty=0,
            possible_gap=False,
        )

    consecutive = state.consecutive_empty + 1
    stretched = _POLICY.next_interval(consecutive, key=jitter_key)
    return _with_interval(
        state,
        min(stretched, cap_seconds),
        trigger=trigger,
        cap_seconds=cap_seconds,
        consecutive_empty=consecutive,
        possible_gap=False,
    )


def _with_interval(
    state: PollState,
    interval_seconds: int,
    *,
    trigger: PollTrigger,
    cap_seconds: int,
    consecutive_empty: int | None = None,
    possible_gap: bool | None = None,
) -> PollState:
    """Rebuild the state with a clamped interval and the fields that changed."""
    clamped = max(MIN_INTERVAL_SECONDS, min(interval_seconds, cap_seconds))
    return PollState(
        consecutive_empty=state.consecutive_empty
        if consecutive_empty is None
        else consecutive_empty,
        interval_seconds=clamped,
        last_trigger=trigger,
        possible_gap=state.possible_gap if possible_gap is None else possible_gap,
    )


def poll_health(
    *, now: datetime, last_polled_at: datetime | None, effective_interval_seconds: int
) -> PollHealth | None:
    """Classify a checkpoint's polling as healthy or overdue.

    Judged against the channel's *own* effective interval, never a fixed age.
    Under adaptive backoff a 20-hour-old check can be perfectly healthy (the
    daily floor for a user with Last.fm redundancy) while a 3-hour-old one is
    broken (past the sole-observer cap) — so a client cannot derive this from a
    timestamp alone, and the interval has to travel with the status.

    None when the channel has never been polled: that is "not started", which
    the caller renders differently from "was working and stopped".
    """
    if last_polled_at is None:
        return None
    deadline = timedelta(seconds=effective_interval_seconds * HEALTH_SLACK_FACTOR)
    return "healthy" if now - last_polled_at <= deadline else "overdue"
