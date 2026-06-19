---
paths:
  - "src/domain/**"
---
# Domain Layer Rules
- Import only from domain and stdlib — domain is a self-contained kernel with zero outward dependencies (no infrastructure, application, interface, or config imports)
- Constants that encode domain business rules (tolerances, thresholds, priorities) belong **in domain**, not in `src/config/constants.py`. Use module-level `Final` values in the domain file that owns the concept.
- All entities use `@define(frozen=True, slots=True)` — immutability prevents side-effect bugs in batch pipelines
- All transformations must be pure (no side effects, no I/O)
- Repository interfaces are `Protocol` classes only (zero implementation)
- Exception: `TYPE_CHECKING` allowed to break a genuine circular import — e.g. `operations.py` → `spotify/personal_data`, and `repositories/{track,play}.py` → `repositories/uow.py` (those service protocols take a `uow` param while `uow.py` imports them for its accessors)
