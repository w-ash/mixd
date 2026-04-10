# Explicit Any Cleanup

**Goal**: Eliminate all `reportExplicitAny` warnings by replacing lazy `Any` usage with precise types. Not just type changes — architectural improvements that make the code more DRY, compact, and type-safe.

**Progress**: 448 → 158 (290 eliminated, 65%). 0 errors. Domain layer complete. Application layer complete (except 1 per-line suppression for Prefect boundary). Persistence layer complete (zero warnings in `src/infrastructure/persistence/`, all file-level suppressions removed). Phase 1, 2a, 2b, 2c, 3a, 3b, 3c, 3d all complete.
Completed work archived in [completed/explicit-any-cleanup-batches-1-3.md](completed/explicit-any-cleanup-batches-1-3.md).

**When suppression is legitimate**: External JSON payloads you don't control (webhooks), SQLAlchemy column expressions where stubs are genuinely incomplete, protocol methods that must accept arbitrary types by design, and attrs validators (which receive `object` by the attrs calling convention). Document why with a comment.

**Endgame (two-step)**: (1) Promote `reportExplicitAny` to `"error"` — stops new `Any` annotations from being written. (2) Promote `reportAny` to `"error"` with `allowedUntypedLibraries` whitelist for third-party leakers (Prefect, etc.). `reportAny` is broader — it catches implicit `Any` flowing in from untyped library return types, not just `Any` you wrote. Promoting both simultaneously would create a wave of third-party noise.

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
| Opaque kwargs (consumed locally) | `**kwargs: object` | Pass-through only — logging, DI. **Cannot** forward to typed functions (`object` is not assignable to narrower types) |
| Typed kwargs (forwarded) | `**kwargs: Unpack[TypedDict]` | PEP 692 — preserves type safety through call chains. Use `Required`/`NotRequired` for optional kwargs |
| Config int/float accessors | Guard `bool` before `int` | `isinstance(True, int)` is `True` — check `bool` first or `cfg_int(cfg, "count")` returns `1` for `True` |

### Preferred replacements (narrowest first)

1. **`Mapping[str, JsonValue]`** — entity fields holding JSON-shaped metadata
2. **`Unpack[TypedDict]`** (PEP 692) — `**kwargs` forwarded to known functions
3. **`ReadOnly[TypedDict]`** (PEP 705, Python 3.14) — config dicts where mutation should be prevented at the type level
4. **Precise unions** — `dict[str, float | int | None]` when all value types are known
5. **`SortKey`** — `str | int | float | datetime` for sort key extractors
6. **`MetricValue`** — `int | float | datetime | None` for per-track metric values
7. **`@overload`** — methods where return/value type depends on a `Literal` key argument (e.g., `with_metadata`)
8. **`object`** — truly opaque values consumed locally only (logging, DI). Not for forwarding — `object` is not assignable to narrower types downstream
9. **`Any`** — genuine boundaries only: attrs validators, `Coroutine[Any, Any, T]`

### When NOT to use JsonValue

- `OperationResult.to_dict()` return → `dict[str, Any]` — justified: output contains `MetricValue` (includes `datetime`) and UUID keys that are not in `JsonValue`. This is a true serialization boundary where FastAPI handles final JSON coercion.
- `TrackList.with_metadata` value param → currently `Any`, **target: `@overload` per `MetadataKey`**. The 7 keys each have a known type in `TrackListMetadata` (e.g., `"metrics"` → `dict[str, dict[UUID, MetricValue]]`). Using `@overload` eliminates `Any` while preserving key-dependent typing. Schedule in Phase 2b.
- `TrackListMetadata` local copies → `dict[str, object]` (TypedDict has non-JSON types: UUID, datetime)
- `progress_coordinator.py` → `dict[str, float | int | None]` (already precise)
- SQLAlchemy `ColumnElement[Any]`, `InstrumentedAttribute` → suppress per-line (third-party stubs)

### Learnings from Phase 1 (apply to all future phases)

