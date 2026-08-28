"""MusicBrainz connector status probe."""

from src.domain.entities.connector import ConnectorStatus
from src.infrastructure.connectors._shared.token_storage import TokenStorage


async def get_musicbrainz_status(
    user_id: str,
    storage: TokenStorage | None,
) -> ConnectorStatus:
    """MusicBrainz is a public API — always available, no auth required.

    Signature matches the uniform ``status_fn`` shape declared by
    ``ConnectorConfig``; ``user_id`` and ``storage`` are unused.
    """
    del user_id, storage
    return ConnectorStatus(name="musicbrainz", auth_method="none", connected=True)
