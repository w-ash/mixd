"""Global exception handling middleware.

Maps Python exceptions to structured JSON error envelopes so the frontend
always receives a consistent error shape regardless of what goes wrong.

Handlers are registered imperatively via ``app.add_exception_handler`` rather
than the ``@app.exception_handler`` decorator. Two reasons, both about keeping
the type checker honest without suppressions:

1. A decorator-captured *inner* function reads as never-referenced to pyright
   (``reportUnusedFunction``, "as designed"). Passing the handler as an argument
   to ``add_exception_handler`` references it, so the warning never fires.
2. Starlette types handlers as ``Callable[[Request, Exception], ...]``. Because
   ``Callable`` is contravariant in its parameters, a handler annotated with a
   specific exception subtype is *not* assignable to that alias. So each handler
   takes ``exc: Exception`` and re-narrows with an ``isinstance`` guard before
   reading subtype-specific attributes — runtime-checked narrowing pyright can
   follow, with no cast and no suppression. (A plain ``assert`` would do the same
   but is stripped under ``python -O`` and banned by ruff S101, so we use an
   explicit guard that falls back to the generic handler in the — unreachable —
   event of a type mismatch.)
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DatabaseError

from src.application.workflows.definition.validation import ConnectorNotAvailableError
from src.config import get_logger
from src.domain.exceptions import (
    ConfirmationRequiredError,
    ConnectorNotConnectedError,
    LastfmAuthRequiredError,
    NotFoundError,
    OptimisticLockError,
    ScheduleAlreadyExistsError,
    ScheduleInvariantError,
    SpotifyAuthRequiredError,
    WorkflowAlreadyRunningError,
)

logger = get_logger(__name__)


def register_exception_handlers(app: FastAPI) -> None:
    """Register global exception-to-HTTP-error-envelope handlers."""

    async def generic_error_handler(_request: Request, exc: Exception) -> JSONResponse:
        logger.error("Unhandled API error", error=str(exc), exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "An internal error occurred",
                }
            },
        )

    async def not_found_handler(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "code": "NOT_FOUND",
                    "message": str(exc),
                }
            },
        )

    async def optimistic_lock_handler(
        _request: Request, exc: Exception
    ) -> JSONResponse:
        if not isinstance(
            exc, OptimisticLockError
        ):  # pragma: no cover — dispatch guarantees the type
            return await generic_error_handler(_request, exc)
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "OPTIMISTIC_LOCK_CONFLICT",
                    "message": str(exc),
                    "details": {
                        "entity_id": str(exc.entity_id),
                        "expected_version": exc.expected_version,
                    },
                }
            },
        )

    async def confirmation_required_handler(
        _request: Request, exc: Exception
    ) -> JSONResponse:
        if not isinstance(
            exc, ConfirmationRequiredError
        ):  # pragma: no cover — dispatch guarantees the type
            return await generic_error_handler(_request, exc)
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "CONFIRMATION_REQUIRED",
                    "message": str(exc),
                    "details": {
                        "removals": exc.removals,
                        "total": exc.total,
                        "remaining": exc.remaining,
                        "confirm_token": exc.confirm_token,
                    },
                }
            },
        )

    async def workflow_running_handler(
        _request: Request, exc: Exception
    ) -> JSONResponse:
        if not isinstance(
            exc, WorkflowAlreadyRunningError
        ):  # pragma: no cover — dispatch guarantees the type
            return await generic_error_handler(_request, exc)
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

    async def schedule_exists_handler(
        _request: Request, exc: Exception
    ) -> JSONResponse:
        if not isinstance(
            exc, ScheduleAlreadyExistsError
        ):  # pragma: no cover — dispatch guarantees the type
            return await generic_error_handler(_request, exc)
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "SCHEDULE_EXISTS",
                    "message": str(exc),
                    "details": {"target": exc.target},
                }
            },
        )

    async def schedule_invariant_handler(
        _request: Request, exc: Exception
    ) -> JSONResponse:
        if not isinstance(
            exc, ScheduleInvariantError
        ):  # pragma: no cover — dispatch guarantees the type
            return await generic_error_handler(_request, exc)
        # A malformed schedule that slipped past request validation and tripped a
        # DB CHECK is a validation failure (422), not a server fault (500).
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "SCHEDULE_INVALID",
                    "message": str(exc),
                    "details": {"constraint": exc.constraint},
                }
            },
        )

    async def connector_not_available_handler(
        _request: Request, exc: Exception
    ) -> JSONResponse:
        if not isinstance(
            exc, ConnectorNotAvailableError
        ):  # pragma: no cover — dispatch guarantees the type
            return await generic_error_handler(_request, exc)
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

    async def spotify_auth_required_handler(
        _request: Request, exc: Exception
    ) -> JSONResponse:
        # Not connected is a precondition the user must resolve (connect Spotify),
        # not a server fault — surface the connect hint instead of an opaque 500.
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "SPOTIFY_AUTH_REQUIRED",
                    "message": str(exc),
                }
            },
        )

    async def lastfm_auth_required_handler(
        _request: Request, exc: Exception
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "LASTFM_AUTH_REQUIRED",
                    "message": str(exc),
                }
            },
        )

    async def connector_not_connected_handler(
        _request: Request, exc: Exception
    ) -> JSONResponse:
        if not isinstance(
            exc, ConnectorNotConnectedError
        ):  # pragma: no cover — dispatch guarantees the type
            return await generic_error_handler(_request, exc)
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "CONNECTOR_NOT_CONNECTED",
                    "message": str(exc),
                    "details": {"connector": exc.connector},
                }
            },
        )

    async def value_error_handler(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": str(exc),
                }
            },
        )

    async def database_error_handler(_request: Request, exc: Exception) -> JSONResponse:
        from src.infrastructure.persistence.database.error_classification import (
            classify_database_error,
        )

        info = classify_database_error(exc)
        logger.error(
            "Database error",
            category=info.category,
            detail=info.detail,
            exc_type=type(exc).__name__,
        )
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "DATABASE_UNAVAILABLE",
                    "message": info.user_message,
                    "details": {"category": info.category},
                }
            },
        )

    app.add_exception_handler(NotFoundError, not_found_handler)
    app.add_exception_handler(OptimisticLockError, optimistic_lock_handler)
    app.add_exception_handler(ConfirmationRequiredError, confirmation_required_handler)
    app.add_exception_handler(WorkflowAlreadyRunningError, workflow_running_handler)
    app.add_exception_handler(ScheduleAlreadyExistsError, schedule_exists_handler)
    app.add_exception_handler(ScheduleInvariantError, schedule_invariant_handler)
    app.add_exception_handler(
        ConnectorNotAvailableError, connector_not_available_handler
    )
    app.add_exception_handler(SpotifyAuthRequiredError, spotify_auth_required_handler)
    app.add_exception_handler(LastfmAuthRequiredError, lastfm_auth_required_handler)
    app.add_exception_handler(
        ConnectorNotConnectedError, connector_not_connected_handler
    )
    app.add_exception_handler(ValueError, value_error_handler)
    app.add_exception_handler(DatabaseError, database_error_handler)
    app.add_exception_handler(Exception, generic_error_handler)
