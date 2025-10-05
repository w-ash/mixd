"""Last.fm API client - Pure API wrapper with retry handling."""

import asyncio
from datetime import datetime
from enum import Enum
import functools
from typing import Any, TypeVar

from attrs import define, field
import backoff
import pylast

from src.config import get_logger, resilient_operation, settings
from src.infrastructure.connectors._shared.error_classification import (
    create_backoff_handler,
    create_giveup_handler,
    should_giveup_on_error,
)
from src.infrastructure.connectors.lastfm.error_classifier import LastFMErrorClassifier

logger = get_logger(__name__).bind(service="lastfm_client")

T = TypeVar("T")


# -------------------------------------------------------------------------
# DECORATOR FACTORY
# -------------------------------------------------------------------------


def _lastfm_retry_backoff(operation_name: str):
    """Decorator factory for Last.fm API retries with backoff."""

    def decorator(func):
        @resilient_operation(operation_name)
        @backoff.on_exception(
            backoff.expo,
            pylast.WSError,
            max_tries=settings.api.lastfm_retry_count_rate_limit,
            giveup=should_giveup_on_error(LastFMErrorClassifier(), "lastfm"),
            on_backoff=create_backoff_handler(LastFMErrorClassifier(), "lastfm"),
            on_giveup=create_giveup_handler(LastFMErrorClassifier(), "lastfm"),
            max_time=settings.api.lastfm_retry_max_delay,
        )
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            return await func(*args, **kwargs)

        return wrapper

    return decorator


# -------------------------------------------------------------------------
# XML PARSER TYPE
# -------------------------------------------------------------------------


class ParserType(Enum):
    """XML parser type for extraction methods."""

    ELEMENT_TREE = "element_tree"
    MINIDOM = "minidom"


# -------------------------------------------------------------------------
# CLIENT
# -------------------------------------------------------------------------


