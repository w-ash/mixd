"""Pure algorithms for track matching and confidence scoring.

These functions contain no external dependencies and implement the core business logic
for determining how well tracks match across different music services.
"""

from collections.abc import Callable, Sequence
from typing import NotRequired, TypedDict

from attrs import define
from rapidfuzz import fuzz

from .config import MatchingConfig
from .isrc_validation import assess_isrc_match_reliability
from .probabilistic import (
    AttributeResult,
    calculate_match_weight,
    classify_artist,
    classify_duration,
    classify_isrc,
    classify_title,
    weight_to_confidence,
)
from .text_normalization import are_phonetic_matches, normalize_for_comparison
from .types import ConfidenceEvidence


class InternalTrackData(TypedDict):
    title: str
    artists: list[str]
    duration_ms: NotRequired[int | None]
    isrc: NotRequired[str | None]


class ServiceTrackData(TypedDict):
    title: str
    artist: str
    duration_ms: NotRequired[int | None]


def calculate_title_similarity(
    title1: str, title2: str, config: MatchingConfig
) -> float:
    """Calculate title similarity accounting for variations like 'Live', 'Remix', etc.

    Applies text normalization (diacritics, equivalences) before comparison,
    then checks exact → variation → phonetic → fuzzy in descending confidence.
    """
    # Normalize titles for comparison
    lower1, lower2 = title1.lower(), title2.lower()

    # 1. Check if titles are identical (case-insensitive)
    if lower1 == lower2:
        return config.identical_similarity_score

    # 2. Check for containment with extra tokens
    # This catches cases like "Paranoid Android" vs "Paranoid Android - Live"
    variation_markers = [
        "live",
        "remix",
        "acoustic",
        "demo",
        "remaster",
        "radio edit",
        "extended",
        "instrumental",
        "album version",
        "single version",
    ]

    # Check if one is contained in the other with variation markers
    if lower1 in lower2:
        remaining = lower2.replace(lower1, "").strip("- ()[]").strip()
        if any(marker in remaining.lower() for marker in variation_markers):
            return config.variation_similarity_score
    elif lower2 in lower1:
        remaining = lower1.replace(lower2, "").strip("- ()[]").strip()
        if any(marker in remaining.lower() for marker in variation_markers):
            return config.variation_similarity_score

    # 3. Normalize (strip diacritics, equivalences) and check exact match
    norm1 = normalize_for_comparison(title1)
    norm2 = normalize_for_comparison(title2)

    if norm1 == norm2:
        return config.identical_similarity_score

    # 4. Phonetic match (Metaphone) — intermediate tier
    if are_phonetic_matches(title1, title2):
        return config.phonetic_similarity_score

    # 5. Fuzzy match on normalized text for better cross-service comparison
    return fuzz.token_set_ratio(norm1, norm2) / 100.0


@define(frozen=True, slots=True)
class SimilarityResult[T]:
    """Result of selecting the best candidate by title similarity."""

    candidate: T
    similarity: float


def select_best_by_title_similarity[T](
    reference_title: str,
    candidates: Sequence[T],
    get_name: Callable[[T], str | None],
    config: MatchingConfig,
    *,
    min_similarity: float = 0.0,
) -> SimilarityResult[T] | None:
    """Select the candidate with the highest title similarity to *reference_title*.

    Generic over ``T`` so callers don't need to couple the domain to Pydantic
    models (Spotify) or any other concrete type.

    Args:
        reference_title: The title to compare against.
        candidates: Non-empty sequence of candidates to rank.
        get_name: Extracts the comparable name from a candidate (may return None).
        config: Matching configuration for similarity scoring.
        min_similarity: Reject the best candidate if its similarity is below this.

    Returns:
        ``SimilarityResult`` for the best candidate, or ``None`` if *candidates*
        is empty, all names are ``None``, or the best similarity is below
        *min_similarity*.
    """
    best: T | None = None
    best_sim = -1.0

    for candidate in candidates:
        name = get_name(candidate)
        if name is None:
            continue
        sim = calculate_title_similarity(reference_title, name, config)
        if sim > best_sim:
            best_sim = sim
            best = candidate

    if best is None or best_sim < min_similarity:
        return None

    return SimilarityResult(candidate=best, similarity=best_sim)


