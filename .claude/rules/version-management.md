---
paths:
  - "pyproject.toml"
  - "src/__init__.py"
  - "src/interface/api/app.py"
  - "src/interface/api/routes/health.py"
  - "web/openapi.json"
  - "scripts/export_openapi.py"
  - "docs/backlog/README.md"
---
# Version Management

Version lives in `pyproject.toml` only — single source of truth.

`src.__version__` reads it via `importlib.metadata.version("mixd")`. FastAPI `app.version`, the health endpoint, and the OpenAPI schema all derive from `__version__`. Never hardcode version strings.

**Critical**: `importlib.metadata` reads from the *installed* package metadata, not directly from `pyproject.toml`. After changing the version in `pyproject.toml`, you **must** run `uv sync` to update the installed metadata before the new version appears at runtime.

**After bumping `pyproject.toml`:**
1. `uv sync` — updates installed package metadata so `importlib.metadata.version()` returns the new version
2. `pnpm --prefix web sync-api` — exports OpenAPI schema (picks up new version) + runs Orval codegen
3. Verify: `uv run python -c "from src import __version__; print(__version__)"` and `head -7 web/openapi.json`
4. Update docs manually (semantic content, not auto-derivable):
   - `docs/backlog/README.md` — current version + milestone status
   - the relevant version file in `docs/backlog/` (see `docs/backlog/README.md` for the version matrix) — check off completed items with implementation notes
   - `docs/web-ui/` — update implementation status markers on endpoints, user flows, architecture
