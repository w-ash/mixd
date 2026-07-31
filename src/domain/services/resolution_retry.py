"""Negative-cache and identity-debounce constants for resolution (v0.10.2).

Two clocks, never conflated (memo §10.5-10.6):

- **Absence backs off.** A ``no_match`` — a connector track we searched for a
  candidate on and found none — retries on a doubling curve
  (``NO_MATCH_BACKOFF``): nothing changed since the last look, so looking again
  sooner buys nothing but API quota. Counter-free at read time, exactly like
  the play poller's ``BackoffPolicy`` (``src.domain.services.play_poll_decision``):
  state lives in two timestamps, not an incrementing field.
- **Death is derived, not tracked.** An id that has missed
  ``DEATH_DEBOUNCE_FAILURES`` times over at least
  ``DEATH_DEBOUNCE_MIN_SPAN_SECONDS`` *looks* dead — read off the same
  backoff row, so there is no second counter to keep in step. Both halves
  are required: a count alone condemns an id that flickered inside one
  import, an age alone condemns a single old failure.
- **Success resets immediately**, on either clock — no hysteresis on recovery.

All three constants below are starting points, to revisit against real
telemetry once resolution runs at scale.
"""

from typing import Final

from src.domain.services.backoff import BackoffPolicy

# No-match retry curve: 1 day doubling to a 32-day cap, the ListenBrainz
# `check_again` shape verified verbatim (memo §10.5). Starting point, revisit
# against telemetry.
NO_MATCH_BACKOFF: Final = BackoffPolicy(base_seconds=86_400, cap_seconds=32 * 86_400)

# Consecutive failed re-checks required before a live mapping is written as
# `id_dead`. Starting point, revisit against telemetry.
DEATH_DEBOUNCE_FAILURES: Final = 3
# ...and those failures must span at least this many seconds (9 days) before
# they count as death rather than a transient blip — the measured Spotify
# transient band is seconds-to-minutes, so this is deliberately conservative
# and the first candidate to tighten once telemetry exists. Starting point,
# revisit against telemetry.
DEATH_DEBOUNCE_MIN_SPAN_SECONDS: Final = 9 * 86_400


def next_no_match_check(consecutive_misses: int, *, key: str = "") -> int:
    """Seconds until a `no_match` candidate search should be retried.

    Thin pure wrapper over :class:`BackoffPolicy` so callers depend on this
    module's vocabulary (``consecutive_misses``) rather than the generic
    backoff shape directly. ``key`` should be the connector-track id or
    similar stable identifier — see :mod:`src.domain.services.backoff` for why
    jitter is deterministic rather than random.
    """
    return NO_MATCH_BACKOFF.next_interval(consecutive_misses, key=key)
