"""Tests for data integrity monitoring domain entities."""

from src.domain.entities.integrity import IntegrityCheckResult, IntegrityReport


class TestIntegrityReport:
    def test_total_issues_sums_counts(self):
        checks = [
            IntegrityCheckResult(name="a", status="fail", count=3),
            IntegrityCheckResult(name="b", status="warn", count=2),
            IntegrityCheckResult(name="c", status="pass", count=0),
        ]
        report = IntegrityReport(checks=checks, overall_status="fail")
        assert report.total_issues == 5
