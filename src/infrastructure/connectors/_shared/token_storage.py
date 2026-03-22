"""Token storage protocol and file-backed implementation.

Abstracts credential persistence so connectors work with either file-based
storage (CLI development) or database-backed storage (hosted deployment).

The protocol is intentionally in infrastructure (_shared/), not domain —
token storage is a pure infrastructure concern with no business logic.
"""

import json
from pathlib import Path
from typing import Protocol, TypedDict, cast

from attrs import define, field

from src.config import get_logger

logger = get_logger(__name__)


class StoredToken(TypedDict, total=False):
    """Token data as stored. All fields optional to support both Spotify and Last.fm.

    Spotify uses: access_token, refresh_token, token_type, expires_in, expires_at, scope
    Last.fm uses: session_key
    Both use: account_name
    """

    access_token: str
    refresh_token: str
    session_key: str
    token_type: str
    expires_in: int
    expires_at: int  # Unix timestamp
    scope: str
    account_name: str
    extra_data: dict[str, object]


class TokenStorage(Protocol):
    """Protocol for reading/writing OAuth tokens and session keys.

    Implementations handle file-based storage (CLI development) or
    database-backed storage (hosted deployment).
    """

    async def load_token(self, service: str) -> StoredToken | None:
        """Load stored token for a service. Returns None if no token exists."""
        ...

    async def save_token(self, service: str, token_data: StoredToken) -> None:
        """Persist token data for a service. Upserts (creates or replaces)."""
        ...

    async def delete_token(self, service: str) -> None:
        """Remove stored token for a service."""
        ...


# ---------------------------------------------------------------------------
# FILE-BACKED IMPLEMENTATION (CLI / local development)
# ---------------------------------------------------------------------------


@define(slots=True)
class FileTokenStorage:
    """File-backed token storage for CLI development.

    Reads/writes JSON files per service. For Spotify, uses the existing
    .spotify_cache format for backward compatibility with spotipy.
    """

    cache_dir: Path = field(factory=Path)

    def _path_for(self, service: str) -> Path:
        if service == "spotify":
            return self.cache_dir / ".spotify_cache"
        return self.cache_dir / f".{service}_cache"

    async def load_token(self, service: str) -> StoredToken | None:
        try:
            return cast(StoredToken, json.loads(self._path_for(service).read_text()))
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to read {service} token cache: {e}")
            return None

    async def save_token(self, service: str, token_data: StoredToken) -> None:
        try:
            self._path_for(service).write_text(json.dumps(token_data))
        except OSError as e:
            logger.warning(f"Failed to write {service} token cache: {e}")

    async def delete_token(self, service: str) -> None:
        try:
            self._path_for(service).unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning(f"Failed to delete {service} token cache: {e}")


# ---------------------------------------------------------------------------
# FACTORY
# ---------------------------------------------------------------------------


def get_token_storage() -> TokenStorage:
    """Return the appropriate TokenStorage implementation.

    Always returns DatabaseTokenStorage since mixd is PostgreSQL-only (v0.5.1+).
    FileTokenStorage is available for explicit use in CLI or testing.
    """
    from src.infrastructure.persistence.repositories.token_storage import (
        DatabaseTokenStorage,
    )

    return DatabaseTokenStorage()
