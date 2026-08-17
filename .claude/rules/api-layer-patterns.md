---
paths:
  - "src/interface/api/**"
---
# API Layer Rules (FastAPI)

## Route Handler Pattern
- Route handlers are **5-10 lines**: parse request → build frozen Command → `execute_use_case()` → serialize Result
- **Zero business logic** in route handlers — delegate everything to application use cases
- Call `execute_use_case()` from `application/runner.py` directly (natively async, no `run_async()` bridge)
- Return domain Result objects serialized to JSON — never return SQLAlchemy models or raw dicts

## Response Format
- **List endpoints**: `{"data": [...], "total": int, "limit": int, "offset": int}`
- **Single resource**: object directly (no `data` wrapper)
- **Errors**: `{"error": {"code": "UPPER_SNAKE", "message": "Human readable", "details": {...}}}`
- **Long operations**: return `{"operation_id": "uuid"}` immediately, stream progress via SSE

## Preview Endpoints
- **Diff previews** (read-only): `GET /resource/{id}/sub-resource/{sub_id}/action/preview` — returns what *would* happen without doing it. No side effects; accept overrides via query params (e.g., `?direction_override=push`). Example: `GET /playlists/{id}/links/{link_id}/sync/preview` → `SyncPreviewResponse(...)`
- **Workflow preview** (`POST /workflows/{id}/preview`) is NOT read-only: sources commit canonical rows (idempotent); only destination writes are skipped. See `application-patterns.md` § Preview use cases before "fixing" this.

## Partial Updates (PATCH)
- `PATCH /resource/{id}/sub-resource/{sub_id}` — update specific fields without replacing the whole resource
- Request body contains only the fields to update (e.g., `{"sync_direction": "push"}`)
- Returns the full updated resource after mutation
- Route handler: parse body → build Command → `execute_use_case()` → return updated resource

## Progress & SSE
- `SSEProgressSubscriber` implements the same `ProgressSubscriber` protocol as CLI's `RichProgressSubscriber`
- SSE events include `id` field for `Last-Event-ID` reconnection
- Event types: `progress`, `complete`, `error`, `cancelled`
