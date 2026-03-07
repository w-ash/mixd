"""Register workflow nodes for music data processing pipeline.

This module defines all available nodes that process track collections through
import, enrichment, filtering, sorting, and export stages. Each node handles
specific music data operations like fetching playlists from Spotify, adding
Last.fm play counts, filtering by release date, or creating new playlists.

Source, enricher, and destination nodes are registered explicitly (each has
unique factory logic). Transform and combiner nodes are auto-registered from
their registries — adding a new filter/sorter/selector/combiner only requires
touching transform_definitions.py.
"""

from .destination_nodes import create_playlist, update_playlist
from .node_factories import (
    build_external_enrichment_config,
    build_play_history_enrichment_config,
    create_enricher_node,
    make_combiner_node,
    make_node,
)
from .node_registry import node
from .source_nodes import playlist_source, source_liked_tracks, source_played_tracks
from .transform_definitions import COMBINER_REGISTRY, TRANSFORM_REGISTRY

# === SOURCE NODES ===
_ = node(
    "source.playlist",
    description="Fetches a playlist from any connector or canonical source with smart ID resolution",
    output_type="tracklist",
)(playlist_source)

_ = node(
    "source.liked_tracks",
    description="Retrieves liked tracks from canonical database for composition with filters/sorters",
    output_type="tracklist",
)(source_liked_tracks)

_ = node(
    "source.played_tracks",
    description="Retrieves tracks from play history for composition with filters/sorters",
    output_type="tracklist",
)(source_played_tracks)

# === ENRICHER NODES ===
_ = node(
    "enricher.lastfm",
    description="Resolves tracks to Last.fm and fetches play counts",
    input_type="tracklist",
    output_type="tracklist",
)(
    create_enricher_node(
        build_external_enrichment_config({
            "connector": "lastfm",
            "attributes": ["lastfm_user_playcount", "lastfm_global_playcount"],
        }),
        enricher_label="lastfm",
    ),
)

_ = node(
    "enricher.spotify",
    description="Enriches tracks with Spotify explicit flags",
    input_type="tracklist",
    output_type="tracklist",
)(
    create_enricher_node(
        build_external_enrichment_config({
            "connector": "spotify",
            "attributes": ["explicit_flag"],
        }),
        enricher_label="spotify",
    ),
)

_ = node(
    "enricher.play_history",
    description="Enriches tracks with play counts and listening history from internal database",
    input_type="tracklist",
    output_type="tracklist",
)(create_enricher_node(build_play_history_enrichment_config))

# === TRANSFORM NODES (auto-registered from TRANSFORM_REGISTRY) ===
for _category, _entries in TRANSFORM_REGISTRY.items():
    for _node_type, _entry in _entries.items():
        _ = node(
            f"{_category}.{_node_type}",
            description=_entry.description,
            input_type="tracklist",
            output_type="tracklist",
        )(make_node(_category, _node_type))

# === COMBINER NODES (auto-registered from COMBINER_REGISTRY) ===
for _combiner_type, _combiner_entry in COMBINER_REGISTRY.items():
    _ = node(
        f"combiner.{_combiner_type}",
        description=_combiner_entry.description,
        input_type="tracklist",
        output_type="tracklist",
    )(make_combiner_node(_combiner_type))

# === DESTINATION NODES ===
_ = node(
    "destination.create_playlist",
    description="Creates a playlist with optional connector sync",
    input_type="tracklist",
    output_type="playlist_id",
)(create_playlist)

_ = node(
    "destination.update_playlist",
    description="Updates playlists with sophisticated differential operations",
    input_type="tracklist",
    output_type="playlist_id",
)(update_playlist)
