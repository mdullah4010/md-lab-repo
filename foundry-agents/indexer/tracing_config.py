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
from azure.identity import DefaultAzureCredential
from azure.monitor.opentelemetry import configure_azure_monitor
from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.trace import Status, StatusCode

# Configure logging properly
def _configure_logging():
    """Configure logging for the tracing module."""
    # Get or create logger
    logger = logging.getLogger(__name__)
    
    # Don't configure if already has handlers (avoid duplicate logs)
    if logger.handlers:
        return logger
    
    # Set logging level based on environment
    debug_mode = os.getenv("TRACING_DEBUG", "false").lower() in ("true", "1", "yes", "on")
    verbose_mode = os.getenv("APP_VERBOSE", "false").lower() in ("true", "1", "yes", "on", "debug")
    
    if debug_mode or verbose_mode:
        level = logging.DEBUG
    else:
        level = logging.INFO
    
    logger.setLevel(level)
    
    # Create console handler if none exists
    if not logger.handlers:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        
        # Create formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        console_handler.setFormatter(formatter)
        
        # Add handler to logger
        logger.addHandler(console_handler)
        
        # Prevent propagation to avoid duplicate logs if parent logger exists
        logger.propagate = False
    
    return logger

# Configure logging and create logger
logger = _configure_logging()

