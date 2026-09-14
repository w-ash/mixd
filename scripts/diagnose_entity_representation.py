#!/usr/bin/env python3
"""Entity representation census — v0.12.0 spike query pack.

Measures how six services (Spotify, Last.fm, Apple Music, Discogs, Tidal,
MusicBrainz) represent artists and albums, against a fixed corpus drawn
from the user's own library. Every request is a GET; the database is only
read. Raw responses are cached under ``--cache-dir`` so ``report`` and any
re-run never re-spend API budget.

Usage:
    MIXD_USER_ID=<id> uv run python scripts/diagnose_entity_representation.py sample
    MIXD_USER_ID=<id> uv run python scripts/diagnose_entity_representation.py probe [--service X]
    MIXD_USER_ID=<id> uv run python scripts/diagnose_entity_representation.py report

``sample`` resolves the corpus against the library (play counts, Spotify
artist/album ids from ``connector_tracks.raw_metadata``) and prints the
top-N candidate tables the corpus was chosen from. ``probe`` runs every
service arm concurrently (each has its own pacer). ``report`` prints the
per-dimension GFM tables that ``docs/backlog/entity-representation-findings.md``
transcribes.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import quote, unquote

from attrs import define, field
import httpx2
from sqlalchemy import text

from src.config import settings, setup_script_logger
from src.config.constants import BusinessLimits
from src.domain.exceptions import (
    AppleMusicAuthRequiredError,
    DiscogsAuthRequiredError,
    TidalAuthRequiredError,
)
from src.domain.matching.text_normalization import (
    normalize_for_comparison,
    strip_parentheticals,
)
from src.infrastructure.connectors._shared.http_client import parse_json_response
from src.infrastructure.connectors._shared.rate_limiting import (
    apply_rate_headers,
    connector_call_slot,
)
from src.infrastructure.connectors.apple_music.client import AppleMusicAPIClient
from src.infrastructure.connectors.discogs.client import DiscogsAPIClient
from src.infrastructure.connectors.lastfm.client import LastFMAPIClient
from src.infrastructure.connectors.musicbrainz.client import MusicBrainzAPIClient
from src.infrastructure.connectors.spotify.client import SpotifyAPIClient
from src.infrastructure.connectors.tidal.client import TidalAPIClient
from src.infrastructure.persistence.database.db_connection import get_session
from src.infrastructure.persistence.database.user_context import user_context

Json = dict[str, Any]

SERVICES = ("spotify", "lastfm", "apple", "discogs", "tidal", "musicbrainz")
DEFAULT_CACHE = Path("data/census")
COUNTRY = "US"

# ---------------------------------------------------------------------------
# Corpus — fixed so the findings doc can name it and the run is reproducible.
# Buckets: solo, band, duo, rename, alias, diacritic, stylized, collision,
# classical, jazz, sentinel.
# ---------------------------------------------------------------------------

CENSUS_ARTISTS: tuple[tuple[str, str], ...] = (
    # forced inclusions
    ("Radiohead", "band"),
    ("Tame Impala", "solo-as-band"),
    ("Four Tet", "alias"),  # KH, ⣎⡇ꉺლ༽இ•̛)ྀ◞ ༎ຶ ༽ৣৢ؞ৢ؞ؖ ꉺლ
    ("Caribou", "alias"),  # Daphni, Manitoba
    ("Daphni", "alias"),  # alias-of-Caribou, in library on its own
    # solo
    ("Jon Hopkins", "solo"),
    ("Bonobo", "solo"),
    ("Tycho", "solo"),
    ("James Blake", "solo"),
    ("Jamie xx", "solo"),
    ("Max Cooper", "solo"),
    ("Grimes", "solo"),
    ("Sufjan Stevens", "solo"),
    ("Beck", "solo"),
    ("Floating Points", "solo"),
    ("Toro y Moi", "solo"),
    ("Washed Out", "solo"),
    ("Rival Consoles", "solo"),
    # renames and name variation
    ("Kanye West", "rename"),  # Ye
    ("TEED", "rename"),  # Totally Enormous Extinct Dinosaurs (FM3c probe)
    ("STRFKR", "rename"),  # Starfucker
    ("RÜFÜS DU SOL", "rename"),  # RÜFÜS
    ("JAŸ-Z", "diacritic"),  # JAY-Z
    ("Fred again..", "stylized"),
    ("CHVRCHES", "stylized"),
    ("Christian Löffler", "diacritic"),
    ("Ben Böhmer", "diacritic"),
    ("Röyksopp", "diacritic"),
    ("The Chemical Brothers", "the-prefix"),
    ("The Avalanches", "the-prefix"),
    # bands and duos
    ("Hot Chip", "band"),
    ("Khruangbin", "band"),
    ("Boards of Canada", "duo"),
    ("Maribou State", "duo"),
    ("Moderat", "supergroup"),  # Modeselektor + Apparat
    ("Kiasmos", "duo"),  # Ólafur Arnalds + Janus Rasmussen
    ("Beach House", "duo"),
    ("Disclosure", "duo"),
    ("M83", "band"),
    ("Mount Kimbie", "band"),
    ("Purity Ring", "duo"),
    ("Jungle", "collision"),
    ("Justice", "collision"),
    ("Bob Moses", "collision"),
    ("Tourist", "collision"),
    ("Booka Shade", "duo"),
    ("Weval", "duo"),
    # classical, jazz
    ("Max Richter", "classical"),
    ("Ólafur Arnalds", "classical"),  # member-of Kiasmos
    ("Vince Guaraldi Trio", "jazz"),
    # sentinel
    ("Various Artists", "sentinel"),
)

CENSUS_ALBUMS: tuple[tuple[str, str, str], ...] = (
    ("Radiohead", "OK Computer", "canonical"),
    ("Radiohead", "OK Computer OKNOTOK 1997 2017", "reissue"),
    ("Radiohead", "In Rainbows", "multi-disc"),
    ("Radiohead", "Kid A", "canonical"),
    ("Radiohead", "KID A MNESIA", "reissue"),
    ("Tame Impala", "Currents", "canonical"),
    ("Tame Impala", "The Slow Rush", "deluxe-family"),
    ("Four Tet", "There Is Love in You (Expanded Edition)", "expanded"),
    ("Four Tet", "New Energy", "canonical"),
    ("Caribou", "Swim", "canonical"),
    ("Caribou", "Our Love (Expanded Edition)", "expanded"),
    ("Caribou", "Suddenly", "canonical"),
    ("Daphni", "Jiaolong", "alias-album"),
    ("Jon Hopkins", "Music For Psychedelic Therapy", "canonical"),
    ("Jamie xx", "In Colour", "canonical"),
    ("Boards of Canada", "The Campfire Headphase", "canonical"),
    ("Hot Chip", "Why Make Sense? (Definitive Version)", "expanded"),
    ("Tycho", "Awake (Deluxe Version)", "deluxe"),
    ("Nirvana", "Nevermind (Remastered)", "remaster"),
    ("Led Zeppelin", "Physical Graffiti (Deluxe Edition)", "multi-disc"),
    ("Moderat", "Moderat (Deluxe Version)", "deluxe"),
    ("Kiasmos", "Kiasmos", "duo-credit"),
    ("Khruangbin", "Texas Sun", "joint-credit"),  # with Leon Bridges
    ("Karen O", "Lux Prima", "joint-credit"),  # with Danger Mouse
    ("Maribou State", "Kingdoms In Colour", "feat-tracks"),
    ("The Avalanches", "We Will Always Love You", "feat-tracks"),
    ("Various Artists", "fabric presents Maribou State", "dj-mix"),
    ("Various Artists", "For the Birds: The Birdsong Project, Vol. I", "compilation"),
    (
        "Vince Guaraldi Trio",
        "A Charlie Brown Christmas (2012 Remastered & Expanded Edition)",
        "remaster",
    ),
    ("Max Richter", "Voices 2", "classical"),
    (
        "Max Richter",
        "Recomposed By Max Richter: Vivaldi, The Four Seasons",
        "classical",
    ),
    ("Justice", "Woman Worldwide", "live"),
    ("Sufjan Stevens", "Illinois", "title-variant"),  # Illinoise
)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def _safe(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", key)[:120]


@define
class Cache:
    root: Path

    def path(self, service: str, kind: str, key: str) -> Path:
        return self.root / service / f"{kind}__{_safe(key)}.json"

    def get(self, service: str, kind: str, key: str) -> Json | None:
        p = self.path(service, kind, key)
        if not p.exists():
            return None
        return json.loads(p.read_text())

    def put(self, service: str, kind: str, key: str, value: Json) -> None:
        p = self.path(service, kind, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(value, ensure_ascii=False, indent=1))

    async def fetch(
        self,
        service: str,
        kind: str,
        key: str,
        loader: Callable[..., Awaitable[Json | None]],
        *args: Any,
    ) -> Json | None:
        """Return the cached body, or load, cache and return it."""
        hit = self.get(service, kind, key)
        if hit is not None:
            return hit.get("body")
        body = await loader(*args)
        if body is None:  # suppressed failure — leave uncached so a re-run retries
            return None
        self.put(
            service,
            kind,
            key,
            {"fetched_at": datetime.now(UTC).isoformat(), "body": body},
        )
        return body


# ---------------------------------------------------------------------------
# Raw GET through each client's pacing + retry + auth
# ---------------------------------------------------------------------------

SOFT_STATUSES = frozenset({400, 404, 422})


def _soft_error(response: httpx2.Response) -> Json | None:
    """A 4xx that is data (nothing there), not a transport failure."""
    if response.status_code in SOFT_STATUSES:
        return {"_status": response.status_code, "_body": response.text[:400]}
    return None


async def _get(
    client: Any,
    op: str,
    path: str,
    params: Mapping[str, str] | None = None,
    *,
    auth: httpx2.Auth | None = None,
) -> Json | None:
    async def impl() -> Json:
        kwargs: dict[str, Any] = {"params": params}
        if auth is not None:
            kwargs["auth"] = auth
        response = await client._client.get(path, **kwargs)
        soft = _soft_error(response)
        if soft is not None:
            return soft
        _ = response.raise_for_status()
        return parse_json_response(response)

    return await client._api_call(op, impl)


# ---------------------------------------------------------------------------
# Name matching for search results
# ---------------------------------------------------------------------------


def _norm(s: str) -> str:
    return normalize_for_comparison(s)


_DISCOGS_SUFFIX = re.compile(r"\s\(\d+\)$")


def _discogs_name(title: str) -> str:
    """Discogs disambiguates same-name artists with a numeric suffix: "Jungle (12)"."""
    return _DISCOGS_SUFFIX.sub("", title)


def _pick(candidates: list[tuple[str, Any]], wanted: str) -> Any | None:
    """First candidate whose normalized name equals the target, else None."""
    target = _norm(wanted)
    for name, ident in candidates:
        if _norm(name) == target:
            return ident
    return None


LOOSE_BUCKETS = frozenset({"rename", "diacritic", "stylized"})


def _pick_artist(candidates: list[tuple[str, Any]], artist: Json) -> Any | None:
    """Exact-name pick; for renamed/stylized artists accept the top hit.

    A rename bucket exists precisely because the library's name is no longer
    the service's primary name — the top search hit is the measurement.
    """
    hit = _pick(candidates, artist["name"])
    if hit is None and artist["bucket"] in LOOSE_BUCKETS and candidates:
        return candidates[0][1]
    return hit


def _pick_mb_artist(items: list[Json], artist: Json, *, loose: bool) -> str | None:
    """MusicBrainz pick — the wanted name may be an alias, not the primary."""
    target = _norm(artist["name"])
    for i in items:
        names = [
            i.get("name", ""),
            *[a.get("name", "") for a in i.get("aliases") or []],
        ]
        if any(_norm(n) == target for n in names):
            return i["id"]
    if loose and artist["bucket"] in LOOSE_BUCKETS and items:
        return items[0]["id"]
    return None


AlbumCandidate = tuple[str, Any, str]  # title, id, credited artist ("" = unknown)
AlbumSearch = Callable[[Json, str], Awaitable[list[AlbumCandidate]]]
_ITUNES_SUFFIX = re.compile(r" - (EP|Single)$")


def _title_variants(title: str) -> list[str]:
    """The title as the library holds it, then with edition qualifiers stripped."""
    stripped = strip_parentheticals(title).strip()
    if not stripped or _norm(stripped) == _norm(title):
        return [title]
    return [title, stripped]


async def _resolve_album(al: Json, svc: str, search: AlbumSearch) -> Any | None:
    """Pick an album id: exact title, then stripped title, then the top hit.

    A candidate credited to a different artist never wins on title alone,
    and the top-hit fallback only considers same-artist candidates. How the
    match was made lands in ``al[f"{svc}_album_match"]`` — the edition-string
    mismatch between the library and a service is itself a measurement.
    """
    want = _norm(al["artist"])

    def same_artist(cands: list[AlbumCandidate]) -> list[AlbumCandidate]:
        return [c for c in cands if not c[2] or want in _norm(c[2])]

    first: list[AlbumCandidate] = []
    for variant in _title_variants(al["title"]):
        cands = await search(al, variant)
        same = same_artist(cands)
        first = first or same
        hit = _pick([(t, i) for t, i, _ in same], variant) or _pick(
            [(t, i) for t, i, _ in cands], variant
        )
        if hit is not None:
            al[f"{svc}_album_match"] = "exact" if variant == al["title"] else "stripped"
            return hit
    if first:
        al[f"{svc}_album_match"] = "top-hit"
        return first[0][1]
    al[f"{svc}_album_match"] = "none"
    return None


# ---------------------------------------------------------------------------
# Service arms
# ---------------------------------------------------------------------------


@define
class Sample:
    artists: list[Json]
    albums: list[Json]


@define
class Arm:
    name: str
    cache: Cache
    sample: Sample
    log: list[str] = field(factory=list)

    def note(self, msg: str) -> None:
        self.log.append(msg)
        print(f"[{self.name}] {msg}", file=sys.stderr)

    async def run(self) -> None:
        raise NotImplementedError


@define
class SpotifyArm(Arm):
    async def run(self) -> None:
        client = SpotifyAPIClient()
        cache, svc = self.cache, "spotify"
        try:
            for a in self.sample.artists:
                name = a["name"]
                search = await cache.fetch(
                    svc,
                    "artist_search",
                    name,
                    _get,
                    client,
                    "census_search",
                    "/search",
                    {"q": name, "type": "artist", "limit": "10"},
                )
                artist_id = a.get("spotify_artist_id") or _pick(
                    [
                        (i["name"], i["id"])
                        for i in ((search or {}).get("artists") or {}).get("items", [])
                    ],
                    name,
                )
                if not artist_id:
                    self.note(f"no artist match: {name}")
                    continue
                await cache.fetch(
                    svc,
                    "artist",
                    artist_id,
                    _get,
                    client,
                    "census_artist",
                    f"/artists/{artist_id}",
                )
                await cache.fetch(
                    svc,
                    "artist_albums",
                    artist_id,
                    _get,
                    client,
                    "census_artist_albums",
                    f"/artists/{artist_id}/albums",
                    {
                        "include_groups": "album,single,compilation,appears_on",
                        "limit": "50",
                        "market": COUNTRY,
                    },
                )
            ids = [
                a["spotify_artist_id"]
                for a in self.sample.artists
                if a.get("spotify_artist_id")
            ]
            if ids:
                await cache.fetch(
                    svc,
                    "artists_batch",
                    "sample",
                    _get,
                    client,
                    "census_artists_batch",
                    "/artists",
                    {"ids": ",".join(ids[:50])},
                )

            async def album_search(al: Json, variant: str) -> list[tuple[str, Any]]:
                search = await cache.fetch(
                    svc,
                    "album_search",
                    f"{al['artist']} - {variant}",
                    _get,
                    client,
                    "census_search",
                    "/search",
                    {
                        "q": f"album:{variant} artist:{al['artist']}",
                        "type": "album",
                        "limit": "10",
                        "market": COUNTRY,
                    },
                )
                items = ((search or {}).get("albums") or {}).get("items", [])
                return [
                    (i["name"], i["id"], " & ".join(a["name"] for a in i["artists"]))
                    for i in items
                ]

            for al in self.sample.albums:
                key = f"{al['artist']} - {al['title']}"
                album_id = al.get("spotify_album_id")
                if album_id:
                    al["spotify_album_match"] = "library-anchor"
                else:
                    album_id = await _resolve_album(al, "spotify", album_search)
                if not album_id:
                    self.note(f"no album match: {key}")
                    continue
                al["spotify_album_id"] = album_id
                await cache.fetch(
                    svc,
                    "album",
                    album_id,
                    _get,
                    client,
                    "census_album",
                    f"/albums/{album_id}",
                    {"market": COUNTRY},
                )
        finally:
            await client.aclose()


@define
class LastfmArm(Arm):
    async def run(self) -> None:
        client = LastFMAPIClient()
        cache, svc = self.cache, "lastfm"

        async def call(method: str, params: Mapping[str, str]) -> Json | None:
            async def impl() -> Json:
                # Plain single encoding on purpose: the production client's
                # double-encoding workaround makes Last.fm echo "%20" names
                # back for multi-word titles on the album/artist read methods.
                response = await client._client.get(
                    "/",
                    params={
                        "method": method,
                        "api_key": client.api_key or "",
                        "format": "json",
                        **params,
                    },
                )
                _ = response.raise_for_status()
                return parse_json_response(response)

            return await client._api_call(f"census_{method}", impl)

        try:
            for a in self.sample.artists:
                name = a["name"]
                for ac in ("0", "1"):
                    await cache.fetch(
                        svc,
                        f"artist_info_ac{ac}",
                        name,
                        call,
                        "artist.getInfo",
                        {"artist": name, "autocorrect": ac},
                    )
                await cache.fetch(
                    svc,
                    "artist_correction",
                    name,
                    call,
                    "artist.getCorrection",
                    {"artist": name},
                )
                await cache.fetch(
                    svc,
                    "artist_search",
                    name,
                    call,
                    "artist.search",
                    {"artist": name, "limit": "10"},
                )
                await cache.fetch(
                    svc,
                    "artist_top_albums",
                    name,
                    call,
                    "artist.getTopAlbums",
                    {"artist": name, "limit": "10"},
                )
            for al in self.sample.albums:
                key = f"{al['artist']} - {al['title']}"
                await cache.fetch(
                    svc,
                    "album_info",
                    key,
                    call,
                    "album.getInfo",
                    {"artist": al["artist"], "album": al["title"], "autocorrect": "1"},
                )
                await cache.fetch(
                    svc,
                    "album_search",
                    key,
                    call,
                    "album.search",
                    {"album": al["title"], "limit": "10"},
                )
                for variant in _title_variants(al["title"])[1:]:
                    await cache.fetch(
                        svc,
                        "album_info",
                        f"{al['artist']} - {variant}",
                        call,
                        "album.getInfo",
                        {"artist": al["artist"], "album": variant, "autocorrect": "1"},
                    )
        finally:
            await client.aclose()


@define
class AppleArm(Arm):
    """Apple Music API when developer keys are configured, else iTunes Lookup.

    The developer JWT (APPLE_TEAM_ID / APPLE_KEY_ID / APPLE_PRIVATE_KEY) lives
    only in the hosted environment. The unauthenticated iTunes Search/Lookup
    API addresses the same catalog ids (adamId), so identity anchors, credits,
    disc/track ordering and compilation semantics are still measurable — the
    Apple Music API's ``relationships`` and ``isCompilation`` are not, and the
    findings doc marks them ⚠︎.
    """

    async def run(self) -> None:
        from src.infrastructure.connectors.apple_music.auth import (
            DeveloperTokenProvider,
        )

        try:
            _ = DeveloperTokenProvider().get_token()
        except RuntimeError:
            self.note("no Apple developer key — using iTunes Search/Lookup")
            await self._run_itunes()
            return
        await self._run_apple_music()

    async def _run_itunes(self) -> None:
        cache, svc = self.cache, "apple"
        client = httpx2.AsyncClient(base_url="https://itunes.apple.com", timeout=30.0)
        gate = asyncio.Lock()

        async def call(path: str, params: Mapping[str, str]) -> Json | None:
            async with gate:  # iTunes Search API: ~20 calls/min
                await asyncio.sleep(3.1)
                response = await client.get(path, params={**params, "country": COUNTRY})
                if response.status_code in SOFT_STATUSES:
                    return _soft_error(response)
                if response.status_code == 403:  # throttled
                    await asyncio.sleep(60)
                    response = await client.get(
                        path, params={**params, "country": COUNTRY}
                    )
                _ = response.raise_for_status()
                return parse_json_response(response)

        try:
            for a in self.sample.artists:
                name = a["name"]
                search = await cache.fetch(
                    svc,
                    "itunes_artist_search",
                    name,
                    call,
                    "/search",
                    {"term": name, "entity": "musicArtist", "limit": "10"},
                )
                items = (search or {}).get("results", [])
                artist_id = _pick_artist(
                    [(i.get("artistName", ""), i.get("artistId")) for i in items], a
                )
                if not artist_id:
                    self.note(f"no artist match: {name}")
                    continue
                a["apple_artist_id"] = str(artist_id)
                await cache.fetch(
                    svc,
                    "itunes_artist_albums",
                    str(artist_id),
                    call,
                    "/lookup",
                    {"id": str(artist_id), "entity": "album", "limit": "200"},
                )

            async def album_search(al: Json, variant: str) -> list[tuple[str, Any]]:
                search = await cache.fetch(
                    svc,
                    "itunes_album_search",
                    f"{al['artist']} - {variant}",
                    call,
                    "/search",
                    {
                        "term": f"{al['artist']} {variant}",
                        "entity": "album",
                        "limit": "10",
                    },
                )
                items = (search or {}).get("results", [])
                if not items:
                    search = await cache.fetch(
                        svc,
                        "itunes_album_search_title",
                        f"{al['artist']} - {variant}",
                        call,
                        "/search",
                        {"term": variant, "entity": "album", "limit": "10"},
                    )
                    items = (search or {}).get("results", [])
                return [
                    (
                        _ITUNES_SUFFIX.sub("", i.get("collectionName", "")),
                        i.get("collectionId"),
                        i.get("artistName", ""),
                    )
                    for i in items
                ]

            for al in self.sample.albums:
                key = f"{al['artist']} - {al['title']}"
                album_id = await _resolve_album(al, "apple", album_search)
                if not album_id:
                    self.note(f"no album match: {key}")
                    continue
                al["apple_album_id"] = str(album_id)
                await cache.fetch(
                    svc,
                    "itunes_album_tracks",
                    str(album_id),
                    call,
                    "/lookup",
                    {"id": str(album_id), "entity": "song", "limit": "200"},
                )
        finally:
            await client.aclose()

    async def _run_apple_music(self) -> None:
        client = AppleMusicAPIClient()
        cache, svc = self.cache, "apple"
        try:
            try:
                sf_obj = await client.get_storefront()
                sf = sf_obj.id if sf_obj else "us"
            except AppleMusicAuthRequiredError:
                sf = "us"
            self.note(f"storefront={sf}")
            base = f"/v1/catalog/{sf}"
            for a in self.sample.artists:
                name = a["name"]
                search = await cache.fetch(
                    svc,
                    "artist_search",
                    name,
                    _get,
                    client,
                    "census_search",
                    f"{base}/search",
                    {"term": name, "types": "artists", "limit": "10"},
                )
                items = (
                    ((search or {}).get("results") or {}).get("artists") or {}
                ).get("data", [])
                artist_id = _pick_artist(
                    [(i["attributes"]["name"], i["id"]) for i in items], a
                )
                if not artist_id:
                    self.note(f"no artist match: {name}")
                    continue
                a["apple_artist_id"] = artist_id
                await cache.fetch(
                    svc,
                    "artist",
                    artist_id,
                    _get,
                    client,
                    "census_artist",
                    f"{base}/artists/{artist_id}",
                    {"include": "albums", "views": "full-albums,compilation-albums"},
                )

            async def album_search(al: Json, variant: str) -> list[tuple[str, Any]]:
                search = await cache.fetch(
                    svc,
                    "album_search",
                    f"{al['artist']} - {variant}",
                    _get,
                    client,
                    "census_search",
                    f"{base}/search",
                    {
                        "term": f"{al['artist']} {variant}",
                        "types": "albums",
                        "limit": "10",
                    },
                )
                items = (((search or {}).get("results") or {}).get("albums") or {}).get(
                    "data", []
                )
                return [
                    (
                        i["attributes"]["name"],
                        i["id"],
                        i["attributes"].get("artistName", ""),
                    )
                    for i in items
                ]

            for al in self.sample.albums:
                key = f"{al['artist']} - {al['title']}"
                album_id = await _resolve_album(al, "apple", album_search)
                if not album_id:
                    self.note(f"no album match: {key}")
                    continue
                al["apple_album_id"] = album_id
                await cache.fetch(
                    svc,
                    "album",
                    album_id,
                    _get,
                    client,
                    "census_album",
                    f"{base}/albums/{album_id}",
                    {"include": "tracks,artists"},
                )
        finally:
            await client.aclose()


@define
class DiscogsArm(Arm):
    async def run(self) -> None:
        client = DiscogsAPIClient()
        cache, svc = self.cache, "discogs"

        async def call(
            path: str, params: Mapping[str, str] | None = None
        ) -> Json | None:
            async def impl() -> Json:
                response = await client._client.get(
                    path, params=params, auth=await client._resolve_auth()
                )
                apply_rate_headers(response, "discogs")
                soft = _soft_error(response)
                if soft is not None:
                    return soft
                _ = response.raise_for_status()
                return parse_json_response(response)

            async with connector_call_slot("discogs"):
                return await client._api_call("census_discogs", impl)

        try:
            await client._resolve_auth()
        except DiscogsAuthRequiredError:
            self.note("no Discogs token — arm skipped (mixd connect discogs)")
            await client.aclose()
            return
        try:
            for a in self.sample.artists:
                name = a["name"]
                search = await cache.fetch(
                    svc,
                    "artist_search",
                    name,
                    call,
                    "/database/search",
                    {"q": name, "type": "artist", "per_page": "10"},
                )
                results = (search or {}).get("results", [])
                # Discogs' compilation sentinel is artist 194 "Various"; the
                # search hits named "Various Artists (n)" are unrelated entities
                artist_id = (
                    194
                    if name == "Various Artists"
                    else _pick_artist(
                        [(_discogs_name(r["title"]), r["id"]) for r in results], a
                    )
                )
                if not artist_id:
                    self.note(f"no artist match: {name}")
                    continue
                a["discogs_artist_id"] = artist_id
                await cache.fetch(
                    svc, "artist", str(artist_id), call, f"/artists/{artist_id}"
                )
                await cache.fetch(
                    svc,
                    "artist_releases",
                    str(artist_id),
                    call,
                    f"/artists/{artist_id}/releases",
                    {"sort": "year", "sort_order": "asc", "per_page": "100"},
                )

            async def master_search(al: Json, variant: str) -> list[tuple[str, Any]]:
                # Discogs credits compilations to "Various", not "Various Artists"
                artist = (
                    "Various" if al["artist"] == "Various Artists" else al["artist"]
                )
                search = await cache.fetch(
                    svc,
                    "master_search",
                    f"{al['artist']} - {variant}",
                    call,
                    "/database/search",
                    {
                        "artist": artist,
                        "release_title": variant,
                        "type": "master",
                        "per_page": "10",
                    },
                )
                results = (search or {}).get("results", [])
                if not results:  # fielded search is literal; fall back to free text
                    search = await cache.fetch(
                        svc,
                        "master_search_q",
                        f"{al['artist']} - {variant}",
                        call,
                        "/database/search",
                        {
                            "q": f"{artist} {variant}",
                            "type": "master",
                            "per_page": "10",
                        },
                    )
                    results = (search or {}).get("results", [])
                # Discogs titles are "Artist - Title"; a hit may be a release
                out: list[AlbumCandidate] = []
                for r in results:
                    artist_part, _, title_part = r["title"].partition(" - ")
                    out.append((
                        _discogs_name(title_part or artist_part),
                        r.get("master_id") or r["id"],
                        _discogs_name(artist_part) if title_part else "",
                    ))
                return out

            for al in self.sample.albums:
                key = f"{al['artist']} - {al['title']}"
                master_id = await _resolve_album(al, "discogs", master_search)
                if not master_id:
                    self.note(f"no master match: {key}")
                    continue
                al["discogs_master_id"] = master_id
                master = await cache.fetch(
                    svc, "master", str(master_id), call, f"/masters/{master_id}"
                )
                await cache.fetch(
                    svc,
                    "master_versions",
                    str(master_id),
                    call,
                    f"/masters/{master_id}/versions",
                    {"per_page": "100"},
                )
                main_release = (master or {}).get("main_release")
                if main_release:
                    al["discogs_release_id"] = main_release
                    await cache.fetch(
                        svc,
                        "release",
                        str(main_release),
                        call,
                        f"/releases/{main_release}",
                    )
        finally:
            await client.aclose()


@define
class TidalArm(Arm):
    async def run(self) -> None:
        client = TidalAPIClient()
        cache, svc = self.cache, "tidal"

        async def call(
            path: str, params: Mapping[str, str] | None = None
        ) -> Json | None:
            async def impl() -> Json:
                response = await client._client.get(path, params=params)
                if response.status_code == 401:
                    raise TidalAuthRequiredError("Tidal token rejected")
                soft = _soft_error(response)
                if soft is not None:
                    return soft
                _ = response.raise_for_status()
                return parse_json_response(response)

            return await client._api_call("census_tidal", impl)

        async def probe() -> None:
            for a in self.sample.artists:
                name = a["name"]
                search = await cache.fetch(
                    svc,
                    "artist_search",
                    name,
                    call,
                    f"/searchResults/{quote(name, safe='')}/relationships/artists",
                    {"countryCode": COUNTRY, "include": "artists"},
                )
                if search is None:
                    self.note(f"search returned nothing: {name}")
                    continue
                inc = [
                    i for i in search.get("included", []) if i.get("type") == "artists"
                ]
                artist_id = _pick_artist(
                    [(i["attributes"]["name"], i["id"]) for i in inc], a
                )
                if not artist_id:
                    self.note(f"no artist match: {name}")
                    continue
                a["tidal_artist_id"] = artist_id
                await cache.fetch(
                    svc,
                    "artist",
                    artist_id,
                    call,
                    f"/artists/{artist_id}",
                    {"countryCode": COUNTRY, "include": "albums,profileArt"},
                )

            async def album_search(al: Json, variant: str) -> list[tuple[str, Any]]:
                term = f"{al['artist']} {variant}"
                search = await cache.fetch(
                    svc,
                    "album_search",
                    f"{al['artist']} - {variant}",
                    call,
                    f"/searchResults/{quote(term, safe='')}/relationships/albums",
                    {"countryCode": COUNTRY, "include": "albums"},
                )
                inc = [
                    i
                    for i in (search or {}).get("included", [])
                    if i.get("type") == "albums"
                ]
                return [(i["attributes"]["title"], i["id"], "") for i in inc]

            for al in self.sample.albums:
                key = f"{al['artist']} - {al['title']}"
                album_id = await _resolve_album(al, "tidal", album_search)
                if not album_id:
                    self.note(f"no album match: {key}")
                    continue
                al["tidal_album_id"] = album_id
                await cache.fetch(
                    svc,
                    "album",
                    album_id,
                    call,
                    f"/albums/{album_id}",
                    {"countryCode": COUNTRY, "include": "items,artists,replacement"},
                )

        try:
            await probe()
        except TidalAuthRequiredError:
            self.note("no Tidal token — arm skipped (mixd connect tidal)")
        finally:
            await client.aclose()


@define
class MusicBrainzArm(Arm):
    async def run(self) -> None:
        client = MusicBrainzAPIClient()
        cache, svc = self.cache, "musicbrainz"
        try:
            for a in self.sample.artists:
                name = a["name"]
                search = await cache.fetch(
                    svc,
                    "artist_search",
                    name,
                    _get,
                    client,
                    "census_mb_search",
                    "/artist",
                    {"query": f'artist:"{name}"', "limit": "10"},
                )
                items = (search or {}).get("artists", [])
                mbid = _pick_mb_artist(items, a, loose=False)
                if not mbid:
                    # The fielded ``artist:`` query ranks a tribute band above
                    # a renamed primary; the alias index is a separate field.
                    search = await cache.fetch(
                        svc,
                        "artist_search_alias",
                        name,
                        _get,
                        client,
                        "census_mb_search",
                        "/artist",
                        {"query": f'alias:"{name}"', "limit": "10"},
                    )
                    items = (search or {}).get("artists", [])
                    mbid = _pick_mb_artist(items, a, loose=True)
                if not mbid:
                    self.note(f"no artist match: {name}")
                    continue
                a["mbid"] = mbid
                await cache.fetch(
                    svc,
                    "artist",
                    mbid,
                    _get,
                    client,
                    "census_mb_artist",
                    f"/artist/{mbid}",
                    {"inc": "aliases+url-rels+artist-rels"},
                )
                await cache.fetch(
                    svc,
                    "artist_release_groups",
                    mbid,
                    _get,
                    client,
                    "census_mb_rgs",
                    "/release-group",
                    {"artist": mbid, "type": "album", "limit": "100"},
                )

            async def rg_search(al: Json, variant: str) -> list[tuple[str, Any]]:
                q = f'releasegroup:"{variant}" AND artist:"{al["artist"]}"'
                search = await cache.fetch(
                    svc,
                    "rg_search",
                    f"{al['artist']} - {variant}",
                    _get,
                    client,
                    "census_mb_search",
                    "/release-group",
                    {"query": q, "limit": "10"},
                )
                items = (search or {}).get("release-groups", [])
                return [
                    (
                        i["title"],
                        i["id"],
                        "".join(
                            c.get("name", "") for c in i.get("artist-credit") or []
                        ),
                    )
                    for i in items
                ]

            for al in self.sample.albums:
                key = f"{al['artist']} - {al['title']}"
                rg = await _resolve_album(al, "musicbrainz", rg_search)
                if not rg:
                    self.note(f"no release-group match: {key}")
                    continue
                al["mb_release_group"] = rg
                detail = await cache.fetch(
                    svc,
                    "release_group",
                    rg,
                    _get,
                    client,
                    "census_mb_rg",
                    f"/release-group/{rg}",
                    {"inc": "releases+artist-credits+url-rels"},
                )
                releases = (detail or {}).get("releases", [])
                if releases:
                    rel = releases[0]["id"]
                    al["mb_release"] = rel
                    await cache.fetch(
                        svc,
                        "release",
                        rel,
                        _get,
                        client,
                        "census_mb_release",
                        f"/release/{rel}",
                        {"inc": "media+recordings+artist-credits+url-rels+labels"},
                    )
        finally:
            await client.aclose()


ARMS: dict[str, type[Arm]] = {
    "spotify": SpotifyArm,
    "lastfm": LastfmArm,
    "apple": AppleArm,
    "discogs": DiscogsArm,
    "tidal": TidalArm,
    "musicbrainz": MusicBrainzArm,
}


# ---------------------------------------------------------------------------
# sample — resolve corpus against the library (read-only)
# ---------------------------------------------------------------------------

TOP_ARTISTS_SQL = """
SELECT artists->'names'->>0 AS artist, sum(play_count) AS plays, count(*) AS tracks
FROM tracks WHERE user_id = :u GROUP BY 1 ORDER BY 2 DESC LIMIT 60
"""
COLLABS_SQL = """
SELECT artists->'names' AS credit, sum(play_count) AS plays, count(*) AS tracks
FROM tracks WHERE user_id = :u AND jsonb_array_length(artists->'names') > 1
GROUP BY 1 ORDER BY 2 DESC LIMIT 15
"""
TOP_ALBUMS_SQL = """
SELECT artists->'names'->>0 AS artist, album, sum(play_count) AS plays, count(*) AS tracks
FROM tracks WHERE user_id = :u AND album IS NOT NULL
GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 40
"""
MULTI_ARTIST_ALBUMS_SQL = """
SELECT album, count(DISTINCT artists->'names'->>0) AS artists, sum(play_count) AS plays
FROM tracks WHERE user_id = :u AND album IS NOT NULL
GROUP BY 1 HAVING count(DISTINCT artists->'names'->>0) >= 4 ORDER BY 3 DESC LIMIT 15
"""
EDITION_ALBUMS_SQL = """
SELECT artists->'names'->>0 AS artist, album, sum(play_count) AS plays
FROM tracks WHERE user_id = :u
  AND album ~* '(remaster|deluxe|anniversary|expanded|edition|reissue)'
GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20
"""
ARTIST_ANCHOR_SQL = """
SELECT sa->>'id' AS spotify_artist_id, count(*) AS n
FROM tracks t
JOIN track_mappings m ON m.track_id = t.id AND m.user_id = t.user_id
  AND m.connector_name = 'spotify' AND m.superseded_by_id IS NULL
JOIN connector_tracks c ON c.id = m.connector_track_id
CROSS JOIN LATERAL jsonb_array_elements(c.raw_metadata->'artists') sa
WHERE t.user_id = :u AND t.artist_normalized = :a AND sa->>'name' = :name
GROUP BY 1 ORDER BY 2 DESC LIMIT 1
"""
ARTIST_PLAYS_SQL = """
SELECT sum(play_count) AS plays, count(*) AS tracks
FROM tracks WHERE user_id = :u AND artist_normalized = :a
"""
ALBUM_ANCHOR_SQL = """
SELECT coalesce(c.raw_metadata->'album'->>'id', c.raw_metadata->>'album_id')
         AS spotify_album_id, count(*) AS n,
       sum(t.play_count) AS plays
FROM tracks t
JOIN track_mappings m ON m.track_id = t.id AND m.user_id = t.user_id
  AND m.connector_name = 'spotify' AND m.superseded_by_id IS NULL
