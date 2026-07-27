"""Poll policy for the Spotify recently-played channel.

The application-side half of adaptive polling: gathers what the pure decision
function needs, claims the lease, and folds the result back into persisted state.
The judgement itself lives in ``domain/services/play_poll_decision`` — this module
only does I/O and wiring.

Module functions rather than ``*UseCase`` classes, deliberately. These are hooks
hanging off a sync-target descriptor, invoked by a background runner rather than
by a route, and giving them Command/Result envelopes would add a shape nothing
consumes while pulling them into the use-case parity registry.
"""

from datetime import UTC, datetime, timedelta
from typing import Final

from attrs import define

from src.application.runner import execute_use_case
from src.application.services.schedule_signal import notify_schedule_changed
from src.application.use_cases._shared.sync_targets import (
    PollClaim,
    PollOutcome,
    TriggerContext,
)
from src.config import get_logger
from src.domain.entities.operations import OperationResult
from src.domain.entities.schedule import Schedule
from src.domain.entities.shared import JsonDict
from src.domain.repositories.play import RECENTLY_PLAYED_PAGE_LIMIT
from src.domain.repositories.uow import UnitOfWorkProtocol
from src.domain.services.play_poll_decision import (
    BASE_INTERVAL_SECONDS,
    CLAIM_TTL,
    PollDecisionInputs,
    PollState,
    apply_poll_result,
    decide_poll,
)

logger = get_logger(__name__)

PLAY_POLL_TARGET = "spotify:plays"
_SERVICE = "spotify"
_ENTITY = "plays"
_LASTFM = "lastfm"


@define(frozen=True, slots=True)
class _ClaimPayload:
    """Pre-poll state carried through the run to ``finish_poll``.

    Re-reading these afterwards would cost a second round trip on a database we
    are trying to let sleep, and would read *post*-poll values where the fold
    needs the pre-poll ones.
    """

    state: PollState
    cap_seconds: int
    # False on a first poll (no stored cursor). A saturated window then means
    # "we just adopted the whole retained window", not "we lost plays".
    resumed: bool = True


async def _has_recently_played_scope(user_id: str) -> bool:
    """Whether the stored Spotify grant still covers recently-played.

    Function-scoped connector import: this is the sanctioned narrow edge for
    reaching token storage from the application layer (the same shape the metric
    config provider uses), keeping the dependency at one call site rather than
    at module scope.
    """
    from src.infrastructure.connectors._shared.token_storage import get_token_storage
    from src.infrastructure.connectors.spotify.auth import (
        RECENTLY_PLAYED_SCOPE,
        missing_scopes,
    )

    token = await get_token_storage().load_token(_SERVICE, user_id)
    if token is None:
        return False
    return RECENTLY_PLAYED_SCOPE not in missing_scopes(token.get("scope"))


async def try_begin_poll(context: TriggerContext) -> PollClaim:
    """Decide whether this trigger polls, and claim the lease if so.

    One transaction, one read of everything the decision needs — this runs on
    every play-surface read, so a second round trip here is a second reason to
    wake a suspended database.
    """
    has_scope = await _has_recently_played_scope(context.user_id)

    async def _decide(uow: UnitOfWorkProtocol) -> PollClaim:
        async with uow:
            repo = uow.get_checkpoint_repository()
            own = await repo.get_sync_checkpoint(context.user_id, _SERVICE, _ENTITY)
            lastfm = await repo.get_sync_checkpoint(context.user_id, _LASTFM, _ENTITY)

            state = PollState.from_json(own.poll_state if own else None)
            inputs = PollDecisionInputs(
                now=context.now,
                trigger=context.trigger,
                last_polled_at=own.last_polled_at if own else None,
                state=state,
                has_scope=has_scope,
                lastfm_last_timestamp=lastfm.last_timestamp if lastfm else None,
            )
            decision = decide_poll(inputs)
            if not decision.should_poll:
                return PollClaim(granted=False, veto_reason=decision.veto_reason)

            # The caller may demand tighter freshness than the target's cadence.
            max_age = (
                min(context.max_age, decision.max_age)
                if context.max_age is not None
                else decision.max_age
            )
            won = await repo.try_claim_poll(
                context.user_id,
                _SERVICE,
                _ENTITY,
                now=context.now,
                max_age=max_age,
                claim_ttl=CLAIM_TTL,
            )
            await uow.commit()
            if not won:
                # Another trigger is already polling, or beat us to it. Both are
                # successful single-flight outcomes, not errors.
                return PollClaim(granted=False, veto_reason="claimed_elsewhere")
            return PollClaim(
                granted=True,
                payload=_ClaimPayload(
                    state=state,
                    cap_seconds=decision.cap_seconds,
                    resumed=own is not None and own.cursor is not None,
                ),
            )

    return await execute_use_case(_decide, user_id=context.user_id)


