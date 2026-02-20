"""Shared connector utilities.

This module provides common functionality used across all connectors:
- Error handling and retry logic
- Consolidated metrics system
- Matching provider base protocols and utilities
"""

from src.domain.matching.protocols import MatchProvider
from src.infrastructure.connectors._shared.error_classification import (
    ErrorClassifier,
    classify_unknown_error,
)
from src.infrastructure.connectors._shared.failure_handling import (
    create_and_log_failure,
    handle_track_processing_failure,
    log_failure_summary,
    log_match_failure,
    merge_results,
    validate_track_for_method,
)
from src.infrastructure.connectors._shared.metrics import (
    MetricResolveFn,
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

__all__ = [
    "ErrorClassifier",
    "MatchProvider",
    "MetricResolveFn",
    "MetricResolverProtocol",
    "classify_unknown_error",
    "create_and_log_failure",
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
    "validate_track_for_method",
]
