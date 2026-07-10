---
name: test-pyramid-architect
description: Use this skill when you need pytest strategy design, async test debugging, fixture patterns, or test pyramid balance (60/35/5) for mixd backend.
---

# Backend Test Strategy — mixd

> Edit-time mechanics (fixtures, markers, directory placement, factories, structure) auto-load from `.claude/rules/test-patterns.md` when touching `tests/**` — this skill adds the strategy layer; don't restate the rule. Note: `unit`/`integration` markers are **auto-applied by directory** — never hand-add them.

## Pyramid (60/35/5)

- **Unit (60%+)** — `tests/unit/`, <100ms, mocked deps. Domain = pure functions (no mocks); use cases = `make_mock_uow()`; connectors = `AsyncMock` HTTP clients.
- **Integration (35%)** — `tests/integration/`, real PostgreSQL via `db_session` + `test_data_tracker`, <1s. Repositories and API routes are *always* this tier (real SQL / real request cycle).
- **E2E (5%)** — complete CLI/user workflows, minimal mocking, critical paths only (import, sync, workflow execution).

Right level per change (from CLAUDE.md): domain=unit, use case=unit+mocks, repository=integration.

## Designing coverage for a change

1. Which layer owns the behavior? Test there; don't retest it from the caller (trust the transform from the use case, the use case from the route).
2. Happy path + at least one error/edge case per change (CLAUDE.md floor). Edge cases that earn their keep here: empty batches (batch-first code), duplicate keys against the real unique constraints, cross-user isolation (RLS + `WHERE user_id`).
3. Async-specific cases: transaction boundaries (does it commit inside `async with uow`?), concurrent claims (the schedules/workflow-runs partial-unique guards), cancellation paths.
4. Anything slow (>1s) gets `@pytest.mark.slow`; >5s `performance`; investigation scripts `diagnostic` — all three are skipped by default, so don't hide correctness assertions in them.

## Test-environment gotchas (source of most false confidence)

- Test DBs are built by `metadata.create_all()`, **bypassing Alembic** — migration-only DDL (pg_trgm GIN, BRIN, CHECK constraints) does not exist in tests. A CHECK-constraint violation or trigram-index behavior cannot be tested this way; migration tests exercise the chain explicitly.
- One testcontainer per pytest-xdist worker; per-test isolation is savepoint rollback via `db_session`. Never create sessions directly (`get_session()`) — it escapes the savepoint and pollutes the worker's DB.
- The TRUNCATE set in `tests/integration/api/conftest.py` is metadata-derived (v0.7.7.1) — new tables join automatically; the auth tables are on an explicit preserve-list.
- Characterization-first for risky refactors: pin current behavior with tests *before* moving code, so the change lands as an assertion flip, not a silent difference (the v0.8.16 executor-flatten and v0.8.18 identity nets are the house precedents).

## Debugging async tests

- **Hang on relationship access** → unloaded relationship without `selectinload()`; fix the repository query (or read via `loaded_list`/`loaded_one`).
- **Pass alone, fail together** → data pollution; something bypassed `db_session`/`test_data_tracker`, or module-level state.
- **Un-awaited coroutine warnings at teardown** → use the `fake_run_async` helper pattern (v0.7.8.19) to close them.
- **CI-only rendering flakes** in CLI tests → terminal size is pinned (`COLUMNS=200`/`LINES=50` in `tests/unit/interface/cli/conftest.py`, v0.8.17.2); don't assert on wrapped output elsewhere either.

## Useful commands

```bash
uv run pytest --durations=20         # find the slow tail
uv run pytest --cov=src/domain --cov-report=term
uv run pytest --co -q | wc -l        # census
```
