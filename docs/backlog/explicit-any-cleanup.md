# Explicit Any Cleanup

**Goal**: Eliminate all `reportExplicitAny` warnings by replacing lazy `Any` usage with precise types. Not just type changes — architectural improvements that make the code more DRY, compact, and type-safe.

**Progress**: 448 → 350 (98 eliminated, 22%). 0 errors. Domain layer complete. Application XS complete. Phase 1 complete. Cross-boundary errors resolved.
Completed work archived in [completed/explicit-any-cleanup-batches-1-3.md](completed/explicit-any-cleanup-batches-1-3.md).

**When suppression is legitimate**: External JSON payloads you don't control (webhooks), SQLAlchemy column expressions where stubs are genuinely incomplete, and protocol methods that must accept arbitrary types by design. Document why with a comment.

**Endgame**: Once all layers are clean, promote `reportAny` and `reportExplicitAny` from `"warning"` to `"error"` in `pyproject.toml` to prevent regression.

---

## Type Strategy

### Architecture: Covariant `JsonValue` with `Mapping`/`Sequence`

Defined in `src/domain/entities/shared.py`. Uses covariant containers (Pydantic PR#9701, pyright#2115) so `list[str]` IS `Sequence[JsonValue]` and `dict[str, int]` IS `Mapping[str, JsonValue]`:

```python
from collections.abc import Mapping, Sequence

type JsonValue = (
    str | int | float | bool | None | Sequence[JsonValue] | Mapping[str, JsonValue]
)
```

### Convention

| Context | Type | Why |
|---------|------|-----|
| Entity fields (frozen, read-only) | `Mapping[str, JsonValue]` | Covariant — accepts any JSON dict |
| Mutable entity fields (e.g. OperationResult) | `dict[str, JsonValue]` | Mutable — supports `__setitem__` |
| Local construction | `dict[str, JsonValue] = {...}` | Mutable, for building data |
| Function params (accepting) | `Mapping[str, JsonValue]` | Covariant — callers can pass any JSON dict |
| Function params (list of) | `Sequence[Mapping[str, ...]]` | `list` is invariant; `Sequence` is covariant |
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

### Learnings from Phase 1 (apply to all future phases)

**Three-layer propagation**: When a domain entity field changes type, update in order: (1) domain protocol interface, (2) infrastructure concrete implementation, (3) local variable declarations. Pyright resolves types through protocols, not implementations — updating only the concrete class doesn't fix callers.

**`list` is invariant, `Sequence` is covariant**: Function parameters that accept lists from callers should use `Sequence[T]` not `list[T]`. `list[tuple[UUID, Mapping[str, JsonValue]]]` is NOT assignable to `list[tuple[UUID, Mapping[str, Any]]]`, but IS assignable to `Sequence[tuple[UUID, Mapping[str, Any]]]`. Always prefer `Sequence` for read-only list params at layer boundaries.

**`Mapping[str, object]` breaks attribute assignment**: When infrastructure code does `db_model.attr = fields["key"]`, the value type matters. `object` is not assignable to typed attributes. Use `Mapping[str, Any]` at infrastructure boundaries where values flow into SQLAlchemy models — these files have `# pyright: reportAny=false` and the `Any` is intentional until the full file is cleaned.

**Config accessor overloads**: `@overload` on `cfg_int`/`cfg_float` makes `cfg_int(cfg, "key", 10)` return `int` (not `int | None`). Without overloads, callers need `or default` fallbacks. Apply this pattern to any accessor where a non-None default guarantees a non-None return.

**`bool` is `int` in Python**: `isinstance(True, int)` is `True`. Config accessors for `int`/`float` must guard against `bool` first, or `cfg_int(cfg, "count")` would accept `True` and return `1`. Guard order: `if isinstance(val, bool): return default` before the `isinstance(val, int)` check.

**Don't silently weaken required fields**: Changing `t["id"]` (raises `KeyError`) to `t.get("id", "")` (silently defaults) is a behavioral regression. Preserve fail-fast semantics for required fields when changing container types.

---

## Phases

Work in dependency order: architectural multipliers first, then layer-by-layer top-down.

### Phase 1: Architectural Force Multipliers + Quick Wins

Do these first — they propagate type safety downstream and prevent rework in later phases.

- [x] **Workflow Config Chain** → `WorkflowTaskDef.config` to `Mapping[str, JsonValue]`, config accessors, ~15 downstream files — Status: Completed (2026-04-08)
- [x] **SSE Event Typing** → typed sentinel, `asyncio.Queue[object]` across observers/SSE/progress/routes — Status: Completed (2026-04-08)
- [x] **`Unpack[TypedDict]` for `RunStatusUpdater`** → `RunStatusKwargs` TypedDict — Status: Completed (2026-04-08)
- [x] **Connector return types** → `dict[UUID, Mapping[str, JsonValue]]` in protocol + impls — Status: Completed (2026-04-08)
- [x] **Quick wins**: removed `from __future__ import annotations` (5 files), narrowed `json.loads()` (1 file), typed `CombinerFn` as Protocol — Status: Completed (2026-04-08)
    - Notes: webhooks.py json.loads deferred to Phase 5 (handlers still use `dict[str, Any]`). BasePlayImporter Unpack deferred to Phase 4a (M-effort, touches abstract methods + all impls).

### Phase 2: Application Layer (remaining warnings — counts reduced by Phase 1)

Protocols first (contracts), then implementations. Warning counts below are from the original audit — many are now resolved. Re-count before starting each file.

- [ ] **2a — Workflow Protocols + Implementations**: `protocols.py` → `observers.py` → `source_nodes.py` + `destination_nodes.py` → `node_factories.py` → `prefect.py`
- [ ] **2b — Use Cases**: `command_validators.py` (16) → `update_connector_playlist.py` (10) → `enrich_tracks.py` (5) + `create_connector_playlist.py` (5) → `update_canonical_playlist.py` (4) + `playlist_results.py` (4)
- [ ] **2c — Application Services**: `metrics_application_service.py` (7) + `connector_protocols.py` (4)

### Phase 3: Infrastructure — Persistence (~110 warnings)

Schema models first, then base repo, then leaf repos.

- [ ] **3a — Core**: `db_models.py` (34) → `base_repo.py` (26) → `repo_decorator.py` (6)
- [ ] **3b — Repository Leaf Files**: `track/connector.py` (18) → `track/mapper.py` (7) + `track/metrics.py` (5) + `track/core.py` (5) → `track/plays.py` (3) + `user_settings.py` (4) → `playlist/connector.py` (1) + `play/connector.py` (1) + `unit_of_work.py`

### Phase 4: Infrastructure — Connectors (~70 warnings)

Shared bases first, then per-service from root client outward.

- [ ] **4a — Shared**: `rate_limited_batch_processor.py` (9) → `base_play_importer.py` (6) → `base.py` (4) → `metric_registry.py` (3) + `protocols.py` (1) + `matching_provider.py` (1) + `http_client.py` (1)
- [ ] **4b — Spotify**: `client.py` (21) → `conversions.py` (5) + `connector.py` (5) + `operations.py` (4) → `play_importer.py` (4) + `play_resolver.py` (3) → `personal_data.py` + `models.py` + `factory.py` + `auth.py` (1 each)
- [ ] **4c — Last.fm + MusicBrainz**: `lastfm/play_importer.py` (6) + `lastfm/conversions.py` (6) → `lastfm/play_resolver.py` (3) + `lastfm/models.py` (3) → remaining Last.fm small files → `musicbrainz/conversions.py` (3) + `musicbrainz/connector.py` (1) → `apple_music/error_classifier.py` (2) + `track_identity_service_impl.py` (1)

### Phase 5: Interface Layer + Config (~54 warnings)

- [ ] **5a — API**: `webhooks.py` (6) → `sse_operations.py` (5) + `progress.py` (4) → `schemas/workflows.py` (4) + `routes/workflows.py` (4) → `background.py` (3) + `imports.py` (2) + `operations.py` (1) + `auth_gate.py` (1)
- [ ] **5b — CLI**: `workflow_commands.py` (3) + `ui.py` (3) + `async_runner.py` (2) → `progress_provider.py` (1) + `cli_helpers.py` (1)
- [ ] **5c — Config**: `settings.py` (4)

### Phase 6: Endgame

- [ ] Remove all per-file `# pyright: reportAny=false` suppressions (79 files, incremental as each file is cleaned)
- [ ] Promote `reportAny` + `reportExplicitAny` to `"error"` in `pyproject.toml`

---

## Pre-implementation fixes (from review)

- [x] **Add `dual_mode` tests** — Status: Completed (2026-04-08)
- [x] **Add `create_import_result` contract test** — Status: Completed (2026-04-08)
- [x] **Use `dual_mode` in `metric_transforms.py`** — Status: Completed (2026-04-08)
- [x] **Introduce covariant `JsonValue` and retrofit domain entities** — Status: Completed (2026-04-08)
    - Notes: Defined with `Sequence`/`Mapping` covariant containers. Retrofitted all domain entity metadata fields. Fixed frozen entity mutation bug in `spotify/operations.py` (now uses `attrs.evolve`). Cross-boundary errors resolved — protocol interfaces and repo implementations updated to accept `Sequence[Mapping[str, Any]]` at boundaries.

---

## Architectural Improvements (Phase 1 details)

These opportunities go beyond replacing types — they improve DDD boundaries, reduce duplication, and make the architecture more maintainable. Prioritize these when working on their respective files.

### High Impact: Workflow Config Chain → `JsonValue`

- [x] **Change `WorkflowTaskDef.config` from `dict[str, Any]` to `Mapping[str, JsonValue]`** — Status: Completed (2026-04-08)
    - Notes: Created `config_accessors.py` with `cfg_str/int/float/bool/str_list`. Updated entity, parse functions, NodeFn, TransformFactory, all node factories, transform lambdas, template_utils, validation, prefect. `NodeContext.data` stays `dict[str, Any]` (Prefect context).

### High Impact: SSE Event Typing

- [x] **Type all SSE queues as `asyncio.Queue[object]` with typed sentinel** — Status: Completed (2026-04-08)
    - Notes: Used `_SSESentinel` class + `asyncio.Queue[object]` (avoids cross-layer TypedDict). Removed `# pyright: reportAny=false` from `sse_operations.py` and `operations.py`. `observers.py` suppression remains (other `dict[str, Any]` usages).

### High Impact: `Unpack[TypedDict]` for Protocol kwargs

- [x] **Type `RunStatusUpdater` kwargs with `Unpack[RunStatusKwargs]`** — Status: Completed (2026-04-08)
    - Notes: `BasePlayImporter` deferred to Phase 4a — already has TypedDicts defined but Unpack conversion touches abstract methods + all implementations.

### Medium Impact: Return Typed Models, Not Dicts

- [x] **Change connector return to `dict[UUID, Mapping[str, JsonValue]]`** — Status: Completed (2026-04-08)
    - Notes: Simpler than typed models — both `.model_dump()` and attrs-to-dict produce JSON-compatible dicts. Spotify impl uses `cast()`, Last.fm works directly. Consumer `_extract_metrics_from_metadata` now narrows with `isinstance` before `float()` — catches non-numeric values that previously passed silently.

### Quick Wins: Python 3.14 Cleanup

- [x] **Remove `from __future__ import annotations`** from 5 files — Status: Completed (2026-04-08)

- [x] **Narrow `json.loads()` with cast** — `workflow_loader.py` done. `webhooks.py` deferred to Phase 5 (handlers still use `dict[str, Any]`). Status: Completed (2026-04-08)

- [x] **Type `CombinerFn` as Protocol** — captures `tracklists: list[TrackList]` + shared kwargs. Status: Completed (2026-04-08)

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
