"""Shared connector utilities.

This module provides common functionality used across all connectors:
- API batch processing utilities
- Error handling and retry logic  
- Consolidated metrics system
"""

from src.infrastructure.connectors._shared.api_batch_processor import APIBatchProcessor
from src.infrastructure.connectors._shared.error_classification import (
    DefaultErrorClassifier,
    ErrorClassifierProtocol,
    create_backoff_handler,
    create_giveup_handler,
    should_giveup_on_error,
)
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
from src.infrastructure.connectors._shared.retry_wrapper import RetryWrapper

__all__ = [
    # API Processing
    "APIBatchProcessor",
    "DefaultErrorClassifier",
    "ErrorClassifierProtocol",
    "MetricResolverProtocol",
    "RetryWrapper",
    # Error Handling
    "create_backoff_handler",
    "create_giveup_handler",
    # Metrics System  
    "get_all_connectors_metrics",
    "get_all_field_mappings",
    "get_connector_metrics",
    "get_field_name",
    "get_metric_freshness",
    "get_metric_resolver",
    "get_registered_metrics",
    "register_connector_metrics",
    "register_metric_config",
    "register_metric_resolver",
    "should_giveup_on_error",
]