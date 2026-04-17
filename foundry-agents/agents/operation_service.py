"""
Operation status tracking service for managing the state of API operations.

This service provides centralized tracking of operation states across all API endpoints,
including progress monitoring, status updates, and operation history.
"""

import json
import asyncio
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta, timezone
from azure.data.tables import TableServiceClient, TableClient
from azure.data.tables.aio import TableServiceClient as AsyncTableServiceClient
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
import os
import logging
from logging_config import get_logger
from operation_models import (
    OperationRecord, 
    OperationStatus, 
    OperationType,
    OperationStatusRequest,
    OperationStatusResponse,
    OperationSummary,
    OperationSummaryResponse
)

# Create logger for this module
logger = get_logger(__name__)

class OperationStatusService:
    """
    Service for tracking and managing operation status across all API endpoints.
    
    Uses Azure Table Storage for persistent state management with efficient querying.
    """
    
    def __init__(self, storage_account_url: str = None, table_name: str = "OperationStatus"):
        """
        Initialize the operation status service.
        
        Args:
            storage_account_url: Azure Storage account URL
            table_name: Name of the table to store operation status
        """
        self.storage_account_url = storage_account_url or os.getenv("AZURE_TABLES_ACCOUNT_URL")
        self.table_name = table_name
        self.table_client = None
        self._initialize_storage()
    
    def _initialize_storage(self):
        """Initialize the Azure Table Storage client and create table if needed."""
        try:
            if not self.storage_account_url:
                raise ValueError("AZURE_TABLES_ACCOUNT_URL environment variable not set")
            
            # Create table service client with managed identity
            from azure.identity import DefaultAzureCredential
            credential = DefaultAzureCredential()
            
            table_service_client = TableServiceClient(
                endpoint=self.storage_account_url,
                credential=credential
            )
            
            # Get table client
            self.table_client = table_service_client.get_table_client(table_name=self.table_name)
            
            # Create table if it doesn't exist
            try:
                self.table_client.create_table()
                logger.info(f"Created operation status table: {self.table_name}")
            except ResourceExistsError:
                logger.info(f"Operation status table already exists: {self.table_name}")
                
        except Exception as ex:
            logger.error(f"Failed to initialize operation status storage: {str(ex)}")
            raise
    
    def _operation_to_entity(self, operation: OperationRecord) -> Dict[str, Any]:
        """
        Convert OperationRecord to Azure Table entity.
        
        Args:
            operation: OperationRecord instance
            
        Returns:
            Dictionary representing the table entity
        """
        # Use app_id as PartitionKey and operation_id as RowKey for efficient querying
        entity = {
            "PartitionKey": operation.app_id,
            "RowKey": operation.operation_id,
            "app_id": operation.app_id,
            "operation_type": operation.operation_type.value,
            "status": operation.status.value,
            "user_object_id": operation.user_object_id,
            "group_object_id": operation.group_object_id,
            "storage_account_name": operation.storage_account_name,
            "resource_group_name": operation.resource_group_name,
            "design_doc_url": operation.design_doc_url,  # Add design_doc_url field
            "timestamp_started": operation.timestamp_started,
            "timestamp_updated": operation.timestamp_updated,
            "timestamp_completed": operation.timestamp_completed,
            "progress_percentage": operation.progress_percentage,
            "current_step": operation.current_step,
            "total_steps": operation.total_steps,
            "completed_steps": operation.completed_steps,
            "duration_seconds": operation.duration_seconds,
            # Store complex objects as JSON strings
            "steps": json.dumps([step.model_dump() for step in operation.steps], default=str),
            "result_data": json.dumps(operation.result_data, default=str) if operation.result_data else None,
            "error_details": json.dumps(operation.error_details, default=str) if operation.error_details else None,
            "metadata": json.dumps(operation.metadata, default=str) if operation.metadata else None,
        }
        
        return entity
    
    def _entity_to_operation(self, entity: Dict[str, Any]) -> OperationRecord:
        """
        Convert Azure Table entity to OperationRecord.
        
        Args:
            entity: Table entity dictionary
            
        Returns:
            OperationRecord instance
        """
        # Parse JSON fields
        steps_data = json.loads(entity.get("steps", "[]"))
        result_data = json.loads(entity.get("result_data", "{}")) if entity.get("result_data") else {}
        error_details = json.loads(entity.get("error_details", "{}")) if entity.get("error_details") else None
        metadata = json.loads(entity.get("metadata", "{}")) if entity.get("metadata") else {}
        
        # Convert steps back to OperationStep objects
        from operation_models import OperationStep
        steps = [OperationStep(**step_data) for step_data in steps_data]
        
        operation = OperationRecord(
            operation_id=entity["RowKey"],
            app_id=entity["app_id"],
            operation_type=OperationType(entity["operation_type"]),
            status=OperationStatus(entity["status"]),
            user_object_id=entity.get("user_object_id"),
            group_object_id=entity.get("group_object_id"),
            storage_account_name=entity["storage_account_name"],
            resource_group_name=entity.get("resource_group_name"),
            design_doc_url=entity.get("design_doc_url"),  # Add design_doc_url field
            timestamp_started=entity["timestamp_started"],
            timestamp_updated=entity["timestamp_updated"],
            timestamp_completed=entity.get("timestamp_completed"),
            progress_percentage=entity.get("progress_percentage", 0),
            current_step=entity.get("current_step", ""),
            total_steps=entity.get("total_steps", 1),
            completed_steps=entity.get("completed_steps", 0),
            duration_seconds=entity.get("duration_seconds"),
            steps=steps,
            result_data=result_data,
            error_details=error_details,
            metadata=metadata
        )
        
        return operation
    
    async def create_operation(self, operation: OperationRecord) -> str:
        """
        Create a new operation record.
        
        Args:
            operation: OperationRecord to create
            
        Returns:
            Operation ID of the created operation
        """
        try:
            entity = self._operation_to_entity(operation)
            self.table_client.create_entity(entity=entity)
            
            logger.info(f"Created operation record: {operation.operation_id} for app {operation.app_id}")
            return operation.operation_id
            
        except Exception as ex:
            logger.error(f"Failed to create operation record {operation.operation_id}: {str(ex)}")
            raise
    
    async def update_operation(self, operation: OperationRecord) -> None:
        """
        Update an existing operation record.
        
        Args:
            operation: OperationRecord with updated information
        """
        try:
            # Update timestamp
            operation.timestamp_updated = datetime.now(timezone.utc)
            
            entity = self._operation_to_entity(operation)
            self.table_client.update_entity(entity=entity, mode="replace")
            
            logger.debug(f"Updated operation {operation.operation_id}: {operation.status.value} - {operation.current_step}")
            
        except Exception as ex:
            logger.error(f"Failed to update operation {operation.operation_id}: {str(ex)}")
            raise
    
    async def get_operation(self, operation_id: str, app_id: str) -> Optional[OperationRecord]:
        """
        Get a specific operation record.
        
        Args:
            operation_id: Unique operation identifier
            app_id: Application ID (used as partition key)
            
        Returns:
            OperationRecord if found, None otherwise
        """
        try:
            entity = self.table_client.get_entity(partition_key=app_id, row_key=operation_id)
            return self._entity_to_operation(entity)
            
        except ResourceNotFoundError:
            logger.warning(f"Operation not found: {operation_id} in app {app_id}")
            return None
        except Exception as ex:
            logger.error(f"Failed to get operation {operation_id}: {str(ex)}")
            raise
    
    async def list_operations(self, request: OperationStatusRequest) -> OperationStatusResponse:
        """
        List operations based on filter criteria.
        
        Args:
            request: Filter and pagination parameters
            
        Returns:
            OperationStatusResponse with matching operations
        """
        try:
            # Build query filter
            filter_conditions = []
            
            if request.app_id:
                filter_conditions.append(f"PartitionKey eq '{request.app_id}'")
            
            if request.operation_type:
                filter_conditions.append(f"operation_type eq '{request.operation_type.value}'")
            
            if request.status:
                filter_conditions.append(f"status eq '{request.status.value}'")
            
            if request.user_object_id:
                filter_conditions.append(f"user_object_id eq '{request.user_object_id}'")
            
            # Combine filters
            query_filter = " and ".join(filter_conditions) if filter_conditions else None
            
            # Query ALL entities first (don't apply pagination yet)
            all_entities = list(self.table_client.query_entities(query_filter=query_filter))
            
            # Convert to OperationRecord objects
            all_operations = [self._entity_to_operation(entity) for entity in all_entities]
            
            # Sort by timestamp_updated (most recent first) BEFORE pagination
            all_operations.sort(key=lambda x: x.timestamp_updated, reverse=True)
            
            # Apply pagination AFTER sorting
            total_count = len(all_operations)
            start_idx = request.offset
            end_idx = request.offset + request.limit
            operations = all_operations[start_idx:end_idx]
            
            # Check if there are more results
            has_more = end_idx < total_count
            
            return OperationStatusResponse(
                total_count=total_count,
                operations=operations,
                has_more=has_more
            )
            
        except Exception as ex:
            logger.error(f"Failed to list operations: {str(ex)}")
            raise
    
    async def get_operation_summary(self, app_id: str = None, days: int = 7) -> OperationSummaryResponse:
        """
        Get operation summary statistics.
        
        Args:
            app_id: Optional filter by application ID
            days: Number of days to include in summary (default: 7)
            
        Returns:
            OperationSummaryResponse with summary statistics
        """
        try:
            # Calculate date threshold
            since_date = datetime.now(timezone.utc) - timedelta(days=days)
            
            # Build query filter
            filter_conditions = [f"timestamp_started ge datetime'{since_date.isoformat()}'"]
            
            if app_id:
                filter_conditions.append(f"PartitionKey eq '{app_id}'")
            
            query_filter = " and ".join(filter_conditions)
            
            # Query all matching entities
            entities = list(self.table_client.query_entities(query_filter=query_filter))
            
            # Calculate statistics
            total_operations = len(entities)
            operations_by_status = {}
            operations_by_type = {}
            completed_durations = []
            successful_operations = 0
            
            for entity in entities:
                status = entity.get("status", "unknown")
                op_type = entity.get("operation_type", "unknown")
                
                # Count by status
                operations_by_status[status] = operations_by_status.get(status, 0) + 1
                
                # Count by type
                operations_by_type[op_type] = operations_by_type.get(op_type, 0) + 1
                
                # Track successful operations
                if status == OperationStatus.COMPLETED.value:
                    successful_operations += 1
                    
                    # Collect duration for completed operations
                    duration = entity.get("duration_seconds")
                    if duration:
                        completed_durations.append(duration)
            
            # Calculate averages
            avg_duration = sum(completed_durations) / len(completed_durations) if completed_durations else None
            success_rate = (successful_operations / total_operations * 100) if total_operations > 0 else 0
            
            # Get recent operations (last 10)
            recent_entities = sorted(entities, key=lambda x: x.get("timestamp_updated", datetime.min), reverse=True)[:10]
            recent_operations = [self._entity_to_operation(entity) for entity in recent_entities]
            
            summary = OperationSummary(
                total_operations=total_operations,
                operations_by_status=operations_by_status,
                operations_by_type=operations_by_type,
                avg_duration_seconds=avg_duration,
                success_rate=round(success_rate, 1)
            )
            
            return OperationSummaryResponse(
                summary=summary,
                recent_operations=recent_operations
            )
            
        except Exception as ex:
            logger.error(f"Failed to get operation summary: {str(ex)}")
            raise
    
    async def cleanup_operations_by_app(self, app_id: str) -> int:
        """
        Clean up ALL operations for a specific application ID.
        
        Args:
            app_id: Application ID - all operations for this app will be deleted
            
        Returns:
            Number of operations deleted
        """
        try:
            # Query all operations for this app (using PartitionKey for efficiency)
            filter_condition = f"PartitionKey eq '{app_id}'"
            
            # Query all operations for this app
            app_entities = list(self.table_client.query_entities(query_filter=filter_condition))
            
            # Delete all operations for this app
            deleted_count = 0
            for entity in app_entities:
                try:
                    self.table_client.delete_entity(
                        partition_key=entity["PartitionKey"],
                        row_key=entity["RowKey"]
                    )
                    deleted_count += 1
                except Exception as ex:
                    logger.warning(f"Failed to delete operation {entity['RowKey']}: {str(ex)}")
            
            logger.info(f"Cleaned up ALL {deleted_count} operation records for app '{app_id}'")
            return deleted_count
            
        except Exception as ex:
            logger.error(f"Failed to cleanup operations for app {app_id}: {str(ex)}")
            raise

    async def cleanup_old_operations(self, days: int = 30, app_id: str = None) -> int:
        """
        Clean up operations older than specified days.
        
        Args:
            days: Operations older than this will be deleted
            app_id: Optional filter by application ID. If provided, only operations 
                   for this app will be cleaned up. If None, all apps will be cleaned.
            
        Returns:
            Number of operations deleted
        """
        try:
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
            
            # Build query filter
            filter_conditions = [f"timestamp_started lt datetime'{cutoff_date.isoformat()}'"]
            
            if app_id:
                filter_conditions.append(f"PartitionKey eq '{app_id}'")
            
            filter_condition = " and ".join(filter_conditions)
            
            # Query old operations
            old_entities = list(self.table_client.query_entities(query_filter=filter_condition))
            
            # Delete old operations
            deleted_count = 0
            for entity in old_entities:
                try:
                    self.table_client.delete_entity(
                        partition_key=entity["PartitionKey"],
                        row_key=entity["RowKey"]
                    )
                    deleted_count += 1
                except Exception as ex:
                    logger.warning(f"Failed to delete old operation {entity['RowKey']}: {str(ex)}")
            
            if app_id:
                logger.info(f"Cleaned up {deleted_count} old operation records for app '{app_id}' (older than {days} days)")
            else:
                logger.info(f"Cleaned up {deleted_count} old operation records across all apps (older than {days} days)")
            
            return deleted_count
            
        except Exception as ex:
            logger.error(f"Failed to cleanup old operations: {str(ex)}")
            raise

# Global instance
_operation_service = None

def get_operation_service() -> OperationStatusService:
    """Get the global operation status service instance."""
    global _operation_service
    if _operation_service is None:
        _operation_service = OperationStatusService()
    return _operation_service