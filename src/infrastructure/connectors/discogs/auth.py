"""Discogs authentication strategies.

Discogs auth is BYO personal access token (v0.11.1 decision): the token is
validated live at connect time, stored encrypted via token storage, and
injected here as ``Authorization: Discogs token=<token>``. The client takes
any ``httpx2.Auth``, so an OAuth 1.0a strategy (RFC 5849 request signing —
zero code for it exists today) can slot in later without touching the client
if write/marketplace scopes are ever needed. Auth choice does not change
throughput: personal tokens and OAuth share the same per-IP 60/min bucket.

Reference: https://www.discogs.com/developers#page:authentication
"""

import collections.abc
from typing import override

import httpx2


class DiscogsTokenAuth(httpx2.Auth):
    """httpx2 auth flow injecting the personal-access-token header.

    Sync flow on purpose — the token is a constructor-supplied string (no
    I/O, no storage coupling, no refresh endpoint), so one flow serves both
    sync and async clients. Loading the token from storage is the API
    client's job; this class only knows how to present one.
    """

    _token: str

    def __init__(self, token: str) -> None:
        self._token = token

    @override
    def auth_flow(
        self, request: httpx2.Request
    ) -> collections.abc.Generator[httpx2.Request, httpx2.Response]:
        request.headers["Authorization"] = f"Discogs token={self._token}"
        yield request
