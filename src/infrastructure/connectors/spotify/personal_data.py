"""Spotify personal data parser for streaming history import."""

from datetime import datetime
import json
from pathlib import Path
from typing import Any

from attrs import define

from src.config import get_logger

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class SpotifyPlayRecord:
    """Raw Spotify play record from personal data export."""

    timestamp: datetime
    track_uri: str
    track_name: str
    artist_name: str
    album_name: str
    ms_played: int
    platform: str
    country: str
    reason_start: str
    reason_end: str
    shuffle: bool
    skipped: bool
    offline: bool
    incognito_mode: bool

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "SpotifyPlayRecord":
        """Parse Spotify personal data JSON record."""
        return cls(
            timestamp=datetime.fromisoformat(data["ts"].replace("Z", "+00:00")),
            track_uri=data["spotify_track_uri"],
            track_name=data["master_metadata_track_name"],
            artist_name=data["master_metadata_album_artist_name"],
            album_name=data["master_metadata_album_album_name"],
            ms_played=data["ms_played"],
            platform=data["platform"],
            country=data["conn_country"],
            reason_start=data["reason_start"],
            reason_end=data["reason_end"],
            shuffle=data["shuffle"],
            skipped=data["skipped"],
            offline=data["offline"],
            incognito_mode=data["incognito_mode"],
        )


def parse_spotify_personal_data(file_path: Path) -> list[SpotifyPlayRecord]:
    """Parse Spotify personal data JSON file into play records."""
    logger.info(f"Parsing Spotify personal data file: {file_path}")

    with file_path.open() as f:
        data = json.load(f)

    # Filter out non-music content and parse records
    records = []
    for item in data:
        if item.get("spotify_track_uri") and item.get("master_metadata_track_name"):
            try:
                records.append(SpotifyPlayRecord.from_json(item))
            except (KeyError, ValueError) as e:
                logger.warning(f"Skipping malformed record: {e}")
                continue

    logger.info(f"Parsed {len(records)} play records")
    return records
