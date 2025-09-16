"""Tests for domain progress entities and business rules.

Validates progress event and operation entities, domain service business logic,
and proper enforcement of progress tracking invariants.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from src.domain.entities.progress import (
    OperationStatus,
    ProgressEvent,
    ProgressOperation,
    ProgressStatus,
    create_progress_event,
    create_progress_operation,
)
from src.domain.services.progress_coordinator import ProgressCoordinator


class TestProgressStatus:
    """Test ProgressStatus enum."""
    
    def test_status_values(self):
        """Test that status enum has expected values."""
        assert ProgressStatus.STARTED.value == "started"
        assert ProgressStatus.IN_PROGRESS.value == "in_progress"
        assert ProgressStatus.COMPLETED.value == "completed"
        assert ProgressStatus.FAILED.value == "failed"
        assert ProgressStatus.CANCELLED.value == "cancelled"


class TestOperationStatus:
    """Test OperationStatus enum."""
    
    def test_status_values(self):
        """Test that operation status enum has expected values."""
        assert OperationStatus.PENDING.value == "pending"
        assert OperationStatus.RUNNING.value == "running"
        assert OperationStatus.COMPLETED.value == "completed"
        assert OperationStatus.FAILED.value == "failed"
        assert OperationStatus.CANCELLED.value == "cancelled"


class TestProgressEvent:
    """Test ProgressEvent domain entity."""
    
    def test_create_valid_progress_event(self):
        """Test creating a valid progress event."""
        event = ProgressEvent(
            operation_id="test-op-123",
            current=50,
            total=100,
            message="Processing items",
            status=ProgressStatus.IN_PROGRESS
        )
        
        assert event.operation_id == "test-op-123"
        assert event.current == 50
        assert event.total == 100
        assert event.message == "Processing items"
        assert event.status == ProgressStatus.IN_PROGRESS
        assert isinstance(event.timestamp, datetime)
        assert event.metadata == {}
    
    def test_progress_event_immutable(self):
        """Test that progress events are immutable."""
        event = ProgressEvent(
            operation_id="test-op-123",
            current=50,
            total=100,
            message="Processing"
        )
        
        with pytest.raises(Exception):  # attrs frozen=True prevents modification
            event.current = 75  # type: ignore
    
    def test_completion_percentage_calculation(self):
        """Test completion percentage calculation."""
        event = ProgressEvent(
            operation_id="test-op-123",
            current=25,
            total=100,
            message="Processing"
        )
        
        assert event.completion_percentage == 25.0
        
        # Test with indeterminate progress
        indeterminate_event = ProgressEvent(
            operation_id="test-op-123",
            current=50,
            total=None,
            message="Processing"
        )
        
        assert indeterminate_event.completion_percentage is None
    
    def test_is_complete_property(self):
        """Test is_complete property for different statuses."""
        in_progress = ProgressEvent(
            operation_id="test-op-123",
            current=50,
            total=100,
            message="Processing",
            status=ProgressStatus.IN_PROGRESS
        )
        assert not in_progress.is_complete
        
        completed = ProgressEvent(
            operation_id="test-op-123",
            current=100,
            total=100,
            message="Done",
            status=ProgressStatus.COMPLETED
        )
        assert completed.is_complete
        
        failed = ProgressEvent(
            operation_id="test-op-123",
            current=50,
            total=100,
            message="Error",
            status=ProgressStatus.FAILED
        )
        assert failed.is_complete
    
    def test_is_determinate_property(self):
        """Test is_determinate property."""
        determinate = ProgressEvent(
            operation_id="test-op-123",
            current=50,
            total=100,
            message="Processing"
        )
        assert determinate.is_determinate
        
        indeterminate = ProgressEvent(
            operation_id="test-op-123",
            current=50,
            total=None,
            message="Processing"
        )
        assert not indeterminate.is_determinate
    
    def test_negative_current_validation(self):
        """Test validation rejects negative current progress."""
        with pytest.raises(ValueError, match="Progress current \\(-5\\) must be non-negative"):
            ProgressEvent(
                operation_id="test-op-123",
                current=-5,
                total=100,
                message="Processing"
            )
    
    def test_zero_total_validation(self):
        """Test validation rejects zero or negative total."""
        with pytest.raises(ValueError, match="Progress total \\(0\\) must be positive when specified"):
            ProgressEvent(
                operation_id="test-op-123",
                current=0,
                total=0,
                message="Processing"
            )
        
        with pytest.raises(ValueError, match="Progress total \\(-10\\) must be positive when specified"):
            ProgressEvent(
                operation_id="test-op-123",
                current=0,
                total=-10,
                message="Processing"
            )
    
    def test_current_exceeds_total_validation(self):
        """Test validation rejects current > total."""
        with pytest.raises(ValueError, match="Progress current \\(150\\) cannot exceed total \\(100\\)"):
            ProgressEvent(
                operation_id="test-op-123",
                current=150,
                total=100,
                message="Processing"
            )
    
    def test_empty_operation_id_validation(self):
        """Test validation rejects empty operation ID."""
        with pytest.raises(ValueError, match="Progress operation_id cannot be empty"):
            ProgressEvent(
                operation_id="",
                current=50,
                total=100,
                message="Processing"
            )
        
        with pytest.raises(ValueError, match="Progress operation_id cannot be empty"):
            ProgressEvent(
                operation_id="   ",  # whitespace only
                current=50,
                total=100,
                message="Processing"
            )
    
    def test_empty_message_validation(self):
        """Test validation rejects empty message."""
        with pytest.raises(ValueError, match="Progress message cannot be empty"):
            ProgressEvent(
                operation_id="test-op-123",
                current=50,
                total=100,
                message=""
            )


class TestProgressOperation:
    """Test ProgressOperation domain entity."""
    
    def test_create_valid_operation(self):
        """Test creating a valid progress operation."""
        operation = ProgressOperation(
            operation_id="test-op-456",
            description="Import tracks",
            total_items=1000,
            status=OperationStatus.PENDING
        )
        
        assert operation.operation_id == "test-op-456"
        assert operation.description == "Import tracks"
        assert operation.total_items == 1000
        assert operation.status == OperationStatus.PENDING
        assert isinstance(operation.start_time, datetime)
        assert operation.end_time is None
        assert operation.metadata == {}
    
    def test_operation_with_defaults(self):
        """Test operation creation with default values."""
        operation = ProgressOperation(description="Test operation")
        
        assert operation.description == "Test operation"
        assert operation.operation_id is not None  # UUID generated
        assert len(operation.operation_id) > 0
        assert operation.total_items is None
        assert operation.status == OperationStatus.PENDING
    
    def test_duration_calculation(self):
        """Test duration calculation for completed operations."""
        start_time = datetime.now(UTC)
        end_time = start_time + timedelta(seconds=30)
        
        operation = ProgressOperation(
            description="Test operation",
            start_time=start_time,
            end_time=end_time
        )
        
        assert operation.duration_seconds == 30.0
        
        # Test ongoing operation
        ongoing = ProgressOperation(
            description="Ongoing operation",
            start_time=start_time
        )
        
        assert ongoing.duration_seconds is None
    
    def test_status_properties(self):
        """Test status convenience properties."""
        running_op = ProgressOperation(
            description="Running",
            status=OperationStatus.RUNNING
        )
        assert running_op.is_running
        assert not running_op.is_complete
        
        completed_op = ProgressOperation(
            description="Completed",
            status=OperationStatus.COMPLETED
        )
        assert not completed_op.is_running
        assert completed_op.is_complete
        
        failed_op = ProgressOperation(
            description="Failed",
            status=OperationStatus.FAILED
        )
        assert not failed_op.is_running
        assert failed_op.is_complete
    
    def test_with_status_immutable_update(self):
        """Test immutable status updates."""
        original = ProgressOperation(
            description="Test operation",
            status=OperationStatus.PENDING
        )
        
        end_time = datetime.now(UTC)
        updated = original.with_status(OperationStatus.COMPLETED, end_time)
        
        # Original unchanged
        assert original.status == OperationStatus.PENDING
        assert original.end_time is None
        
        # New instance updated
        assert updated.status == OperationStatus.COMPLETED
        assert updated.end_time == end_time
        assert updated.operation_id == original.operation_id  # Same ID
    
    def test_with_metadata_immutable_update(self):
        """Test immutable metadata updates."""
        original = ProgressOperation(
            description="Test operation",
            metadata={"batch_size": 100}
        )
        
        updated = original.with_metadata(error_count=5, success_rate=0.95)
        
        # Original unchanged
        assert original.metadata == {"batch_size": 100}
        
        # New instance has combined metadata
        assert updated.metadata == {
            "batch_size": 100,
            "error_count": 5,
            "success_rate": 0.95
        }
    
    def test_empty_description_validation(self):
        """Test validation rejects empty description."""
        with pytest.raises(ValueError, match="Operation description cannot be empty"):
            ProgressOperation(description="")
    
    def test_invalid_total_items_validation(self):
        """Test validation rejects invalid total_items."""
        with pytest.raises(ValueError, match="Operation total_items \\(0\\) must be positive when specified"):
            ProgressOperation(
                description="Test operation",
                total_items=0
            )
        
        with pytest.raises(ValueError, match="Operation total_items \\(-10\\) must be positive when specified"):
            ProgressOperation(
                description="Test operation",
                total_items=-10
            )
    
    def test_invalid_end_time_validation(self):
        """Test validation rejects end_time before start_time."""
        start_time = datetime.now(UTC)
        end_time = start_time - timedelta(seconds=10)  # 10 seconds before start
        
        with pytest.raises(ValueError, match="Operation end_time cannot be before start_time"):
            ProgressOperation(
                description="Test operation",
                start_time=start_time,
                end_time=end_time
            )


class TestFactoryFunctions:
    """Test factory functions for creating domain entities."""
    
    def test_create_progress_event_factory(self):
        """Test create_progress_event factory function."""
        event = create_progress_event(
            operation_id="test-op-789",
            current=75,
            total=100,
            message="Almost done",
            status=ProgressStatus.IN_PROGRESS,
            batch_id="batch-123",
            error_count=2
        )
        
        assert event.operation_id == "test-op-789"
        assert event.current == 75
        assert event.total == 100
        assert event.message == "Almost done"
        assert event.status == ProgressStatus.IN_PROGRESS
        assert event.metadata["batch_id"] == "batch-123"
        assert event.metadata["error_count"] == 2
    
    def test_create_progress_operation_factory(self):
        """Test create_progress_operation factory function."""
        operation = create_progress_operation(
            description="Import playlist",
            total_items=500,
            source="spotify",
            playlist_id="playlist-456"
        )
        
        assert operation.description == "Import playlist"
        assert operation.total_items == 500
        assert operation.metadata["source"] == "spotify"
        assert operation.metadata["playlist_id"] == "playlist-456"
        assert len(operation.operation_id) > 0  # UUID generated


class TestProgressCoordinator:
    """Test ProgressCoordinator domain service."""
    
    @pytest.fixture
    def coordinator(self):
        """Create a progress coordinator for testing."""
        return ProgressCoordinator()
    
    @pytest.fixture
    def sample_operation(self):
        """Create a sample operation for testing."""
        return create_progress_operation(
            description="Test import",
            total_items=100
        )
    
    @pytest.mark.asyncio
    async def test_start_operation(self, coordinator, sample_operation):
        """Test starting operation tracking."""
        running_operation = await coordinator.start_operation(sample_operation)
        
        assert running_operation.status == OperationStatus.RUNNING
        assert running_operation.operation_id == sample_operation.operation_id
        
        # Verify operation is tracked
        retrieved = await coordinator.get_operation(sample_operation.operation_id)
        assert retrieved is not None
        assert retrieved.status == OperationStatus.RUNNING
    
    @pytest.mark.asyncio
    async def test_start_duplicate_operation_fails(self, coordinator, sample_operation):
        """Test that starting duplicate operations fails."""
        await coordinator.start_operation(sample_operation)
        
        with pytest.raises(ValueError, match="Operation .* is already being tracked"):
            await coordinator.start_operation(sample_operation)
    
    @pytest.mark.asyncio
    async def test_validate_progress_event_success(self, coordinator, sample_operation):
        """Test successful progress event validation."""
        await coordinator.start_operation(sample_operation)
        
        event = create_progress_event(
            operation_id=sample_operation.operation_id,
            current=25,
            total=100,
            message="Processing"
        )
        
        is_valid, error = await coordinator.validate_progress_event(event)
        assert is_valid
        assert error is None
    
    @pytest.mark.asyncio
    async def test_validate_nonexistent_operation(self, coordinator):
        """Test validation fails for nonexistent operation."""
        event = create_progress_event(
            operation_id="nonexistent-op",
            current=25,
            total=100,
            message="Processing"
        )
        
        is_valid, error = await coordinator.validate_progress_event(event)
        assert not is_valid
        assert "No active operation found" in error
    
    @pytest.mark.asyncio
    async def test_validate_backwards_progress_fails(self, coordinator, sample_operation):
        """Test that backwards progress is rejected."""
        await coordinator.start_operation(sample_operation)
        
        # Record initial progress
        initial_event = create_progress_event(
            operation_id=sample_operation.operation_id,
            current=50,
            total=100,
            message="Half done"
        )
        await coordinator.record_progress_event(initial_event)
        
        # Try to go backwards
        backwards_event = create_progress_event(
            operation_id=sample_operation.operation_id,
            current=25,  # Less than previous 50
            total=100,
            message="Going backwards"
        )
        
        is_valid, error = await coordinator.validate_progress_event(backwards_event)
        assert not is_valid
        assert "Progress went backwards" in error
    
    @pytest.mark.asyncio
    async def test_record_progress_event_with_metrics(self, coordinator, sample_operation):
        """Test recording progress event calculates derived metrics."""
        await coordinator.start_operation(sample_operation)
        
        event = create_progress_event(
            operation_id=sample_operation.operation_id,
            current=50,
            total=100,
            message="Processing"
        )
        
        enhanced_event = await coordinator.record_progress_event(event)
        
        # Verify derived metrics are added
        assert "event_sequence" in enhanced_event.metadata
        assert "completion_percentage" in enhanced_event.metadata
        assert enhanced_event.metadata["completion_percentage"] == 50.0
        assert enhanced_event.metadata["event_sequence"] == 1
    
    @pytest.mark.asyncio
    async def test_complete_operation(self, coordinator, sample_operation):
        """Test completing an operation."""
        await coordinator.start_operation(sample_operation)
        
        completed_operation = await coordinator.complete_operation(
            sample_operation.operation_id,
            OperationStatus.COMPLETED
        )
        
        assert completed_operation.status == OperationStatus.COMPLETED
        assert completed_operation.end_time is not None
        assert completed_operation.duration_seconds is not None
        assert completed_operation.duration_seconds >= 0
    
    @pytest.mark.asyncio
    async def test_complete_nonexistent_operation_fails(self, coordinator):
        """Test completing nonexistent operation fails."""
        with pytest.raises(ValueError, match="No operation found"):
            await coordinator.complete_operation("nonexistent", OperationStatus.COMPLETED)
    
    @pytest.mark.asyncio
    async def test_get_active_operations(self, coordinator):
        """Test retrieving active operations."""
        op1 = create_progress_operation(description="Operation 1")
        op2 = create_progress_operation(description="Operation 2")
        
        await coordinator.start_operation(op1)
        await coordinator.start_operation(op2)
        
        active_ops = await coordinator.get_active_operations()
        assert len(active_ops) == 2
        
        operation_ids = [op.operation_id for op in active_ops]
        assert op1.operation_id in operation_ids
        assert op2.operation_id in operation_ids
        
        # Complete one operation
        await coordinator.complete_operation(op1.operation_id, OperationStatus.COMPLETED)
        
        active_ops = await coordinator.get_active_operations()
        assert len(active_ops) == 1
        assert active_ops[0].operation_id == op2.operation_id
    
    @pytest.mark.asyncio
    async def test_cleanup_completed_operations(self, coordinator, sample_operation):
        """Test cleanup of old completed operations."""
        await coordinator.start_operation(sample_operation)
        
        # Complete the operation (it will have a recent end_time)
        await coordinator.complete_operation(sample_operation.operation_id, OperationStatus.COMPLETED)
        
        # Cleanup with very short max_age should remove it
        cleanup_count = await coordinator.cleanup_completed_operations(max_age_seconds=0.001)
        
        # Give it a moment for the operation to be considered "old"
        await asyncio.sleep(0.01)
        
        cleanup_count = await coordinator.cleanup_completed_operations(max_age_seconds=0.001)
        assert cleanup_count == 1
        
        # Operation should no longer be found
        retrieved = await coordinator.get_operation(sample_operation.operation_id)
        assert retrieved is None

