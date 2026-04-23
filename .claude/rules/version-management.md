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

**Critical**: `importlib.metadata` reads from the *installed* package metadata, not directly from `pyproject.toml`. After changing the version in `pyproject.toml`, run `uv sync` to update the installed metadata before the new version appears at runtime.

## Four-segment scheme — `major.minor.feature.revision`

| Segment | Example | Role | When to bump |
|---|---|---|---|
| **major** | `0.` | Era / breaking | Manually; `0 → 1` when the v1.0 feature set is complete |
| **minor** | `.7.` | Thematic cycle | Per backlog's minor-version plan (e.g., `0.7.x` = personal metadata theme) |
| **feature** | `.5` | Named feature deliverable within the cycle | Per backlog's feature plan (e.g., `0.7.5` = workflow integration & quick filters) |
| **revision** | `.1` | Post-ship revision of the feature | Every ship AFTER initial `X.Y.Z` goes live, until the user confirms stability |

Mapping note: `feature` and `revision` are mixd's names. A .NET developer would recognize `revision` from `Major.Minor.Build.Revision`; mixd's third segment carries more weight than .NET's `Build` because each feature is a named deliverable.

### Why four segments (not `.postN`)

PEP 440's `.postN` is reserved for **metadata-only fixes** (typo in a PyPI description, wrong classifier). Historical versions `0.7.5.post1` and `0.7.5.post2` shipped code iterations under that suffix — a misuse. Going forward:

- `.postN` is unused by mixd.
- Post-ship code revisions increment the **revision** segment (`0.7.5.1`, `0.7.5.2`, ...), regardless of size. A one-line fix and a 2000-line addition both ship as a revision bump. The size lives in the commit message (`fix:` / `feat:` / `refactor:` prefix) and `git diff`, not the version.
- If the next ship on v0.7.5 is the third post-release change, it is `0.7.5.3` (continuing from the historical `.post2` count).

### When to bump which segment

- First ship of a planned feature (e.g., `0.7.6` Spotify Flow Polish): bump **feature**, drop **revision**.
- Post-ship revision (fixes, prod-discovered capabilities, UX refinements): bump **revision**.
- New minor theme (`0.7.x` → `0.8.x`): bump **minor**, reset feature + revision.
- Feature closeout (user confirms stability): no version change; the backlog row moves from `🚀 Shipped` to `✅ Completed`. Full lifecycle in the `backlog-format` rule.

## After bumping `pyproject.toml`

1. `uv sync` — updates installed package metadata so `importlib.metadata.version()` returns the new version.
2. `pnpm --prefix web sync-api` — exports OpenAPI schema (picks up new version) + runs Orval codegen.
3. Verify: `uv run python -c "from src import __version__; print(__version__)"` and `head -7 web/openapi.json`.
4. Update docs (semantic content, not auto-derivable):
   - `docs/backlog/README.md` — `Current Version` header + feature status cell.
   - The relevant version file in `docs/backlog/` — check off completed items with implementation notes; leave the Post-Deploy Revisions epic open until feature closeout.
   - `docs/web-ui/` — update implementation status markers on endpoints, user flows, architecture.
