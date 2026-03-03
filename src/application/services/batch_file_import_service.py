"""Batch file import orchestration service for processing multiple import files.

Handles the orchestration of importing multiple files sequentially, with file
archiving and error aggregation. Follows hexagonal architecture by keeping file
operations in the application layer and delegating actual import to use cases.
"""

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: use case results, OperationResult metadata, metric values

from pathlib import Path
from typing import Any, Literal, Protocol

from attrs import define, field

from src.config import get_logger
from src.domain.entities import OperationResult
from src.domain.entities.progress import ProgressEmitter

logger = get_logger(__name__)


class ImportExecutorProtocol(Protocol):
    """Protocol for executing individual file imports.

    This abstraction allows the service to work with any import executor,
    whether it's the actual use case or a test stub.
    """

    def __call__(
        self,
        service: Literal["lastfm", "spotify"],
        mode: Literal["recent", "incremental", "full", "file"],
        **kwargs: Any,
    ) -> OperationResult:
        """Execute a single file import.

        Args:
            service: Service name (e.g., "spotify")
            mode: Import mode (e.g., "file")
            **kwargs: Additional import parameters (file_path, batch_size, etc.)

        Returns:
            Operation result from import
        """
        ...


@define(frozen=True, slots=True)
class BatchImportResult:
    """Result from batch file import operation.

    Attributes:
        total_files: Total number of files found
        successful: Number of successfully imported files
        failed: Number of failed imports
        failed_files: List of file names that failed
        archived_files: List of successfully archived file paths
    """

    total_files: int
    successful: int
    failed: int
    failed_files: list[str] = field(factory=list)
    archived_files: list[Path] = field(factory=list)


class BatchFileImportService:
    """Orchestrates batch import of multiple files with archiving.

    This service handles the application-level concerns of:
    - File discovery and validation
    - Sequential import execution
    - File archiving after successful import
    - Error handling and aggregation
    - Progress coordination

    It delegates the actual import work to the import use case via the
    executor protocol, maintaining clean separation of concerns.
    """

    _import_executor: ImportExecutorProtocol

    def __init__(self, import_executor: ImportExecutorProtocol) -> None:
        """Initialize service with import executor.

        Args:
            import_executor: Callable that executes individual file imports
        """
        self._import_executor = import_executor

    def discover_files(self, imports_dir: Path, pattern: str) -> list[Path]:
        """Discover files matching pattern in imports directory.

        Args:
            imports_dir: Directory to search for files
            pattern: Glob pattern (e.g., "Streaming_History_Audio_*.json")

        Returns:
            Sorted list of matching file paths
        """
        imports_dir.mkdir(parents=True, exist_ok=True)
        return sorted(imports_dir.glob(pattern))

    def import_files_batch(
        self,
        service: Literal["lastfm", "spotify"],
        imports_dir: Path,
        imported_dir: Path,
        pattern: str,
        batch_size: int | None,
        progress_emitter: ProgressEmitter,
    ) -> BatchImportResult:
        """Import all files matching pattern and archive on success.

        Args:
            service: Service name ("lastfm" or "spotify")
            imports_dir: Directory containing files to import
            imported_dir: Directory to move successfully imported files
            pattern: Glob pattern for file discovery
            batch_size: Optional batch size for processing
            progress_emitter: Progress emitter for tracking

        Returns:
            Aggregated batch import result
        """
        # Ensure archive directory exists
        imported_dir.mkdir(parents=True, exist_ok=True)

        # Discover files
        pending_files = self.discover_files(imports_dir, pattern)

        if not pending_files:
            logger.info(f"No files found matching pattern: {pattern}")
            return BatchImportResult(
                total_files=0,
                successful=0,
                failed=0,
                failed_files=[],
                archived_files=[],
            )

        logger.info(f"Found {len(pending_files)} files to import")

        # Process each file
        successful = 0
        failed = 0
        failed_files: list[str] = []
        archived_files: list[Path] = []

        for file_path in pending_files:
            try:
                logger.info(f"Importing file: {file_path.name}")

                # Execute import
                _ = self._import_executor(
                    service=service,
                    mode="file",
                    file_path=file_path,
                    batch_size=batch_size,
                    progress_emitter=progress_emitter,
                )

                # Archive file after successful import
                destination = imported_dir / file_path.name
                _ = file_path.rename(destination)
                archived_files.append(destination)

                logger.info(f"Successfully imported and archived: {file_path.name}")
                successful += 1

            except Exception as e:
                logger.error(f"Failed to import {file_path.name}: {e}")
                failed_files.append(file_path.name)
                failed += 1
                # Continue with next file instead of failing entire batch

        return BatchImportResult(
            total_files=len(pending_files),
            successful=successful,
            failed=failed,
            failed_files=failed_files,
            archived_files=archived_files,
        )
