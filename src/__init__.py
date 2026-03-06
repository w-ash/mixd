"""Narada source code root package."""

import tomllib
from pathlib import Path

_pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
__version__: str = tomllib.loads(_pyproject.read_text())["tool"]["poetry"]["version"]
