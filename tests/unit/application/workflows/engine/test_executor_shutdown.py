"""Tests for the executor's process-wide shutdown handlers.

Covers the handler chain that keeps uvicorn shutting down: `add_signal_handler`
replaces whatever `signal.signal` installed before it (uvicorn's `handle_exit`),
so `install_shutdown_handler` captures the predecessor and `_request_shutdown`
re-invokes it. Without that, the API logs the shutdown request and serves on
until Fly.io SIGKILLs it.

Verifies:
- a callable predecessor is invoked on a real SIGTERM, and the flag is set
- SIGINT is covered too, and its predecessor is called with SIGINT — uvicorn
  handles both signals, and only SIGTERM used to reach us
- a non-callable predecessor (SIG_DFL — the CLI path) is skipped, not called
- a second install on the same loop keeps the original predecessor instead of
  capturing asyncio's own trampoline
- a second install on a *new* loop re-registers: the registration is loop-global,
  so the former install-once boolean left the process unprotected
- registered shutdown callbacks run, and a raising one cannot cost us the chain
- `shutdown_requested()` reports the module flag, which is how the interface
  layer reads it without touching the private name
"""

import asyncio
from collections.abc import AsyncGenerator, Callable, Iterator
import contextlib
import os
import signal
from types import FrameType
from unittest.mock import Mock

import pytest

import src.application.workflows.engine.executor as executor_module

type _SignalState = tuple[bool, dict[int, object], object, list[Callable[[], None]]]


def _saved_globals() -> _SignalState:
    return (
        executor_module._shutdown_requested,
        dict(executor_module._previous_handlers),
        executor_module._asyncio_trampoline,
        list(executor_module._shutdown_callbacks),
    )


def _reset_globals() -> None:
    executor_module._shutdown_requested = False
    executor_module._previous_handlers.clear()
    executor_module._asyncio_trampoline = None
    executor_module._shutdown_callbacks.clear()


def _restore_globals(saved: _SignalState) -> None:
    requested, previous, trampoline, callbacks = saved
    executor_module._shutdown_requested = requested
    executor_module._previous_handlers.clear()
    executor_module._previous_handlers.update(previous)
    executor_module._asyncio_trampoline = trampoline
    executor_module._shutdown_callbacks[:] = callbacks


@pytest.fixture
async def _isolated_signals() -> AsyncGenerator[None]:
    """Snapshot and restore every piece of global signal state a test touches.

    Restores both signals' process-level handlers, the loop-level handlers, and
    the executor module globals — a leaked handler here would follow the rest of
    the suite around (and, worse, swallow a real SIGTERM).
    """
    loop = asyncio.get_running_loop()
    original = {sig: signal.getsignal(sig) for sig in executor_module._SHUTDOWN_SIGNALS}
    saved = _saved_globals()
    _reset_globals()
    try:
        yield
    finally:
        for sig, handler in original.items():
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.remove_signal_handler(sig)
            signal.signal(sig, handler)
        _restore_globals(saved)


@pytest.fixture
def _isolated_signals_sync() -> Iterator[None]:
    """The same isolation for tests that drive their own event loops."""
    original = {sig: signal.getsignal(sig) for sig in executor_module._SHUTDOWN_SIGNALS}
    saved = _saved_globals()
    _reset_globals()
    try:
        yield
    finally:
        for sig, handler in original.items():
            signal.signal(sig, handler)
        _restore_globals(saved)


