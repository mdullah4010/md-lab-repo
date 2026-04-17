#!/usr/bin/env python3
"""
Unit Tests - Operation Tracking Data Models

Test Type: Model/Data Structure Testing
Test Classification: Unit Test (Isolated Model Testing)

What These Tests Do:
- Test Pydantic model instantiation (OperationRecord)
- Validate model methods (update_progress, complete_operation)
- Test JSON serialization of operation models
- Verify enum values and status transitions
- No external dependencies (pure Python logic)

Why This IS a Unit Test:
✅ Tests isolated functionality (data models only)
✅ No external service dependencies
✅ Fast and deterministic
✅ Tests single responsibility (model behavior)
✅ Uses pytest framework with assertions
✅ Integrated with test runner
✅ Proper test discovery mechanism
✅ Clear pass/fail status

Usage: pytest tests/unit/test_operation_tracking.py -v
Run with markers: pytest tests/unit/test_operation_tracking.py -v -m unit
"""

import pytest
import json
import time
from datetime import datetime
from uuid import UUID

from agents.operation_models import (
    OperationRecord,
    OperationStatus,
    OperationType,
    OperationStatusRequest,
    OperationStatusResponse
)


# Test Fixtures
@pytest.fixture
def sample_operation_data():
    """Fixture providing sample operation data for tests"""
    return {
        "app_id": "21121",
        "operation_type": OperationType.CREATE_APPLICATION,
        "user_object_id": "12345678-1234-1234-1234-123456789012",
        "storage_account_name": "testaccount",
        "resource_group_name": "test-rg",
        "total_steps": 4
    }


@pytest.fixture
def sample_result_data():
    """Fixture providing sample result data for completed operations"""
    return {
        "status": "success",
        "app_id": "21121",
        "container": {"status": "created"},
        "permissions": {"status": "assigned"}
    }


# Unit Tests
@pytest.mark.unit
class TestOperationRecord:
    """Unit tests for OperationRecord model"""

    def test_create_operation_initializes_correctly(self, sample_operation_data):
        """Test that operation creation initializes all fields correctly"""
        operation = OperationRecord(**sample_operation_data)
        
        # Verify core fields
        assert operation.app_id == "21121"
        assert operation.operation_type == OperationType.CREATE_APPLICATION
        assert operation.user_object_id == "12345678-1234-1234-1234-123456789012"
        assert operation.storage_account_name == "testaccount"
        assert operation.resource_group_name == "test-rg"
        assert operation.total_steps == 4
        
        # Verify initial state
        assert operation.status == OperationStatus.PENDING
        assert operation.progress_percentage == 0
        assert operation.completed_steps == 0
        assert operation.current_step == "Initializing"  # Default value in model
        
        # Verify auto-generated fields
        assert isinstance(operation.operation_id, str)
        assert UUID(operation.operation_id)  # Validates it's a valid UUID
        assert operation.timestamp_started is not None
        assert operation.timestamp_completed is None
        assert operation.duration_seconds is None  # Duration is None until operation completes

    def test_update_progress_updates_fields_correctly(self, sample_operation_data):
        """Test that update_progress method updates fields correctly"""
        operation = OperationRecord(**sample_operation_data)
        
        operation.update_progress("Starting RBAC validation", 25, OperationStatus.IN_PROGRESS)
        
        assert operation.current_step == "Starting RBAC validation"
        assert operation.progress_percentage == 25
        assert operation.status == OperationStatus.IN_PROGRESS
        assert len(operation.steps) == 1
        # steps is a list of OperationStep objects, not dicts
        assert operation.steps[0].step_name == "Starting RBAC validation"
        assert operation.steps[0].status == OperationStatus.IN_PROGRESS

    def test_update_progress_increments_sequentially(self, sample_operation_data):
        """Test that multiple progress updates work sequentially"""
        operation = OperationRecord(**sample_operation_data)
        
        # First update
        operation.update_progress("Step 1", 25, OperationStatus.IN_PROGRESS)
        assert operation.progress_percentage == 25
        assert len(operation.steps) == 1
        
        # Second update
        operation.update_progress("Step 2", 50)
        assert operation.progress_percentage == 50
        assert operation.current_step == "Step 2"
        assert len(operation.steps) == 2
        
        # Third update
        operation.update_progress("Step 3", 75)
        assert operation.progress_percentage == 75
        assert operation.current_step == "Step 3"
        assert len(operation.steps) == 3

    def test_update_progress_defaults_to_current_status(self, sample_operation_data):
        """Test that update_progress uses current status when not specified"""
        operation = OperationRecord(**sample_operation_data)
        
        # Set status to IN_PROGRESS
        operation.update_progress("Step 1", 25, OperationStatus.IN_PROGRESS)
        assert operation.status == OperationStatus.IN_PROGRESS
        
        # Update progress without status parameter - should maintain IN_PROGRESS
        operation.update_progress("Step 2", 50)
        assert operation.status == OperationStatus.IN_PROGRESS

    def test_complete_operation_sets_final_state(self, sample_operation_data, sample_result_data):
        """Test that complete_operation sets operation to completed state"""
        operation = OperationRecord(**sample_operation_data)
        
        # Progress through some steps first
        operation.update_progress("Step 1", 33, OperationStatus.IN_PROGRESS)
        operation.update_progress("Step 2", 66)
        
        # Add small delay to ensure measurable duration
        time.sleep(0.01)  # 10ms delay
        
        # Complete the operation
        operation.complete_operation(sample_result_data)
        
        assert operation.status == OperationStatus.COMPLETED
        assert operation.progress_percentage == 100
        assert operation.completed_steps == operation.total_steps
        assert operation.timestamp_completed is not None
        assert operation.duration_seconds > 0
        assert operation.result_data == sample_result_data

    def test_duration_calculation(self, sample_operation_data, sample_result_data):
        """Test that duration is calculated correctly when operation completes"""
        operation = OperationRecord(**sample_operation_data)
        
        initial_duration = operation.duration_seconds
        assert initial_duration is None  # Duration is None until operation completes
        
        # Add small delay to ensure measurable duration
        time.sleep(0.01)  # 10ms delay
        
        operation.complete_operation(sample_result_data)
        
        # Duration should be positive after completion
        assert operation.duration_seconds > 0
        assert isinstance(operation.duration_seconds, float)

    def test_json_serialization(self, sample_operation_data):
        """Test that operation can be serialized to JSON"""
        operation = OperationRecord(**sample_operation_data)
        operation.update_progress("Test step", 50, OperationStatus.IN_PROGRESS)
        
        # Convert to dict (use model_dump for Pydantic v2)
        operation_dict = operation.model_dump()
        
        # Should be able to serialize to JSON
        json_output = json.dumps(operation_dict, default=str)
        
        assert json_output is not None
        assert len(json_output) > 0
        
        # Verify key fields are in JSON
        assert "operation_id" in json_output
        assert "app_id" in json_output
        assert "21121" in json_output
        assert "Test step" in json_output  # Check exact case


