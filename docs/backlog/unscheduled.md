# Unscheduled Backlog

Backlog items not yet assigned to a version. Items move to a version detail file (e.g., `v0.7.x.md`) when prioritized.
For the planning overview, see [README.md](README.md).

---

## CLI Power Tools

- **Global `--json` Output Flag** (M) - Add `--json` to root Typer callback, stored in `Context.obj`. Commands with existing `--format` take precedence; all others get JSON output for free. JSON always to stdout (pipe-friendly), errors to stderr. Enables CLI scriptability and integration with `jq`, `grep`, etc.
- **`mixd db` Debug Commands** (M) - Infrastructure-level debug tools that bypass use cases: `mixd db stats` (row counts per table, index sizes), `mixd db export --table tracks --format json|csv` (data dump for debugging), `mixd db health` (connection test, Alembic migration status, latency ping). New file: `src/interface/cli/db_commands.py`.
- **`mixd admin claim-data`** (S) - Reassign `user_id='default'` data to a specified user_id (`UPDATE` all 11 user-scoped tables). For local-to-remote migration scenarios. Confirmation prompt with row counts before proceeding.
- **`mixd debug resolve`** (S) - Interactive track matching test: `mixd debug resolve "Artist" "Title" --connector spotify`. Calls matching engine directly, shows candidate matches with confidence scores. For diagnosing incorrect matches.
- **CLI Scriptability Polish** (S) - Consistent exit codes (0 success, 1 error, 2 user cancel), ensure errors to stderr and data to stdout (audit all `console.print` vs `err_console.print`), machine-readable error output with `--json`: `{"error": {"code": "...", "message": "..."}}` on stderr.

## Quality of Life Improvements

- **Two-Way Like Synchronization** (M) - Bidirectional sync between services with conflict resolution
- **Workflow Debugging Tools** (L) - Interactive debugging for workflow testing
- **Playlist Diffing and Merging** (L) - Visualize differences between local and remote playlists
- **Canonical Genre Support** (L) - Complements v0.7.1 freeform tagging with curated MusicBrainz genre data. Add `genres: list[str]` as a first-class Track attribute (like `album` or `isrc`), NOT in `TrackMetric` (float-only) or `connector_metadata` (transient per-connector). Enables workflow transforms like `filter_by_genre(include=["rock"], match_mode="any")`. Source attribution comes free from existing `DBTrackMapping` → `DBConnectorTrack` linkage.
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

