"""Schedulable sync targets and their dispatch — one source of truth.

A background-sync schedule names a ``"service:entity"`` target. ``SYNC_TARGETS``
is the single enumeration: it maps each target to how it runs, what to call it,
whether a user may schedule it, and — for adaptive targets — the hooks that
decide whether a given trigger should run at all. The schedulable set and the
validator both derive from it, so adding a connector is a one-entry edit here.

**Dispatchable is not the same as user-schedulable.** The adaptive play poller
owns its own cadence: it rewrites ``interval_minutes`` on its schedule row after
every poll so the scheduler sleeps through a backed-off interval instead of
waking to be told no. There is exactly one schedule row per (user, sync_target),
so a user editing that row through the generic daily/weekly picker would silently
convert it to a fixed cadence and switch the adaptivity off. Marking the target
``user_schedulable=False`` keeps it out of ``validate_sync_target`` — and
therefore out of the upsert route and the assistant's tool enums — while leaving
it fully dispatchable by the scheduler.
"""

from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timedelta
from typing import Final

from attrs import define

from src.application.use_cases.import_play_history import run_import
from src.application.use_cases.sync_likes import (
    run_lastfm_likes_export,
    run_spotify_likes_import,
)
from src.domain.entities.operations import OperationResult
from src.domain.services.play_poll_decision import PollTrigger

# Re-exported from domain rather than redeclared: the value crosses this seam
# into apply_poll_result, so two copies could drift and accept a kind the
# other cannot represent, with no type error at the boundary.
type PollTriggerKind = PollTrigger


@define(frozen=True, slots=True)
class TriggerContext:
    """Why and when a sync target is being asked to run."""

    user_id: str
    trigger: PollTriggerKind
    now: datetime
    # Demand callers may require a tighter freshness bound than the target's own
    # cadence would apply. None means "use the target's own judgement".
    max_age: timedelta | None = None


@define(frozen=True, slots=True)
class PollClaim:
    """A target's verdict on whether this trigger may proceed.

    ``payload`` is opaque to the runner and round-tripped untouched to the
    matching ``finish_poll``. The two hooks are always supplied together by the
    same target, so it can carry whatever that target needs (the pre-poll backoff
    state, the cap in force) without the descriptor knowing any target's shape.
    """

    granted: bool
    veto_reason: str | None = None
    payload: object = None


@define(frozen=True, slots=True)
class PollOutcome:
    """What the run produced, handed back to the target to fold into its state."""

    context: TriggerContext
    claim: PollClaim
    succeeded: bool
    result: object = None


type SyncRunner = Callable[[str], Awaitable[object]]
type PollBeginHook = Callable[[TriggerContext], Awaitable[PollClaim]]
type PollFinishHook = Callable[[PollOutcome], Awaitable[None]]


@define(frozen=True, slots=True)
class SyncTargetSpec:
    """Everything the runner and the interfaces need to know about one target."""

    # Human-readable name — the single source of display labels, consumed by the
    # API list read-model and the CLI schedule table.
    label: str
    run: SyncRunner
    # False for targets whose cadence is managed for the user (see module docs).
    user_schedulable: bool = True
    # Supplied as a pair, or not at all. A target with no hooks always runs.
    try_begin_poll: PollBeginHook | None = None
    finish_poll: PollFinishHook | None = None


def _play_poll_hooks() -> tuple[PollBeginHook, PollFinishHook]:
    """Resolve the play-poll hooks lazily.

    Function-scoped so this module stays importable from the interface layer
    without dragging in the policy's repository and token-storage dependencies —
    the same narrow-edge shape used for the metric-config provider.
    """
    from src.application.services import play_poll_policy

    return play_poll_policy.try_begin_poll, play_poll_policy.finish_poll


async def _begin_play_poll(context: TriggerContext) -> PollClaim:
    begin, _ = _play_poll_hooks()
    return await begin(context)


async def _finish_play_poll(outcome: PollOutcome) -> None:
    _, finish = _play_poll_hooks()
    await finish(outcome)


# target → how it runs. The keys ARE the dispatchable targets. A scheduled
# ``lastfm:plays`` always runs an *incremental* import — full/file imports are
# user-initiated, never scheduled.
SYNC_TARGETS: Final[Mapping[str, SyncTargetSpec]] = {
    "lastfm:plays": SyncTargetSpec(
        label="Last.fm plays",
        run=lambda user_id: run_import(user_id, "lastfm", "incremental"),
    ),
    "spotify:likes": SyncTargetSpec(
        label="Spotify likes",
        run=run_spotify_likes_import,
    ),
    "lastfm:likes": SyncTargetSpec(
        label="Last.fm loves",
        run=run_lastfm_likes_export,
    ),
    "spotify:plays": SyncTargetSpec(
        label="Spotify recent plays",
        run=lambda user_id: run_import(user_id, "spotify", "incremental"),
        # Cadence is adaptive and self-managed — see the module docstring.
        user_schedulable=False,
        try_begin_poll=_begin_play_poll,
        finish_poll=_finish_play_poll,
    ),
}

# The subset a user (or the assistant, or the API) may attach a schedule to.
USER_SCHEDULABLE_TARGETS: Final[tuple[str, ...]] = tuple(
    sorted(t for t, spec in SYNC_TARGETS.items() if spec.user_schedulable)
)


def sync_target_label(target: str) -> str:
    """Friendly name for a sync target, falling back to the raw id if unknown.

    Reads the full map, not just the schedulable subset: the play poller's
    schedule row is real and appears in listings, so it needs a real label.
    """
    spec = SYNC_TARGETS.get(target)
    return spec.label if spec else target


def validate_sync_target(raw: str) -> str:
    """Return the sync target if a user may schedule it, else raise ``ValueError``.

    Deliberately narrower than ``SYNC_TARGETS``: a self-managed target is
    dispatchable but must not be reachable from the generic cadence surfaces,
    which would overwrite its adaptive interval with a fixed one.
    """
    if raw not in USER_SCHEDULABLE_TARGETS:
        valid = ", ".join(USER_SCHEDULABLE_TARGETS)
        raise ValueError(f"unknown sync target {raw!r}; valid targets: {valid}")
    return raw


def sync_result_failed(result: object) -> bool:
    """True if a sync dispatch's return value signals a (soft) failure.

    The sync use cases behind ``SYNC_TARGETS`` (``run_import``,
    ``run_spotify_likes_import``, ``run_lastfm_likes_export``) do NOT raise on a
    handled failure — they catch it and return an ``OperationResult`` that records
    the failure via an ``errors`` summary metric and an ``error`` metadata key
    (see ``import_play_history``). The runner must read that signal, otherwise a
    failed sync is recorded as a successful fire. A non-``OperationResult``
    return carries no failure signal and is treated as success.

    Reads ``OperationResult.is_failure`` — the same predicate the web SSE seam
    uses — so the scheduler, the web UI, and the CLI never disagree on what a
    failed sync looks like.
    """
    return isinstance(result, OperationResult) and result.is_failure
