"""
Background task functions for long-running API operations.

This module contains the async background task functions that run the actual
processing for endpoints that return immediately with an operation_id.
The background tasks update the operation status as they progress.
"""

import asyncio
import json
import time
from typing import Optional, Dict, Any
from datetime import datetime, timezone

from agents.logging_config import get_logger
from agents.operation_models import OperationRecord, OperationStatus, OperationType
from agents.operation_service import get_operation_service
from agents.orchestrator_agent import call_orchestrator, get_confidence_scores
from agents.rbac_helper import RBACHelper

# Import tracing utilities
from agents.tracing_config import (
    get_tracer,
    add_span_attributes,
    record_error_details
)
from opentelemetry.trace import Status, StatusCode

logger = get_logger(__name__)

# Dictionary to track running background tasks for future task management (cancel, list)
_running_tasks: Dict[str, asyncio.Task] = {}


def track_task(operation_id: str, task: asyncio.Task) -> None:
    """Track a running background task."""
    _running_tasks[operation_id] = task
    logger.debug(f"Tracking background task for operation {operation_id}")


def remove_task(operation_id: str) -> None:
    """Remove a completed/failed background task from tracking."""
    if operation_id in _running_tasks:
        del _running_tasks[operation_id]
        logger.debug(f"Removed background task for operation {operation_id}")


def get_running_tasks() -> Dict[str, asyncio.Task]:
    """Get all currently running background tasks."""
    return _running_tasks.copy()