**Three-layer propagation**: When a domain entity field changes type, update in order: (1) domain protocol interface, (2) infrastructure concrete implementation, (3) local variable declarations. Pyright resolves types through protocols, not implementations — updating only the concrete class doesn't fix callers.

**`list` is invariant, `Sequence` is covariant**: Function parameters that accept lists from callers should use `Sequence[T]` not `list[T]`. `list[tuple[UUID, Mapping[str, JsonValue]]]` is NOT assignable to `list[tuple[UUID, Mapping[str, Any]]]`, but IS assignable to `Sequence[tuple[UUID, Mapping[str, Any]]]`. Always prefer `Sequence` for read-only list params at layer boundaries.

**`Mapping[str, object]` breaks attribute assignment**: When infrastructure code does `db_model.attr = fields["key"]`, the value type matters. `object` is not assignable to typed attributes. Use `Mapping[str, Any]` at infrastructure boundaries where values flow into SQLAlchemy models — these files have `# pyright: reportAny=false` and the `Any` is intentional until the full file is cleaned.

**Config accessor overloads**: `@overload` on `cfg_int`/`cfg_float` makes `cfg_int(cfg, "key", 10)` return `int` (not `int | None`). Without overloads, callers need `or default` fallbacks. Apply this pattern to any accessor where a non-None default guarantees a non-None return.

**`bool` is `int` in Python**: `isinstance(True, int)` is `True`. Config accessors for `int`/`float` must guard against `bool` first, or `cfg_int(cfg, "count")` would accept `True` and return `1`. Guard order: `if isinstance(val, bool): return default` before the `isinstance(val, int)` check.

**Don't silently weaken required fields**: Changing `t["id"]` (raises `KeyError`) to `t.get("id", "")` (silently defaults) is a behavioral regression. Preserve fail-fast semantics for required fields when changing container types. Applies especially to mapper/conversion files in Phases 3b, 4b, 4c.

### Learnings from 2026 Best Practices (apply to all phases)

**Enable `strictGenericNarrowing`**: Add to `pyproject.toml` basedpyright config. When pyright can't infer a TypeVar, it preserves the bound/constraint instead of collapsing to `Any`. This is a significant source of implicit `Any` leakage in generic code (`base_repo.py`, `rate_limited_batch_processor.py`). Free wins.

**`reportExplicitAny` ≠ `reportAny`**: `reportExplicitAny` flags `Any` you wrote (`x: Any`). `reportAny` also flags `Any` flowing in from untyped third-party libraries (e.g., `prefect` task returns, `httpx` internals). Promote separately — `reportExplicitAny` first (stops the source), `reportAny` later (requires `allowedUntypedLibraries` whitelist).

**SQLAlchemy JSONB columns accept `JsonValue` directly**: `Mapped[dict[str, JsonValue]]` with `mapped_column(JSONB)` is valid in SQLAlchemy 2.x and eliminates `Any` from ORM models. Use `type_annotation_map` on `DeclarativeBase` for project-wide consistency. Non-JSONB `Mapped[]` columns are already well-typed by SQLAlchemy stubs — don't over-apply `JsonValue` to those.

**`ReadOnly[TypedDict]` (PEP 705)**: Available in Python 3.14. Useful for workflow config dicts where fields should not be mutated — communicates immutability at the type level, complementing `Mapping` for dict-shaped data.

**Webhook payload typing — discriminated unions**: Use Pydantic v2 discriminated union with `TypeAdapter` for typed event dispatch. Two-layer pattern: inner discriminated union for known event types (O(1) lookup by `event_type` field), outer left-to-right fallback for unknown events. Replaces unsafe `.get()` chaining.

**`object` cannot be forwarded**: `object` is the top type — it is NOT assignable where a narrower type is expected. Use `object` only for values consumed locally (logging, DI). For kwargs forwarded to typed functions, `Unpack[TypedDict]` (PEP 692) is required. `object` and `Unpack` are not interchangeable.

### Learnings from Phase 2b/c (apply to all future phases)

