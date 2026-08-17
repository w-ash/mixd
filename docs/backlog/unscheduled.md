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
- **Spotify Feb-2026 dev-mode endpoint audit** (S, added 2026-07-16) — The user relies on Spotify import/playlist flows that Spotify's February 2026 Web API changes may have quietly degraded: Development Mode apps lost batch fetches (`GET /tracks?ids=…` — used by `get_tracks_concurrent`?), `POST /users/{id}/playlists` (check which endpoint `create_playlist` calls), and search `limit` was cut 50→10 (matching-layer search pagination); existing apps were migrated 2026-03-09. Audit mixd's client calls against the [removal list](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide); if the app runs under extended quota mode, note that and close. Surfaced during the v0.10.1 polling review — `GET /me/player/recently-played` itself is explicitly retained.
- **Two-Way Like Synchronization** (M) - Bidirectional sync between services with conflict resolution
- **Workflow Debugging Tools** (L) - Interactive debugging for workflow testing
- **Playlist Diffing and Merging** (L) - Visualize differences between local and remote playlists
- **Canonical Genre Support** (L) — A curator wants to filter and build playlists by genre ("rock but not metal", "shoegaze deep cuts") when freeform tags (v0.7.1) are too sparse or inconsistent to rely on.
    - **Open question (decide first)**: should `filter_by_genre("rock")` auto-resolve subgenres? MusicBrainz recording lookup returns a *flat* voted list — a "shoegaze" track does not auto-include "rock" — so hierarchy isn't free. Options: (a) flat — the user lists every genre explicitly; (b) fetch/cache the MB genre tree, resolve at enrichment; (c) resolve at filter time.
    - **Direction**: MusicBrainz `inc=genres` as the curated primary source (1 req/s via the existing `MusicBrainzAPIClient`; needs an MBID, so identity resolution runs first). Spotify genres are artist-only + deprecated (not viable); Last.fm tags are high-coverage but noisy ("seen live", "female vocalist") — deferred behind a confidence threshold.
    - **Spec (when scheduled)**: genres as a first-class Track attribute (not `TrackMetric` / `connector_metadata`); leaning toward a separate `EnrichGenresUseCase` + `genres` JSON column + ~1yr TTL + a pure-domain `filter_by_genre` transform. Full locked plan in `.claude/plans/cached-booping-sedgewick.md` — not inlined here.

## Identity Resolution (2026-07 research)

All eight stories from the identity/governance research passes are now **scheduled** (2026-07-02), sequenced by what each unblocks. Findings and evidence remain in [identity-resolution-design-space.md](identity-resolution-design-space.md) + [identity-governance-design-space.md](identity-governance-design-space.md):

