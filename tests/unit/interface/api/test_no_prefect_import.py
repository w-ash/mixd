"""Architectural guard: importing the API app must not import Prefect.

v0.7.8.5 won a zero-Prefect-modules-at-boot state by making every workflow-engine
import function-local; v0.8.1 removes Prefect entirely. This test locks the win in
so a future stray top-level ``import prefect`` can't silently regress cold start.

Runs in a clean subprocess on purpose: within a single pytest session another test
may already have imported the engine (and, pre-swap, Prefect with it), polluting
this process's ``sys.modules``. A fresh interpreter makes the assertion independent
of test ordering — and correct both before and after the swap (importing the app
never pulls Prefect; the engine import sites are all function-local).
"""

import subprocess
import sys


def test_importing_api_app_does_not_import_prefect():
    """A fresh interpreter that imports the FastAPI app has no 'prefect' module."""
    code = (
        "import sys; "
        "import src.interface.api.app; "
        "assert 'prefect' not in sys.modules, "
        "'prefect was imported at app boot: ' "
        "+ ', '.join(m for m in sys.modules if m.split('.')[0] == 'prefect')"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,  # we assert on returncode below to surface a useful message
    )
    assert proc.returncode == 0, proc.stderr
