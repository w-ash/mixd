"""Artist repositories package.

Mirrors ``repositories/track/``: the canonical repository, the connector-side
composition over the cache and the generic mapping mechanism, the favorites
presence store, and the alias cache.
"""

from src.infrastructure.persistence.repositories.artist.aliases import (
    ArtistAliasRepository,
)
from src.infrastructure.persistence.repositories.artist.connector import (
    ARTIST_MAPPING_SHAPE,
    ArtistConnectorRepository,
    ArtistMappingRepository,
    ConnectorArtistRepository,
)
from src.infrastructure.persistence.repositories.artist.core import ArtistRepository
from src.infrastructure.persistence.repositories.artist.favorites import (
    ArtistFavoriteRepository,
)

__all__ = [
    "ARTIST_MAPPING_SHAPE",
    "ArtistAliasRepository",
    "ArtistConnectorRepository",
    "ArtistFavoriteRepository",
    "ArtistMappingRepository",
    "ArtistRepository",
    "ConnectorArtistRepository",
]
