# API Contracts

> Endpoint reference for the Narada REST API.
> Each endpoint notes which existing use case backs it and its implementation status.
> Implemented endpoints have concrete schemas; future endpoints have stub schemas.

---

## Conventions

### Base URL

All endpoints prefixed with `/api/v1`.

### Response Envelope

All list endpoints return a consistent envelope:

```json
{
  "data": [...],
  "total": 1234,
  "limit": 50,
  "offset": 0
}
```

Single-resource endpoints return the object directly (no `data` wrapper).

### Error Response

All errors follow a consistent format:

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Playlist 42 not found",
    "details": null
  }
}
```

Exception-to-HTTP mapping is handled by `src/interface/api/middleware.py`:

| Exception | HTTP Status | Error Code | Notes |
|-----------|-------------|------------|-------|
| `NotFoundError` | `404` | `NOT_FOUND` | Domain exception from `src/domain/exceptions.py` |
| `TemplateReadOnlyError` | `403` | `FORBIDDEN` | Cannot edit/delete template workflows (v0.4.0) |
| `ValueError` | `400` | `VALIDATION_ERROR` | Input validation failures |
| `RequestValidationError` | `422` | (FastAPI default) | Pydantic schema violations (automatic) |
| `WorkflowAlreadyRunningError` | `409` | `CONFLICT` | Workflow is already executing (v0.4.1) |
| `ConnectorNotAvailableError` | `503` | `SERVICE_UNAVAILABLE` | Required connector not connected (v0.4.1) |
| Unhandled `Exception` | `500` | `INTERNAL_ERROR` | Generic message, details logged server-side |

Implemented status codes:

| HTTP Status | Meaning | Since |
|-------------|---------|-------|
| `413` | Payload too large (file upload exceeds 100 MB limit) | v0.3.1 |
| `429` | Too many concurrent operations (max 3 simultaneous imports) | v0.3.1 |

| `409` | Conflict (workflow already running) | v0.4.1 |
| `503` | Service unavailable (connector not connected) | v0.4.1 |

### Pagination

All list endpoints support `limit` (default 50, max 200) and `offset` pagination.

### IDs

All entity IDs are **integers** (matching the codebase -- `Track.id: int`, `Playlist.id: int`). Not UUIDs.

Operation IDs are **UUID strings** (from `ProgressOperation.operation_id`).

---

## Shared Architecture

> CLI and Web UI call the **same use cases** through the **same runner**. No business logic lives in either interface layer.

### Code Path Convergence

Every API endpoint is a thin FastAPI route handler that calls `execute_use_case()` from `application/runner.py` — the same function the CLI invokes via `run_async()`. FastAPI calls it directly (already async); the CLI bridges sync Typer to async through `run_async()`. Route handlers contain zero business logic: parse request → build Command → `execute_use_case()` → serialize Result.

```
CLI (Typer)                          Web UI (FastAPI)
    │                                      │
    ▼                                      ▼
run_async()                          async route handler
    │                                      │
    ▼                                      ▼
execute_use_case(λ uow →             execute_use_case(λ uow →
  UseCase(uow).execute(cmd))           UseCase(uow).execute(cmd))
    │                                      │
    └──────────────┬───────────────────────┘
                   ▼
         Application Layer
         (Use Cases, Services)
                   │
                   ▼
         Domain Layer (pure logic)
                   │
                   ▼
         Infrastructure Layer
         (repos, connectors)
```

### Progress Tracking

Both interfaces share the same progress infrastructure:

- **Domain protocols** (`domain/entities/progress.py`): `ProgressEmitter` and `ProgressSubscriber` define the interface
- **Application hub** (`application/services/progress_manager.py`): `AsyncProgressManager` implements `ProgressEmitter` and fans out to N subscribers
- **CLI subscriber**: `RichProgressProvider` → Rich progress bars in terminal
- **Web subscriber**: `SSEProgressProvider` (new, v0.3.1) → Server-Sent Events to browser

Same use case code emits the same `ProgressEvent` objects — each interface just renders them differently.

### What This Means for Implementation

- Route handlers are ~5-10 lines each
- No new "web-specific" business logic — if the CLI can do it, the API can do it
- Each endpoint table below shows its backing use case — that's the actual code that runs

---

## 0. Health

```
GET    /health
       → { status: "ok", version: "0.3.0" }
