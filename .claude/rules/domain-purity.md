---
paths:
  - "src/domain/**"
---
# Domain Layer Rules
- NEVER import from infrastructure, application, or interface layers
- All entities use `@define(frozen=True, slots=True)` — immutability prevents side-effect bugs in batch pipelines
- All transformations must be pure (no side effects, no I/O)
- Repository interfaces are `Protocol` classes only (zero implementation)
- Exception: `TYPE_CHECKING` allowed for `operations.py` → `spotify/personal_data` circular import
