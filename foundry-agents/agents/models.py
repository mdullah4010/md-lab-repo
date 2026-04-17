"""
Pydantic models for API request/response validation.

This module defines the data models used for API endpoints with strong typing
and validation following Azure best practices.
"""

from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field, field_validator, model_validator
import re

# Import operation tracking models
from operation_models import (
    OperationRecord,
    OperationStatus,
    OperationType,
    OperationStep,
    OperationStatusRequest,
    OperationStatusResponse,
    OperationSummary,
    OperationSummaryResponse
)


class CreateApplicationRequest(BaseModel):
    """
    Request model for creating a new application ID with RBAC setup.
    
    Attributes:
        app_id: Unique application identifier (will be used as container name)
        storage_account_name: Name of the Azure Storage account
        azure_region: Azure region where the storage account is located
        user_object_id: Azure AD user object ID for permission assignment
        resource_group_name: Optional resource group name (auto-discovered if not provided)
    """
    
    app_id: str = Field(
        ...,
        description="Application ID (used as container name)",
        min_length=3,
        max_length=63
    )
    
    storage_account_name: str = Field(
        ...,
        description="Azure Storage account name",
        min_length=3,
        max_length=24
    )
    
    azure_region: str = Field(
        ...,
        description="Azure region (e.g., eastus, westus2)",
        min_length=1
    )
    
    user_object_id: Optional[str] = Field(
        None,
        description="Azure AD user object ID (GUID format) - required if group_object_id not provided",
        pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    )

    group_object_id: Optional[str] = Field(
        None,
        description="Azure AD group object ID (GUID format) - required if user_object_id not provided",
        pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    )

    resource_group_name: Optional[str] = Field(
        None,
        description="Resource group name (optional, auto-discovered if not provided)"
    )
    
    @field_validator('app_id')
    @classmethod
    def validate_app_id(cls, v: str) -> str:
        """
        Validate that app_id follows Azure container naming rules.
        Container names must:
        - Be 3-63 characters long
        - Start with a letter or number
        - Contain only lowercase letters, numbers, and hyphens
        - Not contain consecutive hyphens
        - Not end with a hyphen
        """
        v = v.lower()
        
        if not re.match(r'^[a-z0-9]', v):
            raise ValueError("app_id must start with a letter or number")
        
        if not re.match(r'^[a-z0-9][a-z0-9-]*[a-z0-9]$', v) and len(v) > 1:
            raise ValueError("app_id must contain only lowercase letters, numbers, and hyphens, and cannot end with a hyphen")
        
        if '--' in v:
            raise ValueError("app_id cannot contain consecutive hyphens")
        
        return v
    
    @field_validator('storage_account_name')
    @classmethod
    def validate_storage_account_name(cls, v: str) -> str:
        """
        Validate that storage_account_name follows Azure naming rules.
        Storage account names must:
        - Be 3-24 characters long
        - Contain only lowercase letters and numbers
        """
        v = v.lower()
        
        if not re.match(r'^[a-z0-9]{3,24}$', v):
            raise ValueError("storage_account_name must contain only lowercase letters and numbers (3-24 characters)")
        
        return v
    
    @model_validator(mode='after')
    def validate_at_least_one_id(self):
        """Ensure at least one of user_object_id or group_object_id is provided."""
        if not self.user_object_id and not self.group_object_id:
            raise ValueError("At least one of user_object_id or group_object_id must be provided")
        return self
    
    class Config:
        json_schema_extra = {
            "example": {
                "app_id": "myapp-001",
                "storage_account_name": "mystorageaccount",
                "azure_region": "eastus",
                "user_object_id": "12345678-1234-1234-1234-123456789012",
                "group_object_id": "87654321-4321-4321-4321-210987654321",
                "resource_group_name": "my-resource-group"
            }
        }


class CreateApplicationResponse(BaseModel):
    """
    Response model for application creation endpoint.
    
    Attributes:
        status: Overall status of the operation
        app_id: Application ID that was created/configured
        container: Container creation details
        permissions: Permission assignment details
        tables: Template table cloning results
        message: Human-readable status message
    """
    
    status: str = Field(
        ...,
        description="Operation status (success, partial_success, error)"
    )
    
    app_id: str = Field(
        ...,
        description="Application ID"
    )
    
    container: dict = Field(
        ...,
        description="Container creation details"
    )
    
    permissions: dict = Field(
        ...,
        description="Permission assignment details"
    )
    
    tables: dict = Field(
        default_factory=dict,
        description="Template table cloning results from orchestrator"
    )
    
    message: str = Field(
        ...,
        description="Human-readable status message"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "success",
                "app_id": "myapp-001",
                "container": {
                    "status": "created",
                    "container_name": "myapp-001",
                    "storage_account": "mystorageaccount",
                    "exists": True
                },
                "permissions": {
                    "blob_permissions": {
                        "status": "assigned",
                        "role": "Storage Blob Data Contributor"
                    },
                    "table_permissions": {
                        "status": "assigned",
                        "role": "Storage Table Data Contributor"
                    }
                },
                "tables": {
                    "status": "completed",
                    "cloned_tables": ["AppDetailsTemplate", "MsSqlDBTemplate", "OracleDBTemplate"],
                    "message": "Template tables cloned successfully"
                },
                "message": "Application created successfully with proper RBAC permissions"
            }
        }