**Watch for reinvented frameworks**: When a "utility" file has lots of `Any` and fights the type system, check whether the underlying framework already provides what you need. `command_validators.py` had 16 `Any` and unsolvable invariance problems because it was wrapping `attrs.validators.{ge, le, in_, optional, and_}` with custom factories. Deleting the wrapper and using built-ins directly eliminated all `Any` and 150 lines of code. This pattern will recur — *suspicion of `Any` should drive architectural review, not just type annotation*.

**`attrs.validators.Attribute[T]` is invariant**: Custom validator factories like `def in_choices[T](choices) -> Callable[[object, Attribute[T], T], None]` cannot be assigned to fields with `Literal` subtypes (e.g. `sort_by: PlaySortBy | None` where `PlaySortBy = Literal[...]`). Pyright's bidirectional inference can't bridge `Attribute[str]` and `Attribute[PlaySortBy]`. **Always use built-in `attrs.validators` for primitive constraints** — they handle this correctly via `_ValidatorType` signature `Callable[[Any, Attribute[T], T], Any]` which has `Any` in the right places.

**`dict` invariance bites at every layer boundary**: `dict[UUID, float]` is NOT assignable to `dict[UUID, MetricValue]` even though `float ⊂ MetricValue`. Same for `dict[str, str | bool]` → `dict[str, JsonValue]`. Three options: (1) cast at the boundary, (2) build with explicit annotation, (3) use `Mapping` (covariant) where read-only suffices. For TypedDict spreading (`{**typed_dict_a, **typed_dict_b}`) the result widens unpredictably — always type-annotate the merged result.

**TypedDict.get() is well-typed**: `tracklist.metadata.get("metrics", {})` returns the field's declared type, no cast needed. basedpyright flags unnecessary casts as errors. Don't pre-emptively cast TypedDict field access.

**`@overload` per `Literal` key for heterogeneous TypedDict setters**: When a method takes a key + value where the value type depends on the key, write one `@overload` per `Literal` key option. Eliminates the `value: Any` parameter entirely. Cost: N overload stubs, but they're trivial. Benefit: callers get full type checking on the value side.

**Find dead code while you're there**: `tracklist_or_connector_playlist` (validator), `get_playlist_metadata` (getattr fallback), `syncconnector__playlist` (typo'd import) — all dead. Vulture-whitelisted code is a smell; if `# noqa` or whitelist entries are needed to keep code alive, that's a strong signal it can be deleted.

### Learnings from Phase 2a (apply to all future phases)

**`dict[str, object]` for heterogeneous dicts with dynamic keys**: When a dict has fixed known keys AND dynamic keys (e.g., Prefect context where task IDs become keys), TypedDict doesn't work. `dict[str, object]` is the honest top type — pair it with a typed extraction layer (e.g., `NodeContext`) that centralizes isinstance/cast narrowing. All construction sites work because any type is assignable to `object`.

**Prefect stubs are ~23% type-complete**: `Flow[P, R]` has invariant `R`, so async functions get `R = Coroutine[Any, Any, T]` not `R = T`. The `@flow` and `@task` decorators leak implicit `Any` via `reportAny`. Keep file-level `# pyright: reportAny=false` on Prefect orchestration files until Prefect improves stubs. Per-line `# pyright: ignore[reportExplicitAny]` for `build_flow() -> Any`.

**`TypedDict.get()` preserves field types**: `tracklist.metadata.get("metrics", {})` returns `dict[str, dict[UUID, MetricValue]]` — no cast needed. basedpyright flags unnecessary casts as errors (`reportUnnecessaryCast`). Don't pre-emptively cast TypedDict field access.

**Removing file-level suppressions can cascade**: Removing `# pyright: reportAny=false` from a file may surface `reportAny` warnings (implicit Any from third-party code) even when all `reportExplicitAny` is resolved. Keep the file-level suppression if the file uses Prefect decorators.

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

**Bridging note**: Tightening application protocol signatures in Phase 2a before infrastructure implementations (Phase 3) may create a window of type errors. Existing file-level `# pyright: reportAny=false` suppressions on infrastructure files bridge this gap — the build stays green between phases.

