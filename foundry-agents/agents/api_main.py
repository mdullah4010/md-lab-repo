# Add current directory to sys.path to enable agent imports
import os
import sys
from pathlib import Path

# Add Agents directory to path FIRST (highest priority)
agents_dir = os.path.dirname(os.path.abspath(__file__))
if agents_dir not in sys.path:
    sys.path.insert(0, agents_dir)

# Add parent directory to path
parent_dir = os.path.dirname(agents_dir)
if parent_dir not in sys.path:
    sys.path.insert(1, parent_dir)

# Add architecture_analyzer_agent directory to path (lower priority to avoid conflicts)
arch_agent_path = Path(__file__).parent / "architecture_analyzer_agent"
if str(arch_agent_path) not in sys.path:
    sys.path.append(str(arch_agent_path))

# Import FastAPI and required modules
from fastapi import FastAPI, HTTPException, Path as PathParam, Query, Request, Response, Body, Depends
from fastapi.responses import JSONResponse
from typing import Optional
# Import the orchestrator call function
from agents.orchestrator_agent import call_orchestrator, delete_all_app_data, orchplugin, get_confidence_scores
import asyncio
import json
import logging
import time
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Import logging configuration
from agents.logging_config import get_logger

# Import tracing configuration
from agents.tracing_config import (
    initialize_tracing,
    get_tracer,
    add_span_attributes,
    record_api_call,
    record_error_details
)
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

# Import models and RBAC helper
from agents.models import (
    CreateApplicationRequest, 
    CreateApplicationResponse,
    ApplicationOperationRequest,
    IndexDocumentsRequest,
    IndexDocumentsResponse,
    AnalysisResponse,
    AssessmentReportResponse,
    DeleteAppDataResponse,
    DesignResponse,
    PlanningResponse,
    KubernetesDiscoveryResponse,
    ArchitectureAnalysisRequest,
    ArchitectureAnalysisResponse,
    ArchitectureAnalysisResultResponse,
    # Operation tracking models
    OperationStatusRequest,
    OperationStatusResponse,
    OperationSummaryResponse,
    # Code analysis models
    CodeAnalysisRequest,
    CodeAnalysisResponse,
    CodeAnalysisResultResponse,
    SourceType
)
from agents.rbac_helper import RBACHelper, TEMPLATE_TABLES

# Import RBAC authentication functions
from agents.rbac_auth import user_authentication, validate_container_only_access

# Import operation tracking
from agents.operation_tracker import track_operation, OperationTracker
from agents.operation_models import OperationType, OperationStatus, OperationRecord
from agents.operation_service import get_operation_service

# Import index validation utility
from agents.utils import validate_index

# Import virtual directory setup utility
from agents.utils.common_utils import setup_virtual_directories_for_app

# Import background task functions for async operations
from agents.background_tasks import (
    run_analysis_background,
    generate_assessment_report_background,
    generate_design_background,
    kubernetes_discovery_background,
    generate_app_planning_background,
    track_task,
    remove_task
)

# Background task tracking for architecture analysis
# Stores running asyncio tasks to support future task management (cancel, list)
_running_architecture_tasks: dict = {}  # Dict[str, asyncio.Task]

# Note: CodeAnalyzerPlugin is called through orchestrator's analyze_code_repository kernel function

# Version info - using fallback since version module is deleted
# Semantic version - update this manually for releases
API_VERSION = "0.1.0"

# Build/deployment information - can be set via environment variables
BUILD_NUMBER = os.getenv("BUILD_NUMBER", "local")
GIT_COMMIT = os.getenv("GIT_COMMIT", "unknown")
DEPLOYMENT_TIME = os.getenv("DEPLOYMENT_TIME", datetime.utcnow().isoformat())

def get_version_info():
    """Get comprehensive version information."""
    return {
        "version": API_VERSION,
        "build": BUILD_NUMBER,
        "commit": GIT_COMMIT[:8] if GIT_COMMIT != "unknown" else "unknown",
        "deployed_at": DEPLOYMENT_TIME
    }


# Create logger for this module
logger = get_logger(__name__)

logger.info("API Main initialized")

# Log version information at startup
try:
    version_info = get_version_info()
    logger.info(f"Insights Agent API - Version: {version_info['version']}, Build: {version_info['build']}, Commit: {version_info['commit']}, Deployed: {version_info['deployed_at']}")
except Exception as e:
    logger.warning(f"Could not get version info: {e}")

# Initialize tracing
try:
    tracing_enabled = initialize_tracing()
    if tracing_enabled:
        logger.info("✅ OpenTelemetry tracing initialized successfully for API")
    else:
        logger.warning("⚠️ OpenTelemetry tracing initialization failed - metrics will not be collected")
except Exception as trace_ex:
    logger.error(f"❌ Failed to initialize tracing: {trace_ex}")
    tracing_enabled = False

# Initialize FastAPI app
app = FastAPI(
    title="AI Assessment, Design and Planning (ADP) API",
    description="API for managing application assessments with Azure AI Foundry",
    version=API_VERSION
)

# Add middleware for comprehensive telemetry
@app.middleware("http")
async def telemetry_middleware(request: Request, call_next):
    """
    Middleware to add comprehensive telemetry to all API requests.
    Tracks latency, request/response sizes, errors, and creates spans for each endpoint.
    """
    tracer = get_tracer()
    start_time = time.time()
    
    # Extract request metadata
    endpoint = request.url.path
    method = request.method
    
    # Extract application_id from URL path (path_params not available in middleware)
    # Pattern matches: /createApplicationId/{id}, /indexDocuments/{id}, /runAnalysis/{id}, etc.
    application_id = "unknown"
    path_parts = endpoint.strip('/').split('/')
    if len(path_parts) >= 2:
        # The application_id is typically the last segment in our API paths
        application_id = path_parts[-1]
    
    # Start span for this API call
    with tracer.start_as_current_span(
        f"API {method} {endpoint}",
        kind=trace.SpanKind.SERVER
    ) as span:
        try:
            # Add request attributes
            add_span_attributes(span, {
                "http.method": method,
                "http.route": endpoint,
                "http.scheme": request.url.scheme,
                "http.host": request.url.hostname,
                "application.id": application_id,
                "client.address": request.client.host if request.client else "unknown",
                "user_agent.original": request.headers.get("user-agent", "unknown"),
            })
            
            # Estimate request size
            request_size = 0
            if request.headers.get("content-length"):
                try:
                    request_size = int(request.headers.get("content-length"))
                    span.set_attribute("http.request.body.size", request_size)
                except ValueError:
                    pass
            
            # Record API call start
            span.add_event("request_started", {
                "endpoint": endpoint,
                "method": method,
                "application_id": application_id
            })
            
            # Process request
            response = await call_next(request)
            
            # Calculate latency
            latency_ms = (time.time() - start_time) * 1000
            
            # Get response size
            response_size = 0
            if "content-length" in response.headers:
                try:
                    response_size = int(response.headers["content-length"])
                except ValueError:
                    pass
            
            # Record API call metrics
            record_api_call(
                span=span,
                endpoint=endpoint,
                method=method,
                status_code=response.status_code,
                latency_ms=latency_ms,
                request_size=request_size,
                response_size=response_size
            )
            
            # Add response event
            span.add_event("request_completed", {
                "status_code": response.status_code,
                "latency_ms": f"{latency_ms:.2f}",
                "response_size": response_size
            })
            
            # Set span status based on HTTP status
            if 200 <= response.status_code < 400:
                span.set_status(Status(StatusCode.OK))
            elif 400 <= response.status_code < 500:
                span.set_status(Status(StatusCode.ERROR, f"Client error: {response.status_code}"))
            else:
                span.set_status(Status(StatusCode.ERROR, f"Server error: {response.status_code}"))
            
            # Log metrics
            logger.info(
                f"API Call: {method} {endpoint} | "
                f"Status: {response.status_code} | "
                f"Latency: {latency_ms:.2f}ms | "
                f"App: {application_id}"
            )
            
            return response
            
        except Exception as ex:
            # Calculate latency even for errors
            latency_ms = (time.time() - start_time) * 1000
            
            # Record error details
            record_error_details(
                span=span,
                error_type=type(ex).__name__,
                error_message=str(ex),
                is_retryable=isinstance(ex, (TimeoutError, ConnectionError))
            )
            
            # Record error event
            span.add_event("request_failed", {
                "error_type": type(ex).__name__,
                "error_message": str(ex)[:500],
                "latency_ms": f"{latency_ms:.2f}"
            })
            
            # Log error
            logger.error(
                f"API Error: {method} {endpoint} | "
                f"Error: {type(ex).__name__} | "
                f"Latency: {latency_ms:.2f}ms | "
                f"App: {application_id} | "
                f"Message: {str(ex)}"
            )
            
            # Re-raise the exception
            raise

# Health check endpoint for container health monitoring
@app.get("/health")
async def health_check():
    """
    Health check endpoint for Azure Container Apps health probes.
    Returns service status and basic connectivity information.
    """
    try:
        # Basic health check - verify essential environment variables are set
        required_env_vars = [
            "AZURE_EXISTING_AIPROJECT_ENDPOINT",
            "AZURE_STORAGE_ACCOUNT_URL", 
            "AZURE_TABLES_ACCOUNT_URL"
        ]

        missing_vars = [var for var in required_env_vars if not os.getenv(var)]

        if missing_vars:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "unhealthy",
                    "message": f"Missing required environment variables: {', '.join(missing_vars)}",
                    "timestamp": time.time()
                }
            )

        # Get version information
        version_info = get_version_info()
        logger.info(f"Insights Agent - Version: {version_info['version']}, Build: {version_info['build']}, Commit: {version_info['commit']}, Deployed: {version_info['deployed_at']}")

        # Get current logging level
        log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()

        return {
            "status": "healthy",
            "message": "AI Assessment, Design and Planning (ADP) API is running",
            "version": version_info["version"],
            "build": version_info["build"],
            "commit": version_info["commit"],
            "deployed_at": version_info["deployed_at"],
            "timestamp": time.time(),
            "tracing_enabled": tracing_enabled,
            "logging_level": log_level,
            "trace_level": "ENABLED" if tracing_enabled else "DISABLED"
        }
    except Exception as ex:
        logger.error(f"Health check failed: {str(ex)}")
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy", 
                "message": f"Health check failed, please check logs for internal error",
                "timestamp": time.time()
            }
        )

