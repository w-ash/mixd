# 14 — Split the two oversized service methods along their own phase comments

> Spoke of [The Fable Sweep](README.md) ([v0.8.12](../v0.8.12.md)). Self-contained work order — written so a fresh agent can execute it cold, without re-reading the whole codebase.

**Area:** application · **Suggested executor:** Opus · **Effort:** M · **ROI:** med · **Risk:** med · **Status:** Not Started

## Problem

Two service methods are the longest in the application layer, and both already contain their extraction seams as numbered comments:

1. `MetricsApplicationService.get_external_track_metrics` (`application/services/metrics_application_service.py:89-316`, ~227 lines; the file carries 5 suppressed-PLR violations) — cache-first metric fetch with `# Phase 1` (163), `# Phase 2` (203), `# Phase 3` (285) plus validation/field-mapping preamble.
2. `ConnectorPlaylistProcessingService.process_connector_playlist` (`application/services/connector_playlist_processing_service.py:42-281`, ~239 lines; 3 PLR violations) — connector items → unique tracks → matched Playlist, with `# Step 1` (93) and numbered steps onward.

## Why it matters

Maintainer: these back metric enrichment (flow 6, enricher nodes) and playlist import (flow 5.2) — both hot paths for the Curator. Phase-comment-delimited 200-line methods are extract-method textbook cases; the comments prove the author already knew the boundaries. User: indirect.

## Proposed change

1. `get_external_track_metrics` → `_validate_and_map_fields(...)`, `_load_cached_and_missing(...)` (Phase 1), `_fetch_and_extract_fresh(...)` (Phase 2), `_bulk_save(...)` (Phase 3); public method becomes the ~20-line pipeline.
2. `process_connector_playlist` → same treatment along its Step comments (ingest / unique-track extraction / match / assemble).
3. Delete the phase comments — the method names now carry them.

## Blast radius & behavior-preservation

Private-method extraction only; public signatures unchanged; no caller updates. Cache hit/miss behavior, freshness logic, and thundering-herd characteristics (multi-user shared cache!) must be untouched — pure mechanical extraction, no reordering of awaits.

## Test plan

Existing: `uv run pytest tests/ -k "metrics_application or connector_playlist_processing"`. If phase boundaries lack direct coverage, the public-method tests suffice (extraction is behavior-neutral); add none unless a bug is found (then stop and report, per guardrails).

## Guardrails (do not skip)

- **Clean break:** no `_old_` variants left.
- **Grep gate:** `git grep '# Phase 1' src/application/services/metrics_application_service.py` returns nothing when done.
- **Layer flow:** unchanged.
- **Green:** `uv run pytest` stays green.
- **Ratchet:** both files' PLR0915/PLR0914 hits should clear — verify per-file.
- **Scope discipline:** `scheduler.py` (635 lines) in the same directory is **well-decomposed** (audit dispositioned healthy) — untouched. The two `connector_playlist_*` services are NOT duplicates of each other (distinct concerns) — no merging.

## Notes / counter-proposal

None.
