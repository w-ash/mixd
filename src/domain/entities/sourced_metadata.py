"""Shared source priority logic for user-facing metadata.

Preferences, tags, and playlist metadata imports all need to resolve conflicts
when multiple sources (manual action, service import, playlist mapping) claim
different values for the same track. This module defines the priority ranking
and a pure function for conflict resolution.

Reused by: preference.py (v0.7.0), tags (v0.7.2), playlist metadata (v0.7.4).
"""

from typing import Final, Literal

type MetadataSource = Literal["manual", "service_import", "playlist_mapping"]

# Higher number = higher priority. The user's direct opinion always wins.
SOURCE_PRIORITY: Final[dict[MetadataSource, int]] = {
    "service_import": 0,
    "playlist_mapping": 1,
    "manual": 2,
}


def should_override(
    existing_source: MetadataSource, new_source: MetadataSource
) -> bool:
    """Return True when new_source has strictly higher priority than existing_source.

    Same-priority sources do not override — the existing value is kept.
    """
    return SOURCE_PRIORITY[new_source] > SOURCE_PRIORITY[existing_source]
