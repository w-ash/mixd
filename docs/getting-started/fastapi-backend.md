# FastAPI Backend Patterns

Clean Architecture patterns for FastAPI: layered dependency flow, use case runner, thin route handlers, consistent error envelopes, and OpenAPI spec generation for frontend codegen.

---

## Clean Architecture

```
Interface  →  Application  →  Domain  ←  Infrastructure
(FastAPI)     (Use Cases)     (Logic)    (DB, APIs)
```

- **Domain**: pure business logic, zero external imports, `Protocol` interfaces for repositories
- **Application**: use case orchestration, owns transaction boundaries, constructor injection
- **Infrastructure**: implements repository protocols, API clients, ORM models
- **Interface**: thin route handlers (5-10 lines), delegates everything to use cases

---

## Use Case Runner Pattern

```python
# src/application/runner.py
from collections.abc import Callable, Coroutine
from typing import Any

from src.domain.repositories.interfaces import UnitOfWorkProtocol


async def execute_use_case[TResult](
    use_case_factory: Callable[[UnitOfWorkProtocol], Coroutine[Any, Any, TResult]],
) -> TResult:
    """Run a use case with proper session and UoW lifecycle.

    Lazy imports keep infrastructure out of the application layer's
    module-level namespace.
    """
    from src.infrastructure.persistence.database.db_connection import get_session
    from src.infrastructure.persistence.repositories.factories import get_unit_of_work

    async with get_session() as session:
        uow = get_unit_of_work(session)
        return await use_case_factory(uow)
```

Both CLI and API call the same runner — zero business logic duplication. See [CLI with Typer](cli-typer.md) for how the CLI uses this same pattern.

---

## Thin Route Handlers

```python
# src/interface/api/routes/items.py
from fastapi import APIRouter

from src.application.runner import execute_use_case
from src.application.use_cases.get_item import GetItemCommand, GetItemUseCase
from src.interface.api.schemas.items import ItemResponse

router = APIRouter(prefix="/items", tags=["items"])


@router.get("/{item_id}")
async def get_item(item_id: int) -> ItemResponse:
    result = await execute_use_case(
        lambda uow: GetItemUseCase(uow).execute(GetItemCommand(id=item_id))
    )
    return ItemResponse.from_domain(result)
```

Route handlers should be 5-10 lines. All business logic lives in use cases.

---

## Error Envelope

Consistent error responses across the entire API:

```python
# src/interface/api/middleware.py
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.domain.exceptions import NotFoundError, ValidationError


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(NotFoundError)
    async def not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": str(exc)}},
        )

    @app.exception_handler(ValidationError)
    async def validation_error(_: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "VALIDATION_ERROR", "message": str(exc)}},
        )

    @app.exception_handler(Exception)
    async def internal_error(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "INTERNAL_ERROR", "message": "An internal error occurred"}},
        )
```

**Error shape**: `{"error": {"code": "UPPER_SNAKE", "message": "Human-readable description"}}`.
For paginated lists: `{"data": [...], "total": int, "limit": int, "offset": int}`.

---

## Application Factory

```python
# src/interface/api/app.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.interface.api.middleware import register_exception_handlers
from src.interface.api.routes.health import router as health_router
from src.interface.api.routes.items import router as items_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="My Project",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],  # Vite dev server
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)

    app.include_router(health_router, prefix="/api/v1")
    app.include_router(items_router, prefix="/api/v1")

    return app


app = create_app()
```

---

## OpenAPI Spec for Frontend Codegen

FastAPI auto-generates an OpenAPI spec at `/api/openapi.json`. Copy this to `web/openapi.json` for Orval codegen. Use `tags` on routers to control how Orval splits the generated code into separate files.

See [React Frontend](react-frontend.md) for the Orval configuration that consumes this spec.
