"""
Decorator for automatic operation tracking across API endpoints.

This module provides decorators and utilities to automatically track
the status and progress of operations across all API endpoints.
"""

import asyncio
import functools
import traceback
from typing import Callable, Any, Dict
from datetime import datetime
import inspect
from logging_config import get_logger
from operation_models import (
    OperationRecord,
    OperationStatus,
    OperationType
)
from operation_service import get_operation_service

logger = get_logger(__name__)

def track_operation(operation_type: OperationType, total_steps: int = 1):
    """
    Decorator to automatically track operation status for API endpoints.
    
    Args:
        operation_type: Type of operation being tracked
        total_steps: Total number of steps in the operation
    
    Usage:
        @track_operation(OperationType.RUN_ANALYSIS, total_steps=3)
        async def run_analysis(request: ApplicationOperationRequest):
            # Your endpoint implementation
            pass
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Extract request from arguments (both positional and keyword args)
            request = None
            
            # First check positional arguments
            for arg in args:
                if hasattr(arg, 'app_id') and (hasattr(arg, 'user_object_id') or hasattr(arg, 'group_object_id')):
                    request = arg
                    break
            
            # If not found in args, check keyword arguments
            if not request:
                for key, value in kwargs.items():
                    if hasattr(value, 'app_id') and (hasattr(value, 'user_object_id') or hasattr(value, 'group_object_id')):
                        request = value
                        break
            
            if not request:
                # If no request found, execute function normally
                logger.warning(f"No request object found for operation tracking in {func.__name__}")
                return await func(*args, **kwargs)
            
            # Create operation record
            operation = OperationRecord(
                app_id=request.app_id,
                operation_type=operation_type,
                user_object_id=getattr(request, 'user_object_id', None),
                group_object_id=getattr(request, 'group_object_id', None),
                storage_account_name=request.storage_account_name,
                resource_group_name=getattr(request, 'resource_group_name', None),
                total_steps=total_steps,
                current_step="Starting operation"
            )
            
            operation_service = get_operation_service()
            operation_id = None
            
            try:
                # Create operation record
                operation_id = await operation_service.create_operation(operation)
                
                # Create automatic steps based on total_steps
                if total_steps > 1:
                    # Create steps with generic names
                    step_names = [
                        f"Step {i+1}" if total_steps <= 5 else f"Step {i+1}/{total_steps}" 
                        for i in range(total_steps)
                    ]
                    
                    # Override with more descriptive names for known operation types
                    if operation_type == OperationType.CREATE_APPLICATION and total_steps == 4:
                        step_names = ["Validate request", "Create storage resources", "Configure RBAC", "Initialize application"]
                    elif operation_type == OperationType.INDEX_DOCUMENTS and total_steps == 3:
                        step_names = ["Prepare documents", "Index content", "Finalize indexing"]
                    elif operation_type == OperationType.RUN_ANALYSIS and total_steps == 5:
                        step_names = ["Initialize analysis", "Process data", "Run algorithms", "Generate insights", "Finalize results"]
                    elif operation_type == OperationType.GENERATE_REPORT and total_steps == 3:
                        step_names = ["Gather data", "Generate report", "Format output"]
                    elif operation_type == OperationType.DELETE_APP_DATA and total_steps == 4:
                        step_names = ["Delete all agents", "Delete threads", "Delete storage container", "Delete search index"]
                    
                    # Create all steps as pending initially
                    for i, step_name in enumerate(step_names):
                        operation.update_progress(step_name, int((i / total_steps) * 10), OperationStatus.PENDING)
                    
                    # Mark first step as in progress
                    operation.update_progress(step_names[0], 10, OperationStatus.IN_PROGRESS)
                else:
                    # Single step operation
                    operation.update_progress("Operation started", 10, OperationStatus.IN_PROGRESS)
                
                await operation_service.update_operation(operation)
                
                # Add operation_id to response context (if possible)
                if hasattr(func, '__annotations__'):
                    # Store operation context for the function
                    func._current_operation = operation
                
                # Execute the actual function
                result = await func(*args, **kwargs)
                
                # Mark as completed
                operation.complete_operation({"response": result})
                await operation_service.update_operation(operation)
                
                # Add operation_id to response if it's a dict
                if isinstance(result, dict):
                    result["operation_id"] = operation_id
                elif hasattr(result, '__dict__'):
                    # For Pydantic models, add operation_id if possible
                    try:
                        if hasattr(result, 'operation_id'):
                            result.operation_id = operation_id
                        else:
                            logger.debug(f"Response model {type(result).__name__} doesn't have operation_id field")
                    except (AttributeError, ValueError) as ex:
                        # Field doesn't exist or is read-only, that's okay
                        logger.debug(f"Could not set operation_id on response: {ex}")
                        pass
                
                return result
                
            except Exception as ex:
                # Mark as failed
                if operation_id:
                    error_details = {
                        "error_type": type(ex).__name__,
                        "error_message": str(ex),
                        "traceback": traceback.format_exc()
                    }
                    operation.fail_operation(str(ex), error_details)
                    await operation_service.update_operation(operation)
                
                # Re-raise the exception
                raise
        
        return wrapper
    return decorator

async def update_operation_progress(operation_id: str, app_id: str, step_name: str, progress: int, status: OperationStatus = None):
    """
    Utility function to update operation progress from within an endpoint.
    
    Args:
        operation_id: Operation ID to update
        app_id: Application ID
        step_name: Current step description
        progress: Progress percentage (0-100)
        status: Optional status update
    """
    try:
        operation_service = get_operation_service()
        operation = await operation_service.get_operation(operation_id, app_id)
        
        if operation:
            operation.update_progress(step_name, progress, status)
            await operation_service.update_operation(operation)
        else:
            logger.warning(f"Operation {operation_id} not found for progress update")
            
    except Exception as ex:
        logger.error(f"Failed to update operation progress for {operation_id}: {str(ex)}")

class OperationTracker:
    """
    Context manager for manual operation tracking within functions.
    
    Usage:
        async with OperationTracker(request, OperationType.RUN_ANALYSIS, 5) as tracker:
            await tracker.update_progress("Step 1", 20)
            # Do some work
            await tracker.update_progress("Step 2", 40)
            # Do more work
    """
    
    def __init__(self, request, operation_type: OperationType, total_steps: int = 1):
        self.request = request
        self.operation_type = operation_type
        self.total_steps = total_steps
        self.operation = None
        self.operation_service = get_operation_service()
    
    async def __aenter__(self):
        # Create operation record
        self.operation = OperationRecord(
            app_id=self.request.app_id,
            operation_type=self.operation_type,
            user_object_id=getattr(self.request, 'user_object_id', None),
            group_object_id=getattr(self.request, 'group_object_id', None),
            storage_account_name=self.request.storage_account_name,
            resource_group_name=getattr(self.request, 'resource_group_name', None),
            total_steps=self.total_steps,
            current_step="Initializing"
        )
        
        await self.operation_service.create_operation(self.operation)
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            # Exception occurred
            error_details = {
                "error_type": exc_type.__name__,
                "error_message": str(exc_val),
                "traceback": traceback.format_exc()
            }
            self.operation.fail_operation(str(exc_val), error_details)
        else:
            # Success
            self.operation.complete_operation()
        
        await self.operation_service.update_operation(self.operation)
    
    async def update_progress(self, step_name: str, progress: int, status: OperationStatus = None):
        """Update operation progress."""
        if self.operation:
            self.operation.update_progress(step_name, progress, status)
            await self.operation_service.update_operation(self.operation)
    
    @property
    def operation_id(self) -> str:
        """Get the operation ID."""
        return self.operation.operation_id if self.operation else None