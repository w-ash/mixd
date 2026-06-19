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
- **No suppressions**: resolve lint/type warnings by fixing the code, not `# noqa` / `# type: ignore` / `# pyright: ignore[...]` / file-level `reportAny`. For `Any`, reach for `object`, `Awaitable[T]`, `ParamSpec`, `Protocol`, `TypeVar`, or Pydantic models. A genuinely-required suppression (third-party stubs, `# noqa: S104` for an intentional `0.0.0.0` bind) needs user sign-off first.
  - **Carve-out (repository layer):** string-keyed SQLAlchemy column reflection — `getattr(self.model_class, field)` for dynamic column access, and the SQLAlchemy-generic `ORMOption` / `InstrumentedAttribute[Any]` surface — is a **pre-approved** `# pyright: ignore[reportAny]` (no per-instance sign-off). The only alternative is an uglier `cast("QueryableAttribute[object]", …)`, a suppression in disguise. Keep these few; `scripts/check_ratchet.sh`'s `BASE_PYRIGHT_IGNORE` bounds the total so they can't proliferate.