JOIN connector_tracks c ON c.id = m.connector_track_id
WHERE t.user_id = :u AND lower(t.album) = lower(:album)
  AND t.artist_normalized = :a
GROUP BY 1 ORDER BY 2 DESC LIMIT 1
"""


def _print_rows(title: str, rows: list[Any]) -> None:
    print(f"\n== {title}")
    for r in rows:
        print("  | " + " | ".join(str(x)[:70] for x in r))


async def cmd_sample(user_id: str, cache: Cache) -> None:
    artists: list[Json] = []
    albums: list[Json] = []
    async with get_session() as s:
        await s.execute(text("SET TRANSACTION READ ONLY"))
        p = {"u": user_id}
        for title, sql in (
            ("top artists by plays", TOP_ARTISTS_SQL),
            ("multi-artist credits", COLLABS_SQL),
            ("top albums by plays", TOP_ALBUMS_SQL),
            ("albums with >=4 first-artists", MULTI_ARTIST_ALBUMS_SQL),
            ("edition-qualified album strings", EDITION_ALBUMS_SQL),
        ):
            _print_rows(title, (await s.execute(text(sql), p)).all())
        for name, bucket in CENSUS_ARTISTS:
            a = _norm(name)
            plays = (
                await s.execute(text(ARTIST_PLAYS_SQL), {"u": user_id, "a": a})
            ).one()
            anchor = (
                await s.execute(
                    text(ARTIST_ANCHOR_SQL), {"u": user_id, "a": a, "name": name}
                )
            ).first()
            artists.append({
                "name": name,
                "bucket": bucket,
                "library_plays": int(plays.plays or 0),
                "library_tracks": int(plays.tracks or 0),
                "spotify_artist_id": anchor.spotify_artist_id if anchor else None,
            })
        for artist, title, bucket in CENSUS_ALBUMS:
            anchor = (
                await s.execute(
                    text(ALBUM_ANCHOR_SQL),
                    {"u": user_id, "a": _norm(artist), "album": title},
                )
            ).first()
            albums.append({
                "artist": artist,
                "title": title,
                "bucket": bucket,
                "library_plays": int(anchor.plays or 0) if anchor else 0,
                "spotify_album_id": anchor.spotify_album_id if anchor else None,
            })
    cache.root.mkdir(parents=True, exist_ok=True)
    (cache.root / "sample.json").write_text(
        json.dumps({"artists": artists, "albums": albums}, ensure_ascii=False, indent=1)
    )
    in_lib = sum(1 for a in artists if a["library_tracks"])
    anchored = sum(1 for a in artists if a["spotify_artist_id"])
    print(
        f"\nsample: {len(artists)} artists ({in_lib} in library, {anchored} with Spotify "
        f"anchor), {len(albums)} albums "
        f"({sum(1 for a in albums if a['spotify_album_id'])} with Spotify anchor)"
    )
    _print_rows(
        "artists without a Spotify anchor",
        [(a["name"], a["bucket"]) for a in artists if not a["spotify_artist_id"]],
    )
    _print_rows(
        "albums without a Spotify anchor",
        [(a["artist"], a["title"]) for a in albums if not a["spotify_album_id"]],
    )


def load_sample(cache: Cache) -> Sample:
    data = json.loads((cache.root / "sample.json").read_text())
    return Sample(artists=data["artists"], albums=data["albums"])


def save_sample(cache: Cache, sample: Sample) -> None:
    (cache.root / "sample.json").write_text(
        json.dumps(
            {"artists": sample.artists, "albums": sample.albums},
            ensure_ascii=False,
            indent=1,
        )
    )


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------


async def cmd_probe(cache: Cache, services: tuple[str, ...]) -> None:
    sample = load_sample(cache)
    arms = [ARMS[s](name=s, cache=cache, sample=sample) for s in services]
    results = await asyncio.gather(*(arm.run() for arm in arms), return_exceptions=True)
    for arm, result in zip(arms, results, strict=True):
        if isinstance(result, BaseException):
            arm.note(f"FAILED: {result!r}")
    save_sample(cache, sample)  # ids resolved during probing are kept
    print("\nprobe notes:")
    for arm in arms:
        for line in arm.log:
            print(f"  {arm.name}: {line}")


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


def _g(d: Json | None, *path: str | int, default: Any = None) -> Any:
    cur: Any = d
    for p in path:
        if isinstance(cur, dict):
            cur = cur.get(p)
        elif isinstance(cur, list) and isinstance(p, int) and p < len(cur):
            cur = cur[p]
        else:
            return default
        if cur is None:
            return default
    return cur


def _table(headers: list[str], rows: list[list[Any]]) -> None:
    print("| " + " | ".join(headers) + " |")
    print("|" + "|".join("---" for _ in headers) + "|")
    for r in rows:
        print("| " + " | ".join(str(x).replace("|", "\\|") for x in r) + " |")
    print()


def report_artists(cache: Cache, sample: Sample) -> None:
    print("### Artist identity anchors and aliases\n")
    rows = []
    for a in sample.artists:
        name = a["name"]
        sp = cache.get("spotify", "artist", a.get("spotify_artist_id") or "") or {}
        spb = sp.get("body") or {}
        lf = _g(cache.get("lastfm", "artist_info_ac0", name), "body", "artist") or {}
        lfc = _g(cache.get("lastfm", "artist_correction", name), "body", "corrections")
        corr = unquote(_g(lfc, "correction", "artist", "name") or "")
        ap = _g(
            cache.get("apple", "artist", a.get("apple_artist_id") or ""),
            "body",
            "data",
            0,
        )
        dc = (
            _g(
                cache.get("discogs", "artist", str(a.get("discogs_artist_id") or "")),
                "body",
            )
            or {}
        )
        td = _g(
            cache.get("tidal", "artist", a.get("tidal_artist_id") or ""), "body", "data"
        )
        mb = _g(cache.get("musicbrainz", "artist", a.get("mbid") or ""), "body") or {}
        aliases = mb.get("aliases") or []
        rows.append([
            name,
            a["bucket"],
            spb.get("id") or "—",
            (lf.get("mbid") or "—") if lf else "—",
            corr or "—",
            _g(ap, "id") or "—",
            f"{dc.get('id')} anv={len(dc.get('namevariations') or [])} "
            f"aliases={len(dc.get('aliases') or [])}"
            if dc.get("id")
            else "—",
            _g(td, "id") or "—",
            f"{mb.get('id', '')[:8]} {mb.get('type') or '?'} aliases={len(aliases)}"
            if mb.get("id")
            else "—",
        ])
    _table(
        [
            "artist",
            "bucket",
            "spotify",
            "lastfm mbid",
            "lastfm correction",
            "apple",
            "discogs",
            "tidal",
            "musicbrainz",
        ],
        rows,
    )


def report_albums(cache: Cache, sample: Sample) -> None:
    print("### Album identity, layer, credits, ordering\n")
    rows = []
    for al in sample.albums:
        key = f"{al['artist']} - {al['title']}"
        sp = (
            _g(cache.get("spotify", "album", al.get("spotify_album_id") or ""), "body")
            or {}
        )
        sp_tracks = _g(sp, "tracks", "items") or []
        sp_discs = {t.get("disc_number") for t in sp_tracks}
        sp_credit = " / ".join(x.get("name", "") for x in sp.get("artists") or [])
        lf = _g(cache.get("lastfm", "album_info", key), "body", "album") or {}
        ap = _g(
            cache.get("apple", "album", al.get("apple_album_id") or ""),
            "body",
            "data",
            0,
        )
        ap_tracks = _g(ap, "relationships", "tracks", "data") or []
        ap_discs = {_g(t, "attributes", "discNumber") for t in ap_tracks}
        dm = (
            _g(
                cache.get("discogs", "master", str(al.get("discogs_master_id") or "")),
                "body",
            )
            or {}
        )
        dv = (
            _g(
                cache.get(
                    "discogs", "master_versions", str(al.get("discogs_master_id") or "")
                ),
                "body",
            )
            or {}
        )
        dr = (
            _g(
                cache.get(
                    "discogs", "release", str(al.get("discogs_release_id") or "")
                ),
                "body",
            )
            or {}
        )
        d_credit = " ".join(
            f"{x.get('anv') or x.get('name')}{(' ' + x['join']) if x.get('join') else ''}"
            for x in dr.get("artists") or dm.get("artists") or []
        ).strip()
        td = (
            _g(cache.get("tidal", "album", al.get("tidal_album_id") or ""), "body")
            or {}
        )
        td_attr = _g(td, "data", "attributes") or {}
        td_repl = _g(td, "data", "relationships", "replacement", "data")
        mbrg = (
            _g(
                cache.get(
                    "musicbrainz", "release_group", al.get("mb_release_group") or ""
                ),
                "body",
            )
            or {}
        )
        mbr = (
            _g(cache.get("musicbrainz", "release", al.get("mb_release") or ""), "body")
            or {}
        )
        mb_credit = "".join(
            f"{c.get('name', '')}{c.get('joinphrase', '')}"
            for c in mbrg.get("artist-credit") or []
        )
        rows.append([
            key,
            al["bucket"],
            f"{sp.get('album_type', '—')} {len(sp_tracks)}t/{len(sp_discs)}d [{sp_credit}]"
            if sp.get("id")
            else "—",
            f"mbid={'y' if lf.get('mbid') else 'n'} {len(_g(lf, 'tracks', 'track') or [])}t"
            if lf.get("name")
            else "—",
            f"{'comp' if _g(ap, 'attributes', 'isCompilation') else 'album'} "
            f"{len(ap_tracks)}t/{len(ap_discs)}d [{_g(ap, 'attributes', 'artistName')}] "
            f"upc={'y' if _g(ap, 'attributes', 'upc') else 'n'}"
            if ap
            else "—",
            f"master={dm.get('id')} versions={dv.get('pagination', {}).get('items', '?')} "
            f"main={dm.get('main_release')} {len(dr.get('tracklist') or [])}t [{d_credit}]"
            if dm.get("id")
            else "—",
            f"{len(_g(td, 'data', 'relationships', 'items', 'data') or [])}t "
            f"{td_attr.get('type', '')} repl={'y' if td_repl else 'n'}"
            if td.get("data")
            else "—",
            f"rg={mbrg.get('id', '')[:8]} {mbrg.get('primary-type')}"
            f"{'+' + ','.join(mbrg.get('secondary-types')) if mbrg.get('secondary-types') else ''} "
            f"releases={len(mbrg.get('releases') or [])} media={len(mbr.get('media') or [])} "
            f"[{mb_credit}]"
            if mbrg.get("id")
            else "—",
        ])
    _table(
        [
            "album",
            "bucket",
            "spotify",
            "lastfm",
            "apple",
            "discogs",
            "tidal",
            "musicbrainz",
        ],
        rows,
    )


def report_linkage(cache: Cache, sample: Sample) -> None:
    print("### MusicBrainz url-rels per sampled artist (cross-service linkage)\n")
    targets = ("spotify", "apple", "discogs", "tidal", "last.fm", "wikidata", "deezer")
    rows = []
    for a in sample.artists:
        mb = _g(cache.get("musicbrainz", "artist", a.get("mbid") or ""), "body") or {}
        rels = mb.get("relations") or []
        urls = [_g(r, "url", "resource", default="") for r in rels]
        counts = {t: sum(1 for u in urls if t in u) for t in targets}
        dc = (
            _g(
                cache.get("discogs", "artist", str(a.get("discogs_artist_id") or "")),
                "body",
            )
            or {}
        )
        durls = dc.get("urls") or []
        rows.append(
            [a["name"], len(rels)]
            + [counts[t] for t in targets]
            + [len(durls), sum(1 for u in durls if "musicbrainz" in u)]
        )
    _table(["artist", "mb rels", *targets, "discogs urls", "discogs→mb"], rows)


def report_search_ambiguity(cache: Cache, sample: Sample) -> None:
    print("### Search ambiguity — top-3 artist candidates per service\n")
    rows = []
    for a in sample.artists:
        name = a["name"]
        sp = (
            _g(cache.get("spotify", "artist_search", name), "body", "artists", "items")
            or []
        )
        ap = (
            _g(
                cache.get("apple", "artist_search", name),
                "body",
                "results",
                "artists",
                "data",
            )
            or []
        )
        dc = _g(cache.get("discogs", "artist_search", name), "body", "results") or []
        td = [
            i
            for i in (
                _g(cache.get("tidal", "artist_search", name), "body", "included") or []
            )
            if i.get("type") == "artists"
        ]
        mb = (
            _g(cache.get("musicbrainz", "artist_search", name), "body", "artists") or []
        )
        lf = (
            _g(
                cache.get("lastfm", "artist_search", name),
                "body",
                "results",
                "artistmatches",
                "artist",
            )
            or []
        )
        rows.append([
            name,
            "; ".join(i["name"] for i in sp[:3]),
            "; ".join(i["name"] for i in lf[:3]),
            "; ".join(_g(i, "attributes", "name", default="") for i in ap[:3]),
            "; ".join(i["title"] for i in dc[:3]),
            "; ".join(_g(i, "attributes", "name", default="") for i in td[:3]),
            "; ".join(f"{i['name']}({i.get('score')})" for i in mb[:3]),
        ])
    _table(
        ["artist", "spotify", "lastfm", "apple", "discogs", "tidal", "musicbrainz"],
        rows,
    )


def cmd_report(cache: Cache) -> None:
    sample = load_sample(cache)
    report_artists(cache, sample)
    report_albums(cache, sample)
    report_linkage(cache, sample)
    report_search_ambiguity(cache, sample)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    setup_script_logger("diagnose_entity_representation")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("sample", "probe", "report"))
    parser.add_argument("--user-id", default=None)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--service", action="append", choices=SERVICES)
    args = parser.parse_args()

    user_id = args.user_id or settings.cli.user_id or BusinessLimits.DEFAULT_USER_ID
    cache = Cache(root=args.cache_dir)
    print(f"user={user_id} cache={cache.root}", file=sys.stderr)

    with user_context(user_id):
        if args.command == "sample":
            asyncio.run(cmd_sample(user_id, cache))
        elif args.command == "probe":
            asyncio.run(cmd_probe(cache, tuple(args.service or SERVICES)))
        else:
            cmd_report(cache)


if __name__ == "__main__":
    main()