@define(slots=True)
class LastFMAPIClient:
    """Last.fm API client with authentication and retry logic."""

    api_key: str | None = field(default=None)
    api_secret: str | None = field(default=None)
    lastfm_username: str | None = field(default=None)
    client: pylast.LastFMNetwork | None = field(default=None, init=False, repr=False)
    lastfm_password_hash: str | None = field(default=None, init=False, repr=False)

    def __attrs_post_init__(self) -> None:
        """Initialize Last.fm client with authentication."""
        self.api_key = self.api_key or settings.credentials.lastfm_key
        self.api_secret = self.api_secret or (
            settings.credentials.lastfm_secret.get_secret_value()
            if settings.credentials.lastfm_secret
            else None
        )
        self.lastfm_username = (
            self.lastfm_username or settings.credentials.lastfm_username
        )

        if not self.api_key:
            logger.warning("Last.fm API key not provided")
            return

        client_args = {
            "api_key": self.api_key,
            "api_secret": self.api_secret,
            "username": self.lastfm_username,
        }

        # Add password for write operations
        lastfm_password = (
            settings.credentials.lastfm_password.get_secret_value()
            if settings.credentials.lastfm_password
            else None
        )
        if self.api_secret and self.lastfm_username and lastfm_password:
            self.lastfm_password_hash = pylast.md5(lastfm_password)
            client_args["password_hash"] = self.lastfm_password_hash

        self.client = pylast.LastFMNetwork(**client_args)

    @property
    def is_configured(self) -> bool:
        """Check if client is properly configured."""
        return self.client is not None

    # -------------------------------------------------------------------------
    # XML EXTRACTION HELPERS
    # -------------------------------------------------------------------------

    def _extract_text(
        self, element, tag_name: str, parser: ParserType = ParserType.ELEMENT_TREE
    ) -> str | None:
        """Extract text from XML element (ElementTree or minidom)."""
        try:
            if parser == ParserType.ELEMENT_TREE:
                child = element.find(tag_name)
                if child is not None and child.text and child.text.strip():
                    return child.text.strip()
            else:  # MINIDOM
                child_elements = element.getElementsByTagName(tag_name)
                if child_elements and child_elements[0].firstChild:
                    text = child_elements[0].firstChild.nodeValue.strip()
                    return text or None
        except (AttributeError, IndexError):
            return None
        return None

    def _extract_int(
        self, element, tag_name: str, parser: ParserType = ParserType.ELEMENT_TREE
    ) -> int | None:
        """Extract integer from XML element."""
        text = self._extract_text(element, tag_name, parser)
        return int(text) if text and text.isdigit() else None

    # Legacy method names for compatibility
    def _extract_minidom_text(self, element, tag_name: str) -> str | None:
        return self._extract_text(element, tag_name, ParserType.MINIDOM)

    def _extract_minidom_int(self, element, tag_name: str) -> int | None:
        return self._extract_int(element, tag_name, ParserType.MINIDOM)

    # -------------------------------------------------------------------------
    # API METHODS
    # -------------------------------------------------------------------------

    @_lastfm_retry_backoff("lastfm_get_track_by_mbid")
    async def get_track_by_mbid(self, mbid: str) -> pylast.Track | None:
        """Get track by MusicBrainz ID."""
        if not self.is_configured or self.client is None:
            return None

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(self.client.get_track_by_mbid, mbid),
                timeout=settings.api.lastfm_request_timeout,
            )
            return result
        except TimeoutError:
            logger.warning(f"Timeout getting track by MBID: {mbid}")
            return None
        except Exception as e:
            logger.error(f"Error getting track by MBID {mbid}: {e}")
            return None

    @_lastfm_retry_backoff("lastfm_get_track")
    async def get_track(self, artist: str, title: str) -> pylast.Track | None:
        """Get track by artist and title."""
        if not self.is_configured or self.client is None:
            return None

        try:

            def get_track_blocking():
                client_args = {
                    "api_key": settings.credentials.lastfm_key,
                    "api_secret": settings.credentials.lastfm_secret.get_secret_value(),
                }
                fresh_client = pylast.LastFMNetwork(**client_args)
                return fresh_client.get_track(artist, title)

            result = await asyncio.wait_for(
                asyncio.to_thread(get_track_blocking),
                timeout=settings.api.lastfm_request_timeout,
            )
            return result
        except TimeoutError:
            logger.warning(f"Timeout getting track: {artist} - {title}")
            return None
        except Exception as e:
            logger.error(f"Error getting track {artist} - {title}: {e}")
            return None

    async def get_track_info_comprehensive(
        self, artist: str, title: str
    ) -> dict[str, Any] | None:
        """Get comprehensive track info in single API call."""
        try:
            return await self._get_track_info_comprehensive_with_retries(artist, title)
        except pylast.WSError:
            return None

    @_lastfm_retry_backoff("lastfm_get_track_info_comprehensive")
    async def _get_track_info_comprehensive_with_retries(
        self, artist: str, title: str
    ) -> dict[str, Any] | None:
        """Get comprehensive track info with retries."""
        if not self.is_configured or self.client is None:
            return None

        try:

            def get_track_info_raw():
                client_args = {
                    "api_key": settings.credentials.lastfm_key,
                    "api_secret": settings.credentials.lastfm_secret.get_secret_value(),
                }
                if self.lastfm_username:
                    client_args["username"] = self.lastfm_username
                    if self.lastfm_password_hash:
                        client_args["password_hash"] = self.lastfm_password_hash

                fresh_client = pylast.LastFMNetwork(**client_args)
                return fresh_client.get_track(artist, title)

            track = await asyncio.wait_for(
                asyncio.to_thread(get_track_info_raw),
                timeout=settings.api.lastfm_request_timeout,
            )

            if not track:
                return None

            return await self._get_comprehensive_track_data(track)

        except TimeoutError:
            logger.warning(f"Timeout getting comprehensive info: {artist} - {title}")
            return None
        except (ValueError, TypeError, AttributeError) as e:
            logger.error(f"Error getting comprehensive info: {e}")
            return None

    @_lastfm_retry_backoff("lastfm_get_track_info_comprehensive_by_mbid")
    async def get_track_info_comprehensive_by_mbid(
        self, mbid: str
    ) -> dict[str, Any] | None:
        """Get comprehensive track info by MBID."""
        if not self.is_configured or self.client is None or not mbid:
            return None

        try:

            def get_track_by_mbid_raw():
                client_args = {
                    "api_key": settings.credentials.lastfm_key,
                    "api_secret": settings.credentials.lastfm_secret.get_secret_value(),
                }
                if self.lastfm_username:
                    client_args["username"] = self.lastfm_username
                    if self.lastfm_password_hash:
                        client_args["password_hash"] = self.lastfm_password_hash

                fresh_client = pylast.LastFMNetwork(**client_args)
                return fresh_client.get_track_by_mbid(mbid)

            track = await asyncio.wait_for(
                asyncio.to_thread(get_track_by_mbid_raw),
                timeout=settings.api.lastfm_request_timeout,
            )

            if not track:
                return None

            return await self._get_comprehensive_track_data(track)

        except TimeoutError:
            logger.warning(f"Timeout getting comprehensive info by MBID: {mbid}")
            return None
        except Exception as e:
            logger.error(f"Error getting comprehensive info by MBID {mbid}: {e}")
            return None

    async def _get_comprehensive_track_data(
        self, track: pylast.Track
    ) -> dict[str, Any] | None:
        """Extract comprehensive data from pylast Track object."""
        if not track:
            return None

        try:

            def get_comprehensive_data():
                raw_data = track._request(track.ws_prefix + ".getInfo", cacheable=True)
                if not raw_data:
                    return None

                track_info = {}

                try:
                    if hasattr(raw_data, "getElementsByTagName"):  # minidom
                        track_elements = raw_data.getElementsByTagName("track")
                        if track_elements:
                            elem = track_elements[0]

                            # Basic fields
                            track_info["lastfm_title"] = self._extract_minidom_text(
                                elem, "name"
                            )
                            track_info["lastfm_mbid"] = self._extract_minidom_text(
                                elem, "mbid"
                            )
                            track_info["lastfm_url"] = self._extract_minidom_text(
                                elem, "url"
                            )
                            track_info["lastfm_duration"] = self._extract_minidom_int(
                                elem, "duration"
                            )
                            track_info["lastfm_global_playcount"] = (
                                self._extract_minidom_int(elem, "playcount")
                            )
                            track_info["lastfm_listeners"] = self._extract_minidom_int(
                                elem, "listeners"
                            )

                            # Artist
                            artist_elems = elem.getElementsByTagName("artist")
                            if artist_elems:
                                artist = artist_elems[0]
                                track_info["lastfm_artist_name"] = (
                                    self._extract_minidom_text(artist, "name")
                                )
                                track_info["lastfm_artist_mbid"] = (
                                    self._extract_minidom_text(artist, "mbid")
                                )
                                track_info["lastfm_artist_url"] = (
                                    self._extract_minidom_text(artist, "url")
                                )

                            # Album
                            album_elems = elem.getElementsByTagName("album")
                            if album_elems:
                                album = album_elems[0]
                                track_info["lastfm_album_name"] = (
                                    self._extract_minidom_text(album, "title")
                                )
                                track_info["lastfm_album_mbid"] = (
                                    self._extract_minidom_text(album, "mbid")
                                )
                                track_info["lastfm_album_url"] = (
                                    self._extract_minidom_text(album, "url")
                                )

                            # User data
                            if self.lastfm_username:
                                track_info["lastfm_user_playcount"] = (
                                    self._extract_minidom_int(elem, "userplaycount")
                                )
                                userloved = self._extract_minidom_text(
                                    elem, "userloved"
                                )
                                track_info["lastfm_user_loved"] = userloved == "1"

                    return track_info

                except Exception as parse_error:
                    logger.error(f"Error parsing track data: {parse_error}")
                    return None

            result = await asyncio.wait_for(
                asyncio.to_thread(get_comprehensive_data),
                timeout=settings.api.lastfm_request_timeout,
            )
            return result

        except TimeoutError:
            logger.warning("Timeout getting comprehensive track data")
            return None
        except pylast.WSError:
            raise
        except Exception as e:
            logger.error(f"Error getting comprehensive track data: {e}")
            return None

    async def love_track(self, artist: str, title: str) -> bool:
        """Love a track for the authenticated user."""
        if not self.is_configured or not self.lastfm_username or self.client is None:
            logger.warning("Cannot love track - no username configured")
            return False

        try:
            return await self._love_track_with_retries(artist, title)
        except pylast.WSError:
            return False

    @_lastfm_retry_backoff("lastfm_love_track")
    async def _love_track_with_retries(self, artist: str, title: str) -> bool:
        """Love track with retry logic."""
        if self.client is None:
            raise RuntimeError("LastFM client not initialized")

        try:
            track = await asyncio.wait_for(
                asyncio.to_thread(self.client.get_track, artist, title),
                timeout=settings.api.lastfm_request_timeout,
            )
            await asyncio.wait_for(
                asyncio.to_thread(track.love),
                timeout=settings.api.lastfm_request_timeout,
            )
            return True
        except TimeoutError:
            logger.warning(f"Timeout loving track: {artist} - {title}")
            return False
        except pylast.WSError:
            raise
        except Exception as e:
            logger.error(f"Error loving track {artist} - {title}: {e}")
            return False

    @_lastfm_retry_backoff("lastfm_get_recent_tracks")
    async def get_recent_tracks(
        self,
        username: str | None = None,
        limit: int = 200,
        from_time: datetime | None = None,
        to_time: datetime | None = None,
    ) -> list[dict]:
        """Get recent tracks from Last.fm user.getRecentTracks API."""
        if not self.is_configured or self.client is None:
            logger.error("Last.fm client not initialized")
            return []

        user = username or self.lastfm_username
        if not user:
            logger.error("No Last.fm username provided")
            return []

        # Validate limit
        limit = min(
            max(settings.api.lastfm_recent_tracks_min_limit, limit),
            settings.api.lastfm_recent_tracks_max_limit,
        )

        try:
            # Build time range params
            params = {"limit": limit}
            if from_time:
                params["time_from"] = int(from_time.timestamp())
            if to_time:
                params["time_to"] = int(to_time.timestamp())

            # Get Last.fm user
            lastfm_user = await asyncio.wait_for(
                asyncio.to_thread(self.client.get_user, user),
                timeout=settings.api.lastfm_request_timeout,
            )
            if not lastfm_user:
                logger.error(f"Could not get Last.fm user: {user}")
                return []

            # Get recent tracks
            recent_tracks = await asyncio.wait_for(
                asyncio.to_thread(
                    lastfm_user.get_recent_tracks,
                    limit=params["limit"],
                    time_from=params.get("time_from"),
                    time_to=params.get("time_to"),
                ),
                timeout=settings.api.lastfm_request_timeout * 2,
            )

            # Convert to track data dicts
            tracks_data = []
            for played_track in recent_tracks:
                track = played_track.track
                timestamp_str = played_track.timestamp

                if not timestamp_str:  # Skip currently playing
                    continue

                track_name = (
                    track.get_title() if hasattr(track, "get_title") else str(track)
                )
                artist_name = (
                    track.get_artist().get_name()
                    if hasattr(track, "get_artist") and track.get_artist()
                    else ""
                )
                album_name = played_track.album or None

                track_url = track.get_url() if hasattr(track, "get_url") else None
                track_mbid = track.get_mbid() if hasattr(track, "get_mbid") else None

                artist_url = (
                    track.get_artist().get_url()
                    if hasattr(track, "get_artist") and track.get_artist()
                    else None
                )
                artist_mbid = (
                    track.get_artist().get_mbid()
                    if hasattr(track, "get_artist") and track.get_artist()
                    else None
                )

                album_url = (
                    track.get_album().get_url()
                    if hasattr(track, "get_album") and track.get_album()
                    else None
                )
                album_mbid = (
                    track.get_album().get_mbid()
                    if hasattr(track, "get_album") and track.get_album()
                    else None
                )

                track_data = {
                    "artist_name": artist_name,
                    "track_name": track_name,
                    "album_name": album_name,
                    "timestamp": timestamp_str,
                    "lastfm_track_url": track_url,
                    "lastfm_artist_url": artist_url,
                    "lastfm_album_url": album_url,
                    "mbid": track_mbid,
                    "artist_mbid": artist_mbid,
                    "album_mbid": album_mbid,
                    "raw_data": {
                        "track_url": track_url,
                        "artist_url": artist_url,
                        "album_url": album_url,
                    },
                }

                tracks_data.append(track_data)

            logger.info(
                f"Retrieved {len(tracks_data)} recent tracks for user {user}",
                limit=limit,
            )
            return tracks_data

        except TimeoutError:
            logger.warning(f"Timeout fetching recent tracks for user {user}")
            return []
        except Exception as e:
            logger.error(f"Error fetching recent tracks: {e}")
            raise

    async def extract_track_metadata(self, track: pylast.Track) -> dict:
        """Extract all metadata from a pylast Track object."""
        if not track:
            return {}

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._extract_metadata_sync, track),
                timeout=settings.api.lastfm_request_timeout,
            )
        except TimeoutError:
            logger.warning("Timeout extracting track metadata")
            return {}
        except Exception as e:
            logger.error(f"Failed to extract track metadata: {e}")
            return {}

    def _extract_metadata_sync(self, track: pylast.Track) -> dict:
        """Synchronously extract metadata fields from pylast track."""
        metadata = {}

        extractors = {
            "title": lambda t: t.get_title(),
            "mbid": lambda t: t.get_mbid(),
            "url": lambda t: t.get_url(),
            "duration": lambda t: t.get_duration(),
            "artist_name": lambda t: t.get_artist() and t.get_artist().get_name(),
            "artist_mbid": lambda t: t.get_artist() and t.get_artist().get_mbid(),
            "artist_url": lambda t: t.get_artist() and t.get_artist().get_url(),
            "album_name": lambda t: t.get_album() and t.get_album().get_name(),
            "album_mbid": lambda t: t.get_album() and t.get_album().get_mbid(),
            "album_url": lambda t: t.get_album() and t.get_album().get_url(),
            "user_playcount": lambda t: int(t.get_userplaycount() or 0)
            if t.username
            else None,
            "user_loved": lambda t: bool(t.get_userloved()) if t.username else False,
            "global_playcount": lambda t: int(t.get_playcount() or 0),
            "listeners": lambda t: int(t.get_listener_count() or 0),
        }

        for field_name, extractor in extractors.items():
            try:
                value = extractor(track)
                if value is not None:
                    metadata[f"lastfm_{field_name}"] = value
            except (AttributeError, TypeError, ValueError):
                # Skip fields that can't be extracted
                continue

        return metadata