- [x] **2a — Workflow Protocols + Implementations**: `protocols.py` → `observers.py` → `source_nodes.py` + `destination_nodes.py` → `node_factories.py` → `prefect.py` — Status: Completed (2026-04-08)
- [x] **2b — Use Cases + Domain overloads** (45 warnings → 0) — Status: Completed (2026-04-09)
    - Effort: L
    - What: `playlist_results.py` (4) — `dict[str, object]` for build_playlist_changes; `command_validators.py` — **architectural rewrite**, deleted 4 reinvented validators (`positive_int_in_range`, `optional_positive_int`, `optional_in_choices`, `tracklist_or_connector_playlist`) and replaced call sites with `attrs.validators` built-ins (`and_`, `instance_of`, `ge`, `le`, `gt`, `in_`, `optional`); `enrich_tracks.py` (5) — `MetricValue` for metric dicts; `create_connector_playlist.py` (5) — `dict[str, JsonValue]` for API metadata + deleted dead `get_playlist_metadata` getattr; `update_connector_playlist.py` (10) — same pattern; `update_canonical_playlist.py` (4) — `dict[str, object]` for log summaries; `track.py` — 7 `@overload` signatures for `with_metadata` per `MetadataKey` (eliminates domain-layer `Any` completely).
    - Notes: The `command_validators.py` rewrite was the surprise — the original file (220 lines, 16 `Any`) was reinventing what attrs ships built-in. attrs `Attribute[T]` is invariant in `T`, which made our generic factory functions impossible to type for `Literal`-typed fields. Switching to `attrs.validators.and_(instance_of, ge, le)` etc. eliminated the typing problem, deleted ~150 lines, and is more idiomatic. Also fixed the `PlaylistMetadataBuilder` chain (`metadata_builder.py`) and `classify_*_error` helpers (`playlist_validator.py`) to use `dict[str, JsonValue]` directly instead of inferring through TypedDicts.
- [x] **2c — Application Services** (6 warnings → 0) — Status: Completed (2026-04-09)
    - Effort: S
    - What: `metrics_application_service.py` (3) — `dict[str, dict[UUID, MetricValue]]` (with one cast at the repo boundary); `connector_protocols.py` (3) — `dict[str, JsonValue]` for protocol returns and `Mapping[str, JsonValue]` for `convert_track_to_connector` param.

### Phase 3: Infrastructure — Persistence (~110 warnings)

Schema models first, then base repo, then leaf repos.

**Pre-implementation (before 3a)**:
- [x] **Enable `strictGenericNarrowing`** in `pyproject.toml` — Status: Completed (2026-04-09)
- [x] **Add `base_repo.py` regression tests** — 14 tests covering `_normalize_to_list`, `safe_fetch_relationship`, `has_session_support` TypeIs guard, `find_by` dict/list condition forms — Status: Completed (2026-04-09)
- [x] **Add JSONB round-trip integration test** — 13 tests covering bool/None/nested/mixed/empty preservation — Status: Completed (2026-04-09)
- [x] **Design `base_repo.py` generic type parameters** — `Mapping[str, object]` for condition/update params, `ORMOption` for relationship options, `Sequence[str | ORMOption]` for `get_default_relationships` protocol — Status: Completed (2026-04-09)
- [x] **Add `type_annotation_map`** to `DeclarativeBase` — `{JsonDict: PgJsonb}` for project-wide JSONB→JsonValue mapping. Added `JsonDict` alias to domain layer — Status: Completed (2026-04-09)

**SQLAlchemy guidance**: `Mapped[dict[str, JsonValue]]` is the correct replacement for `Mapped[dict[str, Any]]` on JSONB columns. Non-JSONB `Mapped[]` columns (str, int, UUID, etc.) are already well-typed by SQLAlchemy stubs — don't touch those. Prefer per-line `# pyright: ignore[reportAny]` over file-level suppression in Phase 3 to ensure net suppression count decreases.

