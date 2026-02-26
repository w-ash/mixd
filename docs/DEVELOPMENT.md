# Narada Development Guide

## Getting Started

### Setup
```bash
git clone <repository-url> && cd narada
poetry install && source $(poetry env info --path)/bin/activate
cp .env.example .env  # Edit with your service credentials
poetry run alembic upgrade head
poetry run pytest && poetry run narada --help  # Verify installation
```

## Essential Commands
See `CLAUDE.md` for complete reference:

```bash
# Testing (Fast Development Workflow)
poetry run pytest                               # Fast tests (skip slow/diagnostic, <1min)
poetry run pytest -m "unit"                     # Unit tests only (<10s)
poetry run pytest tests/unit/domain/            # Fast domain tests
poetry run pytest tests/integration/            # Database integration tests
poetry run pytest --cov=narada --cov-report=html # Coverage

# Testing (Complete/CI Workflow)
poetry run pytest -m ""                         # ALL tests including slow/diagnostic
poetry run pytest -m "slow"                     # Slow tests only
poetry run pytest -m "diagnostic"               # Diagnostic/profiling tests only

# Quality
poetry run ruff check . --fix --unsafe-fixes    # Modern Python 3.14+ linting
poetry run ruff format .                        # Format
poetry run basedpyright src/                    # Type check

# Database
poetry run alembic revision --autogenerate     # Generate migration
poetry run alembic upgrade head                # Apply migrations
```

## Architecture

### DDD + Hexagonal Layers
**Dependency Flow**: Interface → Application → Domain ← Infrastructure

```
src/
├── config/                  # Settings, logging, constants
│
├── domain/                  # Pure business logic (zero dependencies)
│   ├── entities/           # DDD aggregates (Track, Playlist)
│   └── repositories/       # Abstract repository protocols
│
├── application/            # Use cases and orchestration
│   ├── services/          # Application services
│   └── use_cases/         # DDD use cases
│
├── infrastructure/        # External adapters
│   ├── connectors/       # External service adapters
│   └── persistence/      # Database adapters
│
└── interface/            # Primary adapters
    └── cli/             # CLI entry points
```

### Key Files
- `src/domain/entities/track.py` - Core Track/Artist entities
- `src/domain/entities/playlist.py` - Playlist domain objects  
- `src/infrastructure/persistence/database/db_models.py` - SQLAlchemy models
- `src/infrastructure/persistence/repositories/` - Repository implementations

## Core Patterns

### Repository Pattern
Domain defines ports, infrastructure provides adapters:
```python
# Domain protocol (port)
class TrackRepository(Protocol):
    async def save_batch(self, tracks: list[Track]) -> list[Track]: ...

# Infrastructure adapter  
class SQLAlchemyTrackRepository:
    async def save_batch(self, tracks: list[Track]) -> list[Track]:
        # SQLAlchemy implementation
```

### UnitOfWork Pattern
Transaction boundary control:
```python
async with get_unit_of_work() as uow:
    track_repo = uow.get_track_repository()
    await track_repo.save_batch(tracks)
    await uow.commit()
```

## Testing Strategy

### Test Pyramid (Speed & Coverage) - 2026 Best Practice
- **Unit Tests** (`tests/unit/`) - Fast, isolated, 60%+ of test suite, <100ms each
- **Integration Tests** (`tests/integration/`) - Real database/APIs, 35% of test suite  
- **E2E Tests** - Critical user workflows, 5% of test suite

### Test Structure (Modern pytest Organization)
```
tests/
├── conftest.py                    # Root fixtures: db_session, test_data_tracker
├── unit/                          # Fast, isolated tests (<100ms)
│   ├── domain/                   # Pure business logic tests
│   ├── application/              # Use cases with mocked repositories
│   ├── infrastructure/           # Connector logic with mocks
│   ├── config/                   # Config layer tests
│   └── interface/                # CLI command tests
├── integration/                  # Real database, external services
│   ├── connectors/              # External service integration tests
│   ├── repositories/            # Database integration tests
│   ├── use_cases/               # End-to-end use case tests
│   └── workflows/               # Workflow execution tests
├── fixtures/                     # Shared test data models
├── diagnostics/                  # Diagnostic and investigation tests
└── data/                        # Test data files
```

