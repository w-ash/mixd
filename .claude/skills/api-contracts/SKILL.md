---
name: api-contracts
description: Mixd REST API endpoint reference — all routes, request/response schemas, SSE event format, pagination, error codes, and use case mapping. Use when implementing FastAPI route handlers or frontend Tanstack Query hooks.
user-invocable: false
---

# Mixd API Contracts Reference

> Condensed from `docs/web-ui/03-api-contracts.md`. Base URL: `/api/v1`. All IDs — entities and operations — are UUID strings.

## Response Conventions

**List**: `{"data": [...], "total": int, "limit": int, "offset": int}` — pagination via `limit` (default 50, max 200) + `offset`
**Single**: object directly (no `data` wrapper)
**Error**: `{"error": {"code": "UPPER_SNAKE", "message": "Human readable", "details": {...}}}`
**Long ops**: return `{"operation_id": "uuid"}` immediately → SSE stream for progress

| HTTP | Meaning |
|------|---------|
| 400 | Bad input / validation failure |
| 404 | Not found |
| 409 | Conflict (op already running, duplicate) |
| 422 | Invalid definition / bad JSON |
| 429 | Rate limited (forwarded from connector) |
| 500 | Internal error |
| 503 | Connector unavailable |

## Endpoints

### Tracks (`/tracks`)

| Method | Path | Use Case | Status |
|--------|------|----------|--------|
| GET | `/tracks?q=&connector=&liked=&sort=&limit=&offset=` | `ListTracksUseCase` | Exists |
| GET | `/tracks/{id}` | `GetTrackDetailsUseCase` | Exists |
| GET | `/tracks/{id}/mappings` | `GetTrackConnectorMappingsUseCase` | Needs impl |
| PATCH | `/tracks/{id}/mappings/{mapping_id}` | Manual mapping correction | Exists |
| DELETE | `/tracks/{id}/mappings/{mapping_id}` | Remove mapping | Exists |
| POST | `/tracks/{id}/like` | `SyncLikesUseCase` (single-track) | Needs impl |
| DELETE | `/tracks/{id}/like` | `SyncLikesUseCase` (single-track) | Needs impl |
| POST | `/tracks/rematch` | `MatchAndIdentifyTracksUseCase` | Needs impl |

### Playlists (`/playlists`)

| Method | Path | Use Case | Status |
|--------|------|----------|--------|
| GET | `/playlists?limit=&offset=` | `ListPlaylistsUseCase` | Exists |
| POST | `/playlists` | `CreateCanonicalPlaylistUseCase` | Exists |
| GET | `/playlists/{id}` | `ReadCanonicalPlaylistUseCase` | Exists |
| PATCH | `/playlists/{id}` | `UpdateCanonicalPlaylistUseCase` | Exists |
| DELETE | `/playlists/{id}` | `DeleteCanonicalPlaylistUseCase` | Exists |
| GET | `/playlists/{id}/tracks?limit=&offset=` | `ReadCanonicalPlaylistUseCase` | Exists |
| POST | `/playlists/{id}/tracks` | `UpdateCanonicalPlaylistUseCase` | Needs impl |
| DELETE | `/playlists/{id}/tracks/{entry_id}` | `UpdateCanonicalPlaylistUseCase` | Needs impl |
| DELETE | `/playlists/{id}/tracks` (batch) | `UpdateCanonicalPlaylistUseCase` | Needs impl |
| PATCH | `/playlists/{id}/tracks/reorder` | `UpdateCanonicalPlaylistUseCase` | Needs impl |
| PATCH | `/playlists/{id}/tracks/move` | `UpdateCanonicalPlaylistUseCase` | Needs impl |

### Playlist Connector Links

| Method | Path | Use Case | Status |
|--------|------|----------|--------|
| GET | `/playlists/{id}/links` | Query connector playlist links | Exists |
| POST | `/playlists/{id}/links` | `CreatePlaylistLinkUseCase` | Exists |
| PATCH | `/playlists/{id}/links/{link_id}` | `UpdatePlaylistLinkUseCase` | Exists |
| DELETE | `/playlists/{id}/links/{link_id}` | `DeletePlaylistLinkUseCase` | Exists |
| POST | `/playlists/{id}/links/{link_id}/sync` | `SyncPlaylistLinkUseCase` | Exists |

