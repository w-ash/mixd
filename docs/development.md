# Mixd Development Guide

## Getting Started

### Setup
```bash
git clone <repository-url> && cd mixd
uv sync
cp .env.example .env  # Edit with your service credentials
docker compose up -d   # Start local PostgreSQL (OrbStack or Docker Desktop)
uv run alembic upgrade head
uv run pytest && uv run mixd --help  # Verify installation
```

> **New to mixd?** See the [Getting Started guide](guides/getting-started.md) for the full walkthrough — prerequisites, credentials, first workflow.

## Quick Reference

- **Commands** → [CLAUDE.md](../CLAUDE.md#essential-commands)
- **Architecture & Patterns** → [architecture/](architecture/README.md)
- **Testing** → [CLAUDE.md](../CLAUDE.md#testing) (self-check + execution tiers), [testing-strategy](dev-setup-guide/testing-strategy.md)
- **Coding Standards** → `.claude/rules/python-conventions.md` (auto-loaded per file)

## Version Management

Version is defined **once** in `pyproject.toml` and derived everywhere else:

```
pyproject.toml  ──→  importlib.metadata.version("mixd")
                         │
                         ├── src/__version__
                         ├── FastAPI app.version (app.py)
                         ├── Health endpoint (health.py)
                         └── OpenAPI schema → Orval types
```

**To bump the version:** edit `pyproject.toml` (the ONE source of truth), then follow the full checklist in `.claude/rules/version-management.md` — `uv sync`, `pnpm --prefix web sync-api`, changelog entry, docs updates, git tag, backlog hygiene gate. That rule is the single authoritative procedure; this section deliberately doesn't duplicate it.

## Common Tasks

### CLI Command
1. Create use case in `src/application/use_cases/`
2. Create CLI command in `src/interface/cli/`
3. Wire with dependency injection

### Workflow Node
```python
# Create transform in domain/transforms/ or application/metadata_transforms/
# Register in application/workflows/node_catalog.py

from src.application.workflows.node_catalog import node


@node("sorter.custom_sort", category="sorter")
async def custom_sort_node(tracklist: TrackList, config: dict) -> TrackList:
    # Your sorting logic
    return sorted_tracklist
```

### External Service Connector

See `.claude/skills/new-connector/` for the full step-by-step guide, or run `/new-connector` to invoke it.

### Database Changes
1. Update `src/infrastructure/persistence/database/db_models.py`
2. Generate: `uv run alembic revision --autogenerate`
3. Apply: `uv run alembic upgrade head`

## Shared Utilities (`src/application/use_cases/_shared/`)

Use these utilities to eliminate duplication in playlist-related use cases:

```python
from src.application.use_cases._shared import (
    # Operation counting for playlist diffs
    count_operation_types,  # Returns OperationCounts(added, removed, moved)
    # Type-safe result objects (replace tuple returns)
    OperationCounts,  # Instead of tuple[int, int, int]
    ApiExecutionResult,  # For API operation results
    AppendOperationResult,  # For append operations
    # Fluent metadata builders (replace dict construction)
    PlaylistMetadataBuilder,  # .with_timestamp().with_operations().build()
    build_api_execution_metadata,
    build_database_update_metadata,
    # Validation and error classification
    classify_connector_api_error,  # Pattern matching for API errors
    classify_database_error,
    ConnectorPlaylistUpdateValidator,
    # Playlist item factories
    create_connector_playlist_items_from_tracks,
)
```

**Extract to `_shared/` when**: Logic duplicated in 3+ files (not 2)
**Keep local when**: Single use, domain-specific, or context-dependent

## Logging

### Basic Usage
```python
from src.config.logging import get_logger

logger = get_logger(__name__)
logger.info("Operation complete", batch_size=100, status="success")
```

### Error Handling
```python
from src.config.logging import resilient_operation


@resilient_operation("spotify_sync")
async def sync_playlist(playlist_id: str):
    # Auto-logs timing, errors with HTTP classification
    return await spotify.get_playlist(playlist_id)


@resilient_operation("batch_import", include_timing=False)
async def import_batch(items: list):
    # Skip timing for bulk operations
    return await process_items(items)
```

### Production Configuration
Set environment variables for production safety:
```bash
# Disable sensitive data logging in production
export LOGGING__DIAGNOSE_IN_PRODUCTION=false
export LOGGING__BACKTRACE_IN_PRODUCTION=false

# Configure log management
export LOGGING__ROTATION="50 MB"
export LOGGING__RETENTION="2 weeks"
export LOGGING__FILE_LEVEL="INFO"
```

## Troubleshooting

### Quick Fixes
```bash
# Type errors
uv run basedpyright src/

# Test failures
uv run pytest -v --tb=short --lf

# Database reset
docker compose down -v && docker compose up -d && uv run alembic upgrade head

# Migration status
uv run alembic current
```

## Dependency Security

Dependabot alerts are derived from GitHub's dependency graph, so when graph ingestion breaks, the alerts page still looks populated — it is just wrong. That is the dangerous failure mode, and this repo lived it: **every Dependabot job failed for over two months and nothing said so.**

**The 2026 incident, because the shape of it recurs.** `.gitmodules` pinned `docs/dev-setup-guide` at a commit that existed only on one laptop — committed, never pushed. Dependabot always clones with `--recurse-submodules`, so its clone died at `job_repo_not_found`, and the `update_graph` job failed on every run from 2026-05-10. CI never noticed because `actions/checkout` here uses `submodules: false`. By the time anyone looked, the graph was wrong about 39 package versions, missing 10 outright, and carrying 56 stale extras — while 17 "open" Python alerts described vulnerabilities that had been patched for months.

The lesson is diagnostic, not architectural: **when the graph looks stale, check whether the jobs that populate it are running before theorising about the parser.**

```bash
gh run list --workflow "Dependency Graph"   # Dependabot's own ingestion jobs
git clone --recurse-submodules <repo>       # reproduces what Dependabot does
```

**The guardrail.** `scripts/check_dependency_graph.py` compares what GitHub reports against what `uv.lock` says and exits 1 on drift. `.github/workflows/dependency-graph.yml` runs it weekly. It is deliberately indifferent to *why* the two disagree — broken clone, parser regression, a workflow that quietly stopped — so it keeps working when the next cause is a different one.

```bash
GITHUB_TOKEN="$(gh auth token)" uv run python scripts/check_dependency_graph.py
```

If it reports drift, check Dependabot's jobs first. GitHub also offers a one-shot rescan — **Dependabot alerts tab → settings icon → Refresh Dependabot alerts** — which is UI-only and rate-limited to once an hour.

`.github/dependabot.yml` covers version-update PRs. Those parse manifests directly rather than reading the graph, but they run in the same updater, so a clone failure kills them too.

**Undeclared imports** are a separate failure mode from stale alerts: code that imports a package we only receive transitively gets no resolver signal when the parent drops it. CI's Python job runs `uvx deptry .` (config in `pyproject.toml` `[tool.deptry]`) to fail on that. Two knobs there are load-bearing — `package_module_name_map` for distributions whose import name differs from their PyPI name (`python-dotenv` → `dotenv`, `pyjwt` → `jwt`, `python-multipart` → `multipart`), mandatory because `uvx` runs deptry isolated from our venv and it cannot infer them; and a single `DEP002` ignore for `python-multipart`, which base `fastapi` needs at request time for `UploadFile` parsing but which no import graph can see.

```bash
uvx deptry .
```

**Transitive vulnerabilities** are pinned as security floors in `web/pnpm-workspace.yaml` `overrides`, each with a comment saying what pulls the package and why the floor is safe against the parent's declared range. Alerts that cannot be fixed are documented there too rather than dismissed — `better-auth` is the standing example — the reason it cannot be floored, and the evidence that its open alerts are unreachable from our client-only usage, is written out in `web/pnpm-workspace.yaml` beside the override block.

## Subagents and Skills

Two subagents run in isolated context — `log-diagnostician` (reads structured JSON logs without flooding the main conversation) and `workflow-manager` (drives the `mixd workflow` CLI). Specialist skills in `.claude/skills/` load their guidance only when invoked.

Both directories are self-describing: each file's `description:` frontmatter states when to reach for it, and Claude Code surfaces those descriptions every session. Read `.claude/agents/` and `.claude/skills/` directly rather than maintaining a separate catalogue.

---

## Resources

### Core Documentation
- **[CLAUDE.md](../CLAUDE.md)** - Essential commands and coding standards
- **[Architecture](architecture/README.md)** - System design and patterns
- **[Database Schema](architecture/database.md)** - Schema reference
- **[Backlog](backlog/)** - Project roadmap

### External References
- [SQLAlchemy 2.0](https://docs.sqlalchemy.org/en/20/) - Database ORM
- [Typer](https://typer.tiangolo.com/) - CLI framework
- [BasedPyright](https://github.com/DetachHead/basedpyright) - Type checker