# Endpoint to create/register a new application ID with RBAC setup
# This will:
# 1. Validate the request parameters
# 2. Check if container exists
# 3. Check user permissions on the container
# 4. Assign Storage Blob Data Contributor role at container level if needed
# 5. Assign Storage Table Data Contributor role at storage account level if needed
# 6. Clone template tables if not present
@app.post("/createApplicationId", response_model=CreateApplicationResponse)
@track_operation(OperationType.CREATE_APPLICATION, total_steps=5)
async def create_application_id(request: CreateApplicationRequest = Body(...)):
    """
    Create a new application with proper RBAC permissions.
    
    Args:
        request: CreateApplicationRequest containing app_id, storage_account_name, 
                azure_region, user_object_id, and optional resource_group_name
    
    Returns:
        CreateApplicationResponse with operation status and details
        
    Raises:
        HTTPException: If validation fails or operation encounters an error
    """
    logger.info(f"API: Creating application ID: {request.app_id}")
    
    try:
        # Update progress - starting validation
        if hasattr(create_application_id, '_current_operation'):
            operation = create_application_id._current_operation
            operation.update_progress("Starting RBAC validation", 20, OperationStatus.IN_PROGRESS)
            operation_service = get_operation_service()
            await operation_service.update_operation(operation)
        
        # Use unified validation and setup
        validation_result = user_authentication(
            storage_account_name=request.storage_account_name,
            app_id=request.app_id,
            user_object_id=request.user_object_id,
            group_object_id=request.group_object_id,
            resource_group_name=request.resource_group_name,
            endpoint_type="create"
        )
        
        logger.info(f"Validation completed: {validation_result['actions_taken']}")
        
        # Update progress - validation completed
        if hasattr(create_application_id, '_current_operation'):
            operation = create_application_id._current_operation
            operation.update_progress("RBAC validation completed", 50, OperationStatus.IN_PROGRESS)
            await operation_service.update_operation(operation)
        
        # Extract container and table information from validation result
        container_result = validation_result["container"]
        tables_info = validation_result["tables"]
        
        # Prepare response data
        blob_permissions_result = container_result.get("permissions", {})
        cloned_table_names = [f"{table}{request.app_id}" for table in TEMPLATE_TABLES]
        
        # Handle table cloning if needed
        tables_result = {}
        cloned_permissions_result = {}
        
        if tables_info.get("status") == "exist_with_permissions":
            # Tables already exist with permissions
            tables_result = {
                "status": "already_exist",
                "message": f"All {len(tables_info.get('existing_tables', []))} tables already exist",
                "existing_tables": tables_info.get("existing_tables", [])
            }
            cloned_permissions_result = {
                "status": tables_info.get("permissions", {}).get("status", "already_assigned"),
                "successful": len(tables_info.get("existing_tables", [])),
                "failed": 0
            }
            
        elif tables_info.get("status") == "will_be_created":
            # Update progress - starting table cloning
            if hasattr(create_application_id, '_current_operation'):
                operation = create_application_id._current_operation
                operation.update_progress("Cloning template tables", 75, OperationStatus.IN_PROGRESS)
                await operation_service.update_operation(operation)
            
            # Clone tables using orchestrator
            logger.info(f"Cloning tables for application {request.app_id}")
            message = f"Clone the tables from the template container for application ID: {request.app_id}"
            orchestrator_result = await call_orchestrator(message, request.app_id)
            
            # Parse orchestrator result
            if isinstance(orchestrator_result, str):
                try:
                    tables_result = json.loads(orchestrator_result) if orchestrator_result else {}
                except json.JSONDecodeError:
                    tables_result = {"raw_message": orchestrator_result}
            elif isinstance(orchestrator_result, dict):
                tables_result = orchestrator_result
            else:
                tables_result = {"result": str(orchestrator_result)}
            
            logger.info(f"Table cloning result: {tables_result}")
            
            # Assign table permissions
            rbac_helper = RBACHelper()
            
            # Assign for user if provided
            if request.user_object_id:
                logger.info(f"Assigning table-level permissions for user on {len(cloned_table_names)} cloned tables")
                user_permissions_result = rbac_helper.assign_table_permissions(
                    user_object_id=request.user_object_id,
                    storage_account_name=request.storage_account_name,
                    resource_group_name=request.resource_group_name,
                    role_name="Storage Table Data Contributor",
                    table_names=cloned_table_names,
                    principal_type="User"
                )
            
            # Assign for group if provided
            if request.group_object_id:
                logger.info(f"Assigning table-level permissions for group on {len(cloned_table_names)} cloned tables")
                group_permissions_result = rbac_helper.assign_table_permissions(
                    user_object_id=request.group_object_id,
                    storage_account_name=request.storage_account_name,
                    resource_group_name=request.resource_group_name,
                    role_name="Storage Table Data Contributor",
                    table_names=cloned_table_names,
                    principal_type="Group"
                )
            
            # Combine results
            cloned_permissions_result = {
                "status": "completed",
                "user_permissions": user_permissions_result if request.user_object_id else None,
                "group_permissions": group_permissions_result if request.group_object_id else None,
                "successful": (user_permissions_result.get("successful", 0) if request.user_object_id else 0) + 
                             (group_permissions_result.get("successful", 0) if request.group_object_id else 0),
                "failed": (user_permissions_result.get("failed", 0) if request.user_object_id else 0) + 
                         (group_permissions_result.get("failed", 0) if request.group_object_id else 0)
            }
        
        # Determine blob permissions status
        blob_status = "already_assigned"

        # Setup virtual directories for all agent endpoints
        # This creates the folder structure needed by agents: design/input, design/output, asr/input, etc.
        if hasattr(create_application_id, '_current_operation'):
            operation = create_application_id._current_operation
            operation.update_progress("Setting up virtual directories", 90, OperationStatus.IN_PROGRESS)
            await operation_service.update_operation(operation)
        
        logger.info(f"Setting up virtual directories for application {request.app_id}")
        virtual_dirs_result = setup_virtual_directories_for_app(app_id=request.app_id)
        logger.info(f"Virtual directories setup result: {virtual_dirs_result['status']} - {virtual_dirs_result['successful']}/{virtual_dirs_result['total']} successful")
        
        
        # Prepare response
        response = CreateApplicationResponse(
            status="success",
            app_id=request.app_id,
            container={
                "status": container_result.get("status", "unknown"),
                "container_name": request.app_id,
                "storage_account": request.storage_account_name,
                "exists": True,
                "virtual_directories": {
                    "status": virtual_dirs_result.get("status", "unknown"),
                    "created": virtual_dirs_result.get("directories_created", []),
                    "existed": virtual_dirs_result.get("directories_existed", []),
                    "failed": virtual_dirs_result.get("directories_failed", []),
                    "total": virtual_dirs_result.get("total", 0),
                    "successful": virtual_dirs_result.get("successful", 0)
                }
                
            },
            permissions={
                "blob_permissions": {
                    "status": blob_status,
                    "role": "Storage Blob Data Contributor",
                    "scope": "container",
                    "details": blob_permissions_result
                },
                "table_permissions": {
                    "cloned_tables": {
                        "status": cloned_permissions_result.get("status", "unknown"),
                        "role": "Storage Table Data Contributor",
                        "scope": "table",
                        "tables": cloned_table_names,
                        "successful": cloned_permissions_result.get("successful", 0),
                        "failed": cloned_permissions_result.get("failed", 0),
                        "details": cloned_permissions_result
                    }
                }
            },
            tables=tables_result,
            message=f"Application '{request.app_id}' setup completed with table-level RBAC. "
                    f"Container: {container_result.get('status')}, "
                    f"Blob permissions: {blob_permissions_result.get('status')}, "
                    f"Tables: {tables_result.get('status', 'cloned')}, "
                    f"Table permissions: {cloned_permissions_result.get('successful', 0)}/{len(cloned_table_names)} successful, "
                    f"Virtual directories: {virtual_dirs_result.get('successful', 0)}/{virtual_dirs_result.get('total', 0)} ready. "
                    f"⚠️ RBAC changes may take up to 10 minutes to propagate."
        )
        
        logger.info(
            f"Successfully setup application ID: {request.app_id} with table-level permissions. "
            f"Tables status: {tables_result.get('status', 'cloned')}, "
            f"Permissions: {cloned_permissions_result.get('successful', 0)}/{len(cloned_table_names)}"
        )
        
        return response
            
    except ValueError as ex:
        # Validation errors
        error_msg = f"Validation error: {str(ex)}"
        logger.error(error_msg)
        raise HTTPException(status_code=400, detail=error_msg)
        
    except Exception as ex:
        # General errors
        error_msg = f"Error creating application ID {request.app_id}: {str(ex)}"
        logger.error(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)

# Endpoint to index documents for a given application ID
# This will directly call the trigger_and_check_indexing method
@app.post("/indexDocuments", response_model=IndexDocumentsResponse)
@track_operation(OperationType.INDEX_DOCUMENTS, total_steps=3)
async def index_documents(request: IndexDocumentsRequest = Body(...)):
    """
    Index documents for an application.
    
    Args:
        request: ApplicationOperationRequest containing app_id, storage_account_name,
                user_object_id, and optional resource_group_name
    
    Returns:
        IndexDocumentsResponse with operation status and details
        
    Raises:
        HTTPException: If authentication fails or operation encounters an error
    """
    logger.info(f"API: Indexing documents for application ID: {request.app_id}, folder_prefix: {request.folder_prefix}")
    try:
        # Check if app_id is 'central' - use container-only validation (skip table checks)
        if request.app_id.lower() == "central":
            logger.info("Detected 'central' container - using container-only validation (skipping table checks)")
            validation_result = validate_container_only_access(
                storage_account_name=request.storage_account_name,
                container_name="central",
                user_object_id=request.user_object_id,
                group_object_id=request.group_object_id,
                resource_group_name=request.resource_group_name
            )
        else:
            # Use unified validation - container must exist, tables not required for indexing
            validation_result = user_authentication(
                storage_account_name=request.storage_account_name,
                app_id=request.app_id,
                user_object_id=request.user_object_id,
                group_object_id=request.group_object_id,
                resource_group_name=request.resource_group_name,
                endpoint_type="operation"
            )
        logger.info(f"User validated for indexing: {validation_result['container']['status']}")
        
        # Update progress - validation completed
        if hasattr(index_documents, '_current_operation'):
            operation = index_documents._current_operation
            operation.update_progress("Authentication validated", 30, OperationStatus.IN_PROGRESS)
            operation_service = get_operation_service()
            await operation_service.update_operation(operation)
        
        # Create an instance of the orchestrator plugin
        plugin = orchplugin()
        
        # Update progress - starting indexing
        if hasattr(index_documents, '_current_operation'):
            operation = index_documents._current_operation
            operation.update_progress("Starting document indexing", 50, OperationStatus.IN_PROGRESS)
            await operation_service.update_operation(operation)
        
        # Call the trigger_and_check_indexing method directly
        # Pass folder_prefix if provided, otherwise pass type=None for full container indexing
        result_json = await plugin.trigger_and_check_indexing(
            application_id=request.app_id,
            folder_prefix=request.folder_prefix
        )
        
        # Parse the JSON string result
        result = json.loads(result_json)
        logger.info(f"Indexing result for application ID {request.app_id}: {result}")
        
        # Check if the indexing operation actually succeeded
        if result.get("result") == "error" or result.get("status") == "error":
            error_message = result.get("message", "Unknown indexing error occurred")
            logger.error(f"Indexing failed for application ID {request.app_id}: {error_message}")
            raise HTTPException(
                status_code=500,
                detail=f"Document indexing failed: {error_message}"
            )
        
        logger.info(f"Successfully indexed documents for application ID: {request.app_id}")
        
        # Update progress - indexing completed
        if hasattr(index_documents, '_current_operation'):
            operation = index_documents._current_operation
            operation.update_progress("Document indexing completed", 90, OperationStatus.IN_PROGRESS)
            await operation_service.update_operation(operation)
        
        # Return structured response
        return IndexDocumentsResponse(
            status="success",
            app_id=request.app_id,
            indexing_result=result,
            message=f"Documents indexed successfully for application '{request.app_id}'"
        )
    except HTTPException:
        # Re-raise HTTP exceptions (auth errors)
        raise
    except Exception as ex:
        logger.error(f"Error indexing documents for application ID {request.app_id}: {str(ex)}")
        raise HTTPException(status_code=500, detail=str(ex))

