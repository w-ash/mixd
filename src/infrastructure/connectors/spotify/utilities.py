"""Shared Spotify utilities for track processing and data conversion.

Contains common functions used across Spotify connectors for:
- Converting Spotify API data to domain objects
- Search → rank → evaluate pipeline shared by cross-discovery and inward resolver
"""

from attrs import define

from src.config.constants import MatchMethod, SpotifyConstants
from src.domain.entities import Artist, Track
from src.domain.matching.algorithms import select_best_by_title_similarity
from src.domain.matching.evaluation_service import TrackMatchEvaluationService
from src.domain.matching.types import MatchResult, RawProviderMatch
from src.infrastructure.connectors.spotify import SpotifyConnector
from src.infrastructure.connectors.spotify.client import (
    field_filtered_search_query,
    free_text_search_query,
)
from src.infrastructure.connectors.spotify.models import SpotifyTrack


@define(frozen=True, slots=True)
class SpotifySearchMatch:
    """Result of the shared search → rank → evaluate pipeline."""

    candidate: SpotifyTrack
    match_result: MatchResult
    similarity: float


@define(frozen=True, slots=True)
class SpotifySearchAttempt:
    """What the pipeline did: every query issued, and the match if one survived.

    ``queries`` is carried so a caller can log the exact strings sent when
    nothing came back — a malformed query and a genuinely absent track are
    otherwise indistinguishable in production logs.
    """

    queries: tuple[str, ...]
    match: SpotifySearchMatch | None


def create_track_from_spotify_data(
    spotify_id: str, spotify_track: SpotifyTrack
) -> Track:
    """Create a Track domain object from Spotify API data.

    Args:
        spotify_id: Spotify track ID
        spotify_track: Validated SpotifyTrack Pydantic model

    Returns:
        Track domain object with Spotify connector ID attached

    Raises:
        ValueError: If required fields are missing or invalid
    """
    # Validate required fields
    if not spotify_track.name:
        raise ValueError(f"Missing track title for Spotify ID {spotify_id}")

    if not spotify_track.artists:
        raise ValueError(f"Missing artists for Spotify ID {spotify_id}")

    # Create Artist objects
    artists = [Artist(name=a.name) for a in spotify_track.artists if a.name]

    if not artists:
        raise ValueError(f"No valid artist names found for Spotify ID {spotify_id}")

    # Extract optional fields
    album = spotify_track.album.name if spotify_track.album else None
    duration_ms = spotify_track.duration_ms or None
    isrc = spotify_track.external_ids.isrc

    # Create Track object with Spotify connector ID
    return Track(
        title=spotify_track.name,
        artists=artists,
        album=album,
        duration_ms=duration_ms,
        isrc=isrc,
    ).with_connector_track_id("spotify", spotify_id)


async def search_and_evaluate_match(
    connector: SpotifyConnector,
    evaluation_service: TrackMatchEvaluationService,
    track: Track,
    artist_name: str,
    track_name: str,
    *,
    min_similarity: float = 0.0,
    fallback_connector_id: str | None = None,
    limit: int = SpotifyConstants.SEARCH_DEFAULT_LIMIT,
) -> SpotifySearchMatch | None:
    """The match from :func:`search_and_evaluate_attempt`, without the queries."""
    attempt = await search_and_evaluate_attempt(
        connector,
        evaluation_service,
        track,
        artist_name,
        track_name,
        min_similarity=min_similarity,
        fallback_connector_id=fallback_connector_id,
        limit=limit,
    )
    return attempt.match


async def search_and_evaluate_attempt(
    connector: SpotifyConnector,
    evaluation_service: TrackMatchEvaluationService,
    track: Track,
    artist_name: str,
    track_name: str,
    *,
    min_similarity: float = 0.0,
    fallback_connector_id: str | None = None,
    limit: int = SpotifyConstants.SEARCH_DEFAULT_LIMIT,
) -> SpotifySearchAttempt:
    """Search Spotify, rank by title similarity, and evaluate match quality.

    Shared pipeline used by both cross-discovery and inward resolver fallback.
    The field-filtered query runs first. If it yields nothing acceptable — no
    candidates, none clearing ``min_similarity``, or no usable connector ID —
    the search widens ONCE to a free-text query and the same selection and
    evaluation run again. Exactly one widening step, never a ladder: the
    filtered query is the precise question and free text is the only broader
    one worth asking, so a third phrasing would only trade precision for
    another round trip. The connector's rate limiter paces the extra call.

    Does NOT catch exceptions — callers handle errors. Does NOT check
    match_result.success — callers have different acceptance policies.

    Args:
        connector: Spotify connector for API search.
        evaluation_service: Domain service for match evaluation.
        track: The canonical track being matched.
        artist_name: Artist name for the search query.
        track_name: Track name for the search query.
        min_similarity: Minimum title similarity to accept (0.0 = any).
        fallback_connector_id: ID to use when best candidate has no .id.
            If None and candidate has no .id, the candidate is unusable.
        limit: Candidates requested per query; both passes use the same one.
    """
    filtered = await connector.search_track(artist_name, track_name, limit)
    match = _rank_and_evaluate(
        connector,
        evaluation_service,
        track,
        track_name,
        filtered,
        min_similarity=min_similarity,
        fallback_connector_id=fallback_connector_id,
    )
    queries = [field_filtered_search_query(artist_name, track_name)]
    if match is not None:
        return SpotifySearchAttempt(queries=tuple(queries), match=match)

    widened = await connector.search_track(
        artist_name, track_name, limit, field_filtered=False
    )
    queries.append(free_text_search_query(artist_name, track_name))
    return SpotifySearchAttempt(
        queries=tuple(queries),
        match=_rank_and_evaluate(
            connector,
            evaluation_service,
            track,
            track_name,
            widened,
            min_similarity=min_similarity,
            fallback_connector_id=fallback_connector_id,
        ),
    )


def _rank_and_evaluate(
    connector: SpotifyConnector,
    evaluation_service: TrackMatchEvaluationService,
    track: Track,
    track_name: str,
    candidates: list[SpotifyTrack],
    *,
    min_similarity: float,
    fallback_connector_id: str | None,
) -> SpotifySearchMatch | None:
    """Pick the best candidate of one search and evaluate it, or None."""
    if not candidates:
        return None

    best_result = select_best_by_title_similarity(
        track_name,
        candidates,
        lambda c: c.name,
        evaluation_service.config,
        min_similarity=min_similarity,
    )
    if best_result is None:
        return None

    best = best_result.candidate
    connector_id = best.id or fallback_connector_id
    if not connector_id:
        return None

    primary_artist = best.artists[0].name if best.artists else ""
    raw_match = RawProviderMatch(
        connector_id=connector_id,
        match_method=MatchMethod.ARTIST_TITLE,
        service_data={
            "title": best.name,
            "artist": primary_artist,
            "duration_ms": best.duration_ms,
        },
    )
    match_result = evaluation_service.evaluate_single_match(
        track, raw_match, connector.connector_name
    )

    return SpotifySearchMatch(
        candidate=best,
        match_result=match_result,
        similarity=best_result.similarity,
    )