class ApplicationOperationRequest(BaseModel):
    """
    Request model for operations on an existing application.
    
    Used by the following endpoints:
    - /runAnalysis - Run ASR analysis
    - /generateAssessmentReport - Generate assessment report
    - /generateDesign - Generate architecture design
    - /discoverKubernetes - Discover Kubernetes resources
    - /deleteAppData - Delete all application data
    
    Note: /indexDocuments uses IndexDocumentsRequest (extends this model with folder_prefix)
    
    Attributes:
        app_id: Application ID to operate on
        storage_account_name: Name of the Azure Storage account
        user_object_id: Azure AD user object ID for authentication (required if group_object_id not provided)
        group_object_id: Azure AD group object ID for authentication (required if user_object_id not provided)
        resource_group_name: Optional resource group name (auto-discovered if not provided)
    """
    
    app_id: str = Field(
        ...,
        description="Application ID",
        min_length=3,
        max_length=63
    )
    
    storage_account_name: str = Field(
        ...,
        description="Azure Storage account name",
        min_length=3,
        max_length=24
    )
    
    user_object_id: Optional[str] = Field(
        None,
        description="Azure AD user object ID (GUID format) - required if group_object_id not provided",
        pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    )
    
    group_object_id: Optional[str] = Field(
        None,
        description="Azure AD group object ID (GUID format) - required if user_object_id not provided",
        pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    )
    
    resource_group_name: Optional[str] = Field(
        default=None,
        description="Resource group name (optional, auto-discovered if not provided)"
    )
    
    @field_validator('app_id')
    @classmethod
    def validate_app_id(cls, v: str) -> str:
        """Validate app_id follows Azure container naming rules."""
        v = v.lower()
        
        if not re.match(r'^[a-z0-9]', v):
            raise ValueError("app_id must start with a letter or number")
        
        if not re.match(r'^[a-z0-9][a-z0-9-]*[a-z0-9]$', v) and len(v) > 1:
            raise ValueError("app_id must contain only lowercase letters, numbers, and hyphens")
        
        if '--' in v:
            raise ValueError("app_id cannot contain consecutive hyphens")
        
        return v
    
    @field_validator('storage_account_name')
    @classmethod
    def validate_storage_account_name(cls, v: str) -> str:
        """Validate storage_account_name follows Azure naming rules."""
        v = v.lower()
        
        if not re.match(r'^[a-z0-9]{3,24}$', v):
            raise ValueError("storage_account_name must contain only lowercase letters and numbers")
        
        return v
    
    @model_validator(mode='after')
    def validate_at_least_one_id(self):
        """Ensure at least one of user_object_id or group_object_id is provided."""
        if not self.user_object_id and not self.group_object_id:
            raise ValueError("At least one of user_object_id or group_object_id must be provided")
        return self
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "app_id": "myapp-001",
                "storage_account_name": "stassess5avb",
                "user_object_id": "12345678-1234-1234-1234-123456789012",
                "group_object_id": "87654321-4321-4321-4321-210987654321",
                "resource_group_name": "rg-migrateassesment"
            }
        }
    }


class IndexDocumentsRequest(ApplicationOperationRequest):
    """
    Request model specifically for the indexDocuments endpoint.
    Extends ApplicationOperationRequest with folder_prefix field.
    
    Attributes:
        folder_prefix: Optional folder path to limit indexing scope
    """
    
    folder_prefix: Optional[str] = Field(
        default=None,
        description="(Optional) Folder prefix to limit indexing to a specific folder path within the container (e.g., 'asr/input/', 'uploads/2026/'). If not provided, indexes entire container.",
        examples=["responder/input/", "kubernetes-discovery/input/", "code-analyzer/input/", None]
    )
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "app_id": "myapp-001",
                "storage_account_name": "stassess5avb",
                "user_object_id": "12345678-1234-1234-1234-123456789012",
                "group_object_id": "87654321-4321-4321-4321-210987654321",
                "resource_group_name": "rg-migrateassesment",
                "folder_prefix": "responder/input/"
            }
        }
    }


class IndexDocumentsResponse(BaseModel):
    """
    Response model for document indexing endpoint.
    
    Attributes:
        status: Indexing operation status
        app_id: Application ID
        indexing_result: Details of the indexing operation
        message: Human-readable status message
    """
    
    status: str = Field(
        ...,
        description="Operation status (success, in_progress, failed)"
    )
    
    app_id: str = Field(
        ...,
        description="Application ID"
    )
    
    indexing_result: dict = Field(
        default_factory=dict,
        description="Indexing operation details"
    )
    
    message: str = Field(
        ...,
        description="Human-readable status message"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "success",
                "app_id": "myapp-001",
                "indexing_result": {
                    "documents_indexed": 150,
                    "duration_seconds": 45
                },
                "message": "Documents indexed successfully"
            }
        }


class AnalysisResponse(BaseModel):
    """
    Response model for analysis execution endpoint (async operation).
    
    This endpoint returns immediately with an operation_id for tracking.
    Use the status_endpoint to check progress and result_endpoint to get results.
    
    Attributes:
        status: Analysis operation status (accepted, in_progress, completed, failed)
        app_id: Application ID
        operation_id: Unique operation identifier for tracking
        analysis_result: Details of the analysis operation (populated when completed)
        message: Human-readable status message
        table_confidence_scores: Optional dictionary of confidence scores per table
        overall_average_confidence_score: Optional overall average confidence score
        status_endpoint: Endpoint to check operation status
        result_endpoint: Endpoint to retrieve results when completed
    """
    
    status: str = Field(
        ...,
        description="Operation status (accepted, in_progress, completed, failed)"
    )
    
    app_id: str = Field(
        ...,
        description="Application ID"
    )
    
    operation_id: Optional[str] = Field(
        default=None,
        description="Unique operation identifier for tracking"
    )
    
    analysis_result: Optional[dict] = Field(
        default_factory=dict,
        description="Analysis operation details (populated when completed)"
    )
    
    message: str = Field(
        ...,
        description="Human-readable status message"
    )
    
    table_confidence_scores: Optional[dict] = Field(
        None,
        description="Aggregate confidence scores per table (0.0-1.0)"
    )
    
    overall_average_confidence_score: Optional[float] = Field(
        None,
        description="Overall average confidence score across all tables (0.0-1.0)"
    )
    
    status_endpoint: Optional[str] = Field(
        default=None,
        description="Endpoint to check operation status"
    )
    
    result_endpoint: Optional[str] = Field(
        default=None,
        description="Endpoint to retrieve results when completed"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "accepted",
                "app_id": "myapp-001",
                "operation_id": "550e8400-e29b-41d4-a716-446655440000",
                "message": "Analysis started. Use operation_id to check status and retrieve results.",
                "status_endpoint": "/operations/550e8400-e29b-41d4-a716-446655440000/status?app_id=myapp-001",
                "result_endpoint": "/operations/550e8400-e29b-41d4-a716-446655440000/result?app_id=myapp-001"
            }
        }