# Endpoint to run analysis for a given application ID
# This will trigger the orchestrator to run the full analysis pipeline
@app.post("/runAnalysis", response_model=AnalysisResponse, status_code=202)
async def run_analysis(request: ApplicationOperationRequest = Body(...)):
    """
    Run analysis for an application (async operation).
    
    This endpoint initiates an asynchronous analysis operation and returns immediately
    with an operation_id for tracking progress.
    
    Args:
        request: ApplicationOperationRequest containing app_id, storage_account_name,
                user_object_id, and optional resource_group_name
    
    Returns:
        AnalysisResponse (HTTP 202 Accepted) with operation_id for tracking
        
    Raises:
        HTTPException: If authentication fails or operation initialization encounters an error
    """
    logger.info(f"API: Running analysis for application ID: {request.app_id}")
    try:
        # STEP 1: RBAC validation - container and tables must exist
        validation_result = user_authentication(
            storage_account_name=request.storage_account_name,
            app_id=request.app_id,
            user_object_id=request.user_object_id,
            group_object_id=request.group_object_id,
            resource_group_name=request.resource_group_name,
            endpoint_type="operation"
        )
        logger.info(
            f"User validated for analysis: Container {validation_result['container']['status']}, "
            f"Tables {validation_result['tables'].get('status', 'unknown')}"
        )
        
        # STEP 2: Create operation record
        operation = OperationRecord(
            app_id=request.app_id,
            operation_type=OperationType.RUN_ANALYSIS,
            status=OperationStatus.PENDING,
            user_object_id=request.user_object_id,
            group_object_id=request.group_object_id,
            storage_account_name=request.storage_account_name,
            resource_group_name=request.resource_group_name,
            total_steps=5,
            current_step="Analysis queued"
        )
        
        operation_service = get_operation_service()
        operation_id = await operation_service.create_operation(operation)
        
        logger.info(f"Created operation {operation_id} for analysis of app {request.app_id}")
        
        # STEP 3: Start background task
        background_task = asyncio.create_task(
            run_analysis_background(
                operation_id=operation_id,
                app_id=request.app_id,
                storage_account_name=request.storage_account_name,
                user_object_id=request.user_object_id,
                group_object_id=request.group_object_id,
                resource_group_name=request.resource_group_name
            )
        )
        
        # Track the task for future management (cancel, list)
        track_task(operation_id, background_task)
        
        logger.info(f"Analysis started in background for operation {operation_id}")
        
        # STEP 4: Return immediately with operation_id (HTTP 202 Accepted)
        return AnalysisResponse(
            status="accepted",
            app_id=request.app_id,
            operation_id=operation_id,
            message=f"Analysis started in background. Use operation_id '{operation_id}' to track progress.",
            status_endpoint=f"/operations/{operation_id}/status?app_id={request.app_id}",
            result_endpoint=f"/operations/{operation_id}/result?app_id={request.app_id}"
        )
    except HTTPException:
        # Re-raise HTTP exceptions (auth errors)
        raise
    except Exception as ex:
        logger.error(f"Error initiating analysis for application ID {request.app_id}: {str(ex)}")
        raise HTTPException(status_code=500, detail=str(ex))

# Endpoint to generate an assessment report for a given application ID
# This will trigger the orchestrator to start the report generation process
@app.post("/generateAssessmentReport", response_model=AssessmentReportResponse, status_code=202)
async def generate_assessment_report(request: ApplicationOperationRequest = Body(...)):
    """
    Generate assessment report for an application (async operation).
    
    This endpoint initiates an asynchronous report generation operation and returns 
    immediately with an operation_id for tracking progress.
    
    Args:
        request: ApplicationOperationRequest containing app_id, storage_account_name,
                user_object_id, and optional resource_group_name
    
    Returns:
        AssessmentReportResponse (HTTP 202 Accepted) with operation_id for tracking
        
    Raises:
        HTTPException: If authentication fails or operation initialization encounters an error
    """
    logger.info(f"API: Generating assessment report for application ID: {request.app_id}")
    try:
        # STEP 1: RBAC validation - container and tables must exist
        validation_result = user_authentication(
            storage_account_name=request.storage_account_name,
            app_id=request.app_id,
            user_object_id=request.user_object_id,
            group_object_id=request.group_object_id,
            resource_group_name=request.resource_group_name,
            endpoint_type="operation"
        )
        logger.info(
            f"User validated for report generation: Container {validation_result['container']['status']}, "
            f"Tables {validation_result['tables'].get('status', 'unknown')}"
        )
        
        # STEP 2: Create operation record
        operation = OperationRecord(
            app_id=request.app_id,
            operation_type=OperationType.GENERATE_REPORT,
            status=OperationStatus.PENDING,
            user_object_id=request.user_object_id,
            group_object_id=request.group_object_id,
            storage_account_name=request.storage_account_name,
            resource_group_name=request.resource_group_name,
            total_steps=3,
            current_step="Report generation queued"
        )
        
        operation_service = get_operation_service()
        operation_id = await operation_service.create_operation(operation)
        
        logger.info(f"Created operation {operation_id} for report generation of app {request.app_id}")
        
        # STEP 3: Start background task
        background_task = asyncio.create_task(
            generate_assessment_report_background(
                operation_id=operation_id,
                app_id=request.app_id,
                storage_account_name=request.storage_account_name,
                user_object_id=request.user_object_id,
                group_object_id=request.group_object_id,
                resource_group_name=request.resource_group_name
            )
        )
        
        # Track the task for future management (cancel, list)
        track_task(operation_id, background_task)
        
        logger.info(f"Report generation started in background for operation {operation_id}")
        
        # STEP 4: Return immediately with operation_id (HTTP 202 Accepted)
        return AssessmentReportResponse(
            status="accepted",
            app_id=request.app_id,
            operation_id=operation_id,
            message=f"Assessment report generation started in background. Use operation_id '{operation_id}' to track progress.",
            status_endpoint=f"/operations/{operation_id}/status?app_id={request.app_id}",
            result_endpoint=f"/operations/{operation_id}/result?app_id={request.app_id}"
        )
    except HTTPException:
        # Re-raise HTTP exceptions (auth errors)
        raise
    except Exception as ex:
        logger.error(f"Error initiating report generation for application ID {request.app_id}: {str(ex)}")
        raise HTTPException(status_code=500, detail=str(ex))

# Endpoint to generate architecture design for a given application ID
# This will trigger the orchestrator to re-index and then invoke the design agent
@app.post("/generateDesign", response_model=DesignResponse, status_code=202)
async def generate_design(request: ApplicationOperationRequest = Body(...)):
    """
    Generate architecture design for an application (async operation).
    
    This endpoint initiates an asynchronous design generation operation and returns 
    immediately with an operation_id for tracking progress.
    
    Args:
        request: ApplicationOperationRequest containing app_id, storage_account_name,
                user_object_id, and optional resource_group_name
    
    Returns:
        DesignResponse (HTTP 202 Accepted) with operation_id for tracking
        
    Raises:
        HTTPException: If authentication fails or operation initialization encounters an error
    """
    logger.info(f"API: Generating architecture design for application ID: {request.app_id}")
    try:
        # STEP 1: RBAC validation - container and tables must exist
        validation_result = user_authentication(
            storage_account_name=request.storage_account_name,
            app_id=request.app_id,
            user_object_id=request.user_object_id,
            group_object_id=request.group_object_id,
            resource_group_name=request.resource_group_name,
            endpoint_type="operation"
        )
        logger.info(
            f"User validated for design generation: Container {validation_result['container']['status']}, "
            f"Tables {validation_result['tables'].get('status', 'unknown')}"
        )
        
        # STEP 2: Create operation record
        operation = OperationRecord(
            app_id=request.app_id,
            operation_type=OperationType.GENERATE_DESIGN,
            status=OperationStatus.PENDING,
            user_object_id=request.user_object_id,
            group_object_id=request.group_object_id,
            storage_account_name=request.storage_account_name,
            resource_group_name=request.resource_group_name,
            total_steps=3,
            current_step="Design generation queued"
        )
        
        operation_service = get_operation_service()
        operation_id = await operation_service.create_operation(operation)
        
        logger.info(f"Created operation {operation_id} for design generation of app {request.app_id}")
        
        # STEP 3: Start background task
        background_task = asyncio.create_task(
            generate_design_background(
                operation_id=operation_id,
                app_id=request.app_id,
                storage_account_name=request.storage_account_name,
                user_object_id=request.user_object_id,
                group_object_id=request.group_object_id,
                resource_group_name=request.resource_group_name
            )
        )
        
        # Track the task for future management (cancel, list)
        track_task(operation_id, background_task)
        
        logger.info(f"Design generation started in background for operation {operation_id}")
        
        # STEP 4: Return immediately with operation_id (HTTP 202 Accepted)
        return DesignResponse(
            status="accepted",
            app_id=request.app_id,
            operation_id=operation_id,
            message=f"Architecture design generation started in background. Use operation_id '{operation_id}' to track progress.",
            status_endpoint=f"/operations/{operation_id}/status?app_id={request.app_id}",
            result_endpoint=f"/operations/{operation_id}/result?app_id={request.app_id}"
        )
    except HTTPException:
        # Re-raise HTTP exceptions (auth errors)
        raise
    except Exception as ex:
        logger.error(f"Error initiating design generation for application ID {request.app_id}: {str(ex)}")
        raise HTTPException(status_code=500, detail=str(ex))

# Endpoint to generate app planning documentation for a given application ID
# This will trigger the orchestrator to index app-planning/input/ and then invoke the planning agent
@app.post("/generateAppPlan", response_model=PlanningResponse, status_code=202)
async def generate_app_plan(request: ApplicationOperationRequest = Body(...)):
    """
    Generate comprehensive app planning documentation for an application (async operation).
    
    This endpoint initiates an asynchronous planning generation operation and returns 
    immediately with an operation_id for tracking progress.
    
    The planning agent generates migration planning documentation including:
    - Detailed Azure service configurations
    - Migration scope and DoD criteria
    - Task breakdowns for Azure DevOps
    - Environment-wise migration plans
    - Data migration strategy
    - Security and compliance planning
    - Testing and validation plans
    - Cutover plans and runbooks
    
    Args:
        request: ApplicationOperationRequest containing app_id, storage_account_name,
                user_object_id, and optional resource_group_name
    
    Returns:
        PlanningResponse (HTTP 202 Accepted) with operation_id for tracking
        
    Raises:
        HTTPException: If authentication fails or operation initialization encounters an error
    """
    logger.info(f"API: Generating app planning documentation for application ID: {request.app_id}")
    try:
        # STEP 1: RBAC validation - container and tables must exist
        validation_result = user_authentication(
            storage_account_name=request.storage_account_name,
            app_id=request.app_id,
            user_object_id=request.user_object_id,
            group_object_id=request.group_object_id,
            resource_group_name=request.resource_group_name,
            endpoint_type="operation"
        )
        logger.info(
            f"User validated for app planning generation: Container {validation_result['container']['status']}, "
            f"Tables {validation_result['tables'].get('status', 'unknown')}"
        )
        
        # STEP 2: Create operation record
        operation = OperationRecord(
            app_id=request.app_id,
            operation_type=OperationType.GENERATE_PLANNING,
            status=OperationStatus.PENDING,
            user_object_id=request.user_object_id,
            group_object_id=request.group_object_id,
            storage_account_name=request.storage_account_name,
            resource_group_name=request.resource_group_name,
            total_steps=3,
            current_step="App planning generation queued"
        )
        
        operation_service = get_operation_service()
        operation_id = await operation_service.create_operation(operation)
        
        logger.info(f"Created operation {operation_id} for app planning generation of app {request.app_id}")
        
        # STEP 3: Start background task
        background_task = asyncio.create_task(
            generate_app_planning_background(
                operation_id=operation_id,
                app_id=request.app_id,
                storage_account_name=request.storage_account_name,
                user_object_id=request.user_object_id,
                group_object_id=request.group_object_id,
                resource_group_name=request.resource_group_name
            )
        )
        
        # Track the task for future management (cancel, list)
        track_task(operation_id, background_task)
        
        logger.info(f"App planning generation started in background for operation {operation_id}")
        
        # STEP 4: Return immediately with operation_id (HTTP 202 Accepted)
        return PlanningResponse(
            status="accepted",
            app_id=request.app_id,
            operation_id=operation_id,
            message=f"App planning generation started in background. Use operation_id '{operation_id}' to track progress.",
            status_endpoint=f"/operations/{operation_id}/status?app_id={request.app_id}",
            result_endpoint=f"/operations/{operation_id}/result?app_id={request.app_id}"
        )
    except HTTPException:
        # Re-raise HTTP exceptions (auth errors)
        raise
    except Exception as ex:
        logger.error(f"Error initiating app planning generation for application ID {request.app_id}: {str(ex)}")
        raise HTTPException(status_code=500, detail=str(ex))


