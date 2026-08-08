"""Unit tests for the shared result → audit-row outcome mapping.

These pin the mapping itself, once, at the place both consumers read it: the SSE
seam (a user-initiated run) and the sync-target runner (a scheduled or demand
one). They used to live only in the seam's tests, which is exactly how the
mapping came to be applied to one of the two paths — a scheduled import that
resolved 480 of 500 tracks recorded as a bare ``error`` with no counts while the
identical manual run recorded ``partial`` with the failed tracks named.

The consumer tests assert only that each seam *threads* this through; the truth
table lives here.
"""

from src.application.services.operation_outcome import audit_outcome, failure_issues
from src.domain.entities.operations import OperationResult


def _result(**metrics: int) -> OperationResult:
    result = OperationResult(operation_name="Import")
    for name, value in metrics.items():
        result.summary_metrics.add(
            name, value, name.replace("_", " ").title(), significance=1
        )
    return result


def _partial_result(
    failures: list[dict[str, str]], truncated: int | None = None
) -> OperationResult:
    """A run that landed 98 plays and recorded per-item resolution failures."""
    result = _result(track_plays=98, errors=len(failures))
    result.metadata["error"] = "2 of 100 plays failed import"
    result.metadata["resolution_failures"] = failures
    if truncated is not None:
        result.metadata["resolution_failures_truncated"] = truncated
    return result


class TestAuditOutcomeStatus:
    def test_clean_result_is_complete_with_counts(self) -> None:
        assert audit_outcome(_result(track_plays=42)) == (
            "complete",
            {"track_plays": 42},
        )

    def test_soft_failure_is_error_with_counts(self) -> None:
        # The use case handled the error and returned rather than raising; the
        # row must not record that as a clean run.
        result = _result(errors=1)
        result.metadata["error"] = "Last.fm timed out"
        status, counts = audit_outcome(result)
        assert status == "error"
        assert counts == {"errors": 1}

    def test_failure_that_landed_work_is_partial(self) -> None:
        # "Finished, but some tracks couldn't be resolved" — a different thing
        # for the user to act on than a run that failed outright.
        result = _result(track_plays=98, errors=2)
        result.metadata["error"] = "2 of 100 plays failed import"
        assert audit_outcome(result) == ("partial", {"track_plays": 98, "errors": 2})

    def test_errors_only_result_stays_error(self) -> None:
        # No success metric → nothing landed → a total failure. This is the shape
        # that must NOT drift into 'partial'.
        assert audit_outcome(_result(errors=1))[0] == "error"

    def test_denominator_metrics_do_not_make_a_failure_partial(self) -> None:
        # raw_plays/candidates count what was *attempted*, so they are positive
        # even when nothing succeeded — they are outside the success allowlist.
        result = _result(raw_plays=100, candidates=100, errors=100)
        assert audit_outcome(result)[0] == "error"

    def test_non_operation_result_is_complete_without_counts(self) -> None:
        # Some callers (a fire-and-forget coro, a sync target whose run hook
        # returns nothing) carry no failure signal and no counts.
        assert audit_outcome(None) == ("complete", None)
        assert audit_outcome("done") == ("complete", None)


class TestFailureIssues:
    """The reason a run failed has to survive into the row.

    ``to_counts()`` flattens summary metrics only, so a soft failure's message —
    stashed in ``OperationResult.metadata`` — otherwise exists nowhere queryable
    (the "errors: 1 with no message" gap).
    """

    def test_metadata_error_becomes_the_headline_issue(self) -> None:
        result = _result(errors=1)
        result.metadata["error"] = "Spotify file import failed: 429"
        assert failure_issues(result) == [
            {"message": "Spotify file import failed: 429"}
        ]

    def test_errors_list_is_joined_into_one_headline(self) -> None:
        # create_error_result stashes the message under metadata["errors"].
        result = _result(errors=1)
        result.metadata["errors"] = ["boom one", "boom two"]
        assert failure_issues(result) == [{"message": "boom one; boom two"}]

    def test_failure_without_a_message_yields_no_issue(self) -> None:
        # errors metric with nothing in metadata: nothing useful to persist.
        assert failure_issues(_result(errors=1)) == []

    def test_long_message_is_capped(self) -> None:
        result = _result(errors=1)
        result.metadata["error"] = "x" * 900
        assert failure_issues(result) == [{"message": "x" * 500}]

    def test_each_recorded_failure_becomes_its_own_issue(self) -> None:
        # This is what makes the Import History expandable row answer *which*
        # tracks couldn't be resolved, rather than only "2 of 100 failed".
        result = _partial_result([
            {
                "track": "Aphex Twin - Xtal",
                "spotify_id": "abc",
                "reason": "track_resolution_failed",
            },
            {
                "track": "Boards of Canada - Roygbiv",
                "spotify_id": "def",
                "reason": "track_resolution_failed",
            },
        ])

        issues = failure_issues(result)

        # Headline first, then one issue per failure, passed through as-is.
        assert issues[0] == {"message": "2 of 100 plays failed import"}
        assert issues[1]["track"] == "Aphex Twin - Xtal"
        assert issues[2]["spotify_id"] == "def"
        assert len(issues) == 3

    def test_truncation_notice_appended_last(self) -> None:
        result = _partial_result(
            [
                {
                    "track": "A - B",
                    "spotify_id": "x",
                    "reason": "track_resolution_failed",
                }
            ],
            truncated=12,
        )
        assert failure_issues(result)[-1] == {
            "message": "…and 12 more failures — see server logs"
        }

    def test_no_truncation_notice_when_under_the_cap(self) -> None:
        result = _partial_result([
            {"track": "A - B", "spotify_id": "x", "reason": "track_resolution_failed"}
        ])
        issues = failure_issues(result)
        assert not any("more failures" in str(i.get("message", "")) for i in issues)
