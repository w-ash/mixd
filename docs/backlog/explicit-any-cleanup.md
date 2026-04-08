# Explicit Any Cleanup

**Goal**: Eliminate all `reportExplicitAny` warnings by replacing lazy `Any` usage with precise types. Not just type changes — architectural improvements that make the code more DRY, compact, and type-safe.

**Progress**: 448 → 385 (63 eliminated, 14%). Domain layer complete. Application XS complete.
Completed work archived in [completed/explicit-any-cleanup-batches-1-3.md](completed/explicit-any-cleanup-batches-1-3.md).

**When suppression is legitimate**: External JSON payloads you don't control (webhooks), SQLAlchemy column expressions where stubs are genuinely incomplete, and protocol methods that must accept arbitrary types by design. Document why with a comment.

**Endgame**: Once all layers are clean, promote `reportAny` and `reportExplicitAny` from `"warning"` to `"error"` in `pyproject.toml` to prevent regression.

---

## Type Strategy

### Architecture: Covariant `JsonValue` with `Mapping`/`Sequence`

Defined in `src/domain/entities/shared.py`. Uses covariant containers (Pydantic PR#9701, pyright#2115) so `list[str]` IS `Sequence[JsonValue]` and `dict[str, int]` IS `Mapping[str, JsonValue]`:

```python
from collections.abc import Mapping, Sequence
type JsonValue = str | int | float | bool | None | Sequence[JsonValue] | Mapping[str, JsonValue]
```

### Convention

| Context | Type | Why |
|---------|------|-----|
| Entity fields (frozen, read-only) | `Mapping[str, JsonValue]` | Covariant — accepts any JSON dict |
| Mutable entity fields (e.g. OperationResult) | `dict[str, JsonValue]` | Mutable — supports `__setitem__` |
| Local construction | `dict[str, JsonValue] = {...}` | Mutable, for building data |
| Function params (accepting) | `Mapping[str, JsonValue]` | Covariant — callers can pass any JSON dict |
| Function returns (producing) | `dict[str, JsonValue]` | Concrete — callers get full dict API |
| Factory for attrs | `field(factory=empty_json_map)` | Typed factory from `shared.py` |
| Opaque kwargs | `**kwargs: object` | NOT JSON — truly opaque |

### Preferred replacements (narrowest first)

1. **`Mapping[str, JsonValue]`** — entity fields holding JSON-shaped metadata
2. **`Unpack[TypedDict]`** (PEP 692) — `**kwargs` forwarded to known functions
3. **Precise unions** — `dict[str, float | int | None]` when all value types are known
4. **`SortKey`** — `str | int | float | datetime` for sort key extractors
5. **`MetricValue`** — `int | float | datetime | None` for per-track metric values
6. **`object`** — truly opaque values (logging kwargs, DI containers)
7. **`Any`** — genuine boundaries only: attrs validators, `Coroutine[Any, Any, T]`

### When NOT to use JsonValue

- `OperationResult.to_dict()` return → `dict[str, Any]` (JSON serialization boundary)
- `TrackList.with_metadata` value param → `Any` (key-dependent, cast validates)
- `TrackListMetadata` local copies → `dict[str, object]` (TypedDict has non-JSON types)
- `progress_coordinator.py` → `dict[str, float | int | None]` (already precise)
- SQLAlchemy `ColumnElement[Any]`, `InstrumentedAttribute` → suppress per-line (third-party stubs)

---

## Pre-implementation fixes (from review)

- [x] **Add `dual_mode` tests** — Status: Completed (2026-04-08)
- [x] **Add `create_import_result` contract test** — Status: Completed (2026-04-08)
- [x] **Use `dual_mode` in `metric_transforms.py`** — Status: Completed (2026-04-08)
- [x] **Introduce covariant `JsonValue` and retrofit domain entities** — Status: Completed (2026-04-08)
    - Notes: Defined with `Sequence`/`Mapping` covariant containers. Retrofitted all domain entity metadata fields. Fixed frozen entity mutation bug in `spotify/operations.py` (now uses `attrs.evolve`). 6 cross-boundary errors remain in uncleaned S/M files (expected, resolves as those files are cleaned).

---

## Architectural Improvements (beyond file-by-file Any replacement)

These opportunities go beyond replacing types — they improve DDD boundaries, reduce duplication, and make the architecture more maintainable. Prioritize these when working on their respective files.

### High Impact: Workflow Config Chain → `JsonValue`

- [ ] **Change `WorkflowTaskDef.config` from `dict[str, Any]` to `Mapping[str, JsonValue]`**
    - Effort: L
    - What: The single biggest `Any` source. Flows into `NodeFn`, `TransformFactory`, `NodeContext.data`, all node factories, and every `cfg.get()` call. Config values ARE JSON — they're deserialized from YAML/JSON workflow definitions.
    - Why: Propagates type safety through the entire workflow pipeline. Every downstream `cfg.get()` would return `JsonValue` instead of `Any`, forcing explicit narrowing.
    - Dependencies: Need a `cfg_str()` / `cfg_int()` narrowing helper for clean access in lambdas
    - Notes: This one change could eliminate ~50 `Any` warnings across 15+ workflow files. Do this BEFORE cleaning individual workflow files.
    - Status: Not Started

### High Impact: SSE Event Typing

- [ ] **Define `type SSEEvent = dict[str, str | dict[str, object]]` and type all SSE queues**
    - Effort: S
    - What: Replace `asyncio.Queue[Any]` and `asyncio.Queue[object]` with `asyncio.Queue[SSEEvent]` across observers, SSE operations, progress, and workflow routes
    - Why: Eliminates `Any` from 12+ locations and removes `# pyright: reportAny=false` from at least `sse_operations.py` and `observers.py`
    - Status: Not Started

### High Impact: `Unpack[TypedDict]` for Protocol kwargs

- [ ] **Type `RunStatusUpdater` and `BasePlayImporter` kwargs with `Unpack[TypedDict]`** (PEP 692)
    - Effort: M
    - What: Replace `**kwargs: Any` on `RunStatusUpdater`, `import_data()`, `_fetch_data()` with per-service TypedDicts. The codebase already uses this pattern in `node_registry.py` with `_NodeRegisterKwargs`.
    - Why: Eliminates `Any` from protocol definitions and propagates to all implementations. Makes each service's expected parameters explicit.
    - Status: Not Started

### Medium Impact: Return Typed Models, Not Dicts

- [ ] **Replace `dict[UUID, dict[str, Any]]` metadata returns with typed models**
    - Effort: M
    - What: `TrackMetadataConnector.get_external_track_data()` returns `dict[UUID, dict[str, Any]]`. The infra rule already says "validate at boundary with Pydantic models." Return `dict[UUID, SpotifyTrackMetadata]` from connectors.
    - Why: Eliminates `Any` from `connector_protocols.py` (4 warnings) and `metrics_application_service.py` (7 warnings) in one structural fix.
    - Status: Not Started

### Quick Wins: Python 3.14 Cleanup

- [ ] **Remove `from __future__ import annotations`** from 5 files
    - Effort: XS
    - What: Python 3.14 ships PEP 649/749 — deferred evaluation is default. Dead import in: `spotify/utilities.py`, `spotify/cross_discovery.py`, `lastfm/inward_resolver.py`, `lastfm/client.py`, `lastfm/track_resolution_service.py`
    - Status: Not Started

- [ ] **Narrow `json.loads()` returns with `cast(dict[str, JsonValue], ...)`**
    - Effort: XS
    - What: `workflow_loader.py`, `webhooks.py` assign `json.loads()` to `dict[str, Any]`. Use `cast(dict[str, JsonValue], ...)` since the result IS JSON.
    - Status: Not Started

- [ ] **Type `CombinerFn` precisely** — `Callable[..., ...]` → `Callable[[list[TrackList]], TrackList]`
    - Effort: XS
    - What: `transform_definitions.py` line 55 uses `Callable[..., ...]` which is implicit `Any`. The combiners all take `list[TrackList]`.
    - Status: Not Started

### Future: Endgame

- [ ] **Remove all per-file `# pyright: reportAny=false` suppressions** (79 files)
    - Effort: XL (incremental — remove as each file is cleaned)
    - What: Replace blanket suppression with targeted per-line `# type: ignore[reportAny]` only where truly necessary (third-party stubs)
    - Status: Not Started

- [ ] **Promote `reportAny`/`reportExplicitAny` to `"error"`**
    - Effort: XS (after all layers clean)
    - What: Change from `"warning"` to `"error"` in `pyproject.toml` to prevent regression
    - Dependencies: All layers complete
    - Status: Not Started

---

## Application Layer — Remaining (~100 warnings)

### Workflows Epic

Order: config chain first (highest impact), then protocols (define contracts), then implementations.

- [ ] **`src/application/workflows/protocols.py`** (3 warnings)
    - Effort: XS
    - What: Replace `Any` in workflow protocol methods. Use `Unpack[TypedDict]` for `RunStatusUpdater`.
    - Dependencies: All workflow node implementations
    - Status: Not Started
    - Notes: (Review) Clean BEFORE implementations — protocols define the contract

- [ ] **`src/application/workflows/node_factories.py`** (11 warnings)
    - Effort: M
    - What: Replace `Any` in factory functions, config dicts, node constructors
    - Why: Node configs have known shapes defined by `node_config_fields.py`
    - Dependencies: Node type definitions
    - Status: Not Started

- [ ] **`src/application/workflows/prefect.py`** (9 warnings)
    - Effort: M
    - What: Replace `Any` in Prefect task wrappers, result types
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/application/workflows/observers.py`** (8 warnings)
    - Effort: S
    - What: Replace `Any` in observer callback/event types. Define `SSEEvent` type alias.
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/application/workflows/source_nodes.py`** (7 warnings)
    - Effort: S
    - What: Replace `Any` in source node output types
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/application/workflows/destination_nodes.py`** (7 warnings)
    - Effort: S
    - What: Replace `Any` in destination node input types
    - Dependencies: None
    - Status: Not Started

### Use Cases Epic

- [ ] **`src/application/use_cases/_shared/command_validators.py`** (16 warnings)
    - Effort: L
    - What: Replace `Any` in validator functions — likely `dict[str, Any]` config validation
    - Why: Validators know exactly what shapes they validate
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/application/use_cases/update_connector_playlist.py`** (10 warnings)
    - Effort: M
    - What: Replace `Any` in playlist update metadata types
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/application/use_cases/enrich_tracks.py`** (5 warnings)
    - Effort: S
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/application/use_cases/create_connector_playlist.py`** (5 warnings)
    - Effort: S
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/application/use_cases/update_canonical_playlist.py`** (4 warnings)
    - Effort: S
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/application/use_cases/_shared/playlist_results.py`** (4 warnings)
    - Effort: S
    - Dependencies: None
    - Status: Not Started

### Application Services & Utilities Epic

- [ ] **`src/application/services/metrics_application_service.py`** (7 warnings)
    - Effort: S
    - Dependencies: None
    - Status: Not Started
    - Notes: Fix with typed connector metadata models (see architectural improvement above)

- [ ] **`src/application/connector_protocols.py`** (4 warnings)
    - Effort: S
    - What: Replace `Any` in connector protocol return types
    - Why: Protocols should express the actual data shape connectors return
    - Dependencies: All connector implementations
    - Status: Not Started

---

## Infrastructure Layer (40 files, ~180 warnings)

Order: shared base classes first, then per-connector files.

### Persistence Epic

Order: `db_models.py` first (defines column types), then repositories.

- [ ] **`src/infrastructure/persistence/database/db_models.py`** (34 warnings)
    - Effort: L
    - What: Replace `Any` in SQLAlchemy mapped columns — JSONB fields, metadata columns
    - Why: Most JSONB columns store known shapes. `Mapping[str, JsonValue]` or TypedDicts are more honest.
    - Dependencies: Repository mappers that read these columns
    - Status: Not Started
    - Notes: Pre-classify before implementation. SQLAlchemy `ColumnElement[Any]` from stubs is unavoidable — suppress per-line.

- [ ] **`src/infrastructure/persistence/repositories/base_repo.py`** (26 warnings)
    - Effort: L
    - What: Replace `Any` in generic repository base class methods
    - Why: Generic methods use `Any` for flexibility but most callsites know the concrete type
    - Dependencies: All repository subclasses
    - Status: Not Started
    - Notes: Many `Any` from SQLAlchemy stubs (`InstrumentedAttribute`, `ColumnElement`) — suppress per-line. Use `ParamSpec` for repo decorator (already done).

- [ ] **`src/infrastructure/persistence/repositories/track/connector.py`** (18 warnings)
    - Effort: M
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/infrastructure/persistence/repositories/track/mapper.py`** (7 warnings)
    - Effort: S
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/infrastructure/persistence/repositories/repo_decorator.py`** (6 warnings)
    - Effort: S
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/infrastructure/persistence/repositories/track/metrics.py`** (5 warnings)
    - Effort: S
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/infrastructure/persistence/repositories/track/core.py`** (5 warnings)
    - Effort: S
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/infrastructure/persistence/repositories/user_settings.py`** (4 warnings)
    - Effort: S
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/infrastructure/persistence/repositories/track/plays.py`** (3 warnings)
    - Effort: XS
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/infrastructure/persistence/repositories/playlist/connector.py`** (1 warning)
    - Effort: XS
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/infrastructure/persistence/repositories/play/connector.py`** (1 warning)
    - Effort: XS
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/infrastructure/persistence/unit_of_work.py`** (from prior suppressions)
    - Effort: XS
    - Dependencies: None
    - Status: Not Started

### Spotify Connector Epic

Order: `client.py` first (root API client), then leaf files.

- [ ] **`src/infrastructure/connectors/spotify/client.py`** (21 warnings)
    - Effort: L
    - What: Replace `Any` in Spotify API response types — use Pydantic models or TypedDicts for known response shapes
    - Why: Spotify API responses have documented schemas; `dict[str, Any]` is lazier than necessary
    - Dependencies: None
    - Status: Not Started
    - Notes: (Review) Start here within Spotify epic — root client all other files depend on

- [ ] **`src/infrastructure/connectors/spotify/conversions.py`** (5 warnings)
    - Effort: S
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/infrastructure/connectors/spotify/connector.py`** (5 warnings)
    - Effort: S
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/infrastructure/connectors/spotify/operations.py`** (4 warnings)
    - Effort: S
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/infrastructure/connectors/spotify/play_importer.py`** (4 warnings)
    - Effort: S
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/infrastructure/connectors/spotify/play_resolver.py`** (3 warnings)
    - Effort: XS
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/infrastructure/connectors/spotify/personal_data.py`** (1 warning)
    - Effort: XS
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/infrastructure/connectors/spotify/models.py`** (1 warning)
    - Effort: XS
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/infrastructure/connectors/spotify/factory.py`** (1 warning)
    - Effort: XS
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/infrastructure/connectors/spotify/auth.py`** (1 warning)
    - Effort: XS
    - Dependencies: None
    - Status: Not Started

### Last.fm Connector Epic

- [ ] **`src/infrastructure/connectors/lastfm/play_importer.py`** (6 warnings)
    - Effort: S
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/infrastructure/connectors/lastfm/conversions.py`** (6 warnings)
    - Effort: S
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/infrastructure/connectors/lastfm/play_resolver.py`** (3 warnings)
    - Effort: XS
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/infrastructure/connectors/lastfm/models.py`** (3 warnings)
    - Effort: XS
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/infrastructure/connectors/lastfm/connector.py`** (2 warnings)
    - Effort: XS
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/infrastructure/connectors/lastfm/client.py`** (2 warnings)
    - Effort: XS
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/infrastructure/connectors/lastfm/operations.py`** (1 warning)
    - Effort: XS
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/infrastructure/connectors/lastfm/matching_provider.py`** (1 warning)
    - Effort: XS
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/infrastructure/connectors/lastfm/factory.py`** (1 warning)
    - Effort: XS
    - Dependencies: None
    - Status: Not Started

### Shared Connectors & Services Epic

Order: base classes before leaf files.

- [ ] **`src/infrastructure/connectors/_shared/rate_limited_batch_processor.py`** (9 warnings)
    - Effort: M
    - What: Generic `Any` in batch processor — use TypeVar for item/result types
    - Why: Batch processor is generic over item type; TypeVar expresses this precisely
    - Dependencies: All callers of RateLimitedBatchProcessor
    - Status: Not Started

- [ ] **`src/infrastructure/services/base_play_importer.py`** (6 warnings)
    - Effort: S
    - Dependencies: None
    - Status: Not Started
    - Notes: Use `Unpack[TypedDict]` for per-service kwargs (see architectural improvement above)

- [ ] **`src/infrastructure/connectors/base.py`** (4 warnings)
    - Effort: S
    - Dependencies: All connector subclasses
    - Status: Not Started

- [ ] **`src/infrastructure/connectors/_shared/metric_registry.py`** (3 warnings)
    - Effort: XS
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/infrastructure/connectors/musicbrainz/conversions.py`** (3 warnings)
    - Effort: XS
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/infrastructure/connectors/apple_music/error_classifier.py`** (2 warnings)
    - Effort: XS
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/infrastructure/connectors/protocols.py`** (1 warning)
    - Effort: XS
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/infrastructure/connectors/musicbrainz/connector.py`** (1 warning)
    - Effort: XS
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/infrastructure/connectors/_shared/matching_provider.py`** (1 warning)
    - Effort: XS
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/infrastructure/connectors/_shared/http_client.py`** (1 warning)
    - Effort: XS
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/infrastructure/services/track_identity_service_impl.py`** (1 warning)
    - Effort: XS
    - Dependencies: None
    - Status: Not Started

---

## Interface Layer (15 files, ~50 warnings)

### API Epic

- [ ] **`src/interface/api/routes/webhooks.py`** (6 warnings)
    - Effort: S
    - What: Webhook payloads are external JSON — evaluate if Pydantic models for known event types are worth it, or if suppression is legitimate
    - Dependencies: None
    - Status: Not Started
    - Notes: (Security review) Add shape validation for nested `event_data.get("user", {})` — malformed payload could cause unhandled 500. Use `cast(dict[str, JsonValue], ...)` for `json.loads()` results.

- [ ] **`src/interface/api/services/sse_operations.py`** (5 warnings)
    - Effort: S
    - Dependencies: None
    - Status: Not Started
    - Notes: Type `status` param as `RunStatus` (Literal), `**extra` as known types. Define `SSEEvent` type.

- [ ] **`src/interface/api/services/progress.py`** (4 warnings)
    - Effort: S
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/interface/api/schemas/workflows.py`** (4 warnings)
    - Effort: S
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/interface/api/routes/workflows.py`** (4 warnings)
    - Effort: S
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/interface/api/services/background.py`** (3 warnings)
    - Effort: XS
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/interface/api/routes/imports.py`** (2 warnings)
    - Effort: XS
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/interface/api/routes/operations.py`** (1 warning)
    - Effort: XS
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/interface/api/auth_gate.py`** (1 warning)
    - Effort: XS
    - Dependencies: None
    - Status: Not Started

### CLI Epic

- [ ] **`src/interface/cli/workflow_commands.py`** (3 warnings)
    - Effort: XS
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/interface/cli/ui.py`** (3 warnings)
    - Effort: XS
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/interface/cli/async_runner.py`** (2 warnings)
    - Effort: XS
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/interface/cli/progress_provider.py`** (1 warning)
    - Effort: XS
    - Dependencies: None
    - Status: Not Started

- [ ] **`src/interface/cli/cli_helpers.py`** (1 warning)
    - Effort: XS
    - Dependencies: None
    - Status: Not Started

---

## Config Layer (1 file, 4 warnings)

- [ ] **`src/config/settings.py`** (4 warnings)
    - Effort: S
    - What: Replace `Any` in Pydantic settings model fields
    - Dependencies: None
    - Status: Not Started
