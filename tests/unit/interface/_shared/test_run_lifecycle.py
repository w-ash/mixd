"""Unit tests for the shared run-lifecycle helpers.

These helpers are shared by the CLI and API (``src/interface/_shared/
run_lifecycle.py``) so the run lifecycle lives in one place. They are thin
DB-session adapters; the *ticker* that calls ``bump_heartbeat`` on a cadence
belongs to ``ExecuteWorkflowRunUseCase`` and is tested alongside it in
``tests/unit/application/use_cases/test_workflow_runs.py``.
"""

from unittest.mock import patch
from uuid import uuid4

import src.interface._shared.run_lifecycle as rl


class TestBumpHeartbeat:
    async def test_suppresses_errors(self) -> None:
        """Heartbeats are advisory — a DB blip during a bump must not propagate."""
        run_id = uuid4()

        with patch.object(rl, "run_repo_session", side_effect=RuntimeError("db down")):
            await rl.bump_heartbeat(run_id)  # must not raise
