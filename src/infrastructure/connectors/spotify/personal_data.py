"""Spotify personal data parser for streaming history import."""

from datetime import datetime
import json
from pathlib import Path
from typing import Self, cast

from attrs import define

from src.config import get_logger
from src.domain.entities.shared import JsonDict, json_bool, json_int, json_str

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
    def from_json(cls, data: JsonDict) -> Self:
        """Parse Spotify personal data JSON record.

        Core fields (ts, spotify_track_uri, track/artist/album names, ms_played)
        remain strict — records without these are genuinely invalid.
        Behavioral metadata fields use safe defaults since they're optional
        in real Spotify exports.
        """
        return cls(
            timestamp=datetime.fromisoformat(json_str(data["ts"])),
            track_uri=json_str(data["spotify_track_uri"]),
            track_name=json_str(data["master_metadata_track_name"]),
            artist_name=json_str(data["master_metadata_album_artist_name"]),
            album_name=json_str(data["master_metadata_album_album_name"]),
            ms_played=json_int(data["ms_played"]),
            platform=json_str(data.get("platform", "unknown"), "unknown"),
            country=json_str(data.get("conn_country", "unknown"), "unknown"),
            reason_start=json_str(data.get("reason_start", "unknown"), "unknown"),
            reason_end=json_str(data.get("reason_end", "unknown"), "unknown"),
            shuffle=json_bool(data.get("shuffle", False)),
            skipped=json_bool(data.get("skipped", False)),
            offline=json_bool(data.get("offline", False)),
            incognito_mode=json_bool(data.get("incognito_mode", False)),
        )


def parse_spotify_personal_data(file_path: Path) -> list[SpotifyPlayRecord]:
    """Parse Spotify personal data JSON file into play records."""
    logger.info(f"Parsing Spotify personal data file: {file_path}")

    with file_path.open(encoding="utf-8") as f:
        data = cast("list[JsonDict]", json.load(f))

    # Filter out non-music content and parse records
    records: list[SpotifyPlayRecord] = []
    for item in data:
        if item.get("spotify_track_uri") and item.get("master_metadata_track_name"):
            try:
                records.append(SpotifyPlayRecord.from_json(item))
            except (KeyError, ValueError, TypeError) as e:
                logger.warning(
                    "Skipping malformed Spotify record",
                    error=str(e),
                    track_name=str(item.get("master_metadata_track_name", "unknown")),
                    artist_name=str(
                        item.get("master_metadata_album_artist_name", "unknown")
                    ),
                )
                continue

    logger.info(f"Parsed {len(records)} play records")
    return records
