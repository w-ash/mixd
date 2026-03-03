---
paths:
  - "tests/**"
---
# Test Rules

## Mechanics
- **ALWAYS** use `db_session` fixture for integration tests, NEVER `get_session()`
- Use `poetry run` prefix for pytest, ruff, basedpyright
- No `--timeout` flag configured; don't pass it
- Markers auto-applied by directory: `tests/unit/` → `unit`, `tests/integration/` → `integration`
- Additional markers applied per-test: `slow` (>1s), `performance` (>5s), `diagnostic` — all skipped by default

## Directory Placement (mirror source structure)
- `src/domain/X.py` → `tests/unit/domain/test_X.py`
- `src/application/use_cases/X.py` → `tests/unit/application/use_cases/test_X.py`
- `src/application/workflows/X.py` → `tests/unit/application/workflows/test_X.py`
- `src/application/services/X.py` → `tests/unit/application/services/test_X.py`
- `src/application/metadata_transforms/X.py` → `tests/unit/application/metadata_transforms/test_X.py`
- `src/infrastructure/connectors/Y/X.py` → `tests/unit/infrastructure/connectors/Y/test_X.py`
- `src/infrastructure/persistence/repositories/X.py` → `tests/integration/repositories/test_X.py`
- `src/interface/api/X.py` → `tests/integration/api/test_X.py`

## Unit vs Integration
- **Unit** (default): Pure logic, mocked dependencies, <100ms
- **Integration**: Real database (`db_session` + `test_data_tracker`), cross-component
- Domain → always unit (pure functions, no mocks needed)
- Use cases → always unit (mock repos via `make_mock_uow()`)
- Connectors → always unit (mock HTTP clients via `AsyncMock`)
- Repositories → always integration (test real SQL behavior)
- API routes → always integration (test real request/response cycle via httpx AsyncClient)

## Factories and Mocks (use existing, don't reinvent)
- `from tests.fixtures import make_track, make_tracks, make_playlist, make_mock_uow`
- `from tests.fixtures import make_connector_track, make_connector_playlist`
- `from tests.fixtures import make_mock_track_repo, make_mock_playlist_repo`
- `make_mock_uow()` → wired UoW with all repos pre-mocked; configure specific repos via return values
- `make_track(id=1, title="X", artist="Y")` → keyword overrides for any Track field
- `make_tracks(count=5)` → batch factory numbered 1..count
- `patch.object(ClassName, "method", mock)` for `slots=True` classes — NOT `patch.object(instance, ...)`

## Test Structure
- Class-based grouping by concern: `TestCreatePlaylistHappyPath`, `TestCreatePlaylistErrors`
- Test names: `test_<scenario>_<expected_behavior>` — descriptive, not cryptic
- Module docstring on every test file explaining what is tested
- Every new test directory needs an `__init__.py` (prevents module name collisions)

## Coverage Checklist
1. **Happy path** — primary success case works end-to-end
2. **Validation** — invalid inputs rejected (empty strings, None, out-of-range)
3. **Edge cases** — empty collections, single item, boundary values, duplicates
4. **Error propagation** — exceptions from dependencies handled correctly
5. **Transaction behavior** — commit on success, rollback on failure (use cases)

## Frontend Tests (web/)
- Co-located: `Component.tsx` → `Component.test.tsx` (same directory)
- Hooks: `useX.ts` → `useX.test.ts` (same directory)
- E2E: `web/e2e/*.spec.ts` (Playwright, Chromium desktop)
- Run: `pnpm --prefix web test` (Vitest) / `pnpm --prefix web test:e2e` (Playwright)
- `@/` path alias maps to `web/src/` — use in imports: `import { renderWithProviders } from "@/test/test-utils"`
- Test utilities live in `web/src/test/`: `setup.ts` (MSW server bootstrap), `test-utils.tsx` (renderWithProviders)
- `renderWithProviders()` wraps with test QueryClient (`retry: false`, `gcTime: 0`) + `MemoryRouter` — use for any component that uses hooks, routing, or queries
- Direct `render()` from `@testing-library/react` is fine for pure presentational components with no hooks/router
- MSW auto-generated handlers from Orval in `web/src/api/generated/**/*.msw.ts` — pre-loaded in `setup.ts`
- Per-test API overrides: `server.use(http.get("*/api/v1/playlists", customHandler))` — reset automatically
- All frontend tests use `*.test.tsx` / `*.test.ts` (no `.integration.test.tsx` convention)

## What NOT to Test
- Python/attrs language features ("frozen raises on mutation")
- Mock return values you configured yourself
- Logic already tested at a lower layer (test transforms in domain, not again in use case)