### Fixture Organization
- **Root** (`tests/conftest.py`) - `db_session`, `test_data_tracker` with automatic cleanup
- **Unit** (`tests/unit/*/conftest.py`) - Mocked repositories and services by layer
- **Integration** (`tests/integration/conftest.py`) - Real database fixtures with cleanup
- **Shared** (`tests/fixtures/`) - Test data models and factory functions

### Test Execution Workflow (2025 Best Practice)

Tests are organized by speed to optimize development feedback loops:

**Fast Development Loop** (default, <1 minute):
```bash
poetry run pytest                    # Skips slow and diagnostic tests
poetry run pytest -m "unit"          # Unit tests only (<10s)
```

**Specific Test Categories**:
```bash
# By marker
poetry run pytest -m "slow"          # Slow tests only (>1s each)
poetry run pytest -m "performance"   # Performance tests only (>5s each)
poetry run pytest -m "diagnostic"    # Diagnostic/profiling tests

# By layer
poetry run pytest -m "integration and not slow"  # Fast integration tests
poetry run pytest tests/unit/                    # All unit tests
poetry run pytest tests/integration/             # All integration tests

# By domain
poetry run pytest -m "matching"      # Track matching tests
poetry run pytest -m "connector"     # Connector tests
```

**Complete Test Suite** (CI/CD):
```bash
poetry run pytest -m ""              # Run ALL tests including slow/diagnostic
```

**Marker Definitions**:
- `unit`: Fast, isolated tests (<100ms each)
- `integration`: Real DB/APIs (<1s each)
- `slow`: Tests taking >1s (skipped by default)
- `performance`: Tests taking >5s (skipped by default)
- `diagnostic`: Investigation/profiling tests (skipped by default)

### Testing Patterns

#### Domain Tests (Pure Logic)
```python
# Pure business logic with no external dependencies
def test_track_matching_algorithm():
    matcher = TrackMatcher()
    result = matcher.calculate_confidence("Song Title", "Song Title")
    assert result >= 0.95
```

#### Integration Tests (Real Database)
```python
# Repository with database and automatic cleanup
@pytest.mark.asyncio
async def test_track_persistence(db_session, test_data_tracker):
    uow = get_unit_of_work(db_session)
    track = Track(title="TEST_Song", artists=[Artist(name="TEST_Artist")])
    saved = await uow.get_track_repository().save_track(track)
    test_data_tracker.add_track(saved.id)  # Auto-cleanup
    
    found = await uow.get_track_repository().get_by_id(saved.id)
    assert found.title == "TEST_Song"
```

#### E2E Tests (Complete Workflows)
```python  
# Full CLI command integration
def test_import_command(cli_runner):
    result = cli_runner.run(["likes", "import-spotify", "--limit", "5"])
    assert result.exit_code == 0
    assert "✓ Spotify likes import completed" in result.output
```

### Test Data Fixtures
Use `tests/fixtures/models.py` for test data creation:
```python
from tests.fixtures.models import create_test_track, create_test_playlist

# Create test data with sensible defaults
track = create_test_track(title="Test", spotify_id="123")
playlist = create_test_playlist(name="Test Playlist")
```

### Test Commands
```bash
# Fast development feedback (skips slow tests by default)
poetry run pytest                               # Fast tests (<1min)
poetry run pytest -x                            # Stop on first failure
poetry run pytest --lf                          # Run last failed tests only
poetry run pytest tests/unit/domain/            # Pure business logic (fastest)

# Specific test categories
poetry run pytest -m "unit"                     # Unit tests only
poetry run pytest -m "integration and not slow" # Fast integration tests
poetry run pytest -m "slow"                     # Slow tests only
poetry run pytest -m "diagnostic"               # Diagnostic/profiling tests

# Complete test suite (CI/CD)
poetry run pytest -m ""                         # ALL tests including slow/diagnostic
poetry run pytest tests/integration/            # All integration tests

# Coverage and quality
poetry run pytest --cov=src --cov-report=html   # Coverage report
poetry run pytest --durations=20                # Find slowest 20 tests
poetry run pytest -k "test_track_"              # Focus on specific functionality
```