```
- **Use case**: None (static response)
- **Status**: ✅ Implemented (v0.3.0)

---

## 1. Tracks

The Track API object is an **assembled view** composed from:
- `Track` entity (title, artists, album, duration_ms, release_date, isrc)
- `track_mappings` (connector linkage with confidence)
- `track_likes` (per-service like status)
- `track_metrics` (collected metrics like play counts)

```
GET    /tracks
       ?q=<search>                    free-text search on title + artist + album (min 2 chars)
       ?connector=<name>              filter by connector mapping
       ?liked=<true|false>            filter by canonical liked status (any service)
       ?sort=<field_dir>              title_asc, title_desc, artist_asc, artist_desc, added_asc, added_desc, duration_asc, duration_desc
       ?limit=&offset=
       → { data: LibraryTrackSchema[], total, limit, offset }
```
- **Use case**: `ListTracksUseCase` (merged search+list — `q` param triggers search)
- **Status**: ✅ Implemented (v0.3.2)
- **Note**: Search uses `ilike` on title, album, and `cast(artists, String)` for JSON column. Sort defaults to `title_asc`.

```
GET    /tracks/{id}
       → TrackDetailSchema (assembled view with mappings, likes, play summary, playlists)
```
- **Use case**: `GetTrackDetailsUseCase` (assembles from 4 repositories in single UoW scope)
- **Status**: ✅ Implemented (v0.3.2)

```
GET    /tracks/{id}/playlists
       → PlaylistBriefSchema[] (flat array)
```
- **Use case**: `GetTrackDetailsUseCase` (reuses same use case, returns playlists subset)
- **Status**: ✅ Implemented (v0.3.2)

```
GET    /tracks/{id}/mappings
       → { data: ConnectorTrackMapping[] }
```
- **Use case**: `GetTrackConnectorMappingsUseCase`
- **Status**: Needs implementation

```
PATCH  /tracks/{id}/mappings/{mapping_id}
       body: { connector_track_id: str }
       → ConnectorTrackMapping
```
- **Use case**: Manual mapping correction (new)
- **Status**: Needs implementation

```
DELETE /tracks/{id}/mappings/{mapping_id}
       → 204
```
- **Use case**: Remove mapping (new)
- **Status**: Needs implementation

```
POST   /tracks/{id}/like
       body: { connector: str }
       → TrackLikeStatus
```
- **Use case**: `SyncLikesUseCase` (single-track variant)
- **Status**: Needs implementation

```
DELETE /tracks/{id}/like
       body: { connector: str }
       → 204
```
- **Use case**: `SyncLikesUseCase` (single-track variant)
- **Status**: Needs implementation

```
POST   /tracks/rematch
       body: { track_ids: int[], connector: str }
       → { operation_id: str }
```
- **Use case**: `MatchAndIdentifyTracksUseCase`
- **Status**: Exists (needs API wrapper)

### Track Object Schemas

Defined in `src/interface/api/schemas/tracks.py`.

```json
// LibraryTrackSchema (list view — lightweight)
{
  "id": 42,
  "title": "string",
  "artists": [{ "name": "string" }],
  "album": "string | null",
  "duration_ms": 180000,
  "isrc": "string | null",
  "connector_names": ["spotify", "lastfm"],
  "is_liked": true
}

// TrackDetailSchema (full detail view)
{
  "id": 42,
  "title": "string",
  "artists": [{ "name": "string" }],
  "album": "string | null",
  "duration_ms": 180000,
  "release_date": "YYYY-MM-DD | null",
  "isrc": "string | null",
  "connector_mappings": [
    { "connector_name": "spotify", "connector_track_id": "string" }
  ],
  "like_status": {
    "spotify": { "is_liked": true, "liked_at": "ISO8601 | null" },
    "lastfm": { "is_liked": false, "liked_at": null }
  },
  "play_summary": {
    "total_plays": 42,
    "first_played": "ISO8601 | null",
    "last_played": "ISO8601 | null"
  },
  "playlists": [
    { "id": 1, "name": "string", "description": "string | null" }
  ]
}
```

---

## 2. Playlists

```
GET    /playlists
       ?limit=&offset=
       → { data: PlaylistSummary[], total, limit, offset }
```
- **Use case**: `ListPlaylistsUseCase`
- **Status**: ✅ Implemented (v0.3.0)

```
POST   /playlists
       body: { name: str, description?: str }
       → PlaylistDetail (status 201)
```
- **Use case**: `CreateCanonicalPlaylistUseCase`
- **Status**: ✅ Implemented (v0.3.0)

```
GET    /playlists/{id}
       → PlaylistDetail (with entries)
```
- **Use case**: `ReadCanonicalPlaylistUseCase`
- **Status**: ✅ Implemented (v0.3.0)

```
PATCH  /playlists/{id}
       body: { name?: str, description?: str }
       → PlaylistDetail
