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

Constants (non-configurable system values):
    BusinessLimits, HTTPStatus, MatchMethod, MappingOrigin, ReviewStatus,
    WorkflowConstants, SpotifyConstants, LastFMConstants, SSEConstants,
    ConnectorPriority, DenormalizedTrackColumns, IntegrityConstants

Usage:
------
```python
from src.config import settings, get_logger, BusinessLimits

batch_size = settings.api.lastfm.batch_size
logger = get_logger(__name__)
```
"""

# Constants (non-configurable system values)
from .constants import (
    BusinessLimits,
    ConnectorPriority,
    DenormalizedTrackColumns,
    HTTPStatus,
    IntegrityConstants,
    LastFMConstants,
    MappingOrigin,
    MatchMethod,
    ReviewStatus,
    SpotifyConstants,
    SSEConstants,
    WorkflowConstants,
)
from .factories import create_evaluation_service, create_matching_config
from .logging import (
    enable_unified_console_output,
    get_logger,
    intercept_prefect_loggers,
    restore_standard_console_output,
    setup_loguru_logger,
    setup_script_logger,
)
from .settings import get_database_url, log_startup_warnings, settings

# Public API
__all__ = [
    "BusinessLimits",
    "ConnectorPriority",
    "DenormalizedTrackColumns",
    "HTTPStatus",
    "IntegrityConstants",
    "LastFMConstants",
    "MappingOrigin",
    "MatchMethod",
    "ReviewStatus",
    "SSEConstants",
    "SpotifyConstants",
    "WorkflowConstants",
    "create_evaluation_service",
    "create_matching_config",
    "enable_unified_console_output",
    "get_database_url",
    "get_logger",
    "intercept_prefect_loggers",
    "log_startup_warnings",
    "restore_standard_console_output",
    "settings",
    "setup_loguru_logger",
    "setup_script_logger",
]