def calculate_confidence(
    internal_track_data: InternalTrackData,
    service_track_data: ServiceTrackData,
    match_method: str,
    config: MatchingConfig,
) -> tuple[int, ConfidenceEvidence]:
    """Calculate confidence score using Fellegi-Sunter probabilistic model.

    Each attribute comparison produces a log-likelihood ratio where rare
    agreements provide stronger evidence than common ones. The composite
    match weight is converted to a 0-100 confidence score via sigmoid.

    Args:
        internal_track_data: Data from our internal track representation.
        service_track_data: Data from external service.
        match_method: How the track was matched ("isrc", "mbid", "artist_title").
        config: Matching configuration.

    Returns:
        Tuple of (confidence_score, evidence).
    """
    # Get track attributes
    internal_title = internal_track_data.get("title", "")
    internal_artists = internal_track_data.get("artists", [])
    internal_duration = internal_track_data.get("duration_ms")

    service_title = service_track_data.get("title", "")
    service_artist = service_track_data.get("artist", "")
    service_duration = service_track_data.get("duration_ms")

    # ── 1. Title comparison ─────────────────────────────────────────────
    title_similarity = 0.0
    title_is_variation = False
    title_is_phonetic = False

    if internal_title and service_title:
        title_similarity = calculate_title_similarity(
            internal_title, service_title, config
        )
        title_is_variation = title_similarity == config.variation_similarity_score
        title_is_phonetic = (
            not title_is_variation
            and title_similarity == config.phonetic_similarity_score
        )

    title_level = classify_title(
        title_similarity,
        is_phonetic_match=title_is_phonetic,
        is_variation=title_is_variation,
        high_similarity_threshold=config.high_similarity_threshold,
    )

    # ── 2. Artist comparison ────────────────────────────────────────────
    artist_similarity = 0.0
    artist_is_phonetic = False

    if internal_artists and service_artist:
        internal_artist_norm = normalize_for_comparison(internal_artists[0])
        service_artist_norm = normalize_for_comparison(service_artist)

        if internal_artist_norm == service_artist_norm:
            artist_similarity = 1.0
        elif are_phonetic_matches(internal_artists[0], service_artist):
            artist_similarity = config.phonetic_similarity_score
            artist_is_phonetic = True
        else:
            artist_similarity = (
                fuzz.token_sort_ratio(internal_artist_norm, service_artist_norm) / 100.0
            )

    artist_level = classify_artist(
        artist_similarity,
        is_phonetic_match=artist_is_phonetic,
        high_similarity_threshold=config.high_similarity_threshold,
    )

    # ── 3. Duration comparison ──────────────────────────────────────────
    duration_diff_ms: int | None
    if not internal_duration or not service_duration:
        duration_diff_ms = None
    else:
        duration_diff_ms = abs(internal_duration - service_duration)

    duration_level = classify_duration(duration_diff_ms)

    # ── 4. ISRC comparison ──────────────────────────────────────────────
    isrc_suspect = False
    isrc_matched = match_method in ("isrc", "mbid")
    isrc_available = isrc_matched  # ISRC was available if it was used to match

    if isrc_matched:
        reliability = assess_isrc_match_reliability(duration_diff_ms)
        isrc_suspect = reliability.suspect

    isrc_level = classify_isrc(
        isrc_matched=isrc_matched,
        isrc_suspect=isrc_suspect,
        isrc_available=isrc_available,
    )

    # ── 5. Aggregate via Fellegi-Sunter ─────────────────────────────────
    attribute_results = [
        AttributeResult("title", title_level),
        AttributeResult("artist", artist_level),
        AttributeResult("duration", duration_level),
        AttributeResult("isrc", isrc_level),
    ]

    match_weight = calculate_match_weight(attribute_results)
    final_score = weight_to_confidence(match_weight)

    # Per-attribute contributions (log-likelihood ratios) for evidence display
    title_score = title_level.log_likelihood_ratio
    artist_score = artist_level.log_likelihood_ratio
    duration_score = duration_level.log_likelihood_ratio

    evidence = ConfidenceEvidence(
        base_score=final_score,  # In probabilistic model, base_score = final (no penalties)
        title_score=title_score,
        artist_score=artist_score,
        duration_score=duration_score,
        title_similarity=title_similarity,
        artist_similarity=artist_similarity,
        duration_diff_ms=duration_diff_ms if duration_diff_ms is not None else 0,
        final_score=final_score,
        isrc_suspect=isrc_suspect,
        match_weight=match_weight,
    )

    return final_score, evidence
