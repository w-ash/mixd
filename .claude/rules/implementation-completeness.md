---
paths:
  - "src/**"
---
# Implementation Completeness
- **Every source change requires corresponding tests** — a feature is not done until tests exist and pass
- **Test level by layer**:
  - Domain → unit tests (pure logic, no mocks needed)
  - Application use cases → unit tests with `make_mock_uow()` from `tests.fixtures`
  - Infrastructure connectors → unit tests with `AsyncMock` clients
  - Repositories → integration tests with `db_session`
- **Minimum coverage**: happy path + at least one error/edge case per public function
- Run tests after implementation: `poetry run pytest tests/path/to/test_file.py -x`
