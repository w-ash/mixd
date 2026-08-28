"""Pydantic models for Apple Music API (JSON:API) response shapes.

These models validate raw JSON from the Apple Music API and exist ONLY in the
infrastructure layer — domain models remain attrs.

Key design decisions:
- extra='ignore': forward-compatible when Apple adds fields; only consumed
  fields are declared.
- Apple's attribute names are camelCase; fields are snake_case with a
  to_camel alias generator (populate_by_name keeps test construction easy).
- Catalog and recent-played songs reliably carry isrc + durationInMillis,
  and their playParams are ``{id, kind}`` only — ``catalogId`` appears on
  library resources (v0.13), so that field stays declared but dormant.

Endpoint coverage:
- GET /v1/me/storefront                     → AppleMusicStorefrontResponse
- GET /v1/catalog/{storefront}/songs        → AppleMusicSongsResponse
  (ids=, filter[isrc]=, filter[equivalents]= all share the envelope)
- GET /v1/me/recent/played/tracks           → AppleMusicRecentlyPlayedResponse

JSON:API error bodies parse via the shared ``_shared.json_api`` models.
"""

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class AppleMusicBaseModel(BaseModel):
    """Base model for all Apple Music API response shapes.

    Declares the shared model_config once — all subclasses inherit it.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="ignore", alias_generator=to_camel, populate_by_name=True
    )


class AppleMusicPlayParams(AppleMusicBaseModel):
    """Playback parameters block on a song resource.

    ``catalog_id`` matters on library resources: the resource ``id`` is the
    library identifier while ``playParams.catalogId`` carries the catalog
    identity the rest of the system matches on. Catalog and recent-played
    playParams are ``{id, kind}`` with no ``catalogId`` — the field is
    dormant until v0.13 library objects arrive.
    """

    id: str | None = Field(default=None)
    catalog_id: str | None = Field(default=None)


class AppleMusicSongAttributes(AppleMusicBaseModel):
    """Consumed attributes of a song resource."""

    name: str
    artist_name: str = Field(default="")
    album_name: str = Field(default="")
    duration_in_millis: int = Field(default=0)
    isrc: str | None = Field(default=None)
    release_date: str | None = Field(default=None)
    play_params: AppleMusicPlayParams | None = Field(default=None)


class AppleMusicSong(AppleMusicBaseModel):
    """Song resource from catalog and recently-played endpoints."""

    id: str
    type: str = Field(default="songs")
    attributes: AppleMusicSongAttributes


class AppleMusicStorefront(AppleMusicBaseModel):
    """Storefront resource — only the id (e.g. ``"us"``) is consumed."""

    id: str


class AppleMusicStorefrontResponse(AppleMusicBaseModel):
    """Envelope for GET /v1/me/storefront."""

    data: list[AppleMusicStorefront] = Field(default_factory=list)


class AppleMusicSongsResponse(AppleMusicBaseModel):
    """Paged envelope of song resources (catalog lookups)."""

    data: list[AppleMusicSong] = Field(default_factory=list)
    next: str | None = Field(default=None)


class AppleMusicRecentlyPlayedResponse(AppleMusicBaseModel):
    """Paged envelope for GET /v1/me/recent/played/tracks."""

    data: list[AppleMusicSong] = Field(default_factory=list)
    next: str | None = Field(default=None)
