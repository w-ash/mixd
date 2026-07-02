# 12 — Delete the dead execution-Strategy abstraction

> Spoke of [The Fable Sweep](README.md) ([v0.8.12](../v0.8.12.md)). Self-contained work order — written so a fresh agent can execute it cold, without re-reading the whole codebase.

**Area:** domain · **Suggested executor:** Opus · **Effort:** M · **ROI:** high · **Risk:** low-med · **Status:** Not Started

## Problem

`domain/playlist/execution_strategies.py` (461 lines) is ~75% dead or unreachable. Verified 2026-07-01:

- Production has exactly **one** consumer: `application/services/connector_push.py:154` — `get_execution_strategy("api").plan_operations(diff).operations`, reading only `.operations`.
- `execute_with_strategy` (425-461): **zero non-test callers** (vulture flags it too). Its two branches are identical anyway — both call `reorder_to_match_target(...)`; the else-branch comment admits "implementation would handle the operations // For now, fall back to reordering."
- `CanonicalExecutionStrategy` (77-135): reachable only via `get_execution_strategy("canonical")` — **never called outside tests**. Its two branches also return the same operations, differing only in an unread `use_atomic_reorder` flag.
- `ExecutionPlan.dependency_order` + `.use_atomic_reorder`: computed (`_calculate_dependency_order`, line 207) but **never read** in production. The Protocol's `can_optimize_to_reorder` has no live caller.

Net production need: `simulate_position_shifts(sequence_operations_for_spotify(diff.operations))`.

## Why it matters

Maintainer: a Protocol + factory + two strategies + a plan type kept alive purely by their own tests — the canonical example of speculative abstraction. Deleting it makes the *real* sync path (flow 5.4 manual push/pull) readable at a glance. User: none.

## Proposed change

1. Reduce `execution_strategies.py` to the live path: keep `sequence_operations_for_spotify` + `simulate_position_shifts` (and whatever `plan_operations("api")` actually computes for `.operations` — inline it as a plain function, e.g. `plan_api_operations(diff) -> list[PlaylistOperation]`).
2. Delete: `ExecutionStrategy` Protocol, `get_execution_strategy` factory, `CanonicalExecutionStrategy`, `execute_with_strategy`, `_calculate_dependency_order`, and the `dependency_order`/`use_atomic_reorder` fields (drop `ExecutionPlan` entirely if `.operations` was its only read field).
3. Update `connector_push.py:154` to call the plain function.
4. Delete the tests that exist solely to exercise the deleted machinery (`tests/unit/domain/test_execution_strategies.py` — keep/port any case that pins the surviving sequencing/shift-simulation behavior).
5. Remove the matching `vulture_whitelist.py` entry if one exists for `execute_with_strategy`.

## Blast radius & behavior-preservation

One production call site. The operations list produced for the "api" path must be identical — that list drives real Spotify mutations (`SpotifyPlaylistSyncOperations`). Port the sequencing-behavior test cases before deleting the rest.

## Test plan

Existing: `uv run pytest tests/ -k "execution_strateg or connector_push or sequence_operations"`. Port the live-path cases from `test_execution_strategies.py` to target the plain functions FIRST, then delete. Deleting tests of deleted code is not weakening — but every surviving behavior keeps its test.

## Guardrails (do not skip)

- **Clean break:** no deprecated aliases.
- **Grep gate:** `git grep 'get_execution_strategy\|execute_with_strategy\|CanonicalExecutionStrategy\|use_atomic_reorder'` returns nothing when done.
- **Layer flow:** domain stays pure; the surviving functions are already pure.
- **Green:** `uv run pytest` stays green.
- **Ratchet:** removes a vulture whitelist pressure point; check `uv run vulture` output shrinks.
- **Scope discipline:** `diff_engine.py`'s LIS functions are healthy (inherent algorithmic complexity) — untouched.

## Notes / counter-proposal

If the team *intends* a future canonical-DB execution path, the right move is still deletion — the stub encodes no real design (its branches are identical), and git history preserves it.