class AssessmentReportResponse(BaseModel):
    """
    Response model for assessment report generation endpoint (async operation).
    
    This endpoint returns immediately with an operation_id for tracking.
    Use the status_endpoint to check progress and result_endpoint to get results.
    
    Attributes:
        status: Report generation status (accepted, in_progress, completed, failed)
        app_id: Application ID
        report: Report details and location (populated when completed)
        message: Human-readable status message
        operation_id: Unique operation identifier for tracking
        status_endpoint: Endpoint to check operation status
        result_endpoint: Endpoint to retrieve results when completed
    """
    
    status: str = Field(
        ...,
        description="Operation status (accepted, in_progress, completed, failed)"
    )
    
    app_id: str = Field(
        ...,
        description="Application ID"
    )
    
    operation_id: Optional[str] = Field(
        default=None,
        description="Unique operation identifier for tracking"
    )
    
    report: Optional[dict] = Field(
        default_factory=dict,
        description="Report generation details (populated when completed)"
    )
    
    message: str = Field(
        ...,
        description="Human-readable status message"
    )
    
    status_endpoint: Optional[str] = Field(
        default=None,
        description="Endpoint to check operation status"
    )
    
    result_endpoint: Optional[str] = Field(
        default=None,
        description="Endpoint to retrieve results when completed"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "accepted",
                "app_id": "myapp-001",
                "operation_id": "550e8400-e29b-41d4-a716-446655440000",
                "message": "Assessment report generation started. Use operation_id to check status and retrieve results.",
                "status_endpoint": "/operations/550e8400-e29b-41d4-a716-446655440000/status?app_id=myapp-001",
                "result_endpoint": "/operations/550e8400-e29b-41d4-a716-446655440000/result?app_id=myapp-001"
            }
        }


class DeleteAppDataResponse(BaseModel):
    """
    Response model for delete app data endpoint.
    
    Deletes all agents (orchestrator, ASR, design, responder, architecture, security, diagram),
    threads, storage container, and search index associated with an application.
    
    Attributes:
        status: Deletion status
        app_id: Application ID
        deletion_result: Details of the deletion operation including what was deleted
        message: Human-readable status message
    """
    
    status: str = Field(
        ...,
        description="Operation status (success, partial_success, failed)"
    )
    
    app_id: str = Field(
        ...,
        description="Application ID"
    )
    
    deletion_result: dict = Field(
        default_factory=dict,
        description="Deletion operation details including agents, threads, storage, and index status"
    )
    
    message: str = Field(
        ...,
        description="Human-readable status message"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "success",
                "app_id": "myapp-001",
                "cleanup_result": {
                    "agents_cleaned": 3,
                    "resources_released": True
                },
                "message": "Assessment completed and resources cleaned up"
            }
        }


class DesignResponse(BaseModel):
    """
    Response model for design generation endpoint (async operation).
    
    This endpoint returns immediately with an operation_id for tracking.
    Use the status_endpoint to check progress and result_endpoint to get results.
    
    Attributes:
        status: Design generation status (accepted, in_progress, completed, failed)
        app_id: Application ID
        operation_id: Unique operation identifier for tracking
        design_result: Details of the design generation operation (populated when completed)
        message: Human-readable status message
        status_endpoint: Endpoint to check operation status
        result_endpoint: Endpoint to retrieve results when completed
    """
    
    status: str = Field(
        ...,
        description="Operation status (accepted, in_progress, completed, failed)"
    )
    
    app_id: str = Field(
        ...,
        description="Application ID"
    )
    
    operation_id: Optional[str] = Field(
        default=None,
        description="Unique operation identifier for tracking"
    )
    
    design_result: Optional[dict] = Field(
        default_factory=dict,
        description="Design generation operation details (populated when completed)"
    )
    
    comms_matrix_result: Optional[dict] = Field(
        default=None,
        description="Communications matrix generation results - automatically triggered after design completion. Contains status, total_flows, blob_url, etc."
    )

    message: str = Field(
        ...,
        description="Human-readable status message"
    )
    
    status_endpoint: Optional[str] = Field(
        default=None,
        description="Endpoint to check operation status"
    )
    
    result_endpoint: Optional[str] = Field(
        default=None,
        description="Endpoint to retrieve results when completed"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "accepted",
                "app_id": "myapp-001",
                "operation_id": "550e8400-e29b-41d4-a716-446655440000",
                "message": "Architecture design generation started. Use operation_id to check status and retrieve results.",
                "status_endpoint": "/operations/550e8400-e29b-41d4-a716-446655440000/status?app_id=myapp-001",
                "result_endpoint": "/operations/550e8400-e29b-41d4-a716-446655440000/result?app_id=myapp-001",
                "comms_matrix_result": {
                    "status": "success",
                    "total_flows": 15,
                    "blob_url": "https://storage.blob.core.windows.net/myapp-001/comms_prep/output/communications_matrix.xlsx",
                    "blob_uploaded": True
                }
            }
        }

class PlanningResponse(BaseModel):
    """
    Response model for app planning generation endpoint (async operation).
    
    This endpoint returns immediately with an operation_id for tracking.
    Use the status_endpoint to check progress and result_endpoint to get results.
    
    Attributes:
        status: Planning generation status (accepted, in_progress, completed, failed)
        app_id: Application ID
        operation_id: Unique operation identifier for tracking
        planning_result: Details of the planning generation operation (populated when completed)
        message: Human-readable status message
        status_endpoint: Endpoint to check operation status
        result_endpoint: Endpoint to retrieve results when completed
    """
    
    status: str = Field(
        ...,
        description="Operation status (accepted, in_progress, completed, failed)"
    )
    
    app_id: str = Field(
        ...,
        description="Application ID"
    )
    
    operation_id: Optional[str] = Field(
        default=None,
        description="Unique operation identifier for tracking"
    )
    
    planning_result: Optional[dict] = Field(
        default_factory=dict,
        description="Planning generation operation details (populated when completed)"
    )
    
    message: str = Field(
        ...,
        description="Human-readable status message"
    )
    
    status_endpoint: Optional[str] = Field(
        default=None,
        description="Endpoint to check operation status"
    )
    
    result_endpoint: Optional[str] = Field(
        default=None,
        description="Endpoint to retrieve results when completed"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "accepted",
                "app_id": "myapp-001",
                "operation_id": "550e8400-e29b-41d4-a716-446655440000",
                "message": "App planning generation started. Use operation_id to check status and retrieve results.",
                "status_endpoint": "/operations/550e8400-e29b-41d4-a716-446655440000/status?app_id=myapp-001",
                "result_endpoint": "/operations/550e8400-e29b-41d4-a716-446655440000/result?app_id=myapp-001"
            }
        }