- ~~**Discogs Enrichment Provider**~~ → Scheduled as [v0.10.2: Physical Media & Discogs](v0.10.x.md#v0102-physical-media--discogs)
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
    - Apple Music / iTunes — via existing Apple Music connector (v0.7.x)
    - Amazon Music — general catalog
    - URL template pattern: `https://www.beatport.com/search?q={artist}+{title}`
    - User-configurable: toggle which stores appear, reorder preferences

## Additional Connector Support

- **Tidal Connector** (L) - Full Tidal integration via official developer API (OAuth 2.1). Library access, playlists, catalog, favorites. Follows `BaseAPIConnector` pattern established by Spotify/Last.fm. [Developer portal](https://developer.tidal.com/), `tidalapi` Python library available on PyPI.
- **Deezer Connector** (L) - Deezer integration via free public API (OAuth 2.0). Library, playlists, catalog (73M+ tracks). No API key costs. [Developer portal](https://developers.deezer.com/).
- **SoundCloud Connector** (M) - SoundCloud integration via public API (OAuth 2.0). More creator-oriented than library-focused, but supports playlists and liked tracks. Lower priority — less aligned with Mixd's library management use case. [Developer docs](https://developers.soundcloud.com/docs).

## Import Flow Polish

- **Pre-Import Library Overlap Preview** (M) - Before confirming a playlist import, show per-playlist "N already in library, M new" counts so the curator knows how much an import grows their library. Cache-only read via `connector_repo.find_tracks_by_connectors` against the cached `DBConnectorPlaylist.items` — no Spotify API call. Uncached playlists fall back to "Counts on import." Perf mitigation: use `connector_track_id = ANY(:ids::text[])` (single-connector shape) instead of tuple-IN to stay fast even at 10k-track playlists. Deferred from v0.7.6 because v0.7.7's Operation Run Log makes the post-import "what actually happened" signal more useful to the curator than the pre-import library-growth forecast. Revisit if users ask for library-growth forecasting, or if a batch-import workflow needs per-playlist triage before committing.

## Bulk Playlist Operations

Deferred from v0.7.6 to keep that sub-version focused on single-playlist preference/tag flows. Each item below is genuinely useful but only earns its keep once bulk-flow demand is observable.

- **Per-Playlist Sync-Direction Override in Batch Import** (S) - Per-row Pull/Push toggle in the multi-select playlist import confirm dialog. Default stays batch-wide; per-row override is progressive disclosure. Backend widens `ImportSpotifyPlaylistsRequest` to accept `overrides: list[{connector_playlist_id, sync_direction}] | None`. Revisit when users routinely do mixed-direction multi-playlist imports in one go.
- **`bulk_insert_returning_inserted` Extraction** (XS) - Extract the `pg_insert(...).on_conflict_do_nothing(...).returning(id)` + filter-by-inserted-id pattern from `track/tags.py:add_tags` and `playlist/links.py:create_links_batch` into `BaseRepository`. Pattern is stable; rule of 3 not yet met. Revisit when a third caller hand-rolls the same shape.
- **Generic `_model_to_values()` via SQLAlchemy `inspect()`** (S) - `BaseModelMapper.default_values_dict(db_model)` using `inspect(type(db_model)).mapper.column_attrs` to iterate columns; mappers with custom serialization (like `ConnectorPlaylist.items`) override. Revisit when a third mapper needs bulk upsert.
- **CTE-Based `create_links_batch`** (S) - Fold the current SELECT-then-INSERT round-trip in `PlaylistLinkRepository.create_links_batch` into one CTE-based statement. The two-query version's explicit pre-insert `missing` `ValueError` is currently worth the RTT. Revisit if a `--all` playlist import shows >500ms latency attributable to the two-query pattern.
- **SSE Progress for Metadata Import** (M) - Live per-mapping progress bar for "Import All" on the playlist-mapping list. Engine emits `progress` + `conflict` events; CLI gets the same via `progress_coordination_context`. Use case accepts an optional `ProgressEmitter`. Revisit when "Import All" is used on 50+ mappings and feels unresponsive.
- **UI-Surfaced Conflict Warnings for Cross-Mapping Conflicts** (M) - When two mappings contradict each other (e.g., a track in Star + Nah), surface the conflict in the UI: pre-import dry-run banner + post-import detail from streamed `conflict` events. New `dry_run: bool = False` mode on `ImportPlaylistMetadataUseCase`; `POST /api/v1/playlist-mappings/import/preview` route. Revisit when users have ≥3 active mappings and report stale-feeling auto-resolves.
- **Per-Mapping `last_applied_at` for Conflict Tiebreak** (S) - Add `last_applied_at: datetime` to `PlaylistAssignment` so same-state contradictions resolve "most-recently-imported wins" instead of iteration-order luck. Migration adds nullable column + backfills. Depends on UI-Surfaced Conflict Warnings for visibility; revisit together.

## Playlist Link Enhancements

- ~~**Browse/Search User's Playlists from Connector**~~ → Scheduled as [v0.8.1: Editor Polish, Templates & Playlist Browse](v0.8.x.md#v081-editor-polish-templates--playlist-browse)
- **MIRROR Sync Direction** (L) - True bidirectional sync with conflict detection and resolution UI. Currently only push (canonical→external) and pull (external→canonical) are supported.
- **Sync History Table** (M) - Full audit trail of all sync operations per link, beyond the current last-sync summary. Browsable in the UI.
- **Scheduled Sync** (M) - Daily/weekly automatic sync of linked playlists via Prefect scheduling. Depends on PAUSED sync state.
- ~~**Playlist Sync Safety Guards**~~ → Scheduled as [v0.5.8: Playlist Sync Safety Guards](v0.5.x.md#v058-playlist-sync-safety-guards)
- **External Change Detection** (S) - Compare Spotify `snapshot_id` (or equivalent) to detect external changes since last sync. Enables "out of sync" notifications.
- **PAUSED Sync State** (S) - Allow users to pause sync on a link without unlinking. Requires scheduled sync infrastructure.

## Tag System Polish

- **Cold-Start Suggested Tags Panel** (S) - First-time taggers see an empty input on Track Detail with no guidance about the `mood:`/`energy:`/`context:`/`genre:` namespace convention. Proposed: a panel that renders when `ListTagsResult` is empty, showing four namespace chips; clicking prefills the input with `namespace:` (cursor after the colon). Hidden once any tag exists. Deferred from v0.7.6 as onboarding bloat — revisit if new-user drop-off at the tag step becomes observable.

## Social & Infrastructure

- **ActivityPub Federation** (XL) - Mastodon-style federation allowing independent Mixd instances to follow users across instances. Users on instance A could follow curators on instance B, see their public playlists and activity in their feed. Would use the ActivityPub protocol (W3C standard) for inter-instance communication. Significant complexity: federated identity, cross-instance content resolution, inbox/outbox delivery, signature verification, moderation across instances. Interesting long-term direction but adds an order of magnitude of infrastructure complexity to the social layer. Evaluate after v1.1.x social features prove out the single-instance model.

## Not Building

Items explicitly descoped — they serve neither persona or are Data Exploiter thinking.

- **Multi-Language Support** — Serves neither persona at current scale.
- **Advanced Analytics Dashboard** — Vague scope, no persona need. If workflow perf metrics are needed, a single metric on the existing dashboard suffices.

## Deferred Clean Architecture Improvements

- **Domain Layer Logging Abstraction** (S) - Remove infrastructure dependency from domain layer