async def run_analysis_background(
    operation_id: str,
    app_id: str,
    storage_account_name: str,
    user_object_id: Optional[str] = None,
    group_object_id: Optional[str] = None,
    resource_group_name: Optional[str] = None
) -> None:
    """
    Background task to run the actual analysis and update operation status.
    
    Args:
        operation_id: Operation ID to track
        app_id: Application ID
        storage_account_name: Storage account for report storage
        user_object_id: User object ID for RBAC
        group_object_id: Group object ID for RBAC
        resource_group_name: Resource group name
    """
    operation_service = get_operation_service()
    tracer = get_tracer()
    analysis_start_time = time.time()
    
    with tracer.start_as_current_span("run_analysis.background_task") as bg_span:
        add_span_attributes(bg_span, {
            "analysis.operation_id": operation_id,
            "analysis.app_id": app_id,
        })
        
        try:
            # Get operation record
            operation = await operation_service.get_operation(operation_id, app_id)
            if not operation:
                logger.error(f"Operation {operation_id} not found for background analysis")
                bg_span.set_status(Status(StatusCode.ERROR, "Operation not found"))
                return
            
            # Update: Starting analysis (5%)
            operation.update_progress("Initializing analysis", 5, OperationStatus.IN_PROGRESS)
            await operation_service.update_operation(operation)
            
            # Update: Calling orchestrator (10%)
            operation.update_progress("Starting analysis via orchestrator", 10, OperationStatus.IN_PROGRESS)
            await operation_service.update_operation(operation)
            
            # Compose the message for running analysis
            message = (
                f"Run analysis for application ID: {app_id} and storage name: {storage_account_name}. "
                f"- create the Responder agent and invoke the Responder agent to process all QA tables, dependencies, and infrastructure"
            )
            logger.debug(f"Analysis orchestrator message: {message}")
            
            # Update: Processing (15%)
            operation.update_progress("Processing data with orchestrator", 15, OperationStatus.IN_PROGRESS)
            await operation_service.update_operation(operation)
            
            # Define progress callback for orchestrator to report intermediate progress
            async def progress_callback(message: str, percentage: int):
                """Callback for orchestrator to report progress during analysis"""
                try:
                    op = await operation_service.get_operation(operation_id, app_id)
                    if op:
                        # Map orchestrator progress (0-100%) to our range (20-90%)
                        adjusted_percentage = 20 + int(percentage * 0.7)
                        op.update_progress(message, adjusted_percentage, OperationStatus.IN_PROGRESS)
                        await operation_service.update_operation(op)
                        logger.info(f"Orchestrator progress: {message} ({adjusted_percentage}%)")
                except Exception as e:
                    logger.warning(f"Failed to update progress from orchestrator: {e}")
            
            # Call the orchestrator agent asynchronously with progress callback
            result = await call_orchestrator(message, app_id, progress_callback=progress_callback)
            logger.info(f"Orchestrator result for analysis: {result}")
            
            # Update: Parsing results (93%)
            operation.update_progress("Parsing analysis results", 93, OperationStatus.IN_PROGRESS)
            await operation_service.update_operation(operation)
            
            # Parse result if it's a string
            analysis_result = {}
            if isinstance(result, str):
                try:
                    analysis_result = json.loads(result) if result else {}
                except json.JSONDecodeError:
                    analysis_result = {"raw_message": result}
            elif isinstance(result, dict):
                analysis_result = result
            else:
                analysis_result = {"result": str(result)}
            
            # Check if the analysis operation actually succeeded
            if analysis_result.get("result") == "error" or analysis_result.get("status") == "error":
                error_message = analysis_result.get("message", "Unknown analysis error occurred")
                logger.error(f"Analysis failed for application ID {app_id}: {error_message}")
                
                operation.fail_operation(error_message, {"result": analysis_result})
                await operation_service.update_operation(operation)
                
                bg_span.set_status(Status(StatusCode.ERROR, error_message[:256]))
                return
            
            # Update: Finalizing (96%)
            operation.update_progress("Finalizing analysis results", 96, OperationStatus.IN_PROGRESS)
            await operation_service.update_operation(operation)
            
            # Extract confidence scores from the orchestrator result (may be None if not present)
            table_confidence_scores = analysis_result.get("table_confidence_scores")
            overall_average_score = analysis_result.get("overall_average_confidence_score")
            
            # If confidence scores weren't in the orchestrator result, retrieve them from stored class variable
            if table_confidence_scores is None or overall_average_score is None:
                stored_scores = get_confidence_scores(app_id)
                if stored_scores:
                    table_confidence_scores = stored_scores.get("table_confidence_scores")
                    overall_average_score = stored_scores.get("overall_average_confidence_score")
                    logger.info(f"Retrieved stored confidence scores for application {app_id}")
            
            # Prepare final result
            final_result = {
                "status": "success",
                "app_id": app_id,
                "analysis_result": analysis_result,
                "table_confidence_scores": table_confidence_scores,
                "overall_average_confidence_score": overall_average_score,
                "message": f"Analysis completed successfully for application '{app_id}'"
            }
            
            # Mark operation as completed
            operation.complete_operation(final_result)
            await operation_service.update_operation(operation)
            
            # Record success metrics
            analysis_duration = (time.time() - analysis_start_time) * 1000
            add_span_attributes(bg_span, {
                "analysis.status": "success",
                "analysis.duration_ms": analysis_duration,
            })
            bg_span.set_status(Status(StatusCode.OK))
            
            logger.info(f"Background analysis completed successfully for operation {operation_id}")
            
        except Exception as ex:
            logger.error(f"Background analysis exception for operation {operation_id}: {str(ex)}")
            bg_span.record_exception(ex)
            bg_span.set_status(Status(StatusCode.ERROR, str(ex)[:256]))
            
            try:
                operation = await operation_service.get_operation(operation_id, app_id)
                if operation:
                    operation.fail_operation(str(ex), {"error_type": type(ex).__name__})
                    await operation_service.update_operation(operation)
            except Exception as update_ex:
                logger.error(f"Failed to update operation status after error: {update_ex}")
        
        finally:
            remove_task(operation_id)


