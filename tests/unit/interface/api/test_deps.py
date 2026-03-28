"""Tests for FastAPI auth dependencies.

Verifies that get_current_user_id correctly extracts the JWT sub claim
from the ASGI scope, falling back to DEFAULT_USER_ID when auth is
disabled or claims are missing.
"""

from starlette.requests import Request

from src.config.constants import BusinessLimits
from src.interface.api.deps import get_current_user_id


def _make_request(scope_extras: dict | None = None) -> Request:
    """Build a minimal Starlette Request with the given scope additions."""
    scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
    if scope_extras:
        scope.update(scope_extras)
    return Request(scope)


class TestGetCurrentUserId:
    """Extracting user ID from Neon Auth JWT claims on the request scope."""

    def test_returns_sub_from_auth_claims(self):
        request = _make_request({
            "auth_user": {"sub": "usr_abc123", "email": "a@b.com"}
        })
        assert get_current_user_id(request) == "usr_abc123"

    def test_returns_default_when_no_auth_user_in_scope(self):
        request = _make_request()
        assert get_current_user_id(request) == BusinessLimits.DEFAULT_USER_ID

    def test_returns_default_when_claims_lack_sub(self):
        request = _make_request({"auth_user": {"email": "a@b.com"}})
        assert get_current_user_id(request) == BusinessLimits.DEFAULT_USER_ID
