# Copyright (c) Microsoft. All rights reserved.

"""
Azure AI Foundry Tracing Configuration Module

This module sets up OpenTelemetry tracing for Azure AI Foundry integration,
following the official documentation patterns for comprehensive agent monitoring.
"""

import os
import logging
from typing import Optional
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
from azure.monitor.opentelemetry import configure_azure_monitor
from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.trace import Status, StatusCode

# Use centralized logging configuration
from agents.logging_config import get_logger

# Configure logging and create logger
logger = get_logger(__name__)

def is_verbose_mode() -> bool:
    """
    Check if verbose mode is enabled from APP_VERBOSE environment variable.
    
    Returns:
        bool: True if verbose logging is enabled
    """
    return os.getenv("APP_VERBOSE", "false").strip().lower() in {"1", "true", "yes", "on", "debug"}

def log_info_if_verbose(message: str) -> None:
    """
    Log info message only if verbose mode is enabled.
    
    Args:
        message: The message to log
    """
    if is_verbose_mode():
        logger.info(message)

def log_success_if_verbose(message: str) -> None:
    """
    Log success message (with ✅ emoji) only if verbose mode is enabled.
    
    Args:
        message: The success message to log
    """
    if is_verbose_mode():
        logger.info(message)

# Test logging on import
if is_verbose_mode():
    logger.info("Tracing configuration module loaded")
    logger.debug("Debug logging is enabled for tracing module")

# Global tracer instance
tracer: Optional[trace.Tracer] = None
_tracing_initialized = False


def debug_ai_project_client(project_endpoint: str) -> None:
    """
    Debug function to explore AIProjectClient capabilities.
    """
    try:
        # Use DefaultAzureCredential which works both locally and in Container Apps
        # Locally: uses Azure CLI or VS Code credentials
        # Container Apps: uses managed identity
        credential = DefaultAzureCredential()
        project_client = AIProjectClient(
            credential=credential,
            endpoint=project_endpoint
        )
        
        logger.debug("=== AIProjectClient Debug Info ===")
        logger.debug(f"Client type: {type(project_client)}")
        
        # List available attributes
        attributes = [attr for attr in dir(project_client) if not attr.startswith('_')]
        logger.debug(f"Available attributes: {attributes}")
        
        # Check specific attributes we're interested in
        if hasattr(project_client, 'telemetry'):
            logger.debug(f"Telemetry type: {type(project_client.telemetry)}")
            telemetry_methods = [method for method in dir(project_client.telemetry) if not method.startswith('_')]
            logger.debug(f"Telemetry methods: {telemetry_methods}")
        
        if hasattr(project_client, 'connections'):
            logger.debug(f"Connections type: {type(project_client.connections)}")
            connections_methods = [method for method in dir(project_client.connections) if not method.startswith('_')]
            logger.debug(f"Connections methods: {connections_methods}")
            
        logger.debug("=== End Debug Info ===")
        
    except Exception as ex:
        logger.debug(f"Debug exploration failed: {ex}")


def get_ai_project_endpoint() -> Optional[str]:
    """
    Get the AI Project endpoint from various sources.
    
    Returns the endpoint from:
    1. Environment variable AZURE_EXISTING_AIPROJECT_ENDPOINT
    2. Semantic Kernel AzureAIAgentSettings (if available)
    3. None if not available
    """
    # First try environment variable
    endpoint = os.getenv("AZURE_EXISTING_AIPROJECT_ENDPOINT")
    if endpoint:
        logger.debug(f"Using AI Project endpoint from environment variable: {endpoint}")
        return endpoint
    
    # Try to get from Semantic Kernel settings
    try:
        from semantic_kernel.agents import AzureAIAgentSettings
        
        settings = None
        try:
            settings = AzureAIAgentSettings()
        except Exception:
            settings = None

        if settings:
            endpoint = getattr(settings, "endpoint", None)
            if endpoint:
                logger.debug(f"Using AI Project endpoint from Semantic Kernel settings: {endpoint}")
                return endpoint
    except ImportError:
        logger.debug("Semantic Kernel not available for endpoint resolution")
    except Exception as ex:
        logger.debug(f"Failed to get endpoint from Semantic Kernel settings: {ex}")
    
    logger.debug("No AI Project endpoint found")
    return None


