"""Audit-event tail shared by the mutate-and-log use cases (tags, preferences).

Enforces the invariant that an audit event is written iff the mutation
actually changed rows — and rides the same commit as the mutation, so the
event log can never disagree with the data it describes.
"""

from collections.abc import Awaitable, Sequence
from typing import Protocol

from src.domain.repositories.uow import UnitOfWorkProtocol


class AddEventsFn[EventT](Protocol):
    """A bound repository ``add_events`` method (tag or preference repo)."""

    def __call__(
        self, events: Sequence[EventT], *, user_id: str
    ) -> Awaitable[Sequence[EventT]]: ...


async def apply_with_event_log[EventT](
    uow: UnitOfWorkProtocol,
    *,
    changed: bool,
    events: Sequence[EventT],
    add_events: AddEventsFn[EventT],
    user_id: str,
) -> bool:
    """Commit an applied mutation together with its audit events.

    When ``changed`` is False the mutation was a no-op: nothing is logged and
    nothing commits (callers return their unchanged Result). Otherwise the
    events are appended and the transaction commits.

    Returns:
        ``changed``, so callers can thread it straight into their Result.
    """
    if not changed:
        return False
    await add_events(events, user_id=user_id)
    await uow.commit()
    return True