# The ingested-observations metric, by the name each import shape gives it.
#
# The two-phase orchestrator this channel runs through builds a *combined*
# result and renames the ingestion phase's ``imported`` to ``connector_plays``
# (``play_import_orchestrator._combine_phase_results``). Reading only
# ``imported`` therefore always yielded 0 and made every productive poll look
# empty, stretching the interval while the user was actively listening — the
# inverse of the control law. Single-phase importers still emit ``imported``
# (``domain/results.py``), so both names are accepted, most specific first.
_INGESTED_KEYS: Final = ("connector_plays", "imported")


def _metric(counts: JsonDict, *names: str) -> int:
    """First present integer metric among ``names``, else 0."""
    for name in names:
        value = counts.get(name)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return 0


def _counts(result: object) -> tuple[int, int]:
    """``(raw_plays, ingested)`` from an import result, or zeros.

    Two counts because they answer different questions — see
    ``apply_poll_result``. A non-``OperationResult`` return carries neither.
    """
    if not isinstance(result, OperationResult):
        return 0, 0
    counts = result.to_counts()
    return _metric(counts, "raw_plays"), _metric(counts, *_INGESTED_KEYS)


async def finish_poll(outcome: PollOutcome) -> None:
    """Release the lease, persist the new backoff state, and re-time the wake.

    Writing the new interval onto the schedule row is what makes the backoff a
    cost mechanism rather than only a politeness one: the scheduler wakes on
    ``next_run_at``, so a stretched interval lets the database stay suspended
    instead of waking every 30 minutes to be told there is nothing to do.
    """
    payload = outcome.claim.payload
    if not isinstance(payload, _ClaimPayload):
        # try_begin_poll always attaches one on a granted claim; being defensive
        # here keeps a bookkeeping mismatch from stranding the lease.
        payload = _ClaimPayload(state=PollState(), cap_seconds=BASE_INTERVAL_SECONDS)

    context = outcome.context
    raw_plays, ingested = _counts(outcome.result)
    next_state = apply_poll_result(
        payload.state,
        trigger=context.trigger,
        succeeded=outcome.succeeded,
        raw_plays=raw_plays,
        imported=ingested,
        window_limit=RECENTLY_PLAYED_PAGE_LIMIT,
        cap_seconds=payload.cap_seconds,
        resumed=payload.resumed,
        jitter_key=context.user_id,
    )
    interval_minutes = max(1, round(next_state.interval_seconds / 60))

    async def _persist(uow: UnitOfWorkProtocol) -> None:
        async with uow:
            await uow.get_checkpoint_repository().finish_poll(
                context.user_id,
                _SERVICE,
                _ENTITY,
                # Only a successful poll advances the freshness gate. A failure
                # that moved it would make a broken connector look healthy.
                polled_at=context.now if outcome.succeeded else None,
                poll_state=next_state.to_json(),
            )
            schedules = uow.get_schedule_repository()
            await schedules.set_poll_interval(
                user_id=context.user_id,
                sync_target=PLAY_POLL_TARGET,
                interval_minutes=interval_minutes,
            )
            if context.trigger == "demand":
                # No scheduler release follows a demand poll, so nothing else
                # would push the wake time out — leaving the heartbeat to fire
                # moments after a poll that just succeeded.
                await schedules.set_next_run_at(
                    user_id=context.user_id,
                    sync_target=PLAY_POLL_TARGET,
                    next_run_at=context.now
                    + timedelta(seconds=next_state.interval_seconds),
                )
            await uow.commit()

    await execute_use_case(_persist, user_id=context.user_id)


