"""Ownership-gated mapping fetch shared by the mapping-mutation use cases.

The tamper guard exists because mapping ids arrive from URLs: a request may
name any mapping id, so the mapping must both exist for this user AND belong
to the track named in the same URL. Relink, unlink, and set-primary all run
this prelude before mutating — one copy here so the multi-tenancy check
cannot drift between them.
"""

from uuid import UUID

from src.domain.entities import TrackMapping
from src.domain.exceptions import NotFoundError
from src.domain.repositories.connector import ConnectorRepositoryProtocol


async def require_owned_mapping(
    connector_repo: ConnectorRepositoryProtocol,
    mapping_id: UUID,
    track_id: UUID,
    *,
    user_id: str,
) -> TrackMapping:
    """Fetch a mapping, enforcing existence and the URL tamper guard.

    Args:
        connector_repo: Connector repository (user-scoped lookup).
        mapping_id: Mapping row id from the request URL.
        track_id: Track id from the same URL — must match the mapping's.
        user_id: Authenticated user id.

    Returns:
        The validated mapping.

    Raises:
        NotFoundError: If the mapping doesn't exist (or is another user's).
        ValueError: If the mapping belongs to a different track (tamper guard).
    """
    mapping = await connector_repo.get_mapping_by_id(mapping_id, user_id=user_id)
    if mapping is None:
        raise NotFoundError(f"Mapping {mapping_id} not found")
    if mapping.track_id != track_id:
        raise ValueError("Mapping does not belong to the specified track")
    return mapping
