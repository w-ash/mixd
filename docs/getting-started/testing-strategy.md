# Testing Strategy

Test pyramid ratios, placement rules by architectural layer, factory fixtures, auto-markers, and coverage targets. Works with both pytest (backend) and Vitest (frontend).

---

## Test Pyramid

```
         /   E2E   \           5%  — Playwright (Chromium desktop)
        /------------\
       / Integration  \       35%  — pytest (real DB), Vitest (MSW)
      /----------------\
     /    Unit Tests    \     60%  — pytest (pure logic), Vitest (components)
    /--------------------\
```

---

## Backend Test Placement

| Source Layer | Test Location | Type | Key Tool |
|---|---|---|---|
| `src/domain/` | `tests/unit/domain/` | unit | No mocks needed |
| `src/application/use_cases/` | `tests/unit/application/use_cases/` | unit | Mock UoW + repos |
| `src/infrastructure/persistence/` | `tests/integration/repositories/` | integration | Real DB session |
| `src/interface/api/` | `tests/integration/api/` | integration | httpx AsyncClient |

---

## Frontend Test Placement

| Source | Test Location | Type |
|---|---|---|
| `web/src/components/` | Co-located `*.test.tsx` | Vitest + RTL |
| `web/src/hooks/` | Co-located `*.test.ts` | Vitest |
| `web/src/pages/` | Co-located `*.test.tsx` | Vitest + MSW |
| Critical user flows | `web/e2e/*.spec.ts` | Playwright |

---

## Factory Fixtures

```python
# tests/fixtures/factories.py
def make_item(*, id: int = 1, name: str = "Test Item", **overrides) -> Item:
    """Factory with keyword overrides for any field."""
    return Item(id=id, name=name, **overrides)

def make_items(count: int = 5) -> list[Item]:
    """Batch factory producing numbered items."""
    return [make_item(id=i, name=f"Item {i}") for i in range(1, count + 1)]
```

```python
# tests/fixtures/mocks.py
def make_mock_uow() -> AsyncMock:
    """Pre-wired UoW mock with all repositories."""
    uow = AsyncMock(spec=UnitOfWorkProtocol)
    uow.get_item_repository.return_value = AsyncMock(spec=ItemRepositoryProtocol)
    return uow
```

**Key patterns**:
- Factories use keyword-only args with sensible defaults — override only what your test cares about
- Batch factories produce numbered items for collection tests
- UoW mocks pre-wire all repository accessors — tests just configure return values

---

## Auto-Markers via conftest.py

```python
# tests/conftest.py
import pytest

def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Auto-apply unit/integration markers based on test file location."""
    for item in items:
        path = str(item.fspath)
        if "/tests/unit/" in path:
            item.add_marker(pytest.mark.unit)
        elif "/tests/integration/" in path:
            item.add_marker(pytest.mark.integration)
```

This eliminates per-function `@pytest.mark.unit` decorators and makes `-m "unit"` / `-m "integration"` filtering reliable.

---

## Coverage Targets

| Layer | Target |
|---|---|
| Domain + Application | 85% |
| Backend overall | 80% |
| Frontend components | 60% |
| E2E critical flows | 100% of identified flows |
