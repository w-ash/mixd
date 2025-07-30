"""Protocol for external metadata providers.

This module defines the contract for fetching fresh metadata from external services
when the external track IDs are already known. This is separate from identity resolution.
"""

from typing import Any, Protocol


class ExternalMetadataProviderProtocol(Protocol):
    """Contract for fetching metadata using known external track IDs.

    This protocol defines the interface for Phase 2 of the architecture refactor,
    where we separate Data Enrichment from Identity Resolution. Providers implementing
    this protocol fetch fresh metadata for tracks whose external service IDs are
    already known and stored in the database.

    Key Principles:
    - ZERO matching logic - IDs must be known beforehand
    - ZERO business decisions - pure data fetching only
    - Used ONLY by EnrichTracksUseCase after identity resolution is complete
    """

    async def fetch_metadata_by_ids(
        self,
        external_ids: list[str],
        **additional_options: Any,
    ) -> dict[str, dict[str, Any]]:
        """Fetch metadata for tracks using known external service IDs.

        Args:
            external_ids: List of external service track IDs (e.g., Spotify track IDs)
            **additional_options: Provider-specific options for metadata fetching

        Returns:
            Dictionary mapping external_id to raw metadata from the service.
            Missing IDs are omitted from results rather than raising errors.

        Raises:
            Exception: Only for unrecoverable errors (network failures, auth issues).

        Note:
            This method assumes external IDs are valid and already verified.
            No identity resolution or matching logic should be performed.
            Providers should handle rate limiting and retries internally.
        """
        ...

    @property
    def service_name(self) -> str:
        """Service identifier (e.g., 'spotify', 'lastfm')."""
        ...

    def supports_batch_requests(self) -> bool:
        """Whether this provider supports batch metadata requests.

        Returns:
            True if provider can efficiently handle multiple IDs in single request
        """
        ...

    def get_max_batch_size(self) -> int:
        """Maximum number of IDs that can be processed in a single batch.

        Returns:
            Maximum batch size for this provider (0 means no limit)
        """
        ...
