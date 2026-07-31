"""A proposed mutation held until the acting user confirms it.

Two-phase writes: mutation (``kind="write"``) chat tools propose an action,
the frontend renders a confirmation card, and the action executes only when
the user explicitly confirms.
"""

from datetime import datetime
from uuid import UUID

from attrs import define

from src.domain.entities.shared import JsonDict


@define(frozen=True, slots=True)
class PendingAction:
    """A proposed mutation held until the acting user confirms it."""

    action_id: UUID
    user_id: str
    tool_name: str
    tool_input: JsonDict
    description: str
    details: JsonDict
    created_at: datetime
