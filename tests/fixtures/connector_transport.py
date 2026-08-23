"""Shared transport-boundary doubles for connector client integration tests.

The connector ``test_client`` suites (Apple Music, Discogs, Tidal) all build
a real API client over an ``httpx2.MockTransport`` — same fake token
storage, same request recorder, same tenacity sleep capture. This module is
the single home for those byte-identical pieces; per-connector conftests
keep only what genuinely differs (payload builders + client wiring).
"""

from collections.abc import Awaitable, Callable, Mapping

import httpx2

from src.infrastructure.connectors._shared.token_storage import StoredToken

type Handler = Callable[[httpx2.Request], httpx2.Response | Awaitable[httpx2.Response]]


class FakeTokenStorage:
    """In-memory TokenStorage double recording loads and saves.

    Set ``fail_saves`` to make every write raise — for asserting that a
    storage failure surfaces instead of being swallowed.
    """

    def __init__(self, token: StoredToken | None = None) -> None:
        self.token = token
        self.loads: list[tuple[str, str]] = []
        self.saved: list[tuple[str, str, StoredToken]] = []
        self.extra_updates: list[tuple[str, str, dict[str, object]]] = []
        self.fail_saves = False

    async def load_token(self, service: str, user_id: str) -> StoredToken | None:
        self.loads.append((service, user_id))
        return self.token

    async def save_token(
        self, service: str, user_id: str, token_data: StoredToken
    ) -> None:
        if self.fail_saves:
            raise RuntimeError("storage write failed")
        self.token = token_data
        self.saved.append((service, user_id, token_data))

    async def update_extra_data(
        self,
        service: str,
        user_id: str,
        updates: Mapping[str, object],
        *,
        account_name: str | None = None,
    ) -> None:
        if self.fail_saves:
            raise RuntimeError("storage write failed")
        self.extra_updates.append((service, user_id, dict(updates)))
        if self.token is None:
            return
        merged = dict(self.token.get("extra_data") or {})
        merged.update(updates)
        self.token["extra_data"] = merged
        if account_name is not None:
            self.token["account_name"] = account_name

    async def delete_token(self, service: str, user_id: str) -> None:
        self.token = None


def recording(handler):
    """Wrap a handler so every request is captured for assertions."""
    requests: list[httpx2.Request] = []

    def _handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return handler(request)

    return _handler, requests


def sleep_recorder(retry_sleeps: list[float]):
    """Async sleep replacement that records durations instead of waiting.

    Swap it in for a tenacity policy's ``sleep`` so ``RetryAfterWait`` stays
    real and tests can assert the exact honored waits without wall time.
    """
    import asyncio

    async def record_sleep(seconds: float) -> None:
        retry_sleeps.append(seconds)
        await asyncio.sleep(0)

    return record_sleep
