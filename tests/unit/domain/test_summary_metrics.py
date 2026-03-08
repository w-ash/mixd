"""Tests for self-describing summary metric domain types."""

from src.domain.entities.summary_metrics import SummaryMetric, SummaryMetricCollection


class TestSummaryMetric:
    """Test SummaryMetric domain entity."""

    def test_summary_metric_creation_with_defaults(self):
        """Test creating summary metric with default values."""
        metric = SummaryMetric(name="imported", value=97, label="Likes Imported")

        assert metric.name == "imported"
        assert metric.value == 97
        assert metric.label == "Likes Imported"
        assert metric.format == "count"  # default
        assert metric.significance == 0  # default

    def test_summary_metric_with_percent_format(self):
        """Test summary metric with percentage format."""
        metric = SummaryMetric(
            name="success_rate",
            value=94.5,
            label="Success Rate",
            format="percent",
        )

        assert metric.format == "percent"
        assert metric.value == 94.5

    def test_summary_metric_with_duration_format(self):
        """Test summary metric with duration format."""
        metric = SummaryMetric(
            name="elapsed",
            value=2.3,
            label="Processing Time",
            format="duration",
        )

        assert metric.format == "duration"
        assert metric.value == 2.3

    def test_summary_metric_with_significance(self):
        """Test summary metric with custom significance for ordering."""
        metric = SummaryMetric(
            name="secondary",
            value=10,
            label="Secondary Metric",
            significance=5,
        )

        assert metric.significance == 5


class TestSummaryMetricCollection:
    """Test SummaryMetricCollection for managing multiple summary metrics."""

    def test_empty_collection(self):
        """Test creating empty summary metric collection."""
        collection = SummaryMetricCollection()

        assert len(collection.metrics) == 0
        assert collection.sorted() == []

    def test_add_summary_metric(self):
        """Test adding summary metric to collection."""
        collection = SummaryMetricCollection()
        collection.add("imported", 97, "Likes Imported")

        assert len(collection.metrics) == 1
        assert collection.metrics[0].name == "imported"
        assert collection.metrics[0].value == 97
        assert collection.metrics[0].label == "Likes Imported"

    def test_add_summary_metric_with_format(self):
        """Test adding summary metric with format specification."""
        collection = SummaryMetricCollection()
        collection.add("rate", 94.5, "Success Rate", format="percent")

        metric = collection.metrics[0]
        assert metric.format == "percent"

    def test_add_summary_metric_with_significance(self):
        """Test adding summary metric with significance for ordering."""
        collection = SummaryMetricCollection()
        collection.add("test", 1, "Test", significance=3)

        assert collection.metrics[0].significance == 3

    def test_sorted_by_significance(self):
        """Test summary metrics are sorted by significance (lower = higher priority)."""
        collection = SummaryMetricCollection()
        collection.add("third", 3, "Third", significance=2)
        collection.add("first", 1, "First", significance=0)
        collection.add("second", 2, "Second", significance=1)

        sorted_metrics = collection.sorted()

        assert len(sorted_metrics) == 3
        assert sorted_metrics[0].label == "First"
        assert sorted_metrics[1].label == "Second"
        assert sorted_metrics[2].label == "Third"

    def test_sorted_preserves_original_order_for_same_significance(self):
        """Test that sorted preserves insertion order when significance is equal."""
        collection = SummaryMetricCollection()
        collection.add("a", 1, "A", significance=0)
        collection.add("b", 2, "B", significance=0)
        collection.add("c", 3, "C", significance=0)

        sorted_metrics = collection.sorted()

        # Python's sorted is stable, so equal significance preserves order
        assert sorted_metrics[0].label == "A"
        assert sorted_metrics[1].label == "B"
        assert sorted_metrics[2].label == "C"

    def test_get_returns_value_by_name(self):
        """Test get() retrieves metric value by name."""
        collection = SummaryMetricCollection()
        collection.add("imported", 97, "Likes Imported")
        collection.add("errors", 3, "Errors")

        assert collection.get("imported") == 97
        assert collection.get("errors") == 3

    def test_get_returns_default_when_not_found(self):
        """Test get() returns default value for missing metric."""
        collection = SummaryMetricCollection()
        collection.add("imported", 97, "Likes Imported")

        assert collection.get("missing") == 0
        assert collection.get("missing", 42) == 42

    def test_get_on_empty_collection(self):
        """Test get() on empty collection returns default."""
        collection = SummaryMetricCollection()

        assert collection.get("anything") == 0
        assert collection.get("anything", -1) == -1

    def test_multiple_summary_metrics_with_different_formats(self):
        """Test collection with mixed format types."""
        collection = SummaryMetricCollection()
        collection.add("count", 100, "Items", format="count")
        collection.add("rate", 95.5, "Success Rate", format="percent")
        collection.add("time", 1.5, "Duration", format="duration")

        assert len(collection.metrics) == 3
        assert collection.metrics[0].format == "count"
        assert collection.metrics[1].format == "percent"
        assert collection.metrics[2].format == "duration"