async def enable_play_polling(user_id: str) -> None:
    """Create or re-enable this user's play-poll schedule at the base cadence.

    Called when a grant lands that includes recently-played. Force-enables rather
    than preserving a previous disabled state: re-consenting to a scope whose only
    purpose is this feature is explicit intent to use it. The interval resets to
    base because a fresh grant is exactly when stored backoff state is least
    trustworthy — the account may have been silent for weeks.

    Writes through the repository rather than ``UpsertScheduleUseCase``, which
    validates against the *user-schedulable* set and would reject this
    self-managed target.
    """

    async def _enable(uow: UnitOfWorkProtocol) -> None:
        async with uow:
            repo = uow.get_schedule_repository()
            existing = await repo.get_for_target(
                user_id=user_id, sync_target=PLAY_POLL_TARGET
            )
            base_minutes = BASE_INTERVAL_SECONDS // 60
            now = datetime.now(UTC)
            if existing is None:
                await repo.create(
                    Schedule(
                        user_id=user_id,
                        sync_target=PLAY_POLL_TARGET,
                        interval_minutes=base_minutes,
                        status="enabled",
                        next_run_at=now,
                    )
                )
            else:
                await repo.update_schedule(
                    Schedule(
                        user_id=user_id,
                        sync_target=PLAY_POLL_TARGET,
                        interval_minutes=base_minutes,
                        status="enabled",
                        next_run_at=now,
                        id=existing.id,
                    ),
                    user_id=user_id,
                )
            await uow.commit()

    await execute_use_case(_enable, user_id=user_id)
    # A newly-enabled schedule is due immediately; wake a parked loop rather than
    # letting it sleep out its current interval.
    notify_schedule_changed()


@define(frozen=True, slots=True)
class PlayPollingState:
    """Whether the heartbeat is on for a user, and at what live cadence."""

    enabled: bool
    interval_minutes: int | None = None
    next_run_at: datetime | None = None


async def get_play_polling_state(user_id: str) -> PlayPollingState:
    """Read the current polling state. Absent schedule reads as off."""

    async def _read(uow: UnitOfWorkProtocol) -> PlayPollingState:
        async with uow:
            existing = await uow.get_schedule_repository().get_for_target(
                user_id=user_id, sync_target=PLAY_POLL_TARGET
            )
        if existing is None:
            return PlayPollingState(enabled=False)
        return PlayPollingState(
            enabled=existing.status == "enabled",
            interval_minutes=existing.interval_minutes,
            next_run_at=existing.next_run_at,
        )

    return await execute_use_case(_read, user_id=user_id)


async def set_play_polling(user_id: str, *, enabled: bool) -> PlayPollingState:
    """Turn the heartbeat on or off, creating the schedule row if needed.

    The reachable counterpart to the auth-callback hook. Without it, a user who
    consented before this feature existed had a valid grant, no schedule row, and
    no way to create one: the Sync card's switch renders disabled with nothing to
    toggle, and the generic upsert route rejects this target by design.
    """
    if enabled:
        await enable_play_polling(user_id)
    else:
        await disable_play_polling(user_id)
    return await get_play_polling_state(user_id)


async def sync_play_polling_after_auth(user_id: str, granted_scope: str | None) -> None:
    """Enable polling if a fresh grant covers recently-played. Never raises.

    Called from the OAuth callback and the CLI auth flow. Swallowing is the point:
    the user has just authorised successfully, and failing that flow over a
    scheduling side effect would be a worse outcome than not polling — the next
    connect, or the user enabling it from the Sync page, recovers.
    """
    from src.infrastructure.connectors.spotify.auth import (
        RECENTLY_PLAYED_SCOPE,
        missing_scopes,
    )

    try:
        if RECENTLY_PLAYED_SCOPE in missing_scopes(granted_scope):
            return
        await enable_play_polling(user_id)
    except Exception:
        logger.warning(
            "Could not enable play polling after auth", user_id=user_id, exc_info=True
        )


async def stop_play_polling_after_disconnect(user_id: str) -> None:
    """Disable polling when Spotify is disconnected. Never raises.

    Same reasoning inverted: the token is already gone, so the disconnect itself
    has succeeded. A poller left enabled without a token vetoes every tick on the
    missing scope anyway — untidy, not harmful.
    """
    try:
        await disable_play_polling(user_id)
    except Exception:
        logger.warning(
            "Could not disable play polling after disconnect",
            user_id=user_id,
            exc_info=True,
        )


async def disable_play_polling(user_id: str) -> None:
    """Disable this user's play-poll schedule. Tolerates there being none.

    Called on disconnect. Disabling rather than deleting preserves the run
    history, and a later reconnect re-enables the same row.
    """

    async def _disable(uow: UnitOfWorkProtocol) -> None:
        async with uow:
            repo = uow.get_schedule_repository()
            existing = await repo.get_for_target(
                user_id=user_id, sync_target=PLAY_POLL_TARGET
            )
            if existing is None:
                return
            await repo.update_schedule(
                Schedule(
                    user_id=user_id,
                    sync_target=PLAY_POLL_TARGET,
                    interval_minutes=existing.interval_minutes,
                    status="disabled",
                    next_run_at=existing.next_run_at,
                    id=existing.id,
                ),
                user_id=user_id,
            )
            await uow.commit()

    await execute_use_case(_disable, user_id=user_id)