## Adding Features

### Domain-First Development
1. **Domain Entity** - Pure business logic with no dependencies
2. **Repository Protocol** - Abstract interface in domain layer  
3. **Use Case** - Application orchestration with dependency injection
4. **Infrastructure** - Repository implementation and CLI command
5. **Tests** - Domain → Application → Integration

```python
# 1. Domain entity (src/domain/entities/)
@define(frozen=True, slots=True)
class NewEntity:
    name: str
    value: int

# 2. Repository protocol (src/domain/repositories/)  
class NewEntityRepository(Protocol):
    async def save(self, entity: NewEntity) -> NewEntity: ...

# 3. Use case (src/application/use_cases/)
class NewFeatureUseCase:
    def __init__(self, repository: NewEntityRepository):
        self.repository = repository
```

## Project Tracking

### Using the WORK Template (2025 Best Practice)

For tactical work tracking on individual epics/initiatives:

```bash
# Copy template to .claude/work/ (auto-loaded by Claude Code)
cp WORK_TEMPLATE.md .claude/work/WORK.md
```

**Template Features (2025 Best Practices)**:
- **ADR-Lite Format** - Structured architecture decision records for AI and humans
- **AI Collaboration Tracking** - Agent logs, context boundaries, decision tracking
- **Work Type Guidance** - Conditional sections for user-facing, backend, or devops work
- **Smart Status Tags** - Progress, type, component, and version tracking

**When to Use Each Section**:

**For User-Facing Work** (`#user-facing`):
- Fill in "User Stories & Scenarios" section
- Emphasize examples in "User-Facing Changes & Examples"
- Add "User Impact" to testing strategy

**For Backend/Technical Work** (`#backend`, `#refactor`):
- Fill in "System Behavior Contract" (what must not break)
- Emphasize "Implementation Details" and architectural layers
- Focus testing on regression and performance

**For DevOps/Infrastructure** (`#devops`):
- Fill in "Deployment Impact" section
- Add "Rollback Strategy" for high-risk changes
- Document downtime and risk assessment

**AI Collaboration Sections**:
1. **Agent Assistance Log** - Track which specialized agents helped (Explore, Plan, sqlalchemy-query-expert)
2. **Context Boundaries** - Document critical files, concepts, and prerequisites for future sessions
3. **AI-Assisted Decisions** - Maintain transparency on AI suggestions vs human decisions

**After Completion**:
```bash
# Archive completed work (preserves context for future reference)
mv .claude/work/WORK.md docs/work-archive/WORK-$(date +%Y%m%d)-epic-name.md
```

**Strategic Planning**: For roadmap and version planning, see `ROADMAP.md`

## Common Tasks

### CLI Command
1. Create use case in `src/application/use_cases/`
2. Create CLI command in `src/interface/cli/`
3. Wire with dependency injection

### Workflow Node
```python
# Create transform in domain/transforms/ or application/transforms/
# Register in application/workflows/node_catalog.py

from src.application.workflows.node_catalog import node

@node("sorter.custom_sort", category="sorter")
async def custom_sort_node(tracklist: TrackList, config: dict) -> TrackList:
    # Your sorting logic
    return sorted_tracklist
```