# Endpoint to generate architecture kubernetes discovery for a given application ID
# This will trigger the orchestrator to re-index and then invoke the kubernetes discovery agent
@app.post("/discoverKubernetes", response_model=KubernetesDiscoveryResponse, status_code=202)
async def kubernetes_discovery(request: ApplicationOperationRequest = Body(...)):
    """
    Initialize Kubernetes Discovery Agent for a given cluster/application (async operation).
    
    This endpoint initiates an asynchronous Kubernetes discovery operation and returns 
    immediately with an operation_id for tracking progress.
    
    Args:
        request: ApplicationOperationRequest containing the app_id
        
    Returns:
        KubernetesDiscoveryResponse (HTTP 202 Accepted) with operation_id for tracking
        
    Raises:
        HTTPException: If authentication fails or operation initialization encounters an error
    """
    logger.info(f"API: Initializing Kubernetes discovery for application ID: {request.app_id}")
    try:
        # STEP 1: RBAC validation - container and tables must exist
        validation_result = user_authentication(
            storage_account_name=request.storage_account_name,
            app_id=request.app_id,
            user_object_id=request.user_object_id,
            group_object_id=request.group_object_id,
            resource_group_name=request.resource_group_name,
            endpoint_type="operation"
        )
        logger.info(
            f"User validated for kubernetes discovery: Container {validation_result['container']['status']}, "
            f"Tables {validation_result['tables'].get('status', 'unknown')}"
        )
        
        # STEP 2: Create operation record
        operation = OperationRecord(
            app_id=request.app_id,
            operation_type=OperationType.KUBERNETES_DISCOVERY,
            status=OperationStatus.PENDING,
            user_object_id=request.user_object_id,
            group_object_id=request.group_object_id,
            storage_account_name=request.storage_account_name,
            resource_group_name=request.resource_group_name,
            total_steps=3,
            current_step="Kubernetes discovery queued"
        )
        
        operation_service = get_operation_service()
        operation_id = await operation_service.create_operation(operation)
        
        logger.info(f"Created operation {operation_id} for kubernetes discovery of app {request.app_id}")
        
        # STEP 3: Start background task
        background_task = asyncio.create_task(
            kubernetes_discovery_background(
                operation_id=operation_id,
                app_id=request.app_id,
                storage_account_name=request.storage_account_name,
                user_object_id=request.user_object_id,
                group_object_id=request.group_object_id,
                resource_group_name=request.resource_group_name
            )
        )
        
        # Track the task for future management (cancel, list)
        track_task(operation_id, background_task)
        
        logger.info(f"Kubernetes discovery started in background for operation {operation_id}")
        
        # STEP 4: Return immediately with operation_id (HTTP 202 Accepted)
        return KubernetesDiscoveryResponse(
            status="accepted",
            app_id=request.app_id,
            operation_id=operation_id,
            message=f"Kubernetes discovery started in background. Use operation_id '{operation_id}' to track progress.",
            status_endpoint=f"/operations/{operation_id}/status?app_id={request.app_id}",
            result_endpoint=f"/operations/{operation_id}/result?app_id={request.app_id}"
        )
    except HTTPException:
        # Re-raise HTTP exceptions (auth errors)
        raise
    except Exception as ex:
        logger.error(f"Error initiating kubernetes discovery for application ID {request.app_id}: {str(ex)}")
        raise HTTPException(status_code=500, detail=str(ex))

# Endpoint to delete all app data (agents, threads, storage, and search index) for a given application ID
@app.post("/deleteAppData", response_model=DeleteAppDataResponse)
@track_operation(OperationType.DELETE_APP_DATA, total_steps=4)
async def delete_app_data(request: ApplicationOperationRequest = Body(...)):
    """
    Delete all application data including agents, threads, storage container, and search index.
    
    This endpoint performs a comprehensive cleanup of all resources associated with an application:
    - Deletes all agents (orchestrator, ASR, design, responder, architecture, security, diagram)
    - Deletes all threads associated with the agents
    - Deletes the storage container for the application [This is not getting deleted currently]
    - Deletes the search index for the application
    
    Args:
        request: ApplicationOperationRequest containing app_id, storage_account_name,
                user_object_id, and optional resource_group_name
    
    Returns:
        DeleteAppDataResponse with operation status and deletion details
        
    Raises:
        HTTPException: If authentication fails or operation encounters an error
    """
    logger.info(f"API: Deleting all app data for application ID: {request.app_id}")
    try:
        # Use unified validation - container must exist (tables may or may not be needed)
        validation_result = user_authentication(
            storage_account_name=request.storage_account_name,
            app_id=request.app_id,
            user_object_id=request.user_object_id,
            group_object_id=request.group_object_id,
            resource_group_name=request.resource_group_name,
            endpoint_type="operation"
        )
        logger.info(f"User validated for delete app data: {validation_result['container']['status']}")
        
        result = await delete_all_app_data(request.app_id)
        logger.info(f"Delete app data result: {result}")
        
        # Parse result if it's a string
        deletion_result = {}
        if isinstance(result, str):
            try:
                deletion_result = json.loads(result) if result else {}
            except json.JSONDecodeError:
                deletion_result = {"raw_message": result}
        elif isinstance(result, dict):
            deletion_result = result
        else:
            deletion_result = {"result": str(result)}
        
        # Check if the deletion operation actually succeeded
        if deletion_result.get("result") == "error" or deletion_result.get("status") == "error":
            error_message = deletion_result.get("message", "Unknown deletion error occurred")
            logger.error(f"Delete app data failed for application ID {request.app_id}: {error_message}")
            raise HTTPException(
                status_code=500,
                detail=f"Delete app data failed: {error_message}"
            )
        
        logger.info(f"Successfully deleted all app data for application ID: {request.app_id}")
        
        # Return structured response
        return DeleteAppDataResponse(
            status=deletion_result.get("status", "success"),
            app_id=request.app_id,
            deletion_result=deletion_result,
            message=f"All app data deleted successfully for application '{request.app_id}'"
        )
    except HTTPException:
        # Re-raise HTTP exceptions (auth errors)
        raise
    except Exception as ex:
        logger.error(f"Error deleting app data for application ID {request.app_id}: {str(ex)}")
        raise HTTPException(status_code=500, detail=str(ex))

# Endpoint to analyze architecture security from Design-doc in input folder
@app.post("/analyzeArchitecture", response_model=ArchitectureAnalysisResponse, status_code=202)
async def analyze_architecture(request: ArchitectureAnalysisRequest = Body(...)):
    """
    Analyze architecture security from design documents in input folder (async operation).
    
    This endpoint initiates an asynchronous architecture security analysis using dynamic mode.
    Design documents are automatically discovered from [app-id]/architecture-analyzer/input/ folder.
    The agent auto-discovers all architecture diagrams from the design documents.
    Returns immediately with an operation_id for tracking progress.
    
    Args:
        request: ArchitectureAnalysisRequest containing app_id, storage info, and RBAC credentials
    
    Returns:
        ArchitectureAnalysisResponse (HTTP 202 Accepted) with operation_id for tracking
        
    Raises:
        HTTPException: If authentication fails, no design documents found, SCF index validation fails, or operation initialization encounters an error
    """
    tracer = get_tracer()
    
    with tracer.start_as_current_span("api.analyze_architecture") as span:
        add_span_attributes(span, {
            "api.endpoint": "/analyzeArchitecture",
            "api.method": "POST",
            "architecture_analysis.app_id": request.app_id
        })
        
        logger.info(f"API: Analyzing architecture security for app ID: {request.app_id}")
        
        try:
            # STEP 0: Discover design documents from input folder
            # Files must start with 'design-' prefix (e.g., 'design-doc.md', 'design-architecture.docx')
            from utils.common_utils import list_files_in_folder_async
            
            input_folder = "architecture-analyzer/input"
            supported_extensions = ['.md', '.docx', '.pdf', '.doc', '.pptx', '.ppt']
            filename_prefix = "design-"
            
            logger.info(f"Discovering design documents from {request.app_id}/{input_folder} with prefix '{filename_prefix}'")
            design_files = await list_files_in_folder_async(
                app_id=request.app_id,
                folder_prefix=input_folder,
                file_extensions=supported_extensions,
                filename_prefix=filename_prefix,
                exclude_placeholder=True
            )
            
            if not design_files:
                logger.error(f"No design documents found in {request.app_id}/{input_folder} with prefix '{filename_prefix}'")
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "No design documents found",
                        "message": f"No design documents found in {request.app_id}/{input_folder}. "
                                   f"Please upload design documents with filename starting with 'design-' (e.g., 'design-doc.md', 'design-architecture.docx') to the input folder.",
                        "input_folder": f"{request.app_id}/{input_folder}",
                        "filename_prefix": filename_prefix,
                        "supported_extensions": supported_extensions
                    }
                )
            
            # Use the first design document found (or could be extended to process multiple)
            design_doc_info = design_files[0]
            design_doc_url = design_doc_info['url']  # Use full blob URL for architecture analyzer
            
            logger.info(f"Found {len(design_files)} design document(s), using: {design_doc_info['name']} from {design_doc_info['path']}")
            add_span_attributes(span, {
                "architecture_analysis.design_doc_url": design_doc_url,
                "architecture_analysis.design_doc_name": design_doc_info['name'],
                "architecture_analysis.design_files_count": len(design_files)
            })
            
            # STEP 1: RBAC validation
            validation_result = user_authentication(
                storage_account_name=request.storage_account_name,
                app_id=request.app_id,
                user_object_id=request.user_object_id,
                group_object_id=request.group_object_id,
                resource_group_name=request.resource_group_name,
                endpoint_type="operation"
            )
            logger.info(f"User validated for architecture analysis: {validation_result['container']['status']}")
            
            add_span_attributes(span, {
                "architecture_analysis.rbac_validation": "passed"
            })
            
            # STEP 1.5: SCF Index validation - ensure SCF index is created and has indexed values
            # This validation must pass before proceeding with architecture analysis
            try:
                scf_index_name = os.getenv("SCF_AZURE_SEARCH_INDEX") or os.getenv("SEARCH_INDEX_NAME")
                scf_validation = validate_index(
                    index_name=scf_index_name,
                    require_documents=True,
                    index_display_name="SCF"
                )
                
                if not scf_validation.is_valid:
                    logger.error(f"SCF index validation failed for architecture analysis: {scf_validation.error_message}")
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "error": "SCF index validation failed",
                            "message": scf_validation.error_message or "SCF index is not complete. Please ensure the SCF index (SCF_AZURE_SEARCH_INDEX) is created and populated before running architecture analysis.",
                            "validation_type": "scf_index",
                            "index_name": scf_validation.index_name,
                            "suggestion": "Please ensure SCF data is properly indexed in Azure AI Search before running architecture analysis."
                        }
                    )
                
                logger.info(f"SCF index validation passed for architecture analysis: {scf_validation.document_count} documents in SCF index '{scf_validation.index_name}'")
                
            except HTTPException:
                raise  # Re-raise HTTP exceptions as-is
            except Exception as validation_ex:
                logger.error(f"SCF index validation error during architecture analysis: {validation_ex}")
                raise HTTPException(
                    status_code=500,
                    detail={
                        "error": "SCF index validation error",
                        "message": f"Failed to validate SCF index: {str(validation_ex)}",
                        "validation_type": "scf_index"
                    }
                )
            
            # STEP 2: Create operation record manually (not using @track_operation decorator for async)
            operation = OperationRecord(
                app_id=request.app_id,
                operation_type=OperationType.ARCHITECTURE_ANALYSIS,
                user_object_id=request.user_object_id,
                group_object_id=request.group_object_id,
                storage_account_name=request.storage_account_name,
                resource_group_name=request.resource_group_name,
                design_doc_url=design_doc_url,  # Store discovered design doc URL in operation
                total_steps=4,
                current_step="Initializing architecture security analysis from input folder"
            )
            
            operation_service = get_operation_service()
            operation_id = await operation_service.create_operation(operation)
            
            add_span_attributes(span, {"architecture_analysis.operation_id": operation_id})
            span.add_event("operation_created", {"operation_id": operation_id})
            
            logger.info(f"Created operation {operation_id} for architecture analysis of {design_doc_url}")
            
            # STEP 3: Start background task (which will call orchestrator)
            asyncio.create_task(_run_architecture_analysis_background(
                app_id=request.app_id,
                operation=operation,
                design_doc_url=design_doc_url
            ))
            
            logger.info(f"Created operation {operation_id} for architecture analysis, started in background")
            
            span.set_status(Status(StatusCode.OK))
        
            # STEP 4: Return immediately with operation_id (HTTP 202 Accepted)
            return ArchitectureAnalysisResponse(
                status="accepted",
                app_id=request.app_id,
                operation_id=operation_id,
                design_doc_url=design_doc_url,
                message=f"Architecture security analysis started. Design document: {design_doc_info['name']}. Use operation_id to check status and retrieve results.",
                status_endpoint=f"/operations/{operation_id}/status?app_id={request.app_id}",
                result_endpoint=f"/operations/{operation_id}/status?app_id={request.app_id}&include_results=true"
            )
            
            
        except HTTPException:
            raise
        except Exception as ex:
            error_msg = f"Failed to start architecture analysis: {str(ex)}"
            logger.error(error_msg)
            
            span.record_exception(ex)
            span.set_status(Status(StatusCode.ERROR, str(ex)[:256]))
            
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "Internal server error",
                    "message": error_msg,
                    "app_id": request.app_id,
                    "input_folder": f"{request.app_id}/architecture-analyzer/input"
                }
            )

