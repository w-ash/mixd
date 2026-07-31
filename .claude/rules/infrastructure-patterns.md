---
paths:
  - "src/infrastructure/**"
---
# Infrastructure Layer Rules
- Convert SQLAlchemy models to domain entities before returning to application layer — DB models stay in infrastructure
- Import only from domain and _shared/ — infrastructure depends inward (no application or interface imports). **Named exception:** an adapter may import the application-owned *port/type* modules it implements (never use cases, never the runner) — `application.connector_protocols` (`Closeable`), and for the LLM adapter `application.chat.protocols` + `application.chat.events`. These are the interfaces the adapter satisfies, so importing them is realising a contract, not reaching outward into orchestration.
- Import domain constants from domain (e.g., `DB_PSEUDO_CONNECTOR` from `domain.entities.playlist`), not from `config.constants`
- `selectinload()` for ALL collection relationships (lazy loading 1000 tracks = 1001 queries; selectinload = 2). `joinedload()` is the exception, and only for many-to-one where it can't multiply rows — `links.py` loading `DBPlaylistMapping.connector_playlist` is the precedent.
- Relationships declare `lazy="raise_on_sql"` — an unloaded access raises instead of emitting a lazy query (which in async either hangs or throws at an unhelpful place). If a repository query starts raising, add the loader option; never relax the relationship.
- `expire_on_commit=False` in all session configs
- Batch operations: `save_batch()`, `get_by_ids()`, `delete_batch()`
- Per-task sessions from PostgreSQL pool (NOT shared session) — each task calls `_with_uow()` for a fresh session; MVCC handles concurrent reads/writes safely across parallel tasks
- API clients extend `BaseAPIClient` from `base.py` — use `_api_call("op_name", impl, *args)` for retry + context + suppress
- Retry via `tenacity` policies from `_shared/retry_policies.py` — use shared policies, not bare `@retry`
- Retry policies integrate with `ErrorClassifier` — retries "temporary"/"rate_limit", fails fast on "permanent"
- **Connectors must implement `aclose()`** delegating to their `_client.aclose()` — `DatabaseUnitOfWork.__aexit__` calls it to clean up cached httpx2 pools
- **Validate at the boundary**: every connector defines Pydantic models in `models.py` — validate raw `dict[str, Any]` → typed model at the API client. Only typed models flow downstream into conversion/matching layers.
- **Three-layer type propagation**: When a domain entity field changes type, update in order: (1) domain protocol interface, (2) infrastructure concrete implementation, (3) local variable declarations. Pyright resolves types through protocols, not implementations — updating only the concrete class doesn't fix callers.
- **Covariant at boundaries**: Function parameters accepting lists from callers should use `Sequence[T]` not `list[T]` (list is invariant, Sequence is covariant). Same for `Mapping` vs `dict` for read-only dict params.
