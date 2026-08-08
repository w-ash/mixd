"""Shared Spotify utilities for track processing and data conversion.

Contains common functions used across Spotify connectors for:
- Converting Spotify API data to domain objects
- The widening search ladder shared by every Spotify search caller
- Search → rank → evaluate pipeline shared by cross-discovery and inward resolver
"""

from collections.abc import AsyncGenerator
from contextlib import aclosing
from typing import Protocol

from attrs import define

from src.config.constants import MatchMethod, SpotifyConstants
from src.domain.entities import Artist, Track
from src.domain.matching.algorithms import select_best_by_title_similarity
from src.domain.matching.evaluation_service import TrackMatchEvaluationService
from src.domain.matching.types import MatchResult, RawProviderMatch
from src.infrastructure.connectors.spotify.client import (
    field_filtered_search_query,
    free_text_search_query,
)
from src.infrastructure.connectors.spotify.connector import SpotifyConnector
from src.infrastructure.connectors.spotify.models import SpotifyTrack


class SpotifyTrackSearch(Protocol):
    """The single call the search ladder needs — client and connector both fit."""

    async def search_track(self, query: str, limit: int) -> list[SpotifyTrack]:
        """Send a prepared query to Spotify and return the track candidates."""
        ...


@define(frozen=True, slots=True)
class SpotifySearchPass:
    """One issued search: the query string sent, and what came back for it."""

    query: str
    candidates: list[SpotifyTrack]


@define(frozen=True, slots=True)
class SpotifySearchMatch:
    """Result of the shared search → rank → evaluate pipeline."""

    candidate: SpotifyTrack
    match_result: MatchResult
    similarity: float


@define(frozen=True, slots=True)
class SpotifySearchAttempt:
    """What the pipeline did: every query issued, and the match if one survived.

    ``queries`` holds the strings handed to the client, which are therefore the
    ones on the wire. A caller logs them when nothing came back — a malformed
    query and a genuinely absent track are otherwise indistinguishable in
    production logs. Only the passes actually issued appear.
    """

    queries: tuple[str, ...]
    match: SpotifySearchMatch | None


async def widening_search_passes(
    search: SpotifyTrackSearch,
    artist_name: str,
    track_name: str,
    limit: int,
) -> AsyncGenerator[SpotifySearchPass]:
    """Yield the field-filtered search, then its free-text widening.

    Two rungs, never a ladder: the filtered query is the precise question and
    free text is the only broader one worth asking, so a third phrasing would
    trade precision for another round trip.

    The second pass is issued only when the consumer advances the iterator, so
    the widening cost is bounded to one extra ``/search`` — against the shared
    5 rps limiter — per consumer that asks for it. A consumer that stops at the
    first pass pays exactly one call, which is what keeps a discovery run over
    tens of thousands of misses from silently doubling its search volume.

    Each pass carries the query it sent, so acceptance and telemetry read the
    same string rather than two independently built ones.
    """
    for query in (
        field_filtered_search_query(artist_name, track_name),
        free_text_search_query(artist_name, track_name),
    ):
        yield SpotifySearchPass(
            query=query, candidates=await search.search_track(query, limit)
        )


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


