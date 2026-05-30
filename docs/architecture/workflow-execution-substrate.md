# Decision Memo — Workflow Execution Substrate (Prefect?)

**Status:** Recommendation (resolves the v0.8.0 prework item) · **Date:** 2026-05-28
**Scope:** Whether to keep Prefect or replace it *before* v0.8.0 scheduling lands. No code in this memo.

---

## TL;DR

- **Decision A — Execution substrate:** **Swap Prefect → stdlib `asyncio`.** High confidence, low
  risk. Sequence it as a v0.8.0 epic *before* the scheduler epics.
- **Decision B — Scheduling substrate:** **Resolved → hand-roll** (`croniter` + asyncio poll loop).
  APScheduler 4 is still alpha (May 2026), 3.x duplicates our schedule store, and PgQueuer's
  multi-instance `SKIP LOCKED` value is latent on a single instance. Closed during v0.8.2 design (2026-05-30).
- **VM lever:** already pulled. The box is `shared-cpu-2x` / **1 GB** (`fly.toml`, commit `0369b414`),
  so the "512 MB survival" framing in the backlog is **stale** and corrected there.

---

## What mixd actually uses Prefect for

`src/application/workflows/prefect.py` is the **only** file importing Prefect:
`@flow` (×2) + `@task` (×1) for naming/`tags`/`flow_run_name`, `get_run_logger()`, and
`cache_policies.NONE`. Everything distinctive is **mixd's own, not Prefect's**:

| Concern | mixd's implementation | Prefect's role |
|---|---|---|
| Parallelism | `asyncio.gather` over `compute_parallel_levels` (Kahn) | none — code *rejects* `ThreadPoolTaskRunner` (breaks SSE) |
| Run state | `workflow_runs`/`workflow_run_nodes` + injected updaters | none |
| Retries | `tenacity` in connectors | none |
| Results | own tables | none (persistence off) |
| Events | disabled (`PREFECT_API_KEY` sentinel) | none |
| Cancellation | own SIGTERM `_shutdown_requested` flag | none |

The decorator metadata targets Prefect's **UI/observability surface**, which is unreachable in prod
(`fly.toml` exposes only port 8000). So the decorators are effectively write-only — yet `@flow` is
what *forces* the embedded ephemeral SQLite+API server (~80–120 MB at run time) and the 222-module
import that caused the v0.7.8.3–.9 firefight.

**Planned future use:** none. The original adoption rationale ("parallel Prefect execution") was
superseded by mixd's own `asyncio.gather`, and v0.8.0 scheduling explicitly avoids Prefect
(`CronSchedule` needs a server). The roadmap adds zero new Prefect dependence.

