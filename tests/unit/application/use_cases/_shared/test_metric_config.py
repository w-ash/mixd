"""The single home for the ``MetricConfigProvider`` factory default."""

from src.application.use_cases._shared.metric_config import default_metric_config


class TestDefaultMetricConfig:
    """``default_metric_config`` bridges to the concrete registry provider."""

    def test_provider_serves_the_connectors_declared_metrics(self) -> None:
        """A fresh provider reads the registry the connectors declared into."""
        provider = default_metric_config()

        assert "lastfm_user_playcount" in provider.get_connector_metrics("lastfm")
        assert "explicit_flag" in provider.get_all_connectors_metrics()["spotify"]
        # The metric name and its payload field differ for Spotify's flag.
        assert provider.get_field_name("explicit_flag") == "explicit"
        assert provider.get_all_field_mappings()["explicit_flag"] == "explicit"
        assert provider.get_metric_label("lastfm_user_playcount") == (
            "Play Count (Last.fm)"
        )
