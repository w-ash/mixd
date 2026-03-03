"""Global exception handling middleware.

Maps Python exceptions to structured JSON error envelopes so the frontend
always receives a consistent error shape regardless of what goes wrong.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.config import get_logger
from src.domain.exceptions import NotFoundError

logger = get_logger(__name__)


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