```
- **Use case**: `UpdateCanonicalPlaylistUseCase`
- **Status**: ✅ Implemented (v0.3.0)
- **Note**: `null` fields are ignored (preserve existing); empty string `""` clears the field

```
DELETE /playlists/{id}
       → 204
```
- **Use case**: `DeleteCanonicalPlaylistUseCase`
- **Status**: ✅ Implemented (v0.3.0)

```
GET    /playlists/{id}/tracks
       ?limit=&offset=
       → { data: PlaylistEntry[], total, limit, offset }
```
- **Use case**: `ReadCanonicalPlaylistUseCase`
- **Status**: ✅ Implemented (v0.3.0)

```
POST   /playlists/{id}/tracks
       body: { track_ids: int[], position?: int }
       → { data: PlaylistEntry[] }
```
- **Use case**: `UpdateCanonicalPlaylistUseCase`
- **Status**: Needs API route (use case exists)

```
DELETE /playlists/{id}/tracks/{entry_id}
       → 204
```
- **Use case**: `UpdateCanonicalPlaylistUseCase`
- **Status**: Needs API route (use case exists)

```
DELETE /playlists/{id}/tracks
       body: { entry_ids: int[] }
       → 204
```
- **Use case**: `UpdateCanonicalPlaylistUseCase` (batch variant)
- **Status**: Needs implementation

```
PATCH  /playlists/{id}/tracks/reorder
       body: { entry_ids: int[] }
       → 204
```
- **Use case**: `UpdateCanonicalPlaylistUseCase`
- **Status**: Needs API route (use case exists)

```
PATCH  /playlists/{id}/tracks/move
       body: { entry_id: int, new_position: int }
       → 204
```
- **Use case**: `UpdateCanonicalPlaylistUseCase`
- **Status**: Needs implementation

### Connector Links

```
GET    /playlists/{id}/links
       → { data: PlaylistMapping[] }
```
- **Use case**: `ListPlaylistLinksUseCase`
- **Status**: ✅ Implemented (v0.4.4)

```
POST   /playlists/{id}/links
       body: { connector: str, connector_playlist_id: str, sync_direction: "narada" | "connector" | "manual" }
       → PlaylistMapping
```
- **Use case**: `CreatePlaylistLinkUseCase`
- **Status**: ✅ Implemented (v0.4.4)

```
PATCH  /playlists/{id}/links/{link_id}
       body: { sync_direction?: str }
       → PlaylistMapping
```
- **Use case**: `UpdateConnectorPlaylistUseCase`
- **Status**: Needs API route (use case exists)

```
DELETE /playlists/{id}/links/{link_id}
       → 204
```
- **Use case**: `DeletePlaylistLinkUseCase`
- **Status**: ✅ Implemented (v0.4.4)

```
POST   /playlists/{id}/links/{link_id}/sync
       body: { direction: "push" | "pull" }
       → { operation_id: str }
```
- **Use case**: `SyncPlaylistLinkUseCase`
- **Status**: ✅ Implemented (v0.4.4)

### Playlist Object Schemas

Defined in `src/interface/api/schemas/playlists.py`.

```json
// PlaylistSummary (list view)
{
  "id": 1,
  "name": "string",
  "description": "string | null",
  "track_count": 42,
  "connector_links": ["spotify"],
  "updated_at": "2026-03-01T12:00:00"
}

// PlaylistDetail (extends PlaylistSummary)
{
  "id": 1,
  "name": "string",
  "description": "string | null",
  "track_count": 42,
  "connector_links": ["spotify"],
  "updated_at": "2026-03-01T12:00:00",
  "entries": [PlaylistEntry]
}

// PlaylistEntry
{
  "position": 0,
  "track": {
    "id": 42,
    "title": "string",
    "artists": [{ "name": "string" }],
    "album": "string | null",
    "duration_ms": 180000
  },
  "added_at": "2026-03-01T12:00:00 | null"
}

// PlaylistLinkSchema (implemented v0.4.4)
{
  "id": 1,
  "connector_name": "spotify",
  "connector_playlist_id": "string",
  "connector_playlist_name": "string | null",
  "sync_direction": "push | pull",
  "sync_status": "never_synced | syncing | synced | error",
  "last_synced": "ISO8601 | null",
  "last_sync_error": "string | null",
  "last_sync_tracks_added": "int | null",
  "last_sync_tracks_removed": "int | null"
}
```

---

## 3. Workflows

### Workflow CRUD (v0.4.0)

```
GET    /workflows
       ?include_templates=true&limit=&offset=
       -> { data: WorkflowSummary[], total, limit, offset }