async def generate_assessment_report_background(
    operation_id: str,
    app_id: str,
    storage_account_name: str,
    user_object_id: Optional[str] = None,
    group_object_id: Optional[str] = None,
    resource_group_name: Optional[str] = None
) -> None:
    """
    Background task to generate the assessment report and update operation status.
    
    Args:
        operation_id: Operation ID to track
        app_id: Application ID
        storage_account_name: Storage account for report storage
        user_object_id: User object ID for RBAC
        group_object_id: Group object ID for RBAC
        resource_group_name: Resource group name
    """
    operation_service = get_operation_service()
    tracer = get_tracer()
    start_time = time.time()
    
    with tracer.start_as_current_span("generate_assessment_report.background_task") as bg_span:
        add_span_attributes(bg_span, {
            "report.operation_id": operation_id,
            "report.app_id": app_id,
        })
        
        try:
            # Get operation record
            operation = await operation_service.get_operation(operation_id, app_id)
            if not operation:
                logger.error(f"Operation {operation_id} not found for background report generation")
                bg_span.set_status(Status(StatusCode.ERROR, "Operation not found"))
                return
            
            # Update: Starting (5%)
            operation.update_progress("Initializing report generation", 5, OperationStatus.IN_PROGRESS)
            await operation_service.update_operation(operation)
            
            # Update: Gathering data (10%)
            operation.update_progress("Gathering data for report", 10, OperationStatus.IN_PROGRESS)
            await operation_service.update_operation(operation)
            
            # Compose the message for the orchestrator agent
            message = f"Generate assessment report for application ID: {app_id}"
            logger.debug(f"Report generation orchestrator message: {message}")
            
            # Update: Generating (15%)
            operation.update_progress("Generating assessment report via orchestrator", 15, OperationStatus.IN_PROGRESS)
            await operation_service.update_operation(operation)
            
            # Define progress callback to update operation status during ASR agent processing
            async def progress_callback(message: str, percentage: int):
                """Update operation progress during ASR agent processing (20-85% range)."""
                operation.update_progress(message, percentage, OperationStatus.IN_PROGRESS)
                await operation_service.update_operation(operation)
            
            # Call the orchestrator agent asynchronously with progress callback
            result = await call_orchestrator(message, app_id, progress_callback=progress_callback)
            logger.info(f"Orchestrator result for assessment report: {result}")
            
            # Update: Processing (90%)
            operation.update_progress("Processing report results", 90, OperationStatus.IN_PROGRESS)
            await operation_service.update_operation(operation)
            
            # Parse result if it's a string
            report_result = {}
            if isinstance(result, str):
                try:
                    report_result = json.loads(result) if result else {}
                except json.JSONDecodeError:
                    report_result = {"raw_message": result}
            elif isinstance(result, dict):
                report_result = result
            else:
                report_result = {"result": str(result)}
            
            # Check if the report generation actually succeeded
            if report_result.get("result") == "error" or report_result.get("status") == "error":
                error_message = report_result.get("message", "Unknown report generation error occurred")
                logger.error(f"Report generation failed for application ID {app_id}: {error_message}")
                
                operation.fail_operation(error_message, {"result": report_result})
                await operation_service.update_operation(operation)
                
                bg_span.set_status(Status(StatusCode.ERROR, error_message[:256]))
                return
            
            # Update: Finalizing (95%)
            operation.update_progress("Finalizing report", 95, OperationStatus.IN_PROGRESS)
            await operation_service.update_operation(operation)
            
            # Prepare final result
            final_result = {
                "status": "success",
                "app_id": app_id,
                "report": report_result,
                "message": f"Assessment report generated successfully for application '{app_id}'"
            }
            
            # Mark operation as completed
            operation.complete_operation(final_result)
            await operation_service.update_operation(operation)
            
            # Record success metrics
            duration = (time.time() - start_time) * 1000
            add_span_attributes(bg_span, {
                "report.status": "success",
                "report.duration_ms": duration,
            })
            bg_span.set_status(Status(StatusCode.OK))
            
            logger.info(f"Background report generation completed successfully for operation {operation_id}")
            
        except Exception as ex:
            logger.error(f"Background report generation exception for operation {operation_id}: {str(ex)}")
            bg_span.record_exception(ex)
            bg_span.set_status(Status(StatusCode.ERROR, str(ex)[:256]))
            
            try:
                operation = await operation_service.get_operation(operation_id, app_id)
                if operation:
                    operation.fail_operation(str(ex), {"error_type": type(ex).__name__})
                    await operation_service.update_operation(operation)
            except Exception as update_ex:
                logger.error(f"Failed to update operation status after error: {update_ex}")
        
        finally:
            remove_task(operation_id)


