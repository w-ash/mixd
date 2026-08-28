"""Discogs connector status probe."""

from src.domain.entities.connector import ConnectorStatus
from src.infrastructure.connectors._shared.connector_status import stored_token_status
from src.infrastructure.connectors._shared.token_storage import TokenStorage


async def get_discogs_status(
    user_id: str,
    storage: TokenStorage | None = None,
) -> ConnectorStatus:
    """Discogs status from the stored personal access token — storage only.

    Never a network call (this probe runs on every Integrations render, and
    the whole instance shares one per-IP 60/min Discogs budget): the token
    was validated live at connect time by ``discogs/token_service.py``,
    which also cached ``extra_data["collection_count"]``. The ``detail``
    suffix renders that cached count — a count of 0 still renders
    ("0 releases" is the zero-state invitation to start cataloguing, not an
    error); a token stored without a count yields ``detail=None``. A
    personal access token never ages, so no reauth rule applies.
    """
    return await stored_token_status(
        "discogs",
        "token",
        user_id,
        storage,
        detail_key="collection_count",
        detail_noun="releases",
    )
