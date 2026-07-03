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

- **Detail-page error semantics: stop reporting failures as "not found"** (S) — A user on a flaky connection opens a workflow/playlist detail page and is told it "doesn't exist" when the request merely failed: `WorkflowDetail`, `WorkflowRunDetail`, and `PlaylistDetail` render *every* query failure as their not-found `EmptyState`. `TrackDetail` already does this right (`ApiError` 404 → "not found"; anything else → `QueryErrorState` with the real message). Align the other three on the TrackDetail fork. Flagged UX change (error copy/appearance changes), found during the v0.8.15 exploration.
- **PlaylistDetail tracks region has no loading state** (XS) — The page ladder covers only the playlist query; the tracks query renders an empty track list until data arrives, so a slow connection shows a playlist that appears trackless. Gate the tracks region on its own query with a `ListRowsSkeleton`. Flagged UX change, found during the v0.8.15 exploration.
- **Two-Way Like Synchronization** (M) - Bidirectional sync between services with conflict resolution
- **Workflow Debugging Tools** (L) - Interactive debugging for workflow testing
- **Playlist Diffing and Merging** (L) - Visualize differences between local and remote playlists
- **Canonical Genre Support** (L) — A curator wants to filter and build playlists by genre ("rock but not metal", "shoegaze deep cuts") when freeform tags (v0.7.1) are too sparse or inconsistent to rely on.
    - **Open question (decide first)**: should `filter_by_genre("rock")` auto-resolve subgenres? MusicBrainz recording lookup returns a *flat* voted list — a "shoegaze" track does not auto-include "rock" — so hierarchy isn't free. Options: (a) flat — the user lists every genre explicitly; (b) fetch/cache the MB genre tree, resolve at enrichment; (c) resolve at filter time.
    - **Direction**: MusicBrainz `inc=genres` as the curated primary source (1 req/s via the existing `MusicBrainzAPIClient`; needs an MBID, so identity resolution runs first). Spotify genres are artist-only + deprecated (not viable); Last.fm tags are high-coverage but noisy ("seen live", "female vocalist") — deferred behind a confidence threshold.
    - **Spec (when scheduled)**: genres as a first-class Track attribute (not `TrackMetric` / `connector_metadata`); leaning toward a separate `EnrichGenresUseCase` + `genres` JSON column + ~1yr TTL + a pure-domain `filter_by_genre` transform. Full locked plan in `.claude/plans/cached-booping-sedgewick.md` — not inlined here.

## Identity Resolution (2026-07 research)

All eight stories from the identity/governance research passes are now **scheduled** (2026-07-02), sequenced by what each unblocks. Findings and evidence remain in [identity-resolution-design-space.md](identity-resolution-design-space.md) + [identity-governance-design-space.md](identity-governance-design-space.md):

