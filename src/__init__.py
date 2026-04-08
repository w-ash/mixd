"""Mixd source code root package."""

from pathlib import Path
import tomllib
from typing import cast


def _read_version() -> str:
    """Read version from pyproject.toml (single source of truth)."""
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    with pyproject.open("rb") as f:
        data = tomllib.load(f)
    return cast(str, data["project"]["version"])


__version__: str = _read_version()
