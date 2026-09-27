---
name: test-pyramid-architect
description: Use this skill when you need pytest strategy design, async test debugging, fixture patterns, test-tier choice, or test-value checks (mutation testing, vacuity) for mixd backend.
---

# Backend Test Strategy — mixd

> Edit-time mechanics (fixtures, markers, directory placement, factories, structure) auto-load from `.claude/rules/test-patterns.md` when touching `tests/**` — this skill adds the strategy layer; don't restate the rule. Note: `unit`/`integration` markers are **auto-applied by directory** — never hand-add them.

## Tiers

What earns a test, mocks, and banned patterns → `.claude/rules/test-value.md`. Tier follows the layer that owns the contract, not a ratio.

- **Unit** — `tests/unit/`, <100ms. Domain = pure functions (no mocks); use cases = `make_mock_uow()`; connectors = `AsyncMock` HTTP clients.
- **Integration** — `tests/integration/`, real PostgreSQL via `db_session`, <1s. Repositories and API routes are *always* this tier (real SQL / real request cycle).
- **E2E** — complete CLI/user workflows, minimal mocking, critical paths only (import, sync, workflow execution).

## Before you write a test

Apply `.claude/rules/test-value.md` (kill criterion, expected values, mock boundaries, red first). For new behavior, also see the test fail with the behavior broken.

## Designing tests for a change

1. Which layer owns the behavior? Test there; don't retest it from the caller (trust the transform from the use case, the use case from the route).
2. Edge cases that earn their keep here: empty batches (batch-first code), duplicate keys against the real unique constraints, cross-user isolation (RLS + `WHERE user_id`).
3. Async-specific cases: transaction boundaries (does it commit inside `async with uow`?), concurrent claims (the schedules/workflow-runs partial-unique guards), cancellation paths.
4. Anything slow (>1s) gets `@pytest.mark.slow`; >5s `performance`; investigation scripts `diagnostic` — all three are skipped by default, so don't hide correctness assertions in them.

## Checking test value

- **Mutation testing** — mutates `src/domain/`. Run it through the wrapper, never bare `mutmut`; usage is in the `scripts/mutmut_run.py` docstring. Survivors: `test-value.md` (Mutation survivors).
  ```bash
  uv run python scripts/mutmut_run.py run "src.domain.<pkg>.<module>*"
  uv run python scripts/mutmut_run.py results      # survivors
  uv run python scripts/mutmut_run.py show <name>  # one mutant's diff
  ```
- **Vacuity check** — `scripts/check_test_vacuity.py` is an AST checker for the V-codes in `test-value.md`. It ratchets against `tests/.vacuity_baseline.json`: it fails when a flagged test is not in the baseline.
- **Red check** — `scripts/check_tests_red.sh` reports new tests that pass on the base branch. For a bug fix or new behavior, such a test does not catch the change.

## Test-environment gotchas (source of most false confidence)

- Test DBs are built by `metadata.create_all()`, **bypassing Alembic** — migration-only DDL (pg_trgm GIN, BRIN, CHECK constraints) does not exist in tests. A CHECK-constraint violation or trigram-index behavior cannot be tested this way; migration tests exercise the chain explicitly.
- One testcontainer per pytest-xdist worker; per-test isolation is savepoint rollback via `db_session`. Never create sessions directly (`get_session()`) — it escapes the savepoint and pollutes the worker's DB.
- The TRUNCATE set in `tests/integration/api/conftest.py` is metadata-derived (v0.7.7.1) — new tables join automatically; the auth tables are on an explicit preserve-list.
- Characterization-first for risky refactors: pin current behavior with tests *before* moving code, so the change lands as an assertion flip, not a silent difference (the v0.8.16 executor-flatten and v0.8.18 identity nets are the house precedents).

## Debugging async tests

- **Hang on relationship access** → unloaded relationship without `selectinload()`; fix the repository query (or read via `loaded_list`/`loaded_one`).
- **Pass alone, fail together** → data pollution; something bypassed `db_session`, or module-level state.
- **Un-awaited coroutine warnings at teardown** → use the `fake_run_async` helper pattern (v0.7.8.19) to close them.
- **CI-only rendering flakes** in CLI tests → terminal size is pinned (`COLUMNS=200`/`LINES=50` in `tests/unit/interface/cli/conftest.py`, v0.8.17.2); don't assert on wrapped output elsewhere either.

## Useful commands

```bash
uv run pytest --durations=20         # find the slow tail
uv run pytest --cov=src/domain --cov-report=term
uv run pytest --co -q | wc -l        # census
```
