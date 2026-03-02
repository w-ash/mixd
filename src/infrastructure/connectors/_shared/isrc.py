"""Shared ISRC (International Standard Recording Code) utilities.

Provides normalization and validation for ISRCs used across all connectors
for cross-service track identity matching.
"""

from src.config import get_logger

logger = get_logger(__name__)

ISRC_LENGTH = 12


def _validate_isrc_format(isrc: str) -> bool:
    """Validate ISRC format (12 alphanumeric characters).

    Args:
        isrc: ISRC string to validate (with or without hyphens).

    Returns:
        True if the ISRC is valid.
    """
    if not isrc:
        return False

    cleaned = isrc.replace("-", "")
    return len(cleaned) == ISRC_LENGTH and cleaned.isalnum()


def normalize_isrc(isrc: str) -> str | None:
    """Normalize ISRC to standard format (remove hyphens, uppercase).

    Args:
        isrc: Raw ISRC string from any service.

    Returns:
        Normalized ISRC or None if invalid.
    """
    if not isrc:
        return None

    normalized = isrc.replace("-", "").upper()

    if not _validate_isrc_format(normalized):
        logger.warning(f"Invalid ISRC format: {isrc}")
        return None

    return normalized
