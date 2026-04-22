"""Connector configuration TypedDict.

Wires Python factories, FastAPI request plumbing, and credential storage
to the domain vocabulary (``src.domain.entities.connector``).
"""

from collections.abc import Awaitable, Callable, Coroutine, Mapping
from typing import TYPE_CHECKING, TypedDict

from src.domain.entities.connector import (
    Capability,
    ConnectorAuthMethod,
    ConnectorCategory,
    ConnectorStatus,
)
from src.domain.entities.shared import JsonValue
from src.infrastructure.connectors._shared.token_storage import TokenStorage

if TYPE_CHECKING:
    from fastapi import Request

# Factory the route handler passes to connector ``build_auth_url`` callables.
# Matches the signature of ``src.interface.api.routes.auth._create_state``:
# ``(user_id, service, *, code_verifier=None) -> state_token``.
CreateStateFn = Callable[..., Awaitable[str]]

# Signature of each connector's ``build_auth_url`` entry point.
# Returns the external provider's authorization URL; the frontend redirects to it.
BuildAuthUrlFn = Callable[[str, "Request", CreateStateFn], Awaitable[str]]


class ConnectorConfig(TypedDict):
    """Declarative registry entry for a music service connector.

    The API layer serializes the runtime result of a registry lookup into a
    ``ConnectorMetadataSchema`` payload so the frontend can render connectors
    generically. ``factory`` / ``status_fn`` / ``build_auth_url`` are the
    three pieces of real connector-specific code — everything else is
    declarative metadata.
    """

    factory: Callable[[Mapping[str, JsonValue]], object]
    dependencies: list[str]
    metrics: dict[str, str]
    display_name: str
    category: ConnectorCategory
    auth_method: ConnectorAuthMethod
    capabilities: frozenset[Capability]
    status_fn: Callable[
        [str, TokenStorage | None], Coroutine[object, object, ConnectorStatus]
    ]
    build_auth_url: BuildAuthUrlFn | None