async def generate_design_background(
    operation_id: str,
    app_id: str,
    storage_account_name: str,
    user_object_id: Optional[str] = None,
    group_object_id: Optional[str] = None,
    resource_group_name: Optional[str] = None
) -> None:
    """
    Background task to generate the architecture design and update operation status.
    
    Args:
        operation_id: Operation ID to track
        app_id: Application ID
        storage_account_name: Storage account for report storage
        user_object_id: User object ID for RBAC
        group_object_id: Group object ID for RBAC
        resource_group_name: Resource group name
    """
    operation_service = get_operation_service()
    tracer = get_tracer()
    start_time = time.time()
    
    with tracer.start_as_current_span("generate_design.background_task") as bg_span:
        add_span_attributes(bg_span, {
            "design.operation_id": operation_id,
            "design.app_id": app_id,
        })
        
        try:
            # Get operation record
            operation = await operation_service.get_operation(operation_id, app_id)
            if not operation:
                logger.error(f"Operation {operation_id} not found for background design generation")
                bg_span.set_status(Status(StatusCode.ERROR, "Operation not found"))
                return
            
            # Update: Starting (5%)
            operation.update_progress("Initializing design generation", 5, OperationStatus.IN_PROGRESS)
            await operation_service.update_operation(operation)
            
            # Update: Authentication validated (10%)
            operation.update_progress("Authentication validated", 10, OperationStatus.IN_PROGRESS)
            await operation_service.update_operation(operation)
            
            # Compose the message for the orchestrator agent
            message = f"Generate architecture design for application ID: {app_id} and storage account name: {storage_account_name}"
            logger.debug(f"Design generation orchestrator message: {message}")
            
            # Update: Starting design generation (15%)
            operation.update_progress("Starting design generation via orchestrator", 15, OperationStatus.IN_PROGRESS)
            await operation_service.update_operation(operation)
            
            # Create progress callback to forward design agent progress to operation status
            async def progress_callback(message: str, percentage: float):
                """Update operation progress during design generation"""
                try:
                    op = await operation_service.get_operation(operation_id, app_id)
                    if op:
                        op.update_progress(message, percentage, OperationStatus.IN_PROGRESS)
                        await operation_service.update_operation(op)
                        logger.debug(f"Design progress update: {percentage}% - {message}")
                except Exception as prog_ex:
                    logger.warning(f"Failed to update design progress: {prog_ex}")
            
            # Call the orchestrator agent asynchronously with progress callback
            result = await call_orchestrator(message, app_id, progress_callback=progress_callback)
            logger.info(f"Orchestrator result for design generation: {result}")
            
            # Update: Processing (90%)
            operation.update_progress("Processing design results", 90, OperationStatus.IN_PROGRESS)
            await operation_service.update_operation(operation)
            
            # Parse result if it's a string
            design_result = {}
            if isinstance(result, str):
                try:
                    design_result = json.loads(result) if result else {}
                except json.JSONDecodeError:
                    design_result = {"raw_message": result}
            elif isinstance(result, dict):
                design_result = result
            else:
                design_result = {"result": str(result)}
            
            # Check if the design generation actually succeeded
            if design_result.get("result") == "error" or design_result.get("status") == "error":
                error_message = design_result.get("message", "Unknown design generation error occurred")
                logger.error(f"Design generation failed for application ID {app_id}: {error_message}")
                
                operation.fail_operation(error_message, {"result": design_result})
                await operation_service.update_operation(operation)
                
                bg_span.set_status(Status(StatusCode.ERROR, error_message[:256]))
                return
            
            # Update: Finalizing (95%)
            operation.update_progress("Design generation completed", 95, OperationStatus.IN_PROGRESS)
            await operation_service.update_operation(operation)
            
            # Prepare final result
            final_result = {
                "status": "success",
                "app_id": app_id,
                "design_result": design_result,
                "message": f"Architecture design generated successfully for application '{app_id}'"
            }
            
            # Mark operation as completed
            operation.complete_operation(final_result)
            await operation_service.update_operation(operation)
            
            # Record success metrics
            duration = (time.time() - start_time) * 1000
            add_span_attributes(bg_span, {
                "design.status": "success",
                "design.duration_ms": duration,
            })
            bg_span.set_status(Status(StatusCode.OK))
            
            logger.info(f"Background design generation completed successfully for operation {operation_id}")
            
        except Exception as ex:
            logger.error(f"Background design generation exception for operation {operation_id}: {str(ex)}")
            bg_span.record_exception(ex)
            bg_span.set_status(Status(StatusCode.ERROR, str(ex)[:256]))
            
            try:
                operation = await operation_service.get_operation(operation_id, app_id)
                if operation:
                    operation.fail_operation(str(ex), {"error_type": type(ex).__name__})
                    await operation_service.update_operation(operation)
            except Exception as update_ex:
                logger.error(f"Failed to update operation status after error: {update_ex}")
        
        finally:
            remove_task(operation_id)


