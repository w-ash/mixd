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

from typing import ClassVar

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from src.domain.entities.shared import JsonValue


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

    status: str
    details: str

    def __init__(self, code: int | str, message: str) -> None:
        self.status = str(code)  # Match pylast.WSError.status interface
        self.details = message
        super().__init__(f"Last.fm error {code}: {message}")


class LastFMBaseModel(BaseModel):
    """Base model for Last.fm API responses using extra='ignore' only."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    @field_validator("mbid", mode="before", check_fields=False)
    @classmethod
    def empty_mbid_to_none(cls, v: str | None) -> str | None:
        """Last.fm sends "" for missing MBIDs — coerce to None at the boundary."""
        return v or None


class LastFMNamedModel(BaseModel):
    """Base model for Last.fm models that use field aliases (populate_by_name=True)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="ignore", populate_by_name=True
    )

    @field_validator("mbid", mode="before", check_fields=False)
    @classmethod
    def empty_mbid_to_none(cls, v: str | None) -> str | None:
        """Last.fm sends "" for missing MBIDs — coerce to None at the boundary."""
        return v or None


class LastFMArtist(LastFMNamedModel):
    """Artist object as returned by user.getRecentTracks.

    Schema differs between extended=1 and non-extended requests:
    - extended=1:    {"name": "...", "url": "...", "image": [...]}
    - non-extended:  {"#text": "...", "mbid": "..."}

    AliasChoices tries "name" first, then "#text", so both schemas populate .name.
    """

    name: str = Field(default="", validation_alias=AliasChoices("name", "#text"))
    url: str | None = Field(default=None)
    mbid: str | None = Field(default=None)  # absent with extended=1


class LastFMAlbum(LastFMBaseModel):
    """Album object. getRecentTracks uses '#text'; track.getInfo uses 'title'."""

    name: str | None = Field(
        default=None, validation_alias=AliasChoices("title", "#text")
    )
    mbid: str | None = Field(default=None)
    url: str | None = Field(default=None)


class LastFMDate(LastFMBaseModel):
    """Timestamp present on completed scrobbles; absent on now-playing entries."""

    uts: str  # UNIX timestamp as string — connector converts to datetime


class LastFMNowPlayingAttr(LastFMBaseModel):
    """@attr object on individual track items (signals now-playing status)."""

    nowplaying: str = Field(default="false")


class LastFMAttr(LastFMBaseModel):
    """Pagination metadata from the @attr node on user.getRecentTracks responses."""

    total_pages: int = Field(default=1, validation_alias="totalPages")
    page: int = Field(default=1)
    total: int = Field(default=0)

    @field_validator("total_pages", "page", "total", mode="before")
    @classmethod
    def coerce_str_to_int(cls, v: str | int) -> int:
        """Last.fm returns all pagination integers as JSON strings."""
        return max(0, int(v))


class LastFMTrackEntry(LastFMNamedModel):
    """Single scrobble entry from user.getRecentTracks."""

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


class LastFMRecentTracksPage(LastFMNamedModel):
    """Validated shape of the recenttracks node from user.getRecentTracks."""

    tracks: list[LastFMTrackEntry] = Field(default=[], alias="track")
    attr: LastFMAttr = Field(default_factory=LastFMAttr, validation_alias="@attr")

    @field_validator("tracks", mode="before")
    @classmethod
    def coerce_single_to_list(
        cls, v: list[object] | dict[str, object] | None
    ) -> list[object]:
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


class LastFMTrackInfoData(LastFMBaseModel):
    """Track node from a track.getInfo JSON response.

    Last.fm returns ints as strings and empty MBIDs as "" — validators coerce
    these at the boundary so downstream code sees clean Python types.
    """

    name: str = Field(default="")
    mbid: str | None = Field(default=None)
    url: str | None = Field(default=None)
    duration: int | None = Field(default=None)
    playcount: int | None = Field(default=None)
    listeners: int | None = Field(default=None)
    artist: LastFMArtist = Field(default_factory=LastFMArtist)
    album: LastFMAlbum | None = Field(default=None)
    # User-specific fields — only present when username param is supplied
    userplaycount: int | None = Field(default=None)
    userloved: str = Field(default="0")

    @field_validator(
        "duration", "playcount", "listeners", "userplaycount", mode="before"
    )
    @classmethod
    def coerce_str_to_int(cls, v: str | int | None) -> int | None:
        """Last.fm returns numeric fields as JSON strings."""
        if v is None:
            return None
        try:
            return int(v)
        except ValueError, TypeError:
            return None

    # mbid coercion inherited from LastFMBaseModel


class LastFMTrackData(LastFMBaseModel):
    """Loosely-structured track shape accepted by the playlist-item conversion seam.

    Distinct from ``LastFMTrackInfoData`` (the strict ``track.getInfo`` response):
    this is the ad-hoc track dict that reaches
    ``LastFMConnector.convert_track_to_connector``, where ``artist`` and ``album``
    may each arrive as a nested object OR a bare string. The before-validators
    reproduce the historical hand-walk exactly:

    - ``artist``/``album`` read only their literal ``"name"`` key when given a
      dict (no ``"#text"``/``AliasChoices`` coercion), and preserve a bare string
      verbatim (``album`` keeps ``""`` as ``""``).
    - ``duration`` is Last.fm seconds; converted to milliseconds only when it is
      an all-digit value, otherwise ``None``.
    - ``playcount``/``listeners``/``userplaycount`` coerce via ``int(str(v or 0))``;
      presence is tracked through ``model_fields_set`` so a metric key is emitted
      downstream only when the source actually provided it (a provided zero is
      kept; an absent field is not).
    """

    name: str = Field(default="")
    mbid: str | None = Field(default=None)
    url: str | None = Field(default=None)
    artist_name: str = Field(default="", validation_alias="artist")
    album: str | None = Field(default=None)
    duration_ms: int | None = Field(default=None, validation_alias="duration")
    playcount: int = Field(default=0)
    listeners: int = Field(default=0)
    userplaycount: int = Field(default=0)

    @field_validator("name", mode="before")
    @classmethod
    def coerce_name(cls, v: JsonValue) -> str:
        """Reproduce ``str(name or "")`` — None/empty/absent all become ""."""
        return str(v or "")

    @field_validator("artist_name", mode="before")
    @classmethod
    def extract_artist_name(cls, v: JsonValue) -> str:
        """Read the literal ``name`` key from a dict, or keep a bare string."""
        if isinstance(v, dict):
            name = v.get("name", "")
            return str(name) if name else ""
        if isinstance(v, str):
            return v
        return ""

    @field_validator("album", mode="before")
    @classmethod
    def extract_album_name(cls, v: JsonValue) -> str | None:
        """Read the literal ``name`` key from a dict, or keep a bare string."""
        if isinstance(v, dict):
            name = v.get("name")
            return str(name) if name else None
        if isinstance(v, str):
            return v
        return None

    @field_validator("duration_ms", mode="before")
    @classmethod
    def seconds_to_ms(cls, v: JsonValue) -> int | None:
        """Convert all-digit Last.fm seconds to milliseconds, else None."""
        if v and str(v).isdigit():
            return int(str(v)) * 1000
        return None

    @field_validator("playcount", "listeners", "userplaycount", mode="before")
    @classmethod
    def coerce_metric(cls, v: JsonValue) -> int:
        """Reproduce ``int(str(v or 0))`` for the metric fields."""
        return int(str(v or 0))
