"""Typed config value accessors for workflow node configuration.

Config values are validated at parse time (validation.py). These accessors
narrow JsonValue to concrete types for downstream domain functions.
"""

from collections.abc import Mapping, Sequence
from typing import overload
from uuid import UUID

from src.domain.entities.shared import ConnectorPlaylistIdentifier, JsonValue


def cfg_str(cfg: Mapping[str, JsonValue], key: str, default: str = "") -> str:
    val = cfg.get(key, default)
    return str(val) if val is not None else default


def cfg_str_or_none(cfg: Mapping[str, JsonValue], key: str) -> str | None:
    val = cfg.get(key)
    return str(val) if val is not None else None


@overload
def cfg_int(cfg: Mapping[str, JsonValue], key: str, default: int) -> int: ...
@overload
def cfg_int(
    cfg: Mapping[str, JsonValue], key: str, default: int | None = None
) -> int | None: ...
def cfg_int(
    cfg: Mapping[str, JsonValue], key: str, default: int | None = None
) -> int | None:
    val = cfg.get(key, default)
    if isinstance(val, bool):
        return default
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        return round(val)
    return default


@overload
def cfg_float(cfg: Mapping[str, JsonValue], key: str, default: float) -> float: ...
@overload
def cfg_float(
    cfg: Mapping[str, JsonValue], key: str, default: float | None = None
) -> float | None: ...
def cfg_float(
    cfg: Mapping[str, JsonValue], key: str, default: float | None = None
) -> float | None:
    val = cfg.get(key, default)
    if isinstance(val, bool):
        return default
    return float(val) if isinstance(val, (int, float)) else default


def cfg_bool(cfg: Mapping[str, JsonValue], key: str, default: bool = False) -> bool:
    val = cfg.get(key, default)
    return val if isinstance(val, bool) else default


def require_connector_playlist_identifier(
    cfg: Mapping[str, JsonValue], *, node: str, connector: str
) -> ConnectorPlaylistIdentifier:
    """Read the connector's playlist ID from ``cfg["playlist_id"]`` or raise.

    KNOWN TERMINOLOGY EXCEPTION — fix eventually.
    The project rule is: ``playlist_id`` always means ``playlists.id`` (the
    canonical mixd UUID); ``connector_playlist_identifier`` always means the
    external service's own ID (Spotify base62, Apple Music alphanumeric).
    Source/destination playlist nodes violate that rule: when ``connector``
    is set, ``playlist_id`` here carries the *connector identifier*, not a
    canonical UUID. The schema in ``nodes/config_fields.py``, every seed JSON
    under ``definitions/``, and the web editor all currently emit this
    polymorphic shape, so reading it as-is is the only thing that doesn't
    silently break saved workflows. The proper fix is to split the field
    into ``playlist_id`` (canonical-only) and ``connector_playlist_identifier``
    (connector-only), rewrite the seed JSONs, and re-seed personal workflows.
    """
    raw = cfg.get("playlist_id")
    if not isinstance(raw, str) or not raw:
        raise ValueError(
            f"{node} with connector={connector!r} is missing 'playlist_id' "
            f"(the {connector} playlist ID)."
        )
    return ConnectorPlaylistIdentifier(raw)


def require_canonical_playlist_uuid(cfg: Mapping[str, JsonValue], *, node: str) -> str:
    """Read the canonical mixd playlist UUID from ``cfg["playlist_id"]`` or raise.

    Returns the normalized string form (``str(UUID(raw))``). When no
    ``connector`` is set, ``playlist_id`` must be a canonical UUID — the
    UUID parse rejects connector-shaped IDs at the boundary instead of
    surfacing as an opaque downstream error.
    """
    raw = cfg.get("playlist_id")
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"{node} is missing 'playlist_id'.")
    try:
        return str(UUID(raw))
    except ValueError as e:
        raise ValueError(
            f"{node} 'playlist_id' must be a canonical mixd UUID when no "
            f"connector is set; got {raw!r}. To target an external service, "
            "set 'connector' (e.g. 'spotify')."
        ) from e


def cfg_str_list(cfg: Mapping[str, JsonValue], key: str) -> list[str]:
    """Read a list of strings from config.

    Accepts both a JSON array (``["a", "b"]``) and a comma-separated string
    (``"a, b"``) — the UI edits the latter while the raw workflow JSON uses
    the former. Empty strings and surrounding whitespace are stripped.
    """
    val = cfg.get(key)
    if isinstance(val, str):
        return [item for piece in val.split(",") if (item := piece.strip())]
    if isinstance(val, Sequence):
        return [s for item in val if (s := str(item).strip())]
    return []