# New Operation Status Endpoints

# Dependency to validate query parameters for /operations/status
async def validate_operations_status_params(
    request: Request,
    app_id: str = Query(...),
    operation_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(1),
    offset: int = Query(0)
):
    """Validate that only allowed query parameters are present."""
    allowed_params = {"app_id", "operation_type", "status", "limit", "offset"}
    query_params = set(request.query_params.keys())
    extra_params = query_params - allowed_params
    
    if extra_params:
        raise HTTPException(
            status_code=400,
            detail=f"Unexpected query parameter(s): {', '.join(extra_params)}. "
                   f"Allowed parameters are: {', '.join(sorted(allowed_params))}"
        )
    
    return {
        "app_id": app_id,
        "operation_type": operation_type,
        "status": status,
        "limit": limit,
        "offset": offset
    }

@app.get("/operations/status", response_model=OperationStatusResponse)
async def get_operations_status(
    request: Request,
    params: dict = Depends(validate_operations_status_params)
):
    """
    Get the status of operations across all endpoints.
    
    Query parameters:
        app_id: Filter by application ID
        operation_type: Filter by operation type (create_application, index_documents, run_analysis, generate_report, delete_app_data)
        status: Filter by status (pending, in_progress, completed, failed, cancelled)
        limit: Maximum number of results (1-100, default: 1)
        offset: Results offset for pagination (default: 0)
    
    Headers (required for RBAC when app_id provided):
        X-User-Object-Id: User object ID for RBAC validation (optional if X-Group-Object-Id provided)
        X-Group-Object-Id: Group object ID for RBAC validation (optional if X-User-Object-Id provided)
        X-Storage-Account: Storage account name for RBAC validation
        X-Resource-Group: Resource group name (optional)
    
    Returns:
        OperationStatusResponse with matching operations and pagination info
        
    Raises:
        HTTPException: If query parameters are invalid or operation fails
    """
    # Extract parameters from dependency
    app_id = params["app_id"]
    operation_type = params["operation_type"]
    status = params["status"]
    limit = params["limit"]
    offset = params["offset"]
    
    # logger.info(f"API: Getting operation status - app_id: {app_id}")
    
    try:
        # Extract RBAC parameters from headers
        user_object_id = request.headers.get("X-User-Object-Id")
        group_object_id = request.headers.get("X-Group-Object-Id")
        storage_account_name = request.headers.get("X-Storage-Account")
        resource_group_name = request.headers.get("X-Resource-Group")
        
        # RBAC validation - check read access to OperationStatus table
        if not user_object_id and not group_object_id:
            raise HTTPException(
                status_code=400,
                detail="At least one of X-User-Object-Id or X-Group-Object-Id header is required"
            )
        if not storage_account_name:
            raise HTTPException(
                status_code=400,
                detail="X-Storage-Account header is required"
            )
        
        # Validate container access for the application
        validation_result = validate_container_only_access(
            storage_account_name=storage_account_name,
            container_name=app_id,
            user_object_id=user_object_id,
            group_object_id=group_object_id,
            resource_group_name=resource_group_name
        )
        
        # Validate and convert operation_type
        op_type = None
        if operation_type:
            try:
                op_type = OperationType(operation_type)
            except ValueError:
                raise HTTPException(
                    status_code=400, 
                    detail=f"Invalid operation_type. Must be one of: {', '.join([t.value for t in OperationType])}"
                )
        
        # Validate and convert status
        op_status = None
        if status:
            try:
                op_status = OperationStatus(status)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid status. Must be one of: {', '.join([s.value for s in OperationStatus])}"
                )
        
        # Validate limit
        if limit < 1 or limit > 100:
            raise HTTPException(status_code=400, detail="Limit must be between 1 and 100")
        
        # Validate offset
        if offset < 0:
            raise HTTPException(status_code=400, detail="Offset must be non-negative")
        
        # Create request object
        request_obj = OperationStatusRequest(
            app_id=app_id,
            user_object_id=user_object_id,
            operation_type=op_type,
            status=op_status,
            limit=limit,
            offset=offset
        )
        
        # Get operation service and query operations
        operation_service = get_operation_service()
        response = await operation_service.list_operations(request_obj)
        
        #logger.info(f"Retrieved {len(response.operations)} operations, total: {response.total_count}")
        return response
        
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as ex:
        logger.error(f"Error getting operation status: {str(ex)}")
        raise HTTPException(status_code=500, detail=str(ex))

@app.get("/operations/summary", response_model=OperationSummaryResponse)
async def get_operations_summary(
    request: Request,
    app_id: str = Query(..., description="Application ID (required for RBAC validation)"),
    days: int = 7
):
    """
    Get summary statistics for operations.
    
    Query parameters:
        app_id: Application ID (required for RBAC validation)
        days: Number of days to include in summary (default: 7, max: 365)
    
    Headers (required for RBAC):
        X-User-Object-Id: User object ID for RBAC validation (optional if X-Group-Object-Id provided)
        X-Group-Object-Id: Group object ID for RBAC validation (optional if X-User-Object-Id provided)
        X-Storage-Account: Storage account name for RBAC validation
        X-Resource-Group: Resource group name (optional)
    
    Returns:
        OperationSummaryResponse with statistics and recent operations
        
    Raises:
        HTTPException: If query parameters are invalid or operation fails
    """
    logger.info(f"API: Getting operation summary - app_id: {app_id}, days: {days}")
    
    try:
        # Extract RBAC parameters from headers
        user_object_id = request.headers.get("X-User-Object-Id")
        group_object_id = request.headers.get("X-Group-Object-Id")
        storage_account_name = request.headers.get("X-Storage-Account")
        resource_group_name = request.headers.get("X-Resource-Group")
        
        # RBAC validation - app_id is required for container-based access control
        if not app_id:
            raise HTTPException(
                status_code=400,
                detail="app_id query parameter is required for RBAC validation"
            )
        if not user_object_id and not group_object_id:
            raise HTTPException(
                status_code=400,
                detail="At least one of X-User-Object-Id or X-Group-Object-Id header is required"
            )
        if not storage_account_name:
            raise HTTPException(
                status_code=400,
                detail="X-Storage-Account header is required"
            )
        
        # Validate container access for the application
        validation_result = validate_container_only_access(
            storage_account_name=storage_account_name,
            container_name=app_id,
            user_object_id=user_object_id,
            group_object_id=group_object_id,
            resource_group_name=resource_group_name
        )
        
        # Validate days parameter
        if days < 1 or days > 365:
            raise HTTPException(status_code=400, detail="Days must be between 1 and 365")
        
        # Get operation service and summary
        operation_service = get_operation_service()
        response = await operation_service.get_operation_summary(app_id=app_id, days=days)
        
        logger.info(f"Generated summary for {response.summary.total_operations} operations over {days} days")
        return response
        
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as ex:
        logger.error(f"Error getting operation summary: {str(ex)}")
        raise HTTPException(status_code=500, detail=str(ex))

@app.get("/operations/{operation_id}/status")
async def get_specific_operation_status(
    request: Request,
    operation_id: str = PathParam(..., description="Operation ID to retrieve"),
    app_id: str = Query(..., description="Application ID (required for efficient lookup)"),
    include_results: bool = Query(False, description="Include operation results in response (for architecture analysis)")
):
    """
    Get the status of a specific operation, optionally including results.
    
    This unified endpoint handles all operation types including architecture analysis.
    For architecture analysis operations, use include_results=true to get analysis results.
    
    Path parameters:
        operation_id: Unique operation identifier
        
    Query parameters:
        app_id: Application ID (required for efficient lookup)
        include_results: If true, includes operation results (e.g., architecture analysis results)
    
    Headers (required for RBAC):
        X-User-Object-Id: User object ID for RBAC validation (optional if X-Group-Object-Id provided)
        X-Group-Object-Id: Group object ID for RBAC validation (optional if X-User-Object-Id provided)
        X-Storage-Account: Storage account name for RBAC validation
        X-Resource-Group: Resource group name (optional)
    
    Returns:
        OperationRecord with detailed operation information, optionally including results
        
    Raises:
        HTTPException: If operation not found or access denied
    """
    logger.info(f"API: Getting specific operation status - operation_id: {operation_id}, app_id: {app_id}, include_results: {include_results}")
    
    try:
        # Extract RBAC parameters from headers
        user_object_id = request.headers.get("X-User-Object-Id")
        group_object_id = request.headers.get("X-Group-Object-Id")
        storage_account_name = request.headers.get("X-Storage-Account")
        resource_group_name = request.headers.get("X-Resource-Group")
        
        # RBAC validation - check container access
        if not user_object_id and not group_object_id:
            raise HTTPException(
                status_code=400,
                detail="At least one of X-User-Object-Id or X-Group-Object-Id header is required"
            )
        if not storage_account_name:
            raise HTTPException(
                status_code=400,
                detail="X-Storage-Account header is required"
            )
        
        # Validate container access for the application
        validation_result = validate_container_only_access(
            storage_account_name=storage_account_name,
            container_name=app_id,
            user_object_id=user_object_id,
            group_object_id=group_object_id,
            resource_group_name=resource_group_name
        )
        
        # Get operation service and retrieve operation
        operation_service = get_operation_service()
        operation = await operation_service.get_operation(operation_id, app_id)
        
        if not operation:
            raise HTTPException(
                status_code=404,
                detail=f"Operation {operation_id} not found for application {app_id}"
            )
        
        logger.info(f"Retrieved operation {operation_id}: {operation.status.value} - {operation.current_step}")
        
        # If include_results=true and this is an architecture analysis operation, return enhanced response
        if include_results and operation.operation_type == OperationType.ARCHITECTURE_ANALYSIS:
            # Check operation status for architecture analysis
            if operation.status == OperationStatus.IN_PROGRESS or operation.status == OperationStatus.PENDING:
                # Still in progress - return HTTP 202
                return JSONResponse(
                    status_code=202,
                    content={
                        "status": "in_progress",
                        "operation_id": operation_id,
                        "operation_type": "architecture_analysis",
                        "design_doc_url": operation.design_doc_url,
                        "current_step": operation.current_step,
                        "progress_percentage": operation.progress_percentage,
                        "message": f"Architecture analysis still in progress: {operation.current_step} ({operation.progress_percentage}% complete)"
                    }
                )
            
            elif operation.status == OperationStatus.FAILED:
                # Failed - return HTTP 500
                error_message = operation.error_details.get("error_message", "Unknown error") if operation.error_details else "Unknown error"
                raise HTTPException(
                    status_code=500,
                    detail=f"Architecture analysis failed: {error_message}"
                )
            
            elif operation.status == OperationStatus.COMPLETED:
                # Completed - extract results from result_data
                result_data = operation.result_data or {}
                
                return JSONResponse(
                    status_code=200,
                    content={
                        "status": "completed",
                        "operation_id": operation_id,
                        "operation_type": "architecture_analysis",
                        "design_doc_url": result_data.get("design_doc_url") or operation.design_doc_url,
                        "total_architectures": result_data.get("total_architectures"),
                        "total_findings": result_data.get("total_findings"),
                        "consolidated_report_url": result_data.get("consolidated_report_url"),
                        "message": "Architecture security analysis completed successfully"
                    }
                )
        
        # Default: return standard operation record
        return operation
        
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as ex:
        logger.error(f"Error getting operation {operation_id}: {str(ex)}")
        raise HTTPException(status_code=500, detail=str(ex))