### Workflows (`/workflows`)

| Method | Path | Use Case | Status |
|--------|------|----------|--------|
| GET | `/workflows` | List workflows | Exists |
| POST | `/workflows` | Create workflow | Exists |
| GET | `/workflows/{id}` | Get workflow | Exists |
| PATCH | `/workflows/{id}` | Update workflow | Exists |
| POST | `/workflows/{id}/run` | Execute via Prefect | Exists |
| POST | `/workflows/{id}/preview` | Dry-run (skip writes) | Exists |
| POST | `/workflows/validate` | Validate definition | Exists |
| GET | `/workflows/{id}/runs?limit=&offset=` | List runs | Exists |
| GET | `/workflows/{id}/runs/{run_id}` | Run detail | Exists |
| GET | `/workflows/active-runs` | List currently-running workflows | Exists |
| GET | `/workflows/nodes` | Node-type registry introspection | Exists |
| GET | `/workflows/templates` | List built-in workflow templates | Exists |
| POST | `/workflows/templates/{template_id}/use` | Instantiate a template as a user workflow | Exists |
| POST | `/workflows/{id}/duplicate` | Duplicate a workflow | Exists |
| POST | `/workflows/preview` | Dry-run an unsaved definition | Exists |
| GET | `/workflows/{id}/versions` | List definition versions | Exists |
| GET | `/workflows/{id}/versions/{version}` | Version detail | Exists |
| POST | `/workflows/{id}/versions/{version}/revert` | Revert to a version | Exists |

### Imports (`/imports`)

| Method | Path | Use Case | Status |
|--------|------|----------|--------|
| POST | `/imports/lastfm/history` | `ImportPlayHistoryUseCase` | Exists |
| POST | `/imports/spotify/history` (multipart) | `ImportPlayHistoryUseCase` | Exists |
| POST | `/imports/spotify/likes` | `SyncLikesUseCase` | Exists |
| POST | `/imports/lastfm/likes` | `SyncLikesUseCase` (Last.fm export) | Exists |
| GET | `/imports/lastfm/export-likes/preview` | Preview count | Needs impl |
| GET | `/imports/checkpoints` | Checkpoint query | Exists |

### Stats (`/stats`)

| Method | Path | Use Case | Status |
|--------|------|----------|--------|
| GET | `/stats/dashboard` | `GetDashboardStatsUseCase` | Exists |
| GET | `/stats/matching` | Match-method stats | Exists |
| GET | `/stats/plays?from=&to=&limit=&offset=` | Play history query | Needs impl |
| GET | `/stats/top-tracks?period_days=30&limit=50` | Top tracks aggregation | Needs impl |

### Operations (`/operations`) — Progress & SSE

| Method | Path | Use Case | Status |
|--------|------|----------|--------|
| GET | `/operations/{id}/progress` (SSE) | `SSEProgressSubscriber` | Exists |
| GET | `/operations/{id}/snapshot` | Operation snapshot | Exists |
| POST | `/operations/{id}/cancel` | Cancel operation | Needs impl |

### Connectors (`/connectors`)

| Method | Path | Use Case | Status |
|--------|------|----------|--------|
| GET | `/connectors` | Status query | Exists |
| GET | `/connectors/{service}/auth-url` | OAuth/auth URL (spotify, lastfm) | Exists |
| GET | `/auth/spotify/callback` | Spotify OAuth exchange → redirect | Exists |
| GET | `/auth/lastfm/callback` | Last.fm session key → redirect | Exists |
| DELETE | `/connectors/{connector}/token` | Disconnect | Exists |
| GET | `/connectors/{connector}/search?q=&limit=` | Track search | Needs impl |
| GET | `/connectors/{connector}/playlists?q=&limit=&offset=` | Browse playlists | Exists |

### Tags (`/tags`)