class KubernetesDiscoveryResponse(BaseModel):
    """
    Response model for Kubernetes discovery endpoint (async operation).
    
    This endpoint returns immediately with an operation_id for tracking.
    Use the status_endpoint to check progress and result_endpoint to get results.
    
    Attributes:
        status: Overall status of the operation (accepted, in_progress, completed, failed)
        app_id: Application/Cluster ID
        operation_id: Unique operation identifier for tracking
        agent_id: Azure AI Agent ID created/used for discovery (populated when completed)
        message: Human-readable status message
        status_endpoint: Endpoint to check operation status
        result_endpoint: Endpoint to retrieve results when completed
    """
    
    status: str = Field(
        ...,
        description="Operation status (accepted, in_progress, completed, failed)"
    )
    
    app_id: str = Field(
        ...,
        description="Application/Cluster ID"
    )
    
    operation_id: Optional[str] = Field(
        default=None,
        description="Unique operation identifier for tracking"
    )
    
    agent_id: Optional[str] = Field(
        None,
        description="Azure AI Agent ID for this discovery session (populated when completed)"
    )
    
    message: str = Field(
        ...,
        description="Human-readable status message"
    )
    
    status_endpoint: Optional[str] = Field(
        default=None,
        description="Endpoint to check operation status"
    )
    
    result_endpoint: Optional[str] = Field(
        default=None,
        description="Endpoint to retrieve results when completed"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "accepted",
                "app_id": "my-aks-cluster",
                "operation_id": "550e8400-e29b-41d4-a716-446655440000",
                "message": "Kubernetes discovery started. Use operation_id to check status and retrieve results.",
                "status_endpoint": "/operations/550e8400-e29b-41d4-a716-446655440000/status?app_id=my-aks-cluster",
                "result_endpoint": "/operations/550e8400-e29b-41d4-a716-446655440000/result?app_id=my-aks-cluster"
            }
        }


class ArchitectureAnalysisRequest(BaseModel):
    """
    Request model for architecture security analysis endpoint.
    
    Attributes:
        app_id: Application ID (used for operation tracking and storage)
        storage_account_name: Name of the Azure Storage account
        user_object_id: Azure AD user object ID for authentication
        group_object_id: Azure AD group object ID for authentication
        resource_group_name: Optional resource group name
    
    Note: Design documents are automatically discovered from [app_id]/architecture-analyzer/input/ folder.
    """
    
    app_id: str = Field(
        ...,
        description="Application ID (used for operation tracking)",
        min_length=3,
        max_length=63
    )
    
    storage_account_name: str = Field(
        ...,
        description="Azure Storage account name",
        min_length=3,
        max_length=24
    )
    
    user_object_id: Optional[str] = Field(
        None,
        description="Azure AD user object ID (GUID format) - required if group_object_id not provided",
        pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    )
    
    group_object_id: Optional[str] = Field(
        None,
        description="Azure AD group object ID (GUID format) - required if user_object_id not provided",
        pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    )
    
    resource_group_name: Optional[str] = Field(
        None,
        description="Resource group name (optional, auto-discovered if not provided)"
    )
    
    @field_validator('app_id')
    @classmethod
    def validate_app_id(cls, v: str) -> str:
        """Validate app_id follows Azure naming rules."""
        v = v.lower()
        if not re.match(r'^[a-z0-9]', v):
            raise ValueError("app_id must start with a letter or number")
        if not re.match(r'^[a-z0-9][a-z0-9-]*[a-z0-9]$', v) and len(v) > 1:
            raise ValueError("app_id must contain only lowercase letters, numbers, and hyphens")
        if '--' in v:
            raise ValueError("app_id cannot contain consecutive hyphens")
        return v
    
    @field_validator('storage_account_name')
    @classmethod
    def validate_storage_account_name(cls, v: str) -> str:
        """Validate storage_account_name follows Azure naming rules."""
        v = v.lower()
        if not re.match(r'^[a-z0-9]{3,24}$', v):
            raise ValueError("storage_account_name must contain only lowercase letters and numbers (3-24 characters)")
        return v
    
    
    @model_validator(mode='after')
    def validate_at_least_one_id(self):
        """Ensure at least one of user_object_id or group_object_id is provided."""
        if not self.user_object_id and not self.group_object_id:
            raise ValueError("At least one of user_object_id or group_object_id must be provided")
        return self
    
    class Config:
        json_schema_extra = {
            "example": {
                "app_id": "myapp-001",
                "design_doc_url": "design-docs/myapp-001/architecture.md",
                "storage_account_name": "mystorageaccount",
                "user_object_id": "12345678-1234-1234-1234-123456789012",
                "group_object_id": "87654321-4321-4321-4321-210987654321",
                "resource_group_name": "my-resource-group"
            }
        }


class ArchitectureAnalysisResponse(BaseModel):
    """
    Response model for architecture security analysis endpoint (async operation).
    
    Attributes:
        status: Operation status (accepted for async operations)
        app_id: Application ID
        operation_id: Unique operation identifier for tracking
        design_doc_url: Blob storage path to design document being analyzed
        message: Human-readable status message
    """
    
    status: str = Field(
        ...,
        description="Operation status (accepted - analysis running in background)"
    )
    
    app_id: str = Field(
        ...,
        description="Application ID"
    )
    
    operation_id: str = Field(
        ...,
        description="Unique operation identifier for tracking progress"
    )
    
    design_doc_url: str = Field(
        ...,
        description="Blob storage path to design document being analyzed"
    )
    
    message: str = Field(
        ...,
        description="Human-readable status message"
    )
    
    error: Optional[str] = Field(
        None,
        description="Error or warning message if validation issues detected (operation may still proceed)"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "accepted",
                "app_id": "myapp-001",
                "operation_id": "123e4567-e89b-12d3-a456-426614174000",
                "design_doc_url": "design-docs/myapp-001/architecture.md",
                "message": "Architecture security analysis started in background. Use operation_id to track progress.",
                "error": None
            }
        }


