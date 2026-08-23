"""Pydantic models for Discogs API response shapes.

These models validate raw JSON from the Discogs API and exist ONLY in the
infrastructure layer — domain models remain attrs. VERIFIED 2026-08-22
against a live, token-authenticated probe (identity, an empty collection
page, release 249504, master 96559): every field below parsed clean. Only
fields the client (or its own tests) reads are declared; ``extra='ignore'``
drops the rest. Discogs JSON is already snake_case.

field                        type      presence         meaning
Identity.id/.username        int/str   always           user id, login name
Pagination.page/pages/per_page/items    int  always     page counters
  .urls.next                 str|None  next-page only   absent, last/empty page
Artist.name                  str       always
  .anv                       str       "" if none       credited name variation
  .join                      str       "" if last credit  phrase to next artist
Label.name/.catno            str       catno "" if none
Format.name                  str       always           e.g. "Vinyl"
  .qty                       str       always           STRING, confirmed not int
  .descriptions               list[str] always
Track.position/.title        str       always
  .duration                  str       "" if unknown    e.g. "3:32"
Release/.Master id/title/year/artists/labels/formats/genres/styles/
  tracklist — as above; year is int|None; Master adds .main_release:
  int, always present.

Observed, NOT declared (extra='ignore' drops, nothing reads): identity
.resource_url/.consumer_name; a tracklist entry's literal "type_" key
(real wire key, always "track" here); ~30 more release/master fields
(country, notes, community, videos, images, most_recent_release...).

Empty collection (real capture): pagination={page:1,pages:1,per_page:3,
items:0,urls:{}}, releases:[] — parses clean, urls.next is None.

Rate headers (live): x-discogs-ratelimit(-remaining/-used) arrive
lowercase; first call observed 60/60/0. httpx2.Headers is case-insensitive,
so pacer.py's "X-Discogs-Ratelimit-Remaining" lookup matches.

Endpoints: /oauth/identity, /users/{u}/collection/folders/0/releases,
/releases/{id}, /masters/{id}.
"""

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field


class DiscogsBaseModel(BaseModel):
    """Base model for all Discogs API response shapes.

    Declares the shared model_config once — all subclasses inherit it.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")


class DiscogsIdentity(DiscogsBaseModel):
    """GET /oauth/identity — the authenticated user."""

    id: int
    username: str


class DiscogsPaginationUrls(DiscogsBaseModel):
    """Pagination navigation block — ``next`` is absent on the last page."""

    next: str | None = Field(default=None)


class DiscogsPagination(DiscogsBaseModel):
    """Pagination envelope on every paginated Discogs endpoint."""

    page: int
    pages: int
    per_page: int
    items: int
    urls: DiscogsPaginationUrls = Field(default_factory=DiscogsPaginationUrls)


class DiscogsArtist(DiscogsBaseModel):
    """One artist credit — name plus the Discogs-distinctive ANV/join model."""

    name: str
    # Artist name variation: the exact credited spelling when it differs
    # from the canonical artist name. Empty string when not applicable.
    anv: str = Field(default="")
    # Phrase joining this credit to the next ("&", "feat.", ...). Confirmed
    # to parse as "" on a real single-artist release; non-empty content
    # unverified — the probe's captures carry no multi-artist credit.
    join: str = Field(default="")


class DiscogsLabel(DiscogsBaseModel):
    """One label credit with its catalog number."""

    name: str
    catno: str = Field(default="")


class DiscogsFormat(DiscogsBaseModel):
    """One physical format entry (Vinyl, CD, ...)."""

    name: str
    # Discogs serializes quantity as a string ("1", "2") — confirmed on the
    # release probe (formats: [{"qty": "1", ...}]).
    qty: str = Field(default="")
    descriptions: list[str] = Field(default_factory=list)


class DiscogsBasicInformation(DiscogsBaseModel):
    """The ``basic_information`` block on a collection release item."""

    id: int
    title: str
    # Discogs uses 0 for "year unknown" (public API docs) — the live probe's
    # collection was empty, so no basic_information item was captured to
    # confirm this; still provisional.
    year: int = Field(default=0)
    artists: list[DiscogsArtist] = Field(default_factory=list)
    labels: list[DiscogsLabel] = Field(default_factory=list)
    formats: list[DiscogsFormat] = Field(default_factory=list)


class DiscogsCollectionRelease(DiscogsBaseModel):
    """One item of a user's collection folder.

    ``id`` is the release; ``instance_id`` identifies THIS copy in the
    collection (the same release can be collected twice).
    """

    id: int
    instance_id: int
    date_added: str = Field(default="")
    # Observed present on every real capture, but not owed to us: display
    # consumers must tolerate an item without it (skip, never fail the page).
    basic_information: DiscogsBasicInformation | None = None


class DiscogsCollectionPage(DiscogsBaseModel):
    """GET /users/{username}/collection/folders/0/releases — one page."""

    pagination: DiscogsPagination
    releases: list[DiscogsCollectionRelease] = Field(default_factory=list)


class DiscogsTrack(DiscogsBaseModel):
    """One tracklist entry — durations are strings and often blank.

    Confirmed on a live release: ``position`` is not always numbered
    ("A"/"B" on a 7" single); ``duration`` is a populated "M:SS" string
    when Discogs has one on file. The wire entry also carries a ``type_``
    key (literal trailing underscore, not a client artifact) — unread,
    so not declared here.
    """

    position: str = Field(default="")
    title: str = Field(default="")
    duration: str = Field(default="")


class DiscogsRelease(DiscogsBaseModel):
    """GET /releases/{id} — full release detail."""

    id: int
    title: str
    year: int | None = Field(default=None)
    artists: list[DiscogsArtist] = Field(default_factory=list)
    labels: list[DiscogsLabel] = Field(default_factory=list)
    formats: list[DiscogsFormat] = Field(default_factory=list)
    genres: list[str] = Field(default_factory=list)
    styles: list[str] = Field(default_factory=list)
    tracklist: list[DiscogsTrack] = Field(default_factory=list)


class DiscogsMaster(DiscogsBaseModel):
    """GET /masters/{id} — master release (the grouping of versions)."""

    id: int
    title: str
    year: int | None = Field(default=None)
    main_release: int
