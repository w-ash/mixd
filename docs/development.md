# Narada Development Guide

## Getting Started

### Setup
```bash
git clone <repository-url> && cd narada
uv sync
cp .env.example .env  # Edit with your service credentials
docker compose up -d   # Start local PostgreSQL (OrbStack or Docker Desktop)
uv run alembic upgrade head
uv run pytest && uv run narada --help  # Verify installation
```

> **New to narada?** See the [Getting Started guide](guides/getting-started.md) for the full walkthrough — prerequisites, credentials, first workflow.

## Quick Reference

- **Commands** → [CLAUDE.md](../CLAUDE.md#essential-commands)
- **Architecture & Patterns** → [architecture/](architecture/README.md)
- **Testing** → [CLAUDE.md](../CLAUDE.md#testing) (self-check + execution tiers), [testing-strategy](dev-setup-guide/testing-strategy.md)
- **Coding Standards** → `.claude/rules/python-conventions.md` (auto-loaded per file)

## Version Management

Version is defined **once** in `pyproject.toml` and derived everywhere else:

```
pyproject.toml  ──→  importlib.metadata.version("narada")
                         │
                         ├── src/__version__
                         ├── FastAPI app.version (app.py)
                         ├── Health endpoint (health.py)
                         └── OpenAPI schema → Orval types
```

**To bump the version:**
```bash
# 1. Edit pyproject.toml (the ONE source of truth)
# 2. Regenerate OpenAPI schema + Orval types:
pnpm --prefix web sync-api
# 3. Update docs/backlog/README.md manually (semantic content)
```

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

## Subagent Usage Guide

See `.claude/skills/subagent-guide/` for the full subagent usage guide — agent descriptions, rotation strategy, when-to-use decision matrix, tool scope table, and best practices.

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
