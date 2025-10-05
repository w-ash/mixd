"""Validation strategies for connector playlist operations.

Provides reusable validation logic for playlist update operations with typed
results using Python 3.13+ patterns.
"""

from typing import Any, TypeIs

from attrs import define, field

from src.config import get_logger

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class ConnectorPlaylistValidationResult:
    """Result of playlist operation validation.

    Provides structured validation feedback with specific failure reasons.
    """

    valid: bool
    issues: list[str] = field(factory=list)
    warnings: list[str] = field(factory=list)
    metadata: dict[str, Any] = field(factory=dict)

    @classmethod
    def success(cls, metadata: dict[str, Any] | None = None) -> "ConnectorPlaylistValidationResult":
        """Create successful validation result."""
        return cls(valid=True, metadata=metadata or {})

    @classmethod
    def failure(
        cls,
        issues: list[str],
        warnings: list[str] | None = None,
    ) -> "ConnectorPlaylistValidationResult":
        """Create failed validation result with issues."""
        return cls(
            valid=False,
            issues=issues,
            warnings=warnings or [],
        )

    @classmethod
    def with_warnings(
        cls,
        warnings: list[str],
        metadata: dict[str, Any] | None = None,
    ) -> "ConnectorPlaylistValidationResult":
        """Create successful validation with warnings."""
        return cls(
            valid=True,
            warnings=warnings,
            metadata=metadata or {},
        )


def is_retryable_error_type(error_type: str) -> TypeIs[str]:
    """Type guard for retryable error classifications."""
    return error_type.lower() in {"timeouterror", "connectionerror", "httperror"}


def is_auth_error_message(error_msg: str) -> TypeIs[str]:
    """Type guard for authentication error messages."""
    msg_lower = error_msg.lower()
    return "auth" in msg_lower or "token" in msg_lower


def is_rate_limit_error(error_msg: str) -> TypeIs[str]:
    """Type guard for rate limit error messages."""
    msg_lower = error_msg.lower()
    return "rate" in msg_lower or "429" in msg_lower


