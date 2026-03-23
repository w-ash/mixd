---
paths:
  - "src/**/*.py"
  - "tests/**/*.py"
---
# Python 3.14+ Conventions (Mixd-Specific)

- **Timestamps**: `datetime.now(UTC)` NOT `datetime.now()` or `datetime.utcnow()`
- **UUID**: `uuid7()` for database IDs, `uuid4()` for random IDs only
- **Type guards**: `def is_valid(x: Any) -> TypeIs[str]:` over `hasattr()` + `# type: ignore`
- **Concurrency**: `async with asyncio.TaskGroup() as tg:` NOT `asyncio.gather()` — structured cancellation on failure
- **Logging**: `get_logger(__name__).bind(service="...")` NOT `logging.getLogger()` — structlog with context binding
- **Exception logging**: `logger.error(msg, exc_info=True)` — structlog stdlib mode uses standard `exc_info` kwarg
- **Context propagation**: `with logging_context(key=value):` for async-safe contextvars — NOT `logger.contextualize()`
- **httpx event hooks on AsyncClient**: hooks MUST be `async def` — `AsyncClient` always awaits them; sync hook raises `TypeError`
- **No TYPE_CHECKING** unless circular imports (exception: `operations.py` → `spotify/personal_data`)
- **Multi-exception syntax**: `except CancelledError, Exception:` (no parens, PEP 758)