# Module loaded confirmation
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
        credential = DefaultAzureCredential(exclude_shared_token_cache_credential=True)
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
    Get Application Insights connection string from Azure AI Project endpoint only.
    
    Returns the connection string from Azure AI Project client using the project endpoint.
    """
    try:
        project_endpoint = os.getenv("AZURE_EXISTING_AIPROJECT_ENDPOINT")
        if not project_endpoint:
            logger.error("AZURE_EXISTING_AIPROJECT_ENDPOINT environment variable is required")
            return None
        
        logger.info(f"Retrieving Application Insights connection string from AI Project: {project_endpoint}")
        
        # Enable debug mode for detailed client exploration
        debug_ai_project_client(project_endpoint)
        
        credential = DefaultAzureCredential(exclude_shared_token_cache_credential=True)
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
                logger.info("✅ Retrieved connection string using telemetry.get_connection_string()")
            except Exception as ex:
                logger.debug(f"Method 1 failed: {ex}")
        
        # Method 2: Try the original method name
        if not connection_string and hasattr(project_client, 'telemetry') and hasattr(project_client.telemetry, 'get_application_insights_connection_string'):
            try:
                connection_string = project_client.telemetry.get_application_insights_connection_string()
                logger.info("✅ Retrieved connection string using telemetry.get_application_insights_connection_string()")
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
                                logger.info("✅ Retrieved connection string from project connections")
                                break
            except Exception as ex:
                logger.debug(f"Method 3 failed: {ex}")
        
        # Method 4: Try alternative connection properties
        if not connection_string:
            try:
                # Some AI projects might have the connection string in different attributes
                if hasattr(project_client, 'get_application_insights_connection_string'):
                    connection_string = project_client.get_application_insights_connection_string()
                    logger.info("✅ Retrieved connection string using direct method")
            except Exception as ex:
                logger.debug(f"Method 4 failed: {ex}")
        
        if connection_string:
            logger.info("✅ Successfully retrieved Application Insights connection string from Azure AI Project")
            return connection_string
        else:
            # Method 5: Fallback to environment variable
            env_connection_string = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
            if env_connection_string:
                logger.info("✅ Retrieved connection string from APPLICATIONINSIGHTS_CONNECTION_STRING environment variable")
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
            logger.info("✅ Retrieved connection string from APPLICATIONINSIGHTS_CONNECTION_STRING environment variable (fallback)")
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
    
    logger.debug(f"Connection String: {connection_string}")
    
    try:
        # Validate connection string format
        if not isinstance(connection_string, str) or not connection_string.strip():
            logger.error("Invalid connection string format: must be a non-empty string")
            return False
        
        # Check if connection string has required components
        conn_str_lower = connection_string.lower()
        if "instrumentationkey=" not in conn_str_lower and "ingestionendpoint=" not in conn_str_lower:
            logger.error("Invalid connection string: missing required components (InstrumentationKey or IngestionEndpoint)")
            logger.debug(f"Connection string preview: {connection_string[:100]}...")
            return False
        
        # Configure Azure Monitor with detailed error logging
        # Try using environment variable method as workaround for connection string validation issues
        logger.debug("Setting APPLICATIONINSIGHTS_CONNECTION_STRING environment variable")
        os.environ["APPLICATIONINSIGHTS_CONNECTION_STRING"] = connection_string
        
        logger.debug("Calling configure_azure_monitor() without explicit connection_string parameter...")
        configure_azure_monitor()
        logger.info("✅ Azure Monitor tracing configured successfully")
        return True
    except ValueError as ve:
        logger.error(f"❌ Connection string validation error: {ve}")
        logger.error(f"Error args: {ve.args}")
        logger.debug(f"Full connection string: {connection_string}")
        logger.debug(f"Exception traceback:", exc_info=True)
        return False
    except ImportError as ie:
        logger.error(f"❌ Azure Monitor OpenTelemetry library error: {ie}")
        logger.error("Make sure azure-monitor-opentelemetry is installed: pip install azure-monitor-opentelemetry")
        return False
    except Exception as ex:
        logger.error(f"❌ Failed to configure Azure Monitor tracing: {type(ex).__name__}: {ex}")
        logger.error(f"Exception args: {ex.args}")
        logger.debug(f"Full exception details:", exc_info=True)
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
        if capture_content == "true":
            logger.info("OpenAI message content capture is enabled")
        
        # Instrument OpenAI SDK
        OpenAIInstrumentor().instrument()
        logger.info("✅ OpenAI SDK instrumented successfully")
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
    
    logger.info("🚀 Initializing Azure AI Foundry tracing with runtime context (Azure Monitor only)...")
    
    # Try to get connection string from provided context
    if not connection_string:
        logger.debug("Attempting to retrieve connection string from runtime context")
        
        if ai_project_client:
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
                            logger.info("Successfully retrieved connection string using get_connection_string()")
                    elif hasattr(ai_project_client.telemetry, 'get_application_insights_connection_string'):
                        logger.debug("Trying telemetry.get_application_insights_connection_string()")
                        connection_string = ai_project_client.telemetry.get_application_insights_connection_string()
                        if connection_string:
                            logger.info("Successfully retrieved connection string using get_application_insights_connection_string()")
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
                credential = DefaultAzureCredential(exclude_shared_token_cache_credential=True)
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
                            logger.info("Successfully retrieved connection string from new client using get_connection_string()")
                    elif hasattr(project_client.telemetry, 'get_application_insights_connection_string'):
                        logger.debug("Trying new client telemetry.get_application_insights_connection_string()")
                        connection_string = project_client.telemetry.get_application_insights_connection_string()
                        if connection_string:
                            logger.info("Successfully retrieved connection string from new client using get_application_insights_connection_string()")
                else:
                    logger.debug("New client does not have telemetry attribute")
            except Exception as ex:
                logger.debug(f"Failed to create client with provided endpoint: {ex}")
        
        if connection_string:
            logger.info(f"Connection string retrieved successfully (length: {len(connection_string)})")
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
    
    logger.info("✅ Tracing initialization completed successfully with Azure Monitor")
    return True


def initialize_tracing(
    connection_string: Optional[str] = None
) -> bool:
    """
    Initialize Azure Monitor tracing configuration (required, no console fallback).
    
    Args:
        connection_string: Optional Application Insights connection string
    
    Returns:
        bool: True if tracing was successfully initialized
    """
    global tracer, _tracing_initialized
    
    if _tracing_initialized:
        logger.debug("Tracing already initialized")
        return True
    
    logger.info("🚀 Initializing Azure AI Foundry tracing (Azure Monitor only)...")
    
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
    
    logger.info("✅ Tracing initialization completed successfully with Azure Monitor")
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


# Initialize tracing on module import if environment variables are set
if (os.getenv("AUTO_INITIALIZE_TRACING", "false").lower() == "true" and 
    os.getenv("AZURE_EXISTING_AIPROJECT_ENDPOINT")):
    try:
        initialize_tracing()
    except Exception as ex:
        logger.debug(f"Failed to auto-initialize tracing: {ex}")