class ArchitectureAnalysisResultResponse(BaseModel):
    """
    Response model for architecture analysis result retrieval.
    
    Attributes:
        status: Result status (completed, in_progress, failed)
        operation_id: Operation identifier
        design_doc_url: Blob storage path to design document analyzed
        total_architectures: Total number of architectures analyzed
        total_findings: Total number of security findings
        consolidated_report_url: URL to the consolidated security report
        message: Human-readable status message
    """
    
    status: str = Field(
        ...,
        description="Result status (completed, in_progress, failed)"
    )
    
    operation_id: str = Field(
        ...,
        description="Operation identifier"
    )
    
    design_doc_url: Optional[str] = Field(
        None,
        description="Blob storage path to design document analyzed"
    )
    
    total_architectures: Optional[int] = Field(
        None,
        description="Total number of architectures analyzed"
    )
    
    total_findings: Optional[int] = Field(
        None,
        description="Total number of security findings across all architectures"
    )
    
    consolidated_report_url: Optional[str] = Field(
        None,
        description="URL to the consolidated security report (available when completed)"
    )
    
    message: str = Field(
        ...,
        description="Human-readable status message"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "completed",
                "operation_id": "123e4567-e89b-12d3-a456-426614174000",
                "design_doc_url": "design-docs/myproject/architecture.md",
                "total_architectures": 5,
                "total_findings": 23,
                "consolidated_report_url": "https://mystorageaccount.blob.core.windows.net/reports/consolidated_report.md",
                "message": "Architecture security analysis completed successfully"
            }
        }


class CleanupRequest(BaseModel):
    """
    Request model for cleanup operations.
    
    Attributes:
        design_doc_url: Blob storage path to design document to identify resources for cleanup
        force_cleanup: Whether to force cleanup even if operations are in progress
    """
    
    design_doc_url: str = Field(
        ...,
        description="Blob storage path to design document used to identify resources for cleanup"
    )
    
    force_cleanup: bool = Field(
        default=False,
        description="Force cleanup even if operations are in progress"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "design_doc_url": "design-docs/myproject/architecture.md",
                "force_cleanup": False
            }
        }


class CleanupResponse(BaseModel):
    """
    Response model for cleanup operations.
    
    Attributes:
        status: Cleanup operation status
        design_doc_url: Blob storage path to design document that was cleaned up
        cleanup_result: Details of what was cleaned up
        message: Human-readable status message
    """
    
    status: str = Field(
        ...,
        description="Cleanup status (success, partial_success, failed)"
    )
    
    design_doc_url: str = Field(
        ...,
        description="Blob storage path to design document that was cleaned up"
    )
    
    cleanup_result: Dict[str, Any] = Field(
        default_factory=dict,
        description="Details of cleanup operations performed"
    )
    
    message: str = Field(
        ...,
        description="Human-readable status message"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "success",
                "design_doc_url": "design-docs/myproject/architecture.md",
                "cleanup_result": {
                    "architecture_agent": {
                        "status": "success",
                        "agent_id": "arch-agent-12345",
                        "agent_name": "Architecture-Agent-Design-doc1"
                    },
                    "orchestrator_agent": {
                        "status": "success",
                        "agent_id": "orch-agent-67890"
                    },
                    "threads_cleaned": 2,
                    "resources_released": True
                },
                "message": "All resources cleaned up successfully"
            }
        }


class GitHubCodeAnalysisRequest(BaseModel):
    """
    Request model for GitHub repository code analysis endpoint.
    
    Attributes:
        github_repo_url: GitHub repository URL to clone and analyze
        perform_security_scan: Whether to scan for secrets before upload
        analysis_options: Optional configuration for analysis behavior
    """
    
    github_repo_url: str = Field(
        ...,
        description="GitHub repository URL to clone and analyze (e.g., https://github.com/user/repo)",
        min_length=10
    )
    
    perform_security_scan: bool = Field(
        default=True,
        description="Whether to scan for secrets before uploading files"
    )
    
    analysis_options: Optional[Dict[str, Any]] = Field(
        default_factory=dict,
        description="Optional analysis configuration parameters"
    )
    
    @field_validator('github_repo_url')
    @classmethod
    def validate_github_url(cls, v: str) -> str:
        """
        Validate that github_repo_url is a valid GitHub URL.
        """
        if not v or len(v.strip()) == 0:
            raise ValueError("github_repo_url cannot be empty")
        
        # Check if it's a valid GitHub URL
        github_patterns = [
            r'https?://github\.com/[\w-]+/[\w.-]+',
            r'git@github\.com:[\w-]+/[\w.-]+\.git'
        ]
        
        if not any(re.match(pattern, v.strip()) for pattern in github_patterns):
            raise ValueError("github_repo_url must be a valid GitHub repository URL")
        
        return v.strip()
    
    class Config:
        json_schema_extra = {
            "example": {
                "github_repo_url": "https://github.com/microsoft/azure-sdk-for-python",
                "perform_security_scan": True,
                "analysis_options": {
                    "include_code_metrics": True,
                    "check_best_practices": True,
                    "analyze_security": True
                }
            }
        }


