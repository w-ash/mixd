"""Unit tests for BatchFileImportService.

Tests cover:
- File discovery with glob patterns
- Batch import orchestration
- File archiving after successful import
- Error handling and aggregation
- Edge cases (no files, partial failures)
"""

from pathlib import Path

import pytest

from src.application.services.batch_file_import_service import (
    BatchFileImportService,
    BatchImportResult,
)
from src.domain.entities import OperationResult
from src.domain.entities.progress import NullProgressEmitter


@pytest.fixture
def mock_executor():
    """Mock import executor that returns success results."""

    def executor(service: str, mode: str, **kwargs):
        return OperationResult(
            operation_name=f"Import {kwargs.get('file_path', 'test')}"
        )

    return executor


@pytest.fixture
def failing_executor():
    """Mock import executor that always raises exceptions."""

    def executor(service: str, mode: str, **kwargs):
        raise ValueError("Import failed")

    return executor


@pytest.fixture
def service(mock_executor):
    """BatchFileImportService with mock executor."""
    return BatchFileImportService(import_executor=mock_executor)


class TestFileDiscovery:
    """Test file discovery with glob patterns."""

    def test_discover_files_finds_matching_files(self, service, tmp_path):
        """Discover files finds files matching glob pattern."""
        # Create test files
        (tmp_path / "Streaming_History_Audio_1.json").write_text("{}")
        (tmp_path / "Streaming_History_Audio_2.json").write_text("{}")
        (tmp_path / "other_file.json").write_text("{}")

        # Discover
        files = service.discover_files(tmp_path, "Streaming_History_Audio_*.json")

        # Verify
        assert len(files) == 2
        assert all("Streaming_History_Audio" in f.name for f in files)

    def test_discover_files_returns_sorted_list(self, service, tmp_path):
        """Discover files returns files in sorted order."""
        # Create files in non-alphabetical order
        (tmp_path / "file_3.json").write_text("{}")
        (tmp_path / "file_1.json").write_text("{}")
        (tmp_path / "file_2.json").write_text("{}")

        # Discover
        files = service.discover_files(tmp_path, "file_*.json")

        # Verify sorted
        assert [f.name for f in files] == ["file_1.json", "file_2.json", "file_3.json"]

    def test_discover_files_returns_empty_when_no_matches(self, service, tmp_path):
        """Discover files returns empty list when no matches."""
        files = service.discover_files(tmp_path, "nonexistent_*.json")
        assert files == []

    def test_discover_files_creates_directory_if_missing(self, service, tmp_path):
        """Discover files creates imports directory if it doesn't exist."""
        missing_dir = tmp_path / "missing"
        assert not missing_dir.exists()

        service.discover_files(missing_dir, "*.json")

        assert missing_dir.exists()
        assert missing_dir.is_dir()


class TestBatchImportSuccess:
    """Test successful batch import scenarios."""

    def test_import_single_file_success(self, service, tmp_path):
        """Import single file successfully archives it."""
        # Setup
        imports_dir = tmp_path / "imports"
        imported_dir = tmp_path / "imports" / "imported"
        imports_dir.mkdir()

        test_file = imports_dir / "Streaming_History_Audio_1.json"
        test_file.write_text("{}")

        # Execute
        result = service.import_files_batch(
            service="spotify",
            imports_dir=imports_dir,
            imported_dir=imported_dir,
            pattern="Streaming_History_Audio_*.json",
            batch_size=None,
            progress_emitter=NullProgressEmitter(),
        )

        # Verify
        assert result.total_files == 1
        assert result.successful == 1
        assert result.failed == 0
        assert len(result.archived_files) == 1

        # Verify file was moved
        assert not test_file.exists()
        assert (imported_dir / test_file.name).exists()

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

        # Verify
        assert result.total_files == 3
        assert result.successful == 3
        assert result.failed == 0
        assert len(result.archived_files) == 3

        # Verify all files moved
        assert len(list(imports_dir.glob("Streaming_History_Audio_*.json"))) == 0
        assert len(list(imported_dir.glob("Streaming_History_Audio_*.json"))) == 3

    def test_import_creates_archive_directory(self, service, tmp_path):
        """Import creates archive directory if it doesn't exist."""
        # Setup
        imports_dir = tmp_path / "imports"
        imported_dir = tmp_path / "imports" / "imported"
        imports_dir.mkdir()
        (imports_dir / "test.json").write_text("{}")

        assert not imported_dir.exists()

        # Execute
        service.import_files_batch(
            service="spotify",
            imports_dir=imports_dir,
            imported_dir=imported_dir,
            pattern="test.json",
            batch_size=None,
            progress_emitter=NullProgressEmitter(),
        )

        # Verify
        assert imported_dir.exists()
        assert imported_dir.is_dir()


