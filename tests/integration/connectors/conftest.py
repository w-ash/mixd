"""Shared fixtures for connector integration tests.

Every suite under this directory runs with the connector rate limiter's
pacing paths disabled — pacing sleeps would make transport-boundary tests
nondeterministic. Only the two paths that sleep are patched: per-attempt
token acquisition (base) and the 429 pause (retry_policies). The limiter
resolved inside ``rate_limiting`` itself stays real so ``connector_call_slot``
keeps its serialization semantics; it cannot sleep with acquisition patched
off, and the limiter cache is per-event-loop, so no pause state crosses
tests.
"""

import pytest


@pytest.fixture(autouse=True)
def no_rate_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests deterministic — no pacing sleeps, no cross-test pause state."""
    monkeypatch.setattr(
        "src.infrastructure.connectors.base.get_connector_rate_limiter",
        lambda _service_name: None,
    )
    monkeypatch.setattr(
        "src.infrastructure.connectors._shared.retry_policies.get_connector_rate_limiter",
        lambda _service_name: None,
    )


@pytest.fixture
def retry_sleeps() -> list[float]:
    """Sleep durations tenacity would have waited, in order."""
    return []