```
- **Use case**: `ListWorkflowsUseCase`
- **Status**: ✅ Implemented (v0.4.0)
- **Notes**: `include_templates` defaults to `true`. Templates have `is_template: true` and cannot be edited/deleted.

```
POST   /workflows
       body: { name: str, description?: str, definition: object }
       -> WorkflowDetail (201)
```
- **Use case**: `CreateWorkflowUseCase`
- **Status**: ✅ Implemented (v0.4.0)
- **Notes**: Validates definition via `validate_workflow_def()` before persisting. Returns `422` with structured `ValidationError[]` on invalid definition.

```
GET    /workflows/{id}
       -> WorkflowDetail
```
- **Use case**: `GetWorkflowUseCase`
- **Status**: ✅ Implemented (v0.4.0)

```
PATCH  /workflows/{id}
       body: { name?: str, description?: str, definition?: object }
       -> WorkflowDetail
```
- **Use case**: `UpdateWorkflowUseCase`
- **Status**: ✅ Implemented (v0.4.0)
- **Notes**: Templates (`is_template=true`) return `403 Forbidden`. Validates definition if provided.

```
DELETE /workflows/{id}
       -> 204
```
- **Use case**: `DeleteWorkflowUseCase`
- **Status**: ✅ Implemented (v0.4.0)
- **Notes**: Templates return `403 Forbidden`.

```
POST   /workflows/validate
       body: { definition: object }
       -> { valid: bool, errors: ValidationError[] }
```
- **Use case**: Validate workflow definition (structural only, no execution)
- **Status**: ✅ Implemented (v0.4.0)
- **Notes**: Checks: valid JSON, known node types, valid DAG (no cycles), required config fields present, config value types correct.

```
GET    /workflows/nodes
       -> { data: NodeTypeInfo[] }
```
- **Use case**: Node registry introspection (for editor node palette and reference)
- **Status**: ✅ Implemented (v0.4.0)
- **Notes**: Returns all registered node types with category, description, and config schema. Sourced from `list_nodes()` in `node_registry.py`.

### Workflow Execution (v0.4.1)

```
POST   /workflows/{id}/run
       -> { operation_id: str, run_id: int }
```
- **Use case**: `RunWorkflowUseCase` (delegates to `run_workflow()` in `prefect.py`)
- **Status**: ✅ Implemented (v0.4.1)
- **Notes**: Pre-flight validation checks required connectors. Returns `503` with `{ required_connectors: ["spotify"] }` if unmet. Returns `409 Conflict` if workflow is already running. Emits `node_status` SSE events during execution.

```
GET    /workflows/{id}/runs
       ?limit=&offset=
       -> { data: WorkflowRunSummary[], total, limit, offset }
```
- **Use case**: `GetWorkflowRunsUseCase`
- **Status**: ✅ Implemented (v0.4.1)

```
GET    /workflows/{id}/runs/{run_id}
       -> WorkflowRunDetail
```
- **Use case**: `GetWorkflowRunsUseCase`
- **Status**: ✅ Implemented (v0.4.1)
- **Notes**: Includes `definition_snapshot` (the exact workflow definition at execution time) and per-node execution details.

### Workflow Version & Run Output (v0.4.2)

No new endpoints — existing endpoints gain new fields for version tracking and run output persistence.

**Schema additions to existing types**:

| Schema | New Field | Type | Description |
|--------|-----------|------|-------------|
| `WorkflowSummarySchema` | `definition_version` | `int` | Current definition version counter (auto-incremented on definition changes) |
| `WorkflowRunSummarySchema` | `definition_version` | `int` | Version at execution time (enables "changed since this run" indicator) |
| `WorkflowRunDetailSchema` | `output_tracks` | `OutputTrackSchema[]` | Denormalized snapshot of final output tracks |
| `WorkflowRunNodeSchema` | `node_details` | `object \| null` | Per-node structured output (filter removal reasons, sort rankings, destination diff) |

**New types**:

```json
// OutputTrackSchema (run output)
{
  "track_id": 42,
  "title": "Midnight City",
  "artists": ["M83"],
  "metrics": {"play_count": 15, "lastfm_play_count": 23},
  "rank": 1
}

