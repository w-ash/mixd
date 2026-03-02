# Narada — System Summary for Web UI Planning

Narada is a personal music metadata hub. Users import their listening data from Spotify and Last.fm, then build smart playlists using criteria no single service offers — like "tracks I've liked but haven't played in 6 months, sorted by Last.fm play count." The backend handles cross-service identity resolution, enrichment, and sync. Today it's CLI-only.

---

## What the User Needs to Do

### Browse and manage their music library

The user accumulates thousands of tracks through imports and playlist backups. They need to browse, search, and view details for these tracks — including which external services each track is linked to and how confident those matches are.

**What exists today**: You can view a single track by ID. That's it. No browsing, no search, no pagination. The backend has 10k+ tracks with no way to explore them.

**What the web UI needs**: Paginated track list, free-text search (title/artist), track detail view showing metadata + connector mappings + play history + like status. Ability to correct bad matches ("this isn't the right Spotify track").

### Manage playlists

Playlists are the core user-facing object. Users create them, curate them, and sync them with external services.

**What exists today**: The CLI can list all playlists (name, track count) and delete them. It can "backup" an external playlist (pull from Spotify into Narada). But you cannot view a playlist's tracks, create an empty playlist, rename one, add/remove tracks, or reorder tracks through the CLI. The backend use cases for read, create, and update *do* exist — they've just never been exposed to a user interface.

**What the web UI needs**: Full playlist CRUD — create, view, rename, edit description, add/remove/reorder tracks. Playlist detail view showing the ordered track list with added-at dates.

### Understand and control connector links

Each playlist (and each track) can be linked to external services. A canonical playlist might be linked to a Spotify playlist and an Apple Music playlist. These links are how sync works.

**What exists today**: The link data is in the database but completely invisible. Users have no way to see which external playlists are connected, when they last synced, or which direction data flows.

**What the web UI needs**: For each playlist, show its connector links — which Spotify/Apple Music playlist it maps to, last sync time, and sync direction. Allow the user to link/unlink playlists and choose sync direction. For tracks, show connector mappings with confidence scores and match method, and let users correct mistakes.

### Control sync direction

When a canonical playlist is linked to an external one, who's the source of truth? This is a new concept we need to introduce:

- **Narada is master** — user curates locally, pushes to Spotify/Apple Music. External edits get overwritten on next sync.
- **Connector is master** — external playlist is the authority. Local copy stays in sync with Spotify. Local edits get overwritten on next pull.
- **Manual** — no automatic sync. User explicitly triggers push or pull. Safe default.

We intentionally avoid bidirectional sync. Merging concurrent edits to the same playlist is high-risk and low-value for a single-user tool. Pick a master.

### Import data from external services

Users pull in their listening history, liked tracks, and playlists from external services.

**What exists today (CLI)**: Import play history from Last.fm (incremental, with date ranges). Import Spotify history from GDPR export files. Import liked tracks from Spotify. Export likes to Last.fm as "loved" tracks. All of these work and report progress.

**What the web UI needs**: Same operations, but with live progress reporting (the backend already emits progress events with operation ID, current/total counts, and status). File upload for Spotify exports. Display of sync checkpoints so users know "Last.fm history imported through Feb 15."

### Run and manage workflows

Workflows are the power feature — declarative pipelines that combine sources, enrichment, filtering, sorting, and output. "Take my Liked Songs, enrich with Last.fm play counts, keep only tracks played 8+ times in the last 30 days, sort by play count, push top 20 to Spotify."

**What exists today (CLI)**: List available workflows and run them by ID, with progress and results display. This works well.

**What the web UI needs**: Same run + progress experience, plus: CRUD for workflow definitions (currently hand-edited JSON files on disk), execution history, and eventually a visual workflow builder.

### View listening stats

Users import tens of thousands of plays. They want to see top tracks, listening trends, play counts.

**What exists today**: The backend can query played tracks with aggregations (total plays, last played, plays within N days) and liked tracks with sort options. But none of this is exposed to the user — the CLI only does imports.

**What the web UI needs**: Listening history view (recent plays, top tracks by period). Liked tracks view with like dates and cross-service sync status. Dashboard-level stats (total tracks, total plays, tracks per connector, etc.).

---

## Data Model Essentials

### The canonical track pattern

Every track in Narada has a **canonical** record (our internal representation) that can be linked to external service records via **connector mappings**. A single canonical track might map to a Spotify track ID, a Last.fm artist/title pair, and a MusicBrainz recording ID — each with a confidence score (0–100) and match method (`direct`, `isrc`, `mbid`, `artist_title`).

### Key entities

**Track** — title, artists (list), album, duration, release date, ISRC. System-managed: connector IDs, timestamps.