@app.get("/operations/{operation_id}/result", response_model=CodeAnalysisResultResponse)
async def get_operation_result(
    request: Request,
    operation_id: str = PathParam(..., description="Operation ID to retrieve results for"),
    app_id: str = Query(..., description="Application ID (required for efficient lookup)")
):
    """
    Get the final results of a completed code analysis operation.
    
    This endpoint returns the detailed analysis results only when the operation
    has completed successfully. For in-progress or failed operations, appropriate
    HTTP status codes are returned.
    
    Path parameters:
        operation_id: Unique operation identifier
        
    Query parameters:
        app_id: Application ID (required for efficient lookup)
    
    Headers (required for RBAC):
        X-User-Object-Id: User object ID for RBAC validation (optional if X-Group-Object-Id provided)
        X-Group-Object-Id: Group object ID for RBAC validation (optional if X-User-Object-Id provided)
        X-Storage-Account: Storage account name for RBAC validation
        X-Resource-Group: Resource group name (optional)
    
    Returns:
        CodeAnalysisResultResponse with detailed analysis results
        
    Raises:
        HTTPException: 
            - 404: Operation not found
            - 202: Operation still in progress
            - 500: Operation failed or no results found
            - 410: Operation was cancelled
    """
    logger.info(f"API: Getting operation result - operation_id: {operation_id}, app_id: {app_id}")
    
    try:
        # Extract RBAC parameters from headers
        user_object_id = request.headers.get("X-User-Object-Id")
        group_object_id = request.headers.get("X-Group-Object-Id")
        storage_account_name = request.headers.get("X-Storage-Account")
        resource_group_name = request.headers.get("X-Resource-Group")
        
        # RBAC validation - check container access
        if not user_object_id and not group_object_id:
            raise HTTPException(
                status_code=400,
                detail="At least one of X-User-Object-Id or X-Group-Object-Id header is required"
            )
        if not storage_account_name:
            raise HTTPException(
                status_code=400,
                detail="X-Storage-Account header is required"
            )
        
        # Validate container access for the application
        validation_result = validate_container_only_access(
            storage_account_name=storage_account_name,
            container_name=app_id,
            user_object_id=user_object_id,
            group_object_id=group_object_id,
            resource_group_name=resource_group_name
        )
        
        # Get operation service and retrieve operation
        operation_service = get_operation_service()
        operation = await operation_service.get_operation(operation_id, app_id)
        
        if not operation:
            raise HTTPException(
                status_code=404,
                detail=f"Operation {operation_id} not found for application {app_id}"
            )
        
        # Check operation status - only return results for completed operations
        if operation.status == OperationStatus.PENDING:
            raise HTTPException(
                status_code=202,
                detail=f"Operation {operation_id} is still pending. Current step: {operation.current_step}"
            )
        elif operation.status == OperationStatus.IN_PROGRESS:
            raise HTTPException(
                status_code=202,
                detail=f"Operation {operation_id} is still in progress ({operation.progress_percentage}%). Current step: {operation.current_step}"
            )
        elif operation.status == OperationStatus.FAILED:
            error_msg = operation.error_details.get("error_message", "Unknown error") if operation.error_details else "Operation failed"
            raise HTTPException(
                status_code=500,
                detail=f"Operation {operation_id} failed: {error_msg}"
            )
        elif operation.status == OperationStatus.CANCELLED:
            raise HTTPException(
                status_code=410,
                detail=f"Operation {operation_id} was cancelled"
            )
        
        # Operation is completed - extract results
        result_data = operation.result_data or {}
        
        if not result_data:
            raise HTTPException(
                status_code=500,
                detail=f"Operation {operation_id} completed but no results found"
            )
        
        # Get blob_url from operation record (most reliable source)
        blob_url = operation.blob_url or result_data.get("report_url")
        
        # Build the response
        response = CodeAnalysisResultResponse(
            status=result_data.get("status", "success"),
            operation_id=operation_id,
            app_id=app_id,
            repo_url=result_data.get("repo_url", operation.repo_url or ""),
            content_type=result_data.get("content_type", "unknown"),
            config_folder=result_data.get("config_folder", "unknown"),
            analysis_result=result_data.get("analysis_result", {}),
            repo_metadata=result_data.get("repo_metadata", {}),
            agents_info=result_data.get("agents_info", {}),
            message=result_data.get("message", "Analysis completed"),
            report_url=blob_url
        )
        
        logger.info(f"Retrieved code analysis results for operation {operation_id}, report_url: {blob_url}")
        return response
        
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as ex:
        logger.error(f"Error getting operation result {operation_id}: {str(ex)}")
        raise HTTPException(status_code=500, detail=str(ex))


# =============================================================================
# CODE ANALYSIS ENDPOINTS
# =============================================================================

async def _run_code_analysis_background(
    operation_id: str,
    app_id: str,
    repo_url: str, 
    source_type: str,
    perform_security_scan: bool,
    analysis_options: dict,
    storage_account_name: str,
    user_object_id: str = None,
    group_object_id: str = None,
    resource_group_name: str = None
):
    """
    Background task to run the actual code analysis and update operation status.
    
    Args:
        operation_id: Operation ID to track
        app_id: Application ID for RBAC and blob storage
        repo_url: Repository URL (GitHub, GitLab, Azure Blob, etc.)
        source_type: Type of source (github, gitlab, blob, etc.)
        perform_security_scan: Whether to scan for secrets
        analysis_options: Analysis configuration options
        storage_account_name: Storage account for report storage
        user_object_id: User object ID for RBAC
        group_object_id: Group object ID for RBAC
        resource_group_name: Resource group name
    """
    operation_service = get_operation_service()
    tracer = get_tracer()
    analysis_start_time = time.time()
    
    # Start background analysis span
    with tracer.start_as_current_span("code_analysis.background_task") as bg_span:
        add_span_attributes(bg_span, {
            "code_analysis.operation_id": operation_id,
            "code_analysis.app_id": app_id,
            "code_analysis.repo_url": repo_url[:500] if repo_url else None,
            "code_analysis.source_type": source_type,
            "code_analysis.security_scan_enabled": perform_security_scan
        })
        
        try:
            # Get operation record
            operation = await operation_service.get_operation(operation_id, app_id)
            if not operation:
                logger.error(f"Operation {operation_id} not found for background analysis")
                bg_span.set_status(Status(StatusCode.ERROR, "Operation not found"))
                bg_span.add_event("operation_not_found", {"operation_id": operation_id})
                return
            
            # Update: Starting analysis (10%)
            operation.update_progress("Initializing code analysis", 10, OperationStatus.IN_PROGRESS)
            await operation_service.update_operation(operation)
            
            # Update: Calling orchestrator (15%)
            operation.update_progress("Starting code analysis via orchestrator", 15, OperationStatus.IN_PROGRESS)
            await operation_service.update_operation(operation)
            
            # Compose the message for the orchestrator agent to analyze code
            # Include operation_id so the kernel function can update operation record directly
            message = (
                f"Analyze code repository for application ID: \"{app_id}\". "
                f"Repository URL: \"{repo_url}\". "
                f"Source type: \"{source_type}\". "
                f"Perform security scan: {perform_security_scan}. "
                f"Operation ID: \"{operation_id}\"."
            )
            logger.debug(f"Code analysis orchestrator message: {message}")
            
            # Call the orchestrator agent - it will route to analyze_code_repository kernel function
            result = await call_orchestrator(message, app_id)
            logger.info(f"Orchestrator result for code analysis: {result}")
            
            # Parse the result from orchestrator
            if isinstance(result, str):
                try:
                    result = json.loads(result) if result else {}
                except json.JSONDecodeError:
                    result = {"raw_message": result}
            elif not isinstance(result, dict):
                result = {"result": str(result)}
            
            # Extract the actual code analysis results from the orchestrator response
            # Orchestrator wraps it as: {"result": "ok", "code_analysis_results": {...}}
            analysis_data = result.get("code_analysis_results", result)
            
            # Parse the result if it's a string
            if isinstance(analysis_data, str):
                try:
                    analysis_data = json.loads(analysis_data)
                except json.JSONDecodeError:
                    analysis_data = {"raw_message": analysis_data}
            
            if analysis_data.get("result") == "success" or analysis_data.get("status") == "success":
                # Re-fetch operation to check if kernel function already completed it
                operation = await operation_service.get_operation(operation_id, app_id)
                
                if operation and operation.status == OperationStatus.COMPLETED:
                    # Kernel function already completed the operation with structured data
                    # Don't overwrite - just log success
                    blob_url = operation.blob_url
                    logger.info(f"Operation {operation_id} already completed by kernel function, blob_url: {blob_url}")
                    
                    # Record success metrics
                    analysis_duration = (time.time() - analysis_start_time) * 1000
                    add_span_attributes(bg_span, {
                        "code_analysis.status": "success",
                        "code_analysis.duration_ms": analysis_duration,
                        "code_analysis.blob_url": blob_url[:200] if blob_url else None,
                        "code_analysis.completed_by": "kernel_function"
                    })
                    bg_span.set_status(Status(StatusCode.OK))
                else:
                    # Kernel function didn't complete - complete here (fallback)
                    logger.warning(f"Operation {operation_id} not completed by kernel function, completing in background task")
                    
                    # Update: Finalizing results (85%)
                    operation.update_progress("Finalizing analysis results", 85, OperationStatus.IN_PROGRESS)
                    await operation_service.update_operation(operation)
                    
                    # Get blob_url from orchestrator result (kernel function handles upload)
                    blob_url = analysis_data.get("blob_url") or analysis_data.get("report_url")
                    
                    # Prepare final result
                    final_result = {
                        "status": "success",
                        "app_id": app_id,
                        "repo_url": repo_url,
                        "source_type": source_type,
                        "content_type": analysis_data.get("content_type", "unknown"),
                        "config_folder": analysis_data.get("config_folder", "unknown"),
                        "analysis_result": analysis_data.get("analysis_summary", {}),
                        "repo_metadata": analysis_data.get("repo_metadata", {}),
                        "codebase_analysis": analysis_data.get("codebase_analysis", {}),
                        "agents_info": {
                            "agents_used": analysis_data.get("analysis_summary", {}).get("agents_used", []),
                            "orchestrator_used": True
                        },
                        "report_url": blob_url,
                        "message": "Code analysis completed successfully via orchestrator"
                    }
                    
                    # Update operation with blob_url
                    operation.blob_url = blob_url
                    operation.complete_operation(final_result)
                    await operation_service.update_operation(operation)
                    
                    # Record success metrics
                    analysis_duration = (time.time() - analysis_start_time) * 1000
                    add_span_attributes(bg_span, {
                        "code_analysis.status": "success",
                        "code_analysis.duration_ms": analysis_duration,
                        "code_analysis.blob_url": blob_url[:200] if blob_url else None,
                        "code_analysis.completed_by": "background_task"
                    })
                    bg_span.set_status(Status(StatusCode.OK))
                    
                    logger.info(f"Background code analysis completed successfully for operation {operation_id}, blob_url: {blob_url}")
                
            else:
                error_message = analysis_data.get("message", "Code analysis failed")
                operation.fail_operation(error_message, {"result": analysis_data})
                await operation_service.update_operation(operation)
                
                bg_span.set_status(Status(StatusCode.ERROR, error_message[:256]))
                logger.error(f"Background code analysis failed for operation {operation_id}: {error_message}")
                
        except Exception as ex:
            logger.error(f"Background code analysis exception for operation {operation_id}: {str(ex)}")
            bg_span.record_exception(ex)
            bg_span.set_status(Status(StatusCode.ERROR, str(ex)[:256]))
            
            try:
                operation = await operation_service.get_operation(operation_id, app_id)
                if operation:
                    operation.fail_operation(str(ex), {"error_type": type(ex).__name__})
                    await operation_service.update_operation(operation)
            except Exception as update_ex:
                logger.error(f"Failed to update operation status after error: {update_ex}")
                
