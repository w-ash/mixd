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
    """Read ``connector_playlist_identifier`` from a node config or raise.

    Fails fast at the workflow boundary rather than minutes later on an
    external "not found", and steers users away from the common mistake
    of pointing a connector node at a canonical mixd UUID.
    """
    raw = cfg.get("connector_playlist_identifier")
    if not isinstance(raw, str) or not raw:
        raise ValueError(
            f"{node} with connector={connector!r} requires "
            "'connector_playlist_identifier' in config (the external "
            "service's playlist ID, e.g., a Spotify base62 string). "
            f"Got config keys: {sorted(cfg.keys())}. If you meant the "
            "canonical mixd playlist, remove the 'connector' field and "
            "set 'playlist_id' to the canonical UUID."
        )
    return ConnectorPlaylistIdentifier(raw)


def require_canonical_playlist_uuid(cfg: Mapping[str, JsonValue], *, node: str) -> str:
    """Read ``playlist_id`` as a canonical UUID string or raise.

    Returns the normalized string form (``str(UUID(raw))``). Validates the
    UUID shape so a Spotify ID accidentally placed here fails at parse
    time with an explanatory message instead of producing an unrelated
    downstream error.
    """
    raw = cfg.get("playlist_id")
    if not isinstance(raw, str) or not raw:
        raise ValueError(
            f"{node} requires 'playlist_id' (the canonical mixd playlist "
            "UUID) when no connector is set. If you meant an external "
            "service, add 'connector': 'spotify' (or 'apple_music' etc.) "
            "and use 'connector_playlist_identifier' for the service's ID."
        )
    try:
        return str(UUID(raw))
    except ValueError as e:
        raise ValueError(
            f"{node} 'playlist_id' must be a canonical UUID; got {raw!r}. "
            "If this is a Spotify playlist ID, set 'connector': 'spotify' "
            "and rename the field to 'connector_playlist_identifier'."
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