class GitHubCodeAnalysisResponse(BaseModel):
    """
    Response model for GitHub repository code analysis endpoint.
    
    Attributes:
        status: Analysis operation status
        github_repo_url: Original GitHub repository URL that was analyzed
        content_type: Detected content type (terraform, java, python, etc.)
        config_folder: Configuration folder used for analysis (terrasec or kinfosec)
        analysis_result: Detailed analysis results from agents
        repo_metadata: Metadata about the analyzed repository
        agents_info: Information about agents that performed the analysis
        message: Human-readable status message
        operation_id: Optional operation ID for tracking
    """
    
    status: str = Field(
        ...,
        description="Operation status (success, failed, in_progress)"
    )
    
    github_repo_url: str = Field(
        ...,
        description="GitHub repository URL that was analyzed"
    )
    
    content_type: str = Field(
        ...,
        description="Detected content type (terraform, java, python, javascript, general)"
    )
    
    config_folder: str = Field(
        ...,
        description="Configuration folder used (terrasec or kinfosec)"
    )
    
    analysis_result: Dict[str, Any] = Field(
        default_factory=dict,
        description="Detailed analysis results including agent conversations and findings"
    )
    
    repo_metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Metadata about the analyzed repository"
    )
    
    agents_info: Dict[str, Any] = Field(
        default_factory=dict,
        description="Information about agents that performed the analysis"
    )
    
    message: str = Field(
        ...,
        description="Human-readable status message"
    )
    
    operation_id: Optional[str] = Field(
        default=None,
        description="Unique operation identifier for tracking"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "success",
                "github_repo_url": "https://github.com/user/terraform-infrastructure",
                "content_type": "terraform",
                "config_folder": "terrasec",
                "analysis_result": {
                    "security_scan": {
                        "performed": True,
                        "secrets_found": 0,
                        "files_excluded": 0
                    },
                    "files_processed": 15,
                    "agents_used": [
                        "Terraform_Expert",
                        "Security_Expert"
                    ],
                    "messages": [
                        {
                            "agent": "Terraform_Expert",
                            "content": "Analyzed 15 Terraform resources...",
                            "role": "assistant"
                        },
                        {
                            "agent": "Security_Expert",
                            "content": "Found 2 security recommendations...",
                            "role": "assistant"
                        }
                    ]
                },
                "repo_metadata": {
                    "total_files": 25,
                    "total_size_bytes": 102400,
                    "file_types": {
                        ".tf": 12,
                        ".tfvars": 3,
                        ".md": 5
                    }
                },
                "agents_info": {
                    "agents_count": 2,
                    "orchestrator_used": True
                },
                "message": "GitHub repository analysis completed successfully"
            }
        }


class ArchitectureAnalyzerRequest(BaseModel):
    """
    Request model for architecture analyzer endpoints.
    
    Automatically discovers and analyzes all architectures from blob storage using dynamic mode.
    
    Attributes:
        app_id: Application ID for resource isolation and agent naming
        design_doc_url: Blob storage path to design document to analyze
        analysis_instructions: Custom instructions for the architecture analysis agent
    """
    
    app_id: str = Field(
        ...,
        description="Application ID for resource isolation and agent naming (e.g., 'myapp-001')",
        min_length=1
    )
    design_doc_url: str = Field(
        ..., 
        description="Blob storage path to design document (e.g., 'design-docs/project1/architecture.md')",
        min_length=3
    )
    analysis_instructions: str = Field(
        default="Analyze this architecture for security compliance and generate recommendations",
        description="Custom instructions for the architecture analysis agent"
    )
    
    @field_validator('app_id')
    @classmethod
    def validate_app_id(cls, v: str) -> str:
        """Validate that app_id is not empty."""
        if not v or len(v.strip()) == 0:
            raise ValueError("app_id cannot be empty")
        return v.strip()
    
    @field_validator('design_doc_url')
    @classmethod
    def validate_design_doc_url(cls, v: str) -> str:
        """Validate that design_doc_url is not empty."""
        if not v or len(v.strip()) == 0:
            raise ValueError("design_doc_url cannot be empty")
        
        # Accept both blob paths and full URLs
        return v.strip()
    
    class Config:
        json_schema_extra = {
            "example": {
                "design_doc_url": "design-docs/myproject/architecture.md",
                "analysis_instructions": "Focus on security compliance and data flow analysis"
            }
        }


class ArchitectureAnalyzerResponse(BaseModel):
    """
    Response model for synchronous architecture analyzer endpoint.
    
    Attributes:
        status: Analysis operation status
        agent_response: Response from the architecture analysis agent (single mode)
        execution_log: Detailed log of execution steps (single mode)
        generated_report_url: URL to the generated analysis report
        analysis_summary: Summary of analysis results
        timestamp: Response timestamp
        operation_id: Optional operation ID for tracking
        analysis_mode: The analysis mode used
        total_architectures: Total architectures analyzed (batch/dynamic mode)
        total_findings: Total findings across architectures (batch/dynamic mode)
        architecture_results: Individual architecture results (batch/dynamic mode)
        execution_time_seconds: Total execution time (batch/dynamic mode)
    """
    
    status: str = Field(..., description="Analysis operation status")
    agent_response: Optional[str] = Field(default=None, description="Response from the architecture analysis agent")
    execution_log: Optional[List[str]] = Field(default=None, description="Detailed log of execution steps")
    generated_report_url: Optional[str] = Field(default=None, description="URL to the generated analysis report")
    analysis_summary: Optional[Dict[str, Any]] = Field(default=None, description="Summary of analysis results")
    timestamp: str = Field(..., description="Response timestamp")
    operation_id: Optional[str] = Field(default=None, description="Operation ID for tracking")
    total_architectures: Optional[int] = Field(default=None, description="Total architectures analyzed")
    total_findings: Optional[int] = Field(default=None, description="Total findings across architectures")
    architecture_results: Optional[Dict[str, Any]] = Field(default=None, description="Individual architecture results")
    execution_time_seconds: Optional[float] = Field(default=None, description="Total execution time")


class ArchitectureAnalyzerAsyncResponse(BaseModel):
    """
    Response model for asynchronous architecture analyzer endpoint.
    
    Attributes:
        status: Initial operation status
        operation_id: Unique operation identifier
        message: Human-readable status message
        app_id: Application/project identifier
        timestamp: Response timestamp
    """
    
    status: str = Field(..., description="Initial operation status")
    operation_id: str = Field(..., description="Unique operation identifier")
    message: str = Field(..., description="Human-readable status message")
    app_id: str = Field(..., description="Application/project identifier")
    timestamp: str = Field(..., description="Response timestamp")