// TrackDecision (within node_details JSON)
{
  "track_id": 42,
  "title": "Midnight City",
  "artists": ["M83"],
  "decision": "kept | removed | added",
  "reason": "play_count=15 >= threshold=5",
  "metric_name": "play_count",
  "metric_value": 15.0,
  "threshold": 5.0
}
```

**`node_details` structure by node category**:

- **Filter nodes**: `{ removed: [TrackDecision], summary: "Removed 15 tracks with play_count < 5" }`
- **Sorter nodes**: `{ sort_key: str, direction: str, top_tracks: [{ track_id, title, sort_value, rank }] }`
- **Destination nodes**: `{ playlist_name: str, tracks_added: [{ track_id, title, artists }], tracks_removed: [{ track_id, title, artists, reason }] }`

### Workflow Preview (v0.4.3)

```
POST   /workflows/{id}/preview
       -> { operation_id: str } (status 202)
```
- **Use case**: `PreviewWorkflowUseCase` (dry-run: destination nodes become no-ops)
- **Status**: ✅ Implemented (v0.4.3)
- **Notes**: Enricher nodes still call external APIs for realistic output. Only destination writes are skipped. Streams SSE `node_status` + `preview_complete` events via the operation's SSE queue.

```
POST   /workflows/preview
       body: { definition: object }
       -> { operation_id: str } (status 202)
```
- **Use case**: `PreviewWorkflowUseCase` (for unsaved workflows)
- **Status**: ✅ Implemented (v0.4.3)

### Workflow SSE Events (v0.4.1)

In addition to the standard `progress` events from `GET /operations/{id}/progress`, workflow execution emits `node_status` events:

```
event: node_status
data: {
  "node_id": "filter_step",
  "node_type": "filter.by_metric",
  "status": "RUNNING",
  "input_track_count": 120
}

event: node_status
data: {
  "node_id": "filter_step",
  "node_type": "filter.by_metric",
  "status": "COMPLETED",
  "output_track_count": 42,
  "duration_ms": 1200
}
```

`node_status` fields:
- `node_id` (str): Task ID from definition (e.g., "filter_step")
- `node_type` (str): Node type key (e.g., "filter.by_metric")
- `status` (str): PENDING | RUNNING | COMPLETED | FAILED | SKIPPED
- `input_track_count` (int | null): Track count entering this node
- `output_track_count` (int | null): Track count after this node
- `duration_ms` (int | null): Execution time in milliseconds
- `error_message` (str | null): Error details for FAILED status

### Workflow Object Schemas

```json
// WorkflowSummary (list view)
{
  "id": 1,
  "name": "Current Obsessions",
  "description": "Heavy rotation tracks from the last 30 days",
  "is_template": false,
  "source_template": "current_obsessions",
  "definition_version": 3,
  "task_count": 6,
  "node_types": ["source", "enricher", "filter", "sorter", "selector", "destination"],
  "last_run": {
    "id": 3,
    "status": "COMPLETED",
    "completed_at": "2026-03-01T10:00:45Z",
    "output_track_count": 42
  },
  "created_at": "2026-02-15T09:00:00Z",
  "updated_at": "2026-03-01T10:00:00Z"
}

// WorkflowDetail (extends WorkflowSummary)
{
  ...WorkflowSummary,
  "definition": {
    "id": "current_obsessions",
    "name": "Current Obsessions",
    "version": "1.0",
    "tasks": [
      { "id": "src", "type": "source.playlist", "config": {"playlist_id": "spotify:liked"}, "upstream": [] },
      { "id": "enrich", "type": "enricher.play_history", "config": {"days_back": 30}, "upstream": ["src"] }
    ]
  }
}

// WorkflowRunSummary (list view)
{
  "id": 3,
  "status": "COMPLETED",
  "definition_version": 2,
  "started_at": "2026-03-01T10:00:00Z",
  "completed_at": "2026-03-01T10:00:45Z",
  "duration_ms": 45000,
  "output_track_count": 42,
  "error_message": null
}

