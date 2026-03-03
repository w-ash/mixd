"""Shared playlist resolution logic for use cases.

Resolves a playlist ID (internal database integer or external connector string)
into a Playlist entity. Used by read, delete, and update use cases.
"""

from src.config import get_logger
from src.domain.entities.playlist import Playlist
from src.domain.exceptions import NotFoundError
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


async def resolve_playlist(
    playlist_id: str,
    uow: UnitOfWorkProtocol,
    *,
    connector: str = "spotify",
    raise_if_not_found: bool = True,
) -> Playlist | None:
    """Resolve a playlist by internal ID or external connector ID.

    Tries to parse ``playlist_id`` as an integer for direct database lookup.
    If it isn't numeric, falls back to searching by connector ID.

    Args:
        playlist_id: Internal database ID (numeric string) or external service ID.
        uow: Unit of work for repository access.
        connector: External service name for fallback lookup (default: ``"spotify"``).
        raise_if_not_found: If ``True``, raise ``NotFoundError`` when the playlist
            is not found.  If ``False``, return ``None``.

    Returns:
        The resolved playlist, or ``None`` when not found and
        ``raise_if_not_found`` is ``False``.

    Raises:
        NotFoundError: If the playlist is not found and ``raise_if_not_found`` is ``True``.
    """
    playlist_repo = uow.get_playlist_repository()

    try:
        return await playlist_repo.get_playlist_by_id(int(playlist_id))
    except ValueError:
        # Not an integer — treat as external connector ID
        playlist = await playlist_repo.get_playlist_by_connector(
            connector, playlist_id, raise_if_not_found=raise_if_not_found
        )
        if playlist is not None:
            return playlist

    if raise_if_not_found:
        raise NotFoundError(f"Playlist with ID {playlist_id} not found")
    return None


async def require_playlist(
    playlist_id: str,
    uow: UnitOfWorkProtocol,
    *,
    connector: str = "spotify",
) -> Playlist:
    """Resolve a playlist, raising if not found.

    Convenience wrapper around ``resolve_playlist`` that guarantees a non-None
    return, giving callers a clean ``Playlist`` type without narrowing guards.

    Args:
        playlist_id: Internal database ID (numeric string) or external service ID.
        uow: Unit of work for repository access.
        connector: External service name for fallback lookup.

    Returns:
        The resolved playlist.

    Raises:
        NotFoundError: If the playlist is not found.
    """
    playlist = await resolve_playlist(
        playlist_id, uow, connector=connector, raise_if_not_found=True
    )
    if playlist is None:  # pragma: no cover — raise_if_not_found=True guarantees this
        msg = f"Playlist {playlist_id} not found"
        raise NotFoundError(msg)
    return playlist