- ~~Characterization test net · Confidence integrity repair · ISRC guard · Last.fm identifier unification · Healing correctness · Matching drift metrics~~ → **[v0.8.18: Identity Integrity](v0.8.18.md)** — active-corruption repairs; must precede v0.10.0's artist identity (which mirrors track-matching patterns) and the v1.0.1 Apple Music connector
- ~~Mapping supersession + resolution event log~~ → **[v1.0.0: Data Quality](v1.0.x.md#v100-data-quality)** — substrate for the manual-mapping UI, v1.0.1's platform-asserted successors, and the v1.0.3 ledger backfill
- ~~Alias-aware artist comparison~~ → **[v0.10.0: First-Class Artists](v0.10.x.md#v0100-first-class-artists)** — first Artist Identity epic (depends on v0.8.18)

## Data Ownership

The larger question — what "ownership of listening data" means architecturally (sovereign-server + exit rights vs Obsidian-style local-first) — is held open with an evidence ledger in [PDR-001](../decisions/PDR-001-data-ownership-model.md).

- ~~**Continuous Personal Archive (exit rights made real)**~~ → Scheduled as **[v1.0.4: Data Sovereignty — Archive & Exit Rights](v1.0.x.md#v104-data-sovereignty--archive--exit-rights)** (2026-07-02) — deliberately the last pre-social milestone: exit rights ship before other people's data lives on hosted instances. Direction-neutral under PDR-001.

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

> **2026-07-02**: the entries below predate the omni-integration research and contain stale program facts (Deezer app registration is closed — enrichment-only today; SoundCloud API keys now require paid Artist Pro; Tidal has no play-history API at all). See the [capability matrix](identity-resolution-design-space.md#6-provider-capability-matrix) for current identity/curation/program status and sequencing evidence before scheduling any of these.

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

## Manual Playlist Track Editing (web flows 3.4–3.6)

→ **Scheduled as [v0.8.11: Manual Playlist Track Editing](v0.8.11.md#v0811-manual-playlist-track-editing)** (2026-06-15). Confirmed for build; entry-identity threading already shipped in v0.8.7, then add → remove → reorder. Design-space in [design-debt-findings.md](design-debt-findings.md) §4 (F6).

## Playlist Link Enhancements

- ~~**Browse/Search User's Playlists from Connector**~~ → Scheduled as [v0.8.10: Editor Polish — Sub-Flows & Playlist Browse](v0.8.9-0.8.10.md#v0810-editor-polish---sub-flows--playlist-browse)
- **MIRROR Sync Direction** (L) - True bidirectional sync with conflict detection and resolution UI. Currently only push (canonical→external) and pull (external→canonical) are supported.
- **Sync History Table** (M) - Full audit trail of all sync operations per link, beyond the current last-sync summary. Browsable in the UI.
- **Scheduled Sync** (M) - Daily/weekly automatic sync of linked playlists via Prefect scheduling. Depends on PAUSED sync state.
- ~~**Playlist Sync Safety Guards**~~ → Scheduled as [v0.5.8: Playlist Sync Safety Guards](completed/v0.5.x.md#v058-playlist-sync-safety-guards)
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

## Design Debt (2026-06 review)

→ **Scheduled into the v0.8.x series** (2026-06-15): the user-facing correctness cluster ships first as [v0.8.5 Operation & Surface Reliability](v0.8.5-0.8.6.md#v085-operation--surface-reliability); structural/cleanup items + the two rule-change proposals follow as [v0.8.6 Cycle Hardening & Cleanup](v0.8.5-0.8.6.md#v086-cycle-hardening--cleanup). Full evidence and design-spaces remain in [design-debt-findings.md](design-debt-findings.md).
## Deferred Clean Architecture Improvements

- **Domain Layer Logging Abstraction** (S) - Remove infrastructure dependency from domain layer
- **Multi-value config-field validation parity** (S) — `cfg_str_list` (runtime) accepts a config value as *either* a JSON array (`["a","b"]`) or a comma-separated string (`"a,b"`), but `validate_workflow_def`'s `_FIELD_TYPE_MAP` maps `field_type="string"` → `str` and rejects the array form. So a `filter.by_tag` / `filter.by_tag_namespace` workflow using list-valued `tags`/`values` runs fine yet fails validation if re-saved through the create path. Surfaced (and worked around at the data level) in v0.8.9 by fixing `mood_playlist.json` to the string form. Real fix: either a `"string_list"` field type the validator understands, or accept both shapes for these keys. Touches `config_fields.py` (field types) + `validation.py` (`_FIELD_TYPE_MAP`).

> _Scheduled 2026-06 (from the v0.8.9 review): config-aware enrichment validation + the day-window key rename + the editor-store lifecycle contract all shipped in [v0.8.10](v0.8.9-0.8.10.md) (the play-history-config follow-ons were pulled into v0.8.10 rather than a separate version). The Multi-value item above is the same subsystem and a natural companion if picked up._

## Workflow Editor

- **Navigating large (40+ node) workflows** (M, design-first) — _Problem:_ real power-user workflows reach 40+ nodes (the bundled gallery templates are 5–9-node starter content and aren't representative), and the canvas offers no aid for orienting within a large graph. _Considered + rejected:_ sub-flows / collapsible named groups (planned for v0.8.10, cut) — too much overhead (a persistence model + the cycle's only `WorkflowDef` schema change) for the problem, and snippet reuse overlaps v0.8.9 templates + import/export. _Lighter candidates to weigh (starting point, not a chosen design):_ React Flow `<MiniMap>`; a node-search / jump-to-node command (palette-style, focus + center on match); an outline / index side panel listing nodes by type with click-to-focus; fit-view-to-selection. Aim for one or two cheap wins, no schema change. Touches `EditorCanvas.tsx` + the editor store.

- **Expose `enricher.play_history` metrics + `period_days` as editor config fields** (S) — _Problem:_ `enricher.play_history` has an empty config-field tuple (`config_fields.py`), so the editor renders no inputs for `metrics` or `period_days` — they live only in hand-authored/template JSON. Consequence (surfaced by v0.8.10's config-aware validation): an editor-built `filter.by_metric(metric_name="period_plays")` over a default enricher *always* warns and the user can't fix it in the UI (can't add `period_plays`). _Fix:_ add a multi-select `metrics` field (the four `ENRICHER_METRIC_DEFS` values) + a `period_days` number field to the enricher's config-field tuple, making the warning actionable in-editor. Needs the `"string_list"` field-type work tracked under *Deferred Clean Architecture Improvements*.

- **Play-history template descriptions vs mechanism** (XS) — several gallery templates describe period semantics ("Current Obsessions: 8+ plays in the last 30 days") but implement `min_plays` over `total_plays` + a recency filter, not `period_plays` over a window — behaviorally "8+ *total* plays, played within 30 days," close but not what the copy says. Per template, either reword the description to match the total-plays mechanism, or switch to `period_plays` + `period_days` (a behavior change). Surfaced during the v0.8.10 inert-`period_days` cleanup.

## Testing & CI

- **✅ Resolved (v0.8.10)** — ~~Track down the latent web-suite unhandled error~~ (S). Root cause was **not** an un-torn-down subscription: `useWorkflowSSE`'s snapshot-recovery effect read `snapshot.nodes.length` assuming `nodes` is always present. A unit test whose mocked SSE stream *ended* (`mockSSEWithEvents` completes its generator) fired `onStreamEnd` while `isRunning` was still true, enabling the **real** `GET /operations/{id}/snapshot` recovery query; that late-resolving fetch arrived against a torn-down mock with `nodes` undefined → the cross-test `TypeError`. Fixed by defaulting `snapshot.nodes ?? []` in the recovery effect (also a genuine partial-snapshot robustness win) plus abort-signal guards on `useSSEConnection`'s consumer-loop `setState`s. Full `pnpm --prefix web test` now exits 0 across repeated runs.

- **E2E suite hardening — restore mobile + functional coverage** (M) - The `web-e2e` CI job was chronically red (independent of any one feature) for two pre-existing reasons, descoped during the v0.8.1 ship to make the gate meaningful: (1) `navigation.spec.ts` and `playlist-browse.spec.ts` assert on **live backend data** but the Playwright CI container has no backend (`pnpm dev` can't boot Postgres+API there → `ECONNREFUSED`), violating the documented "mock the API via `page.route()`" pattern (`web-e2e-patterns.md`); (2) the `iphone-15-pro` project's baseline snapshots were never captured/committed (only `chromium` exists → every mobile shot fails "snapshot doesn't exist"). **To restore**: add `page.route()` fixtures to the two functional specs per the `auth-smoke.spec.ts` pass-through pattern, then remove them from `testIgnore` in `playwright.config.ts`; re-add the `iphone-15-pro` project and generate its baselines **in the pinned Playwright Docker image** (`web/e2e/README.md` procedure — local macOS PNGs won't match). Note: the chromium `visual.spec.ts` baselines currently capture *empty-state* pages (no backend), so consider whether visual baselines should be captured against mocked data for meaningful coverage.
