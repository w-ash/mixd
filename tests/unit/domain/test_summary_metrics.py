"""Tests for the summary metric collection: display order, lookup, format hints."""

from src.domain.entities.summary_metrics import SummaryMetricCollection


class TestSummaryMetricCollection:
    """Test SummaryMetricCollection for managing multiple summary metrics."""

    def test_add_carries_the_format_hint(self):
        """Format defaults to a count; percent and duration pass through."""
        collection = SummaryMetricCollection()
        collection.add("count", 100, "Items")
        collection.add("rate", 95.5, "Success Rate", format="percent")
        collection.add("time", 1.5, "Duration", format="duration")

        assert [m.format for m in collection.metrics] == [
            "count",
            "percent",
            "duration",
        ]

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