// WorkflowRunDetail (extends WorkflowRunSummary)
{
  ...WorkflowRunSummary,
  "definition_snapshot": { /* full WorkflowDef JSON as it was at execution time */ },
  "output_playlist_id": 5,
  "output_tracks": [
    { "track_id": 42, "title": "Midnight City", "artists": ["M83"], "metrics": {"play_count": 15}, "rank": 1 },
    { "track_id": 87, "title": "Sprawl II", "artists": ["Arcade Fire"], "metrics": {"play_count": 12}, "rank": 2 }
  ],
  "nodes": [
    {
      "node_id": "src",
      "node_type": "source.playlist",
      "status": "COMPLETED",
      "started_at": "2026-03-01T10:00:00Z",
      "completed_at": "2026-03-01T10:00:03Z",
      "duration_ms": 3400,
      "input_track_count": null,
      "output_track_count": 120,
      "error_message": null,
      "execution_order": 1,
      "node_details": null
    },
    {
      "node_id": "filter_step",
      "node_type": "filter.by_metric",
      "status": "COMPLETED",
      "started_at": "2026-03-01T10:00:03Z",
      "completed_at": "2026-03-01T10:00:05Z",
      "duration_ms": 1200,
      "input_track_count": 120,
      "output_track_count": 42,
      "error_message": null,
      "execution_order": 3,
      "node_details": {
        "removed": [
          { "track_id": 99, "title": "Faded", "artists": ["Alan Walker"], "decision": "removed", "reason": "play_count=2 < threshold=5", "metric_value": 2.0 }
        ],
        "summary": "Removed 78 tracks with play_count < 5"
      }
    }
  ]
}

// NodeTypeInfo (for editor node palette)
{
  "type": "filter.by_metric",
  "category": "filter",
  "description": "Filter tracks by metric value range",
  "required_config": ["metric_name"],
  "optional_config": ["min_value", "max_value", "include_missing"]
}

// ValidationError
{
  "task_id": "filter_step",
  "field": "config.metric_name",
  "message": "Required config key 'metric_name' is missing"
}

// NodePreviewSummary (preview/dry-run results)
{
  "node_id": "filter_step",
  "node_type": "filter.by_metric",
  "output_track_count": 42,
  "sample_tracks": [
    { "title": "Song A", "artists": ["Artist 1"], "album": "Album X" },
    { "title": "Song B", "artists": ["Artist 2"], "album": "Album Y" }
  ]
}
```

---

## 4. Imports

```
POST   /imports/lastfm/history
       body: { mode: "recent" | "incremental" | "full", limit?: int, from_date?: str, to_date?: str }
       → { operation_id: str }
```
- **Use case**: `ImportPlayHistoryUseCase` (via `run_import()`)
- **Status**: ✅ Implemented (v0.3.1)
- **Background**: Launches as background task, returns operation_id immediately for SSE progress subscription

```
POST   /imports/spotify/history
       body: multipart/form-data (single JSON file, max 100 MB)
       → { operation_id: str }
```
- **Use case**: `ImportPlayHistoryUseCase` (file variant via `run_import()`)
- **Status**: ✅ Implemented (v0.3.1)
- **Note**: Returns `413` if file exceeds 100 MB. File is saved to temp location and cleaned up after processing.

```
POST   /imports/spotify/likes
       body: { limit?: int, max_imports?: int }
       → { operation_id: str }
```
- **Use case**: `SyncLikesUseCase` (Spotify import direction via `run_spotify_likes_import()`)
- **Status**: ✅ Implemented (v0.3.1)

```
POST   /imports/lastfm/likes
       body: { batch_size?: int, max_exports?: int }
       → { operation_id: str }
```
- **Use case**: `SyncLikesUseCase` (Last.fm export direction via `run_lastfm_likes_export()`)
- **Status**: ✅ Implemented (v0.3.1)

```
GET    /imports/lastfm/likes/preview
       → { count: int, tracks: TrackSummary[] }
```
- **Use case**: Preview export count
- **Status**: Needs implementation

```
GET    /imports/checkpoints
       → CheckpointStatus[] (flat array, no envelope)
```
- **Use case**: Checkpoint query (via `get_sync_checkpoint_status()`)
- **Status**: ✅ Implemented (v0.3.1)

### Import Object Schemas

Defined in `src/interface/api/schemas/imports.py`.

```json
// OperationStartedResponse
{
  "operation_id": "uuid-string"
}

// CheckpointStatus
{
  "service": "spotify | lastfm",
  "entity_type": "likes | plays",
  "last_sync_timestamp": "ISO8601 | null",
  "has_previous_sync": true
}
```

### Concurrency Limit

A maximum of 3 concurrent import operations are allowed (configurable via `SSEConstants.MAX_CONCURRENT_OPERATIONS`). The limit is checked against *logically active* operations — operations that have finished their use-case work but are still in the 30-second SSE grace period do not count against the limit. Exceeding the limit returns `429` with `Retry-After: 30`.

---

## 5. Stats (Dashboard)

```
GET    /stats/dashboard
       → {
           total_tracks: int,
           total_plays: int,
           total_playlists: int,
           tracks_by_connector: { "spotify": int, "lastfm": int, ... },
           total_liked: int,
           liked_by_connector: { "spotify": int, "lastfm": int, ... }
         }
