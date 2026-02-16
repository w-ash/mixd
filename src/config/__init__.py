"""Configuration module for Narada.

This module provides a clean, type-safe configuration system using Pydantic Settings
with backward compatibility for existing code.

Public API:
----------
settings: Settings instance
    Modern Pydantic settings object with nested configuration

get_config(key: str, default=None) -> Any
    Backward-compatible configuration access function

get_logger(name: str) -> Logger
    Get a context-aware logger for your module

setup_loguru_logger(verbose: bool = False) -> None
    Configure Loguru logger for the application

resilient_operation(operation_name: str)
    Decorator for handling errors in external API calls

log_startup_info() -> None
    Log system configuration and API status at startup

configure_prefect_logging() -> None
    Configure Prefect to use our Loguru setup

Usage:
------
```python
# Modern usage (recommended for new code)
from src.config import settings

batch_size = settings.api.lastfm_batch_size

# Logging
from src.config import get_logger

logger = get_logger(__name__)
logger.info("Starting operation")
```
"""

# Import everything from the submodules
from .logging import (
    configure_prefect_logging,
    get_logger,
    log_startup_info,
    resilient_operation,
    setup_loguru_logger,
)
from .settings import settings

# Public API
__all__ = [
    "configure_prefect_logging",
    # Logging
    "get_logger",
    "log_startup_info",
    "resilient_operation",
    # Modern settings
    "settings",
    "setup_loguru_logger",
]