### External Service Connector
```python
# 1. Define Pydantic models for API response shapes
# src/infrastructure/connectors/new_service/models.py
class NewServiceBaseModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

class NewServiceTrack(NewServiceBaseModel):
    id: str
    name: str
    # ... typed fields matching API JSON shape

# 2. Validate raw dict → typed model at the API client boundary
# src/infrastructure/connectors/new_service/client.py
class NewServiceAPIClient(BaseAPIClient):
    async def get_track(self, track_id: str) -> NewServiceTrack | None:
        data = response.json()
        return NewServiceTrack.model_validate(data)  # Validate here

# 3. Connector facade delegates to typed client + conversions
# src/infrastructure/connectors/new_service/connector.py
class NewServiceConnector(BaseAPIConnector):
    @property
    def connector_name(self) -> str:
        return "new_service"

    def convert_track_to_connector(self, track_data: dict[str, Any]) -> ConnectorTrack:
        from .conversions import convert_new_service_track
        return convert_new_service_track(track_data)

# 4. Conversions receive typed models, not raw dicts
# src/infrastructure/connectors/new_service/conversions.py
def convert_new_service_track(data: dict[str, Any] | NewServiceTrack) -> ConnectorTrack:
    track = NewServiceTrack.model_validate(data) if isinstance(data, dict) else data
    # All access is typed — no isinstance() guards needed

# 5. Matching provider works with typed models
# src/infrastructure/connectors/new_service/matching_provider.py
class NewServiceMatchingProvider(BaseMatchingProvider):
    # Implement matching logic using typed models from client
```

### Database Changes
1. Update `src/infrastructure/persistence/database/db_models.py`
2. Generate: `poetry run alembic revision --autogenerate`
3. Apply: `poetry run alembic upgrade head`

## Code Standards

### Principles
- **Ruthlessly DRY** - No code duplication in single-maintainer codebase
- **Batch-First** - Design for collections, single items are degenerate cases
- **Immutable Domain** - Pure transformations with no side effects
- **Python 3.14+** - Modern syntax, type safety, strict typing

### Conventions
- 88-character lines, double quotes, Google docstrings
- Type everything: `list[str]`, `dict[str, Any]`, generics `[T, R]`
- UTC timestamps: `datetime.now(UTC)`
- Dependency injection in use cases

### Shared Utilities (`src/application/use_cases/_shared/`)

Use these utilities to eliminate duplication in playlist-related use cases:

```python
from src.application.use_cases._shared import (
    # Operation counting for playlist diffs
    count_operation_types,           # Returns OperationCounts(added, removed, moved)

    # Type-safe result objects (replace tuple returns)
    OperationCounts,                 # Instead of tuple[int, int, int]
    ApiExecutionResult,              # For API operation results
    AppendOperationResult,           # For append operations

    # Fluent metadata builders (replace dict construction)
    PlaylistMetadataBuilder,         # .with_timestamp().with_operations().build()
    build_api_execution_metadata,
    build_database_update_metadata,

    # Validation and error classification
    classify_connector_api_error,    # Pattern matching for API errors
    classify_database_error,
    ConnectorPlaylistUpdateValidator,

    # Playlist item factories
    create_connector_playlist_items_from_tracks,
)
```

**Extract to `_shared/` when**: Logic duplicated in 3+ files (not 2)
**Keep local when**: Single use, domain-specific, or context-dependent

### Intentional "Duplication" (Don't Extract)

Some patterns repeat by design in hexagonal architecture:

**Command/Result Classes** - Each use case has its own types
```python
# ✅ Correct: Each use case defines its own Command/Result
@define(frozen=True)
class GetPlayedTracksCommand:  # Specific to this use case
    limit: int
    days_back: int | None
```

**Transaction Boundaries** - Each use case manages its own UoW
```python
# ✅ Correct: Use case controls transaction lifecycle
async with uow:
    result = await self._execute_operation(...)
    await uow.commit()  # Business logic decides when
```

**Context-Specific Error Handling** - Preserves valuable debugging info
```python
# ✅ Correct: Context-specific error messages
except Exception as e:
    logger.error("Playlist sync failed", playlist_id=id, error=str(e))
    # Don't extract - context matters for debugging
```

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
poetry run basedpyright src/

# Test failures  
poetry run pytest -v --tb=short --lf

# Database reset
rm data/narada.db && poetry run alembic upgrade head

