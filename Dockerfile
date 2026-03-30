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
FROM ghcr.io/astral-sh/uv:0.10-python3.14-trixie-slim AS python-builder

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
FROM node:22-slim AS node-builder

WORKDIR /app/web

# Build-time args injected by CI / fly deploy
ARG VITE_NEON_AUTH_URL=""
ARG BUILD_HASH="dev"
ENV VITE_NEON_AUTH_URL=$VITE_NEON_AUTH_URL \
    BUILD_HASH=$BUILD_HASH

# Install pnpm globally (simpler than corepack for build stages)
RUN npm install -g pnpm@10

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
    PREFECT_SERVER_ALLOW_EPHEMERAL_MODE=true \
    LOGGING__LOG_FILE=/app/data/mixd.log

USER mixd
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')"]

CMD ["uvicorn", "src.interface.api.app:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--access-log"]
