"""
Models for operation status tracking and management.

This module defines the data models used for tracking the status and progress
of long-running operations across all API endpoints.
"""

from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field, model_validator
from datetime import datetime, timezone
from enum import Enum
import uuid


class OperationStatus(str, Enum):
    """
    Enumeration of possible operation statuses.
    """
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class OperationType(str, Enum):
    """
    Enumeration of operation types corresponding to API endpoints.
    """
    CREATE_APPLICATION = "create_application"
    INDEX_DOCUMENTS = "index_documents"
    RUN_ANALYSIS = "run_analysis"
    GENERATE_REPORT = "generate_report"
    GENERATE_DESIGN = "generate_design"
    GENERATE_PLANNING = "generate_planning"
    KUBERNETES_DISCOVERY = "kubernetes_discovery"
    DELETE_APP_DATA = "delete_app_data"
    ARCHITECTURE_ANALYSIS = "architecture_analysis"
    CODE_ANALYSIS = "code_analysis"


class OperationStep(BaseModel):
    """
    Individual step within an operation.
    """
    step_name: str = Field(..., description="Name of the step")
    status: OperationStatus = Field(..., description="Status of this step")
    started_at: Optional[datetime] = Field(None, description="When step started")
    completed_at: Optional[datetime] = Field(None, description="When step completed")
    details: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Step-specific details")
    error_message: Optional[str] = Field(None, description="Error message if step failed")


class OperationRecord(BaseModel):
    """
    Complete record of an operation with all tracking information.
    """
    operation_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique operation identifier")
    app_id: str = Field(..., description="Application ID")
    operation_type: OperationType = Field(..., description="Type of operation")
    status: OperationStatus = Field(default=OperationStatus.PENDING, description="Current operation status")
    user_object_id: Optional[str] = Field(None, description="User who initiated the operation (optional if group_object_id provided)")
    group_object_id: Optional[str] = Field(None, description="Group that initiated the operation (optional if user_object_id provided)")
    storage_account_name: str = Field(..., description="Storage account name")
    resource_group_name: Optional[str] = Field(None, description="Resource group name")
    design_doc_url: Optional[str] = Field(None, description="Design document URL for architecture analysis operations")
    repo_url: Optional[str] = Field(None, description="Repository URL for code analysis (GitHub, GitLab, etc.)")
    
    # Code analysis specific fields
    blob_url: Optional[str] = Field(None, description="Blob URL where analysis report is stored")
    
    # Timing information
    timestamp_started: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="When operation started")
    timestamp_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Last update timestamp")
    timestamp_completed: Optional[datetime] = Field(None, description="When operation completed")
    
    # Progress tracking
    progress_percentage: int = Field(default=0, description="Progress percentage (0-100)")
    current_step: str = Field(default="Initializing", description="Current step description")
    total_steps: int = Field(default=1, description="Total number of steps")
    completed_steps: int = Field(default=0, description="Number of completed steps")
    steps: List[OperationStep] = Field(default_factory=list, description="Detailed step information")
    
    # Results and metadata
    result_data: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Operation result data")
    result: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Operation result data (current field)")
    blob_url: Optional[str] = Field(None, description="Direct URL to the result blob (report file)")
    generated_report_url: Optional[str] = Field(None, description="Generated report URL (alias for blob_url)")
    error_details: Optional[Dict[str, Any]] = Field(None, description="Error details if operation failed")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional operation metadata")
    
    # Performance metrics
    duration_seconds: Optional[float] = Field(None, description="Total operation duration in seconds")
    
    @model_validator(mode='after')
    def validate_at_least_one_id(self):
        """Ensure at least one of user_object_id or group_object_id is provided."""
        if not self.user_object_id and not self.group_object_id:
            raise ValueError("At least one of user_object_id or group_object_id must be provided")
        return self
    
    def update_progress(self, step_name: str, progress: int, status: OperationStatus = None):
        """Update operation progress and current step."""
        self.current_step = step_name
        self.progress_percentage = min(100, max(0, progress))
        self.timestamp_updated = datetime.now(timezone.utc)
        
        if status:
            self.status = status
            
        # Update steps list
        existing_step = next((s for s in self.steps if s.step_name == step_name), None)
        if existing_step:
            old_status = existing_step.status
            existing_step.status = status or existing_step.status
            if status == OperationStatus.COMPLETED:
                existing_step.completed_at = datetime.now(timezone.utc)
                # Increment completed_steps counter if this step wasn't already completed
                if old_status != OperationStatus.COMPLETED:
                    self.completed_steps += 1
        else:
            new_step = OperationStep(
                step_name=step_name,
                status=status or OperationStatus.IN_PROGRESS,
                started_at=datetime.now(timezone.utc)
            )
            if status == OperationStatus.COMPLETED:
                new_step.completed_at = datetime.now(timezone.utc)
                self.completed_steps += 1
            self.steps.append(new_step)
    
    def complete_operation(self, result_data: Dict[str, Any] = None):
        """Mark operation as completed."""
        self.status = OperationStatus.COMPLETED
        self.progress_percentage = 100
        self.completed_steps = self.total_steps  # Set completed steps to total steps
        self.timestamp_completed = datetime.now(timezone.utc)
        self.timestamp_updated = datetime.now(timezone.utc)
        self.current_step = "Operation completed"
        
        # Mark all steps as completed
        if self.steps:
            completion_time = datetime.now(timezone.utc)
            for step in self.steps:
                if step.status != OperationStatus.COMPLETED:
                    step.status = OperationStatus.COMPLETED
                    step.completed_at = completion_time
        
        if result_data:
            # Store in both fields for compatibility
            self.result_data = result_data
            self.result = result_data
            
            # Sync blob_url and generated_report_url
            if "generated_report_url" in result_data and not self.blob_url:
                self.blob_url = result_data["generated_report_url"]
                self.generated_report_url = result_data["generated_report_url"]
            elif self.blob_url and not self.generated_report_url:
                self.generated_report_url = self.blob_url
            
        # Calculate duration
        if self.timestamp_started:
            self.duration_seconds = (self.timestamp_completed - self.timestamp_started).total_seconds()
    
    def fail_operation(self, error_message: str, error_details: Dict[str, Any] = None):
        """Mark operation as failed."""
        self.status = OperationStatus.FAILED
        self.timestamp_updated = datetime.now(timezone.utc)
        self.current_step = f"Failed: {error_message}"
        
        if error_details:
            self.error_details = error_details
        else:
            self.error_details = {"error_message": error_message}
    
    @property
    def report_url(self) -> Optional[str]:
        """Get the report URL (prioritizes blob_url, falls back to generated_report_url)."""
        return self.blob_url or self.generated_report_url
    
    def set_blob_url(self, url: str):
        """Set both blob_url and generated_report_url to keep them in sync."""
        self.blob_url = url
        self.generated_report_url = url
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }


