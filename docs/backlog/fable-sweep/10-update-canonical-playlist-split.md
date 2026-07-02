# 10 — update_canonical_playlist: push the diff sub-algorithms down

> Spoke of [The Fable Sweep](README.md) ([v0.8.12](../v0.8.12.md)). Self-contained work order — written so a fresh agent can execute it cold, without re-reading the whole codebase.

**Area:** application · **Suggested executor:** Opus · **Effort:** M · **ROI:** med · **Risk:** med · **Status:** Not Started

## Problem

`application/use_cases/update_canonical_playlist.py` (463 lines) is ~1.8× its paired `create_canonical_playlist.py` (261) because it inlines two full sub-algorithms: `_append_entries` (lines 392-463) and the diff-execution handling around `_execute_operations` (313-349), plus a metadata-only short-circuit (224-233). Much of this is playlist-diff mechanics that belongs beside the domain diff engine (`domain/playlist/diff_engine.py`) or the repository layer, not in a use case. Two `_shared` modules exist solely for this file (`playlist_results.py` 82, `operation_counters.py` 44) — a sign the module's extractions went sideways instead of down.

## Why it matters

Maintainer: this use case backs both the manual playlist edits (flow 3.4–3.6, shipped v0.8.11) and workflow destination updates — the highest-traffic mutation path in the app. Its size is where the next entries-identity bug hides. User: indirect.

## Proposed change

1. Move the pure parts of `_append_entries` (dedup-vs-append decision, entry construction) into `domain/playlist/` as pure functions next to the diff engine — they operate on entities only.
2. Keep the use case as orchestration: resolve → choose mode (metadata-only / append / diff) → call domain → persist → build result. Target ≤ ~250 lines, symmetrical with create.
3. Fold `_shared/operation_counters.py` into `_shared/playlist_results.py` (single consumer each; one coherent "result assembly" module) or inline them here if they stay single-consumer.

## Blast radius & behavior-preservation

Callers: playlists routes (PATCH), workflow destination nodes, CLI. The v0.8.11 entry-identity semantics (`PlaylistEntry.id` preserved through reorder; manual add allows duplicates; workflow-append dedupes) are load-bearing — the moved code must keep them bit-exact.

## Test plan

Existing: `uv run pytest tests/ -k "update_canonical or playlist_update or append"` — the v0.8.11 suites are the characterization net. Add unit tests for the newly-pure domain functions (cheap wins: dedup decision table).

## Guardrails (do not skip)

- **Clean break:** moved functions deleted from the use case; domain imports only.
- **Grep gate:** `git grep '_append_entries' src/application` returns nothing when done.
- **Layer flow:** the moved functions must be pure (no uow/repo access) to sit in domain — if a piece needs I/O it stays in application.
- **Green:** `uv run pytest` stays green; no test weakened.
- **Ratchet:** none expected.
- **Scope discipline:** don't touch `create_canonical_playlist` beyond what symmetry requires.

## Notes / counter-proposal

Sequence after spoke 07 if both are approved (both touch `_shared/`).
