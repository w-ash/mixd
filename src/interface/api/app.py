"""FastAPI application factory and server entry point.

create_app() builds a configured FastAPI instance with CORS, routers, and
exception handlers. run_server() is the poetry script entry point for
`narada-api`.

When web/dist/ exists (frontend build output), the app also serves the
React SPA with a catch-all fallback to index.html for client-side routing.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.interface.api.middleware import register_exception_handlers
from src.interface.api.routes.health import router as health_router

# Resolve web/dist/ relative to project root (3 levels up from this file)
_WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Wire SSE progress subscriber to the global progress manager."""
    from src.application.services.progress_manager import get_progress_manager
    from src.interface.api.services.progress import (
        SSEProgressSubscriber,
        get_operation_registry,
    )

    registry = get_operation_registry()
    subscriber = SSEProgressSubscriber(registry)
    manager = get_progress_manager()
    sub_id = await manager.subscribe(subscriber)
    yield
    await manager.unsubscribe(sub_id)


def create_app() -> FastAPI:
    """Build and configure the FastAPI application.

    Returns a fully wired FastAPI instance with:
    - CORS configured for the Vite dev server
    - All API routers mounted under /api/v1
    - Global exception-to-error-envelope handlers
    - SSE progress subscriber wired via lifespan
    - Static file serving + SPA catch-all when web/dist/ exists
    """
    app = FastAPI(
        title="Narada",
        description="Personal music metadata hub",
        version="0.3.1",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    # CORS — configurable via CORS_ORIGINS env var or settings.server.cors_origins
    from src.config import settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.server.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Exception → error envelope mapping
    register_exception_handlers(app)

    # Mount routers
    from src.interface.api.routes.connectors import router as connectors_router
    from src.interface.api.routes.imports import router as imports_router
    from src.interface.api.routes.operations import router as operations_router
    from src.interface.api.routes.playlists import router as playlists_router

    app.include_router(health_router, prefix="/api/v1")
    app.include_router(playlists_router, prefix="/api/v1")
    app.include_router(connectors_router, prefix="/api/v1")
    app.include_router(imports_router, prefix="/api/v1")
    app.include_router(operations_router, prefix="/api/v1")

    # Serve built frontend if web/dist/ exists
    _mount_static(app)

    return app


def _mount_static(app: FastAPI) -> None:
    """Mount static files and SPA catch-all when the frontend build exists."""
    if not _WEB_DIST.is_dir():
        return

    index_html = _WEB_DIST / "index.html"
    if not index_html.is_file():
        return

    # Serve Vite's hashed asset bundles
    assets_dir = _WEB_DIST / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    # SPA catch-all: any non-API, non-asset path returns index.html
    # so React Router can handle client-side routing
    @app.get("/{path:path}", include_in_schema=False)
    async def spa_catchall(path: str) -> FileResponse:  # pyright: ignore[reportUnusedFunction]
        # Never intercept API routes — let FastAPI return its own 404/422
        if path.startswith("api/"):
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Not found")

        # Serve actual files from dist/ if they exist (e.g. favicon)
        file_path = _WEB_DIST / path
        if path and file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(index_html))


# Module-level instance for uvicorn CLI: `uvicorn src.interface.api.app:app`
app = create_app()


def run_server() -> None:
    """Poetry script entry point for `narada-api`."""
    import uvicorn

    uvicorn.run(
        "src.interface.api.app:app",
        host="0.0.0.0",  # noqa: S104
        port=8000,
        reload=True,
    )
