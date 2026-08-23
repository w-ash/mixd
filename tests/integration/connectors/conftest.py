"""Shared fixtures for connector integration tests.

Every suite under this directory runs with the process-global connector
rate limiter disabled — pacing sleeps and cross-test pause state would make
transport-boundary tests nondeterministic. The limiter is resolved through
three module bindings, and all three are patched so no suite depends on a
leaked pacing path.
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
    # pacer.apply_rate_headers resolves the limiter through its own module
    # binding — without this patch, a canned low-remaining rate header would
    # pause the process-global Discogs limiter and brake later tests.
    monkeypatch.setattr(
        "src.infrastructure.connectors.discogs.pacer.get_connector_rate_limiter",
        lambda _service_name: None,
    )


@pytest.fixture
def retry_sleeps() -> list[float]:
    """Sleep durations tenacity would have waited, in order."""
    return []