```
- **Use case**: `GetDashboardStatsUseCase` (v0.3.3)
- **Status**: ✅ Implemented (v0.3.3)

```
GET    /stats/plays
       ?limit=&offset=
       ?from=<ISO8601>&to=<ISO8601>
       → { data: TrackPlay[], total, limit, offset }
```
- **Use case**: Play history query
- **Status**: Needs implementation

```
GET    /stats/top-tracks
       ?period_days=30
       ?limit=50
       → { data: [{ track: TrackSummary, play_count: int, last_played: ISO8601 }] }
```
- **Use case**: Top tracks aggregation
- **Status**: Needs implementation

---

## 6. Operations (Progress & SSE)

```
GET    /operations/{operation_id}/progress
       Accept: text/event-stream
       → SSE stream of ProgressEvent
```
- **Use case**: `OperationBoundEmitter` + `OperationRegistry` (implements `ProgressEmitter` protocol)
- **Status**: ✅ Implemented (v0.3.1)
- **SSE reconnection**: Supports `Last-Event-ID` header for reconnection
- **Grace period**: SSE queue stays alive 30 seconds after operation completes to allow clients to receive final events

```
GET    /operations
       → { data: OperationSummary[] } (active operations only)
```
- **Use case**: List currently active operations
- **Status**: ✅ Implemented (v0.3.1)

```
GET    /operations/{operation_id}
       → ProgressEvent (snapshot for polling fallback)
```
- **Use case**: Current operation state
- **Status**: Needs implementation

```
POST   /operations/{operation_id}/cancel
       → 204
```
- **Use case**: Cancel running operation
- **Status**: Needs implementation

### SSE Event Format

```
event: started
data: {"total": 5000, "description": "Importing listening history..."}

event: progress
data: {"current": 150, "total": 5000, "message": "Importing plays...", "completion_percentage": 3.0, "items_per_second": 12.5, "eta_seconds": 388}

event: complete
data: {"final_status": "completed"}

event: error
data: {"message": "Import failed: rate limited"}
```

- `event` types: `started`, `progress`, `complete`, `error`
- `started` provides total count and description for initial display
- `progress` events include optional computed metrics (percentage, throughput, ETA)
- Both `complete` and `error` events trigger query invalidation on the frontend

---

## 7. Connectors (Auth & Search)

```
GET    /connectors
       → ConnectorStatus[]
```
- **Use case**: Reads filesystem/environment state directly (no use case — connector-specific logic)
- **Status**: ✅ Implemented (v0.3.0)
- **Note**: Returns a **flat array**, not the standard `{ data: [...] }` envelope. This endpoint reads credential files and environment variables rather than querying the database, so it doesn't go through a use case. Spotify includes silent token refresh — if the cached token is expired but a refresh_token exists, the endpoint refreshes it before responding.

```
GET    /connectors/spotify/auth-url
       → { url: str }
```
- **Use case**: Generate Spotify OAuth URL
- **Status**: Needs implementation

```
GET    /auth/spotify/callback
       ?code=&state=
       → 302 redirect to /settings
```
- **Use case**: Exchange OAuth code for tokens
- **Status**: Needs implementation

```
GET    /connectors/lastfm/auth-url
       → { url: str }
```
- **Use case**: Generate Last.fm auth URL
- **Status**: Needs implementation

```
GET    /auth/lastfm/callback
       ?token=
       → 302 redirect to /settings
```
- **Use case**: Store Last.fm session key
- **Status**: Needs implementation

```
DELETE /connectors/{connector}/auth
       → 204
```
- **Use case**: Disconnect connector
- **Status**: Needs implementation

```
GET    /connectors/{connector}/search
       ?q=<title artist>
       ?limit=10
       → { data: [{ connector_track_id: str, title: str, artists: [], album: str, duration_ms: int, preview_url: str | null }] }
```
- **Use case**: Connector track search (for mapping correction)
- **Status**: Needs implementation

```
GET    /connectors/{connector}/playlists
       ?q=<search>
       ?limit=50&offset=0
       → { data: [{ connector_playlist_id: str, name: str, track_count: int, owner: str }], total, limit, offset }
