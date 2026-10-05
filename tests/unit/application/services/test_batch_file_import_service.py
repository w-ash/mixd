"""Unit tests for BatchFileImportService.

Tests cover:
- File discovery with glob patterns
- Batch import orchestration
- File archiving after successful import
- Error handling and aggregation
- Edge cases (no files, partial failures)
"""

import pytest

from src.application.services.batch_file_import_service import BatchFileImportService
from src.domain.entities import OperationResult
from src.domain.entities.progress import NullProgressEmitter


@pytest.fixture
def mock_executor():
    """Mock import executor that returns success results."""

    def executor(spec, **kwargs):
        return OperationResult(operation_name=f"Import {spec.file_path}")

    return executor


@pytest.fixture
def failing_executor():
    """Mock import executor that always raises exceptions."""

    def executor(spec, **kwargs):
        raise ValueError("Import failed")

    return executor


@pytest.fixture
def service(mock_executor):
    """BatchFileImportService with mock executor."""
    return BatchFileImportService(import_executor=mock_executor)


class TestFileDiscovery:
    """Test file discovery with glob patterns."""

    def test_discover_files_returns_only_matching_files_sorted(self, service, tmp_path):
        """Discover files keeps only pattern matches, in name order."""
        (tmp_path / "Streaming_History_Audio_3.json").write_text("{}")
        (tmp_path / "Streaming_History_Audio_1.json").write_text("{}")
        (tmp_path / "other_file.json").write_text("{}")
        (tmp_path / "Streaming_History_Audio_2.json").write_text("{}")

        files = service.discover_files(tmp_path, "Streaming_History_Audio_*.json")

        assert [f.name for f in files] == [
            "Streaming_History_Audio_1.json",
            "Streaming_History_Audio_2.json",
            "Streaming_History_Audio_3.json",
        ]

    def test_discover_files_creates_directory_if_missing(self, service, tmp_path):
        """Discover files creates imports directory if it doesn't exist."""
        missing_dir = tmp_path / "missing"
        assert not missing_dir.exists()

        service.discover_files(missing_dir, "*.json")

        assert missing_dir.exists()
        assert missing_dir.is_dir()


class TestBatchImportSuccess:
    """Test successful batch import scenarios."""

    def test_import_multiple_files_success(self, service, tmp_path):
        """Import multiple files successfully archives all."""
        # Setup
        imports_dir = tmp_path / "imports"
        imported_dir = tmp_path / "imports" / "imported"
        imports_dir.mkdir()

        # Create 3 test files
        for i in range(1, 4):
            (imports_dir / f"Streaming_History_Audio_{i}.json").write_text("{}")

        # Execute
        result = service.import_files_batch(
            service="spotify",
            imports_dir=imports_dir,
            imported_dir=imported_dir,
            pattern="Streaming_History_Audio_*.json",
            batch_size=100,
            progress_emitter=NullProgressEmitter(),
        )

        names = [f"Streaming_History_Audio_{i}.json" for i in range(1, 4)]
        assert result.total_files == 3
        assert result.successful == 3
        assert result.failed == 0
        assert result.failed_files == []
        assert result.archived_files == [imported_dir / name for name in names]
        assert all(not (imports_dir / name).exists() for name in names)
        assert all((imported_dir / name).exists() for name in names)


class TestBatchImportErrors:
    """Test error handling in batch import."""

    def test_import_continues_on_single_failure(self, tmp_path):
        """Import continues processing after single file failure."""
        # Setup executor that fails on second file
        call_count = 0

        def conditional_executor(spec, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise ValueError("Second file failed")
            return OperationResult(operation_name="Import")

        service = BatchFileImportService(import_executor=conditional_executor)

        # Setup files
        imports_dir = tmp_path / "imports"
        imported_dir = tmp_path / "imports" / "imported"
        imports_dir.mkdir()

        for i in range(1, 4):
            (imports_dir / f"file_{i}.json").write_text("{}")

        # Execute
        result = service.import_files_batch(
            service="spotify",
            imports_dir=imports_dir,
            imported_dir=imported_dir,
            pattern="file_*.json",
            batch_size=None,
            progress_emitter=NullProgressEmitter(),
        )

        # Verify
        assert result.total_files == 3
        assert result.successful == 2  # files 1 and 3
        assert result.failed == 1  # file 2
        assert result.failed_files == ["file_2.json"]
        assert result.archived_files == [
            imported_dir / "file_1.json",
            imported_dir / "file_3.json",
        ]

        # Verify failed file not moved
        assert (imports_dir / "file_2.json").exists()
        assert not (imported_dir / "file_2.json").exists()


class TestBatchImportEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_import_no_files_found_returns_empty_result(self, service, tmp_path):
        """Import with no matching files returns empty result."""
        imports_dir = tmp_path / "imports"
        imported_dir = tmp_path / "imports" / "imported"
        imports_dir.mkdir()

        result = service.import_files_batch(
            service="spotify",
            imports_dir=imports_dir,
            imported_dir=imported_dir,
            pattern="nonexistent_*.json",
            batch_size=None,
            progress_emitter=NullProgressEmitter(),
        )

        # Verify
        assert result.total_files == 0
        assert result.successful == 0
        assert result.failed == 0
        assert result.failed_files == []
        assert result.archived_files == []

    def test_import_executor_receives_correct_parameters(self, tmp_path):
        """Import executor receives all expected parameters."""
        captured_params = {}

        def capturing_executor(spec, **kwargs):
            captured_params.update({
                "service": spec.service,
                "mode": spec.mode,
                "file_path": spec.file_path,
                "batch_size": spec.batch_size,
                **kwargs,
            })
            return OperationResult(operation_name="Import")

        service = BatchFileImportService(import_executor=capturing_executor)

        # Setup
        imports_dir = tmp_path / "imports"
        imported_dir = tmp_path / "imports" / "imported"
        imports_dir.mkdir()

        test_file = imports_dir / "test.json"
        test_file.write_text("{}")

        emitter = NullProgressEmitter()

        service.import_files_batch(
            service="spotify",
            imports_dir=imports_dir,
            imported_dir=imported_dir,
            pattern="test.json",
            batch_size=500,
            progress_emitter=emitter,
        )

        # Verify executor received correct params
        assert captured_params["service"] == "spotify"
        assert captured_params["mode"] == "file"
        assert captured_params["file_path"] == test_file
        assert captured_params["batch_size"] == 500
        assert captured_params["progress_emitter"] is emitter
