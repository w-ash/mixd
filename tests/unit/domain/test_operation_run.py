"""Unit tests for OperationRun retry-eligibility domain logic.

`is_retryable` / `failed_connector_identifiers` are the single source of truth
for both the retry route's 409 gate and the `retryable` flag the web UI reads,
so they're pinned here at the domain layer.
"""

from datetime import UTC, datetime

from src.domain.entities.operation_run import OperationRun, OperationStatus
from src.domain.entities.shared import JsonDict


def _run(
    *,
    status: OperationStatus = "error",
    operation_type: str = "import_connector_playlists",
    issues: list[JsonDict] | None = None,
    request_params: JsonDict | None = None,
) -> OperationRun:
    return OperationRun(
        user_id="u",
        operation_type=operation_type,
        started_at=datetime.now(UTC),
        status=status,
        issues=[{"connector_playlist_identifier": "pl-1"}]
        if issues is None
        else issues,
        request_params={"connector_name": "spotify", "sync_direction": "pull"}
        if request_params is None
        else request_params,
    )


def test_failed_connector_identifiers_reads_ids_and_skips_issues_without_one() -> None:
    run = _run(
        issues=[
            {"connector_playlist_identifier": "pl-1"},
            {"connector_playlist_identifier": "pl-2"},
            {"error": "no identifier here"},
        ]
    )
    assert run.failed_connector_identifiers == ["pl-1", "pl-2"]


def test_is_retryable_true_for_failed_import_with_config_and_failures() -> None:
    assert _run().is_retryable is True


def test_is_retryable_false_for_completed_run() -> None:
    assert _run(status="complete").is_retryable is False


def test_is_retryable_false_for_running_run() -> None:
    assert _run(status="running").is_retryable is False


def test_is_retryable_false_for_non_retryable_operation_type() -> None:
    assert _run(operation_type="import_lastfm_history").is_retryable is False


def test_is_retryable_false_when_no_failed_items() -> None:
    assert _run(issues=[]).is_retryable is False


def test_is_retryable_false_when_connector_name_missing() -> None:
    assert _run(request_params={"sync_direction": "pull"}).is_retryable is False


def test_is_retryable_false_when_sync_direction_missing() -> None:
    assert _run(request_params={"connector_name": "spotify"}).is_retryable is False