class OperationResultResponse(BaseModel):
    """
    Unified response model for operation results (code analysis and architecture analysis).
    
    This model consolidates both GitHubCodeAnalysisResponse and ArchitectureAnalysisResultResponse
    into a single, flexible response that can handle both operation types.
    """
    # Common fields for all operations
    status: str = Field(..., description="Operation status (success, failed, pending, in_progress, cancelled)")
    operation_id: str = Field(..., description="Unique operation identifier")
    operation_type: str = Field(..., description="Type of operation (code_analysis, architecture_analysis)")
    operation_status: str = Field(..., description="Current operation status")
    progress_percentage: int = Field(..., description="Progress percentage (0-100)")
    current_step: str = Field(..., description="Current processing step")
    timestamp: str = Field(..., description="Response timestamp")
    
    # Optional common fields
    error_details: Optional[Dict[str, Any]] = Field(default=None, description="Error details if operation failed")
    
    # Code analysis specific fields (when operation_type == 'code_analysis')
    github_repo_url: Optional[str] = Field(default=None, description="GitHub repository URL (code analysis)")
    content_type: Optional[str] = Field(default=None, description="Detected content type (code analysis)")
    config_folder: Optional[str] = Field(default=None, description="Configuration folder used (code analysis)")
    analysis_result: Optional[Dict[str, Any]] = Field(default=None, description="Detailed analysis results (code analysis)")
    repo_metadata: Optional[Dict[str, Any]] = Field(default=None, description="Repository metadata (code analysis)")
    agents_info: Optional[Dict[str, Any]] = Field(default=None, description="Agent information (code analysis)")
    
    # Architecture analysis specific fields (when operation_type == 'architecture_analysis')
    design_doc_url: Optional[str] = Field(default=None, description="Blob storage path to design document (architecture analysis)")
    app_id: Optional[str] = Field(default=None, description="Application ID (architecture analysis)")
    agent_response: Optional[str] = Field(default=None, description="Agent response content (architecture analysis)")
    execution_log: Optional[List[str]] = Field(default=None, description="Execution log (architecture analysis)")
    analysis_summary: Optional[Dict[str, Any]] = Field(default=None, description="Analysis summary (architecture analysis)")
    
    # Common fields for both
    generated_report_url: Optional[str] = Field(default=None, description="Generated report URL")
    report_url: Optional[str] = Field(default=None, description="Report URL (alias for generated_report_url)")
    message: Optional[str] = Field(default=None, description="Human-readable status message")


# =============================================================================
# CODE ANALYSIS MODELS
# =============================================================================

from enum import Enum


class SourceType(str, Enum):
    """
    Enum for supported source types for code analysis.
    """
    GITHUB = "github"
    GITLAB = "gitlab"
    AZURE_DEVOPS = "azure_devops"
    BITBUCKET = "bitbucket"
    BLOB = "blob"  # Azure Blob Storage URL where code is already uploaded
    LOCAL = "local"  # Local path (for internal use)


class CodeAnalysisRequest(BaseModel):
    """
    Request model for code analysis endpoint.
    Supports multiple source types: GitHub, GitLab, Azure DevOps, Bitbucket, and Azure Blob Storage.
    
    Attributes:
        app_id: Application ID for RBAC and operation tracking
        storage_account_name: Azure Storage account name for RBAC validation
        user_object_id: Azure AD user object ID for RBAC
        group_object_id: Azure AD group object ID for RBAC
        resource_group_name: Resource group name (optional)
        repo_url: Repository URL or Blob URL containing code to analyze
        source_type: Type of source (github, gitlab, azure_devops, bitbucket, blob)
        perform_security_scan: Whether to scan for secrets before upload
        analysis_options: Optional configuration for analysis behavior
    """

    # RBAC fields (following Intake pattern)
    app_id: str = Field(
        ...,
        description="Application ID for operation tracking and RBAC",
        min_length=3,
        max_length=63
    )

    storage_account_name: str = Field(
        ...,
        description="Azure Storage account name for RBAC validation",
        min_length=3,
        max_length=24
    )

    user_object_id: Optional[str] = Field(
        None,
        description="Azure AD user object ID (GUID format) - required if group_object_id not provided",
        pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    )

    group_object_id: Optional[str] = Field(
        None,
        description="Azure AD group object ID (GUID format) - required if user_object_id not provided",
        pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    )

    resource_group_name: Optional[str] = Field(
        None,
        description="Resource group name (optional, auto-discovered if not provided)"
    )

    # Code analysis fields
    repo_url: str = Field(
        ...,
        description="Repository URL or Blob URL containing code to analyze",
        min_length=10
    )

    source_type: Optional[SourceType] = Field(
        default=None,
        description="Type of source (github, gitlab, azure_devops, bitbucket, blob). Auto-detected if not provided."
    )

    perform_security_scan: bool = Field(
        default=True,
        description="Whether to scan for secrets before uploading files"
    )

    analysis_options: Optional[dict] = Field(
        default_factory=dict,
        description="Optional analysis configuration parameters"
    )

    @field_validator('app_id')
    @classmethod
    def validate_app_id(cls, v: str) -> str:
        """Validate app_id follows Azure container naming rules."""
        v = v.lower()
        if not re.match(r'^[a-z0-9]', v):
            raise ValueError("app_id must start with a letter or number")
        if '--' in v:
            raise ValueError("app_id cannot contain consecutive hyphens")
        return v

    @field_validator('repo_url')
    @classmethod
    def validate_repo_url(cls, v: str) -> str:
        """
        Validate that repo_url is a valid URL.
        
        For Azure Blob Storage URLs, the URL must point to a .zip or .tar.gz file.
        """
        if not v or len(v.strip()) == 0:
            raise ValueError("repo_url cannot be empty")

        v = v.strip()

        # Check if it's a valid URL (http, https, or git patterns)
        url_patterns = [
            r'https?://github\.com/[\w-]+/[\w.-]+',
            r'https?://gitlab\.com/[\w-]+/[\w.-]+',
            r'https?://dev\.azure\.com/[\w-]+/[\w.-]+',
            r'https?://bitbucket\.org/[\w-]+/[\w.-]+',
            r'https?://[\w-]+\.blob\.core\.windows\.net/[\w-]+/.+',  # Azure Blob Storage
            r'git@github\.com:[\w-]+/[\w.-]+\.git',
            r'git@gitlab\.com:[\w-]+/[\w.-]+\.git',
        ]

        if not any(re.match(pattern, v) for pattern in url_patterns):
            raise ValueError("repo_url must be a valid repository URL (GitHub, GitLab, Azure DevOps, Bitbucket) or Azure Blob Storage URL")

        # Additional validation for blob URLs - must be a zip or tar.gz file
        if '.blob.core.windows.net' in v.lower():
            url_path = v.split('?')[0].lower()  # Remove query params (SAS tokens)
            if not (url_path.endswith('.zip') or url_path.endswith('.tar.gz') or url_path.endswith('.tgz')):
                raise ValueError(
                    "Azure Blob Storage URL must point to a .zip or .tar.gz file. "
                    "Please zip your code folder and upload it to blob storage."
                )

        return v

    @model_validator(mode='after')
    def validate_at_least_one_id(self):
        """Ensure at least one of user_object_id or group_object_id is provided."""
        if not self.user_object_id and not self.group_object_id:
            raise ValueError("At least one of user_object_id or group_object_id must be provided")
        return self

    def get_source_type(self) -> SourceType:
        """
        Get the source type, auto-detecting if not explicitly set.
        """
        if self.source_type:
            return self.source_type

        url = self.repo_url.lower()
        if 'github.com' in url:
            return SourceType.GITHUB
        elif 'gitlab.com' in url:
            return SourceType.GITLAB
        elif 'dev.azure.com' in url or 'visualstudio.com' in url:
            return SourceType.AZURE_DEVOPS
        elif 'bitbucket.org' in url:
            return SourceType.BITBUCKET
        elif '.blob.core.windows.net' in url:
            return SourceType.BLOB
        else:
            return SourceType.GITHUB  # Default fallback

    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "summary": "GitHub Repository",
                    "value": {
                        "app_id": "myapp-001",
                        "storage_account_name": "mystorageaccount",
                        "user_object_id": "12345678-1234-1234-1234-123456789012",
                        "repo_url": "https://github.com/microsoft/azure-sdk-for-python",
                        "source_type": "github",
                        "perform_security_scan": True
                    }
                },
                {
                    "summary": "Azure Blob Storage (zip file)",
                    "value": {
                        "app_id": "myapp-001",
                        "storage_account_name": "mystorageaccount",
                        "user_object_id": "12345678-1234-1234-1234-123456789012",
                        "repo_url": "https://myaccount.blob.core.windows.net/code-uploads/myproject.zip",
                        "source_type": "blob",
                        "perform_security_scan": True
                    }
                }
            ]
        }