class TestBatchImportErrors:
    """Test error handling in batch import."""

    def test_import_continues_on_single_failure(self, tmp_path):
        """Import continues processing after single file failure."""
        # Setup executor that fails on second file
        call_count = 0

        def conditional_executor(service: str, mode: str, **kwargs):
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
        assert len(result.failed_files) == 1
        assert "file_2.json" in result.failed_files

        # Verify failed file not moved
        assert (imports_dir / "file_2.json").exists()
        assert not (imported_dir / "file_2.json").exists()

    def test_import_aggregates_all_failures(self, tmp_path):
        """Import tracks all failed files."""
        # Setup
        service = BatchFileImportService(
            import_executor=lambda **kwargs: (_ for _ in ()).throw(ValueError("Failed"))
        )

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
        assert result.successful == 0
        assert result.failed == 3
        assert len(result.failed_files) == 3

    def test_import_preserves_failed_files(self, failing_executor, tmp_path):
        """Failed import does not move or delete files."""
        # Setup
        service = BatchFileImportService(import_executor=failing_executor)

        imports_dir = tmp_path / "imports"
        imported_dir = tmp_path / "imports" / "imported"
        imports_dir.mkdir()

        test_file = imports_dir / "test.json"
        test_file.write_text("{}")

        # Execute
        service.import_files_batch(
            service="spotify",
            imports_dir=imports_dir,
            imported_dir=imported_dir,
            pattern="test.json",
            batch_size=None,
            progress_emitter=NullProgressEmitter(),
        )

        # Verify file still in original location
        assert test_file.exists()
        assert not (imported_dir / "test.json").exists()


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

        def capturing_executor(service: str, mode: str, **kwargs):
            captured_params.update({"service": service, "mode": mode, **kwargs})
            return OperationResult(operation_name="Import")

        service = BatchFileImportService(import_executor=capturing_executor)

        # Setup
        imports_dir = tmp_path / "imports"
        imported_dir = tmp_path / "imports" / "imported"
        imports_dir.mkdir()

        test_file = imports_dir / "test.json"
        test_file.write_text("{}")

        # Execute
        service.import_files_batch(
            service="spotify",
            imports_dir=imports_dir,
            imported_dir=imported_dir,
            pattern="test.json",
            batch_size=500,
            progress_emitter=NullProgressEmitter(),
        )

        # Verify executor received correct params
        assert captured_params["service"] == "spotify"
        assert captured_params["mode"] == "file"
        assert captured_params["file_path"] == test_file
        assert captured_params["batch_size"] == 500
        assert "progress_emitter" in captured_params


class TestBatchImportResult:
    """Test BatchImportResult data class."""

    def test_batch_import_result_creation(self):
        """BatchImportResult can be created with all fields."""
        result = BatchImportResult(
            total_files=5,
            successful=3,
            failed=2,
            failed_files=["file1.json", "file2.json"],
            archived_files=[Path("/archived/file3.json")],
        )

        assert result.total_files == 5
        assert result.successful == 3
        assert result.failed == 2
        assert len(result.failed_files) == 2
        assert len(result.archived_files) == 1

    def test_batch_import_result_immutable(self):
        """BatchImportResult is immutable (frozen)."""
        result = BatchImportResult(total_files=1, successful=1, failed=0)

        with pytest.raises(AttributeError):
            result.successful = 2  # type: ignore
