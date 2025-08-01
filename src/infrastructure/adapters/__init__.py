"""Infrastructure adapters for external service integration.

Pure adapters that handle only source-specific transformations without orchestration logic.
"""

from .spotify_play_adapter import SpotifyPlayAdapter

__all__ = ["SpotifyPlayAdapter"]
