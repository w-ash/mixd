---
paths:
  - "pyproject.toml"
  - "src/__init__.py"
  - "src/interface/api/app.py"
  - "src/interface/api/routes/health.py"
  - "web/openapi.json"
  - "scripts/export_openapi.py"
  - "ROADMAP.md"
---
# Version Management

Version lives in `pyproject.toml` only — single source of truth.

`src.__version__` reads it via `importlib.metadata`. FastAPI `app.version`, the health endpoint, and the OpenAPI schema all derive from `__version__`. Never hardcode version strings.

**After bumping `pyproject.toml`:**
1. `pnpm --prefix web sync-api` — exports OpenAPI schema + runs Orval codegen
2. Update `ROADMAP.md` manually (semantic content, not auto-derivable)