@app.post("/analyzeCode", response_model=CodeAnalysisResponse, status_code=202)
async def analyze_code(request: CodeAnalysisRequest = Body(...)):
    """
    Start code analysis for a repository or blob storage (async operation).
    
    This endpoint supports multiple source types:
    - GitHub repositories
    - GitLab repositories  
    - Azure DevOps repositories
    - Bitbucket repositories
    - Azure Blob Storage URLs (where code is already uploaded as .zip)
    
    The source type is auto-detected from the URL if not explicitly provided.
    
    This endpoint:
    1. Validates RBAC permissions using app_id
    2. Creates an operation record
    3. Returns operation_id IMMEDIATELY
    4. Starts analysis in the background
    
    Use the returned operation_id to:
    - Check status: GET /operations/{operation_id}/status?app_id={app_id}
    - Get results: GET /operations/{operation_id}/result?app_id={app_id}
    
    Args:
        request: CodeAnalysisRequest containing app_id, repo_url, RBAC info, and options
    
    Returns:
        CodeAnalysisResponse with operation_id and status endpoints
        
    Raises:
        HTTPException: If validation fails or RBAC check fails
    """
    tracer = get_tracer()
    
    with tracer.start_as_current_span("api.analyze_code") as span:
        source_type = request.get_source_type()
        
        add_span_attributes(span, {
            "api.endpoint": "/analyzeCode",
            "api.method": "POST",
            "code_analysis.app_id": request.app_id,
            "code_analysis.repo_url": request.repo_url[:500] if request.repo_url else None,
            "code_analysis.source_type": source_type.value,
            "code_analysis.security_scan_enabled": request.perform_security_scan
        })
        
        logger.info(f"API: Starting code analysis for app_id: {request.app_id}, repo: {request.repo_url}")
        
        try:
            # RBAC validation - ensure user has access to the app_id
            validation_result = user_authentication(
                storage_account_name=request.storage_account_name,
                app_id=request.app_id,
                user_object_id=request.user_object_id,
                group_object_id=request.group_object_id,
                resource_group_name=request.resource_group_name,
                endpoint_type="operation"
            )
            logger.info(f"User validated for code analysis: {validation_result['container']['status']}")
            
            # SCF Index validation - ensure SCF index is created and has indexed values
            # This validation must pass before proceeding with code assessment
            try:
                scf_index_name = os.getenv("SCF_AZURE_SEARCH_INDEX") or os.getenv("SEARCH_INDEX_NAME")
                scf_validation = validate_index(
                    index_name=scf_index_name,
                    require_documents=True,
                    index_display_name="SCF"
                )
                
                if not scf_validation.is_valid:
                    logger.error(f"SCF index validation failed: {scf_validation.error_message}")
                    span.set_status(Status(StatusCode.ERROR, "SCF index validation failed"))
                    add_span_attributes(span, {
                        "code_analysis.scf_validation": "failed",
                        "code_analysis.scf_error": scf_validation.error_message
                    })
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "error": "SCF index validation failed",
                            "message": scf_validation.error_message or "SCF index is not complete. Please ensure the SCF index (SCF_AZURE_SEARCH_INDEX) is created and populated before running code analysis.",
                            "validation_type": "scf_index",
                            "index_name": scf_validation.index_name
                        }
                    )
                
                logger.info(f"SCF index validation passed: {scf_validation.document_count} documents in SCF index '{scf_validation.index_name}'")
                add_span_attributes(span, {
                    "code_analysis.scf_validation": "passed",
                    "code_analysis.scf_index_name": scf_validation.index_name,
                    "code_analysis.scf_document_count": scf_validation.document_count
                })
                
            except HTTPException:
                raise  # Re-raise HTTP exceptions as-is
            except Exception as validation_ex:
                logger.error(f"SCF index validation error: {validation_ex}")
                span.set_status(Status(StatusCode.ERROR, f"SCF validation error: {str(validation_ex)}"))
                raise HTTPException(
                    status_code=500,
                    detail={
                        "error": "SCF index validation error",
                        "message": str(validation_ex),
                        "validation_type": "validation_error"
                    }
                )
            
            # Create operation record
            operation = OperationRecord(
                app_id=request.app_id,
                operation_type=OperationType.CODE_ANALYSIS,
                status=OperationStatus.PENDING,
                user_object_id=request.user_object_id,
                group_object_id=request.group_object_id,
                storage_account_name=request.storage_account_name,
                resource_group_name=request.resource_group_name,
                repo_url=request.repo_url,
                total_steps=5,
                current_step="Analysis queued"
            )
            
            operation_service = get_operation_service()
            operation_id = await operation_service.create_operation(operation)
            
            add_span_attributes(span, {"code_analysis.operation_id": operation_id})
            span.add_event("operation_created", {"operation_id": operation_id})
            
            # Start background task
            asyncio.create_task(_run_code_analysis_background(
                operation_id=operation_id,
                app_id=request.app_id,
                repo_url=request.repo_url,
                source_type=source_type.value,
                perform_security_scan=request.perform_security_scan,
                analysis_options=request.analysis_options or {},
                storage_account_name=request.storage_account_name,
                user_object_id=request.user_object_id,
                group_object_id=request.group_object_id,
                resource_group_name=request.resource_group_name
            ))
            
            logger.info(f"Created operation {operation_id} for code analysis, started in background")
            
            span.set_status(Status(StatusCode.OK))
            
            return CodeAnalysisResponse(
                status="accepted",
                operation_id=operation_id,
                app_id=request.app_id,
                repo_url=request.repo_url,
                source_type=source_type.value,
                message="Code analysis started. Use operation_id to check status and retrieve results.",
                status_endpoint=f"/operations/{operation_id}/status?app_id={request.app_id}",
                result_endpoint=f"/operations/{operation_id}/result?app_id={request.app_id}"
            )
            
        except HTTPException:
            raise
        except Exception as ex:
            error_msg = f"Failed to start code analysis: {str(ex)}"
            logger.error(error_msg)
            
            span.record_exception(ex)
            span.set_status(Status(StatusCode.ERROR, str(ex)[:256]))
            
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "Internal server error",
                    "message": error_msg,
                    "app_id": request.app_id,
                    "repo_url": request.repo_url
                }
            )


@app.delete("/operations/cleanup")
async def cleanup_operations(
    request: Request,
    confirm: bool = False,
    app_id: str = Query(..., description="Application ID (required for RBAC validation)"),
    all_for_app: bool = False,
    days: int = 30
):
    """
    Clean up operations with flexible options.
    
    Query parameters:
        confirm: Must be true to actually perform deletion (required)
        app_id: Application ID (required for RBAC validation)
        all_for_app: If true, delete ALL operations for the app (ignores days parameter)
        days: Operations older than this will be deleted (default: 30, max: 365)
               Only used when all_for_app=false
    
    Headers (required for RBAC):
        X-User-Object-Id: User object ID for RBAC validation (optional if X-Group-Object-Id provided)
        X-Group-Object-Id: Group object ID for RBAC validation (optional if X-User-Object-Id provided)
        X-Storage-Account: Storage account name for RBAC validation
        X-Resource-Group: Resource group name (optional)
    
    Examples:
        - Delete ALL operations for app: ?confirm=true&app_id=51151&all_for_app=true (with headers)
        - Delete old operations for app: ?confirm=true&app_id=51151&days=30 (with headers)
    
    Returns:
        Dictionary with cleanup results
        
    Raises:
        HTTPException: If parameters are invalid or access denied
    """
    logger.info(f"API: Cleanup operations - confirm: {confirm}, app_id: {app_id}, all_for_app: {all_for_app}, days: {days}")
    
    try:
        if not confirm:
            raise HTTPException(
                status_code=400,
                detail="Must set confirm=true to perform cleanup operation"
            )
        
        # Extract RBAC parameters from headers
        user_object_id = request.headers.get("X-User-Object-Id")
        group_object_id = request.headers.get("X-Group-Object-Id")
        storage_account_name = request.headers.get("X-Storage-Account")
        resource_group_name = request.headers.get("X-Resource-Group")
        
        # RBAC validation - container-based access control
        if not user_object_id and not group_object_id:
            raise HTTPException(
                status_code=400,
                detail="At least one of X-User-Object-Id or X-Group-Object-Id header is required"
            )
        if not storage_account_name:
            raise HTTPException(
                status_code=400,
                detail="X-Storage-Account header is required"
            )
        
        # Validate container access for the application
        validation_result = validate_container_only_access(
            storage_account_name=storage_account_name,
            container_name=app_id,
            user_object_id=user_object_id,
            group_object_id=group_object_id,
            resource_group_name=resource_group_name
        )
        
        # Get operation service
        operation_service = get_operation_service()
        
        if all_for_app:
            # Clean ALL operations for the specified app
            deleted_count = await operation_service.cleanup_operations_by_app(app_id=app_id)
            
            result = {
                "status": "success",
                "deleted_count": deleted_count,
                "app_id": app_id,
                "cleanup_type": "all_operations_for_app",
                "message": f"Successfully deleted ALL {deleted_count} operations for app {app_id}"
            }
        else:
            # Clean operations based on age (days) for the specified app
            if days < 7 or days > 365:
                raise HTTPException(status_code=400, detail="Days must be between 7 and 365")
            
            deleted_count = await operation_service.cleanup_old_operations(days=days, app_id=app_id)
            
            result = {
                "status": "success",
                "deleted_count": deleted_count,
                "days": days,
                "app_id": app_id,
                "cleanup_type": "old_operations",
                "message": f"Successfully deleted {deleted_count} operations for app {app_id} older than {days} days"
            }
        
        logger.info(f"Cleanup completed: {deleted_count} operations deleted")
        return result
        
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as ex:
        logger.error(f"Error during cleanup: {str(ex)}")
        raise HTTPException(status_code=500, detail=str(ex))


