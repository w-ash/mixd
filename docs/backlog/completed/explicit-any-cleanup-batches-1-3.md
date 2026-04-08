# Explicit Any Cleanup — Completed Batches 1-3 + Pre-implementation Fixes

Completed 2026-04-08. Reduced `reportExplicitAny` from 448 → 385 (63 eliminated).

## Batch 1: Domain Layer XS Files (7 files, 11 warnings)

- [x] **`src/domain/entities/shared.py`** — Added `SortKey`, `JsonValue` (covariant: `Sequence`/`Mapping`), `empty_json_map` factory
- [x] **`src/domain/transforms/core.py`** (2) — Replaced `optional_tracklist_transform` decorator with explicit `dual_mode` helper; updated all consumers in filtering.py, selecting.py, sorting.py
- [x] **`src/domain/transforms/sorting.py`** (1) — `Callable[[Track], SortKey]`
- [x] **`src/domain/results.py`** (1) — `list[ConnectorTrackPlay]`, fixed latent type mismatch
- [x] **`src/domain/matching/protocols.py`** (1) — `**additional_options: object`
- [x] **`src/domain/matching/types.py`** (2) — `service_data: Mapping[str, JsonValue]`
- [x] **`src/domain/services/progress_coordinator.py`** (2) — `dict[str, float | int | None]`
- [x] **`src/domain/playlist/execution_strategies.py`** (2) — `Mapping[str, JsonValue]` for field, `dict[str, JsonValue]` for locals/returns

## Batch 2: Domain Layer S/M Files (7 files, 29 warnings)

- [x] **`src/domain/entities/operations.py`** (10) — `Mapping[str, JsonValue]` on frozen fields, `dict[str, JsonValue]` on mutable `OperationResult.metadata`, kept `Attribute[Any]` (attrs stubs) and `to_dict()` (JSON boundary)
- [x] **`src/domain/entities/workflow.py`** (6) — 2 fixed, 4 kept (node config + JSON parsing boundary)
- [x] **`src/domain/entities/track.py`** (6) — `Mapping[str, JsonValue]` for connector_metadata/raw_metadata/metadata, 1 kept (`value: Any` in `with_metadata`)
- [x] **`src/domain/entities/progress.py`** (5) — `Mapping[str, JsonValue]` for metadata, `**kwargs: JsonValue` for factories
- [x] **`src/domain/entities/playlist.py`** (4) — `Mapping[str, JsonValue]` for extras/metadata/raw_metadata
- [x] **`src/domain/matching/play_dedup.py`** (4) — `Mapping[str, JsonValue]` for field, `dict[str, JsonValue]` for locals
- [x] **`src/domain/repositories/interfaces.py`** (6) — 2 fixed, 4 kept (keyset pagination, service metadata, pass-through kwargs)

## Batch 3: Application Layer XS Files (25 files, 23 warnings)

### Use Cases (10 files)
- [x] **`create_canonical_playlist.py`** (2) — `Mapping[str, JsonValue]` for command metadata
- [x] **`metadata_builder.py`** (2), **`workflow_runs.py`** (2), **`match_and_identify_tracks.py`** (2), **`workflow_preview.py`** (1), **`read_canonical_playlist.py`** (1), **`import_play_history.py`** (1), **`get_played_tracks.py`** (1), **`get_liked_tracks.py`** (1), **`delete_canonical_playlist.py`** (1) — `dict[str, object]` or `Queue[object]`

### Metric Transforms (2 files)
- [x] **`metric_transforms.py`** (3) — `MetricValue` for metric dicts, `SortKey` for sort key fn, proper None/type narrowing, `dual_mode` helper
- [x] **`metric_routing.py`** (3) — `SortKey` for extractors, `isinstance` narrowing

### Services & Utilities (4 files)
- [x] **`enhanced_database_batch_processor.py`** (1) — `**kwargs: JsonValue`
- [x] **`batch_results.py`** (1), **`play_import_orchestrator.py`** (1), **`batch_file_import_service.py`** (1) — `**kwargs: object`

### Workflow XS (8 files — kept as `Any`)
- [x] **`enricher_nodes.py`**, **`context.py`**, **`node_context.py`**, **`node_registry.py`**, **`template_utils.py`**, **`validation.py`**, **`workflow_loader.py`**, **`transform_definitions.py`** — All kept as `Any` (node config/context chain, DI container, JSON boundary)

### Runner (1 file — kept as `Any`)
- [x] **`runner.py`** (2) — `Coroutine[Any, Any, T]` is standard async protocol

## Pre-implementation Fixes (from review)

- [x] **`dual_mode` tests** — `test_transforms_core.py` now covers None vs non-None branching
- [x] **`create_import_result` contract test** — `test_results.py` verifies empty tracks on import result
- [x] **`dual_mode` DRY in `metric_transforms.py`** — 4 inline patterns replaced with `dual_mode` calls
- [x] **Covariant `JsonValue` architecture** — `Sequence`/`Mapping` solve dict invariance (Pydantic PR#9701 pattern). Retrofitted all domain entity metadata fields. Fixed frozen mutation bug in `spotify/operations.py` (now uses `attrs.evolve`).

## Infrastructure Downstream Fixes (from domain type changes)

- [x] **`spotify/matching_provider.py`** — `service_data: dict[str, JsonValue]` local
- [x] **`lastfm/matching_provider.py`** — `service_data: dict[str, JsonValue]` local
- [x] **`musicbrainz/matching_provider.py`** — `service_data: dict[str, JsonValue]` local
- [x] **`musicbrainz/conversions.py`** — `dict[str, JsonValue]` return type and local
- [x] **`spotify/play_resolver.py`** — `isinstance(track_uri, str)` narrowing
- [x] **`spotify/operations.py`** — `attrs.evolve` instead of mutating frozen `raw_metadata`
- [x] **`interface/api/services/progress.py`** — `isinstance(parent_id, str)` narrowing
- [x] **`interface/cli/progress_provider.py`** — `isinstance(eta, (int, float))` narrowing

## Patterns Established

| Pattern | When to Use |
|---|---|
| `Mapping[str, JsonValue]` | Frozen entity fields holding JSON-shaped data |
| `dict[str, JsonValue]` | Mutable fields, local construction, function returns |
| `dict[str, float \| int \| None]` | When all possible value types are known |
| `SortKey` alias | Sort key extractors (`Callable[[Track], SortKey]`) |
| `MetricValue` alias | Per-track metric values in dict lookups |
| `dual_mode(transform, tracklist)` | Transform dual-mode (immediate vs deferred) |
| `**kwargs: object` | Extensibility kwargs treated opaquely |
| `**kwargs: JsonValue` | Extensibility kwargs that are JSON-shaped |
| `isinstance()` narrowing | Downstream consumers reading from `Mapping`/`object` values |
| `attrs.evolve()` | Updating frozen entity fields (not mutation) |
| `empty_json_map` factory | `field(factory=empty_json_map)` for `Mapping[str, JsonValue]` attrs fields |