@pytest.mark.unit
class TestOperationEnums:
    """Unit tests for operation enums"""

    def test_operation_status_values(self):
        """Test that OperationStatus enum has expected values"""
        assert OperationStatus.PENDING.value == "pending"
        assert OperationStatus.IN_PROGRESS.value == "in_progress"
        assert OperationStatus.COMPLETED.value == "completed"
        assert OperationStatus.FAILED.value == "failed"
        assert OperationStatus.CANCELLED.value == "cancelled"

    def test_operation_type_values(self):
        """Test that OperationType enum has expected values"""
        assert OperationType.CREATE_APPLICATION.value == "create_application"
        assert OperationType.INDEX_DOCUMENTS.value == "index_documents"
        assert OperationType.RUN_ANALYSIS.value == "run_analysis"
        assert OperationType.GENERATE_REPORT.value == "generate_report"
        assert OperationType.GENERATE_DESIGN.value == "generate_design"
        assert OperationType.KUBERNETES_DISCOVERY.value == "kubernetes_discovery"
        assert OperationType.DELETE_APP_DATA.value == "delete_app_data"
        assert OperationType.ARCHITECTURE_ANALYSIS.value == "architecture_analysis"
        assert OperationType.CODE_ANALYSIS.value == "code_analysis"


@pytest.mark.unit
class TestOperationStatusRequest:
    """Unit tests for OperationStatusRequest model"""

    def test_create_status_request_with_app_id(self):
        """Test creating status request with app_id"""
        request = OperationStatusRequest(app_id="21121")
        
        assert request.app_id == "21121"
        assert request.status is None
        assert request.operation_type is None
        assert request.limit == 10  # Default value in model

    def test_create_status_request_with_filters(self):
        """Test creating status request with all filters"""
        request = OperationStatusRequest(
            app_id="21121",
            status=OperationStatus.COMPLETED,
            operation_type=OperationType.CREATE_APPLICATION,
            limit=50
        )
        
        assert request.app_id == "21121"
        assert request.status == OperationStatus.COMPLETED
        assert request.operation_type == OperationType.CREATE_APPLICATION
        assert request.limit == 50


@pytest.mark.unit
class TestOperationStatusResponse:
    """Unit tests for OperationStatusResponse model"""

    def test_create_empty_response(self):
        """Test creating empty operation status response"""
        response = OperationStatusResponse(
            total_count=0,
            operations=[],
            has_more=False  # Required field, not optional
        )
        
        assert response.total_count == 0
        assert response.operations == []
        assert response.has_more is False

    def test_create_response_with_operations(self, sample_operation_data):
        """Test creating response with operations"""
        operation = OperationRecord(**sample_operation_data)
        
        response = OperationStatusResponse(
            total_count=1,
            operations=[operation],
            has_more=False
        )
        
        assert response.total_count == 1
        assert len(response.operations) == 1
        assert response.operations[0].app_id == "21121"
        assert response.has_more is False