def get_application_insights_connection_string() -> Optional[str]:
    """
    Get Application Insights connection string from multiple sources with fallback priority:
    
    1. Environment variable APPLICATIONINSIGHTS_CONNECTION_STRING (highest priority - standard OpenTelemetry)
    2. Azure AI Project endpoint connections API (fallback for project-level configuration)
    
    Returns the connection string or None if not available from any source.
    """
    # Method 1: Check environment variable first (most reliable, standard OpenTelemetry pattern)
    # This is automatically set by Container Apps Environment when appInsightsConnectionString is configured
    connection_string = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if connection_string:
        log_success_if_verbose("✅ Using Application Insights connection string from APPLICATIONINSIGHTS_CONNECTION_STRING environment variable")
        return connection_string
    
    logger.debug("APPLICATIONINSIGHTS_CONNECTION_STRING environment variable not found, attempting to retrieve from AI Project")
    
    # Method 2: Retrieve from Azure AI Project endpoint (fallback)
    try:
        project_endpoint = os.getenv("AZURE_EXISTING_AIPROJECT_ENDPOINT")
        if not project_endpoint:
            logger.error("AZURE_EXISTING_AIPROJECT_ENDPOINT environment variable is required")
            return None
        
        log_info_if_verbose(f"Retrieving Application Insights connection string from AI Project: {project_endpoint}")
        
        # Enable debug mode if verbose logging is on
        if is_verbose_mode():
            debug_ai_project_client(project_endpoint)
        
        # Use DefaultAzureCredential which works both locally and in Container Apps
        # Locally: uses Azure CLI or VS Code credentials
        # Container Apps: uses managed identity
        credential = DefaultAzureCredential()
        project_client = AIProjectClient(
            credential=credential,
            endpoint=project_endpoint
        )
        
        # Try different methods to get the connection string
        connection_string = None
        
        # Method 1: Check if telemetry property exists and has the method
        if hasattr(project_client, 'telemetry') and hasattr(project_client.telemetry, 'get_connection_string'):
            try:
                connection_string = project_client.telemetry.get_connection_string()
                log_success_if_verbose("✅ Retrieved connection string using telemetry.get_connection_string()")
            except Exception as ex:
                logger.debug(f"Method 1 failed: {ex}")
        
        # Method 2: Try the original method name
        if not connection_string and hasattr(project_client, 'telemetry') and hasattr(project_client.telemetry, 'get_application_insights_connection_string'):
            try:
                connection_string = project_client.telemetry.get_application_insights_connection_string()
                log_success_if_verbose("✅ Retrieved connection string using telemetry.get_application_insights_connection_string()")
            except Exception as ex:
                logger.debug(f"Method 2 failed: {ex}")
        
        # Method 3: Try to get from project properties/connections
        if not connection_string:
            try:
                # Check if there's a connections property
                if hasattr(project_client, 'connections'):
                    connections = project_client.connections.list()
                    for conn in connections:
                        if hasattr(conn, 'connection_type') and 'applicationinsights' in str(conn.connection_type).lower():
                            if hasattr(conn, 'target') and conn.target:
                                connection_string = conn.target
                                log_success_if_verbose("✅ Retrieved connection string from project connections")
                                break
            except Exception as ex:
                logger.debug(f"Method 3 failed: {ex}")
        
        # Method 4: Try alternative connection properties
        if not connection_string:
            try:
                # Some AI projects might have the connection string in different attributes
                if hasattr(project_client, 'get_application_insights_connection_string'):
                    connection_string = project_client.get_application_insights_connection_string()
                    log_success_if_verbose("✅ Retrieved connection string using direct method")
            except Exception as ex:
                logger.debug(f"Method 4 failed: {ex}")
        
        if connection_string:
            log_success_if_verbose("✅ Successfully retrieved Application Insights connection string from Azure AI Project")
            return connection_string
        else:
            # Method 5: Fallback to environment variable
            env_connection_string = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
            if env_connection_string:
                log_success_if_verbose("✅ Retrieved connection string from APPLICATIONINSIGHTS_CONNECTION_STRING environment variable")
                return env_connection_string
            
            logger.error("❌ Could not retrieve Application Insights connection string from Azure AI Project")
            logger.error("Please ensure your AI Project has Application Insights configured, you have proper permissions,")
            logger.error("or set the APPLICATIONINSIGHTS_CONNECTION_STRING environment variable")
            return None
            
    except Exception as ex:
        logger.error(f"❌ Failed to get connection string from Azure AI Project: {ex}")
        # Fallback to environment variable even on exception
        env_connection_string = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
        if env_connection_string:
            log_success_if_verbose("✅ Retrieved connection string from APPLICATIONINSIGHTS_CONNECTION_STRING environment variable (fallback)")
            return env_connection_string
        return None


