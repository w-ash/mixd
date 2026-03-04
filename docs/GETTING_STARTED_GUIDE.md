# Getting Started: Python 3.14 + FastAPI + React with Claude Code

**A bootstrap guide for new full-stack projects — March 2026 best practices**

This guide covers everything you need to go from zero to a production-ready Python / FastAPI / React + Tailwind monorepo, optimized for development with Claude Code. It distills battle-tested patterns from real projects into copy-pasteable configurations and templates.

**Target audience**: Solo developers or small teams building full-stack web applications in 2026.

**Core philosophy**:
- **Ruthlessly DRY** — no code duplication in single-maintainer codebases
- **Immutable domain** — pure transformations, no side effects
- **Batch-first** — design for collections, single items are degenerate cases
- **Test-mandatory** — a feature is not done until tests pass
- **Validate at boundaries** — typed models at entry points, trust internals

---

## Table of Contents

1. [Project Structure](#1-project-structure)
2. [CLAUDE.md — The Project Brain](#2-claudemd--the-project-brain)
3. [.claude/ Directory Configuration](#3-claude-directory-configuration)
4. [Python 3.14+ Tooling Setup](#4-python-314-tooling-setup)
5. [Python 3.14+ Coding Patterns](#5-python-314-coding-patterns)
6. [FastAPI Backend Patterns](#6-fastapi-backend-patterns)
7. [React + TypeScript + Tailwind Frontend](#7-react--typescript--tailwind-frontend)
8. [Testing Strategy](#8-testing-strategy)
9. [CI/CD with GitHub Actions](#9-cicd-with-github-actions)
10. [Essential Commands Cheat Sheet](#10-essential-commands-cheat-sheet)
11. [Bootstrap Checklist](#11-bootstrap-checklist)

---

## 1. Project Structure

```
my-project/
├── .claude/                          # Claude Code AI configuration
│   ├── settings.json                 # PostToolUse hooks (auto-format on edit)
│   ├── settings.local.json           # Permissions matrix (gitignored)
│   ├── agents/                       # Read-only specialist subagents
│   │   ├── architecture-guardian.md
│   │   └── test-pyramid-architect.md
│   ├── rules/                        # Path-based enforcement rules
│   │   ├── domain-purity.md
│   │   ├── application-patterns.md
│   │   ├── infrastructure-patterns.md
│   │   ├── interface-patterns.md
│   │   ├── test-patterns.md
│   │   ├── web-frontend-patterns.md
│   │   └── implementation-completeness.md
│   └── skills/                       # Reference docs & workflow guides
│       └── api-contracts/SKILL.md
├── .github/workflows/
│   └── claude.yml                    # Claude Code @mention trigger
├── src/                              # Python backend (Clean Architecture)
│   ├── domain/                       # Pure business logic, zero deps
│   │   ├── entities/                 # Immutable data models
│   │   ├── exceptions.py
│   │   └── repositories/            # Protocol interfaces only
│   ├── application/                  # Use case orchestration
│   │   ├── runner.py                 # execute_use_case() entry point
│   │   └── use_cases/
│   ├── infrastructure/               # External adapters & persistence
│   │   └── persistence/
│   │       └── repositories/         # Protocol implementations
│   ├── interface/                    # Presentation layer
│   │   └── api/
│   │       ├── app.py                # FastAPI application factory
│   │       ├── middleware.py         # Exception → error envelope
│   │       ├── routes/
│   │       └── schemas/              # Pydantic request/response
│   └── config/
│       ├── __init__.py
│       └── constants.py              # All project constants centralized
├── tests/                            # Mirror of src/ structure
│   ├── conftest.py                   # Root fixtures + auto-markers
│   ├── fixtures/                     # Factory helpers
│   │   ├── factories.py              # make_entity() builders
│   │   └── mocks.py                  # make_mock_uow()
│   ├── unit/                         # Fast, mocked (<100ms each)
│   │   ├── domain/
│   │   └── application/
│   └── integration/                  # Real deps (DB, HTTP)
│       ├── api/                      # Route handler tests
│       └── repositories/             # Real DB tests
├── web/                              # React frontend
│   ├── src/
│   │   ├── api/
│   │   │   ├── client.ts             # customFetch + ApiError
│   │   │   ├── query-client.ts       # QueryClient factory
│   │   │   └── generated/            # Orval codegen (never hand-edit)
│   │   ├── components/
│   │   │   ├── ui/                   # Primitives (shadcn/ui)
│   │   │   └── shared/               # Reusable composites
│   │   ├── hooks/
│   │   ├── pages/
│   │   ├── test/
│   │   │   ├── setup.ts              # MSW server bootstrap
│   │   │   └── test-utils.tsx        # renderWithProviders()
│   │   ├── App.tsx
│   │   └── main.tsx
│   ├── biome.json
│   ├── openapi.json                  # Copied from FastAPI backend
│   ├── orval.config.ts
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   └── vitest.config.ts
├── CLAUDE.md                         # Project instructions for Claude Code
├── pyproject.toml                    # Python deps + ruff + pyright + pytest
├── .pre-commit-config.yaml
└── .gitignore
```

**Key principles**:
- Monorepo with clear backend/frontend separation (`src/` and `web/`)
- Clean Architecture layers: `Interface → Application → Domain ← Infrastructure`
- Tests mirror the source tree under `tests/unit/` and `tests/integration/`
- All Claude Code configuration lives in `.claude/`

---

## 2. CLAUDE.md — The Project Brain

`CLAUDE.md` is the single most important file for Claude Code. It's loaded as the system prompt for every conversation, so it directly determines code quality.

### Recommended Sections

```markdown
# Project Name

## What This Project Does
[2-3 sentences: user problem, solution approach]

## Core Principles (YOU MUST FOLLOW)
- **Python 3.14+ Required** - Modern syntax, type safety
- **Ruthlessly DRY** - No code duplication
- **Immutable Domain** - Pure transformations, no side effects
- **Batch-First** - Collections over single items
- [Your project-specific principles]

## Architecture
**Dependency Flow**: Interface → Application → Domain ← Infrastructure

[Layer descriptions with directory mappings]

## Essential Commands
[Dev, test, lint, format, type-check commands]

## Required Coding Patterns
### Python 3.14+ Syntax (REQUIRED)
[DO / DON'T examples — see Section 5]

## Testing
### Self-Check (after every implementation)
1. Did I write tests? If not, write them now
2. Right level? Domain=unit, UseCase=unit+mocks, Repository=integration
3. Beyond happy path? Error cases, edge cases, validation
4. Using existing factories from tests/fixtures/?
5. Tests pass? `poetry run pytest tests/path/to/test_file.py -x`

## Documentation Map
[Links to deeper docs]
```

### Writing Style Tips

- **Use imperative language**: "YOU MUST FOLLOW" — Claude treats CLAUDE.md as authoritative instructions
- **Keep it under 300 lines** — lines beyond that risk context truncation; link to deeper docs
- **Include a Self-Check pattern** — a checklist Claude runs after every implementation to catch its own gaps
- **Be specific about commands** — include the exact `poetry run` prefix, flag combinations, etc.

---

## 3. `.claude/` Directory Configuration

### 3a. settings.json — PostToolUse Hooks

Hooks run automatically after Claude uses the Edit or Write tools. This ensures every file Claude touches is instantly formatted and linted.

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "jq -r '.tool_input.file_path // empty' | xargs -I{} poetry run ruff check {} --fix --quiet 2>/dev/null; exit 0"
          }
        ]
      },
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "jq -r '.tool_input.file_path // empty' | grep -E '\\.(tsx?|jsx?)$' | xargs -I{} pnpm --prefix web exec biome check --write {} 2>/dev/null; exit 0"
          }
        ]
      }
    ]
  }
}
```

**How it works**:
- `jq` extracts the file path from Claude's tool input JSON
- First hook: runs `ruff check --fix` on any Python file edit
- Second hook: runs `biome check --write` on TypeScript/JavaScript edits (filtered by `grep`)
- `exit 0` ensures hook failures never block Claude's workflow
- `2>/dev/null` suppresses noise from files outside the tool's scope

### 3b. settings.local.json — Permissions Matrix

This file is **gitignored** — it's per-developer. It controls what Claude can do without asking.

```json
{
  "permissions": {
    "allow": [
      "Bash(poetry run pytest:*)",
      "Bash(poetry run ruff check:*)",
      "Bash(poetry run ruff format:*)",
      "Bash(poetry run basedpyright:*)",
      "Bash(pnpm:*)",
      "Bash(git status:*)",
      "Bash(git diff:*)",
      "Bash(git log:*)",
      "Bash(ls:*)",
      "Bash(find:*)",
      "Bash(grep:*)",
      "WebSearch"
    ],
    "deny": []
  }
}
```

**Principle**: allow read-only operations and dev tooling by default; require confirmation for destructive operations (git push, file deletion, etc.).

### 3c. rules/ — Path-Based Enforcement

Rules activate automatically when Claude reads or edits files matching their `paths` glob. Each file uses YAML frontmatter to specify its target.

**`rules/domain-purity.md`**:
```markdown
---
paths:
  - "src/domain/**"
---
# Domain Layer Rules
- NEVER import from infrastructure, application, or interface layers
- All entities use immutable data classes (frozen=True)
- All transformations must be pure (no side effects, no I/O)
- Repository interfaces are Protocol classes only (zero implementation)
```

**`rules/application-patterns.md`**:
```markdown
---
paths:
  - "src/application/**"
---
# Application Layer Rules
- NEVER import from infrastructure directly — use Protocol interfaces
- Use case owns transaction boundaries (commit/rollback)
- All use cases run through the runner function
- Constructor injection for all dependencies
```

**`rules/infrastructure-patterns.md`**:
```markdown
---
paths:
  - "src/infrastructure/**"
---
# Infrastructure Layer Rules
- NEVER expose ORM models to application layer — convert to domain entities
- Validate at the boundary: raw data → typed models at API clients
- Batch operations for all repository methods (save_batch, get_by_ids)
```

**`rules/interface-patterns.md`**:
```markdown
---
paths:
  - "src/interface/**"
---
# Interface Layer Rules
- NEVER access repositories directly — call execute_use_case()
- Zero business logic in route handlers — delegate to use cases
- Route handlers are 5-10 lines maximum
```

**`rules/test-patterns.md`**:
```markdown
---
paths:
  - "tests/**"
---
# Test Rules
- Markers auto-applied by directory: tests/unit/ → unit, tests/integration/ → integration
- Use existing factory functions from tests/fixtures/
- Test names: test_<scenario>_<expected_behavior>
- Minimum coverage: happy path + at least one error/edge case per public function
```

**`rules/web-frontend-patterns.md`**:
```markdown
---
paths:
  - "web/**"
---
# Web Frontend Rules
- Three component layers: ui/ (primitives), shared/ (composites), pages/ (routes)
- Server state via Tanstack Query — no Redux/Zustand
- TypeScript strict mode — no any, no @ts-ignore
- API hooks auto-generated by Orval — never hand-edit generated/
- Co-located tests: Component.tsx → Component.test.tsx
```

**`rules/implementation-completeness.md`**:
```markdown
---
paths:
  - "src/**"
---
# Implementation Completeness
- Every source change requires corresponding tests
- After implementing, verify a test file exists at the mirror path
- Minimum coverage: happy path + at least one error/edge case
- Run tests after implementation
```

### 3d. agents/ — Specialist Subagents

Agents are read-only specialists that Claude invokes for deep analysis. They recommend — the main agent implements. Start with two essential agents.

**Agent metadata format** (YAML frontmatter):
```markdown
---
name: architecture-guardian
description: Use this agent when you need architectural review for Clean Architecture compliance
model: sonnet
allowed_tools: ["Read", "Glob", "Grep"]
---
```

**`agents/architecture-guardian.md`** — validates layer dependencies, repository patterns, transaction boundaries. Outputs: Approved / Approved with suggestions / Rejected with violations.

**`agents/test-pyramid-architect.md`** — designs test strategies, identifies correct test level per layer, recommends fixture patterns. Targets: 60% unit / 35% integration / 5% E2E.

**When to add more agents**: when you find yourself repeatedly giving the same specialized guidance (e.g., ORM optimization, frontend testing patterns, log analysis).

### 3e. skills/ — Reference Documents

Skills are embedded reference documents. Two types:

**Non-invocable** (background context, loaded automatically when relevant):
```markdown
---
name: api-contracts
description: REST API endpoint reference — routes, schemas, error codes
user-invocable: false
---
# API Contracts
[Condensed reference content]
```

**Invocable** (step-by-step workflows triggered by users):
```markdown
---
name: new-module
description: Step-by-step guide for adding a new module to the project
---
# Adding a New Module
## Step 1: Create domain entities...
## Step 2: Define repository protocol...
```

Use skills for: API contract references, design system tokens, database schema docs, repeatable multi-step workflows.

---

## 4. Python 3.14+ Tooling Setup

### 4a. Poetry

Initialize with `poetry init`, then configure `pyproject.toml`:

**Core dependencies** (adjust to your project):
```
fastapi, uvicorn[standard], httpx, loguru, pydantic, pydantic-settings, python-dotenv
```

**Dev dependencies**:
```
pytest, pytest-asyncio, ruff, basedpyright, pre-commit
```

### 4b. Ruff Configuration

Ruff replaces Black, isort, flake8, and dozens of plugins in a single tool.

```toml
[tool.ruff]
line-length = 88
target-version = "py314"
preview = true
output-format = "full"

[tool.ruff.lint]
select = [
    "E",     # pycodestyle errors
    "F",     # pyflakes
    "I",     # isort (import sorting)
    "B",     # flake8-bugbear
    "UP",    # pyupgrade — modernize syntax
    "N",     # pep8-naming
    "SIM",   # flake8-simplify
    "RUF",   # Ruff-specific rules
    "C4",    # flake8-comprehensions
    "S",     # flake8-bandit (security)
    "PT",    # flake8-pytest-style
    "COM",   # flake8-commas (trailing commas)
    "DTZ",   # flake8-datetimez (timezone awareness)
    "PERF",  # Performance anti-patterns
    "ASYNC", # Async/await issues
    "ARG",   # Function argument validation
    "FURB",  # Modernize Python patterns
    "LOG",   # Logging best practices
    "TRY",   # Exception handling
    "PL",    # Pylint checks
    "PTH",   # Use pathlib
]
ignore = [
    "E501",    # Line too long — handled by formatter
    "COM812",  # Trailing comma — conflicts with formatter
    "TC001",   # Move to TYPE_CHECKING — obsolete with PEP 649
    "TC002",   # Move to TYPE_CHECKING — obsolete with PEP 649
    "TC003",   # Move to TYPE_CHECKING — obsolete with PEP 649
    "TC006",   # Cast quotes — contradicts PEP 649
    "TRY003",  # Long exception messages — clear errors are good
    "TRY400",  # Use logging.exception — doesn't apply to loguru
]
fixable = ["ALL"]

[tool.ruff.lint.per-file-ignores]
"__init__.py" = ["F401"]           # Unused imports in __init__
"src/interface/api/**/*.py" = [
    "RUF029",                       # FastAPI async handlers without await
]
"tests/**/*.py" = [
    "S101",     # Allow assert
    "ARG001",   # Unused function args (fixtures)
    "ARG002",   # Unused method args (fixtures)
    "PLR2004",  # Magic numbers in tests
    "PT011",    # Broad pytest.raises
    "ERA001",   # Commented-out code (documentation)
]

[tool.ruff.lint.isort]
known-first-party = ["src"]
combine-as-imports = true
split-on-trailing-comma = true
force-sort-within-sections = true
order-by-type = true

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
docstring-code-format = true
docstring-code-line-length = "dynamic"
```

**Why these ignores matter**:
- `TC001`/`TC002`/`TC003`/`TC006`: Python 3.14's PEP 649 (deferred evaluation of annotations) makes `TYPE_CHECKING` blocks unnecessary except for genuine circular imports
- `preview = true`: enables the latest rules and formatter improvements
- Test-specific ignores: `S101` (assert), `ARG001` (fixture args), `PLR2004` (magic test values)

### 4c. BasedPyright Configuration

BasedPyright is a modern fork of Pyright with stricter defaults suited for 2026 Python.

```toml
[tool.basedpyright]
include = ["src"]
exclude = ["tests", "**/__pycache__"]
typeCheckingMode = "strict"
pythonVersion = "3.14"
pythonPlatform = "All"
venvPath = "."
venv = ".venv"

# Essential reports
reportMissingImports = true
reportMissingTypeStubs = false       # Many libs lack stubs
reportUnusedImport = true
reportUnusedVariable = true
reportDeprecated = "warning"

# Reduce noise from legitimate patterns
reportAny = "warning"                # JSON payloads, protocol flexibility
reportExplicitAny = "warning"
reportUnusedCallResult = "none"      # Side-effect calls (.pop, .discard)
reportMissingTypeArgument = "warning"  # Bare generics valid in 3.14
reportUninitializedInstanceVariable = "none"  # Decorator-initialized fields
enableTypeIgnoreComments = true
```

### 4d. pytest Configuration

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
addopts = "--strict-markers --tb=short -m 'not slow and not performance and not diagnostic'"
markers = [
    "unit: unit tests — pure logic, no external deps (<100ms)",
    "integration: integration tests — real DB, mocked APIs (<1s)",
    "slow: tests >1 second (skipped by default)",
    "performance: timing assertion tests (skipped by default)",
    "diagnostic: investigation-only tests (skipped by default)",
]
```

**Key settings**:
- `asyncio_mode = "auto"`: pytest-asyncio discovers async tests without `@pytest.mark.asyncio`
- Default markers skip slow/diagnostic tests — run all with `pytest -m ""`
- `--strict-markers` catches typos in marker names

### 4e. Pre-commit Hooks

```yaml
# .pre-commit-config.yaml
repos:
-   repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.5.0
    hooks:
    -   id: trailing-whitespace
    -   id: end-of-file-fixer
    -   id: check-yaml
    -   id: check-toml
    -   id: check-json
    -   id: debug-statements
    -   id: check-added-large-files
-   repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.15.0
    hooks:
    -   id: ruff
        args: [--fix]
    -   id: ruff-format
-   repo: https://github.com/python-poetry/poetry
    rev: 1.8.0
    hooks:
    -   id: poetry-check
```

No Black hook needed — Ruff handles both linting and formatting.

After creating the file: `pre-commit install`

---

## 5. Python 3.14+ Coding Patterns

Each pattern shows the modern way and the legacy way it replaces.

### Generics (PEP 695)
```python
# DO — Python 3.14 syntax
class Repository[TModel, TDomain]:
    ...

async def execute[TResult](factory: Callable[..., TResult]) -> TResult:
    ...

# DON'T — legacy
from typing import Generic, TypeVar
T = TypeVar("T")
class Repository(Generic[T]):
    ...
```

### Union Types (PEP 604)
```python
# DO
def find(id: int) -> User | None: ...
def parse(value: str | int | float) -> str: ...

# DON'T
from typing import Optional, Union
def find(id: int) -> Optional[User]: ...
```

### PEP 649 Deferred Annotations
```python
# DO — Python 3.14 evaluates annotations lazily by default
def process(item: MyClass) -> MyClass:
    ...

# DON'T
from __future__ import annotations     # Unnecessary in 3.14
def process(item: "MyClass") -> "MyClass":  # String quotes unnecessary
    ...
```

### Timestamps (PEP 615)
```python
# DO
from datetime import UTC, datetime
now = datetime.now(UTC)

# DON'T
now = datetime.utcnow()     # Returns naive datetime (deprecated)
now = datetime.now()         # Returns local time (ambiguous)
```

### Structured Concurrency (PEP 654)
```python
# DO — TaskGroup cancels all on first failure
async with asyncio.TaskGroup() as tg:
    tg.create_task(fetch_users())
    tg.create_task(fetch_orders())

# DON'T — gather leaves partial results on failure
results = await asyncio.gather(fetch_users(), fetch_orders())
```

### Multi-Exception Handling (PEP 758)
```python
# DO — Python 3.14, no parentheses needed
except TimeoutError, ConnectionError:
    handle_network_error()

# Previous style (still works)
except (TimeoutError, ConnectionError):
    handle_network_error()
```

### Type Guards (PEP 742)
```python
# DO
from typing import TypeIs

def is_admin(user: User) -> TypeIs[AdminUser]:
    return user.role == "admin"

if is_admin(user):
    user.admin_action()  # Type narrowed to AdminUser

# DON'T
if hasattr(user, "admin_action"):  # type: ignore
    user.admin_action()
```

### Structured Logging (loguru)
```python
# DO
from loguru import logger
log = logger.bind(service="auth", user_id=user.id)
log.info("Login successful")
log.opt(exception=True).error("Authentication failed")

# DON'T
import logging
logger = logging.getLogger(__name__)
logger.error("Failed", exc_info=True)  # loguru ignores exc_info kwarg
```

---

## 6. FastAPI Backend Patterns

### 6a. Clean Architecture

```
Interface  →  Application  →  Domain  ←  Infrastructure
(FastAPI)     (Use Cases)     (Logic)    (DB, APIs)
```

- **Domain**: pure business logic, zero external imports, `Protocol` interfaces for repositories
- **Application**: use case orchestration, owns transaction boundaries, constructor injection
- **Infrastructure**: implements repository protocols, API clients, ORM models
- **Interface**: thin route handlers (5-10 lines), delegates everything to use cases

### 6b. Use Case Runner Pattern

```python
# src/application/runner.py
from collections.abc import Callable, Coroutine
from typing import Any

from src.domain.repositories.interfaces import UnitOfWorkProtocol


async def execute_use_case[TResult](
    use_case_factory: Callable[[UnitOfWorkProtocol], Coroutine[Any, Any, TResult]],
) -> TResult:
    """Run a use case with proper session and UoW lifecycle.

    Lazy imports keep infrastructure out of the application layer's
    module-level namespace.
    """
    from src.infrastructure.persistence.database.db_connection import get_session
    from src.infrastructure.persistence.repositories.factories import get_unit_of_work

    async with get_session() as session:
        uow = get_unit_of_work(session)
        return await use_case_factory(uow)
```

Both CLI and API call the same runner — zero business logic duplication.

### 6c. Thin Route Handlers

```python
# src/interface/api/routes/items.py
from fastapi import APIRouter

from src.application.runner import execute_use_case
from src.application.use_cases.get_item import GetItemCommand, GetItemUseCase
from src.interface.api.schemas.items import ItemResponse

router = APIRouter(prefix="/items", tags=["items"])


@router.get("/{item_id}")
async def get_item(item_id: int) -> ItemResponse:
    result = await execute_use_case(
        lambda uow: GetItemUseCase(uow).execute(GetItemCommand(id=item_id))
    )
    return ItemResponse.from_domain(result)
```

### 6d. Error Envelope

Consistent error responses across the entire API:

```python
# src/interface/api/middleware.py
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.domain.exceptions import NotFoundError, ValidationError


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(NotFoundError)
    async def not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": str(exc)}},
        )

    @app.exception_handler(ValidationError)
    async def validation_error(_: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "VALIDATION_ERROR", "message": str(exc)}},
        )

    @app.exception_handler(Exception)
    async def internal_error(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "INTERNAL_ERROR", "message": "An internal error occurred"}},
        )
```

**Error shape**: `{"error": {"code": "UPPER_SNAKE", "message": "Human-readable description"}}`.
For paginated lists: `{"data": [...], "total": int, "limit": int, "offset": int}`.

### 6e. Application Factory

```python
# src/interface/api/app.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.interface.api.middleware import register_exception_handlers
from src.interface.api.routes.health import router as health_router
from src.interface.api.routes.items import router as items_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="My Project",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],  # Vite dev server
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)

    app.include_router(health_router, prefix="/api/v1")
    app.include_router(items_router, prefix="/api/v1")

    return app


app = create_app()
```

### 6f. OpenAPI Spec for Frontend Codegen

FastAPI auto-generates an OpenAPI spec at `/api/openapi.json`. Copy this to `web/openapi.json` for Orval codegen. Use `tags` on routers to control how Orval splits the generated code into separate files.

---

## 7. React + TypeScript + Tailwind Frontend

### 7a. Vite Configuration

```typescript
// web/vite.config.ts
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": "/src" },
  },
  server: {
    open: true,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
  },
});
```

Key features: Tailwind v4 plugin, `@/` import alias, and `/api` proxy to FastAPI during development.

### 7b. TypeScript Configuration

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "jsx": "react-jsx",
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "verbatimModuleSyntax": true,
    "moduleDetection": "force",
    "noEmit": true,
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "erasableSyntaxOnly": true,
    "noFallthroughCasesInSwitch": true,
    "noUncheckedSideEffectImports": true,
    "baseUrl": ".",
    "paths": { "@/*": ["src/*"] }
  },
  "include": ["src"]
}
```

`erasableSyntaxOnly`: ensures only type-level syntax is used (no enums, no parameter properties) — aligns with modern bundler expectations.

### 7c. Biome Configuration

Biome replaces both ESLint and Prettier in a single Rust-based tool.

```json
{
  "$schema": "https://biomejs.dev/schemas/2.4.5/schema.json",
  "vcs": { "enabled": true, "clientKind": "git", "useIgnoreFile": true },
  "files": {
    "includes": ["**", "!!**/dist", "!!src/api/generated/**", "!!src/components/ui/**"]
  },
  "formatter": { "enabled": true, "indentStyle": "space", "indentWidth": 2 },
  "linter": { "enabled": true, "rules": { "recommended": true } },
  "css": { "parser": { "cssModules": false, "tailwindDirectives": true } },
  "javascript": { "formatter": { "quoteStyle": "double" } },
  "assist": {
    "enabled": true,
    "actions": { "source": { "organizeImports": "on" } }
  }
}
```

**Exclusions**: `api/generated/` (Orval output, auto-generated) and `components/ui/` (shadcn/ui primitives, separately maintained).

### 7d. Orval — API Code Generation

Orval generates TypeScript types, React Query hooks, and MSW mock handlers from your OpenAPI spec.

```typescript
// web/orval.config.ts
import { defineConfig } from "orval";

