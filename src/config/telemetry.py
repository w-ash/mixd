"""Per-span database and API accounting — the import's flight recorder.

Answers what no repository-level timer can: how many network round trips did
that chunk cost, and who issued them? The count comes from SQLAlchemy's
``before_cursor_execute``/``after_cursor_execute``, which fire once per
``cursor.execute()`` — so it sees ``insertmanyvalues`` batches and
``SAVEPOINT``/``RELEASE``, both invisible when the v0.10.2.4 audit priced a
persist phase at ~22-26 statements against a real ~68.

Every recorder is a no-op when no probe is active, so non-measuring callers
pay one contextvar read per statement.

Lives in ``src/config`` because it is the only package both ``application``
(which opens spans) and ``infrastructure`` (which feeds them) may import.
"""

from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
import time
from typing import Final

from attrs import define, field

__all__ = [
    "ChunkProbe",
    "current_probe",
    "measure_chunk",
    "operation_scope",
    "phase",
    "record_api_call",
    "record_statement",
]

#: Bucket for statements issued outside any ``@db_operation`` — savepoints,
#: releases, and raw ``session.execute`` calls in repository privates. A large
#: ``_bare`` count is the savepoint signal.
BARE_OPERATION: Final = "_bare"

_NS_PER_MS: Final = 1_000_000


@define(slots=True)
class ChunkProbe:
    """Mutable accounting for one measured span. Never logs, never raises.

    Attributes:
        statements: Round trips — one per ``cursor.execute()``.
        db_ns: Summed time inside those executions.
        api_calls: Calls through ``BaseAPIClient._api_call``.
        api_ns: Summed API time; can exceed wall time under a ``TaskGroup``.
        by_operation: ``name -> (count, ns)``, credited to the outermost
            enclosing ``@db_operation``.
        phases: ``name -> wall ns``, accumulated across re-entry.
        wall_ns: Span wall time, stamped on exit.
    """

    statements: int = 0
    db_ns: int = 0
    api_calls: int = 0
    api_ns: int = 0
    by_operation: dict[str, tuple[int, int]] = field(factory=dict)
    phases: dict[str, int] = field(factory=dict)
    wall_ns: int = 0

    @property
    def db_ms(self) -> float:
        """Database time in milliseconds."""
        return self.db_ns / _NS_PER_MS

    @property
    def wall_ms(self) -> float:
        """Span wall time in milliseconds."""
        return self.wall_ns / _NS_PER_MS

    @property
    def api_ms_sum(self) -> float:
        """Summed API time in milliseconds — a sum, not a wall measurement."""
        return self.api_ns / _NS_PER_MS

    @property
    def rtt_ms(self) -> float:
        """Mean round-trip cost — near the deployment's wide-area latency means
        the span is round-trip-bound; well above it, one query dominates and
        :attr:`by_operation` names it."""
        return self.db_ms / self.statements if self.statements else 0.0

    def phase_ms(self, name: str) -> float:
        """Accumulated wall time for ``name`` in milliseconds, 0 if unseen."""
        return self.phases.get(name, 0) / _NS_PER_MS

    def top_operations(self, limit: int = 6) -> dict[str, list[int]]:
        """The ``limit`` costliest operations as ``[count, ms]``, rest folded
        into ``other``. A dict so structlog emits it as JSON that ``jq`` reads."""
        ranked = sorted(self.by_operation.items(), key=lambda kv: -kv[1][1])
        top = {
            name: [count, round(ns / _NS_PER_MS)]
            for name, (count, ns) in ranked[:limit]
        }
        rest = ranked[limit:]
        if rest:
            top["other"] = [
                sum(count for _, (count, _) in rest),
                round(sum(ns for _, (_, ns) in rest) / _NS_PER_MS),
            ]
        return top


_probe: ContextVar[ChunkProbe | None] = ContextVar("mixd_probe", default=None)
_operation: ContextVar[str] = ContextVar("mixd_db_operation", default=BARE_OPERATION)


def current_probe() -> ChunkProbe | None:
    """The probe for the current context, or ``None`` when nothing is measuring."""
    return _probe.get()


@asynccontextmanager
async def measure_chunk() -> AsyncGenerator[ChunkProbe]:
    """Open a measured span. Nested spans replace rather than nest — the inner
    gets its own probe and the outer resumes untouched."""
    probe = ChunkProbe()
    token = _probe.set(probe)
    started = time.perf_counter_ns()
    try:
        yield probe
    finally:
        probe.wall_ns = time.perf_counter_ns() - started
        _probe.reset(token)


@asynccontextmanager
async def phase(name: str) -> AsyncGenerator[None]:
    """Attribute wall time to ``name``. Additive, not exclusive: three entries
    of ``phase("api")`` report their total."""
    started = time.perf_counter_ns()
    try:
        yield
    finally:
        probe = _probe.get()
        if probe is not None:
            elapsed = time.perf_counter_ns() - started
            probe.phases[name] = probe.phases.get(name, 0) + elapsed


@contextmanager
def operation_scope(name: str) -> Generator[None]:
    """Attribute every statement issued inside to ``name`` — outermost wins.

    Repository methods nest freely, so crediting the innermost frame would
    scatter one logical operation across dozens of leaf buckets.
    """
    if _operation.get() != BARE_OPERATION:
        yield
        return
    token = _operation.set(name)
    try:
        yield
    finally:
        _operation.reset(token)


def record_statement(elapsed_ns: int) -> None:
    """Record one completed round trip against the ambient probe."""
    probe = _probe.get()
    if probe is None:
        return
    probe.statements += 1
    probe.db_ns += elapsed_ns
    name = _operation.get()
    count, total = probe.by_operation.get(name, (0, 0))
    probe.by_operation[name] = (count + 1, total + elapsed_ns)


def record_api_call(elapsed_ns: int) -> None:
    """Record one external API call.

    ``TaskGroup`` children inherit a context copy holding the *same* probe, so
    concurrent calls all land here and the total can exceed span wall time.
    It is a sum; wall time comes from ``phase("api")``.
    """
    probe = _probe.get()
    if probe is None:
        return
    probe.api_calls += 1
    probe.api_ns += elapsed_ns
