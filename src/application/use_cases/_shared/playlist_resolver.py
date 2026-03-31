"""Shared playlist and playlist-link resolution logic for use cases.

Resolves playlist IDs (internal UUID or external connector string) and
playlist-link IDs into domain entities. Used by CRUD and sync use cases.
"""

from uuid import UUID

from src.config import get_logger
from src.domain.entities.playlist import Playlist
from src.domain.entities.playlist_link import PlaylistLink
from src.domain.exceptions import NotFoundError
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


async def resolve_playlist(
    playlist_id: str,
    uow: UnitOfWorkProtocol,
    *,
    user_id: str,
    connector: str = "spotify",
    raise_if_not_found: bool = True,
) -> Playlist | None:
    """Resolve a playlist by internal ID or external connector ID.

    Tries to parse ``playlist_id`` as a UUID for direct database lookup.
    If it isn't a valid UUID, falls back to searching by connector ID.

    Args:
        playlist_id: Internal database ID (UUID string) or external service ID.
        uow: Unit of work for repository access.
        user_id: Authenticated user ID for ownership scoping.
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
        return await playlist_repo.get_playlist_by_id(
            UUID(playlist_id), user_id=user_id
        )
    except ValueError, NotFoundError:
        # ValueError: not a valid UUID — treat as external connector ID
        # NotFoundError: valid UUID but no matching playlist
        playlist = await playlist_repo.get_playlist_by_connector(
            connector,
            playlist_id,
            user_id=user_id,
            raise_if_not_found=raise_if_not_found,
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
    user_id: str,
    connector: str = "spotify",
) -> Playlist:
    """Resolve a playlist, raising if not found.

    Convenience wrapper around ``resolve_playlist`` that guarantees a non-None
    return, giving callers a clean ``Playlist`` type without narrowing guards.

    Args:
        playlist_id: Internal database ID (numeric string) or external service ID.
        uow: Unit of work for repository access.
        user_id: Authenticated user ID for ownership scoping.
        connector: External service name for fallback lookup.

    Returns:
        The resolved playlist.

    Raises:
        NotFoundError: If the playlist is not found.
    """
    playlist = await resolve_playlist(
        playlist_id, uow, user_id=user_id, connector=connector, raise_if_not_found=True
    )
    if playlist is None:  # pragma: no cover — raise_if_not_found=True guarantees this
        msg = f"Playlist {playlist_id} not found"
        raise NotFoundError(msg)
    return playlist


async def require_playlist_link(
    link_id: UUID,
    uow: UnitOfWorkProtocol,
    *,
    user_id: str,
) -> PlaylistLink:
    """Fetch a playlist link and verify the user owns its parent playlist.

    Common gate for delete, update, and sync link use cases. Raises
    ``NotFoundError`` if the link doesn't exist or the parent playlist
    belongs to a different user.

    Args:
        link_id: The playlist link UUID.
        uow: Unit of work for repository access.
        user_id: Authenticated user ID for ownership scoping.

    Returns:
        The validated playlist link.

    Raises:
        NotFoundError: If the link or its parent playlist is not found
            (or belongs to another user).
    """
    link_repo = uow.get_playlist_link_repository()
    link = await link_repo.get_link(link_id)
    if link is None:
        raise NotFoundError(f"Playlist link {link_id} not found")
    # Ownership check: require_playlist raises NotFoundError for wrong user
    await require_playlist(str(link.playlist_id), uow, user_id=user_id)
    return link