| Method | Path | Use Case | Status |
|--------|------|----------|--------|
| GET | `/tags` | List tags with track counts | Exists |
| POST | `/tags/merge` | Merge tags | Exists |
| PATCH | `/tags/{tag}` | Rename a tag | Exists |
| DELETE | `/tags/{tag}` | Delete a tag | Exists |
| POST | `/tracks/{id}/tags` | Add tags to a track | Exists |
| POST | `/tracks/tags/batch` | Batch-tag tracks | Exists |
| DELETE | `/tracks/{id}/tags/{tag}` | Remove a tag from a track | Exists |

### Schedules (`/schedules`, `/workflows/{id}/schedule`, `/sync/schedules`)

| Method | Path | Use Case | Status |
|--------|------|----------|--------|
| GET | `/schedules` | List all schedules | Exists |
| GET | `/workflows/{id}/schedule` | Get a workflow's schedule | Exists |
| PUT | `/workflows/{id}/schedule` | Create/replace a workflow schedule | Exists |
| PATCH | `/workflows/{id}/schedule` | Update a workflow schedule | Exists |
| DELETE | `/workflows/{id}/schedule` | Remove a workflow schedule | Exists |
| GET | `/sync/schedules/{target_id}` | Get a sync-target schedule | Exists |
| PUT | `/sync/schedules/{target_id}` | Create/replace a sync schedule | Exists |
| PATCH | `/sync/schedules/{target_id}` | Update a sync schedule | Exists |
| DELETE | `/sync/schedules/{target_id}` | Remove a sync schedule | Exists |

### Settings (`/settings`)

| Method | Path | Use Case | Status |
|--------|------|----------|--------|
| GET | `/settings` | Read user settings | Exists |
| PATCH | `/settings` | Update user settings | Exists |

### Reviews (`/reviews`)

| Method | Path | Use Case | Status |
|--------|------|----------|--------|
| GET | `/reviews` | List match reviews | Exists |
| POST | `/reviews/{review_id}/resolve` | Resolve a match review | Exists |

### Playlist Assignments (`/playlist-assignments`)

| Method | Path | Use Case | Status |
|--------|------|----------|--------|
| POST | `/playlist-assignments` | Create a tag→playlist assignment | Exists |
| POST | `/playlist-assignments/apply-bulk` | Apply assignments in bulk | Exists |
| POST | `/playlist-assignments/{assignment_id}/apply` | Apply one assignment | Exists |
| DELETE | `/playlist-assignments/{assignment_id}` | Delete an assignment | Exists |

### Operation Runs (`/operation-runs`)

| Method | Path | Use Case | Status |
|--------|------|----------|--------|
| GET | `/operation-runs?limit=&offset=` | List audit rows for past operations | Exists |
| GET | `/operation-runs/{run_id}` | Operation-run detail | Exists |

### Webhooks (`/webhooks`)

| Method | Path | Use Case | Status |
|--------|------|----------|--------|
| POST | `/webhooks/neon-auth` | Neon Auth user-sync webhook | Exists |

## SSE Event Format

```
id: 42
event: progress
data: {"operation_id":"uuid","status":"RUNNING","current":150,"total":5000,"message":"Importing plays...","metadata":{}}

id: 43
event: complete
data: {"operation_id":"uuid","status":"COMPLETED","current":5000,"total":5000,"message":"Import complete","result":{...}}
```

Event types: `progress`, `complete`, `error`, `cancelled`. Supports `Last-Event-ID` reconnection.

## Key Object Shapes

**Track**: `{id, title, artists: [{name}], album, duration_ms, release_date, isrc, connector_mappings: [{id, connector, connector_track_id, confidence, match_method, is_primary}], like_status: {service: {is_liked, liked_at}}, metrics: [{connector, metric_type, value, collected_at}], play_summary: {total_plays, last_played, first_played}}`

**PlaylistSummary**: `{id, name, description, track_count, connector_links: [str], updated_at}`

**PlaylistEntry**: `{id, position, track: {id, title, artists}, added_at}`

**WorkflowRun**: `{id (uuid), workflow_id, started_at, completed_at, status, result_summary, node_results: [{node_id, status, track_count, duration_ms}], error}`

## Architecture Reminder

Route handlers are 5-10 lines: parse request → build frozen Command → `execute_use_case()` → serialize Result. CLI and API call the same use cases through the same runner. Zero business logic in either interface layer.
