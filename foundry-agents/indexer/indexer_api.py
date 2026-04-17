import os
import time
import logging
from datetime import datetime
from fastapi import FastAPI, HTTPException, Body, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional
import indexer

# Import logging configuration (using local logging_config.py similar to orchestrator_agent)
from logging_config import get_logger

# Import tracing configuration (using local tracing_config.py)
try:
    from tracing_config import (
        initialize_tracing,
        initialize_tracing_with_context,
        get_tracer,
        add_span_attributes,
        record_search_operation
    )
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode
    TRACING_AVAILABLE = True
    # Note: Tracing configuration imported successfully, but initialization happens later
except ImportError as import_ex:
    TRACING_AVAILABLE = False
    # Use basic logging if proper logging isn't configured yet
    logging.basicConfig(level=logging.WARNING)
    logging.warning(f"Tracing configuration not available - running without telemetry: {import_ex}")

# Version info - using fallback since version module is not available in container
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

# Set up proper logging using the same pattern as orchestrator_agent
logger = get_logger(__name__)

# Get version info
try:
    version_info = get_version_info()
    logger.info(f"Indexer Service - Version: {version_info['version']}, Build: {version_info['build']}, Commit: {version_info['commit']}, Deployed: {version_info['deployed_at']}")
except Exception as e:
    logger.warning(f"Could not get version info: {e}")

# Initialize tracing
tracing_enabled = False
if TRACING_AVAILABLE:
    try:
        logger.info("🔧 Starting tracing initialization...")
        
        # Get required environment variables
        ai_project_endpoint = os.getenv("AZURE_EXISTING_AIPROJECT_ENDPOINT")
        
        # Try context-aware initialization first (like orchestrator_agent)
        if ai_project_endpoint:
            logger.info(f"🔗 Initializing tracing with Azure AI Foundry endpoint")
            tracing_enabled = initialize_tracing_with_context(project_endpoint=ai_project_endpoint)
        else:
            logger.info("🔗 Initializing tracing without specific endpoint")
            tracing_enabled = initialize_tracing()
            
        if tracing_enabled:
            logger.info("✅ OpenTelemetry tracing initialized successfully for Indexer Service API")
            # Verify tracing is working by testing tracer creation
            try:
                test_tracer = get_tracer()
                if test_tracer:
                    logger.info("✅ Tracer verification successful - tracing is fully operational")
                else:
                    logger.warning("⚠️ Tracer verification failed - tracer is None")
                    tracing_enabled = False
            except Exception as tracer_test_ex:
                logger.warning(f"⚠️ Tracer verification failed: {tracer_test_ex}")
                tracing_enabled = False
        else:
            logger.warning("⚠️ OpenTelemetry tracing initialization returned False - check Azure AI Foundry connection")
    except Exception as trace_ex:
        logger.error(f"❌ Failed to initialize tracing: {trace_ex}", exc_info=True)
        tracing_enabled = False
else:
    logger.warning("⚠️ Tracing not available - running without telemetry")

# Final tracing status logging
logger.info(f"🔍 Final tracing status: TRACING_AVAILABLE={TRACING_AVAILABLE}, tracing_enabled={tracing_enabled}")

# Initialize FastAPI app
app = FastAPI(
    title="Indexer Service API",
    description="Document indexing service for Azure Container Apps",
    version=API_VERSION
)

# Add middleware for comprehensive telemetry
@app.middleware("http")
async def telemetry_middleware(request: Request, call_next):
    """
    Middleware to add comprehensive telemetry to all API requests.
    Tracks latency, request/response sizes, errors, and creates spans for each endpoint.
    """
    if not tracing_enabled:
        return await call_next(request)
    
    tracer = get_tracer()
    start_time = time.time()
    
    # Extract request metadata
    endpoint = request.url.path
    method = request.method
    
    with tracer.start_as_current_span(f"indexer_api_{method.lower()}_{endpoint.replace('/', '_')}") as span:
        try:
            # Add request attributes to span
            add_span_attributes(span, {
                "http.method": method,
                "http.url": str(request.url),
                "http.scheme": request.url.scheme,
                "http.host": request.headers.get("host", "unknown"),
                "user_agent.original": request.headers.get("user-agent", "unknown"),
                "service.name": "indexer-service",
                "service.version": "1.0.0"
            })
            
            # Process request
            response = await call_next(request)
            
            # Calculate latency
            latency = time.time() - start_time
            
            # Add response attributes
            add_span_attributes(span, {
                "http.status_code": response.status_code,
                "http.response.latency_ms": round(latency * 1000, 2)
            })
            
            # Set span status based on HTTP status code
            if response.status_code >= 400:
                span.set_status(Status(StatusCode.ERROR, f"HTTP {response.status_code}"))
            else:
                span.set_status(Status(StatusCode.OK))
            
            return response
            
        except Exception as e:
            # Record error details manually
            add_span_attributes(span, {
                "error.type": type(e).__name__,
                "error.message": str(e),
                "endpoint": endpoint,
                "method": method,
                "latency_ms": round((time.time() - start_time) * 1000, 2)
            })
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise

# Request/Response Models
class IndexRequest(BaseModel):
    appId: str = Field(..., description="Application identifier for the indexing operation", example="myapp-001")
    container: str = Field(..., description="Azure Blob Storage container name to index", example="documents")
    folder_prefix: Optional[str] = Field(
        default=None, 
        description="Optional folder prefix to limit indexing to a specific folder path within the container",
        example="uploads/2026/01/"
    )
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "appId": "myapp-001",
                    "container": "documents",
                    "folder_prefix": "uploads/2026/01/"
                },
                {
                    "appId": "myapp-001",
                    "container": "documents"
                }
            ]
        }
    }