@define(slots=True)
class ConnectorPlaylistUpdateValidator:
    """Validates connector playlist update operations.

    Provides pre/post execution validation and state consistency checks
    for external playlist synchronization operations.
    """

    def validate_pre_execution_state(
        self,
        applied_operations: list[Any],
        operations_applied_count: int,
    ) -> ConnectorPlaylistValidationResult:
        """Validate state before attempting database update.

        Checks if external API operations were actually applied before
        proceeding with local database synchronization.

        Args:
            applied_operations: List of operations that should have been applied
            operations_applied_count: Count from API metadata

        Returns:
            Validation result with any issues or warnings
        """
        issues = []
        warnings = []

        # Check operation count consistency
        if operations_applied_count == 0 and applied_operations:
            issues.append(
                f"No operations were applied despite {len(applied_operations)} requested"
            )
            logger.warning(
                "Pre-execution validation failed: operation count mismatch",
                requested=len(applied_operations),
                applied=operations_applied_count,
            )

        # Warn if partial application
        if 0 < operations_applied_count < len(applied_operations):
            warnings.append(
                f"Partial application: {operations_applied_count}/{len(applied_operations)} operations"
            )

        if issues:
            return ConnectorPlaylistValidationResult.failure(issues, warnings)

        if warnings:
            return ConnectorPlaylistValidationResult.with_warnings(warnings)

        return ConnectorPlaylistValidationResult.success()

    def validate_post_execution_state(
        self,
        validation_passed: bool,
        operations_applied: int,
    ) -> ConnectorPlaylistValidationResult:
        """Validate external API execution results.

        Args:
            validation_passed: Whether API-level validation succeeded
            operations_applied: Number of operations actually applied

        Returns:
            Validation result with warnings if validation issues detected
        """
        warnings = []

        if not validation_passed and operations_applied > 0:
            warnings.append(
                "API validation failed but some operations were applied (inconsistent state)"
            )
            logger.warning(
                "Post-execution validation inconsistency",
                validation_passed=validation_passed,
                operations_applied=operations_applied,
            )

        if warnings:
            return ConnectorPlaylistValidationResult.with_warnings(
                warnings,
                metadata={"validation_passed": validation_passed},
            )

        return ConnectorPlaylistValidationResult.success(
            metadata={"validation_passed": validation_passed}
        )

    def validate_playlist_items_created(
        self,
        track_count: int,
        items_created_count: int,
    ) -> ConnectorPlaylistValidationResult:
        """Validate playlist item creation consistency.

        Checks if the number of created playlist items matches the expected
        track count from the update command.

        Args:
            track_count: Number of tracks in the update command
            items_created_count: Number of playlist items actually created

        Returns:
            Validation result with warnings if counts don't match
        """
        warnings = []

        if items_created_count == 0 and track_count > 0:
            warnings.append(
                f"No playlist items created despite {track_count} tracks in command"
            )
            logger.warning(
                "Playlist item creation validation failed",
                command_tracks=track_count,
                items_created=items_created_count,
            )

        if 0 < items_created_count < track_count:
            warnings.append(
                f"Partial item creation: {items_created_count}/{track_count} items"
            )

        if warnings:
            return ConnectorPlaylistValidationResult.with_warnings(warnings)

        return ConnectorPlaylistValidationResult.success()

    def validate_connector_playlist_entity(
        self,
        connector_name: str,
        connector_playlist_id: str,
    ) -> ConnectorPlaylistValidationResult:
        """Validate connector playlist entity before database save.

        Args:
            connector_name: Name of the connector service
            connector_playlist_id: External playlist identifier

        Returns:
            Validation result with issues if required fields are missing
        """
        issues = []

        if not connector_name:
            issues.append("Connector name cannot be empty")

        if not connector_playlist_id:
            issues.append("Connector playlist identifier cannot be empty")

        if issues:
            return ConnectorPlaylistValidationResult.failure(issues)

        return ConnectorPlaylistValidationResult.success()

    def validate_state_consistency(
        self,
        requested_tracks: int,
        created_items: int,
        requested_operations: int,
        applied_operations: int,
    ) -> ConnectorPlaylistValidationResult:
        """Comprehensive state consistency validation.

        Validates that all counts are consistent across the operation lifecycle.

        Args:
            requested_tracks: Track count from command
            created_items: Items created for database
            requested_operations: Operations planned
            applied_operations: Operations executed

        Returns:
            Validation result with detailed consistency check metadata
        """
        warnings = []
        metadata = {
            "requested_tracks": requested_tracks,
            "created_items": created_items,
            "requested_operations": requested_operations,
            "applied_operations": applied_operations,
            "track_item_ratio": (
                created_items / requested_tracks if requested_tracks > 0 else 0
            ),
            "operation_success_ratio": (
                applied_operations / requested_operations
                if requested_operations > 0
                else 0
            ),
        }

        # Check for major inconsistencies
        if created_items < requested_tracks * 0.5:  # Less than 50% success
            warnings.append(
                f"Low item creation rate: {created_items}/{requested_tracks} tracks"
            )

        if applied_operations < requested_operations * 0.5:  # Less than 50% success
            warnings.append(
                f"Low operation success rate: {applied_operations}/{requested_operations}"
            )

        if warnings:
            return ConnectorPlaylistValidationResult.with_warnings(
                warnings, metadata=metadata
            )

        return ConnectorPlaylistValidationResult.success(metadata=metadata)


def classify_connector_api_error(exception: Exception) -> dict[str, str | bool]:
    """Classify connector API errors using pattern matching.

    Uses Python 3.13+ pattern matching and type guards for error classification.

    Args:
        exception: Exception from connector API call

    Returns:
        Classification dict with error_type, is_retryable, is_auth_error, is_rate_limit
    """
    error_type_name = type(exception).__name__
    error_message = str(exception)

    # Use pattern matching for error type classification
    match error_type_name:
        case "TimeoutError" | "ConnectionError" | "HTTPError":
            is_retryable = True
        case _:
            is_retryable = False

    # Use type guards for message-based classification
    is_auth = is_auth_error_message(error_message)
    is_rate_limit = is_rate_limit_error(error_message)

    return {
        "error_type": error_type_name,
        "is_retryable": is_retryable,
        "is_auth_error": is_auth,
        "is_rate_limit": is_rate_limit,
    }


def classify_database_error(exception: Exception) -> dict[str, str | bool]:
    """Classify database errors for retry and recovery decisions.

    Args:
        exception: Database exception

    Returns:
        Classification dict with error_type, is_constraint_violation, is_connection_error
    """
    error_message = str(exception).lower()

    return {
        "error_type": type(exception).__name__,
        "is_constraint_violation": "constraint" in error_message
        or "unique" in error_message,
        "is_connection_error": "connection" in error_message
        or "timeout" in error_message,
    }