class OperationStatusRequest(BaseModel):
    """
    Request model for querying operation status.
    """
    operation_id: Optional[str] = Field(None, description="Specific operation ID to query")
    app_id: str = Field(..., description="Filter by application ID")
    repo_url: Optional[str] = Field(None, description="Filter by repository URL (code analysis)")
    user_object_id: Optional[str] = Field(None, description="Filter by user object ID")
    group_object_id: Optional[str] = Field(None, description="Filter by group object ID")
    operation_type: Optional[OperationType] = Field(None, description="Filter by operation type")
    status: Optional[OperationStatus] = Field(None, description="Filter by status")
    limit: int = Field(default=10, ge=1, le=100, description="Maximum number of results")
    offset: int = Field(default=0, ge=0, description="Results offset for pagination")


class OperationStatusResponse(BaseModel):
    """
    Response model for operation status queries.
    """
    total_count: int = Field(..., description="Total number of matching operations")
    operations: List[OperationRecord] = Field(..., description="List of operation records")
    has_more: bool = Field(..., description="Whether more results are available")
    
    class Config:
        json_schema_extra = {
            "example": {
                "total_count": 25,
                "operations": [
                    {
                        "operation_id": "123e4567-e89b-12d3-a456-426614174000",
                        "app_id": "myapp-001",
                        "operation_type": "run_analysis",
                        "status": "in_progress",
                        "user_object_id": "12345678-1234-1234-1234-123456789012",
                        "progress_percentage": 65,
                        "current_step": "Processing QA tables",
                        "timestamp_started": "2025-10-21T10:30:00Z",
                        "timestamp_updated": "2025-10-21T10:35:00Z"
                    }
                ],
                "has_more": True
            }
        }


class OperationSummary(BaseModel):
    """
    Summary statistics for operations.
    """
    total_operations: int = Field(..., description="Total number of operations")
    operations_by_status: Dict[str, int] = Field(..., description="Count by status")
    operations_by_type: Dict[str, int] = Field(..., description="Count by operation type")
    avg_duration_seconds: Optional[float] = Field(None, description="Average completion duration")
    success_rate: float = Field(..., description="Success rate percentage")


class OperationSummaryResponse(BaseModel):
    """
    Response model for operation summary endpoint.
    """
    summary: OperationSummary = Field(..., description="Operation summary statistics")
    recent_operations: List[OperationRecord] = Field(..., description="Most recent operations")
    
    class Config:
        json_schema_extra = {
            "example": {
                "summary": {
                    "total_operations": 150,
                    "operations_by_status": {
                        "completed": 130,
                        "in_progress": 5,
                        "failed": 15
                    },
                    "operations_by_type": {
                        "create_application": 30,
                        "index_documents": 30,
                        "run_analysis": 30,
                        "generate_report": 30,
                        "assessment_complete": 30
                    },
                    "avg_duration_seconds": 45.6,
                    "success_rate": 86.7
                },
                "recent_operations": []
            }
        }