```
- **Use case**: Browse/search user's playlists on a connector (for linking)
- **Status**: Needs implementation

---

### Connector Status Schema

Defined in `src/interface/api/schemas/connectors.py`.

```json
// ConnectorStatus
{
  "name": "spotify",
  "connected": true,
  "account_name": "display_name | null",
  "token_expires_at": 1709312400
}
```

Fields:
- `name`: connector identifier (`"spotify"`, `"lastfm"`, `"musicbrainz"`, `"apple"`)
- `connected`: whether valid credentials exist
- `account_name`: display name if available (Spotify: fetched via `/me`, Last.fm: from settings, others: null)
- `token_expires_at`: Unix timestamp (Spotify only, null for others)

---

## Use Case Mapping Summary

### Implemented API Routes

| Use Case | API Endpoints | Route File | Since |
|----------|--------------|------------|-------|
| `ListPlaylistsUseCase` | `GET /playlists` | `routes/playlists.py` | v0.3.0 |
| `CreateCanonicalPlaylistUseCase` | `POST /playlists` | `routes/playlists.py` | v0.3.0 |
| `ReadCanonicalPlaylistUseCase` | `GET /playlists/{id}`, `GET /playlists/{id}/tracks` | `routes/playlists.py` | v0.3.0 |
| `UpdateCanonicalPlaylistUseCase` | `PATCH /playlists/{id}` | `routes/playlists.py` | v0.3.0 |
| `DeleteCanonicalPlaylistUseCase` | `DELETE /playlists/{id}` | `routes/playlists.py` | v0.3.0 |
| *(no use case)* | `GET /connectors` | `routes/connectors.py` | v0.3.0 |
| *(no use case)* | `GET /health` | `routes/health.py` | v0.3.0 |
| `ImportPlayHistoryUseCase` | `POST /imports/lastfm/history`, `POST /imports/spotify/history` | `routes/imports.py` | v0.3.1 |
| `SyncLikesUseCase` | `POST /imports/spotify/likes`, `POST /imports/lastfm/likes` | `routes/imports.py` | v0.3.1 |
| Checkpoint query | `GET /imports/checkpoints` | `routes/imports.py` | v0.3.1 |
| SSE progress streaming | `GET /operations/{id}/progress` | `routes/operations.py` | v0.3.1 |
| Active operations list | `GET /operations` | `routes/operations.py` | v0.3.1 |
| `ListTracksUseCase` | `GET /tracks` | `routes/tracks.py` | v0.3.2 |
| `GetTrackDetailsUseCase` | `GET /tracks/{id}`, `GET /tracks/{id}/playlists` | `routes/tracks.py` | v0.3.2 |
| `RunWorkflowUseCase` | `POST /workflows/{id}/run` | `routes/workflows.py` | v0.4.1 |
| `GetWorkflowRunsUseCase` | `GET /workflows/{id}/runs`, `GET /workflows/{id}/runs/{run_id}` | `routes/workflows.py` | v0.4.1 |
| `PreviewWorkflowUseCase` | `POST /workflows/{id}/preview`, `POST /workflows/preview` | `routes/workflows.py` | v0.4.3 |
| `GetDashboardStatsUseCase` | `GET /stats/dashboard` | `routes/stats.py` | v0.3.3 |
| `ListPlaylistLinksUseCase` | `GET /playlists/{id}/links` | `routes/playlists.py` | v0.4.4 |
| `CreatePlaylistLinkUseCase` | `POST /playlists/{id}/links` | `routes/playlists.py` | v0.4.4 |
| `DeletePlaylistLinkUseCase` | `DELETE /playlists/{id}/links/{link_id}` | `routes/playlists.py` | v0.4.4 |
| `SyncPlaylistLinkUseCase` | `POST /playlists/{id}/links/{link_id}/sync` | `routes/playlists.py` | v0.4.4 |

### Use Cases With Existing Logic (Need API Routes)

| Use Case | API Endpoints | Milestone |
|----------|--------------|-----------|
| `UpdateCanonicalPlaylistUseCase` | `POST /playlists/{id}/tracks`, `DELETE .../tracks`, `PATCH .../reorder` | v0.3.2+ |
| `MatchAndIdentifyTracksUseCase` | `POST /tracks/rematch` | v0.3.2 |
| `EnrichTracksUseCase` | Internal (used by workflows) | — |

### Use Cases Needing Implementation

| Use Case | Milestone | API Endpoints |
|----------|-----------|--------------|
| Operation cancellation | v0.3.2+ | `POST /operations/{id}/cancel` |
| `GetTrackConnectorMappingsUseCase` | v0.3.2+ | `GET /tracks/{id}/mappings` |
| Connector playlist browse | v0.4.5+ | `GET /connectors/{connector}/playlists` |
| Connector OAuth flows | v0.5.0 | `/auth/*`, `/connectors/*/auth-url` |
| `GetUnmappedTracksUseCase` | v0.7.0 | `GET /tracks?unmapped_for=...` |