class CodeAnalysisResponse(BaseModel):
    """
    Response model for code analysis endpoint (async - returns operation_id immediately).
    
    Attributes:
        status: Acceptance status
        operation_id: Unique operation identifier for tracking
        app_id: Application ID
        repo_url: Repository URL being analyzed
        source_type: Detected source type
        message: Human-readable status message
        status_endpoint: URL to check status
        result_endpoint: URL to get results
    """

    status: str = Field(
        ...,
        description="Operation acceptance status (accepted)"
    )

    operation_id: str = Field(
        ...,
        description="Unique operation identifier for tracking"
    )

    app_id: str = Field(
        ...,
        description="Application ID"
    )

    repo_url: str = Field(
        ...,
        description="Repository URL being analyzed"
    )

    source_type: str = Field(
        ...,
        description="Detected source type (github, gitlab, azure_devops, bitbucket, blob)"
    )

    message: str = Field(
        ...,
        description="Human-readable status message"
    )

    status_endpoint: str = Field(
        ...,
        description="Endpoint to check operation status"
    )

    result_endpoint: str = Field(
        ...,
        description="Endpoint to get operation results"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "status": "accepted",
                "operation_id": "123e4567-e89b-12d3-a456-426614174000",
                "app_id": "myapp-001",
                "repo_url": "https://github.com/user/repo",
                "source_type": "github",
                "message": "Code analysis started. Use operation_id to check status and retrieve results.",
                "status_endpoint": "/operations/123e4567-e89b-12d3-a456-426614174000/status?app_id=myapp-001",
                "result_endpoint": "/operations/123e4567-e89b-12d3-a456-426614174000/result?app_id=myapp-001"
            }
        }


class CodeAnalysisResultResponse(BaseModel):
    """
    Response model for code analysis result retrieval.
    
    Attributes:
        status: Analysis operation status
        operation_id: Operation identifier
        app_id: Application ID
        repo_url: Repository URL that was analyzed
        content_type: Detected content type (terraform, java, python, etc.)
        config_folder: Configuration folder used for analysis (terrasec or kinfosec)
        analysis_result: Detailed analysis results from agents
        repo_metadata: Metadata about the analyzed repository
        agents_info: Information about agents that performed the analysis
        message: Human-readable status message
        report_url: URL to the generated analysis report
    """

    status: str = Field(
        ...,
        description="Operation status (success, failed)"
    )

    operation_id: str = Field(
        ...,
        description="Operation identifier"
    )

    app_id: str = Field(
        ...,
        description="Application ID"
    )

    repo_url: str = Field(
        ...,
        description="Repository URL that was analyzed"
    )

    content_type: str = Field(
        default="unknown",
        description="Detected content type (terraform, java, python, javascript, general)"
    )

    config_folder: str = Field(
        default="unknown",
        description="Configuration folder used (terrasec or kinfosec)"
    )

    analysis_result: dict = Field(
        default_factory=dict,
        description="Detailed analysis results including agent conversations and findings"
    )

    repo_metadata: dict = Field(
        default_factory=dict,
        description="Metadata about the analyzed repository"
    )

    agents_info: dict = Field(
        default_factory=dict,
        description="Information about agents that performed the analysis"
    )

    message: str = Field(
        ...,
        description="Human-readable status message"
    )

    report_url: Optional[str] = Field(
        None,
        description="URL to the generated analysis report in blob storage"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "status": "success",
                "operation_id": "123e4567-e89b-12d3-a456-426614174000",
                "app_id": "myapp-001",
                "repo_url": "https://github.com/user/terraform-infrastructure",
                "content_type": "terraform",
                "config_folder": "terrasec",
                "analysis_result": {
                    "security_scan": {
                        "performed": True,
                        "secrets_found": 0
                    },
                    "files_processed": 15,
                    "agents_used": ["Terraform_Expert", "Security_Expert"]
                },
                "repo_metadata": {
                    "total_files": 25,
                    "total_size_bytes": 102400
                },
                "agents_info": {
                    "agents_count": 2,
                    "orchestrator_used": True
                },
                "message": "Code analysis completed successfully",
                "report_url": "https://storage.blob.core.windows.net/myapp-001/code_analysis_report.md"
            }
        }