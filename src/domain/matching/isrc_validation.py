"""Pure domain ISRC validation and quality assessment.

Structural validation beyond format checking, and reliability assessment
for ISRC-based matches to detect known problems like remaster reuse and
clean/explicit version duplication.
"""

import re

from attrs import define

# ISRC structure: CC-XXX-YY-NNNNN (stored without hyphens as 12 chars)
# CC = country code (2 alpha, ISO 3166-1 alpha-2)
# XXX = registrant code (3 alphanumeric)
# YY = year of reference (2 digits)
# NNNNN = designation code (5 digits)
_ISRC_PATTERN = re.compile(r"^[A-Z]{2}[A-Z0-9]{3}\d{2}\d{5}$")

# Duration difference thresholds for ISRC reliability assessment
SUSPECT_DURATION_DIFF_MS = 10_000  # >10s suggests remaster/different version


@define(frozen=True, slots=True)
class ISRCValidationResult:
    """Result of structural ISRC validation."""

    valid: bool
    country_code: str = ""
    registrant_code: str = ""
    year: str = ""
    designation_code: str = ""
    error: str = ""


@define(frozen=True, slots=True)
class ISRCReliability:
    """Assessment of ISRC match reliability between two tracks."""

    suspect: bool
    reason: str = ""


def validate_isrc_structure(isrc: str) -> ISRCValidationResult:
    """Validate ISRC beyond basic format — checks structural components.

    Args:
        isrc: Normalized ISRC (12 chars, uppercase, no hyphens).

    Returns:
        Structured validation result with parsed components.
    """
    if not isrc:
        return ISRCValidationResult(valid=False, error="empty ISRC")

    expected_length = 12
    if len(isrc) != expected_length:
        return ISRCValidationResult(
            valid=False, error=f"expected 12 characters, got {len(isrc)}"
        )

    if not _ISRC_PATTERN.match(isrc):
        return ISRCValidationResult(
            valid=False,
            error="invalid structure: expected CC(alpha)XXX(alphanum)YY(digit)NNNNN(digit)",
        )

    return ISRCValidationResult(
        valid=True,
        country_code=isrc[:2],
        registrant_code=isrc[2:5],
        year=isrc[5:7],
        designation_code=isrc[7:12],
    )


def assess_isrc_match_reliability(
    duration_diff_ms: int | None,
) -> ISRCReliability:
    """Assess whether an ISRC-based match is reliable or potentially suspect.

    ISRC reuse across remasters, clean/explicit versions, and DJ mixes
    is a well-documented industry problem. Duration comparison is the most
    reliable secondary signal.

    Args:
        duration_diff_ms: Absolute duration difference between matched tracks.
            None if either track is missing duration data.

    Returns:
        Reliability assessment with suspect flag and reason.
    """
    if duration_diff_ms is None:
        return ISRCReliability(
            suspect=False,
            reason="duration data unavailable for reliability check",
        )

    if duration_diff_ms > SUSPECT_DURATION_DIFF_MS:
        return ISRCReliability(
            suspect=True,
            reason=f"duration differs by {duration_diff_ms}ms despite matching ISRC — possible remaster or different version",
        )

    return ISRCReliability(suspect=False)
