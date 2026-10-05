"""Shared fixtures for the Last.fm integration suites.

Several suites patch ``LastFMConnector`` while an import runs. When connector
discovery runs for the first time under that patch, the process-wide
registry caches the Mock as the Last.fm factory, and every later test on the
worker receives it. Each test here gets the registry back as it found it.
"""

import pytest

from src.infrastructure.connectors import discovery


@pytest.fixture(autouse=True)
def restore_connector_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Discard any connector registry a test built while a class was patched."""
    monkeypatch.setattr(discovery, "_connectors_cache", discovery._connectors_cache)
