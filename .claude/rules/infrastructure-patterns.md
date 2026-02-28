---
globs: src/infrastructure/**
---
# Infrastructure Layer Rules
- NEVER expose SQLAlchemy models to application layer — always convert to domain entities
- NEVER import from application or interface layers
- `selectinload()` for ALL relationships (lazy loading 1000 tracks = 1001 queries; selectinload = 2)
- `expire_on_commit=False` in all session configs
- Batch operations: `save_batch()`, `get_by_ids()`, `delete_batch()`
- Shared session per Prefect workflow (NOT session-per-task) — prevents SQLite "database locked"
- API clients extend `BaseAPIClient` from `base.py` — use `_api_call("op_name", impl, *args)` for retry + context + suppress
- Retry via `tenacity` policies from `_shared/retry_policies.py` — NOT bare `@retry` or `backoff` library
- Retry policies integrate with `ErrorClassifier` — retries "temporary"/"rate_limit", fails fast on "permanent"
- **Connectors must implement `aclose()`** delegating to their `_client.aclose()` — `DatabaseUnitOfWork.__aexit__` calls it to clean up cached httpx pools
- **Validate at the boundary**: every connector defines Pydantic models in `models.py` — validate raw `dict[str, Any]` → typed model at the API client, pass typed models downstream. NEVER leak raw dicts into conversion/matching layers.