# Migration status
poetry run alembic current
```

## Subagent Usage Guide

Narada uses specialized Claude Code subagents for deep technical expertise. Main agent delegates to subagents for advisory consultation, then implements with full context.

### Available Subagents (5 Total, 3 Active at a Time)

**Backend Agents**:
1. **sqlalchemy-async-optimizer** - SQLAlchemy 2.0 async patterns, SQLite concurrency, N+1 query prevention
2. **architecture-guardian** - Clean Architecture + DDD enforcement (backend + frontend)
3. **test-pyramid-architect** - pytest strategy, async test debugging, 60/35/5 pyramid balance

**Frontend Agents** (v0.5.0+):
4. **react-architecture-specialist** - React + TypeScript patterns, Tanstack Query, performance optimization
5. **vitest-strategy-architect** - Vitest component testing, React Testing Library, Playwright E2E

### Rotation Strategy (Maximize 3 Active)

**Current Phase** → **Active Agents**:

**Backend-Heavy Development** (Now → v0.4.0):
- ✅ sqlalchemy-async-optimizer
- ✅ architecture-guardian
- ✅ test-pyramid-architect

**Frontend-Heavy Development** (v0.5.0):
- ✅ architecture-guardian (universal - always useful)
- ✅ react-architecture-specialist
- ✅ vitest-strategy-architect

**Full-Stack Development** (v0.6.0+):
- ✅ architecture-guardian (always active)
- ✅ 2 domain-specific agents (backend or frontend based on current task)

### When to Use Each Agent

#### sqlalchemy-async-optimizer
**Use when**:
- Designing repository methods with complex joins/relationships
- Debugging "database locked" errors
- Optimizing `selectinload()` strategies
- Implementing batch operations efficiently

**Example invocation**:
> "I need to fetch playlists with all their tracks. How should I structure the query to avoid N+1 problems?"

**Output**: Query design with `selectinload()`, rationale, performance implications

#### architecture-guardian
**Use when**:
- Reviewing new use cases before implementation
- Validating refactors across multiple layers
- Self-review for architectural violations
- Designing adapters for new services

**Example invocation**:
> "Review this use case for Clean Architecture violations: Does it import from infrastructure? Are repository protocols used correctly?"

**Output**: ✅ Approved / ⚠️ Approved with suggestions / ❌ Rejected with specific violations

#### test-pyramid-architect
**Use when**:
- Designing test coverage for new features
- Debugging flaky async tests (SQLite locks, task cleanup)
- Ensuring proper fixture usage (`db_session` vs `get_session()`)
- Maintaining 60/35/5 test pyramid ratio

**Example invocation**:
> "Design test strategy for SyncPlaylistUseCase. What's the unit/integration split?"

**Output**: Test plan with unit/integration breakdown, fixture recommendations, test case outlines

#### react-architecture-specialist (v0.5.0+)
**Use when**:
- Designing component hierarchies
- Reviewing Tanstack Query patterns (cache configuration, stale-while-revalidate)
- Performance optimization (React.memo, useMemo, useCallback)
- State management strategy (context vs props vs query state)

**Example invocation**:
> "Should my TrackList component fetch tracks from the API or receive them as props?"

**Output**: Component architecture design with container/presentational split, Tanstack Query configuration

#### vitest-strategy-architect (v0.5.0+)
**Use when**:
- Designing component test strategy
- Debugging flaky async component tests
- Mocking Tanstack Query in tests
- Planning E2E test scenarios (Chromium desktop only)

**Example invocation**:
> "How should I test the PlaylistCard component? What's the right mix of component tests vs integration tests?"

**Output**: Test strategy with component/integration split, mocking patterns, test case outlines

### Subagent Response Pattern

All subagents follow this structure:

1. **Analyze Context** - Understand the specific challenge
2. **Provide Solution** - Concrete, implementable recommendations with code examples
3. **Explain Rationale** - Why this approach, performance/architectural implications
4. **Anticipate Issues** - Potential pitfalls, edge cases, testing considerations
5. **Success Criteria** - How to verify the solution works correctly

### Tool Scope (Read-Only by Default)

| Agent | Read | Glob | Grep | Bash | Edit | Write |
|-------|------|------|------|------|------|-------|
| sqlalchemy-async-optimizer | ✅ | ✅ | ✅ | ✅* | ❌ | ❌ |
| architecture-guardian | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| test-pyramid-architect | ✅ | ✅ | ✅ | ✅* | ❌ | ❌ |
| react-architecture-specialist | ✅ | ✅ | ✅ | ✅* | ❌ | ❌ |
| vitest-strategy-architect | ✅ | ✅ | ✅ | ✅* | ❌ | ❌ |

**Bash restrictions**:
- sqlalchemy-async-optimizer: `sqlite3`, `alembic` (inspection only, no migrations)
- test-pyramid-architect: `pytest` execution, coverage analysis
- react-architecture-specialist: `vite build`, `vitest` execution
- vitest-strategy-architect: `vitest`, `playwright test` execution

**Why read-only**: Subagents provide expert guidance, main agent implements with full context. This preserves:
- Context awareness (main agent sees full picture)
- Architectural safety (subagents flag violations, don't "fix" incorrectly)
- Learning retention (main agent applies patterns consistently)

### Ad-Hoc Task Tool

For one-off investigations not warranting permanent agents:
```bash
# Use built-in Task tool for:
# - Library research (tenacity internals, dependency evaluation)
# - Minimal reproduction cases
# - Temporary specialists (delete after use)
```

**Example**: Debugging retry logging bug
```
Use Task to create minimal tenacity reproduction:
- Test if before_sleep callbacks fire with retry_if_exception()
- Compare to retry_base class approach
- Output: Narrow 7 hypotheses to 2-3 root causes
```

### Best Practices

**When to Use Subagents**:
- ✅ Complex architectural decisions (multiple valid approaches)
- ✅ Performance optimization (query strategies, React memoization)
- ✅ Testing strategy design (what to test, how to test)
- ✅ Debugging specialized issues (SQLite locks, async patterns)

**When to Use Main Agent Directly**:
- ❌ Simple implementations (read file, fix typo)
- ❌ Straightforward patterns (already documented in CLAUDE.md)
- ❌ When you already know the approach

**Subagent Workflow**:
1. Main agent identifies need for specialist expertise
2. Invokes subagent with specific question
3. Subagent returns focused recommendation
4. Main agent implements with full codebase context
5. (Optional) Subagent reviews implementation for compliance

### Tracking Subagent Usage

Document subagent consultations in WORK.md using enhanced format:

```markdown
| Agent | Task | Outcome | Decision | Context Files |
|-------|------|---------|----------|---------------|
| **Subagent**: architecture-guardian | Review refactor | No violations | ✅ Accepted | retry_policies.py |
| **Subagent**: test-pyramid-architect | Test strategy | 6 unit, 3 integration | ✅ Implemented | test_sync_playlist.py |
| **Task**: Ad-hoc | Minimal repro | Callbacks work | 🔍 Narrowed issue | /tmp/test_tenacity.py |
| **Main**: Direct | Implement fix | 827 tests passing | ✅ Complete | Applied learnings |
```

**Key columns**:
- **Agent**: Type (Subagent name, Task, Main)
- **Decision**: ✅ Accepted / ⚠️ Modified / ❌ Rejected / 🔍 Narrowed
- **Context Files**: Critical files for future reference

---

## Resources

### Core Documentation
- **[CLAUDE.md](../CLAUDE.md)** - Essential commands and coding standards
- **[ARCHITECTURE.md](ARCHITECTURE.md)** - System design and patterns
- **[DATABASE.md](DATABASE.md)** - Schema reference
- **[BACKLOG.md](../BACKLOG.md)** - Project roadmap

### External References  
- [SQLAlchemy 2.0](https://docs.sqlalchemy.org/en/20/) - Database ORM
- [Typer](https://typer.tiangolo.com/) - CLI framework
- [BasedPyright](https://github.com/DetachHead/basedpyright) - Type checker