**Hard constraint (re-verified May 2026):** Prefect 3 has **no offline `@flow`**. `flow.fn()`
bypasses orchestration entirely (giving nothing a bare coroutine doesn't); any orchestrated call
needs a live API. There is no "keep the decorators, drop the server" middle path.

---

## Decision A — Execution substrate: swap Prefect → stdlib `asyncio`

**No job framework can replace this layer.** Procrastinate, PgQueuer, SAQ, Dramatiq, arq, and TaskIQ
are job *queues* (one job = one function); none model a node DAG with per-node timeout,
enricher-degradation, and asyncio.Queue SSE observers. The only real candidate is **stdlib asyncio —
which mixd already runs.** Prefect's decorators are the only layer on top.

Why swap (with memory no longer the driver):
- Removes ongoing complexity: ephemeral-server lifecycle, two env-var workarounds (`PREFECT_API_KEY`
  + `ALLOW_EPHEMERAL_MODE`), 222-module import, slower first-run bootstrap.
- **Forward risk that survives the 1 GB bump:** v0.8.0 drives *concurrent* runs
  (`max_concurrent_scheduled_runs = 3`); Prefect's embedded SQLite server is documented to hit
  stuck-states under concurrent flows. More RAM doesn't fix that — removing the server does.
- The seam is clean: `run_workflow(workflow_def, …) -> OperationResult` leaks no Prefect types.

Why "stay" is defensible (and why it loses): it works and has been stable since v0.7.8.10 + the bump,
and `@flow`/`@task` retain latent optionality. But that optionality buys nothing mixd uses or plans,
while the concurrency risk is real and imminent. **Recommend swap, before the scheduler epics.**

### Migration sketch (future epic — not this memo)
- Rewrite `prefect.py` internals: drop the decorators/`tags`/`cache_policies`/`get_run_logger`.
- **Logging — no capability loss + a small explicit re-emit (audited against installed Prefect
  source, not docs):** Today Prefect's `prefect.flow_runs`/`task_runs` loggers reach prod *only* by
  propagating into mixd's root handlers — the `api` handler short-circuits because
  `PREFECT_LOGGING_TO_API_ENABLED=false`. The engine's rich INFO lifecycle logs are **suppressed in
  prod** anyway (`PREFECT_LOGGING_LEVEL=WARNING`), and the surviving failure/crash lines are
  **redundant** with mixd's own node-level `logger.error(..., exc_info=True)` (`prefect.py:322`), the
  `RunHistoryObserver` DB status, and the outer `"Workflow failed"` log. Per-run correlation is
  mixd's own `logging_context` contextvars + `add_workflow_run_logger` JSONL sink keyed on
  `workflow_run_id` (`logging.py:236`) — never Prefect's run ids. **The concrete port task:** re-emit
  the ~4 `flow_logger.*` breadcrumbs ("Starting workflow", per-task start, "completed successfully")
  via the module's existing `logger = get_logger(__name__)`. Drop the dead
  `getLogger("prefect").setLevel(...)` + `LoggingConfig.prefect_log_level`. Aligns with
  `python-conventions.md`.
- **Concurrency:** move `asyncio.gather` → `async with asyncio.TaskGroup()` + per-task try/except
  (house rule; the v0.8.0 scheduler spec mandates it too). Keep `compute_parallel_levels` (or move to
  `graphlib.TopologicalSorter`). Preserve per-node timeout, enricher-degrade-vs-fatal, the SIGTERM
  shutdown checks, and observer callbacks.
- Rename `prefect.py` → `executor.py`; update 3 import sites (`workflow_runs.py`,
  `workflow_preview.py`, `cli/workflow_commands.py`).
- Remove `prefect>=3.6.19` (pyproject.toml), `PREFECT_*` env vars (fly.toml + Dockerfile), prefect
  log config (settings.py + logging.py).

### Implicit `@flow`/`@task` behavior audit (from installed Prefect source)

The risk in a swap is losing *implicit* runtime behavior, not the explicit calls. Audited
`flow_engine.py`/`task_engine.py`/`logging/*` in `.venv` against mixd's config + code:

| Prefect implicit behavior | Active in mixd? | Already owned by mixd | Port cost |
|---|---|---|---|
| Auto lifecycle/exception logging | partial (WARNING+ only in prod) | failures: `logger.error(…exc_info)`, observer, DB status | re-emit ~4 breadcrumbs |
| Retries (`handle_retry`/`retry_delay`) | No (`@task` sets none) | `tenacity` in connectors | none |
| Task timeout | No (`timeout_seconds` unset) | own `asyncio.timeout()` (`prefect.py:311`) | none |
| Exception→state + crash detection | inert (state → ephemeral DB we never read) | own try/except + DB status + `workflow_run_sweeper` | none |
| Heartbeats (crash monitoring) | emits to ephemeral server only | sweeper marks stalled runs failed | none |
| Result/state persistence | ephemeral DB | `workflow_runs` tables | none |
| Cancellation (server service) | inert without server | own SIGTERM `_shutdown_requested` | none |
| `log_prints` stdout capture | No (unset) | n/a (`print()` uncaptured today too) | none |
| Tag concurrency limits | inert (needs server) | own `acquire_workflow_slot` | none |

**Conclusion:** every implicit behavior is inactive, server-dependent (inert in ephemeral mode), or
already reimplemented by mixd. The only concrete port task is the log breadcrumbs. This *strengthens*
Decision A — the runtime value was already mixd's own.

---

## Decision B — Scheduling substrate (RESOLVED → hand-roll; closed 2026-05-30)

The backlog planned a hand-rolled `croniter` + poll loop. Current research surfaced a stronger
*framework* option the original analysis missed (**PgQueuer**), so the choice was made explicit:

| Option | Backend | In-process | Cron | Notes | Fit |
|---|---|---|---|---|---|
| **PgQueuer 1.0.1** | Postgres | ✅ `await pgq.run()` | ✅ persisted, `SKIP LOCKED` | psycopg driver OK; own `pgqueuer_*` tables; dedicated `autocommit` conn; v1.0 (newest) | Strongest framework fit |
| **Hand-roll** (croniter 6.x + asyncio) | own tables | ✅ | ✅ (DIY catchup) | ~100 LOC, fully owned, tested via existing harness | Competitive |
| **APScheduler 3.x** | none / SQLAlchemy | ✅ AsyncIOScheduler | ✅ misfire/coalesce/max_instances | mature; v4 (better PG store) not prod-ready | Safe middle |
| Procrastinate 3.8.1 | Postgres | ✅ | ✅ | **LISTEN/NOTIFY dies on Neon pooler** → polling anyway; 2nd pool, own tables, non-Alembic migrations | Over-provisioned |
| SAQ / TaskIQ | Redis/PG | ❌ worker | ✅ | separate worker process | Fails in-process |
| Dramatiq / arq | Redis/RabbitMQ | ❌ | partial | mandatory Redis | Fails zero-infra |

**The decision:** is DB-backed schedule persistence + a `SKIP LOCKED` concurrency guard (PgQueuer,
for free) worth a v1.0 dependency + a dedicated `autocommit` connection + tables outside Alembic —
versus ~100 lines of `croniter`/`asyncio`/advisory-lock that mixd fully owns and tests with its
existing testcontainers harness? mixd is multi-user but single-instance today, so PgQueuer's
multi-instance `SKIP LOCKED` value is latent, not current. **Resolved (2026-05-30): hand-roll** —
`run_sweeper_loop()` is a living precedent, APScheduler 4 is alpha + 3.x duplicates the schedule
store, and the hardening below is mandatory regardless. Revisit only if multi-instance becomes real.

> Refined Neon note: PgQueuer's *scheduler* uses `FOR UPDATE SKIP LOCKED` (polling), not
> LISTEN/NOTIFY — so the Neon pooled-endpoint NOTIFY limitation hurts low-latency job *dispatch*,
> not cron *scheduling* correctness. The Neon concern is real for Procrastinate, minor for PgQueuer-as-scheduler.

---

## Lessons from Prefect's engine (hardening for the replacement)

Mining the installed Prefect source (`flow_engine.py`/`task_engine.py`/`task_runners.py`/
`server/services/*`/`states.py`/`client/schemas/objects.py`) for what a production engine handles that
a naive replacement would miss. Three of these are **present-tense bugs in current code — fix
regardless of the swap** (all three ship in **v0.8.0**, including the cleanup-shield — not the v0.8.1 swap).

**Present-tense bugs (independent of the swap):**
- **[fix-now] Terminal-write race.** The sweeper (`workflow_run_sweeper.py:104`) and the run's own
  `except`/complete path (`workflow_runs.py:367,416`) can both write a terminal status to the same
  run. Add `WHERE status NOT IN (terminal_set)` to `update_run_status` (first-writer-wins). Mirrors
  Prefect `HandleFlowTerminalStateTransitions` (`core_policy.py:1589-1607`). *Highest value.*
- **[fix-now] Unshielded cleanup.** `await ...connectors.aclose()` (`prefect.py:471`) can be hit by
  `CancelledError` on SIGTERM mid-`await` → leaked httpx pools. Wrap in `asyncio.shield()`. Mirrors
  Prefect's shielded finalizers (`flow_engine.py:1491-1497`).
- **[fix-now] Heartbeat false-positives.** If the heartbeat ticker is an asyncio task, a CPU-bound
  node blocks the loop and the sweeper reaps a *healthy* run (mixd's own sweeper docstring names this
  failure mode). Emit heartbeats from an **OS daemon thread**, as Prefect does (`flow_engine.py:303-306`).

**DAG executor (for the swap):**
- **[decide] TaskGroup sibling-cancellation is a semantics choice, not a mechanical swap.** Because
  `_run_node_lifecycle` never raises (returns tuples), `gather`→`TaskGroup` is behavior-preserving but
  delivers zero sibling cancellation. Choose consciously: keep tuple-return (siblings finish, like
  Prefect — `futures.py:557-576`) or let fatal nodes raise inside the group + `except*`.
- **[adopt] Keep `CancelledError` out of the degrade/failure path.** **Verify (don't assume):** in
  Python 3.8+ `CancelledError` is a `BaseException`, so `except (TimeoutError, Exception)`
  (`prefect.py:313`) already won't catch it — confirm this holds and ensure `WorkflowCancelledError`
  can't enter the degrade path. Prefect's `Exception` vs `BaseException` fault line (`task_engine.py:1499-1513`).
- **[validated] Single event loop, level-based concurrency** — confirmed correct by counter-example:
  Prefect's thread-per-task + `asyncio.run`-per-task (`task_runners.py:412-419`) is the exact
  SSE-breaking model mixd's docstring rejects.

**Scheduler (hardening the v0.8.0 plan):**
- **[adopt] Compute next-run forward from `now`,** never from a stale stored `next_run_at`
  (`scheduler.py:177`) — prevents croniter-iteration explosion after an outage.
- **[adopt] Anchor interval schedules to a stable grid** (`schedules.py:303-306`) — the plan's
  recompute-from-now-on-enable (`v0.8.x.md:107`) drifts phase across restarts; anchor keeps "every
  6h" firing at stable times.
- **[adopt] Cadence correction:** `sleep(max(0, interval − work_duration))` (`docket/_perpetual.py:130`).
- **[adopt] Stuck-start reaper** for a dispatch that dies between `mark_schedule_started` and
  `*_completed` (Prefect's `MarkLateRuns`, `late_runs.py:69-85`).
- **[validated] `catchup=False` (skip missed, advance), 60s poll, per-row `next_run_at`** all match
  Prefect's only built-in behavior.

**Run history:**
- **[adopt] Distinct `crashed` vs `failed` status** (`StateType.CRASHED`; exit-code registry
  `_infrastructure_exit_codes.py:21-113`) — `failed` = logic raised, `crashed` = worker died
  (OOM/SIGTERM). Reclassify the SIGTERM/server-reload path (`workflow_runs.py:393`) off `failed`.
  Treat `crashed` as fail-class in rollups (`states.py:686-687`).
- **[adopt] Stale threshold as a multiple of heartbeat interval** (Foreman, `foreman.py:88-94`), stop
  heartbeating once terminal (`flow_engine.py:330-336`), batch-cap each sweep (`late_runs.py:68`).
- **[adopt-lightweight] Surface cold-start latency = `started_at − created_at`** (Prefect's
  `estimated_start_time_delta`, `objects.py:660`).
- **[pattern] Use name-overlays on existing statuses** for future sub-states (retrying/cached) rather
  than minting new top-level statuses (`states.py:883,931,940`).

**Pre-execution validation:**
- **[validated] mixd already exceeds Prefect here.** Prefect 3 does *no* static DAG validation (dynamic
  graph — grep for `acyclic`/`topological`/cycle-detection in core returns nothing); its only cycle
  guard is a runtime self-dependency check (`utilities/engine/__init__.py:963`). mixd's `validation.py`
  statically checks multi-node cycles (Kahn's), dangling edges, duplicate IDs, unknown node types,
  config required-keys/types, connector availability, + enrichment-dependency warnings — and is
  **already Prefect-free by design** (module docstring), so the swap doesn't touch it. Extra de-risk.
- **[adopt] Close the silent-wrong-result validation gaps** (confirmed in `validate_workflow_def` +
  `_run_node_lifecycle`): (a) `primary_input` set-but-not-in-`upstream` silently falls back to
  `upstream[0]` (`prefect.py:285-289`); (b) a `result_key` that collides with another task's id
  silently overwrites that task's stored result via the alias write (`prefect.py:404-409`) — also
  reject duplicate `result_key`s; (c) a non-source node with zero upstream passes validation then
  fails at runtime. These produce wrong output *without erroring* — add to `validate_workflow_def`.
- **[consider] Pydantic parameter validation** — the one thing Prefect validates that mixd doesn't
  (`@flow(validate_parameters=True)`, `flows.py:218`); mixd passes `**parameters: object` untyped.
  Relevant only if/when workflows take runtime parameters.

**Consciously skipped** (server/HA/queue artifacts mixd doesn't need): look-ahead run window,
idempotency-key + `ON CONFLICT` (multi-scheduler HA),
`CANCELLING`/`SCHEDULED`/`LATE`/`PAUSED`/`AWAITING_RETRY` states, full per-state history table,
thread/process cancellation machinery, `run_coro_as_sync` loop-juggling.

> **Superseded (2026-05-30):** the earlier "UTC-only / DST skipped" scoping is dropped. v0.8.2 adopts
> **per-schedule IANA timezones** — croniter evaluates the cron against a tz-aware base (handling DST)
> and `next_run_at` is stored in UTC, so the polling query stays UTC-simple while rituals fire in the
> user's local time.

---

## Verification strategy (for the swap epic)

The swap is **behavior-preserving**, so verification is regression-first. The existing tests are the
safety net and should pass after the rename: `test_validation.py`, `test_fault_tolerance.py`,
`test_transform_execution.py`, `test_prefect.py` (pure helpers), `test_workflow_runs.py`, and the
integration `test_workflow_run_repository.py` / `test_workflow_runs.py` (testcontainers PG).

New/changed tests: concurrency-gate (independent nodes run in parallel under `TaskGroup`); TaskGroup
sibling-cancellation on a fatal node; graceful-shutdown skip + `WorkflowCancelledError`; per-node
timeout; golden `OperationResult` snapshot (identical pre/post swap); per-run JSONL logging still
keyed on `workflow_run_id`; an architectural guard asserting `"prefect" not in sys.modules` after app
import. Remove the `prefect_log_level` assertions in `tests/unit/config/`. Gates per CLAUDE.md:
`pytest -m ""`, `basedpyright src/`, `ruff check .`.

---

## Sources

- **Primary docs (Context7, May 2026):** PgQueuer `/janbjorge/pgqueuer` (drivers incl. `PsycopgDriver`,
  `in_memory()`, programmatic `pgq.run()`, `@pgq.schedule` → `pgqueuer_schedules`, `SKIP LOCKED`);
  croniter `/pallets-eco/croniter` (`is_valid(strict)`, `get_next(datetime)`, tz/DST).
- **Alternatives scan:** Procrastinate 3.8.1, PgQueuer 1.0.1, APScheduler 3.11.2 (v4 alpha),
  SAQ/Dramatiq/arq/TaskIQ; Neon connection-pooling docs; Prefect community discussions #15980/#15066
  (no offline `@flow`).
- **Codebase / history:** `prefect.py`, `validation.py`, `run_guard.py`, `logging.py`,
  `interface/api/app.py`, `fly.toml`; commits `a4e43950` (.3) → `52f5c7c5` (.9) → `0369b414` (1 GB bump).
