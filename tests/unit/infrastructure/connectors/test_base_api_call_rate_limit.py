"""Rate-limit wiring in BaseAPIClient._api_call.

The guarantee under test: a token is taken per attempt, so tenacity retries are
paced too — and a service without a configured rate limit keeps the exact
pre-pacing call path.
"""

from typing import ClassVar, override

from attrs import define, field
import pytest
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_none,
)

from src.infrastructure.connectors._shared.rate_limiting import ConnectorRateLimiter
from src.infrastructure.connectors.base import BaseAPIClient, service_name_for_client
from src.infrastructure.connectors.lastfm.client import LastFMAPIClient
from src.infrastructure.connectors.musicbrainz.client import MusicBrainzAPIClient
from src.infrastructure.connectors.spotify.client import SpotifyAPIClient


class CountingLimiter(ConnectorRateLimiter):
    """Records acquisitions and never waits."""

    def __init__(self) -> None:
        super().__init__(rate_per_second=1.0)
        self.acquired: int = 0

    @override
    async def acquire(self) -> None:
        self.acquired += 1


@define(slots=True)
class StubClient(BaseAPIClient):
    """Client with a real tenacity policy that retries ValueError three times."""

    _SUPPRESS_ERRORS: ClassVar[tuple[type[BaseException], ...]] = (KeyError,)
    _retry_policy: AsyncRetrying = field(init=False, repr=False)

    def __attrs_post_init__(self) -> None:
        self._retry_policy = AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_none(),
            retry=retry_if_exception_type(ValueError),
            reraise=True,
        )


def _patch_limiter(
    monkeypatch: pytest.MonkeyPatch, limiter: ConnectorRateLimiter | None
) -> None:
    monkeypatch.setattr(
        "src.infrastructure.connectors.base.get_connector_rate_limiter",
        lambda _service_name: limiter,
    )


class TestRetriesArePaced:
    """Every attempt takes a token, not just the first."""

    async def test_token_acquired_for_each_retry_attempt(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        limiter = CountingLimiter()
        _patch_limiter(monkeypatch, limiter)
        attempts = 0

        async def failing_impl() -> str:
            nonlocal attempts
            attempts += 1
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            _ = await StubClient()._api_call("op", failing_impl)

        assert attempts == 3
        assert limiter.acquired == 3

    async def test_token_acquired_before_a_successful_call(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        limiter = CountingLimiter()
        _patch_limiter(monkeypatch, limiter)

        async def impl(value: str) -> str:
            return value.upper()

        result = await StubClient()._api_call("op", impl, "ok")

        assert result == "OK"
        assert limiter.acquired == 1


class TestUnpacedServicesAreUnchanged:
    """A None limiter leaves the pre-existing call path intact."""

    async def test_impl_receives_args_and_result_passes_through(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        _patch_limiter(monkeypatch, None)
        seen: list[tuple[object, ...]] = []

        async def impl(*args: object) -> str:
            seen.append(args)
            return "done"

        result = await StubClient()._api_call("op", impl, "a", 1)

        assert result == "done"
        assert seen == [("a", 1)]

    async def test_suppressed_errors_still_return_none(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        _patch_limiter(monkeypatch, None)

        async def impl() -> str:
            raise KeyError("missing")

        assert await StubClient()._api_call("op", impl) is None

    async def test_suppression_still_applies_when_paced(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        _patch_limiter(monkeypatch, CountingLimiter())

        async def impl() -> str:
            raise KeyError("missing")

        assert await StubClient()._api_call("op", impl) is None


class TestServiceNameResolution:
    """Pins each live client to the settings key its pacing is read from."""

    @pytest.mark.parametrize(
        ("client_class", "expected"),
        [
            (SpotifyAPIClient, "spotify"),
            (LastFMAPIClient, "lastfm"),
            (MusicBrainzAPIClient, "musicbrainz"),
        ],
    )
    def test_client_resolves_to_its_settings_key(
        self, client_class: type[BaseAPIClient], expected: str
    ):
        assert service_name_for_client(client_class) == expected

    def test_client_outside_a_connector_package_has_no_service(self):
        assert service_name_for_client(StubClient) != "spotify"
