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
    val = cfg.get(key)
    if isinstance(val, Sequence) and not isinstance(val, str):
        return [str(item) for item in val]
    return []