class IndexResponse(BaseModel):
    status: str
    mode: str
    result: Dict[str, Any]

class ErrorResponse(BaseModel):
    status: str
    error: str

@app.get("/health")
async def health_check():
    """
    Health check endpoint for Azure Container Apps health probes.
    Returns service status and version information.
    """
    try:
        logger.info("[HEALTH] Health check requested")
        
        # Basic health check - verify essential environment variables are set
        required_env_vars = [
            "AZURE_EXISTING_AIPROJECT_ENDPOINT",
            "AZURE_STORAGE_ACCOUNT_URL"
        ]

        missing_vars = [var for var in required_env_vars if not os.getenv(var)]

        if missing_vars:
            logger.warning(f"⚠️ Health check failed - missing environment variables: {missing_vars}")
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
        logger.info(f"Indexer Service - Version: {version_info['version']}, Build: {version_info['build']}, Commit: {version_info['commit']}, Deployed: {version_info['deployed_at']}")

        # Check if tracing is actually active (more comprehensive check)
        actual_tracing_status = tracing_enabled
        if TRACING_AVAILABLE:
            try:
                # Try to get a tracer to verify tracing is actually working
                tracer = get_tracer()
                if tracer is not None:
                    # Check if we can create a span (indicates tracing is truly active)
                    with tracer.start_as_current_span("health_check_tracing_test") as test_span:
                        if test_span is not None:
                            actual_tracing_status = True
                            test_span.set_attribute("test.verification", "success")
            except Exception as tracing_check_ex:
                logger.info(f"Tracing verification failed: {tracing_check_ex}")
                actual_tracing_status = False

        logger.info("✅ Health check passed")
        
        # Get current logging level
        current_log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        
        return {
            "status": "healthy",
            "message": "AI Indexer Service is running",
            "version": version_info["version"],
            "build": version_info["build"],
            "commit": version_info["commit"],
            "deployed_at": version_info["deployed_at"],
            "timestamp": time.time(),
            "tracing_enabled": actual_tracing_status,
            "logging_level": current_log_level,
            "trace_level": "ENABLED" if actual_tracing_status else "DISABLED"
        }
    except Exception as ex:
        logger.error(f"❌ Health check failed: {str(ex)}", exc_info=True)
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "message": "Health check failed, please check logs for internal error",
                "timestamp": time.time()
            }
        )

@app.post("/api/index", response_model=IndexResponse)
async def index_documents(request: IndexRequest = Body(...)):
    """
    Index documents for an application.
    
    Args:
        request: IndexRequest containing appId and container
    
    Returns:
        IndexResponse with indexing results
        
    Raises:
        HTTPException: If indexing fails
    """
    logger.info(f"[INDEXER] index_documents called with appId={request.appId}, container={request.container}")
    
    if tracing_enabled:
        tracer = get_tracer()
        with tracer.start_as_current_span("indexer_index_documents") as span:
            try:
                # Add span attributes (following insights_orchestrator pattern)
                add_span_attributes(span, {
                    "application_id": request.appId,
                    "container": request.container,
                    "operation": "index_documents",
                    "service.name": "indexer-service",
                    "service.operation": "index_container"
                })
                
                logger.info(f"Processing indexing request for container={request.container}, folder_prefix={request.folder_prefix}")
                
                # Call the indexer with tracing
                result = indexer.index_container(
                    app_id=request.appId, 
                    container=request.container,
                    folder_prefix=request.folder_prefix
                )
                
                # Record search operation (since indexing involves search operations)
                record_search_operation(span, "container_indexing", request.container, {
                    "app_id": request.appId,
                    "result_type": type(result).__name__,
                    "result_size": len(result) if isinstance(result, (list, dict)) else 1
                })
                
                # Set success status
                span.set_status(Status(StatusCode.OK))
                logger.info(f"✅ Indexing completed successfully for appId={request.appId}")
                
                return IndexResponse(
                    status="success",
                    mode="container",
                    result=result
                )
                
            except Exception as ex:
                # Record error details manually
                add_span_attributes(span, {
                    "error.type": type(ex).__name__,
                    "error.message": str(ex),
                    "application_id": request.appId,
                    "container": request.container,
                    "operation": "index_documents"
                })
                span.set_status(Status(StatusCode.ERROR, str(ex)))
                logger.error(f"❌ Indexing failed for appId={request.appId}: {ex}", exc_info=True)
                raise HTTPException(status_code=500, detail=f"Indexing failed: {str(ex)}")
    else:
        # Fallback without tracing (following insights_orchestrator pattern)
        try:
            logger.info(f"Processing indexing request for container={request.container}, folder_prefix={request.folder_prefix} (no tracing)")
            
            # Call the indexer
            result = indexer.index_container(
                app_id=request.appId, 
                container=request.container,
                folder_prefix=request.folder_prefix
            )
            
            logger.info(f"✅ Indexing completed successfully for appId={request.appId}")
            
            return IndexResponse(
                status="success",
                mode="container",
                result=result
            )
            
        except Exception as ex:
            logger.error(f"❌ Indexing failed for appId={request.appId}: {ex}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=f"Indexing failed: {str(ex)}"
            )

# Startup logging (following insights_orchestrator pattern)
logger.info("🔍 Indexer Service API initialized successfully")
logger.info(f"📊 Tracing enabled: {tracing_enabled}")
if tracing_enabled:
    logger.info("🔗 Traces will be sent to Azure AI Foundry for monitoring")
