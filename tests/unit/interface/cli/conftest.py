"""CLI test fixtures.

Pins a wide, deterministic terminal size for every test in this directory.
Rich's ``Console`` and Click's error formatter auto-detect terminal width
from the environment; under some CI runners that detection resolves to an
unexpectedly narrow value, which truncates rendered output mid-word (seen
in ``test_sync_commands.py::test_cadence_requires_at``, which asserts on a
literal substring of Typer's ``--at HH:MM is required`` error text). Pinning
``COLUMNS``/``LINES`` wide removes the dependency on ambient detection —
widening can only reduce wrapping/truncation, never introduce it.
"""

import pytest


@pytest.fixture(autouse=True)
def _wide_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("LINES", "50")
