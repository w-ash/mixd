"""Apple Music connector module (future implementation).

This module will contain the Apple Music API integration when implemented.
Currently contains only the error classifier for future use.

Components (planned):
- AppleMusicAPIClient: Pure API wrapper with authentication
- AppleMusicOperations: Business logic for complex workflows
- AppleMusicConnector: Main facade implementing connector protocols

Usage (future):
    from src.infrastructure.connectors.apple_music import AppleMusicConnector
    connector = AppleMusicConnector()
    # ... Apple Music operations
"""

# Currently only error classifier is implemented
from src.infrastructure.connectors.apple_music.connector import get_connector_config
from src.infrastructure.connectors.apple_music.error_classifier import (
    AppleMusicErrorClassifier,
)

__all__ = [
    "AppleMusicErrorClassifier",
    "get_connector_config",
]
