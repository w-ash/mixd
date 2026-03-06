"""Configuration module for Narada.

This module provides a clean, type-safe configuration system using Pydantic Settings.

Public API:
----------
settings: Settings instance
    Modern Pydantic settings object with nested configuration

get_logger(name: str) -> Logger
    Get a context-aware logger for your module

setup_loguru_logger(verbose: bool = False) -> None
    Configure Loguru logger for the application

Usage:
------
```python
# Modern usage (recommended for new code)
from src.config import settings

batch_size = settings.api.lastfm.batch_size

# Logging
from src.config import get_logger

logger = get_logger(__name__)
logger.info("Starting operation")
```
"""

# Import everything from the submodules
from .logging import (
    get_logger,
    setup_loguru_logger,
    setup_script_logger,
)
from .settings import create_matching_config, settings

# Public API
__all__ = [
    "create_matching_config",
    "get_logger",
    "settings",
    "setup_loguru_logger",
    "setup_script_logger",
]
