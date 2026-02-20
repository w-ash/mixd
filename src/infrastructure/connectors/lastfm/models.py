"""Pydantic models and exception types for Last.fm API.

These models validate raw JSON from Last.fm's Web Services API and exist ONLY
in the infrastructure layer — domain models remain attrs.

Key design decisions:
- extra='ignore': forward-compatible when Last.fm adds new fields
- AliasChoices on artist.name: handles extended=1 schema ("name") vs non-extended ("#text")
- field_validator on int fields: Last.fm returns pagination integers as JSON strings ("3" not 3)
- field_validator on track list: Last.fm returns a single dict instead of a list for exactly
  1 track — normalised here before callers see it
- is_now_playing: dual check — @attr.nowplaying flag OR absent date field
"""

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


class LastFMAPIError(Exception):
    """Last.fm API-level error returned as HTTP 200 with an error code.

    Last.fm signals errors in the response body as {"error": N, "message": "..."}
    rather than via HTTP status codes. This exception wraps those.

    The .status attribute mirrors pylast.WSError.status so LastFMErrorClassifier
    requires no changes to its PERMANENT_ERROR_CODES / TEMPORARY_ERROR_CODES lookups.

    Args:
        code: Last.fm service error code (integer 1-29)
        message: Human-readable error description
    """

    def __init__(self, code: int | str, message: str) -> None:
        self.status = str(code)  # Match pylast.WSError.status interface
        self.details = message
        super().__init__(f"Last.fm error {code}: {message}")


class LastFMArtist(BaseModel):
    """Artist object as returned by user.getRecentTracks.

    Schema differs between extended=1 and non-extended requests:
    - extended=1:    {"name": "...", "url": "...", "image": [...]}
    - non-extended:  {"#text": "...", "mbid": "..."}

    AliasChoices tries "name" first, then "#text", so both schemas populate .name.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: str = Field(default="", validation_alias=AliasChoices("name", "#text"))
    url: str | None = Field(default=None)
    mbid: str | None = Field(default=None)  # absent with extended=1


class LastFMAlbum(BaseModel):
    """Album object. getRecentTracks uses '#text'; track.getInfo uses 'title'."""

    model_config = ConfigDict(extra="ignore")

    name: str | None = Field(
        default=None, validation_alias=AliasChoices("title", "#text")
    )
    mbid: str | None = Field(default=None)
    url: str | None = Field(default=None)


class LastFMDate(BaseModel):
    """Timestamp present on completed scrobbles; absent on now-playing entries."""

    model_config = ConfigDict(extra="ignore")

    uts: str  # UNIX timestamp as string — connector converts to datetime


class LastFMNowPlayingAttr(BaseModel):
    """@attr object on individual track items (signals now-playing status)."""

    model_config = ConfigDict(extra="ignore")

    nowplaying: str = Field(default="false")


class LastFMAttr(BaseModel):
    """Pagination metadata from the @attr node on user.getRecentTracks responses."""

    model_config = ConfigDict(extra="ignore")

    total_pages: int = Field(default=1, validation_alias="totalPages")
    page: int = Field(default=1)
    total: int = Field(default=0)

    @field_validator("total_pages", "page", "total", mode="before")
    @classmethod
    def coerce_str_to_int(cls, v: str | int) -> int:
        """Last.fm returns all pagination integers as JSON strings."""
        return max(0, int(v))


class LastFMTrackEntry(BaseModel):
    """Single scrobble entry from user.getRecentTracks."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: str = Field(default="")
    mbid: str | None = Field(default=None)
    url: str | None = Field(default=None)
    artist: LastFMArtist = Field(default_factory=LastFMArtist)
    album: LastFMAlbum | None = Field(default=None)
    date: LastFMDate | None = Field(default=None)
    # @attr.nowplaying = "true" marks the currently-playing track (no scrobble date)
    attr: LastFMNowPlayingAttr | None = Field(default=None, validation_alias="@attr")
    # "userloved" is "1"/"0" string; only present with extended=1
    userloved: str = Field(default="0")

    @property
    def loved(self) -> bool:
        return self.userloved == "1"

    @property
    def timestamp_uts(self) -> str | None:
        return self.date.uts if self.date else None

    @property
    def is_now_playing(self) -> bool:
        """True for the currently-playing track — has no timestamp, must be skipped."""
        return (
            self.attr is not None and self.attr.nowplaying == "true"
        ) or self.date is None


class LastFMRecentTracksPage(BaseModel):
    """Validated shape of the recenttracks node from user.getRecentTracks."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    tracks: list[LastFMTrackEntry] = Field(default=[], alias="track")
    attr: LastFMAttr = Field(default_factory=LastFMAttr, validation_alias="@attr")

    @field_validator("tracks", mode="before")
    @classmethod
    def coerce_single_to_list(cls, v: list | dict | None) -> list:
        """Last.fm returns a bare dict (not a list) when there is exactly 1 track."""
        if isinstance(v, dict):
            return [v]
        return v or []

    @property
    def playable_tracks(self) -> list[LastFMTrackEntry]:
        """Completed scrobbles only — excludes the now-playing entry if present."""
        return [t for t in self.tracks if not t.is_now_playing]

    @property
    def total_pages(self) -> int:
        return max(1, self.attr.total_pages)
