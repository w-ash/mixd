"""Typed config value accessors for workflow node configuration.

Config values are validated at parse time (validation.py). These accessors
narrow JsonValue to concrete types for downstream domain functions.
"""

from collections.abc import Mapping, Sequence
from typing import overload

from src.domain.entities.shared import JsonValue


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