export default defineConfig({
  myProject: {
    input: { target: "./openapi.json" },
    output: {
      mode: "tags-split",
      target: "src/api/generated",
      schemas: "src/api/generated/model",
      client: "react-query",
      mock: true,
      override: {
        mutator: { path: "src/api/client.ts", name: "customFetch" },
        query: { useQuery: true, useSuspenseQuery: false },
      },
    },
  },
});
```

- `tags-split`: splits generated files by OpenAPI tag (e.g., `items/items.ts`, `health/health.ts`)
- `mock: true`: auto-generates MSW handlers for testing
- `mutator`: points to a custom fetch wrapper that handles error envelopes
- Regenerate after API changes: `pnpm --prefix web generate`
- **Never hand-edit** files in `src/api/generated/`

### 7e. Custom Fetch + ApiError

```typescript
// web/src/api/client.ts
export class ApiError extends Error {
  status: number;
  code: string;
  details?: Record<string, string>;

  constructor(
    status: number,
    code: string,
    message: string,
    details?: Record<string, string>,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export async function customFetch<T>(
  url: string,
  init: RequestInit = {},
): Promise<T> {
  const response = await fetch(url, init);

  if (response.status === 204) {
    return { data: undefined, status: 204, headers: response.headers } as T;
  }

  const body = await response.json();

  if (!response.ok) {
    const error = body?.error;
    throw new ApiError(
      response.status,
      error?.code ?? "UNKNOWN_ERROR",
      error?.message ?? "An unknown error occurred",
      error?.details,
    );
  }

  return { data: body, status: response.status, headers: response.headers } as T;
}
```

This wraps every API response into an `{data, status, headers}` envelope that Orval expects, and converts error responses into typed `ApiError` instances.

### 7f. QueryClient Factory

```typescript
// web/src/api/query-client.ts
import { QueryClient } from "@tanstack/react-query";
import { ApiError } from "./client";

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        retry: (failureCount, error) => {
          if (error instanceof ApiError) {
            return error.status >= 500 && failureCount < 2;
          }
          return false;
        },
      },
    },
  });
}
```

Only retries on 5xx server errors (never on 4xx client errors). 30-second stale time prevents unnecessary refetches.

### 7g. Vitest Configuration

```typescript
// web/vitest.config.ts
import { defineConfig, mergeConfig } from "vitest/config";
import viteConfig from "./vite.config";

