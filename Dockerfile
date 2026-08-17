# =============================================================================
# Mixd — Multi-stage production build
# =============================================================================
# Stage 1: Python dependencies via uv
# Stage 2: Frontend build via pnpm
# Stage 3: Minimal runtime image
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: Python builder (dependencies only — project source copied in runtime)
# ---------------------------------------------------------------------------
# Keep this tag in step with `required-version` in pyproject.toml — uv rejects a
# uv.lock whose schema is newer than it understands, so a stale tag here fails the
# `uv sync --locked` below at deploy time, long after CI went green.
FROM ghcr.io/astral-sh/uv:0.12-python3.14-trixie-slim AS python-builder

WORKDIR /app

# uv best practices for containers
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Install dependencies only (not the project itself — source is COPY'd in runtime)
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

# ---------------------------------------------------------------------------
# Stage 2: Node builder
# ---------------------------------------------------------------------------
FROM node:24-slim AS node-builder

WORKDIR /app/web

# Build-time args injected by CI / fly deploy
ARG VITE_NEON_AUTH_URL=""
ARG BUILD_HASH="dev"
ENV VITE_NEON_AUTH_URL=$VITE_NEON_AUTH_URL \
    BUILD_HASH=$BUILD_HASH

# Install pnpm globally — pin to the version declared in web/package.json's
# `packageManager` field. Drift here caused the v0.7.8 deploy to fail with
# `settings.onlyBuiltDependencies.push is not a function` against a workspace
# config that pnpm v11 tolerates and pnpm v10 doesn't.
RUN npm install -g pnpm@11.20.0

# Install dependencies first (layer cache)
COPY web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml ./
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile

# Copy source and build (tsc + vite build → dist/)
# pyproject.toml needed by vite.config.ts to read the app version
COPY pyproject.toml /app/pyproject.toml
COPY web/ ./
RUN pnpm build

# ---------------------------------------------------------------------------
# Stage 3: Runtime
# ---------------------------------------------------------------------------
FROM python:3.14-slim-trixie AS runtime

LABEL org.opencontainers.image.source="https://github.com/w-ash/mixd" \
      org.opencontainers.image.description="Personal music metadata hub" \
      org.opencontainers.image.licenses="AGPL-3.0-only"

# Create non-root user
RUN groupadd --gid 1000 mixd && \
    useradd --uid 1000 --gid mixd --create-home mixd

WORKDIR /app

# Copy Python venv from builder (dependencies only — no project source)
COPY --chown=mixd:mixd --from=python-builder /app/.venv /app/.venv

# Copy source tree (needed for imports and _WEB_DIST path resolution)
COPY --chown=mixd:mixd src/ src/

# Copy Alembic (migrations run via release_command or entrypoint)
COPY --chown=mixd:mixd alembic/ alembic/
COPY --chown=mixd:mixd alembic.ini ./

# pyproject.toml needed at runtime for version reading via tomllib
COPY --chown=mixd:mixd pyproject.toml ./

# Copy built frontend (served by FastAPI static file mount)
COPY --chown=mixd:mixd --from=node-builder /app/web/dist web/dist/

# Create data directory for logs/imports (ephemeral in Fly.io)
RUN mkdir -p /app/data && chown mixd:mixd /app/data

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    LOGGING__LOG_FILE=/app/data/mixd.log

USER mixd
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')"]

# --timeout-graceful-shutdown bounds uvicorn's wait for in-flight requests. It
# defaults to None (unbounded), and uvicorn drains connections *before* running
# lifespan shutdown — so a long-lived SSE stream that only ends on a
# lifespan-pushed sentinel deadlocks the two against each other until Fly's
# kill_timeout (300s) SIGKILLs the machine. 30s breaks that unconditionally and
# leaves the rest of the drain window as headroom — the lifespan's own task drain
# runs after this window, not inside it.
#
# --limit-max-requests recycles the worker with no signal at all, so the
# shutdown flag the SSE streams poll never gets set on that path; this timeout is
# its only bound. Accepted: a recycle is not a deploy, and 30s is well inside the
# kill_timeout.
CMD ["uvicorn", "src.interface.api.app:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", \
     "--no-access-log", \
     "--timeout-graceful-shutdown", "30", \
     "--limit-concurrency", "50", \
     "--limit-max-requests", "5000"]
