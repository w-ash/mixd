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

## Test Execution Policy (DO NOT over-test)
**During implementation (inner loop)** — run ONLY affected tests:
- Editing `src/domain/X.py` → `poetry run pytest tests/unit/domain/test_X.py -x`
- Editing `src/application/use_cases/X.py` → `poetry run pytest tests/unit/application/use_cases/test_X.py -x`
- Editing a connector → `poetry run pytest tests/unit/infrastructure/connectors/Y/test_X.py -x`
- Editing a repository → `poetry run pytest tests/integration/repositories/test_X.py -x`
- Editing an API route → `poetry run pytest tests/integration/api/test_X.py -x`
- Editing frontend → `pnpm --prefix web test src/path/to/Component.test.tsx`
- Use `-k "test_name"` when iterating on a specific failure
- Use `--lf` to rerun only previously-failed tests

**Before committing** — full fast suite:
- `poetry run pytest` (runs with `-n auto`, excludes slow/diagnostic)
- `pnpm --prefix web test` (all frontend)

**Full verification (version bump, dep update, or explicit request only)**:
- `poetry run pytest -m ""` — all tests including slow
- `poetry run basedpyright src/` — type check
- `poetry run ruff check .` — lint
- `pnpm --prefix web check && pnpm --prefix web build`

**Anti-patterns** — NEVER do these during normal implementation:
- Running `poetry run pytest` (full suite) after every small edit
- Running `poetry run basedpyright src/` after editing a single file
- Running `pnpm --prefix web build` to verify a component change
- Running slow/diagnostic tests unless touching connectors or infrastructure

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

## What NOT to Test
- Python/attrs language features ("frozen raises on mutation")
- Mock return values you configured yourself
- Logic already tested at a lower layer (test transforms in domain, not again in use case)