- [x] **3a — Core**: `db_models.py` (34→0), `base_repo.py` (80→0), `repo_decorator.py` (6→0) — Status: Completed (2026-04-09)
    - Notes: `type_annotation_map` with `JsonDict` alias, `SchemaItem` for `__table_args__`, `Mapping[str, object]` for condition/update params, `Sequence[str | ORMOption]` for `get_default_relationships`, `Select[tuple[TDBModel]]` overload for no-arg `self.select()`, `CursorResult` cast for `rowcount`, `_safe_loaded_list` generic helper for greenlet-safe relationship access. 11 file-level `# pyright: reportAny=false` suppressions removed across all persistence repos. Typed sort column registry on `TrackRepository`. `TrackMetric` domain entity with symmetric mapper + `save_track_metrics(list[TrackMetric])` + bool→float coercion fix. Also cleaned `stats.py`, `playlist/core.py`, `db_connection.py` (beyond original plan scope).
- [x] **3b — Repository Leaf Files**: all leaf repos (0 warnings each) — Status: Completed (2026-04-09)
    - Notes: `track/connector.py` (84→0): `get_connector_metadata` `@overload`s, `JsonDict` propagation, defensive `isinstance` narrowing for JSONB artist lists. `track/mapper.py` (30→0): `_safe_loaded_list[T]` helper, `ORMOption` return type, `Sequence[str | Mapping[str, JsonValue]]` for `extract_artist_names`. `track/core.py` (32→0): `_SORT_COLUMNS` registry, `sa_cast` alias to avoid `typing.cast` collision. `track/plays.py` (17→0): `add_columns` chain, `isinstance` guards for `bulk_update_play_source_services`. `stats.py` (24→0): `cast("list[tuple[str, int]]", ...)` for aggregate Row unpacking.

### Phase 4: Infrastructure — Connectors (~70 warnings)

Shared bases first, then per-service from root client outward. **Phases 3 and 4 are largely independent** — they can be parallelized if bandwidth allows.

- [ ] **4a — Shared**: `rate_limited_batch_processor.py` (9) → `base_play_importer.py` (6) → `base.py` (4) → `metric_registry.py` (3) + `protocols.py` (1) + `matching_provider.py` (1) + `http_client.py` (1)
- [ ] **4b — Spotify**: `client.py` (21) → `conversions.py` (5) + `connector.py` (5) + `operations.py` (4) → `play_importer.py` (4) + `play_resolver.py` (3) → `personal_data.py` + `models.py` + `factory.py` + `auth.py` (1 each)
- [ ] **4c — Last.fm + MusicBrainz**: `lastfm/play_importer.py` (6) + `lastfm/conversions.py` (6) → `lastfm/play_resolver.py` (3) + `lastfm/models.py` (3) → remaining Last.fm small files → `musicbrainz/conversions.py` (3) + `musicbrainz/connector.py` (1) → `apple_music/error_classifier.py` (2) + `track_identity_service_impl.py` (1)

### Phase 5: Interface Layer + Config (~54 warnings)

**Pre-implementation (before 5a)**:
- [ ] **Webhook Pydantic models** — Replace unsafe `.get()` chaining in `webhooks.py` with Pydantic v2 discriminated union. Define typed event models for `user.before_create` and `user.created` (known event types), with a `GenericEvent` fallback for unknown types. Use `TypeAdapter` for validation. This is a genuine architectural improvement, not just a type fix — malformed-but-signed payloads currently cause unhandled 500s.
- [ ] **`auth_gate.py` email assertion** — `claims.get("email", "")` defaults to empty string if JWT lacks `email` claim. If `allowed_emails` is configured but `email` is absent, the empty string silently fails the allowlist check (correct behavior today). But if Neon Auth ever stops including `email`, *every* user silently passes. Add `assert email` or log a warning when `email` is absent and `allowed_emails` is configured.
- [ ] **`deps.py` claims cast** — `cast(dict[str, str], raw_claims)` asserts all-string values, but JWT claims contain mixed types (`exp: int`, `iat: int`). Safe today because only `sub` (string) is consumed downstream, but fragile. Narrow the cast to `dict[str, Any]` and extract `sub` with an explicit `str()` conversion.
- [ ] **Config accessor `bool` guard test** — Add unit test confirming `cfg_int(cfg, "count")` where the stored value is `True` returns `default` not `1`. The guard order learning from Phase 1 is not backed by a test.

