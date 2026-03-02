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

## Progress & SSE
- `SSEProgressProvider` implements the same `ProgressSubscriber` protocol as CLI's `RichProgressProvider`
- SSE events include `id` field for `Last-Event-ID` reconnection
- Event types: `progress`, `complete`, `error`, `cancelled`

## Shared Architecture
- API and CLI are two presentation layers over the same application core
- Same use cases, same runner, same domain logic — never create "web-specific" business logic
