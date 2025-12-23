"""Register workflow nodes for music data processing pipeline.

This module defines all available nodes that process track collections through
import, enrichment, filtering, sorting, and export stages. Each node handles
specific music data operations like fetching playlists from Spotify, adding
Last.fm play counts, filtering by release date, or creating new playlists.

The registration pattern maps node IDs (e.g., "source.playlist") to their
implementations, enabling workflow composition through configuration files.
"""

from __future__ import annotations

from .node_factories import (
    create_destination_node,
    create_enricher_node,
    create_play_history_enricher_node,
    make_node,
)
from .node_registry import node
from .source_nodes import playlist_source, source_liked_tracks, source_played_tracks

# === SOURCE NODES ===
node(
    "source.playlist",
    description="Fetches a playlist from any connector or canonical source with smart ID resolution",
    output_type="tracklist",
)(playlist_source)

# Basic data sources that work with filter and sorter nodes
node(
    "source.liked_tracks",
    description="Retrieves liked tracks from canonical database for composition with filters/sorters",
    output_type="tracklist",
)(source_liked_tracks)

node(
    "source.played_tracks",
    description="Retrieves tracks from play history for composition with filters/sorters",
    output_type="tracklist",
)(source_played_tracks)

# === ENRICHER NODES ===
# LastFm enricher
node(
    "enricher.lastfm",
    description="Resolves tracks to Last.fm and fetches play counts",
    input_type="tracklist",
    output_type="tracklist",
)(
    create_enricher_node({
        "connector": "lastfm",
        "attributes": ["lastfm_user_playcount", "lastfm_global_playcount"],
    }),
)

# Spotify metadata enricher
node(
    "enricher.spotify",
    description="Enriches tracks with Spotify popularity and explicit flags",
    input_type="tracklist",
    output_type="tracklist",
)(
    create_enricher_node({
        "connector": "spotify",
        "attributes": ["spotify_popularity", "explicit_flag"],
    }),
)

# Play history enricher
node(
    "enricher.play_history",
    description="Enriches tracks with play counts and listening history from internal database",
    input_type="tracklist",
    output_type="tracklist",
)(create_play_history_enricher_node())
# === FILTER NODES ===
node(
    "filter.deduplicate",
    description="Removes duplicate tracks",
    input_type="tracklist",
    output_type="tracklist",
)(make_node("filter", "deduplicate"))

node(
    "filter.by_release_date",
    description="Filters tracks by release date range",
    input_type="tracklist",
    output_type="tracklist",
)(make_node("filter", "by_release_date"))

node(
    "filter.by_tracks",
    description="Excludes tracks from input that are present in exclusion source",
    input_type="tracklist",
    output_type="tracklist",
)(make_node("filter", "by_tracks"))

node(
    "filter.by_artists",
    description="Excludes tracks whose artists appear in exclusion source",
    input_type="tracklist",
    output_type="tracklist",
)(make_node("filter", "by_artists"))

node(
    "filter.by_metric",
    description="Filters tracks based on metric value range",
    input_type="tracklist",
    output_type="tracklist",
)(make_node("filter", "by_metric"))

# Unified play history filter
node(
    "filter.by_play_history",
    description="Filters tracks by play count and/or listening date with flexible constraints",
    input_type="tracklist",
    output_type="tracklist",
)(make_node("filter", "by_play_history"))

# === SORTER NODES ===
node(
    "sorter.by_metric",
    description="Sorts tracks by any metric specified in config",
    input_type="tracklist",
    output_type="tracklist",
)(make_node("sorter", "by_metric"))

node(
    "sorter.by_play_history",
    description="Sorts tracks by play frequency within optional time windows",
    input_type="tracklist",
    output_type="tracklist",
)(make_node("sorter", "by_play_history"))

node(
    "sorter.weighted_shuffle",
    description="Shuffles tracks with configurable strength (0.0=original order, 1.0=fully random)",
    input_type="tracklist",
    output_type="tracklist",
)(make_node("sorter", "weighted_shuffle"))

# === SELECTOR NODES ===
node(
    "selector.limit_tracks",
    description="Limits playlist to specified number of tracks",
    input_type="tracklist",
    output_type="tracklist",
)(make_node("selector", "limit_tracks"))

# === COMBINER NODES ===
node(
    "combiner.merge_playlists",
    description="Combines multiple playlists into one",
    input_type="tracklist",
    output_type="tracklist",
)(make_node("combiner", "merge_playlists"))

node(
    "combiner.concatenate_playlists",
    description="Joins playlists in specified order",
    input_type="tracklist",
    output_type="tracklist",
)(make_node("combiner", "concatenate_playlists"))

node(
    "combiner.interleave_playlists",
    description="Interleaves tracks from multiple playlists",
    input_type="tracklist",
    output_type="tracklist",
)(make_node("combiner", "interleave_playlists"))

# === DESTINATION NODES ===
node(
    "destination.create_playlist",
    description="Creates a playlist with optional connector sync",
    input_type="tracklist",
    output_type="playlist_id",
)(create_destination_node("create_playlist"))

node(
    "destination.update_playlist",
    description="Updates playlists with sophisticated differential operations",
    input_type="tracklist",
    output_type="playlist_id",
)(create_destination_node("update_playlist"))
