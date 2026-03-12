"""Tests for data integrity monitoring domain entities."""

import pytest

from src.domain.entities.integrity import IntegrityCheckResult, IntegrityReport


class TestIntegrityCheckResult:
    def test_frozen_immutable(self):
        result = IntegrityCheckResult(name="test", status="pass", count=0)
        with pytest.raises(AttributeError):
            result.count = 5  # type: ignore[misc]

    def test_defaults(self):
        result = IntegrityCheckResult(name="test", status="pass", count=0)
        assert result.details == []

    def test_with_details(self):
        details = [{"track_id": 1, "connector_name": "spotify"}]
        result = IntegrityCheckResult(
            name="violations", status="fail", count=1, details=details
        )
        assert result.details == details
        assert result.count == 1


class TestIntegrityReport:
    def test_total_issues_sums_counts(self):
        checks = [
            IntegrityCheckResult(name="a", status="fail", count=3),
            IntegrityCheckResult(name="b", status="warn", count=2),
            IntegrityCheckResult(name="c", status="pass", count=0),
        ]
        report = IntegrityReport(checks=checks, overall_status="fail")
        assert report.total_issues == 5

    def test_total_issues_zero_when_all_pass(self):
        checks = [
            IntegrityCheckResult(name="a", status="pass", count=0),
            IntegrityCheckResult(name="b", status="pass", count=0),
        ]
        report = IntegrityReport(checks=checks, overall_status="pass")
        assert report.total_issues == 0

    def test_frozen_immutable(self):
        report = IntegrityReport(checks=[], overall_status="pass")
        with pytest.raises(AttributeError):
            report.overall_status = "fail"  # type: ignore[misc]