async def kubernetes_discovery_background(
    operation_id: str,
    app_id: str,
    storage_account_name: str,
    user_object_id: Optional[str] = None,
    group_object_id: Optional[str] = None,
    resource_group_name: Optional[str] = None
) -> None:
    """
    Background task to run Kubernetes discovery and update operation status.
    
    Args:
        operation_id: Operation ID to track
        app_id: Application ID
        storage_account_name: Storage account for report storage
        user_object_id: User object ID for RBAC
        group_object_id: Group object ID for RBAC
        resource_group_name: Resource group name
    """
    operation_service = get_operation_service()
    tracer = get_tracer()
    start_time = time.time()
    
    with tracer.start_as_current_span("kubernetes_discovery.background_task") as bg_span:
        add_span_attributes(bg_span, {
            "kubernetes.operation_id": operation_id,
            "kubernetes.app_id": app_id,
        })
        
        try:
            # Get operation record
            operation = await operation_service.get_operation(operation_id, app_id)
            if not operation:
                logger.error(f"Operation {operation_id} not found for background kubernetes discovery")
                bg_span.set_status(Status(StatusCode.ERROR, "Operation not found"))
                return
            
            # Update: Starting (5%)
            operation.update_progress("Initializing kubernetes discovery", 5, OperationStatus.IN_PROGRESS)
            await operation_service.update_operation(operation)
            
            # Update: Authentication validated (10%)
            operation.update_progress("Authentication validated", 10, OperationStatus.IN_PROGRESS)
            await operation_service.update_operation(operation)
            
            # Compose the message for the orchestrator agent
            message = f'Clone the kubernetes template table and Create cluster summary report of kubernetes discovery for application ID: "{app_id}"'
            logger.debug(f"Kubernetes discovery orchestrator message: {message}")
            
            # Update: Starting kubernetes discovery (15%)
            operation.update_progress("Starting kubernetes discovery via orchestrator", 15, OperationStatus.IN_PROGRESS)
            await operation_service.update_operation(operation)
            
            # Create progress callback to forward kubernetes discovery agent progress to operation status
            # Agent reports progress in 20-85% range during prompt processing
            async def progress_callback(message: str, percentage: float):
                """Update operation progress during kubernetes discovery (20-85% range)"""
                try:
                    op = await operation_service.get_operation(operation_id, app_id)
                    if op:
                        # Ensure percentage stays within expected 20-85% range
                        bounded_percentage = max(20, min(85, percentage))
                        op.update_progress(message, bounded_percentage, OperationStatus.IN_PROGRESS)
                        await operation_service.update_operation(op)
                        logger.debug(f"Kubernetes discovery progress update: {bounded_percentage}% - {message}")
                except Exception as prog_ex:
                    logger.warning(f"Failed to update kubernetes discovery progress: {prog_ex}")
            
            # Call the orchestrator agent asynchronously with progress callback
            result = await call_orchestrator(message, app_id, progress_callback=progress_callback)
            logger.info(f"Orchestrator result for kubernetes discovery: {result}")
            
            # Update: Processing (90%)
            operation.update_progress("Processing kubernetes discovery results", 90, OperationStatus.IN_PROGRESS)
            await operation_service.update_operation(operation)
            
            # Parse result if it's a string
            discovery_result = {}
            if isinstance(result, str):
                try:
                    discovery_result = json.loads(result) if result else {}
                except json.JSONDecodeError:
                    discovery_result = {"raw_message": result}
            elif isinstance(result, dict):
                discovery_result = result
            else:
                discovery_result = {"result": str(result)}
            
            # Extract the actual kubernetes discovery results from the orchestrator response
            # Orchestrator wraps it as: {"result": "ok", "kubernetes_discovery_results": {...}}
            kubernetes_data = discovery_result.get("kubernetes_discovery_results", discovery_result)
            
            # Update: Assigning permissions (95%)
            operation.update_progress("Assigning K8S table permissions", 95, OperationStatus.IN_PROGRESS)
            await operation_service.update_operation(operation)
            
            # Assign permissions to the K8S table that was just cloned
            # This is done separately because K8S table is not part of the standard template tables
            try:
                kubernetes_table_name = f"K8S{app_id}"
                logger.info(f"Assigning permissions to K8S table: {kubernetes_table_name}")
                
                rbac_helper = RBACHelper()
                
                # Assign permissions for user (if provided)
                if user_object_id:
                    logger.info(f"Assigning K8S table permissions for user: {user_object_id}")
                    user_assignment = rbac_helper.assign_table_permissions(
                        user_object_id=user_object_id,
                        storage_account_name=storage_account_name,
                        resource_group_name=resource_group_name,
                        role_name="Storage Table Data Contributor",
                        table_names=[kubernetes_table_name],
                        principal_type="User"
                    )
                    logger.info(f"User K8S table permission assignment result: {user_assignment}")
                
                # Assign permissions for group (if provided)
                if group_object_id:
                    logger.info(f"Assigning K8S table permissions for group: {group_object_id}")
                    group_assignment = rbac_helper.assign_table_permissions(
                        user_object_id=group_object_id,
                        storage_account_name=storage_account_name,
                        resource_group_name=resource_group_name,
                        role_name="Storage Table Data Contributor",
                        table_names=[kubernetes_table_name],
                        principal_type="Group"
                    )
                    logger.info(f"Group K8S table permission assignment result: {group_assignment}")
                    
            except Exception as perm_ex:
                # Log permission assignment errors but don't fail the entire operation
                logger.warning(f"Failed to assign permissions to K8S table, but continuing: {str(perm_ex)}")
            
            # Check if the kubernetes discovery actually succeeded
            if kubernetes_data.get("result") == "error" or kubernetes_data.get("status") == "error":
                error_message = kubernetes_data.get("message", "Unknown kubernetes discovery error occurred")
                logger.error(f"Kubernetes discovery failed for application ID {app_id}: {error_message}")
                
                operation.fail_operation(error_message, {"result": kubernetes_data})
                await operation_service.update_operation(operation)
                
                bg_span.set_status(Status(StatusCode.ERROR, error_message[:256]))
                return
            
            # Update: Finalizing (98%)
            operation.update_progress("Kubernetes discovery completed", 98, OperationStatus.IN_PROGRESS)
            await operation_service.update_operation(operation)
            
            # Extract agent_id from the kubernetes discovery results
            agent_id = kubernetes_data.get("agent_id")
            
            # Prepare final result
            final_result = {
                "status": "success",
                "app_id": app_id,
                "agent_id": agent_id,
                "kubernetes_data": kubernetes_data,
                "message": f"Kubernetes discovery completed successfully for application '{app_id}'"
            }
            
            # Mark operation as completed
            operation.complete_operation(final_result)
            await operation_service.update_operation(operation)
            
            # Record success metrics
            duration = (time.time() - start_time) * 1000
            add_span_attributes(bg_span, {
                "kubernetes.status": "success",
                "kubernetes.duration_ms": duration,
                "kubernetes.agent_id": agent_id,
            })
            bg_span.set_status(Status(StatusCode.OK))
            
            logger.info(f"Background kubernetes discovery completed successfully for operation {operation_id}")
            
        except Exception as ex:
            logger.error(f"Background kubernetes discovery exception for operation {operation_id}: {str(ex)}")
            bg_span.record_exception(ex)
            bg_span.set_status(Status(StatusCode.ERROR, str(ex)[:256]))
            
            try:
                operation = await operation_service.get_operation(operation_id, app_id)
                if operation:
                    operation.fail_operation(str(ex), {"error_type": type(ex).__name__})
                    await operation_service.update_operation(operation)
            except Exception as update_ex:
                logger.error(f"Failed to update operation status after error: {update_ex}")
        
        finally:
            remove_task(operation_id)

