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

from collections.abc import Awaitable, Callable
from typing import cast

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DatabaseError

from src.application.workflows.definition.validation import ConnectorNotAvailableError
from src.config import get_logger
from src.domain.exceptions import (
    AppleMusicAuthRequiredError,
    ConfirmationRequiredError,
    ConnectorNotConnectedError,
    ConnectorScopeMissingError,
    DiscogsAuthRequiredError,
    DiscogsInvalidTokenError,
    InvalidApiKeyError,
    LastfmAuthRequiredError,
    NotFoundError,
    OptimisticLockError,
    ScheduleAlreadyExistsError,
    ScheduleInvariantError,
    SpotifyAuthRequiredError,
    SpotifyQuotaExhaustedError,
    TidalAuthRequiredError,
    ToolExecutionError,
    WorkflowAlreadyRunningError,
)
from src.interface.api.error_codes import CHAT_ERROR_CODES

logger = get_logger(__name__)

# Connector credential errors → code+status envelopes, registered in a loop
# via ``_simple_handler`` (the CHAT_ERROR_CODES pattern). The 409 auth family:
# not-connected is a precondition the user must resolve (connect the service),
# not a server fault — surface the connect hint instead of an opaque 500.
# Tidal's not-connected and dead-grant (the reauth subclass) both resolve with
# the same reconnect remedy. ``DiscogsInvalidTokenError`` is the one 400:
# connect-time token validation failure (PUT /connectors/discogs/token) the
# token form renders inline, distinct from the parent class's 409 "stored
# credential unusable" — Starlette dispatches on the most specific registered
# class, so the subclass handler wins.
CONNECTOR_AUTH_ERROR_CODES: dict[type[Exception], tuple[str, int]] = {
    SpotifyAuthRequiredError: ("SPOTIFY_AUTH_REQUIRED", 409),
    LastfmAuthRequiredError: ("LASTFM_AUTH_REQUIRED", 409),
    AppleMusicAuthRequiredError: ("APPLE_MUSIC_AUTH_REQUIRED", 409),
    DiscogsAuthRequiredError: ("DISCOGS_AUTH_REQUIRED", 409),
    TidalAuthRequiredError: ("TIDAL_AUTH_REQUIRED", 409),
    DiscogsInvalidTokenError: ("DISCOGS_INVALID_TOKEN", 400),
}


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

    async def spotify_quota_exhausted_handler(
        _request: Request, exc: Exception
    ) -> JSONResponse:
        # PDR-003 quota exhaustion is a service-wide outage of the pooled
        # developer account — not a caller fault, not per-user rate limiting,
        # and not fixable by the 409 auth family's reconnect remedy. 503 tells
        # clients the service is temporarily unable; the distinct code lets
        # the frontend say why without parsing the message.
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "SPOTIFY_QUOTA_EXHAUSTED",
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

    async def connector_scope_missing_handler(
        _request: Request, exc: Exception
    ) -> JSONResponse:
        if not isinstance(
            exc, ConnectorScopeMissingError
        ):  # pragma: no cover — dispatch guarantees the type
            return await generic_error_handler(_request, exc)
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "CONNECTOR_SCOPE_MISSING",
                    "message": str(exc),
                    "details": {
                        "connector": exc.connector,
                        "missing_scopes": exc.missing_scopes,
                    },
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
        from src.interface._shared.error_classification import (
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

    async def request_validation_handler(
        _request: Request, exc: Exception
    ) -> JSONResponse:
        """FastAPI's default 422 shape, minus each error's ``input`` echo.

        The default handler reflects the offending value back in every error
        dict — for write-only credential bodies (the Discogs token PUT) that
        would put the submitted secret into a response body/log. Stripping
        ``input`` globally keeps the loc/msg/type triple clients key on while
        never echoing what was posted.
        """
        if not isinstance(
            exc, RequestValidationError
        ):  # pragma: no cover — dispatch guards
            return await generic_error_handler(_request, exc)
        # fastapi types errors() as Sequence[Any]; pydantic documents the
        # real shape as error dicts — one cast pins it to object values.
        raw_errors = cast("list[dict[str, object]]", exc.errors())
        errors = [
            {key: value for key, value in error.items() if key != "input"}
            for error in raw_errors
        ]
        return JSONResponse(
            status_code=422, content={"detail": jsonable_encoder(errors)}
        )

    def _simple_handler(
        exc_type: type[Exception], status_code: int, code: str
    ) -> Callable[[Request, Exception], Awaitable[JSONResponse]]:
        """Build a code+status envelope handler for a simple domain exception."""

        async def handler(_request: Request, exc: Exception) -> JSONResponse:
            if not isinstance(exc, exc_type):  # pragma: no cover — dispatch guards
                return await generic_error_handler(_request, exc)
            return JSONResponse(
                status_code=status_code,
                content={"error": {"code": code, "message": str(exc)}},
            )

        return handler

    app.add_exception_handler(NotFoundError, not_found_handler)
    app.add_exception_handler(OptimisticLockError, optimistic_lock_handler)
    # Chat assistant (v0.9.0): pre-stream errors surface as the HTTP error
    # envelope (in-stream errors become SSE `error` events; see chat_sse.py).
    # The code/status table is shared with chat_sse so a code can't drift between
    # the two paths (see error_codes.CHAT_ERROR_CODES).
    for exc_type, (code, status_code) in CHAT_ERROR_CODES.items():
        app.add_exception_handler(
            exc_type, _simple_handler(exc_type, status_code, code)
        )
    # ToolExecutionError at confirm time (pre-stream) — 422 with the same code
    # string the SSE path emits, so the frontend handles one code on both paths.
    app.add_exception_handler(
        ToolExecutionError,
        _simple_handler(ToolExecutionError, 422, "TOOL_EXECUTION_ERROR"),
    )
    app.add_exception_handler(
        InvalidApiKeyError,
        _simple_handler(InvalidApiKeyError, 400, "INVALID_API_KEY"),
    )
    app.add_exception_handler(ConfirmationRequiredError, confirmation_required_handler)
    app.add_exception_handler(WorkflowAlreadyRunningError, workflow_running_handler)
    app.add_exception_handler(ScheduleAlreadyExistsError, schedule_exists_handler)
    app.add_exception_handler(ScheduleInvariantError, schedule_invariant_handler)
    app.add_exception_handler(
        ConnectorNotAvailableError, connector_not_available_handler
    )
    app.add_exception_handler(
        SpotifyQuotaExhaustedError, spotify_quota_exhausted_handler
    )
    # Connector credential errors — see CONNECTOR_AUTH_ERROR_CODES for the
    # status/code rationale per entry.
    for exc_type, (code, status_code) in CONNECTOR_AUTH_ERROR_CODES.items():
        app.add_exception_handler(
            exc_type, _simple_handler(exc_type, status_code, code)
        )
    app.add_exception_handler(
        ConnectorNotConnectedError, connector_not_connected_handler
    )
    app.add_exception_handler(
        ConnectorScopeMissingError, connector_scope_missing_handler
    )
    app.add_exception_handler(RequestValidationError, request_validation_handler)
    app.add_exception_handler(ValueError, value_error_handler)
    app.add_exception_handler(DatabaseError, database_error_handler)
    app.add_exception_handler(Exception, generic_error_handler)