def setup_azure_monitor_tracing(connection_string: Optional[str] = None) -> bool:
    """
    Configure Azure Monitor OpenTelemetry tracing (required, no fallback).
    
    Args:
        connection_string: Optional Application Insights connection string.
                          If not provided, will attempt to retrieve from AI Project.
    
    Returns:
        bool: True if successfully configured, False otherwise.
    """
    if not connection_string:
        connection_string = get_application_insights_connection_string()
    
    if not connection_string:
        logger.error("❌ Cannot configure Azure Monitor tracing: No connection string available from AI Project")
        logger.error("Please ensure your AI Foundry project has Application Insights configured")
        return False
    
    try:
        # Configure Azure Monitor
        configure_azure_monitor(connection_string=connection_string)
        log_success_if_verbose("✅ Azure Monitor tracing configured successfully")
        return True
    except Exception as ex:
        logger.error(f"❌ Failed to configure Azure Monitor tracing: {ex}")
        return False


def instrument_openai() -> bool:
    """
    Instrument OpenAI SDK for automatic tracing.
    
    Returns:
        bool: True if successfully instrumented, False otherwise.
    """
    try:
        # Check if message content capture is enabled
        capture_content = os.getenv("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "false").lower()
        if capture_content == "true" and is_verbose_mode():
            logger.info("OpenAI message content capture is enabled")
        
        # Instrument OpenAI SDK
        OpenAIInstrumentor().instrument()
        log_success_if_verbose("✅ OpenAI SDK instrumented successfully")
        return True
    except Exception as ex:
        logger.error(f"Failed to instrument OpenAI SDK: {ex}")
        return False


def initialize_tracing_with_context(
    ai_project_client=None,
    project_endpoint: Optional[str] = None,
    connection_string: Optional[str] = None
) -> bool:
    """
    Initialize tracing with runtime context from the application (Azure Monitor only).
    
    This is designed to be called from within the main application where
    the AI Project client or endpoint is already available.
    
    Args:
        ai_project_client: Optional AIProjectClient instance
        project_endpoint: Optional project endpoint string
        connection_string: Optional Application Insights connection string
    
    Returns:
        bool: True if tracing was successfully initialized
    """
    global tracer, _tracing_initialized
    
    if _tracing_initialized:
        logger.debug("Tracing already initialized")
        return True
    
    log_info_if_verbose("🚀 Initializing Azure AI Foundry tracing with runtime context (Azure Monitor only)...")
    
    # Try to get connection string from provided context with proper fallback priority
    if not connection_string:
        # Priority 1: Check environment variable (standard OpenTelemetry pattern)
        connection_string = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
        if connection_string:
            log_success_if_verbose("✅ Using connection string from APPLICATIONINSIGHTS_CONNECTION_STRING environment variable")
        else:
            logger.debug("Attempting to retrieve connection string from runtime context (AI Project client)")
        
        # Priority 2: Try to get from provided AI Project client (fallback)
        if not connection_string and ai_project_client:
            logger.debug("AI Project client provided, attempting connection string retrieval")
            try:
                # Try different methods to get the connection string from the client
                if hasattr(ai_project_client, 'telemetry'):
                    logger.debug("Client has telemetry attribute")
                    telemetry_methods = [method for method in dir(ai_project_client.telemetry) if not method.startswith('_')]
                    logger.debug(f"Available telemetry methods: {telemetry_methods}")
                    
                    if hasattr(ai_project_client.telemetry, 'get_connection_string'):
                        logger.debug("Trying telemetry.get_connection_string()")
                        connection_string = ai_project_client.telemetry.get_connection_string()
                        if connection_string:
                            log_success_if_verbose("Successfully retrieved connection string using get_connection_string()")
                    elif hasattr(ai_project_client.telemetry, 'get_application_insights_connection_string'):
                        logger.debug("Trying telemetry.get_application_insights_connection_string()")
                        connection_string = ai_project_client.telemetry.get_application_insights_connection_string()
                        if connection_string:
                            log_success_if_verbose("Successfully retrieved connection string using get_application_insights_connection_string()")
                else:
                    logger.debug("Client does not have telemetry attribute")
                    client_attrs = [attr for attr in dir(ai_project_client) if not attr.startswith('_')]
                    logger.debug(f"Available client attributes: {client_attrs}")
            except Exception as ex:
                logger.debug(f"Failed to get connection string from provided client: {ex}")
        
        elif project_endpoint:
            logger.debug(f"Creating new AI Project client with endpoint: {project_endpoint}")
            # Create a client with the provided endpoint
            try:
                # Use DefaultAzureCredential which works both locally and in Container Apps
                # Locally: uses Azure CLI or VS Code credentials
                # Container Apps: uses managed identity
                credential = DefaultAzureCredential()
                project_client = AIProjectClient(
                    credential=credential,
                    endpoint=project_endpoint
                )
                logger.debug("AI Project client created successfully")
                
                if hasattr(project_client, 'telemetry'):
                    logger.debug("New client has telemetry attribute")
                    if hasattr(project_client.telemetry, 'get_connection_string'):
                        logger.debug("Trying new client telemetry.get_connection_string()")
                        connection_string = project_client.telemetry.get_connection_string()
                        if connection_string:
                            log_success_if_verbose("Successfully retrieved connection string from new client using get_connection_string()")
                    elif hasattr(project_client.telemetry, 'get_application_insights_connection_string'):
                        logger.debug("Trying new client telemetry.get_application_insights_connection_string()")
                        connection_string = project_client.telemetry.get_application_insights_connection_string()
                        if connection_string:
                            log_success_if_verbose("Successfully retrieved connection string from new client using get_application_insights_connection_string()")
                else:
                    logger.debug("New client does not have telemetry attribute")
            except Exception as ex:
                logger.debug(f"Failed to create client with provided endpoint: {ex}")
        
        if connection_string:
            log_info_if_verbose(f"Connection string retrieved successfully (length: {len(connection_string)})")
        else:
            logger.debug("No connection string retrieved from runtime context")
    
    # Always try to use Azure Monitor (no console fallback)
    azure_monitor_enabled = setup_azure_monitor_tracing(connection_string)
    
    if not azure_monitor_enabled:
        logger.error("❌ Failed to initialize Azure Monitor tracing")
        logger.error("Tracing initialization failed - Application Insights connection is required")
        return False
    
    # Instrument OpenAI SDK
    instrument_openai()
    
    # Get tracer instance
    tracer = trace.get_tracer(__name__)
    _tracing_initialized = True
    
    log_success_if_verbose("✅ Tracing initialization completed successfully with Azure Monitor")
    return True


def initialize_tracing(
    connection_string: Optional[str] = None
) -> bool:
    """
    Initialize Azure Monitor tracing configuration (required, no console fallback).
    
    Connection string resolution priority:
    1. Explicit connection_string parameter (if provided)
    2. APPLICATIONINSIGHTS_CONNECTION_STRING environment variable (standard OpenTelemetry)
    3. Azure AI Project connections API (queries project for Application Insights connection)
    
    Args:
        connection_string: Optional Application Insights connection string
    
    Returns:
        bool: True if tracing was successfully initialized
    """
    global tracer, _tracing_initialized
    
    if _tracing_initialized:
        logger.debug("Tracing already initialized")
        return True
    
    log_info_if_verbose("🚀 Initializing Azure AI Foundry tracing (Azure Monitor only)...")
    
    # If no connection string provided, the setup function will check:
    # 1. APPLICATIONINSIGHTS_CONNECTION_STRING env var (via get_application_insights_connection_string)
    # 2. AI Project connections API (via get_application_insights_connection_string)
    azure_monitor_enabled = setup_azure_monitor_tracing(connection_string)
    
    if not azure_monitor_enabled:
        logger.error("❌ Failed to initialize Azure Monitor tracing")
        logger.error("Tracing initialization failed - Application Insights connection is required")
        return False
    
    # Instrument OpenAI SDK
    instrument_openai()
    
    # Get tracer instance
    tracer = trace.get_tracer(__name__)
    _tracing_initialized = True
    
    log_success_if_verbose("✅ Tracing initialization completed successfully with Azure Monitor")
    return True


def get_tracer() -> trace.Tracer:
    """
    Get the global tracer instance.
    Initializes tracing if not already done.
    
    Returns:
        trace.Tracer: OpenTelemetry tracer instance
    """
    global tracer
    
    if not _tracing_initialized:
        initialize_tracing()
    
    if tracer is None:
        tracer = trace.get_tracer(__name__)
    
    return tracer


def get_current_span() -> trace.Span:
    """
    Get the current active span from the OpenTelemetry context.
    
    Returns:
        trace.Span: The currently active span, or a non-recording span if none exists
    """
    return trace.get_current_span()


def trace_function(operation_name: str):
    """
    Decorator to automatically trace function execution.
    
    Args:
        operation_name: Name of the operation for the span
    
    Usage:
        @trace_function("process_questions")
        def my_function():
            pass
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            tracer = get_tracer()
            with tracer.start_as_current_span(operation_name) as span:
                try:
                    # Add function metadata
                    span.set_attribute("function.name", func.__name__)
                    span.set_attribute("function.module", func.__module__)
                    
                    # Execute function
                    result = func(*args, **kwargs)
                    
                    # Mark as successful
                    span.set_status(Status(StatusCode.OK))
                    return result
                    
                except Exception as ex:
                    # Record exception
                    span.record_exception(ex)
                    span.set_status(Status(StatusCode.ERROR, str(ex)))
                    raise
        return wrapper
    return decorator


def trace_async_function(operation_name: str):
    """
    Decorator to automatically trace async function execution.
    
    Args:
        operation_name: Name of the operation for the span
    
    Usage:
        @trace_async_function("async_process_questions")
        async def my_async_function():
            pass
    """
    def decorator(func):
        async def wrapper(*args, **kwargs):
            tracer = get_tracer()
            with tracer.start_as_current_span(operation_name) as span:
                try:
                    # Add function metadata
                    span.set_attribute("function.name", func.__name__)
                    span.set_attribute("function.module", func.__module__)
                    
                    # Execute async function
                    result = await func(*args, **kwargs)
                    
                    # Mark as successful
                    span.set_status(Status(StatusCode.OK))
                    return result
                    
                except Exception as ex:
                    # Record exception
                    span.record_exception(ex)
                    span.set_status(Status(StatusCode.ERROR, str(ex)))
                    raise
        return wrapper
    return decorator


def add_span_attributes(span: trace.Span, attributes: dict) -> None:
    """
    Add custom attributes to a span.
    
    Args:
        span: OpenTelemetry span
        attributes: Dictionary of attributes to add
    """
    try:
        for key, value in attributes.items():
            if value is not None:
                span.set_attribute(key, str(value))
    except Exception as ex:
        logger.warning(f"Failed to add span attributes: {ex}")


def record_agent_interaction(
    span: trace.Span,
    agent_id: str,
    thread_id: Optional[str] = None,
    message_count: Optional[int] = None,
    operation_type: str = "agent_interaction"
) -> None:
    """
    Record agent interaction metadata in the current span.
    
    Args:
        span: Current OpenTelemetry span
        agent_id: ID of the agent
        thread_id: Optional thread ID
        message_count: Optional message count
        operation_type: Type of operation
    """
    attributes = {
        "agent.id": agent_id,
        "agent.operation_type": operation_type
    }
    
    if thread_id:
        attributes["agent.thread_id"] = thread_id
    
    if message_count is not None:
        attributes["agent.message_count"] = message_count
    
    add_span_attributes(span, attributes)


def record_table_operation(
    span: trace.Span,
    table_name: str,
    operation: str,
    entity_count: Optional[int] = None,
    partition_key: Optional[str] = None
) -> None:
    """
    Record table operation metadata in the current span.
    
    Args:
        span: Current OpenTelemetry span
        table_name: Name of the table
        operation: Type of operation (query, upsert, delete, etc.)
        entity_count: Optional count of entities processed
        partition_key: Optional partition key
    """
    attributes = {
        "table.name": table_name,
        "table.operation": operation
    }
    
    if entity_count is not None:
        attributes["table.entity_count"] = entity_count
    
    if partition_key:
        attributes["table.partition_key"] = partition_key
    
    add_span_attributes(span, attributes)


def record_search_operation(
    span: trace.Span,
    index_name: str,
    query: Optional[str] = None,
    result_count: Optional[int] = None,
    operation: str = "search"
) -> None:
    """
    Record search operation metadata in the current span.
    
    Args:
        span: Current OpenTelemetry span
        index_name: Name of the search index
        query: Optional search query
        result_count: Optional result count
        operation: Type of operation
    """
    attributes = {
        "search.index_name": index_name,
        "search.operation": operation
    }
    
    if query:
        # Truncate long queries
        attributes["search.query"] = query[:500] if len(query) > 500 else query
    
    if result_count is not None:
        attributes["search.result_count"] = result_count
    
    add_span_attributes(span, attributes)


def record_llm_interaction(
    span: trace.Span,
    model: str,
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
    total_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    operation: str = "completion"
) -> None:
    """
    Record LLM interaction metadata including token usage.
    
    Args:
        span: Current OpenTelemetry span
        model: Model name/deployment
        prompt_tokens: Number of prompt tokens
        completion_tokens: Number of completion tokens
        total_tokens: Total tokens used
        temperature: Temperature setting
        max_tokens: Max tokens setting
        operation: Type of operation
    """
    attributes = {
        "gen_ai.system": "azure_openai",
        "gen_ai.operation.name": operation,
        "gen_ai.request.model": model,
    }
    
    if prompt_tokens is not None:
        attributes["gen_ai.usage.prompt_tokens"] = prompt_tokens
    
    if completion_tokens is not None:
        attributes["gen_ai.usage.completion_tokens"] = completion_tokens
    
    if total_tokens is not None:
        attributes["gen_ai.usage.total_tokens"] = total_tokens
    
    if temperature is not None:
        attributes["gen_ai.request.temperature"] = temperature
    
    if max_tokens is not None:
        attributes["gen_ai.request.max_tokens"] = max_tokens
    
    add_span_attributes(span, attributes)


def record_api_call(
    span: trace.Span,
    endpoint: str,
    method: str,
    status_code: Optional[int] = None,
    latency_ms: Optional[float] = None,
    request_size: Optional[int] = None,
    response_size: Optional[int] = None
) -> None:
    """
    Record API call metadata including latency and sizes.
    
    Args:
        span: Current OpenTelemetry span
        endpoint: API endpoint
        method: HTTP method
        status_code: HTTP status code
        latency_ms: Latency in milliseconds
        request_size: Request size in bytes
        response_size: Response size in bytes
    """
    attributes = {
        "http.method": method,
        "http.route": endpoint,
    }
    
    if status_code is not None:
        attributes["http.status_code"] = status_code
    
    if latency_ms is not None:
        attributes["http.duration_ms"] = latency_ms
    
    if request_size is not None:
        attributes["http.request.body.size"] = request_size
    
    if response_size is not None:
        attributes["http.response.body.size"] = response_size
    
    add_span_attributes(span, attributes)


def record_batch_operation(
    span: trace.Span,
    operation_name: str,
    batch_size: int,
    processed_count: Optional[int] = None,
    failed_count: Optional[int] = None,
    success_rate: Optional[float] = None
) -> None:
    """
    Record batch operation metrics.
    
    Args:
        span: Current OpenTelemetry span
        operation_name: Name of batch operation
        batch_size: Total items in batch
        processed_count: Number of items processed successfully
        failed_count: Number of items that failed
        success_rate: Success rate percentage
    """
    attributes = {
        "batch.operation": operation_name,
        "batch.size": batch_size,
    }
    
    if processed_count is not None:
        attributes["batch.processed_count"] = processed_count
    
    if failed_count is not None:
        attributes["batch.failed_count"] = failed_count
    
    if success_rate is not None:
        attributes["batch.success_rate"] = success_rate
    
    add_span_attributes(span, attributes)


def record_error_details(
    span: trace.Span,
    error_type: str,
    error_message: str,
    error_code: Optional[str] = None,
    is_retryable: Optional[bool] = None
) -> None:
    """
    Record detailed error information.
    
    Args:
        span: Current OpenTelemetry span
        error_type: Type/category of error
        error_message: Error message
        error_code: Optional error code
        is_retryable: Whether error is retryable
    """
    attributes = {
        "error.type": error_type,
        "error.message": error_message[:1000],  # Truncate long messages
    }
    
    if error_code:
        attributes["error.code"] = error_code
    
    if is_retryable is not None:
        attributes["error.retryable"] = is_retryable
    
    add_span_attributes(span, attributes)
    span.set_status(Status(StatusCode.ERROR, error_message[:256]))


# Initialize tracing on module import if environment variables are set AND verbose mode is enabled
if (os.getenv("AUTO_INITIALIZE_TRACING", "false").lower() == "true" and 
    os.getenv("AZURE_EXISTING_AIPROJECT_ENDPOINT") and 
    is_verbose_mode()):
    try:
        initialize_tracing()
    except Exception as ex:
        logger.debug(f"Failed to auto-initialize tracing: {ex}")