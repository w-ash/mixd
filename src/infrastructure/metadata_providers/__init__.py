"""Metadata providers for fetching track data using known external IDs.

This module provides the foundation for Phase 2 of the architecture refactor.
Metadata providers fetch fresh data for tracks whose external identities are
already known, separate from the identity resolution process.
"""

from __future__ import annotations
