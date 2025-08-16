"""Shared connector utilities.

This module provides common functionality used across all connectors:
- API batch processing utilities
- Error handling and retry logic
- Consolidated metrics system
- Matching provider base protocols and utilities
"""

from src.infrastructure.connectors._shared.api_batch_processor import APIBatchProcessor
from src.infrastructure.connectors._shared.error_classification import (
    DefaultErrorClassifier,
    ErrorClassifierProtocol,
    create_backoff_handler,
    create_giveup_handler,
    should_giveup_on_error,
)
from src.infrastructure.connectors._shared.failure_logging import (
    log_failure_summary,
    log_match_failure,
)
from src.infrastructure.connectors._shared.failure_utils import (
    create_and_log_failure,
    handle_track_processing_failure,
    merge_results,
    validate_track_for_method,
)
from src.infrastructure.connectors._shared.matching_provider_base import MatchProvider
from src.infrastructure.connectors._shared.metrics import (
    MetricResolverProtocol,
    get_all_connectors_metrics,
    get_all_field_mappings,
    get_connector_metrics,
    get_field_name,
    get_metric_freshness,
    get_metric_resolver,
    get_registered_metrics,
    register_connector_metrics,
    register_metric_config,
    register_metric_resolver,
)

# Provider registry imports removed to prevent circular dependencies
from src.infrastructure.connectors._shared.retry_wrapper import RetryWrapper

__all__ = [
    "APIBatchProcessor",
    "DefaultErrorClassifier",
    "ErrorClassifierProtocol",
    "MatchProvider",
    "MetricResolverProtocol",
    "RetryWrapper",
    "create_and_log_failure",
    "create_backoff_handler",
    "create_giveup_handler",
    "get_all_connectors_metrics",
    "get_all_field_mappings",
    "get_connector_metrics",
    "get_field_name",
    "get_metric_freshness",
    "get_metric_resolver",
    "get_registered_metrics",
    "handle_track_processing_failure",
    "log_failure_summary",
    "log_match_failure",
    "merge_results",
    "register_connector_metrics",
    "register_metric_config",
    "register_metric_resolver",
    "should_giveup_on_error",
    "validate_track_for_method",
]
