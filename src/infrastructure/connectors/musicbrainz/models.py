"""Pydantic models for MusicBrainz JSON API response shapes.

These models validate raw JSON from the MusicBrainz Web Services API and exist
ONLY in the infrastructure layer — domain models remain attrs.

Key design decisions:
- extra='ignore': forward-compatible when MusicBrainz adds new fields
- populate_by_name=True: MusicBrainz uses hyphenated keys (artist-credit)
  that need aliases, but we also want access via Pythonic names
- Required fields (id on recording/artist) have no default — ValidationError if absent
- Optional/nullable fields default sensibly (empty string, empty list, None)

Endpoint coverage:
- GET /recording?query=...  → MusicBrainzRecording (via recordings[] array)
- GET /isrc/{isrc}          → MusicBrainzRecording (via recordings[] array)
"""

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field


class MusicBrainzBaseModel(BaseModel):
    """Base model for all MusicBrainz API response shapes."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="ignore", populate_by_name=True
    )


class MusicBrainzArtist(MusicBrainzBaseModel):
    """Artist object nested inside artist-credit entries."""

    id: str
    name: str = Field(default="")


class MusicBrainzArtistCredit(MusicBrainzBaseModel):
    """Single entry in the artist-credit array."""

    name: str = Field(default="")
    artist: MusicBrainzArtist | None = Field(default=None)


class MusicBrainzRelease(MusicBrainzBaseModel):
    """Release (album) object nested inside recording responses."""

    id: str = Field(default="")
    title: str = Field(default="")


class MusicBrainzRecording(MusicBrainzBaseModel):
    """Recording object from search and ISRC lookup endpoints."""

    id: str
    title: str = Field(default="")
    length: int | None = Field(default=None)
    artist_credit: list[MusicBrainzArtistCredit] = Field(
        default_factory=list, alias="artist-credit"
    )
    releases: list[MusicBrainzRelease] = Field(default_factory=list)
    isrcs: list[str] = Field(default_factory=list)