async def search_and_evaluate_attempt(
    connector: SpotifyConnector,
    evaluation_service: TrackMatchEvaluationService,
    track: Track,
    artist_name: str,
    track_name: str,
    *,
    widen: bool,
    require_success: bool,
    min_candidate_duration_ms: int | None = None,
    min_similarity: float = 0.0,
    min_artist_similarity: float | None = None,
    fallback_connector_id: str | None = None,
    limit: int = SpotifyConstants.SEARCH_DEFAULT_LIMIT,
) -> SpotifySearchAttempt:
    """Search Spotify, rank by title similarity, and evaluate match quality.

    Shared pipeline used by both cross-discovery and inward resolver fallback.
    Both acceptance dials are required keywords: each caller pays a different
    price for a wrong answer, and neither default is safe for the other.

    Does NOT catch exceptions — callers handle errors.

    Args:
        connector: Spotify connector for API search.
        evaluation_service: Domain service for match evaluation.
        track: The canonical track being matched.
        artist_name: Artist name for the search query.
        track_name: Track name for the search query.
        widen: Retry the free-text query when the filtered pass yields nothing
            acceptable. Costs one extra ``/search`` per miss against the shared
            limiter, which a caller searching once per unmatched play cannot
            afford and a caller rescuing a durable dead id can.
        require_success: Accept only a candidate that clears the full
            artist+title evaluation, not the title-similarity floor alone.
            Title similarity says nothing about the artist, so without this a
            same-title recording by an unrelated artist is a match.
        min_candidate_duration_ms: Drop candidates shorter than this before
            anything is ranked, so a surviving longer candidate can still win.
            For callers holding evidence of how long the track runs; ``None``
            for callers holding none.
        min_similarity: Minimum title similarity to accept (0.0 = any).
        min_artist_similarity: Minimum artist similarity to accept, or None
            for no floor. ``require_success`` alone is not artist-proof —
            artist disagreement costs so little confidence that a same-title
            wrong-artist recording can clear the auto-accept bar — so callers
            writing durable substitutions set this floor as well.
        fallback_connector_id: ID to use when best candidate has no .id.
            If None and candidate has no .id, the candidate is unusable.
        limit: Candidates requested per query; both passes use the same one.
    """
    queries: list[str] = []
    passes = widening_search_passes(connector, artist_name, track_name, limit)
    async with aclosing(passes):
        async for search_pass in passes:
            queries.append(search_pass.query)
            match = _rank_and_evaluate(
                connector,
                evaluation_service,
                track,
                track_name,
                search_pass.candidates,
                min_similarity=min_similarity,
                min_candidate_duration_ms=min_candidate_duration_ms,
                fallback_connector_id=fallback_connector_id,
            )
            if (
                match is not None
                and (match.match_result.success or not require_success)
                and _clears_artist_floor(match, min_artist_similarity)
            ):
                return SpotifySearchAttempt(queries=tuple(queries), match=match)
            if not widen:
                break

    return SpotifySearchAttempt(queries=tuple(queries), match=None)


def _clears_artist_floor(match: SpotifySearchMatch, floor: float | None) -> bool:
    """False when the candidate's artist is too unlike the hint's.

    Fail-closed on missing evidence when a floor was requested: a caller that
    asked for artist verification is writing a durable substitution, and an
    unverifiable artist is exactly the case it must not accept.
    """
    if floor is None:
        return True
    evidence = match.match_result.evidence
    if evidence is None:
        return False
    return evidence.artist_similarity >= floor


def _clears_minimum_duration(candidate: SpotifyTrack, minimum_ms: int | None) -> bool:
    """False for a candidate too short to be the recording the caller means.

    Silent in both directions where evidence is missing: no minimum, or a
    candidate Spotify reported no length for (``duration_ms`` defaults to 0),
    passes untouched. Absence of evidence is not evidence of a mismatch.
    """
    if minimum_ms is None or not candidate.duration_ms:
        return True
    return candidate.duration_ms >= minimum_ms


def _rank_and_evaluate(
    connector: SpotifyConnector,
    evaluation_service: TrackMatchEvaluationService,
    track: Track,
    track_name: str,
    candidates: list[SpotifyTrack],
    *,
    min_similarity: float,
    min_candidate_duration_ms: int | None,
    fallback_connector_id: str | None,
) -> SpotifySearchMatch | None:
    """Pick the best candidate of one search and evaluate it, or None.

    Impossibly short candidates are discarded BEFORE ranking, not after: the
    title-similarity winner is a single candidate, so vetoing it afterwards
    would throw away a longer, equally-titled candidate further down the list.
    """
    usable = [
        candidate
        for candidate in candidates
        if _clears_minimum_duration(candidate, min_candidate_duration_ms)
    ]
    if not usable:
        return None

    best_result = select_best_by_title_similarity(
        track_name,
        usable,
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