export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      environment: "jsdom",
      globals: true,
      setupFiles: ["./src/test/setup.ts"],
      include: ["src/**/*.test.{ts,tsx}"],
      exclude: ["src/api/generated/**"],
    },
  }),
);
```

Merges the Vite config (aliases, plugins) so tests resolve `@/` imports identically to the app.

### 7h. Test Utilities

**MSW Server Bootstrap** (`web/src/test/setup.ts`):
```typescript
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll } from "vitest";

// Import auto-generated MSW handlers from Orval
import { getHealthMock } from "@/api/generated/health/health.msw";
import { getItemsMock } from "@/api/generated/items/items.msw";

export const server = setupServer(
  ...getHealthMock(),
  ...getItemsMock(),
);

beforeAll(() => server.listen({ onUnhandledRequest: "warn" }));
afterEach(() => { server.resetHandlers(); cleanup(); });
afterAll(() => server.close());
```

**renderWithProviders** (`web/src/test/test-utils.tsx`):
```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type RenderOptions, render } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { MemoryRouter, type MemoryRouterProps } from "react-router";

function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
}

function Providers({ children, routerProps }: { children: ReactNode; routerProps?: MemoryRouterProps }) {
  return (
    <QueryClientProvider client={createTestQueryClient()}>
      <MemoryRouter {...routerProps}>{children}</MemoryRouter>
    </QueryClientProvider>
  );
}

