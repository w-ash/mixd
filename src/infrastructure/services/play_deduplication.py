"""Identifies duplicate listening events across music streaming services.

When users track their music on multiple platforms (e.g., Spotify + Last.fm),
the same song play gets recorded twice. This module detects and scores these
duplicates by comparing track metadata and timestamps within a time window.
"""

import operator

from src.domain.entities import TrackPlay
from src.domain.matching.algorithms import calculate_confidence
from src.domain.matching.types import ConfidenceEvidence

# NOTE: Redundant helper functions removed - now using TrackPlay methods directly


def calculate_play_match_confidence(
    play1: TrackPlay,
    play2: TrackPlay,
    time_window_seconds: int = 300,
) -> tuple[int, ConfidenceEvidence]:
    """Scores likelihood that two plays represent the same listening event.

    Compares track metadata (title, artist, duration) and applies time-based
    penalty for plays further apart. Returns 0-100 confidence score where
    higher values indicate more likely duplicates.

    Args:
        play1: First play (typically Spotify).
        play2: Second play (typically Last.fm).
        time_window_seconds: Maximum time difference to consider a match.
            Defaults to 300 (5 minutes).

    Returns:
        Confidence score (0-100) and detailed scoring evidence.
    """
    # Check time window first - fail fast if outside window
    time_diff_seconds = abs((play1.played_at - play2.played_at).total_seconds())
    if time_diff_seconds > time_window_seconds:
        # Outside time window - no match
        evidence = ConfidenceEvidence(
            base_score=0,
            final_score=0,
        )
        return 0, evidence

    # Convert plays to data format expected by existing confidence system
    play1_data = play1.to_track_metadata()
    play2_data = play2.to_track_metadata()

    # Use play with more complete data as the "internal track"
    if len(play1_data) >= len(play2_data):
        internal_track = play1.to_track()
        service_track_data = play2_data
    else:
        internal_track = play2.to_track()
        service_track_data = play1_data

    # Calculate base confidence using existing track matching logic
    # Convert track to dict format for domain function
    internal_track_data = {
        "title": internal_track.title,
        "artists": [artist.name for artist in internal_track.artists]
        if internal_track.artists
        else [],
        "duration_ms": internal_track.duration_ms,
    }

    base_confidence, evidence = calculate_confidence(
        internal_track_data=internal_track_data,
        service_track_data=service_track_data,
        match_method="cross_service_time_match",
    )

    # Apply time-based penalty to reduce confidence
    # Linear penalty: 0 seconds = no penalty, time_window_seconds = max penalty
    time_penalty_factor = time_diff_seconds / time_window_seconds
    time_penalty = int(20 * time_penalty_factor)  # Max 20 point penalty

    # Calculate final confidence
    final_confidence = max(0, base_confidence - time_penalty)

    # Update evidence with time information
    evidence = ConfidenceEvidence(
        base_score=evidence.base_score,
        title_score=evidence.title_score,
        artist_score=evidence.artist_score,
        duration_score=evidence.duration_score
        - time_penalty,  # Include time penalty in duration
        title_similarity=evidence.title_similarity,
        artist_similarity=evidence.artist_similarity,
        duration_diff_ms=int(
            time_diff_seconds * 1000
        ),  # Store time diff as "duration" diff
        final_score=final_confidence,
    )

    return final_confidence, evidence


def find_potential_duplicate_plays(
    target_play: TrackPlay,
    candidate_plays: list[TrackPlay],
    time_window_seconds: int = 300,
    min_confidence: int = 70,
) -> list[tuple[TrackPlay, int, ConfidenceEvidence]]:
    """Finds plays that could be duplicates of the target play.

    Filters candidates by time window and service type, then scores each
    potential match. Only returns plays above the confidence threshold,
    sorted by match strength.

    Args:
        target_play: Play to find duplicates for.
        candidate_plays: List of potential duplicate plays to check.
        time_window_seconds: Time window for matching. Defaults to 300 (5 minutes).
        min_confidence: Minimum confidence threshold (0-100). Defaults to 70.

    Returns:
        List of (play, confidence_score, evidence) tuples for potential
        duplicates, sorted by confidence (highest first).
    """
    duplicates = []

    for candidate in candidate_plays:
        # Skip same service comparisons (handled by database deduplication)
        if target_play.service == candidate.service:
            continue

        # Skip if not within time window (optimization)
        time_diff = abs((target_play.played_at - candidate.played_at).total_seconds())
        if time_diff > time_window_seconds:
            continue

        # Calculate match confidence using existing system
        confidence, evidence = calculate_play_match_confidence(
            target_play, candidate, time_window_seconds
        )

        # Only include matches above confidence threshold
        if confidence >= min_confidence:
            duplicates.append((candidate, confidence, evidence))

    # Sort by confidence (highest first)
    duplicates.sort(key=operator.itemgetter(1), reverse=True)

    return duplicates
