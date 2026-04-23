---
paths:
  - "tests/**"
---
# Test Rules

## Mechanics
- Use the `db_session` fixture for all integration tests (not `get_session()`).
- Prefix pytest, ruff, basedpyright commands with `uv run`.
- Skip `--timeout` flag — none configured.
- Markers auto-applied by directory: `tests/unit/` → `unit`, `tests/integration/` → `integration`.
- Per-test markers: `slow` (>1s), `performance` (>5s), `diagnostic` — all skipped by default.

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
- **Unit** (default): pure logic, mocked dependencies, <100ms.
- **Integration**: real database (`db_session` + `test_data_tracker`), cross-component.
- Domain → always unit (pure functions, no mocks needed).
- Use cases → always unit (mock repos via `make_mock_uow()`).
- Connectors → always unit (mock HTTP clients via `AsyncMock`).
- Repositories → always integration (real SQL behavior).
- API routes → always integration (real request/response cycle via httpx AsyncClient).

## Factories and Mocks (use existing)
- `from tests.fixtures import make_track, make_tracks, make_playlist, make_mock_uow`
- `from tests.fixtures import make_connector_track, make_connector_playlist`
- `from tests.fixtures import make_mock_track_repo, make_mock_playlist_repo`
- `make_mock_uow()` → wired UoW with all repos pre-mocked; configure specific repos via return values.
- `make_track(id=1, title="X", artist="Y")` → keyword overrides for any Track field.
- `make_tracks(count=5)` → batch factory numbered 1..count.
- `patch.object(ClassName, "method", mock)` for `slots=True` classes (NOT `patch.object(instance, ...)`).

## Test Structure
- Class-based grouping by concern: `TestCreatePlaylistHappyPath`, `TestCreatePlaylistErrors`.
- Module docstring on every test file explaining what is tested.
- Every new test directory needs an `__init__.py` (prevents module name collisions).

## Test scope
Test the smallest unit that owns the behavior:
- Test transforms in domain; trust the use case to call them correctly without retesting them.
- Skip assertions on mock return values you configured yourself — that tests the mock setup, not your code.
- Skip Python/attrs language features ("frozen raises on mutation").