@pytest.mark.usefixtures("_isolated_signals")
class TestInstallShutdownHandler:
    """install_shutdown_handler chains to the handlers it displaces."""

    async def test_previous_handler_runs_on_sigterm(self):
        """A real SIGTERM sets the flag AND reaches uvicorn's stand-in handler."""
        fired = asyncio.Event()
        received: list[int] = []

        def fake_handle_exit(sig: int, _frame: FrameType | None) -> None:
            # Stands in for uvicorn's Server.handle_exit, which sets should_exit.
            received.append(sig)
            fired.set()

        signal.signal(signal.SIGTERM, fake_handle_exit)

        assert executor_module.install_shutdown_handler() is True

        # Safe to raise for real: SIGTERM's disposition is now asyncio's handler,
        # never SIG_DFL, so this cannot terminate the test process.
        os.kill(os.getpid(), signal.SIGTERM)
        async with asyncio.timeout(5):
            await fired.wait()

        assert received == [signal.SIGTERM]
        assert executor_module._shutdown_requested is True

    async def test_sigint_is_covered_and_chains_with_its_own_signum(self):
        """Uvicorn handles SIGINT too; the predecessor must hear SIGINT, not SIGTERM."""
        fired = asyncio.Event()
        received: list[int] = []

        def fake_handle_exit(sig: int, _frame: FrameType | None) -> None:
            received.append(sig)
            fired.set()

        signal.signal(signal.SIGINT, fake_handle_exit)

        assert executor_module.install_shutdown_handler() is True

        os.kill(os.getpid(), signal.SIGINT)
        async with asyncio.timeout(5):
            await fired.wait()

        assert received == [signal.SIGINT]
        assert executor_module._shutdown_requested is True

    async def test_non_callable_previous_handler_is_skipped(self):
        """The CLI path (no uvicorn, SIG_DFL installed) must not raise."""
        signal.signal(signal.SIGTERM, signal.SIG_DFL)

        assert executor_module.install_shutdown_handler() is True
        assert executor_module._previous_handlers[signal.SIGTERM] is signal.SIG_DFL

        executor_module._request_shutdown(signal.SIGTERM)

        assert executor_module._shutdown_requested is True

    async def test_second_install_keeps_the_original_handler(self):
        """Re-installing must not re-capture (asyncio's trampoline) or chain twice."""
        fake_handle_exit = Mock()
        signal.signal(signal.SIGTERM, fake_handle_exit)

        assert executor_module.install_shutdown_handler() is True
        assert executor_module.install_shutdown_handler() is True

        assert executor_module._previous_handlers[signal.SIGTERM] is fake_handle_exit

        executor_module._request_shutdown(signal.SIGTERM)

        fake_handle_exit.assert_called_once_with(signal.SIGTERM, None)


@pytest.mark.usefixtures("_isolated_signals_sync")
class TestInstallAcrossEventLoops:
    """The registration is loop-global; the guard against re-install was not."""

    def test_install_on_a_fresh_loop_re_registers(self):
        """A second `asyncio.run` must end up protected, not silently skipped.

        Closing a loop restores SIGTERM to SIG_DFL, so an install-once boolean
        left the next loop with no handler at all — the process would then be
        hard-killed instead of draining at a node boundary.
        """

        async def _install_and_probe() -> tuple[bool, object]:
            return (
                executor_module.install_shutdown_handler(),
                signal.getsignal(signal.SIGTERM),
            )

        first_ok, first_disposition = asyncio.run(_install_and_probe())
        # The loop is closed now, which hands SIGTERM back to the default.
        assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL

        second_ok, second_disposition = asyncio.run(_install_and_probe())

        assert first_ok is True
        assert second_ok is True
        assert second_disposition is first_disposition

    def test_reinstall_keeps_the_stored_callable_predecessor(self):
        """Loop close restores SIG_DFL; a re-install must not replace the
        captured callable (uvicorn's handle_exit) with it."""
        fake_handle_exit = Mock()
        signal.signal(signal.SIGTERM, fake_handle_exit)

        async def _install() -> bool:
            return executor_module.install_shutdown_handler()

        assert asyncio.run(_install()) is True
        assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL
        assert asyncio.run(_install()) is True

        assert executor_module._previous_handlers[signal.SIGTERM] is fake_handle_exit


@pytest.mark.usefixtures("_isolated_signals")
class TestShutdownCallbacks:
    """The hook the interface layer uses to observe a signal it doesn't own."""

    async def test_callbacks_run_on_shutdown(self):
        calls: list[str] = []
        executor_module.add_shutdown_callback(lambda: calls.append("sse"))

        executor_module._request_shutdown(signal.SIGTERM)

        assert calls == ["sse"]

    async def test_raising_callback_does_not_break_the_chain(self):
        """The chain is the only thing that still ends uvicorn's serve loop."""

        def boom() -> None:
            raise RuntimeError("observer exploded")

        fake_handle_exit = Mock()
        signal.signal(signal.SIGTERM, fake_handle_exit)
        assert executor_module.install_shutdown_handler() is True

        executor_module.add_shutdown_callback(boom)
        later_calls: list[str] = []
        executor_module.add_shutdown_callback(lambda: later_calls.append("ran"))

        executor_module._request_shutdown(signal.SIGTERM)

        assert later_calls == ["ran"]
        fake_handle_exit.assert_called_once_with(signal.SIGTERM, None)


@pytest.mark.usefixtures("_isolated_signals")
class TestShutdownRequested:
    """The public predicate the SSE endpoints poll instead of the private flag."""

    async def test_false_before_any_signal(self):
        assert executor_module.shutdown_requested() is False

    async def test_true_after_shutdown_is_requested(self):
        executor_module._request_shutdown(signal.SIGTERM)

        assert executor_module.shutdown_requested() is True
