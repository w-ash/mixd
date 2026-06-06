"""Schedulable sync targets and their dispatch — one source of truth.

A background-sync schedule names a ``"service:entity"`` target. ``SYNC_DISPATCH``
is the single enumeration: it maps each schedulable target to the coroutine that
runs it (called with the schedule owner's ``user_id``). The schedulable set and
the validator both derive from it, and the scheduler dispatches through it — so
adding a connector is a one-line edit here, with no second list to keep in sync.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Final

from src.application.use_cases.import_play_history import run_import
from src.application.use_cases.sync_likes import (
    run_lastfm_likes_export,
    run_spotify_likes_import,
)
from src.domain.entities.operations import OperationResult

# target → coroutine factory (called with the schedule owner's user_id). The keys
# ARE the schedulable targets. A scheduled ``lastfm:plays`` always runs an
# *incremental* import — full/file imports are user-initiated, never scheduled.
SYNC_DISPATCH: Final[Mapping[str, Callable[[str], Awaitable[object]]]] = {
    "lastfm:plays": lambda user_id: run_import(user_id, "lastfm", "incremental"),
    "spotify:likes": run_spotify_likes_import,
    "lastfm:likes": run_lastfm_likes_export,
}

# The runtime source of truth for "is this target schedulable?".
SCHEDULABLE_SYNC_TARGETS: Final[frozenset[str]] = frozenset(SYNC_DISPATCH)


def validate_sync_target(raw: str) -> str:
    """Return the sync target if schedulable, else raise ``ValueError``."""
    if raw not in SYNC_DISPATCH:
        valid = ", ".join(sorted(SYNC_DISPATCH))
        raise ValueError(f"unknown sync target {raw!r}; valid targets: {valid}")
    return raw


def sync_result_failed(result: object) -> bool:
    """True if a sync dispatch's return value signals a (soft) failure.

    The sync use cases behind ``SYNC_DISPATCH`` (``run_import``,
    ``run_spotify_likes_import``, ``run_lastfm_likes_export``) do NOT raise on a
    handled failure — they catch it and return an ``OperationResult`` that records
    the failure via an ``errors`` summary metric and an ``error`` metadata key
    (see ``import_play_history``). The scheduler must read that signal, otherwise a
    failed scheduled sync is recorded as a successful fire. A non-``OperationResult``
    return carries no failure signal and is treated as success.
    """
    if not isinstance(result, OperationResult):
        return False
    return result.summary_metrics.get("errors", 0) > 0 or "error" in result.metadata