# Helper functions for architecture analysis background tasks

def _track_architecture_task(operation_id: str, task: asyncio.Task) -> None:
    """Track a running architecture analysis background task."""
    _running_architecture_tasks[operation_id] = task
    logger.debug(f"Tracking architecture analysis task for operation {operation_id}")


def _remove_architecture_task(operation_id: str) -> None:
    """Remove a completed/failed architecture analysis task from tracking."""
    if operation_id in _running_architecture_tasks:
        del _running_architecture_tasks[operation_id]
        logger.debug(f"Removed architecture analysis task for operation {operation_id}")


async def _run_architecture_analysis_background(app_id: str, operation: OperationRecord, design_doc_url: str) -> None:
    """
    Background task to run architecture analysis and update operation status.
    
    This function runs in the background, updating the operation record as it progresses.
    Results are stored in operation.result_data with report URL and summary metadata.
    
    Args:
        app_id: Application ID for RBAC and blob storage
        operation: Operation record to track progress
        design_doc_url: Blob storage path to design document to analyze
    """
    operation_service = get_operation_service()
    tracer = get_tracer()
    analysis_start_time = time.time()
    
    # Start background analysis span
    with tracer.start_as_current_span("architecture_analysis.background_task") as bg_span:
        add_span_attributes(bg_span, {
            "architecture_analysis.operation_id": operation.operation_id,
            "architecture_analysis.app_id": app_id,
            "architecture_analysis.design_doc_url": design_doc_url[:500] if design_doc_url else None
        })
        
        try:
            # Update operation to in-progress
            operation.update_progress("Initializing architecture analysis", 10, OperationStatus.IN_PROGRESS)
            await operation_service.update_operation(operation)
            
            logger.info(f"Starting background architecture analysis for operation {operation.operation_id}")
            
            # Update: Calling orchestrator (15%)
            operation.update_progress("Starting architecture analysis via orchestrator", 15, OperationStatus.IN_PROGRESS)
            await operation_service.update_operation(operation)
            
            # Define progress callback to forward progress from agent to operation
            async def progress_callback(message: str, percentage: float):
                """
                Forward progress updates from architecture analyzer agent to operation record.
                Agent reports progress in 20-85% range during architecture processing.
                """
                # Ensure percentage stays within 20-85% range
                percentage = max(20, min(85, percentage))
                logger.info(f"Architecture analysis progress: {percentage}% - {message}")
                operation.update_progress(message, percentage, OperationStatus.IN_PROGRESS)
                await operation_service.update_operation(operation)
            
            # Compose the message for the orchestrator agent to analyze architecture
            # Include operation_id so the kernel function can update operation record directly
            message = (
                f"Analyze architecture security for application ID: \"{app_id}\". "
                f"Design document URL: \"{design_doc_url}\". "
                f"Analysis mode: \"dynamic\". "
                f"Analysis instructions: \"Analyze this architecture for security compliance and generate recommendations\". "
                f"Operation ID: \"{operation.operation_id}\"."
            )
            logger.debug(f"Architecture analysis orchestrator message: {message}")
            
            # Call the orchestrator agent with progress callback - it will route to analyze_architecture kernel function
            result = await call_orchestrator(message, app_id, progress_callback=progress_callback)
            logger.info(f"Orchestrator result for architecture analysis: {result}")
            
            # Parse the result from orchestrator
            if isinstance(result, str):
                try:
                    result = json.loads(result) if result else {}
                except json.JSONDecodeError:
                    result = {"raw_message": result}
            elif not isinstance(result, dict):
                result = {"result": str(result)}
            
            # Extract the actual architecture analysis results from the orchestrator response
            # Orchestrator wraps it as: {"result": "ok", "architecture_analysis_results": {...}}
            analysis_data = result.get("architecture_analysis_results", result)
            
            # Parse the result if it's a string
            if isinstance(analysis_data, str):
                try:
                    analysis_data = json.loads(analysis_data)
                except json.JSONDecodeError:
                    analysis_data = {"raw_message": analysis_data}
            
            # Ensure analysis_data is a dict to prevent NoneType errors
            if not isinstance(analysis_data, dict):
                analysis_data = {"status": "error", "error": f"Invalid analysis result type: {type(analysis_data).__name__}"}
            
            # Check if validation or analysis failed
            result_status = analysis_data.get("status", "unknown")
            
            if result_status == "validation_failed":
                # SCF index validation failed - mark operation as failed
                error_message = analysis_data.get("error", "SCF index validation failed")
                validation_details = analysis_data.get("validation_details", {})
                error_details = {
                    "error_message": error_message,
                    "status": result_status,
                    "total_architectures": analysis_data.get("total_architectures", 0),
                    "total_findings": analysis_data.get("total_findings", 0),
                    "consolidated_report_url": analysis_data.get("consolidated_report_url"),
                    "design_doc_url": design_doc_url,
                    "analysis_mode": "dynamic",
                    "validation_details": validation_details,
                    "suggestion": analysis_data.get("suggestion", "")
                }
                operation.fail_operation(error_message, error_details)
                await operation_service.update_operation(operation)
                
                bg_span.set_status(Status(StatusCode.ERROR, error_message[:256]))
                logger.error(f"Architecture analysis failed for operation {operation.operation_id}: {error_message}")
                
            elif result_status == "error":
                # Analysis error - mark operation as failed
                # Check both 'error' and 'message' fields for error details
                error_message = analysis_data.get("error") or analysis_data.get("message") or "Architecture analysis failed"
                error_details = {
                    "error_message": error_message,
                    "error_type": analysis_data.get("error_type"),
                    "status": result_status,
                    "total_architectures": analysis_data.get("total_architectures", 0),
                    "total_findings": analysis_data.get("total_findings", 0),
                    "consolidated_report_url": analysis_data.get("consolidated_report_url"),
                    "design_doc_url": design_doc_url,
                    "analysis_mode": "dynamic"
                }
                operation.fail_operation(error_message, error_details)
                await operation_service.update_operation(operation)
                
                bg_span.set_status(Status(StatusCode.ERROR, error_message[:256]))
                logger.error(f"Architecture analysis failed for operation {operation.operation_id}: {error_message}")
                
            elif result_status == "success" or analysis_data.get("result") == "success":
                # Check if message contains error indicators (BlobNotFound, etc.)
                message_text = analysis_data.get("message", "")
                if "BlobNotFound" in message_text or "could not be started" in message_text or "does not exist" in message_text:
                    # Orchestrator detected an error but returned success status - treat as error
                    error_message = message_text or "Design-doc extraction failed"
                    error_details = {
                        "error_message": error_message,
                        "status": "error",
                        "design_doc_url": design_doc_url,
                        "analysis_mode": "dynamic"
                    }
                    operation.fail_operation(error_message, error_details)
                    await operation_service.update_operation(operation)
                    
                    bg_span.set_status(Status(StatusCode.ERROR, error_message[:256]))
                    logger.error(f"Architecture analysis failed (BlobNotFound) for operation {operation.operation_id}: {error_message}")
                    return
                # Re-fetch operation to check if kernel function already completed it
                refetched_operation = await operation_service.get_operation(operation.operation_id, app_id)
                
                if refetched_operation and refetched_operation.status == OperationStatus.COMPLETED:
                    # Kernel function already completed the operation with structured data
                    # Don't overwrite - just log success
                    consolidated_report_url = refetched_operation.result_data.get("consolidated_report_url") if refetched_operation.result_data else None
                    logger.info(f"Operation {operation.operation_id} already completed by kernel function, report_url: {consolidated_report_url}")
                    
                    # Record success metrics
                    analysis_duration = (time.time() - analysis_start_time) * 1000
                    add_span_attributes(bg_span, {
                        "architecture_analysis.status": "success",
                        "architecture_analysis.duration_ms": analysis_duration,
                        "architecture_analysis.report_url": consolidated_report_url[:200] if consolidated_report_url else None,
                        "architecture_analysis.completed_by": "kernel_function"
                    })
                    bg_span.set_status(Status(StatusCode.OK))
                elif refetched_operation:
                    # Kernel function didn't complete - complete here (fallback)
                    # Update operation variable for subsequent uses
                    operation = refetched_operation
                    logger.warning(f"Operation {operation.operation_id} not completed by kernel function, completing in background task")
                    
                    # Update: Finalizing results (85%)
                    operation.update_progress("Finalizing analysis results", 85, OperationStatus.IN_PROGRESS)
                    await operation_service.update_operation(operation)
                    
                    # Extract summary metadata for result_data
                    summary_data = {
                        "status": "success",
                        "total_architectures": analysis_data.get("total_architectures", 0),
                        "total_findings": analysis_data.get("total_findings", 0),
                        "consolidated_report_url": analysis_data.get("consolidated_report_url"),
                        "design_doc_url": design_doc_url,
                        "analysis_mode": "dynamic"
                    }
                    
                    # Mark operation as completed with summary data
                    operation.complete_operation(summary_data)
                    await operation_service.update_operation(operation)
                    
                    # Record success metrics
                    analysis_duration = (time.time() - analysis_start_time) * 1000
                    add_span_attributes(bg_span, {
                        "architecture_analysis.status": "success",
                        "architecture_analysis.duration_ms": analysis_duration,
                        "architecture_analysis.report_url": summary_data.get("consolidated_report_url", "")[:200],
                        "architecture_analysis.completed_by": "background_task"
                    })
                    bg_span.set_status(Status(StatusCode.OK))
                    
                    logger.info(f"Background architecture analysis completed successfully for operation {operation.operation_id}")
                else:
                    # Could not refetch operation - log error and mark as failed
                    error_message = f"Failed to refetch operation {operation.operation_id} after successful analysis"
                    logger.error(error_message)
                    bg_span.set_status(Status(StatusCode.ERROR, error_message[:256]))
                    # Try to mark original operation as failed
                    try:
                        operation.fail_operation(error_message, {"analysis_data": analysis_data})
                        await operation_service.update_operation(operation)
                    except Exception as fail_ex:
                        logger.error(f"Failed to update operation status: {fail_ex}")
            else:
                # Unknown status - treat as error
                error_message = analysis_data.get("message", "Architecture analysis failed with unknown status")
                operation.fail_operation(error_message, {"result": analysis_data})
                await operation_service.update_operation(operation)
                
                bg_span.set_status(Status(StatusCode.ERROR, error_message[:256]))
                logger.error(f"Background architecture analysis failed for operation {operation.operation_id}: {error_message}")
            
        except Exception as ex:
            logger.error(f"Background architecture analysis exception for operation {operation.operation_id}: {str(ex)}")
            bg_span.record_exception(ex)
            bg_span.set_status(Status(StatusCode.ERROR, str(ex)[:256]))
            
            try:
                operation = await operation_service.get_operation(operation.operation_id, app_id)
                if operation:
                    error_details = {
                        "error_type": type(ex).__name__,
                        "error_message": str(ex),
                        "design_doc_url": design_doc_url
                    }
                    operation.fail_operation(str(ex), error_details)
                    await operation_service.update_operation(operation)
            except Exception as update_ex:
                logger.error(f"Failed to update operation status after error: {update_ex}")
        
        finally:
            # Remove task from tracking registry
            _remove_architecture_task(operation.operation_id)