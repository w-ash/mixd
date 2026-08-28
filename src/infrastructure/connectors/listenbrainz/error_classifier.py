"""ListenBrainz error classification for retry behavior.

The shared ``HTTPErrorClassifier`` template covers everything ListenBrainz
needs: 429 → rate_limit (the shared retry policy honors ``Retry-After``),
5xx → temporary, 400 → permanent (a malformed lookup body must fail fast,
never retry). No service-specific hook required.
"""

from typing import override

from src.infrastructure.connectors._shared.error_classifier import (
    HTTPErrorClassifier,
)


class ListenBrainzErrorClassifier(HTTPErrorClassifier):
    """ListenBrainz classifier on the shared HTTP classification template."""

    @property
    @override
    def service_name(self) -> str:
        """Service name for logging."""
        return "listenbrainz"