- [ ] **5a — API**: `webhooks.py` (6) → `sse_operations.py` (5) + `progress.py` (4) → `schemas/workflows.py` (4) + `routes/workflows.py` (4) → `background.py` (3) + `imports.py` (2) + `operations.py` (1) + `auth_gate.py` (1)
- [ ] **5b — CLI**: `workflow_commands.py` (3) + `ui.py` (3) + `async_runner.py` (2) → `progress_provider.py` (1) + `cli_helpers.py` (1)
- [ ] **5c — Config**: `settings.py` (4)

### Phase 6: Endgame (two-step)

**Gate**: Run `uv run basedpyright src/` with zero `reportExplicitAny` warnings across all files before proceeding. Any remaining warnings must be resolved or justified with per-line suppression.

- [ ] **6a — Suppression removal**: Remove all per-file `# pyright: reportAny=false` suppressions (~70 files, incremental as each file is cleaned). Replace with per-line `# pyright: ignore[reportAny]` only where genuinely necessary (third-party stubs, attrs validators).
- [ ] **6b — Promote `reportExplicitAny` to `"error"`** — prevents new `Any` annotations. This is the primary goal of the cleanup.
- [ ] **6c — Audit `reportAny` warnings** — with `reportExplicitAny` at error, remaining `reportAny` warnings come from third-party library leaks. Triage: fix with wrapper types where practical, add to `allowedUntypedLibraries` where not.
- [ ] **6d — Promote `reportAny` to `"error"`** — only after `allowedUntypedLibraries` whitelist is in place. This is the stretch goal — may be deferred if third-party stub quality is insufficient.

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

### Future: Endgame (Two-Step)

- [ ] **Remove all per-file `# pyright: reportAny=false` suppressions** (~70 files)
    - Effort: XL (incremental — remove as each file is cleaned)
    - What: Replace blanket suppression with targeted per-line `# pyright: ignore[reportAny]` only where truly necessary (third-party stubs, attrs validators)
    - Status: Not Started
    - Notes: Gate — run `uv run basedpyright src/` with zero `reportExplicitAny` warnings before removing suppressions

- [ ] **Promote `reportExplicitAny` to `"error"`**
    - Effort: XS (after all layers clean)
    - What: Change from `"warning"` to `"error"` in `pyproject.toml` — prevents new `Any` annotations
    - Dependencies: All layers complete, suppressions replaced with per-line ignores
    - Status: Not Started

- [ ] **Promote `reportAny` to `"error"` with `allowedUntypedLibraries`**
    - Effort: M (requires third-party stub audit)
    - What: Triage remaining `reportAny` warnings (implicit `Any` from third-party libraries). Fix with wrapper types where practical, whitelist in `allowedUntypedLibraries` where not. Then promote to `"error"`.
    - Dependencies: `reportExplicitAny` already at `"error"`
    - Status: Not Started
    - Notes: This is the stretch goal. `reportAny` catches `Any` flowing in from Prefect, httpx internals, etc. May be deferred if stub quality is insufficient.

---

## Application Layer — Remaining (~100 warnings)

### Workflows Epic

Order: config chain first (highest impact), then protocols (define contracts), then implementations.

- [x] **Workflow Protocols + Implementations** (24 warnings → 0, 1 suppressed) — Status: Completed (2026-04-08)
    - Effort: M
    - What: `dict[str, Any]` → `dict[str, object]` for Prefect context and node_details across `node_registry.py`, `node_context.py`, `protocols.py`, `observers.py`, `source_nodes.py`, `destination_nodes.py`, `enricher_nodes.py`, `node_factories.py`, `prefect.py`. Metrics `dict[str, dict[UUID, Any]]` → `dict[str, dict[UUID, MetricValue]]`. `task_def: Any` → `WorkflowTaskDef`.
    - Notes: Added 3 typed extraction helpers to `NodeContext` (`get_upstream_task_ids`, `get_progress_manager`, `get_workflow_operation_id`) to centralize narrowing from `dict[str, object]`. Removed 7 file-level `# pyright: reportAny=false` suppressions. `build_flow() -> Any` suppressed per-line — Prefect stubs are ~23% type-complete, `Flow[P,R]` invariance breaks async returns. Interface-layer `NodeStatusUpdater` implementations aligned (`routes/workflows.py`, `workflow_commands.py`). Fixed pre-existing broken import `syncconnector__playlist` → `sync_connector_playlist` in `source_nodes.py`.

