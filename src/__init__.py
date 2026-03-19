"""Narada source code root package."""

from pathlib import Path
import tomllib


def _read_version() -> str:
    """Read version from pyproject.toml (single source of truth)."""
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    with pyproject.open("rb") as f:
        return tomllib.load(f)["project"]["version"]


__version__: str = _read_version()
