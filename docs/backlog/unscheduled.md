# Unscheduled Backlog

Backlog items not yet assigned to a version. Items move to a version detail file (e.g., `v0.7.x.md`) when prioritized.
For the planning overview, see [README.md](README.md).

---

## Quality of Life Improvements

- **Two-Way Like Synchronization** (M) - Bidirectional sync between services with conflict resolution
- **Workflow Debugging Tools** (L) - Interactive debugging for workflow testing
- **Playlist Diffing and Merging** (L) - Visualize differences between local and remote playlists
- **Canonical Genre Support** (L) - Add `genres: list[str]` as a first-class Track attribute (like `album` or `isrc`), NOT in `TrackMetric` (float-only) or `connector_metadata` (transient per-connector). Enables workflow transforms like `filter_by_genre(include=["rock"], match_mode="any")`. Source attribution comes free from existing `DBTrackMapping` → `DBConnectorTrack` linkage.
    - **MusicBrainz API** (primary source, verified Feb 2026):
        - Endpoint: `GET /ws/2/recording/{MBID}?inc=genres&fmt=json`
        - Response: `[{name: str, id: str, count: int, disambiguation: str}]` — flat list per recording, sorted by community vote count
        - `inc=genres` returns curated taxonomy only; `inc=tags` is broader community superset
        - Rate limit: 1 req/sec (existing `MusicBrainzAPIClient` already handles via asyncio.Lock)
        - Requires MBID on track — identity resolution must run first
        - Thousands of curated genre entries, flat taxonomy
    - **Genre hierarchy** (key open design question):
        - MusicBrainz has genre-genre relationships: subgenre-of, influenced-by, fusion-of
        - BUT recording lookup returns a flat list — hierarchy is NOT embedded in the response
        - A track tagged "shoegaze" does NOT auto-include "alternative rock" or "rock" — only genres explicitly voted on are returned
        - Decision needed: should `filter_by_genre("rock")` auto-resolve subgenres? Options: (a) flat only — user lists all desired genres explicitly, (b) fetch/cache the MB genre tree and resolve at enrichment time, (c) resolve at filter time via a genre tree utility
    - **Other sources evaluated**:
        - Spotify: genres on **artists only** (not tracks) and field is **deprecated** — not viable
        - Last.fm: `track.getTopTags` returns freeform tags with count 0–100, includes non-genre labels ("seen live", "female vocalist") — would need confidence threshold + genre-vs-non-genre filtering. High coverage but noisy. Defer for now, MusicBrainz-only first.
    - **Architecture decisions** (from planning, not yet implemented):
        - Separate `EnrichGenresUseCase` (not extending `EnrichTracksUseCase` — different data shape, writes to Track entity not TrackMetric table)
        - DB: `genres` JSON column + `genres_updated_at` DateTime on `tracks` table (Alembic migration)
        - Freshness: ~1 year TTL (genres are very stable), checked via `genres_updated_at`, separate from metric freshness registry
        - Pure domain `filter_by_genre` transform (genres live on Track, no metadata lookup needed)
        - Full plan available at `.claude/plans/cached-booping-sedgewick.md`

## Enrichment Sources

- **Discogs Enrichment Provider** (M) - Release metadata enrichment via free Discogs API (OAuth 1.0a). 15M+ releases with label, credits, catalog number, format, and release country data. Complements MusicBrainz with stronger vinyl/physical release coverage. [API docs](https://www.discogs.com/developers/).
- **ListenBrainz Integration** (M) - Open-source listening statistics and recommendations via ListenBrainz API. Listen history import/export, user statistics, collaborative filtering recommendations. Could serve as an open alternative to Last.fm for scrobble data. [Docs](https://listenbrainz.readthedocs.io/).
- **Audio Analysis Provider — BPM, Key, Energy** (M) - Track-level audio features (BPM, musical key, time signature, danceability, energy) now that Spotify's Audio Features API is deprecated (403 for new apps since late 2024). Candidate sources:
    - [GetSongBPM](https://getsongbpm.com/api) — free API, attribution required. BPM, key, time signature, danceability, acousticness.
    - [Tunebat](https://tunebat.com/API) — paid API. BPM, key, energy, danceability, popularity. More comprehensive but has costs.
    - Architecture: new `MetadataProvider` implementation, stores results in `TrackMetric` (BPM, energy, danceability are floats) or new columns for key/time signature.

## DJ & Purchase Links

- **DJ Purchase Link-Out** (S) - "Buy this track" links on track detail pages and track list context menus. Links to external DJ download stores using search URL templates (no API integration needed). Platforms:
    - [Beatport](https://www.beatport.com/) — electronic/dance music (partner-only API, use search URLs)
    - [Traxsource](https://www.traxsource.com/) — house/underground
    - [Juno Download](https://www.junodownload.com/) — dance music, WAV/FLAC/AIFF formats
    - [Bandcamp](https://bandcamp.com/) — indie/artist-direct
    - Apple Music / iTunes — via existing Apple Music connector (v0.6.x)
    - Amazon Music — general catalog
    - URL template pattern: `https://www.beatport.com/search?q={artist}+{title}`
    - User-configurable: toggle which stores appear, reorder preferences

## Additional Connector Support

- **Tidal Connector** (L) - Full Tidal integration via official developer API (OAuth 2.1). Library access, playlists, catalog, favorites. Follows `BaseAPIConnector` pattern established by Spotify/Last.fm. [Developer portal](https://developer.tidal.com/), `tidalapi` Python library available on PyPI.
- **Deezer Connector** (L) - Deezer integration via free public API (OAuth 2.0). Library, playlists, catalog (73M+ tracks). No API key costs. [Developer portal](https://developers.deezer.com/).
- **SoundCloud Connector** (M) - SoundCloud integration via public API (OAuth 2.0). More creator-oriented than library-focused, but supports playlists and liked tracks. Lower priority — less aligned with Narada's library management use case. [Developer docs](https://developers.soundcloud.com/docs).

## Lower Priority Ideas

- **Advanced Analytics Dashboard** - Workflow usage and performance metrics
- **Multi-Language Support** - UI translations for international users

## Deferred Clean Architecture Improvements

- **Domain Layer Logging Abstraction** (S) - Remove infrastructure dependency from domain layer
