"""Static metric configuration for integrations.

This module contains all connector metric definitions, mappings, and freshness values.
It eliminates the need for dynamic registration and complex lookups.

The module provides:
- CONNECTOR_METRICS: Dictionary mapping connectors to their supported metrics
- FIELD_MAPPINGS: Dictionary mapping metric names to connector field names
- METRIC_FRESHNESS: Dictionary defining how often metrics should be refreshed
- Helper functions for retrieving metric configuration
"""

# All supported metrics by connector
CONNECTOR_METRICS = {
    "lastfm": [
        "lastfm_user_playcount",
        "lastfm_global_playcount",
        "lastfm_listeners",
    ],
    "spotify": [
        "spotify_popularity",
        "explicit_flag",
    ],
}

# Field mappings from metric names to connector field names
FIELD_MAPPINGS = {
    # LastFM
    "lastfm_user_playcount": "userplaycount",
    "lastfm_global_playcount": "playcount",
    "lastfm_listeners": "listeners",
    # Spotify
    "spotify_popularity": "popularity",
    "explicit_flag": "explicit",
}

# Freshness periods in hours
METRIC_FRESHNESS = {
    # Default freshness
    "default": 24.0,
    # Specific overrides
    "lastfm_user_playcount": 1.0,
    "lastfm_global_playcount": 24.0,
    "lastfm_listeners": 24.0,
    "spotify_popularity": 24.0,
}


def get_metric_freshness(metric_name: str) -> float:
    """Get freshness period for a metric in hours.

    Args:
        metric_name: Name of the metric to get freshness for

    Returns:
        Number of hours after which the metric should be considered stale
    """
    return METRIC_FRESHNESS.get(metric_name, METRIC_FRESHNESS["default"])


def get_field_name(metric_name: str) -> str:
    """Get the connector field name for a given metric.

    Args:
        metric_name: Name of the metric to get field name for

    Returns:
        Field name in the connector's API response structure
    """
    return FIELD_MAPPINGS.get(metric_name, metric_name)
