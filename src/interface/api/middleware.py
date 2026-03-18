"""Global exception handling middleware.

Maps Python exceptions to structured JSON error envelopes so the frontend
always receives a consistent error shape regardless of what goes wrong.
"""

from typing import Final

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DatabaseError

from src.application.workflows.prefect import WorkflowAlreadyRunningError
from src.application.workflows.validation import ConnectorNotAvailableError
from src.config import get_logger
from src.domain.exceptions import NotFoundError, TemplateReadOnlyError

logger = get_logger(__name__)

_DB_ERROR_ENVELOPE: Final[dict[str, dict[str, str]]] = {
    "error": {
        "code": "DATABASE_UNAVAILABLE",
        "message": "Database connection unavailable. Ensure PostgreSQL is running.",
    }
}


def register_exception_handlers(app: FastAPI) -> None:
    """Register global exception-to-HTTP-error-envelope handlers."""

    @app.exception_handler(NotFoundError)
    async def not_found_handler(_request: Request, exc: NotFoundError) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "code": "NOT_FOUND",
                    "message": str(exc),
                }
            },
        )

    @app.exception_handler(TemplateReadOnlyError)
    async def template_readonly_handler(  # pyright: ignore[reportUnusedFunction]
        _request: Request, exc: TemplateReadOnlyError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "TEMPLATE_READONLY",
                    "message": str(exc),
                }
            },
        )

    @app.exception_handler(WorkflowAlreadyRunningError)
    async def workflow_running_handler(  # pyright: ignore[reportUnusedFunction]
        _request: Request, exc: WorkflowAlreadyRunningError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "WORKFLOW_RUNNING",
                    "message": str(exc),
                    "details": {"workflow_id": exc.workflow_id},
                }
            },
        )

    @app.exception_handler(ConnectorNotAvailableError)
    async def connector_not_available_handler(  # pyright: ignore[reportUnusedFunction]
        _request: Request, exc: ConnectorNotAvailableError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "CONNECTOR_NOT_AVAILABLE",
                    "message": str(exc),
                    "details": {"required_connectors": exc.missing_connectors},
                }
            },
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(_request: Request, exc: ValueError) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": str(exc),
                }
            },
        )

    @app.exception_handler(DatabaseError)
    async def database_error_handler(  # pyright: ignore[reportUnusedFunction]
        _request: Request, exc: DatabaseError
    ) -> JSONResponse:
        logger.error("Database error", error=str(exc), exc_type=type(exc).__name__)
        return JSONResponse(status_code=503, content=_DB_ERROR_ENVELOPE)

    @app.exception_handler(Exception)
    async def generic_error_handler(_request: Request, exc: Exception) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        logger.opt(exception=True).error("Unhandled API error", error=str(exc))
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "An internal error occurred",
                }
            },
        )
