"""The single home for the ``MetricConfigProvider`` factory default."""

from src.application.use_cases._shared.metric_config import default_metric_config


class TestDefaultMetricConfig:
    """``default_metric_config`` bridges to the concrete registry provider."""

    def test_default_metric_config_returns_a_provider(self) -> None:
        """Every ``MetricConfigProvider`` method answers, not just exists."""
        provider = default_metric_config()

        mappings = provider.get_all_field_mappings()
        connectors = provider.get_all_connectors_metrics()

        assert isinstance(mappings, dict)
        assert isinstance(connectors, dict)
        assert mappings, "registry should expose at least one metric field mapping"

        metric = next(iter(mappings))
        assert isinstance(provider.get_field_name(metric), str)
        assert isinstance(provider.get_metric_freshness(metric), float)

        connector = next(iter(connectors))
        assert isinstance(provider.get_connector_metrics(connector), list)