### Use Cases Epic

- [ ] **`src/application/use_cases/_shared/command_validators.py`** (16 warnings)
    - Effort: L
    - What: Replace `Any` in validator functions — likely `dict[str, Any]` config validation. If narrowing callable types (e.g., `Callable[..., Any]` → `Callable[[Command], ValidationResult]`), verify covariance/contravariance in multi-validator composition.
    - Why: Validators know exactly what shapes they validate
    - Dependencies: None
    - Status: Not Started
    - Notes: Existing tests cover validation rejection cases but not type narrowing through composition. Add a test where a validator receives a Command subclass to verify contravariance is handled correctly.

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

- [ ] **`src/domain/entities/track.py` — `TrackList.with_metadata` `@overload`**
    - Effort: S
    - What: Replace `value: Any` with 7 `@overload` signatures, one per `MetadataKey`. Each overload maps key to its corresponding `TrackListMetadata` value type (e.g., `Literal["metrics"]` → `dict[str, dict[UUID, MetricValue]]`). Eliminates the last `Any` in the domain layer.
    - Dependencies: None
    - Status: Not Started
    - Notes: Domain entity, so this is a unit-test-level change. Callers already pass the correct types — overloads just make pyright verify it.

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
    - What: Replace `Any` in JSONB columns with `Mapped[dict[str, JsonValue]]`. Non-JSONB `Mapped[]` columns (str, int, UUID, etc.) are already well-typed by SQLAlchemy stubs — don't touch those.
    - Why: Most JSONB columns store known shapes. Use `type_annotation_map` on `DeclarativeBase` for consistency.
    - Dependencies: Repository mappers that read these columns, Phase 3 pre-implementation `type_annotation_map` task
    - Status: Not Started
    - Notes: Pre-classify before implementation. SQLAlchemy `ColumnElement[Any]` from stubs is unavoidable — suppress per-line with `# pyright: ignore[reportAny]` (not file-level).

- [ ] **`src/infrastructure/persistence/repositories/base_repo.py`** (26 warnings)
    - Effort: L
    - What: Replace `Any` in generic repository base class methods. Design TypeVar bounds for generic query builder BEFORE starting — changes cascade through all leaf repos in 3b.
    - Why: Generic methods use `Any` for flexibility but most callsites know the concrete type. `strictGenericNarrowing` (enabled in pre-impl) will prevent TypeVar→Any collapse.
    - Dependencies: All repository subclasses, Phase 3 pre-implementation regression tests
    - Status: Not Started
    - Notes: `getattr(model, col_name)` dynamics produce unavoidable `Any` — pre-approve `cast()` with comment for those call sites. SQLAlchemy stubs (`InstrumentedAttribute`, `ColumnElement`) — suppress per-line with `# pyright: ignore[reportAny]`.

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
    - Effort: M (increased — Pydantic models + discriminated union)
    - What: Replace `.get()` chaining with Pydantic v2 discriminated union for typed webhook dispatch. Define `UserBeforeCreateEvent`, `UserCreatedEvent` models, `GenericEvent` fallback. Use `TypeAdapter` for validation. This is a genuine architectural improvement — see Phase 5 pre-implementation.
    - Dependencies: Phase 5 pre-implementation webhook models
    - Status: Not Started
    - Notes: Current `.get("user", {}).get("email", "")` pattern silently swallows malformed payloads. Pydantic validation makes shape errors explicit (4xx) instead of silent (500 or wrong behavior).

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
    - Dependencies: Phase 5 pre-implementation email assertion
    - Status: Not Started
    - Notes: See Phase 5 pre-implementation — harden email claim handling alongside the type fix.

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
