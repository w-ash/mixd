"""Tests for the lifespan's sse-starlette shutdown bridge.

Installing our own signal handler displaces uvicorn's, which silently breaks
sse-starlette's two ways of noticing a shutdown: its `Server.handle_exit`
monkey-patch (already too late — uvicorn binds the method before importing this
app) and its fallback that recovers the Server from
`signal.getsignal(SIGTERM).__self__` (now asyncio's trampoline). The `/mcp` mount
streams over sse-starlette, so without this bridge an open MCP stream holds
uvicorn's connection drain for the whole graceful-shutdown window.
"""

from collections.abc import Iterator

import pytest
from sse_starlette.sse import AppStatus

from src.interface.api.app import _drain_sse_starlette_streams


@pytest.fixture(autouse=True)
def _restore_app_status() -> Iterator[None]:
    """`AppStatus.should_exit` is module-global and never cleared by the library."""
    original = AppStatus.should_exit
    yield
    AppStatus.should_exit = original


class TestDrainSseStarletteStreams:
    def test_sets_the_flag_sse_starlette_watches(self) -> None:
        AppStatus.should_exit = False

        _drain_sse_starlette_streams()

        assert AppStatus.should_exit is True

    def test_is_safe_to_run_twice(self) -> None:
        """Two signals in a row is normal; the bridge must stay idempotent."""
        AppStatus.should_exit = False

        _drain_sse_starlette_streams()
        _drain_sse_starlette_streams()

        assert AppStatus.should_exit is True