async def generate_app_planning_background(
    operation_id: str,
    app_id: str,
    storage_account_name: str,
    user_object_id: Optional[str] = None,
    group_object_id: Optional[str] = None,
    resource_group_name: Optional[str] = None
) -> None:
    """
    Background task to generate the app planning documentation and update operation status.
    
    Args:
        operation_id: Operation ID to track
        app_id: Application ID
        storage_account_name: Storage account for report storage
        user_object_id: User object ID for RBAC
        group_object_id: Group object ID for RBAC
        resource_group_name: Resource group name
    """
    operation_service = get_operation_service()
    tracer = get_tracer()
    start_time = time.time()
    
    with tracer.start_as_current_span("generate_app_planning.background_task") as bg_span:
        add_span_attributes(bg_span, {
            "planning.operation_id": operation_id,
            "planning.app_id": app_id,
        })
        
        try:
            # Get operation record
            operation = await operation_service.get_operation(operation_id, app_id)
            if not operation:
                logger.error(f"Operation {operation_id} not found for background planning generation")
                bg_span.set_status(Status(StatusCode.ERROR, "Operation not found"))
                return
            
            # Update: Starting (10%)
            operation.update_progress("Initializing app planning generation", 10, OperationStatus.IN_PROGRESS)
            await operation_service.update_operation(operation)
            
            # Update: Authentication validated (30%)
            operation.update_progress("Authentication validated", 30, OperationStatus.IN_PROGRESS)
            await operation_service.update_operation(operation)
            
            # Compose the message for the orchestrator agent
            message = f"Generate app planning for application ID: {app_id}"
            logger.debug(f"App planning generation orchestrator message: {message}")
            
            # Update: Starting planning generation (50%)
            operation.update_progress("Starting app planning generation via orchestrator", 50, OperationStatus.IN_PROGRESS)
            await operation_service.update_operation(operation)
            
            # Call the orchestrator agent asynchronously
            result = await call_orchestrator(message, app_id)
            logger.info(f"Orchestrator result for app planning generation: {result}")
            
            # Update: Processing (75%)
            operation.update_progress("Processing planning results", 75, OperationStatus.IN_PROGRESS)
            await operation_service.update_operation(operation)
            
            # Parse result if it's a string
            planning_result = {}
            if isinstance(result, str):
                try:
                    planning_result = json.loads(result) if result else {}
                except json.JSONDecodeError:
                    planning_result = {"raw_message": result}
            elif isinstance(result, dict):
                planning_result = result
            else:
                planning_result = {"result": str(result)}
            
            # Check if the planning generation actually succeeded
            if planning_result.get("result") == "error" or planning_result.get("status") == "error":
                error_message = planning_result.get("message", "Unknown planning generation error occurred")
                logger.error(f"App planning generation failed for application ID {app_id}: {error_message}")
                
                operation.fail_operation(error_message, {"result": planning_result})
                await operation_service.update_operation(operation)
                
                bg_span.set_status(Status(StatusCode.ERROR, error_message[:256]))
                return
            
            # Update: Finalizing (90%)
            operation.update_progress("App planning generation completed", 90, OperationStatus.IN_PROGRESS)
            await operation_service.update_operation(operation)
            
            # Prepare final result
            final_result = {
                "status": "success",
                "app_id": app_id,
                "planning_result": planning_result,
                "message": f"App planning documentation generated successfully for application '{app_id}'"
            }
            
            # Mark operation as completed
            operation.complete_operation(final_result)
            await operation_service.update_operation(operation)
            
            # Record success metrics
            duration = (time.time() - start_time) * 1000
            add_span_attributes(bg_span, {
                "planning.status": "success",
                "planning.duration_ms": duration,
            })
            bg_span.set_status(Status(StatusCode.OK))
            
            logger.info(f"Background app planning generation completed successfully for operation {operation_id}")
            
        except Exception as ex:
            logger.error(f"Background app planning generation exception for operation {operation_id}: {str(ex)}")
            bg_span.record_exception(ex)
            bg_span.set_status(Status(StatusCode.ERROR, str(ex)[:256]))
            
            try:
                operation = await operation_service.get_operation(operation_id, app_id)
                if operation:
                    operation.fail_operation(str(ex), {"error_type": type(ex).__name__})
                    await operation_service.update_operation(operation)
            except Exception as update_ex:
                logger.error(f"Failed to update operation status after error: {update_ex}")
        
        finally:
            remove_task(operation_id)
