"""Unit tests for the Discogs connect-time token validation service.

``validate_and_build_token`` probes ``/oauth/identity`` with a throwaway
client built around the submitted token, reads the collection count from a
single ``per_page=1`` collection page, and returns the ``StoredToken`` both
the PUT route and ``mixd discogs connect`` persist. The API client is
mocked (AsyncMock per connector-test idiom); under test are the
``StoredToken`` shape, the actionable invalid-token error, and that the
throwaway client is always closed.
"""

import time
from unittest.mock import AsyncMock, patch

import pytest

from src.domain.exceptions import DiscogsAuthRequiredError, DiscogsInvalidTokenError
from src.infrastructure.connectors.discogs.auth import DiscogsTokenAuth
from src.infrastructure.connectors.discogs.models import (
    DiscogsCollectionPage,
    DiscogsIdentity,
    DiscogsPagination,
)
from src.infrastructure.connectors.discogs.token_service import (
    validate_and_build_token,
)

_SVC = "src.infrastructure.connectors.discogs.token_service"


def _page(items: int) -> DiscogsCollectionPage:
    return DiscogsCollectionPage(
        pagination=DiscogsPagination(page=1, pages=1, per_page=1, items=items),
        releases=[],
    )


def _mock_client(
    identity: DiscogsIdentity | None,
    page: DiscogsCollectionPage | None = None,
    identity_error: Exception | None = None,
) -> AsyncMock:
    client = AsyncMock()
    if identity_error is not None:
        client.get_identity = AsyncMock(side_effect=identity_error)
    else:
        client.get_identity = AsyncMock(return_value=identity)
    client.get_collection_page = AsyncMock(return_value=page)
    return client


class TestValidateAndBuildToken:
    async def test_valid_token_builds_stored_token(self) -> None:
        client = _mock_client(DiscogsIdentity(id=1, username="wash"), _page(42))
        with patch(f"{_SVC}.DiscogsAPIClient", return_value=client) as client_cls:
            stored = await validate_and_build_token("tok-123")

        # The throwaway client is built around the submitted token's auth.
        auth = client_cls.call_args.args[0]
        assert isinstance(auth, DiscogsTokenAuth)
        assert stored["access_token"] == "tok-123"
        assert stored["token_type"] == "personal_token"
        assert stored["account_name"] == "wash"
        extra = stored["extra_data"]
        assert extra["collection_count"] == 42
        validated_at = extra["validated_at"]
        assert isinstance(validated_at, int)
        assert abs(validated_at - time.time()) < 10
        client.get_collection_page.assert_awaited_once_with("wash", per_page=1)
        client.aclose.assert_awaited_once()

    async def test_zero_item_collection_stores_zero_count(self) -> None:
        client = _mock_client(DiscogsIdentity(id=1, username="wash"), _page(0))
        with patch(f"{_SVC}.DiscogsAPIClient", return_value=client):
            stored = await validate_and_build_token("tok-123")

        assert stored["extra_data"]["collection_count"] == 0

    async def test_rejected_token_raises_invalid_token_error(self) -> None:
        # The connect-time type — the middleware maps it to a 400
        # DISCOGS_INVALID_TOKEN envelope, distinct from the parent's 409.
        client = _mock_client(
            None, identity_error=DiscogsAuthRequiredError("401 from client")
        )
        with (
            patch(f"{_SVC}.DiscogsAPIClient", return_value=client),
            pytest.raises(DiscogsInvalidTokenError, match="rejected"),
        ):
            await validate_and_build_token("bad-token")
        client.aclose.assert_awaited_once()

    async def test_unreachable_discogs_raises_invalid_token_error(self) -> None:
        # A suppressed transport failure surfaces as get_identity() → None.
        client = _mock_client(None)
        with (
            patch(f"{_SVC}.DiscogsAPIClient", return_value=client),
            pytest.raises(DiscogsInvalidTokenError, match="reach"),
        ):
            await validate_and_build_token("tok-123")
        client.aclose.assert_awaited_once()

    async def test_missing_collection_page_omits_count(self) -> None:
        client = _mock_client(DiscogsIdentity(id=1, username="wash"), page=None)
        with patch(f"{_SVC}.DiscogsAPIClient", return_value=client):
            stored = await validate_and_build_token("tok-123")

        assert "collection_count" not in stored["extra_data"]
        assert stored["account_name"] == "wash"
