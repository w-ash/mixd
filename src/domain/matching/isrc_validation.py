"""Pure domain ISRC quality assessment.

Reliability assessment for ISRC-based matches to detect known problems like
remaster reuse and clean/explicit version duplication.
"""

from typing import Final

from attrs import define

# Duration difference threshold for ISRC reliability assessment: the line
# between an ISRC_EXACT and an ISRC_SUSPECT comparison level. >10s suggests a
# remaster or a different version. Part of the matcher-version surface —
# ``src.domain.matching.version`` hashes it, so changing this number changes
# the matcher version.
SUSPECT_DURATION_DIFF_MS: Final[int] = 10_000


@define(frozen=True, slots=True)
class ISRCReliability:
    """Assessment of ISRC match reliability between two tracks."""

    suspect: bool
    reason: str = ""


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


def compute_duration_diff_ms(a: int | None, b: int | None) -> int | None:
    """Absolute duration difference in ms, or None if either side is unknown.

    Companion to ``assess_isrc_match_reliability``, which every ISRC-suspect
    call site feeds. A missing OR zero duration counts as unknown (returns
    None) so an absent duration never reads as a 0ms "perfect" match — the
    truthiness guard the call sites shared before this was centralized.
    """
    if a and b:
        return abs(a - b)
    return None