**Playlist** — name, description, ordered list of entries. Each **PlaylistEntry** holds a track reference plus `added_at` and `added_by` — this metadata survives reordering.

**TrackLike** — per-service like status (track + service + is_liked + liked_at). One record per track per service.

**TrackPlay** — immutable play event (track + service + played_at + ms_played). Users can have tens of thousands of these.

**TrackMetric** — cached enrichment data (track + connector + metric_type + value + collected_at). Types include `play_count`, `popularity`, `explicit_flag`, etc.

**ConnectorTrack / ConnectorPlaylist** — cached copies of how tracks and playlists appear in each external service, with full raw metadata.

**ConnectorTrackMapping** — links canonical track ↔ connector track. Has `confidence` (0–100), `match_method`, and `is_primary` flag.

**PlaylistMapping** — links canonical playlist ↔ connector playlist. One link per connector per playlist. Has `last_synced`. Needs a new `sync_direction` field.

**SyncCheckpoint** — tracks where each import left off (service + entity_type + last_timestamp + cursor).

### Relationships

```
Track ←→ ConnectorTrack    (M:N via mapping, with confidence)
Track ←→ Playlist          (M:N via PlaylistEntry, with added_at)
Track → TrackLike          (1:N, one per service)
Track → TrackMetric        (1:N, per connector + metric type)
Track → TrackPlay          (1:N, immutable events)
Playlist → PlaylistMapping → ConnectorPlaylist  (1:N, one per connector)
```

---

## Connector Capabilities

All upstream API details (auth, rate limits, pagination) are abstracted by the backend.

| | Tracks | History | Likes | Playlists | Enrichment |
|-|:------:|:-------:|:-----:|:---------:|:----------:|
| **Spotify** | Read/search | File upload only | Import | Full CRUD | popularity, explicit |
| **Last.fm** | Read | Live import | Export (love) | — | user/global playcount, listeners |
| **Apple Music** | Read/search | — | Read/write favorites | Create, append | — |

**Key asymmetries for UI design**:
- Spotify history requires file upload (GDPR export), not a "sync" button
- Last.fm can't do playlists at all — it's only for history, likes, and enrichment
- Apple Music playlists are append-only — no remove/reorder, so "overwrite" means replace-all
- Enrichment metrics only come from Spotify and Last.fm
- MusicBrainz runs behind the scenes for identity resolution; not user-facing

---

## Workflow System

Workflows are JSON DAGs. Each task has an ID, type, config, and upstream dependencies. The pipeline pattern is: **Source → Enrich → Filter → Sort → Select → Destination**.

### Node types available

**Sources**: Load from a canonical or external playlist.

**Enrichers**: Add metadata — Last.fm (play counts, listeners), Spotify (popularity, explicit flag), or internal play history (total plays, last played, plays in period).

**Filters**: By release date, duration, metric threshold, play history (count + time window), liked status, explicit content, artist exclusion, track exclusion, deduplication.

**Sorters**: By any metric, release date, play history, added-at date, first/last played. Also: reverse and weighted shuffle.

**Selectors**: Limit to N tracks (first/last/random) or percentage.

**Combiners**: Merge (union), concatenate, interleave, or intersect multiple playlists.

**Destinations**: Create or update a playlist, optionally pushing to an external connector. Supports template naming (`{date}`, `{track_count}`, etc.).

Full node configs with parameter details are in `docs/workflow_guide.md`.

---

## Backend Gaps for Web UI

### Infrastructure blockers

- **PostgreSQL migration** — SQLite can't handle concurrent web requests. Must complete before FE work.
- **Containerization** — no Dockerfile today.
- **OAuth token persistence** — Spotify tokens are in a local file; need DB-backed storage for containers.

### Missing use cases

The backend is strong on pipelines but weak on CRUD. These need to be built:

- **Track browsing**: paginated list, free-text search, metadata editing
- **Mapping visibility**: view/correct connector mappings per track, aggregate mapping stats
- **Playlist mapping management**: view/create/remove connector links, set sync direction
- **Liked tracks display**: paginated view with like dates, manual like/unlike
- **Play history display**: browsable history, top tracks by period, per-track play counts
- **Workflow CRUD**: create/edit/delete workflows (currently files on disk)
- **Workflow execution history**: no record of past runs
- **Dashboard stats**: total tracks, plays, playlists, mapped tracks per connector

### Architecture notes

- Every backend operation goes through a single `execute_use_case()` entry point — FastAPI endpoints will call this directly
- Long-running operations (imports, enrichments, workflows) emit `ProgressEvent` objects with `operation_id`, `current`, `total`, `message`, and `status` (`PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`) — plan for SSE or WebSocket
- `OperationResult` has a `.to_dict()` method for JSON serialization
