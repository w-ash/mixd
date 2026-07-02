# 11 — Workflow executor: flatten the triple-nested closure stack

> Spoke of [The Fable Sweep](README.md) ([v0.8.12](../v0.8.12.md)). Self-contained work order — written so a fresh agent can execute it cold, without re-reading the whole codebase.

**Area:** application · **Suggested executor:** Fable · **Effort:** L · **ROI:** high · **Risk:** med-high · **Status:** Not Started

## Problem

`application/workflows/engine/executor.py` (859 lines, 7 suppressed-PLR violations — the #2 PLR file in the repo) hides its core in a three-deep nested closure stack inside `build_flow` (lines 226-593): `workflow_flow` (~319 lines) → `_run_node_lifecycle` (~223) → `_run_task_inner` (~160). The nesting exists to close over shared mutable state — exactly `task_results: dict[str, NodeResult]` and `node_records: list[NodeExecutionRecord]` plus `parameters`/`flat_order`/`node_observer`/`flow_name`/`total_nodes`. `run_workflow` (686-859) repeats the pattern with its own nested `_build_and_execute_workflow`.

The top-level helpers (`_safe_emit`, `_is_failure_recoverable`, `_get_node_timeout`, `execute_node`, `_aggregate_workflow_metrics`, …) and the `_CATEGORY_TIMEOUTS` dispatch table are healthy — the debt is the closure stack only.

## Why it matters

Maintainer: this is the engine behind every workflow run (flow 6.2 — the Curator's Sunday ritual). 160-statement doubly-nested async bodies are untestable in isolation and are the top structural risk in the application layer. User: indirect — reliability of the core feature.

## Proposed change

1. Introduce a small mutable run-state object: `@define class _RunState: task_results: dict[str, NodeResult]; node_records: list[NodeExecutionRecord]; parameters: ...; node_observer: ...; flow_name: str; total_nodes: int` (exact field set = what the closures capture today — enumerate before moving).
2. Promote `_run_node_lifecycle` and `_run_task_inner` to module-level `async def`s taking `state: _RunState` + the per-node args. Keep the "small protective try clause" property their docstrings insist on — the split boundaries stay, only the nesting goes.
3. `workflow_flow` shrinks to level-loop + TaskGroup wiring. Same for `run_workflow`'s inner `_build_and_execute_workflow`.
4. Do NOT change concurrency semantics: same TaskGroup-per-level structure, same timeout lookup, same enricher-degrade fault tolerance, same SIGTERM/connector-cleanup `finally` blocks (lines are subtle and well-documented — move them intact).

## Blast radius & behavior-preservation

Single consumer surface: `run_workflow` / `build_flow` called from workflow use cases (`workflow_runs.py`, `workflow_preview.py`) and the scheduler. Node execution order, observer event sequence, metrics aggregation, and failure semantics must be identical — the SSE progress stream (frontend `useWorkflowSSE`) consumes the observer events.

## Test plan

Existing: `uv run pytest tests/ -k "executor or workflow_engine or build_flow"` + the workflow integration suites. **Add a characterization test first** if none pins: (a) observer event sequence for a 3-node happy path, (b) enricher-failure degrade path, (c) node timeout path. This is the one spoke where the characterization-before-decomposition rule is non-negotiable.

## Guardrails (do not skip)

- **Clean break:** nested defs deleted; module-level functions are the only path.
- **Grep gate:** `python -c "import ast,sys; t=ast.parse(open('src/application/workflows/engine/executor.py').read()); print(max(len([n for n in ast.walk(f) if isinstance(n,(ast.AsyncFunctionDef,ast.FunctionDef))]) for f in ast.walk(t) if isinstance(f,(ast.AsyncFunctionDef,ast.FunctionDef))))"` — no function contains another function containing another (spot-check nesting ≤ 2).
- **Layer flow:** unchanged.
- **Green:** `uv run pytest` stays green; no test weakened.
- **Ratchet:** executor.py's 7 PLR violations should drop to ~0 — this spoke is the biggest single contributor to re-enabling `PLR0915`/`PLR1702`.
- **Scope discipline:** `config_fields.py` (816 lines) in the same package is a **healthy declarative registry** — explicitly out of scope (audit dispositioned it leave-alone).

## Notes / counter-proposal

Highest-risk spoke in the sweep; suggested LAST among the backend spokes so the suite is maximally trusted by then.
