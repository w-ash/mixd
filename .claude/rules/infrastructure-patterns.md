---
paths:
  - "src/infrastructure/**"
---
# Infrastructure Layer Rules
- Convert SQLAlchemy models to domain entities before returning to application layer — DB models stay in infrastructure
- Import only from domain and _shared/ — infrastructure depends inward (no application or interface imports)
- Import domain constants from domain (e.g., `DB_PSEUDO_CONNECTOR` from `domain.entities.playlist`), not from `config.constants`
- `selectinload()` for ALL relationships (lazy loading 1000 tracks = 1001 queries; selectinload = 2)
- `expire_on_commit=False` in all session configs
- Batch operations: `save_batch()`, `get_by_ids()`, `delete_batch()`
- Shared session per Prefect workflow (NOT session-per-task) — prevents SQLite "database locked"
- API clients extend `BaseAPIClient` from `base.py` — use `_api_call("op_name", impl, *args)` for retry + context + suppress
- Retry via `tenacity` policies from `_shared/retry_policies.py` — use shared policies, not bare `@retry`
- Retry policies integrate with `ErrorClassifier` — retries "temporary"/"rate_limit", fails fast on "permanent"
- **Connectors must implement `aclose()`** delegating to their `_client.aclose()` — `DatabaseUnitOfWork.__aexit__` calls it to clean up cached httpx pools
- **Validate at the boundary**: every connector defines Pydantic models in `models.py` — validate raw `dict[str, Any]` → typed model at the API client. Only typed models flow downstream into conversion/matching layers.