interface ExtendedRenderOptions extends Omit<RenderOptions, "wrapper"> {
  routerProps?: MemoryRouterProps;
}

export function renderWithProviders(
  ui: ReactElement,
  { routerProps, ...options }: ExtendedRenderOptions = {},
) {
  return render(ui, {
    wrapper: ({ children }) => <Providers routerProps={routerProps}>{children}</Providers>,
    ...options,
  });
}

export { act, screen, waitFor, within } from "@testing-library/react";
export { default as userEvent } from "@testing-library/user-event";
```

Use `renderWithProviders()` for any component that needs hooks, routing, or queries. Use plain `render()` for pure presentational components.

---

## 8. Testing Strategy

### Test Pyramid

```
         /   E2E   \           5%  — Playwright (Chromium desktop)
        /------------\
       / Integration  \       35%  — pytest (real DB), Vitest (MSW)
      /----------------\
     /    Unit Tests    \     60%  — pytest (pure logic), Vitest (components)
    /--------------------\
```

### Backend Test Placement

| Source Layer | Test Location | Type | Key Tool |
|---|---|---|---|
| `src/domain/` | `tests/unit/domain/` | unit | No mocks needed |
| `src/application/use_cases/` | `tests/unit/application/use_cases/` | unit | Mock UoW + repos |
| `src/infrastructure/persistence/` | `tests/integration/repositories/` | integration | Real DB session |
| `src/interface/api/` | `tests/integration/api/` | integration | httpx AsyncClient |

### Frontend Test Placement

| Source | Test Location | Type |
|---|---|---|
| `web/src/components/` | Co-located `*.test.tsx` | Vitest + RTL |
| `web/src/hooks/` | Co-located `*.test.ts` | Vitest |
| `web/src/pages/` | Co-located `*.test.tsx` | Vitest + MSW |
| Critical user flows | `web/e2e/*.spec.ts` | Playwright |

### Factory Fixtures

```python
# tests/fixtures/factories.py
def make_item(*, id: int = 1, name: str = "Test Item", **overrides) -> Item:
    """Factory with keyword overrides for any field."""
    return Item(id=id, name=name, **overrides)

def make_items(count: int = 5) -> list[Item]:
    """Batch factory producing numbered items."""
    return [make_item(id=i, name=f"Item {i}") for i in range(1, count + 1)]
```

```python
# tests/fixtures/mocks.py
def make_mock_uow() -> AsyncMock:
    """Pre-wired UoW mock with all repositories."""
    uow = AsyncMock(spec=UnitOfWorkProtocol)
    uow.get_item_repository.return_value = AsyncMock(spec=ItemRepositoryProtocol)
    return uow
```

### Auto-Markers via conftest.py

```python
# tests/conftest.py
import pytest

def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Auto-apply unit/integration markers based on test file location."""
    for item in items:
        path = str(item.fspath)
        if "/tests/unit/" in path:
            item.add_marker(pytest.mark.unit)
        elif "/tests/integration/" in path:
            item.add_marker(pytest.mark.integration)
```

This eliminates per-function `@pytest.mark.unit` decorators and makes `-m "unit"` / `-m "integration"` filtering reliable.

### Coverage Targets

| Layer | Target |
|---|---|
| Domain + Application | 85% |
| Backend overall | 80% |
| Frontend components | 60% |
| E2E critical flows | 100% of identified flows |

---

## 9. CI/CD with GitHub Actions

### Claude Code @mention Workflow

Triggers when someone writes `@claude` in an issue, PR comment, or review.

```yaml
# .github/workflows/claude.yml
name: Claude Code
on:
  issue_comment:
    types: [created]
  pull_request_review_comment:
    types: [created]
  issues:
    types: [opened, assigned]
  pull_request_review:
    types: [submitted]

jobs:
  claude:
    if: |
      (github.event_name == 'issue_comment' && contains(github.event.comment.body, '@claude')) ||
      (github.event_name == 'pull_request_review_comment' && contains(github.event.comment.body, '@claude')) ||
      (github.event_name == 'pull_request_review' && contains(github.event.review.body, '@claude')) ||
      (github.event_name == 'issues' && contains(github.event.issue.body, '@claude'))
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: read
      issues: read
      id-token: write
      actions: read
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 1
      - uses: anthropics/claude-code-action@beta
        with:
          claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
```

**Setup**: Add `CLAUDE_CODE_OAUTH_TOKEN` as a repository secret. See the [Claude Code Action docs](https://github.com/anthropics/claude-code-action) for OAuth setup.

---

## 10. Essential Commands Cheat Sheet

```bash
# ── Backend ────────────────────────────────────
poetry run pytest                           # Fast tests only
poetry run pytest -m ""                     # All tests (including slow)
poetry run pytest tests/unit/ -x            # Unit tests, stop on first failure
poetry run ruff check . --fix               # Lint + autofix
poetry run ruff format .                    # Format
poetry run basedpyright src/                # Type check

# ── Frontend ───────────────────────────────────
pnpm --prefix web dev                       # Vite dev server (port 5173)
pnpm --prefix web test                      # Vitest component tests
pnpm --prefix web check                     # Biome lint + tsc type check
pnpm --prefix web build                     # Production build
pnpm --prefix web generate                  # Orval codegen from openapi.json

# ── Quality Gates (run before committing) ──────
poetry run ruff check . --fix && poetry run ruff format . && poetry run basedpyright src/ && poetry run pytest
pnpm --prefix web check && pnpm --prefix web test
```

---

## 11. Bootstrap Checklist

From zero to a running project:

1. **`poetry init`** — configure `pyproject.toml` with Python >=3.14
2. **Add dependencies** — fastapi, uvicorn, httpx, loguru, pydantic-settings + dev deps
3. **Configure tooling** — add `[tool.ruff]`, `[tool.basedpyright]`, `[tool.pytest.ini_options]` sections ([Section 4](#4-python-314-tooling-setup))
4. **Create `src/`** — with `domain/`, `application/`, `infrastructure/`, `interface/`, `config/` layers
5. **Create `tests/`** — with `unit/`, `integration/`, `fixtures/`, `conftest.py`
6. **Write `CLAUDE.md`** — project overview, principles, architecture, commands, patterns, testing ([Section 2](#2-claudemd--the-project-brain))
7. **Create `.claude/settings.json`** — PostToolUse hooks for auto-format ([Section 3a](#3a-settingsjson--posttooluse-hooks))
8. **Create `.claude/settings.local.json`** — permissions matrix, add to `.gitignore` ([Section 3b](#3b-settingslocaljson--permissions-matrix))
9. **Create `.claude/rules/`** — path-based enforcement files ([Section 3c](#3c-rules--path-based-enforcement))
10. **Create `.claude/agents/`** — architecture-guardian + test-pyramid-architect ([Section 3d](#3d-agents--specialist-subagents))
11. **Scaffold frontend** — `pnpm create vite web -- --template react-ts`
12. **Configure frontend tooling** — `vite.config.ts`, `tsconfig.json`, `biome.json`, `vitest.config.ts`, `orval.config.ts` ([Section 7](#7-react--typescript--tailwind-frontend))
13. **Set up API client** — `web/src/api/client.ts` + `query-client.ts` ([Section 7e-7f](#7e-custom-fetch--apierror))
14. **Set up test infra** — `web/src/test/setup.ts` + `test-utils.tsx` ([Section 7h](#7h-test-utilities))
15. **Create `.pre-commit-config.yaml`** — then run `pre-commit install` ([Section 4e](#4e-pre-commit-hooks))
16. **Create `.github/workflows/claude.yml`** ([Section 9](#9-cicd-with-github-actions))
17. **Write a health check endpoint** — verify the full stack works end to end
18. **Run all quality gates** — confirm a clean baseline before your first commit
