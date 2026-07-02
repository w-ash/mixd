# 09 — Shared timed-query envelope for read use cases

> Spoke of [The Fable Sweep](README.md) ([v0.8.12](../v0.8.12.md)). Self-contained work order — written so a fresh agent can execute it cold, without re-reading the whole codebase.

**Area:** application · **Suggested executor:** Haiku · **Effort:** S · **ROI:** med · **Risk:** low · **Status:** Not Started

## Problem

A second, heavier envelope inflates the read-side use cases: `ExecutionTimer` + try/except/else logging + `operation_summary` scaffolding, duplicated across `get_liked_tracks.py` (195 lines), `get_played_tracks.py` (235), `read_canonical_playlist.py` (148), and the canonical-playlist CRUD modules. The same "list tracks by filter" shape costs 78 lines in `get_preferred_tracks.py` (the clean baseline) vs 195–235 in its siblings — the delta is pure envelope.

## Why it matters

Maintainer: ~200 lines of copy-pasted timing/logging ceremony; the three near-duplicate query modules at three sizes confuse the next reader about which is canonical. User: none.

## Proposed change

1. Add `_shared/timed_execution.py`: an async context manager or decorator `timed_query(operation_name)` owning the `ExecutionTimer` + success/failure logging that the modules currently hand-roll.
2. Rewrite `get_liked_tracks`, `get_played_tracks`, `read_canonical_playlist` (and the canonical CRUD timer blocks) to use it; target: each query module within ~1.5× of the `get_preferred_tracks` baseline.
3. Log lines keep their message text and bound fields (dashboards/log queries may reference them).

## Blast radius & behavior-preservation

Results, Command surfaces, and log semantics unchanged; only the scaffolding moves. These use cases feed workflow sources and the playlist detail page — the existing suites pin their outputs.

## Test plan

Existing: `uv run pytest tests/unit/application/use_cases/ -k "liked or played or read_canonical"` unchanged. One new unit test for `timed_query` (success + exception paths).

## Guardrails (do not skip)

- **Clean break:** no module keeps a private copy of the timer scaffolding.
- **Grep gate:** `git grep -c 'ExecutionTimer' src/application/use_cases/` shows only `_shared/timed_execution.py` (plus any orchestration module with genuinely bespoke timing).
- **Layer flow:** unchanged.
- **Green:** `uv run pytest` stays green; no test weakened.
- **Ratchet:** none expected.
- **Scope discipline:** don't restructure the query logic itself (pagination, hydration) — envelope only.

## Notes / counter-proposal

Can run before or after spoke 07; same `_shared/` directory, no file overlap.
