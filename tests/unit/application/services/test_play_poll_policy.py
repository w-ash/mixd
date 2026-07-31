"""Unit tests for the Spotify play-poll policy.

The judgement lives in the pure domain function and is tested there. What matters
here is the wiring: that the lease decides single-flight, that a failed poll does
not advance the freshness gate, and that the new interval reaches the schedule row
— the last being the whole reason the backoff saves money rather than just being
polite.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.application.services import play_poll_policy
from src.application.services.play_poll_policy import (
    PLAY_POLL_TARGET,
    finish_poll,
    try_begin_poll,
)
from src.application.use_cases._shared.sync_targets import (
    PollClaim,
    PollOutcome,
    TriggerContext,
)
from src.domain.entities.operations import OperationResult, SyncCheckpoint
from src.domain.entities.schedule import Schedule
from src.domain.repositories.play import RECENTLY_PLAYED_SCOPE
from src.domain.services.play_poll_decision import (
    BASE_INTERVAL_SECONDS,
    MIN_INTERVAL_SECONDS,
    PollState,
)
from tests.fixtures import make_mock_uow

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)


def _ctx(trigger: str = "schedule", **kwargs: object) -> TriggerContext:
    base: dict[str, object] = {"user_id": "u1", "trigger": trigger, "now": _NOW}
    return TriggerContext(**(base | kwargs))  # pyright: ignore[reportArgumentType]


def _checkpoint(**kwargs: object) -> SyncCheckpoint:
    base: dict[str, object] = {
        "user_id": "u1",
        "service": "spotify",
        "entity_type": "plays",
    }
    return SyncCheckpoint(**(base | kwargs))  # pyright: ignore[reportArgumentType]


def _uow_with(
    checkpoints: dict[tuple[str, str], SyncCheckpoint | None],
    *,
    has_scope: bool = True,
    **repo_kw,
):
    """A mock UoW whose checkpoint repo answers per (service, entity_type).

    ``has_scope`` drives the grant provider the policy now reads through, so
    the scope gate is exercised on the same UoW as the rest of the decision
    rather than as a separate patched call.
    """
    uow = make_mock_uow()
    repo = uow.get_checkpoint_repository()
    repo.get_sync_checkpoint = AsyncMock(
        side_effect=lambda user_id, service, entity_type: checkpoints.get((
            service,
            entity_type,
        ))
    )
    for k, v in repo_kw.items():
        setattr(repo, k, v)

    grants = MagicMock()
    grants.granted_scopes = AsyncMock(
        return_value=frozenset({RECENTLY_PLAYED_SCOPE}) if has_scope else frozenset()
    )
    uow.get_connector_grant_provider = MagicMock(return_value=grants)
    return uow


def _run_with(uow):
    """Patch execute_use_case so the policy runs against a mock UoW."""

    async def _execute(factory, user_id=None):
        return await factory(uow)

    return patch.object(play_poll_policy, "execute_use_case", _execute)


class TestTryBeginPoll:
    async def test_claims_when_due(self) -> None:
        uow = _uow_with({}, try_claim_poll=AsyncMock(return_value=True))
        with _run_with(uow):
            claim = await try_begin_poll(_ctx())

        assert claim.granted is True
        assert claim.payload is not None

    async def test_missing_scope_vetoes_without_touching_the_lease(self) -> None:
        # A scope gap already surfaces as "re-connect needed" on the connector
        # card; claiming and failing here would add nothing but noise.
        claim_mock = AsyncMock(return_value=True)
        uow = _uow_with({}, has_scope=False, try_claim_poll=claim_mock)
        with _run_with(uow):
            claim = await try_begin_poll(_ctx())

        assert claim.granted is False
        assert claim.veto_reason == "scope_missing"
        claim_mock.assert_not_awaited()

    async def test_losing_the_lease_is_a_veto_not_an_error(self) -> None:
        """Concurrent triggers collapse into one import.

        The loser must return a plain veto: two triggers racing is the *expected*
        path (a page load and the heartbeat can easily coincide), not a fault.
        """
        uow = _uow_with({}, try_claim_poll=AsyncMock(return_value=False))
        with _run_with(uow):
            claim = await try_begin_poll(_ctx())

        assert claim.granted is False
        assert claim.veto_reason == "claimed_elsewhere"

    async def test_a_tighter_caller_max_age_wins(self) -> None:
        # A demand caller may require fresher data than the target's own cadence.
        # The claim must be made under the tighter of the two, or the SQL guard
        # would let through a poll the decision would have rejected.
        claim_mock = AsyncMock(return_value=True)
        uow = _uow_with({}, try_claim_poll=claim_mock)
        with _run_with(uow):
            await try_begin_poll(_ctx("demand", max_age=timedelta(minutes=2)))

        assert claim_mock.await_args.kwargs["max_age"] == timedelta(minutes=2)

    async def test_lastfm_checkpoint_selects_the_redundant_cap(self) -> None:
        # A recent scrobble means a missed window is recoverable, so the poller
        # may relax toward the daily cap.
        checkpoints = {
            ("spotify", "plays"): _checkpoint(),
            ("lastfm", "plays"): SyncCheckpoint(
                user_id="u1",
                service="lastfm",
                entity_type="plays",
                last_timestamp=_NOW - timedelta(days=1),
            ),
        }
        uow = _uow_with(checkpoints, try_claim_poll=AsyncMock(return_value=True))
        with _run_with(uow):
            claim = await try_begin_poll(_ctx())

        payload = claim.payload
        assert payload is not None
        assert payload.cap_seconds == 24 * 60 * 60  # pyright: ignore[reportAttributeAccessIssue]


class TestFinishPoll:
    @staticmethod
    def _outcome(*, succeeded: bool, result: object, trigger: str = "schedule"):
        from src.application.services.play_poll_policy import _ClaimPayload

        return PollOutcome(
            context=_ctx(trigger),
            claim=PollClaim(
                granted=True,
                payload=_ClaimPayload(state=PollState(), cap_seconds=7200),
            ),
            succeeded=succeeded,
            result=result,
        )

    @staticmethod
    def _import_result(*, raw: int, imported: int) -> OperationResult:
        """A result shaped like what the two-phase orchestrator actually returns.

        The metric names matter and are the reason this is a helper rather than
        an inline literal: ``_combine_phase_results`` renames the ingestion
        phase's ``imported`` to ``connector_plays``, and a test that invented an
        ``imported`` metric is exactly what let a broken read pass review.
        """
        r = OperationResult(operation_name="import")
        r.summary_metrics.add("raw_plays", raw, "Raw Plays Found", significance=0)
        r.summary_metrics.add(
            "connector_plays", imported, "Connector Plays Ingested", significance=1
        )
        r.summary_metrics.add(
            "track_plays", imported, "Track Plays Created", significance=2
        )
        return r

    async def test_successful_poll_advances_the_gate(self) -> None:
        uow = _uow_with({})
        with _run_with(uow):
            await finish_poll(
                self._outcome(
                    succeeded=True, result=self._import_result(raw=3, imported=3)
                )
            )

        kwargs = uow.get_checkpoint_repository().finish_poll.await_args.kwargs
        assert kwargs["polled_at"] == _NOW

    async def test_failed_poll_releases_the_lease_without_advancing_the_gate(
        self,
    ) -> None:
        """A broken connector must not look recently checked.

        Advancing on failure would make the health surface report "healthy" for
        an integration that has not fetched anything in days.
        """
        uow = _uow_with({})
        with _run_with(uow):
            await finish_poll(self._outcome(succeeded=False, result=None))

        repo = uow.get_checkpoint_repository()
        kwargs = repo.finish_poll.await_args.kwargs
        assert kwargs["polled_at"] is None
        # Still called at all — the lease is released either way.
        repo.finish_poll.assert_awaited_once()

    async def test_the_new_interval_reaches_the_schedule_row(self) -> None:
        """The mechanism that turns backoff into a cost saving.

        The scheduler wakes on ``next_run_at``, which derives from this interval.
        Without the write-back the heartbeat keeps firing every 30 minutes and
        waking the database just to be told there is nothing to do.
        """
        uow = _uow_with({})
        with _run_with(uow):
            await finish_poll(
                self._outcome(
                    succeeded=True, result=self._import_result(raw=0, imported=0)
                )
            )

        kwargs = uow.get_schedule_repository().set_poll_interval.await_args.kwargs
        assert kwargs["sync_target"] == PLAY_POLL_TARGET
        # An empty poll stretched past the base cadence.
        assert kwargs["interval_minutes"] > BASE_INTERVAL_SECONDS // 60

    async def test_a_saturated_window_floors_the_cadence(self) -> None:
        uow = _uow_with({})
        with _run_with(uow):
            await finish_poll(
                self._outcome(
                    succeeded=True, result=self._import_result(raw=50, imported=50)
                )
            )

        kwargs = uow.get_schedule_repository().set_poll_interval.await_args.kwargs
        assert kwargs["interval_minutes"] == MIN_INTERVAL_SECONDS // 60

    async def test_demand_poll_also_pushes_the_next_wake_out(self) -> None:
        # No scheduler release follows a demand poll, so without this the
        # heartbeat would fire moments after a poll that just succeeded.
        uow = _uow_with({})
        with _run_with(uow):
            await finish_poll(
                self._outcome(
                    succeeded=True,
                    result=self._import_result(raw=1, imported=1),
                    trigger="demand",
                )
            )

        uow.get_schedule_repository().set_next_run_at.assert_awaited_once()

    async def test_scheduled_poll_leaves_next_run_at_to_the_scheduler(self) -> None:
        # The scheduler recomputes it from a fresh read inside its own claim
        # transaction; writing it here too would race that path for no gain.
        uow = _uow_with({})
        with _run_with(uow):
            await finish_poll(
                self._outcome(
                    succeeded=True, result=self._import_result(raw=1, imported=1)
                )
            )

        uow.get_schedule_repository().set_next_run_at.assert_not_awaited()


class TestEnableDisable:
    """The lifecycle hooks that decide whether polling ever runs at all."""

    async def test_enable_creates_a_schedule_at_the_base_cadence(self) -> None:
        uow = _uow_with({})
        uow.get_schedule_repository().get_for_target = AsyncMock(return_value=None)
        with _run_with(uow), patch.object(play_poll_policy, "notify_schedule_changed"):
            await play_poll_policy.enable_play_polling("u1")

        created = uow.get_schedule_repository().create.await_args.args[0]
        assert created.sync_target == PLAY_POLL_TARGET
        assert created.interval_minutes == BASE_INTERVAL_SECONDS // 60
        assert created.status == "enabled"

    async def test_enable_wakes_a_parked_scheduler(self) -> None:
        # A newly-enabled schedule is due immediately; without the signal the
        # loop sleeps out its current interval before noticing.
        uow = _uow_with({})
        uow.get_schedule_repository().get_for_target = AsyncMock(return_value=None)
        with (
            _run_with(uow),
            patch.object(play_poll_policy, "notify_schedule_changed") as m_notify,
        ):
            await play_poll_policy.enable_play_polling("u1")

        m_notify.assert_called_once()

    async def test_enable_force_enables_and_resets_an_existing_row(self) -> None:
        """Re-consent is explicit intent, and stale backoff is untrustworthy.

        A user who disabled polling and then deliberately re-granted the scope
        wants it on; and an account that has been silent for weeks carries a
        stretched interval that should not survive the reconnect.
        """
        existing = Schedule(
            user_id="u1",
            sync_target=PLAY_POLL_TARGET,
            interval_minutes=1440,
            status="disabled",
        )
        uow = _uow_with({})
        uow.get_schedule_repository().get_for_target = AsyncMock(return_value=existing)
        with _run_with(uow), patch.object(play_poll_policy, "notify_schedule_changed"):
            await play_poll_policy.enable_play_polling("u1")

        updated = uow.get_schedule_repository().update_schedule.await_args.args[0]
        assert updated.id == existing.id
        assert updated.status == "enabled"
        assert updated.interval_minutes == BASE_INTERVAL_SECONDS // 60

    async def test_disable_preserves_the_row_and_its_history(self) -> None:
        existing = Schedule(
            user_id="u1",
            sync_target=PLAY_POLL_TARGET,
            interval_minutes=120,
            status="enabled",
        )
        uow = _uow_with({})
        uow.get_schedule_repository().get_for_target = AsyncMock(return_value=existing)
        with _run_with(uow):
            await play_poll_policy.disable_play_polling("u1")

        updated = uow.get_schedule_repository().update_schedule.await_args.args[0]
        assert updated.status == "disabled"
        # Disabled, not deleted — a later reconnect resumes the same row.
        uow.get_schedule_repository().delete_for_user.assert_not_awaited()

    async def test_disable_tolerates_a_missing_row(self) -> None:
        uow = _uow_with({})
        uow.get_schedule_repository().get_for_target = AsyncMock(return_value=None)
        with _run_with(uow):
            await play_poll_policy.disable_play_polling("u1")

        uow.get_schedule_repository().update_schedule.assert_not_awaited()


class TestAuthHooks:
    """Never-raise wrappers: a scheduling side effect must not fail an auth flow."""

    async def test_a_grant_with_the_scope_enables_polling(self) -> None:
        with patch.object(
            play_poll_policy, "enable_play_polling", AsyncMock()
        ) as m_enable:
            await play_poll_policy.sync_play_polling_after_auth(
                "u1", "user-library-read user-read-recently-played"
            )
        m_enable.assert_awaited_once_with("u1")

    async def test_a_grant_without_the_scope_does_nothing(self) -> None:
        with patch.object(
            play_poll_policy, "enable_play_polling", AsyncMock()
        ) as m_enable:
            await play_poll_policy.sync_play_polling_after_auth(
                "u1", "user-library-read"
            )
        m_enable.assert_not_awaited()

    async def test_a_failure_never_breaks_the_auth_flow(self) -> None:
        # The user has just authorised successfully. Failing that over a
        # scheduling side effect is strictly worse than not polling.
        with patch.object(
            play_poll_policy,
            "enable_play_polling",
            AsyncMock(side_effect=RuntimeError("db down")),
        ):
            await play_poll_policy.sync_play_polling_after_auth(
                "u1", "user-read-recently-played"
            )

    async def test_a_failure_never_breaks_the_disconnect_flow(self) -> None:
        with patch.object(
            play_poll_policy,
            "disable_play_polling",
            AsyncMock(side_effect=RuntimeError("db down")),
        ):
            await play_poll_policy.stop_play_polling_after_disconnect("u1")


class TestIngestedMetricNaming:
    """Regression: the backoff must read the metric production actually emits.

    ``_counts`` read ``imported``, which the two-phase orchestrator never emits
    — it renames that metric to ``connector_plays`` when combining phases. Every
    productive poll therefore reported 0 ingested and the interval doubled while
    the user was mid-session, the exact inverse of the control law. The live
    acceptance poll missed it because a saturated window (50/50) short-circuits
    into the ``saturated`` branch without consulting the count at all.
    """

    @staticmethod
    def _combined(raw: int, connector_plays: int) -> OperationResult:
        r = OperationResult(operation_name="import")
        r.summary_metrics.add("raw_plays", raw, "Raw Plays Found", significance=0)
        r.summary_metrics.add(
            "connector_plays",
            connector_plays,
            "Connector Plays Ingested",
            significance=1,
        )
        return r

    def test_reads_connector_plays_from_the_two_phase_shape(self) -> None:
        assert play_poll_policy._counts(self._combined(12, 12)) == (12, 12)

    def test_still_reads_imported_from_a_single_phase_shape(self) -> None:
        # domain/results.create_import_result emits `imported`; a future
        # single-phase channel must not silently report zero.
        r = OperationResult(operation_name="import")
        r.summary_metrics.add("raw_plays", 4, "Raw Plays Found", significance=0)
        r.summary_metrics.add("imported", 4, "Track Plays Created", significance=1)
        assert play_poll_policy._counts(r) == (4, 4)

    def test_a_non_result_return_carries_no_counts(self) -> None:
        assert play_poll_policy._counts(None) == (0, 0)

    async def test_an_ordinary_productive_poll_resets_to_base(self) -> None:
        """The scenario the bug broke: 12 new plays, well under near-full.

        Before the fix this took the empty branch and stretched toward the cap
        while the user was actively listening.
        """
        uow = _uow_with({})
        outcome = TestFinishPoll._outcome(succeeded=True, result=self._combined(12, 12))
        with _run_with(uow):
            await finish_poll(outcome)

        kwargs = uow.get_schedule_repository().set_poll_interval.await_args.kwargs
        assert kwargs["interval_minutes"] == BASE_INTERVAL_SECONDS // 60
