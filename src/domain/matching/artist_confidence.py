"""Confidence scoring for artist identity resolution.

Artist matching reuses the track side's Fellegi-Sunter machinery rather than a
fresh table of literals: "direct ID 100, MBID 95, name 80" would recreate the
scattered-constants defect the v0.8.18 repairs removed, and a literal cannot be
combined with other evidence or recalibrated. Every level here is a
:class:`~src.domain.matching.probabilistic.ComparisonLevel`, so the artist
score and the track score are the same currency and both move with the matcher
version.
"""

from .config import MatchingConfig
from .probabilistic import (
    ARTIST_ALIAS_NAME,
    ARTIST_ID_CONNECTOR,
    ARTIST_ID_MBID,
    ARTIST_NAME_LASTFM,
    AttributeResult,
    ComparisonLevel,
    calculate_match_weight,
    classify_artist,
    weight_to_confidence,
)
from .types import ArtistEvidence, ArtistEvidenceLevel

_IDENTITY_LEVELS: dict[ArtistEvidenceLevel, ComparisonLevel] = {
    "connector_id": ARTIST_ID_CONNECTOR,
    "mbid": ARTIST_ID_MBID,
    "alias_name": ARTIST_ALIAS_NAME,
}


def calculate_artist_confidence(
    level: ArtistEvidenceLevel,
    *,
    name_similarity: float | None = None,
    lastfm: bool = False,
    config: MatchingConfig,
) -> ArtistEvidence:
    """Score one artist match and return the evidence behind it.

    Args:
        level: What the match was decided on.
        name_similarity: Name agreement (0.0-1.0) for the ``name`` level.
            Ignored by the identifier levels, which do not rest on the string.
        lastfm: Whether Last.fm supplied the agreement. Caps the weight: its
            one-page-per-name model co-mingles same-name artists, so no
            Last.fm evidence may reach the auto-accept band however strong the
            string agreement looks.
        config: Matching configuration, for the similarity tier boundary.

    Returns:
        :class:`~src.domain.matching.types.ArtistEvidence` carrying the level,
        the resulting 0-100 score and the raw match weight.
    """
    comparison = _comparison_level(
        level, name_similarity, config.high_similarity_threshold
    )
    if lastfm:
        comparison = _capped(comparison)

    match_weight = calculate_match_weight([AttributeResult("artist", comparison)])

    return ArtistEvidence(
        level=level,
        name_similarity=name_similarity,
        lastfm=lastfm,
        final_score=weight_to_confidence(match_weight),
        match_weight=match_weight,
    )


def _comparison_level(
    level: ArtistEvidenceLevel,
    name_similarity: float | None,
    high_similarity_threshold: float,
) -> ComparisonLevel:
    """The level an artist match is priced at.

    Identifier levels are their own tier. A ``name`` match falls back to the
    track side's ``classify_artist``, so one string-agreement scale serves both
    and a tier boundary moves in exactly one place.
    """
    identity = _IDENTITY_LEVELS.get(level)
    if identity is not None:
        return identity
    return classify_artist(
        name_similarity,
        is_phonetic_match=False,
        high_similarity_threshold=high_similarity_threshold,
    )


def _capped(level: ComparisonLevel) -> ComparisonLevel:
    """The weaker of *level* and the Last.fm cap.

    A ceiling, not a floor: capped evidence never scores higher than the cap,
    and a weak agreement is not lifted to it either.
    """
    if level.log_likelihood_ratio <= ARTIST_NAME_LASTFM.log_likelihood_ratio:
        return level
    return ARTIST_NAME_LASTFM
