"""Connector configuration TypedDict.

Wires Python factories, FastAPI request plumbing, and credential storage
to the domain vocabulary (``src.domain.entities.connector``).
"""

from collections.abc import Awaitable, Callable, Coroutine, Mapping
from typing import TYPE_CHECKING, NotRequired, TypedDict

from src.domain.entities.connector import (
    Capability,
    ConnectorAuthMethod,
    ConnectorCategory,
    ConnectorStatus,
)
from src.domain.matching.protocols import CrossDiscoveryProvider
from src.domain.repositories.play import (
    ImportKind,
    PlayImporterProtocol,
    PlayResolverProtocol,
)
from src.infrastructure.connectors._shared.token_storage import TokenStorage

if TYPE_CHECKING:
    # fastapi (~160ms import) is used only in annotations here; the guard
    # keeps it out of the CLI's connector import path.
    from fastapi import Request

# Factory the route handler passes to connector ``build_auth_url`` callables.
# Matches the signature of ``src.interface.api.routes.auth._create_state``:
# ``(user_id, service, *, code_verifier=None) -> state_token``.
CreateStateFn = Callable[..., Awaitable[str]]

# Signature of each connector's ``build_auth_url`` entry point.
# Returns the external provider's authorization URL; the frontend redirects to it.
BuildAuthUrlFn = Callable[[str, "Request", CreateStateFn], Awaitable[str]]

# Zero-arg builders, mirroring ``factory``: any heavy imports stay inside the
# callable so declaring one adds no import-time weight.
type PlayImporterFactory = Callable[[], PlayImporterProtocol]
type PlayResolverFactory = Callable[[], PlayResolverProtocol]
type CrossDiscoveryFactory = Callable[[], CrossDiscoveryProvider]

# Connector-side cleanup run after a user disconnects; receives the user id.
type DisconnectHook = Callable[[str], Awaitable[None]]


class ConnectorConfig(TypedDict):
    """Declarative registry entry for a music service connector.

    The API layer serializes the runtime result of a registry lookup into a
    ``ConnectorMetadataSchema`` payload so the frontend can render connectors
    generically. ``factory`` / ``status_fn`` / ``build_auth_url`` are the
    three pieces of real connector-specific code — everything else is
    declarative metadata. ``metrics`` (metric name → metadata field) and the
    optional ``metric_freshness_hours`` feed the metric registry: connector
    discovery registers them via ``register_metrics``.

    Play wiring is config-declared so a new connector's channels need no
    edits outside its package. ``play_importer_factories`` (keyed by
    ``ImportKind``) and ``play_resolver_factory`` feed the
    ``PlayImportServiceRegistry`` lookup; ``play_service_name`` names the
    data-plane service its play rows key on when that differs from the
    package name (``apple_music`` → ``"apple"``). ``cross_discovery_factory``
    marks the connector as a cross-discovery source other connectors resolve
    through discovery instead of a concrete import. ``on_disconnect`` is an
    optional connector-side cleanup hook the disconnect route awaits.
    ``supports_play_polling`` gates the adaptive play-polling surface: the
    poll policy itself is application-owned (``play_poll_policy``), so the
    interface keys its enable/teardown calls off this flag — configs cannot
    reference application code.
    """

    factory: Callable[[], object]
    metrics: dict[str, str]
    metric_freshness_hours: NotRequired[float]
    display_name: str
    category: ConnectorCategory
    auth_method: ConnectorAuthMethod
    capabilities: frozenset[Capability]
    status_fn: Callable[
        [str, TokenStorage | None], Coroutine[object, object, ConnectorStatus]
    ]
    build_auth_url: BuildAuthUrlFn | None
    play_service_name: NotRequired[str]
    play_importer_factories: NotRequired[Mapping[ImportKind, PlayImporterFactory]]
    play_resolver_factory: NotRequired[PlayResolverFactory]
    cross_discovery_factory: NotRequired[CrossDiscoveryFactory]
    on_disconnect: NotRequired[DisconnectHook]
    supports_play_polling: NotRequired[bool]
