# Future Ideas & Research

Speculative features, research notes, and deferred improvements.
Items here are not committed — they move to [BACKLOG.md](BACKLOG.md) when prioritized into a version.
For the strategic roadmap, see [ROADMAP.md](../ROADMAP.md).

---

## Quality of Life Improvements

- **Background Sync Capabilities** (M) - Scheduled synchronization of play history and likes
- **Two-Way Like Synchronization** (M) - Bidirectional sync between services with conflict resolution
- **Advanced Node Palette** (M) - Enhanced node selection with categories, search, and favorites
- **Discovery Workflow Templates** (S) - Pre-built templates ("Hidden Gems", "Seasonal Favorites", "Rediscovery")
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

## Lower Priority Ideas

- **Advanced Analytics Dashboard** - Workflow usage and performance metrics
- **Multi-Language Support** - UI translations for international users

## Deferred Clean Architecture Improvements

- **Domain Layer Logging Abstraction** (S) - Remove infrastructure dependency from domain layer
