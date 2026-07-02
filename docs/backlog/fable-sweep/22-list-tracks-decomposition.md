# 22 — track/core.py: extract the list_tracks filter builder

> Spoke of [The Fable Sweep](README.md) ([v0.8.12](../v0.8.12.md)). Self-contained work order — written so a fresh agent can execute it cold, without re-reading the whole codebase.

**Area:** infrastructure · **Suggested executor:** Opus · **Effort:** S-M · **ROI:** med · **Risk:** low-med · **Status:** Not Started

## Problem

`persistence/repositories/track/core.py` (1,099 lines, 5 suppressed-PLR violations) centers on `list_tracks` (lines 435-640, ~205 lines): a 15-parameter query method building filter conditions (query/liked/connector/preference/tags/namespace, each a subquery — lines 464-542), count, sort/keyset pagination, and facets dispatch inline. The filter-building block alone is ~80 lines of homogeneous "append a subquery condition" logic.

Also in the file: the merge family (`move_references_to_track`, `merge_mappings_to_track` at 752-863 ~111 lines, `merge_metrics_to_track`) — large but cohesive, with NamedTuple count reporting; and `save_track` (~98 lines). These are dense-but-structured; the debt concentrates in `list_tracks`.

## Why it matters

Maintainer: `list_tracks` backs the Library page (flow 2.1) — the most-filtered query in the app; every new filter (e.g. future artist filter, v0.10.x) grows the method today. User: indirect.

## Proposed change

1. Extract `_build_list_filters(user_id, query, liked, connector, preference, tags, tag_mode, namespace) -> list[ColumnElement[bool]]` — pure query-construction, unit-testable against compiled SQL.
2. Extract `_apply_sort_and_page(stmt, sort_by, limit, offset, after_value, after_id)` for the sort + keyset block (below line 554 — read before extracting).
3. `list_tracks` becomes: build filters → count (early-return zero) → sort/page → execute → hydrate → facets. Target ≤ ~80 lines.
4. Consider a frozen `TrackListFilters` attrs param object if the 15-param signature still trips PLR0913 after extraction — decide on the diff, don't force it (callers: `list_tracks` use case + CLI).

## Blast radius & behavior-preservation

Signature can stay identical (extraction is internal). SQL emitted must be identical — pg_trgm ILIKE patterns, tag AND/OR having-count semantics (lines 512-531), keyset WHERE. The tag_mode="and" distinct-count trick is load-bearing; move it verbatim.

## Test plan

Existing: `uv run pytest tests/integration/ -k list_tracks` + the Library API tests. The integration suite pins filter semantics per mode; if tag_mode AND/OR lacks a direct case, add one before refactoring.

## Guardrails (do not skip)

- **Clean break:** no `_old` variants.
- **Grep gate:** n/a (internal extraction) — instead assert `wc -l` of `list_tracks` body ≤ ~80.
- **Layer flow:** unchanged.
- **Green:** `uv run pytest` stays green (Docker required).
- **Ratchet:** this file's 5 PLR violations should drop; verify per-file.
- **Scope discipline:** the merge family and `save_track` are dense-but-cohesive — leave them this pass; `_compute_facets` already extracted, untouched.

## Notes / counter-proposal

None.
