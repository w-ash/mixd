# Python 3.14+ Tooling Setup

Poetry for dependency management, Ruff for linting and formatting, BasedPyright for type checking, pytest for testing, and pre-commit hooks for automation. Plus modern Python 3.14+ coding patterns.

---

## Poetry

Initialize with `poetry init`, then configure `pyproject.toml`:

**Core dependencies** (adjust to your project):
```
fastapi, uvicorn[standard], httpx, loguru, pydantic, pydantic-settings, python-dotenv
```

**Dev dependencies**:
```
pytest, pytest-asyncio, ruff, basedpyright, pre-commit
```

---

## Ruff Configuration

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

---

## BasedPyright Configuration

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

---

## pytest Configuration

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

---

## Pre-commit Hooks

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

## Python 3.14+ Coding Patterns

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
