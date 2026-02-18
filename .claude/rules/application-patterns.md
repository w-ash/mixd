---
globs: src/application/**
---
# Application Layer Rules
- NEVER import from infrastructure directly — use `UnitOfWorkProtocol` and repository protocols
- NEVER bypass UnitOfWork for database operations
- Use case owns transaction boundaries: `async with uow:` ... `await uow.commit()`
- All use cases run through `application/runner.py` → `execute_use_case()`
- Command/Result objects: `@define(frozen=True)`
- Constructor injection for all dependencies