- ~~Characterization test net · Confidence integrity repair · ISRC guard · Last.fm identifier unification · Healing correctness · Matching drift metrics~~ → **[v0.8.18: Identity Integrity](v0.8.18.md)** — active-corruption repairs; must precede v0.12.1's artist identity (which mirrors track-matching patterns) and the v0.11.x Apple Music foundation
- ~~Mapping supersession + resolution event log~~ → **[v0.10.2: Mapping Supersession & Resolution Event Log](v0.10.x.md#v0102-mapping-supersession--resolution-event-log)** (pulled forward 2026-07-16) — substrate for the manual-mapping UI (v0.14.0), v0.11.0's platform-asserted successors, and the v1.1.0 ledger backfill
- ~~Alias-aware artist comparison~~ → **[v0.12.1: First-Class Artists](v0.12.x.md#v0121-first-class-artists)** — first Artist Identity epic (depends on v0.8.18)
- **Selective Last.fm MBID verification** (M) — The user wants ambiguous Last.fm matches (duration off, title similarity below exact) to get a second opinion so the right recording wins — without paying MusicBrainz's 1 req/s on *every* track, which a past per-track attempt proved unusably slow. v0.8.18 demoted all Last.fm matches to `artist_title` scoring (unverified MBIDs are junk — LB-431) but preserves the MBID as `service_data["lastfm_mbid"]`. **Trigger-gated verification**: consult MusicBrainz only when cheap evidence disagrees (duration delta beyond tolerance, title below exact tier); verification must be Track-vs-Recording aware and follow 301 merges (FM7d says the client's 301 handling is itself unverified — same work item). A verified Recording MBID re-earns `mbid`-grade scoring (`ISRC_GRADE_METHODS` already keeps the slot open). Natural home: near v1.1.0's re-validation machinery.
    - **v0.8.18 review hardening (persistence side)** — the identity review (2026-07) found the matching layer's distrust was undone in persistence: `lastfm/inward_resolver.py` injected the Last.fm MBID into `connector_track_identifiers['musicbrainz']`, which fed `save_track`'s `uq_tracks_user_mbid` **merge** key, so a stale/type-confused Last.fm MBID could collapse two distinct recordings. The injection was removed — the unverified MBID now survives only in the enrichment debug log, not in any identity slot. So this work item must **also** (a) capture the unverified hint somewhere durable (e.g. the LASTFM mapping's `metadata`) rather than only logs, and (b) re-promote a *verified* Recording MBID back into `connector_track_identifiers['musicbrainz']` — not just re-score — since the write path no longer trusts the raw value.

- **Decide whether `tracks.isrc` is an identity key or a reference** (M, added 2026-08-10) — The user's
  model is that a canonical track's ISRC is *reference only*: the authoritative per-release codes live
  on its linked `connector_tracks`, one per release, which is how the data already behaves (one
  canonical on prod holds four Spotify ids, and 81,893 of 81,954 connector tracks carry their own
  ISRC). But `uq_tracks_user_isrc` is a `UNIQUE(user_id, isrc)` constraint, which asserts the opposite
  — a reference field cannot be a uniqueness key without also being an identity claim.

    **Why it matters**: it did not cause the v0.10.3 twin groups (those differ by ISRC; ISRC-keyed
    *reuse* caused them) and it does not block the 29 pending merges, since `mixd track merge` deletes
    the loser row. But the mismatch keeps generating false alarms — it was the reason a merge looked
    unsafe during triage until the connector-track ISRCs were checked. Decide: keep the constraint and
    document `tracks.isrc` as "the primary release's code, load-bearing", or drop it and let
    `connector_tracks.isrc` carry identity alone.

    **Natural home**: v0.12.x, which revisits entity identity for artists and albums and will face the
    same reference-vs-key question one level up.

## Data Ownership

The larger question — what "ownership of listening data" means architecturally (sovereign-server + exit rights vs Obsidian-style local-first) — is held open with an evidence ledger in [PDR-001](../decisions/PDR-001-data-ownership-model.md).

- ~~**Continuous Personal Archive (exit rights made real)**~~ → Scheduled as **[v0.15.0: Data Sovereignty — Archive & Exit Rights](v0.15.x.md#v0150-data-sovereignty--archive--exit-rights)** (2026-07-02; renumbered 2026-07-16) — deliberately sequenced immediately before the v1.0.0 gate: exit rights ship before other people's data lives on hosted instances. Direction-neutral under PDR-001.

## Enrichment Sources

- ~~**Discogs Enrichment Provider**~~ → Scheduled as [v0.13.1: Physical Media & Discogs](v0.13.x.md#v0131-physical-media--discogs)
- **ListenBrainz Integration** (M) — Open-source scrobble store with a strong [PDR-001](../decisions/PDR-001-data-ownership-model.md) data-ownership fit: full history exportable, no ads/tiers. *(2026-07 check: simple token auth — `Authorization: Token`, no signed requests; `POST /1/submit-listens` batches up to 1,000 vs Last.fm's 50; API current.)* v0.13.2's `ScrobbleSink` seam is the designed write-side entry point (scrobble out); listen-history import lands as a new observation channel (`listenbrainz`/`listenbrainz_api`) on the [v0.10.0 convergence projection](v0.10.x.md#v0100-convergent-play-history) — channel-keyed grouping means no new dedup code, and neither side touches the record-listen use case. Candidate open alternative to maintenance-mode Last.fm. [Docs](https://listenbrainz.readthedocs.io/).
- **Audio Analysis Provider — BPM, Key, Energy** (M) - Track-level audio features (BPM, musical key, time signature, danceability, energy) now that Spotify's Audio Features API is deprecated (403 for new apps since late 2024). Candidate sources:
    - [GetSongBPM](https://getsongbpm.com/api) — free API, attribution required. BPM, key, time signature, danceability, acousticness.
    - [Tunebat](https://tunebat.com/API) — paid API. BPM, key, energy, danceability, popularity. More comprehensive but has costs.
    - Architecture: new `MetadataProvider` implementation, stores results in `TrackMetric` (BPM, energy, danceability are floats) or new columns for key/time signature.

## Artists & Albums Follow-Ups (deferred from v0.10.x planning, 2026-07-16)

- **Artist Favorites Service Sync** (S) — Mirror Spotify followed artists into `artist_favorites` as *evidence*, never overwrite — favorites are Mixd-owned curation per the v0.12.1 decision. Deferred from v0.12.1 to keep favorites Mixd-only first.
- **Discogs Write-Back** (M) — Push manual ownership edits (adds, removals, notes) to the user's Discogs collection. v0.13.1 is deliberately read-only — the user manages their collection on Discogs; revisit if manual-entry usage shows users treating mixd as the primary collection editor. Requires OAuth 1.0a (write scope) — the auth-strategy seam in the v0.11.1 client is the entry point.

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

### Import-pipeline hardening, phase 2 (deferred from v0.10.2.2, 2026-08-07)

Gate: all three wait for the first clean at-scale prod import to land (the v0.10.2.2 trio — chunked resolution commits, connector rate limiting, bulk statement-timeout budget — is expected to be sufficient for it; these are the structural follow-ups the 2026-08 hardening research recommended).

- **Three-pass chunked resolution** (L) — The user wants imports that can't be corrupted or stalled by a flaky external API: restructure Phase-2 resolution per chunk into read (load + local matches, short transaction) → gather (batch connector API calls, NO open transaction) → write (apply results, short transaction). Eliminates the API-call-inside-transaction anti-pattern wholesale and makes in-memory tracker state trivially consistent (built only from committed reads). Research: universal guidance; our own `ChatUseCase` exemption already states the principle.
- **Ledger resolution-status columns + re-drive** (M) — The user wants "retry failed items" as a first-class operation instead of re-running whole imports: add `resolution_status ∈ (pending, resolved, failed, quarantined)`, `resolution_attempts`, `last_resolution_error` to `connector_plays`; failed rows re-driven via `SELECT … WHERE status='failed' FOR UPDATE SKIP LOCKED` (the standard Postgres-as-DLQ shape). Replaces JSONB-issue-only error capture with queryable per-item state; migration + repo methods + a `mixd history retry-failed` surface.
- **Import-throughput follow-ons** (deferred from the v0.10.2.4 performance audit, 2026-08-08 — measured baseline: ~4.5h+ for the 200k set pre-fix, dominated 80/14 by per-track DB writes and per-id API probes):
  - *Last.fm monthly fetch windows* (M) — the day loop costs one `user.getRecentTracks` call per calendar day even for zero-scrobble days (~5,100 sequential calls for 14 years); monthly windows cut it to ~600. Cost: coarser checkpoint granularity.
  - *Concurrent gather for Last.fm resolution* (L) — `_create_tracks_batch` is a sequential per-identifier loop (getInfo + a Spotify cross-discovery search each, ~0.5-0.8s/track); gathering the API half concurrently under the 4.5/s bucket is the three-pass redesign's "gather" pass (~50 min saved on ~10k unknowns).
  - *Cross-discovery off the import path* (S/M) — a full Spotify search per unknown Last.fm track is ~half its sequential calls; defer to post-import enrichment at the cost of lower first-pass linkage.
  - *(Savepoint-counting chunk commits was **dropped** in v0.10.2.11: its premise was false — savepoints are per-batch, not per-item, so a chunk holds ~10-15 against a cap of 64 that `commit_batch` resets each chunk, and a counter cannot help because chunk size is fixed before it is known whether the bulk write will fail. Replaced by the `degraded_persists` signal, which is what would gate any future chunk-size increase.)*
  - *Bind-parameter ceilings* (S, latent) — `_RESOLUTION_BATCH_SIZE` at 5,000 × 6 params is within ~2× of Postgres's 65,535 cap with no guard tying them; `bulk_upsert_play_sources` / `bulk_insert_plays` are unchunked (caps ~10.9k / ~5k rows) and a single dense projection day could exceed them. Add assertions + chunking. **Real trigger is not chunk sizing** (the projection chunks by UTC day, which cannot hold 5k real plays) but a GDPR export with epoch/malformed `ts` values collapsing 198k plays onto 1970-01-01 — which fails the whole import with an opaque parameter-count error. Derive the cap from `model_class.__table__.columns` rather than hardcoding it.
  - *`ms_played` IS NOT DISTINCT FROM in the dedupe probe* (S, latent) — not btree-indexable, so `uq_connector_plays_deduplication` serves only its leading equality columns on that path.
- **Direct Neon endpoint for bulk imports** (XS, returned from v0.10.2.11 with its premise corrected and its value measured) — the original rationale ("attacks the 26ms constant") was wrong: measured from the `sjc` machine, pooler RTT was 24.55ms and direct 23.29ms, so **PgBouncer costs 1.26ms** and the rest was geography (fixed by co-locating to `sea`). At ~68 statements/chunk the whole candidate is worth ~90ms — about 5% of what the region move bought, for the price of a second engine. Re-measure from `sea` before considering it; the latency case is close to closed. What remains is **backend affinity**: PgBouncer runs transaction mode, so each `commit_batch` can hand the next transaction to a different server backend, discarding psycopg3's prepared statements and the plan cache. Plausible, unproven. **Gate it on evidence, not reasoning**: sample `pg_stat_activity` during an import (the method that diagnosed v0.10.2.9) — a stable backend pid means there is nothing to win and this should be closed. If built, note the hard prerequisite already shipped: `create_session_factory`'s RLS listener is now `event.contains`-guarded, without which a second factory would have doubled the RLS statement for every transaction in the process.
- **Probe-ordinal matching in `find_tracks_by_title_artist`** (S, found during v0.10.2.11) — `DISTINCT ON (probe.pair_ordinal)` with an ordinality column would return "first match per pair, oldest by id" directly and delete the Python-side `lookup_to_lower` index entirely. Deliberately *not* done inside a perf change that had to stay provably behaviour-neutral, because it is a behaviour change: today a track already claimed by pair A cannot be claimed by pair B, so a second connector id for the same recording falls through to creation and produces a duplicate canonical — which is exactly what canonical reuse exists to prevent. Confirm that is a real latent bug, then fix it as its own story.
- **Colour-fragile CLI output assertions** (S, found while fixing a CI-only failure in v0.10.2.11) — Rich splits styled tokens, so `--at` renders as `ESC[1;36m-ESC[0mESC[1;36m-atESC[0m` and a literal `"--at" in result.output` is false whenever colour is on. That is why `test_cadence_requires_at` passed on every developer machine and failed only under CI's xdist workers. `tests/fixtures/cli_output.plain()` now exists and is applied in `test_sync_commands.py` and `test_playlist_commands.py`; running `FORCE_COLOR=1 uv run pytest tests/unit/interface/cli/` shows **8 more** in `test_tag_commands.py`, `test_workflow_commands.py` and others that are latent today. Sweep them through `plain()`. Negative assertions (`"Traceback" not in output`) matter most — they pass *spuriously* against styled output, so they are not currently testing anything under colour.
- **Search-fallback depth** (S, deferred from v0.10.2.3) — The user wants dead-ID rescue to keep working as catalogs drift: paginate past the single 10-result page (Feb-2026 dev-mode cap), and try ISRC-based search when a dead id's canonical already carries an ISRC. Revisit if the v0.10.3 audit shows residual false dead-IDs after the quoted-query fix.
- **Backoff-clearing surface + suppressed-metric honesty** (S, deferred from v0.10.2.3) — The user wants a way to say "try this one again now" (`mixd tracks retry-dead-id <id>` clearing the no-match backoff) instead of waiting out the 1→32-day curve; and the next-import metrics currently report `dead_ids_unresolved: 0` for a play that still fails because its id is `suppressed` — surface suppressed counts in the import result so the numbers agree with the outcome.

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

## Agentic Workspace Extensions

- **Scheduled / Background Agents** (L, design-first) — The Weekly Curator wants their Sunday maintenance ritual to run *without them*: "every Sunday, review the week's imports, flag suspect matches, refresh my rotation playlists, and leave me a summary" — the agent initiates work on a schedule or trigger, not just in response to a chat turn (the pattern Notion shipped as Custom Agents in early 2026). Mixd already has the pieces this composes: workflow scheduling (v0.8.2–v0.8.4) for *deterministic* recurring work, the shared tool registry + parity contract (v0.9.0/v0.9.1) for agent capabilities, and operation runs for auditable results.
    - **Shape (sketch, not spec)**: a stored agent task (prompt + schedule/trigger + effort budget) executed by the same chat loop headlessly; every mutation still lands as an operation-run record attributed to the agent; results delivered as a run-log entry + a summary the user reads next time they open chat. Mutation policy is the open design question — headless runs can't two-phase-confirm, so either scoped pre-authorization per task ("may edit these playlists") or propose-only mode (queue confirmations for the user) must come first.
    - **Activation trigger**: real demand after v0.9.1 ships — i.e., users repeatedly re-issuing the same maintenance conversations. Don't build speculatively; the interactive assistant must prove the capability surface first.

## Tenancy hardening

- **Remove `Track.user_id`/`TrackLike.user_id` silent `'default'`** (M, added 2026-08-08) — The user wants canonicals to always land under the tenant that created them: the v0.10.2.9 incident traced mistenanted Spotify-import canonicals (and their one-shot repair script, `cleanup_mistenanted_spotify_tracks.py`) to `Track.user_id`'s silent `"default"` — `create_track_from_spotify_data` never set it, and nothing failed loudly. Make `user_id` a required field on both entities (no default; `DEFAULT_USER_ID` stays a local-dev convenience at the interface edge, never an entity fallback), and consider an optional repository-level write-path backstop that rejects a persist whose entity tenant disagrees with the ambient `user_context`. Deferred from the v0.10.2.9 review (F8): an entity-signature change touching every construction site deserves its own pass, not a hotfix rider.

- **Make RLS actually enforce, or stop claiming it does** (L, added 2026-08-10) — The user is promised
  multi-tenant isolation, and today it rests entirely on application-level `user_id` predicates: every
  tenant table has RLS *enabled and forced*, and production connects as `neondb_owner` with
  `rolbypassrls = true`, which supersedes `FORCE`. Measured during the v0.10.3 audit
  ([findings](v0.10.3-audit-findings.md) C3/C7), where it let a match-before-create reuse another
  tenant's canonical tracks — 20 of the user's plays ended up depending on `default`-owned rows through
  a cascading FK. One missed predicate is currently a cross-tenant read with no backstop.

    **Decisions to make**: a non-bypass application role is the obvious fix, but Neon's owner role is
    what migrations and several scripts assume — so this is a connection-identity change, not a config
    toggle. Alternative is to drop the RLS pretence and rely on predicates plus tests, which is at
    least honest. Either way `CLAUDE.md`'s "multi-tenancy (RLS)" claim should match reality.

    **Blocks**: nothing today (single real user), but it gates v1.0.0's invited testers — that is the
    first moment a missed predicate stops being theoretical.

## Social & Infrastructure

- **ActivityPub Federation** (XL) - Mastodon-style federation allowing independent Mixd instances to follow users across instances. Users on instance A could follow curators on instance B, see their public playlists and activity in their feed. Would use the ActivityPub protocol (W3C standard) for inter-instance communication. Significant complexity: federated identity, cross-instance content resolution, inbox/outbox delivery, signature verification, moderation across instances. Interesting long-term direction but adds an order of magnitude of infrastructure complexity to the social layer. Evaluate after v1.1.x social features prove out the single-instance model.

## Not Building

Items explicitly descoped — they serve neither persona or are Data Exploiter thinking.

- **Multi-Language Support** — Serves neither persona at current scale.
- **Advanced Analytics Dashboard** — Vague scope, no persona need. If workflow perf metrics are needed, a single metric on the existing dashboard suffices.

## Design Debt (2026-06 review)

→ **Scheduled into the v0.8.x series** (2026-06-15): the user-facing correctness cluster ships first as [v0.8.5 Operation & Surface Reliability](v0.8.5-0.8.6.md#v085-operation--surface-reliability); structural/cleanup items + the two rule-change proposals follow as [v0.8.6 Cycle Hardening & Cleanup](v0.8.5-0.8.6.md#v086-cycle-hardening--cleanup). Full evidence and design-spaces remain in [design-debt-findings.md](design-debt-findings.md).
## Deferred Clean Architecture Improvements

- **Write-free workflow preview** (L, design-first) — Preview is deliberately "a run minus destination writes": sources commit canonical rows because downstream nodes re-query them by id from their own sessions (rationale in `workflow_preview.py`). Write-free would need a shared no-commit session threaded through the engine — and would hold uncommitted identity keys for the preview's whole duration, the exact lock-contention blocker the ingest retry exists to survive. **Revisit only if preview materialization causes real problems.**

- **Domain Layer Logging Abstraction** (S) - Remove infrastructure dependency from domain layer
- **Multi-value config-field validation parity** (S) — `cfg_str_list` (runtime) accepts a config value as *either* a JSON array (`["a","b"]`) or a comma-separated string (`"a,b"`), but `validate_workflow_def`'s `_FIELD_TYPE_MAP` maps `field_type="string"` → `str` and rejects the array form. So a `filter.by_tag` / `filter.by_tag_namespace` workflow using list-valued `tags`/`values` runs fine yet fails validation if re-saved through the create path. Surfaced (and worked around at the data level) in v0.8.9 by fixing `mood_playlist.json` to the string form. Real fix: either a `"string_list"` field type the validator understands, or accept both shapes for these keys. Touches `config_fields.py` (field types) + `validation.py` (`_FIELD_TYPE_MAP`).

> _Scheduled 2026-06 (from the v0.8.9 review): config-aware enrichment validation + the day-window key rename + the editor-store lifecycle contract all shipped in [v0.8.10](v0.8.9-0.8.10.md) (the play-history-config follow-ons were pulled into v0.8.10 rather than a separate version). The Multi-value item above is the same subsystem and a natural companion if picked up._

## Workflow Editor

- **Navigating large (40+ node) workflows** (M, design-first) — _Problem:_ real power-user workflows reach 40+ nodes (the bundled gallery templates are 5–9-node starter content and aren't representative), and the canvas offers no aid for orienting within a large graph. _Considered + rejected:_ sub-flows / collapsible named groups (planned for v0.8.10, cut) — too much overhead (a persistence model + the cycle's only `WorkflowDef` schema change) for the problem, and snippet reuse overlaps v0.8.9 templates + import/export. _Lighter candidates to weigh (starting point, not a chosen design):_ React Flow `<MiniMap>`; a node-search / jump-to-node command (palette-style, focus + center on match); an outline / index side panel listing nodes by type with click-to-focus; fit-view-to-selection. Aim for one or two cheap wins, no schema change. Touches `EditorCanvas.tsx` + the editor store.

- **Expose `enricher.play_history` metrics + `period_days` as editor config fields** (S) — _Problem:_ `enricher.play_history` has an empty config-field tuple (`config_fields.py`), so the editor renders no inputs for `metrics` or `period_days` — they live only in hand-authored/template JSON. Consequence (surfaced by v0.8.10's config-aware validation): an editor-built `filter.by_metric(metric_name="period_plays")` over a default enricher *always* warns and the user can't fix it in the UI (can't add `period_plays`). _Fix:_ add a multi-select `metrics` field (the four `ENRICHER_METRIC_DEFS` values) + a `period_days` number field to the enricher's config-field tuple, making the warning actionable in-editor. Needs the `"string_list"` field-type work tracked under *Deferred Clean Architecture Improvements*.

- **Play-history template descriptions vs mechanism** (XS) — several gallery templates describe period semantics ("Current Obsessions: 8+ plays in the last 30 days") but implement `min_plays` over `total_plays` + a recency filter, not `period_plays` over a window — behaviorally "8+ *total* plays, played within 30 days," close but not what the copy says. Per template, either reword the description to match the total-plays mechanism, or switch to `period_plays` + `period_days` (a behavior change). Surfaced during the v0.8.10 inert-`period_days` cleanup.

## Testing & CI

- **Generated reference docs for API + schema skills** (S) - The `api-contracts` and `database-schema` skills hand-synced enumerable facts (endpoint tables, table censuses) and drifted twice: the v0.8.6 doc-truing pass found 7 whole routers missing, and the 2026-07-03 rules/skills audit found the schema skill still saying 28 tables after `playlist_sync_bases` made it 29. Both skills were slimmed to semantics + pointers-to-generated-truth as the stopgap. The durable fix: generate the endpoint reference from `web/openapi.json` during `pnpm --prefix web sync-api`, and a table/RLS census from SQLAlchemy metadata (same metadata-derivation pattern as the v0.7.7.1 test-TRUNCATE fix), emitted as `references/` files the skills point at — hand-synced mirrors of generatable truth always decay.

- **✅ Resolved (v0.8.10)** — ~~Track down the latent web-suite unhandled error~~ (S). Root cause was **not** an un-torn-down subscription: `useWorkflowSSE`'s snapshot-recovery effect read `snapshot.nodes.length` assuming `nodes` is always present. A unit test whose mocked SSE stream *ended* (`mockSSEWithEvents` completes its generator) fired `onStreamEnd` while `isRunning` was still true, enabling the **real** `GET /operations/{id}/snapshot` recovery query; that late-resolving fetch arrived against a torn-down mock with `nodes` undefined → the cross-test `TypeError`. Fixed by defaulting `snapshot.nodes ?? []` in the recovery effect (also a genuine partial-snapshot robustness win) plus abort-signal guards on `useSSEConnection`'s consumer-loop `setState`s. Full `pnpm --prefix web test` now exits 0 across repeated runs.

- **E2E suite hardening — restore mobile + functional coverage** (M) - The `web-e2e` CI job was chronically red (independent of any one feature) for two pre-existing reasons, descoped during the v0.8.1 ship to make the gate meaningful: (1) `navigation.spec.ts` and `playlist-browse.spec.ts` assert on **live backend data** but the Playwright CI container has no backend (`pnpm dev` can't boot Postgres+API there → `ECONNREFUSED`), violating the documented "mock the API via `page.route()`" pattern (`web-e2e-patterns.md`); (2) the `iphone-15-pro` project's baseline snapshots were never captured/committed (only `chromium` exists → every mobile shot fails "snapshot doesn't exist"). **To restore**: add `page.route()` fixtures to the two functional specs per the `auth-smoke.spec.ts` pass-through pattern, then remove them from `testIgnore` in `playwright.config.ts`; re-add the `iphone-15-pro` project and generate its baselines **in the pinned Playwright Docker image** (`web/e2e/README.md` procedure — local macOS PNGs won't match). Note: the chromium `visual.spec.ts` baselines currently capture *empty-state* pages (no backend), so consider whether visual baselines should be captured against mocked data for meaningful coverage.
