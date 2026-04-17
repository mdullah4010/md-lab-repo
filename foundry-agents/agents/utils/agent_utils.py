"""
Agent Lifecycle Utility Functions

This module consolidates common agent management functions used across multiple agents:
- Agent discovery (find existing agents)
- Agent cleanup and deletion
- AI Project client creation
- Search tool configuration
- Thread management
- Run processing

These utilities reduce code duplication and improve maintainability.

Usage:
    from agents.utils.agent_utils import (
        find_existing_agent,
        cleanup_agent,
        create_ai_project_client,
        configure_search_tool,
        create_project_index,
        wait_for_run_completion,
        collect_run_response,
        AgentClientManager
    )
"""

import os
import asyncio
import time
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass

from azure.identity.aio import DefaultAzureCredential as AsyncDefaultAzureCredential
from azure.identity import DefaultAzureCredential as SyncDefaultAzureCredential
from azure.ai.projects.aio import AIProjectClient
from azure.ai.projects.models import ConnectionType
from azure.ai.agents.models import AzureAISearchTool, AzureAISearchQueryType
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

# Import tracing configuration
from agents.tracing_config import (
    get_tracer,
    trace_async_function,
    add_span_attributes
)

# Import logging configuration
from agents.logging_config import get_logger

logger = get_logger(__name__)


# =============================================================================
# CONFIGURATION DATACLASSES
# =============================================================================

@dataclass
class AgentConfig:
    """Configuration for an agent."""
    name: str
    model_deployment: str
    instructions: str
    temperature: float = 0.1
    tools: Optional[List] = None
    tool_resources: Optional[Any] = None
    search_tool_config: Optional["SearchToolConfig"] = None  # Optional search tool configuration


@dataclass
class SearchToolConfig:
    """Configuration for Azure AI Search tool."""
    index_name: str
    query_type: AzureAISearchQueryType = AzureAISearchQueryType.SEMANTIC
    top_k: int = 20
    filter: str = ""
    connection_id: Optional[str] = None
    semantic_config: Optional[str] = None
    use_vector: bool = False
    field_mapping: Optional[Dict[str, Any]] = None  # Custom field mapping for project index

@dataclass
class FilteredSearchToolResult:
    """
    Result from create_filtered_search_tool containing both tool definitions and resources.
   
    Both definitions and resources must be passed to runs.create() for the filter to work:
    - definitions: Tool definition list for the `tools` parameter
    - resources: Tool resources for the `tool_resources` parameter
    - filter_expression: The OData filter expression that was configured
    """
    definitions: List
    resources: Optional[Any]
    filter_expression: str

# =============================================================================
# ENVIRONMENT CONFIGURATION
# =============================================================================

def get_ai_project_endpoint() -> str:
    """
    Get the Azure AI Project endpoint from environment variables.
    
    Returns:
        AI Project endpoint URL
    
    Raises:
        ValueError: If endpoint is not configured
    """
    endpoint = os.getenv("AZURE_EXISTING_AIPROJECT_ENDPOINT")
    if not endpoint:
        raise ValueError("AZURE_EXISTING_AIPROJECT_ENDPOINT environment variable is not set")
    return endpoint


def get_model_deployment_name() -> str:
    """
    Get the model deployment name from environment variables.
    
    Returns:
        Model deployment name
    
    Raises:
        ValueError: If deployment name is not configured
    """
    deployment = (
        os.getenv("AZURE_AI_AGENT_DEPLOYMENT_NAME") or
        os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT") or
        os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
    )
    if not deployment:
        raise ValueError("AZURE_AI_AGENT_DEPLOYMENT_NAME environment variable is not set")
    return deployment


def get_search_config() -> Dict[str, Optional[str]]:
    """
    Get Azure Search configuration from environment variables.
    
    Uses managed identity for authentication (no API key needed).
    
    Returns:
        Dict with search configuration
    """
    return {
        "endpoint": os.getenv("AZURE_SEARCH_ENDPOINT"),
        "semantic_config": os.getenv("AZURE_SEARCH_SEMANTIC_CONFIG"),
        "filter": os.getenv("AZURE_AI_SEARCH_FILTER")
    }


# =============================================================================
# CLIENT MANAGEMENT
# =============================================================================

class AgentClientManager:
    """
    Context manager for Azure AI Project client lifecycle.
    
    Provides proper async context management for credentials and clients.
    Supports accepting an existing client (for when called from orchestrator).
    
    Usage:
        # Create new client
        async with AgentClientManager() as manager:
            client = manager.client
            # Use client...
        
        # Use existing client (won't close it on exit)
        async with AgentClientManager(existing_client=orchestrator_client) as manager:
            client = manager.client
            # Use client...
    """
    
    def __init__(
        self, 
        endpoint: Optional[str] = None,
        existing_client: Optional[AIProjectClient] = None
    ):
        """
        Initialize the client manager.
        
        Args:
            endpoint: Optional AI Project endpoint (uses env var if not provided)
            existing_client: Optional existing client to use (won't be closed on exit)
        """
        self._existing_client = existing_client
        self._owns_client = existing_client is None
        self.endpoint = endpoint or (get_ai_project_endpoint() if self._owns_client else None)
        self._credential: Optional[AsyncDefaultAzureCredential] = None
        self._client: Optional[AIProjectClient] = existing_client
    
    @property
    def client(self) -> AIProjectClient:
        """Get the AI Project client."""
        if self._client is None:
            raise RuntimeError("Client not initialized. Use async with AgentClientManager() as manager:")
        return self._client
    
    @property
    def credential(self) -> Optional[AsyncDefaultAzureCredential]:
        """Get the credential (may be None if using existing client)."""
        return self._credential
    
    @property
    def owns_client(self) -> bool:
        """Return True if this manager owns the client (and should close it)."""
        return self._owns_client
    
    async def __aenter__(self) -> "AgentClientManager":
        """Enter the async context."""
        if self._owns_client:
            self._credential = AsyncDefaultAzureCredential(exclude_shared_token_cache_credential=True)
            self._client = AIProjectClient(credential=self._credential, endpoint=self.endpoint)
            logger.debug(f"Created AIProjectClient for endpoint: {self.endpoint[:50]}...")
        else:
            logger.debug("Using existing AIProjectClient")
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit the async context and cleanup resources (only if we own them)."""
        if self._owns_client:
            if self._client:
                try:
                    await self._client.close()
                except Exception as ex:
                    logger.debug(f"Error closing client: {ex}")
            if self._credential:
                try:
                    await self._credential.close()
                except Exception as ex:
                    logger.debug(f"Error closing credential: {ex}")
        return False


async def create_ai_project_client(
    endpoint: Optional[str] = None
) -> Tuple[AIProjectClient, AsyncDefaultAzureCredential]:
    """
    Create an AI Project client with credentials.
    
    Note: Caller is responsible for closing the client and credential.
    Prefer using AgentClientManager for automatic cleanup.
    
    Args:
        endpoint: Optional AI Project endpoint
    
    Returns:
        Tuple of (AIProjectClient, credential)
    """
    if not endpoint:
        endpoint = get_ai_project_endpoint()
    
    credential = AsyncDefaultAzureCredential(exclude_shared_token_cache_credential=True)
    client = AIProjectClient(credential=credential, endpoint=endpoint)
    
    logger.debug(f"Created AIProjectClient for endpoint: {endpoint[:50]}...")
    return client, credential


# =============================================================================
# AGENT DISCOVERY
# =============================================================================

@trace_async_function("find_existing_agent")
async def find_existing_agent(
    client: AIProjectClient,
    agent_name: str,
    application_id: Optional[str] = None
) -> Optional[Any]:
    """
    Find an existing agent by name.
    
    This is a generic implementation that can be used by any agent type.
    
    Args:
        client: AI Project client
        agent_name: Full name of the agent to find
        application_id: Optional application ID for logging
    
    Returns:
        Agent object if found, None otherwise
    """
    tracer = get_tracer()
    
    with tracer.start_as_current_span("agent_search") as span:
        add_span_attributes(span, {
            "agent.search.name": agent_name,
            "application_id": application_id or "N/A",
            "operation": "search_existing_agent"
        })
        
        try:
            logger.debug(f"Looking for existing agent: {agent_name}")
            span.add_event("searching_agents", {"agent_name": agent_name})
            
            # List all agents and find the one with matching name
            agents = client.agents.list_agents()
            agent_count = 0
            
            async for agent in agents:
                agent_count += 1
                if hasattr(agent, 'name') and agent.name == agent_name:
                    logger.debug(f"Found existing agent: {agent.id} with name: {agent.name}")
                    add_span_attributes(span, {
                        "agent.found": True,
                        "agent.id": agent.id,
                        "agents_searched": agent_count
                    })
                    span.add_event("existing_agent_found", {
                        "agent_id": agent.id,
                        "agent_name": agent.name,
                        "agents_searched": agent_count
                    })
                    span.set_status(Status(StatusCode.OK))
                    
                    # Store agent ID in environment for backward compatibility
                    os.environ["AZURE_EXISTING_AGENT_ID"] = agent.id
                    return agent
            
            logger.debug(f"No existing agent found with name: {agent_name}")
            add_span_attributes(span, {
                "agent.found": False,
                "agents_searched": agent_count
            })
            span.add_event("no_existing_agent", {
                "agent_name": agent_name,
                "agents_searched": agent_count
            })
            span.set_status(Status(StatusCode.OK))
            return None
            
        except Exception as ex:
            logger.error(f"Error searching for existing agent: {ex}")
            span.record_exception(ex)
            span.set_status(Status(StatusCode.ERROR, str(ex)))
            span.add_event("agent_search_failed", {
                "agent_name": agent_name,
                "error_message": str(ex)
            })
            return None


def build_agent_name(agent_type: str, application_id: str) -> str:
    """
    Build a standardized agent name.
    
    Args:
        agent_type: Type of agent (e.g., 'ASRAgent', 'Design-Agent', 'Responder-Agent')
        application_id: Application ID
    
    Returns:
        Formatted agent name
    """
    # Handle different naming conventions
    if '-' in agent_type:
        return f"{agent_type}-{application_id}"
    else:
        return f"{agent_type}{application_id}"


# =============================================================================
# AGENT CLEANUP
# =============================================================================

@trace_async_function("cleanup_agent")
async def cleanup_agent(
    application_id: str,
    agent_type: str,
    thread_id: Optional[str] = None,
    client: Optional[AIProjectClient] = None,
    agent_id: Optional[str] = None,
    find_existing_fn: Optional[callable] = None
) -> Dict[str, Any]:
    """
    Clean up an agent and all associated threads.
    
    Generic implementation that can be used by any agent type.
    
    Args:
        application_id: The application ID to clean up
        agent_type: Type of agent (for naming pattern, e.g., 'ASRAgent', 'Design-Agent')
        thread_id: Optional thread ID to delete
        client: Optional AI Project client (creates new if not provided)
        agent_id: Optional specific agent ID to delete
        find_existing_fn: Optional custom function to find existing agent
    
    Returns:
        dict: Result containing status and cleanup details
    """
    tracer = get_tracer()
    
    with tracer.start_as_current_span("agent_cleanup") as cleanup_span:
        add_span_attributes(cleanup_span, {
            "application_id": application_id,
            "agent_type": agent_type,
            "agent_id_provided": agent_id is not None,
            "thread_id": thread_id or "N/A"
        })
        
        logger.debug(f"Starting cleanup for {agent_type} agent (application_id: {application_id})")
        
        # Create client if not provided
        client_created = False
        if client is None:
            try:
                creds = AsyncDefaultAzureCredential(exclude_shared_token_cache_credential=True)
                endpoint = get_ai_project_endpoint()
                client = AIProjectClient(credential=creds, endpoint=endpoint)
                client_created = True
            except Exception as ex:
                cleanup_span.record_exception(ex)
                cleanup_span.set_status(Status(StatusCode.ERROR, str(ex)))
                return {"status": "error", "message": f"Failed to create client: {str(ex)}"}
        
        try:
            # Find the agent if agent_id not provided
            if not agent_id:
                agent_name = build_agent_name(agent_type, application_id)
                
                if find_existing_fn:
                    existing_agent = await find_existing_fn(client, application_id)
                else:
                    existing_agent = await find_existing_agent(client, agent_name, application_id)
                
                if existing_agent:
                    agent_id = existing_agent.id
                else:
                    logger.debug(f"No {agent_type} agent found for application_id: {application_id}")
                    add_span_attributes(cleanup_span, {"cleanup.agent_found": False})
                    cleanup_span.set_status(Status(StatusCode.OK))
                    return {"status": "success", "message": "No agent found to clean up"}
            
            add_span_attributes(cleanup_span, {
                "cleanup.agent_id": agent_id,
                "cleanup.agent_found": True
            })
            
            logger.debug(f"Cleaning up {agent_type} agent: {agent_id}")
            
            # Delete the thread if provided
            threads_deleted = 0
            if thread_id:
                try:
                    await client.agents.threads.delete(thread_id=thread_id)
                    threads_deleted += 1
                    logger.debug(f"Deleted thread: {thread_id}")
                except Exception as threads_ex:
                    logger.warning(f"Warning: Error deleting thread: {str(threads_ex)}")
                    cleanup_span.add_event("threads_cleanup_warning", {"error": str(threads_ex)})
            
            add_span_attributes(cleanup_span, {"cleanup.threads_deleted": threads_deleted})
            
            # Delete the agent
            try:
                await client.agents.delete_agent(agent_id)
                logger.debug(f"Successfully deleted {agent_type} agent: {agent_id}")
                add_span_attributes(cleanup_span, {"cleanup.agent_deleted": True})
            except Exception as agent_ex:
                cleanup_span.record_exception(agent_ex)
                cleanup_span.set_status(Status(StatusCode.ERROR, str(agent_ex)))
                return {
                    "status": "error",
                    "message": f"Failed to delete agent: {str(agent_ex)}",
                    "threads_deleted": threads_deleted
                }
            
            cleanup_span.set_status(Status(StatusCode.OK))
            return {
                "status": "success",
                "message": "Cleanup completed successfully",
                "agent_id": agent_id,
                "threads_deleted": threads_deleted
            }
            
        except Exception as ex:
            cleanup_span.record_exception(ex)
            cleanup_span.set_status(Status(StatusCode.ERROR, str(ex)))
            logger.error(f"Error during cleanup: {str(ex)}")
            return {"status": "error", "message": str(ex)}
        finally:
            # Close client if we created it
            if client_created and hasattr(client, 'close'):
                try:
                    await client.close()
                except Exception:
                    pass


# =============================================================================
# SEARCH TOOL CONFIGURATION
# =============================================================================

async def get_search_connection(client: AIProjectClient) -> Optional[Any]:
    """
    Get the default Azure AI Search connection.
    
    Args:
        client: AI Project client
    
    Returns:
        Connection object if found, None otherwise
    """
    try:
        default_conn = await client.connections.get_default(ConnectionType.AZURE_AI_SEARCH)
        if default_conn and hasattr(default_conn, 'id'):
            logger.debug(f"Found Azure AI Search connection: {default_conn.id}")
            return default_conn
        logger.warning("Could not get Azure AI Search connection")
        return None
    except Exception as ex:
        logger.warning(f"Error getting search connection: {ex}")
        return None


async def create_project_index(
    client: AIProjectClient,
    index_name: str,
    connection_name: str,
    field_mapping: Optional[Dict[str, Any]] = None,
    version: str = "1"
) -> Optional[Any]:
    """
    Create or update a project index for Azure AI Search.
    
    Args:
        client: AI Project client
        index_name: Name for the index
        connection_name: Search connection name
        field_mapping: Optional field mapping configuration
        version: Index version
    
    Returns:
        Project index object if successful, None otherwise
    """
    tracer = get_tracer()
    
    with tracer.start_as_current_span("create_project_index") as span:
        project_index_name = f"project-index-{index_name}"
        
        add_span_attributes(span, {
            "project_index_name": project_index_name,
            "index_name": index_name,
            "version": version
        })
        
        # Default field mapping
        if field_mapping is None:
            field_mapping = {
                "contentFields": ["content", "metadata"],
                "titleField": "title",
                "urlField": "source",
                "vectorFields": ["contentVector"]
            }
        
        try:
            logger.debug(f"Creating/updating project index '{project_index_name}'")
            
            project_index = await client.indexes.create_or_update(
                name=project_index_name,
                version=version,
                index={
                    "connectionName": connection_name,
                    "indexName": index_name,
                    "type": "AzureSearch",
                    "fieldMapping": field_mapping
                }
            )
            
            logger.debug(f"Created project index: {project_index_name}")
            span.set_status(Status(StatusCode.OK))
            return project_index
            
        except Exception as ex:
            logger.warning(f"Failed to create project index: {ex}")
            span.record_exception(ex)
            span.set_status(Status(StatusCode.ERROR, str(ex)))
            return None


async def configure_search_tool(
    client: AIProjectClient,
    config: SearchToolConfig,
    use_project_index: bool = True
) -> Optional[AzureAISearchTool]:
    """
    Configure Azure AI Search tool for an agent.
    
    Supports both project index approach and legacy connection-based approach.
    
    Args:
        client: AI Project client
        config: Search tool configuration
        use_project_index: Whether to use project index approach (recommended)
    
    Returns:
        AzureAISearchTool if configured successfully, None otherwise
    """
    tracer = get_tracer()
    
    with tracer.start_as_current_span("configure_search_tool") as span:
        add_span_attributes(span, {
            "search.index_name": config.index_name,
            "search.query_type": str(config.query_type),
            "search.use_project_index": use_project_index
        })
        
        try:
            # Get default search connection
            default_conn = await get_search_connection(client)
            if not default_conn:
                logger.warning("No default Azure AI Search connection found")
                return None
            
            conn_id = default_conn.id
            
            # Get connection name
            connection_name = "default"
            if hasattr(default_conn, 'name'):
                connection_name = default_conn.name
            elif hasattr(default_conn, 'connection_name'):
                connection_name = default_conn.connection_name
            elif hasattr(default_conn, 'id'):
                connection_name = default_conn.id
            
            logger.debug(f"Using connection name: {connection_name}")
            
            if use_project_index:
                # Create project index with optional custom field mapping
                project_index = await create_project_index(
                    client=client,
                    index_name=config.index_name,
                    connection_name=connection_name,
                    field_mapping=config.field_mapping  # Pass custom field mapping if provided
                )
                
                if project_index:
                    # Create search tool using project index approach
                    search_tool = AzureAISearchTool(
                        index_connection_id="",
                        index_name="",
                        query_type=config.query_type,
                        filter=config.filter,
                        top_k=config.top_k,
                        index_asset_id=f"{project_index.name}/versions/{project_index.version}"
                    )
                    logger.debug(f"Configured Azure AI Search tool with project index '{project_index.name}'")
                    span.set_status(Status(StatusCode.OK))
                    return search_tool
                else:
                    logger.warning("Failed to create project index, falling back to legacy approach")
            
            # Fallback to legacy connection-based approach
            search_tool = AzureAISearchTool(
                index_connection_id=conn_id,
                index_name=config.index_name,
                query_type=config.query_type,
                filter=config.filter,
                top_k=config.top_k
            )
            logger.debug("Configured Azure AI Search tool with legacy approach")
            span.set_status(Status(StatusCode.OK))
            return search_tool
            
        except Exception as ex:
            logger.error(f"Error configuring search tool: {ex}")
            span.record_exception(ex)
            span.set_status(Status(StatusCode.ERROR, str(ex)))
            return None

async def create_filtered_search_tool(
    client: AIProjectClient,
    partition_key: str,
    filter_expression: str,
    query_type: AzureAISearchQueryType = AzureAISearchQueryType.VECTOR_SEMANTIC_HYBRID,
    top_k: int = 20
) -> FilteredSearchToolResult:
    """
    Create a search tool with a custom filter for run-level override.
   
    This function uses configure_search_tool with the connection-based approach
    (use_project_index=False) which properly supports OData filter expressions.
    The project index approach does NOT respect filters properly.
   
    Args:
        client: AI Project client (required to get search connection)
        partition_key: The application/partition key (used as index name)
        filter_expression: OData filter expression (e.g., "appId eq '12345' and metadata eq 'dependency'")
        query_type: Search query type (default: VECTOR_SEMANTIC_HYBRID)
        top_k: Number of results to return (default: 20)
   
    Returns:
        FilteredSearchToolResult containing:
        - definitions: Tool definitions for runs.create(tools=...)
        - resources: Tool resources for runs.create(tool_resources=...)
        - filter_expression: The configured filter expression
   
    Usage:
        # Create a custom-filtered search tool
        filtered_result = await create_filtered_search_tool(
            client=ai_client,
            partition_key="app123",
            filter_expression="appId eq 'app123' and metadata eq 'dependency'"
        )
       
        # Pass BOTH definitions AND resources to execute_run_with_retry
        result = await execute_run_with_retry(
            client=client,
            agent_id=agent_id,
            thread_id=thread_id,
            prompt="Find all servers...",
            tools=filtered_result.definitions,
            tool_resources=filtered_result.resources
        )
    """
    try:
        # Create SearchToolConfig with the filter expression
        search_config = SearchToolConfig(
            index_name=partition_key,
            query_type=query_type,
            top_k=top_k,
            filter=filter_expression
        )
       
        # Use configure_search_tool with use_project_index=False
        # The connection-based approach properly respects OData filters
        search_tool = await configure_search_tool(
            client=client,
            config=search_config,
            use_project_index=False  # CRITICAL: Connection-based approach supports filters
        )
       
        if search_tool:
            # Enhanced logging for filter debugging
            logger.info(
                f"🔍 FILTER DEBUG: Created filtered search tool (connection-based)\n"
                f"   Index Name: {partition_key}\n"
                f"   Filter Expression: {filter_expression}\n"
                f"   Query Type: {query_type}\n"
                f"   Top K: {top_k}\n"
                f"   Has Resources: {search_tool.resources is not None}"
            )
           
            return FilteredSearchToolResult(
                definitions=search_tool.definitions,
                resources=search_tool.resources,
                filter_expression=filter_expression
            )
        else:
            logger.warning("configure_search_tool returned None, filter may not work")
            return FilteredSearchToolResult(
                definitions=[],
                resources=None,
                filter_expression=filter_expression
            )
       
    except Exception as ex:
        logger.error(f"Error creating filtered search tool: {ex}")
        return FilteredSearchToolResult(
            definitions=[],
            resources=None,
            filter_expression=filter_expression
        )

# =============================================================================
# SEARCH INDEX UTILITIES
# =============================================================================

def check_index_exists(index_name: str) -> Optional[bool]:
    """
    Check if a search index exists.
    
    Args:
        index_name: Name of the index to check
    
    Returns:
        True if exists, False if not found, None if check failed
    """
    try:
        search_config = get_search_config()
        svc_endpoint = search_config.get("endpoint")
        
        if not svc_endpoint:
            logger.debug("No search endpoint configured for index validation")
            return None
        
        from azure.search.documents.indexes import SearchIndexClient
        
        credential = SyncDefaultAzureCredential(exclude_shared_token_cache_credential=True)
        
        sic = SearchIndexClient(endpoint=svc_endpoint, credential=credential)
        sic.get_index(index_name)
        return True
        
    except Exception as ex:
        if ex.__class__.__name__ == "ResourceNotFoundError":
            return False
        logger.warning(f"Search index existence check failed: %s", ex)
        return None


def check_semantic_config(index_name: str) -> Optional[str]:
    """
    Check if semantic search is configured for an index.
    
    Args:
        index_name: Name of the index to check
    
    Returns:
        Semantic config name if available, None otherwise
    """
    try:
        search_config = get_search_config()
        svc_endpoint = search_config.get("endpoint")
        sem_config = search_config.get("semantic_config")
        
        if not svc_endpoint:
            logger.debug("No search endpoint configured for semantic config check")
            return sem_config
        
        from azure.search.documents.indexes import SearchIndexClient
        
        credential = SyncDefaultAzureCredential(exclude_shared_token_cache_credential=True)
        
        sic = SearchIndexClient(endpoint=svc_endpoint, credential=credential)
        index = sic.get_index(index_name)
        
        if hasattr(index, 'semantic_search') and index.semantic_search:
            configs = getattr(index.semantic_search, 'configurations', [])
            if configs and len(configs) > 0:
                config_name = getattr(configs[0], 'name', sem_config) or sem_config
                logger.debug(f"Found semantic configuration: {config_name}")
                return config_name
        
        logger.debug("No semantic search configuration found in index")
        return sem_config
        
    except Exception as ex:
        logger.warning(f"Semantic config check failed: {ex}")
        return get_search_config().get("semantic_config")


def check_vector_fields(index_name: str) -> bool:
    """
    Check if an index has vector fields.
    
    Args:
        index_name: Name of the index to check
    
    Returns:
        True if vector fields exist, False otherwise
    """
    try:
        search_config = get_search_config()
        svc_endpoint = search_config.get("endpoint")
        
        if not svc_endpoint:
            logger.debug("No search endpoint configured for vector field check")
            return False
        
        from azure.search.documents.indexes import SearchIndexClient
        
        credential = SyncDefaultAzureCredential(exclude_shared_token_cache_credential=True)
        
        sic = SearchIndexClient(endpoint=svc_endpoint, credential=credential)
        index = sic.get_index(index_name)
        
        if hasattr(index, 'fields'):
            for field in index.fields:
                if (hasattr(field, 'name') and field.name == 'contentVector' and
                    hasattr(field, 'type') and 'Collection(Edm.Single)' in str(field.type) and
                    hasattr(field, 'vector_search_dimensions') and 
                    field.vector_search_dimensions > 0):
                    logger.debug(f"Found vector field '{field.name}' with {field.vector_search_dimensions} dimensions")
                    return True
        
        logger.debug("No vector fields found in index")
        return False
        
    except Exception as ex:
        logger.warning(f"Vector field check failed: {ex}")
        return False


def determine_optimal_query_type(index_name: str) -> AzureAISearchQueryType:
    """
    Determine the optimal query type based on index capabilities.
    
    Args:
        index_name: Name of the index to analyze
    
    Returns:
        Optimal AzureAISearchQueryType
    """
    semantic_config = check_semantic_config(index_name)
    has_vector_fields = check_vector_fields(index_name)
    
    if has_vector_fields and semantic_config:
        logger.debug("Using VECTOR_SEMANTIC_HYBRID search (best quality)")
        return AzureAISearchQueryType.VECTOR_SEMANTIC_HYBRID
    elif semantic_config:
        logger.debug("Using SEMANTIC search")
        return AzureAISearchQueryType.SEMANTIC
    elif has_vector_fields:
        logger.debug("Using VECTOR search")
        return AzureAISearchQueryType.VECTOR
    else:
        logger.debug("Using SIMPLE keyword search")
        return AzureAISearchQueryType.SIMPLE

# =============================================================================
# METADATA FILTERING UTILITIES (Direct Filter Approach)
# =============================================================================
 
def build_metadata_filter_expression(
    app_id: str,
    metadata_criteria: Optional[Dict[str, str]] = None
) -> str:
    """
    Build an OData filter expression for direct metadata filtering.
   
    Since metadata is stored as a JSON string (e.g., '{"category":"infra","document_state":"final"}'),
    this function builds search.ismatch expressions to filter by key-value pairs directly.
   
    This is more efficient than the two-step path-based filtering approach as it:
    1. Eliminates the need for a separate query to find matching document paths
    2. Reduces API calls from 2 to 1
    3. Keeps filter size constant regardless of document count
   
    Args:
        app_id: Application ID for the base filter
        metadata_criteria: Dictionary of metadata key-value pairs to filter by
                          Example: {"category": "infra", "document_state": "final"}
   
    Returns:
        OData filter expression using search.ismatch on metadata field
   
    Example:
        filter_expr = build_metadata_filter_expression(
            app_id="2001app",
            metadata_criteria={"category": "infra", "document_state": "final"}
        )
        # Returns: "appId eq '2001app' and search.ismatch('\"category\":\"infra\"', 'metadata') and search.ismatch('\"document_state\":\"final\"', 'metadata')"
    """
    filter_expression = f"appId eq '{app_id}'"
   
    if not metadata_criteria:
        return filter_expression
   
    # Build filter expression with metadata criteria using search.ismatch for JSON key-value pairs
    metadata_filters = []
    for key, value in metadata_criteria.items():
        # Match JSON key-value pattern: "key":"value" within metadata field
        # Using escaped quotes to match the JSON structure stored in metadata
        metadata_filters.append(f"search.ismatch('\"{key}\":\"{value}\"', 'metadata')")
   
    if metadata_filters:
        filter_expression = f"appId eq '{app_id}' and " + " and ".join(metadata_filters)
   
    return filter_expression

def check_index_has_metadata(index_name: str, app_id: str) -> Tuple[bool, List[str]]:
    """
    Check if the search index has any documents with metadata for the given app.
   
    Args:
        index_name: Name of the search index
        app_id: Application ID to check
   
    Returns:
        Tuple of (has_metadata: bool, sample_keys: list of metadata keys found)
    """
    try:
        search_config = get_search_config()
        svc_endpoint = search_config.get("endpoint")
       
        if not svc_endpoint:
            return False, []
       
        from azure.search.documents import SearchClient
        import json
       
        credential = SyncDefaultAzureCredential(exclude_shared_token_cache_credential=True)
       
        search_client = SearchClient(
            endpoint=svc_endpoint,
            index_name=index_name,
            credential=credential
        )
       
        # Search for documents with non-empty metadata
        results = search_client.search(
            search_text="*",
            filter=f"appId eq '{app_id}'",
            select=["metadata"],
            top=50
        )
       
        metadata_keys = set()
        has_metadata = False
       
        for result in results:
            metadata_str = result.get("metadata", "")
            if metadata_str and metadata_str.strip():
                has_metadata = True
                try:
                    metadata_dict = json.loads(metadata_str)
                    if isinstance(metadata_dict, dict):
                        metadata_keys.update(metadata_dict.keys())
                except json.JSONDecodeError:
                    pass
       
        logger.debug(f"Index '{index_name}' has_metadata={has_metadata}, keys={list(metadata_keys)}")
        return has_metadata, list(metadata_keys)
       
    except Exception as ex:
        logger.warning(f"Error checking index metadata: {ex}")
        return False, []

# =============================================================================
# RUN PROCESSING UTILITIES
# =============================================================================

async def wait_for_run_completion(
    client: AIProjectClient,
    thread_id: str,
    run_id: str,
    timeout_seconds: int = 300,
    poll_interval: float = 2.0,
    handle_transport_errors: bool = False
) -> Tuple[str, Optional[Any], Optional[str]]:
    """
    Wait for an agent run to complete.
    
    Args:
        client: AI Project client
        thread_id: Thread ID
        run_id: Run ID
        timeout_seconds: Maximum wait time
        poll_interval: Time between status checks
        handle_transport_errors: If True, returns transport_error status instead of raising
    
    Returns:
        Tuple of (status, run_object, error_message)
        - status: 'completed', 'failed', 'cancelled', 'succeeded', 'timeout', or 'transport_error'
        - run_object: The run object (may be None on transport error)
        - error_message: Error message if applicable, None otherwise
    """
    terminal_statuses = {"completed", "failed", "cancelled", "succeeded"}
    poll_start = time.time()
    last_status = None
    current = None
    
    while True:
        try:
            current = await client.agents.runs.get(thread_id=thread_id, run_id=run_id)
            last_status = getattr(current, 'status', None)
            
            if last_status in terminal_statuses:
                break
            
            if time.time() - poll_start > timeout_seconds:
                logger.error(f"Run timeout after {timeout_seconds}s")
                return "timeout", current, f"Timeout after {timeout_seconds}s"
            
            await asyncio.sleep(poll_interval)
            
        except Exception as poll_ex:
            # Handle HTTP transport closure during polling
            if handle_transport_errors:
                error_str = str(poll_ex).lower()
                if "transport" in error_str or "closed" in error_str:
                    wait_time = time.time() - poll_start
                    error_msg = f"HTTP transport closed after {wait_time:.1f}s: {poll_ex}"
                    logger.error(error_msg)
                    return "transport_error", current, error_msg
            raise  # Re-raise if not handling transport errors or different error type
    
    return last_status, current, None


# =============================================================================
# AGENT CREATION UTILITIES
# =============================================================================

@dataclass
class AgentCreationResult:
    """Result of agent creation with search tool."""
    agent: Any
    search_tool: Optional[AzureAISearchTool] = None
    is_new: bool = True
    error_message: str = ""


async def create_or_update_agent(
    client: AIProjectClient,
    config: AgentConfig,
    existing_agent: Optional[Any] = None
) -> Any:
    """
    Create a new agent or update an existing one.
    
    If config.search_tool_config is provided, the search tool will be configured
    and attached to the agent automatically.
    
    Args:
        client: AI Project client
        config: Agent configuration (optionally includes search_tool_config)
        existing_agent: Optional existing agent to update
    
    Returns:
        Agent definition object
    """
    tracer = get_tracer()
    
    # Configure search tool if config provided
    search_tool = None
    if config.search_tool_config:
        search_tool = await configure_search_tool(client, config.search_tool_config)
        if search_tool:
            logger.debug(f"Configured search tool for agent '{config.name}'")
            # Add search tool to the tools list
            if config.tools is None:
                config.tools = list(search_tool.definitions)
            else:
                config.tools = list(config.tools) + list(search_tool.definitions)
        else:
            logger.warning(f"Failed to configure search tool for agent '{config.name}'")
    
    if existing_agent:
        with tracer.start_as_current_span("update_agent") as span:
            add_span_attributes(span, {
                "agent.id": existing_agent.id,
                "agent.name": config.name,
                "agent.has_search_tool": search_tool is not None
            })
            
            try:
                update_kwargs = {
                    "agent_id": existing_agent.id,
                    "instructions": config.instructions,
                    "temperature": config.temperature
                }
                if config.tools is not None:
                    update_kwargs["tools"] = config.tools
                if config.tool_resources is not None:
                    update_kwargs["tool_resources"] = config.tool_resources
                
                await client.agents.update_agent(**update_kwargs)
                logger.debug(f"Updated existing agent: {existing_agent.id}")
                span.set_status(Status(StatusCode.OK))
                return existing_agent
                
            except Exception as ex:
                span.record_exception(ex)
                span.set_status(Status(StatusCode.ERROR, str(ex)))
                logger.warning(f"Update agent failed, will reuse as-is: {ex}")
                return existing_agent
    else:
        with tracer.start_as_current_span("create_agent") as span:
            add_span_attributes(span, {
                "agent.name": config.name,
                "agent.model": config.model_deployment,
                "agent.has_search_tool": search_tool is not None
            })
            
            try:
                create_kwargs = {
                    "model": config.model_deployment,
                    "name": config.name,
                    "instructions": config.instructions,
                    "temperature": config.temperature
                }
                if config.tools is not None:
                    create_kwargs["tools"] = config.tools
                if config.tool_resources is not None:
                    create_kwargs["tool_resources"] = config.tool_resources
                
                agent = await client.agents.create_agent(**create_kwargs)
                logger.debug(f"Created new agent: {agent.id}")
                os.environ["AZURE_EXISTING_AGENT_ID"] = agent.id
                span.set_status(Status(StatusCode.OK))
                return agent
                
            except Exception as ex:
                span.record_exception(ex)
                span.set_status(Status(StatusCode.ERROR, str(ex)))
                logger.error(f"Failed to create agent: {ex}")
                raise


async def create_agent_with_search_tool(
    client: AIProjectClient,
    agent_name: str,
    application_id: str,
    instructions: str,
    search_tool_config: Optional[SearchToolConfig] = None,
    model_deployment: Optional[str] = None,
    temperature: float = 0.1,
    additional_tools: Optional[List] = None,
    find_existing: bool = True
) -> AgentCreationResult:
    """
    Comprehensive utility to create or update an agent with optional search tool.
    
    This is a high-level utility that combines:
    1. Finding existing agent (optional)
    2. Configuring search tool (optional)
    3. Creating or updating the agent
    
    Args:
        client: AI Project client
        agent_name: Name of the agent (without application_id suffix)
        application_id: Application ID (appended to agent name)
        instructions: Agent instructions
        search_tool_config: Optional search tool configuration
        model_deployment: Model deployment name (uses env var if not provided)
        temperature: Agent temperature setting
        additional_tools: Additional tools to attach (e.g., MCP tools)
        find_existing: Whether to look for an existing agent first
    
    Returns:
        AgentCreationResult with agent, search_tool, and status
    
    Usage:
        # Simple agent with search tool
        result = await create_agent_with_search_tool(
            client=client,
            agent_name="ASRAgent",
            application_id="app123",
            instructions="You are an ASR agent...",
            search_tool_config=SearchToolConfig(
                index_name="app123",
                query_type=AzureAISearchQueryType.SEMANTIC,
                top_k=20
            )
        )
        agent = result.agent
        search_tool = result.search_tool
    """
    tracer = get_tracer()
    
    with tracer.start_as_current_span("create_agent_with_search_tool") as span:
        # Build full agent name
        full_agent_name = build_agent_name(agent_name, application_id)
        
        add_span_attributes(span, {
            "agent.name": full_agent_name,
            "application_id": application_id,
            "has_search_config": search_tool_config is not None,
            "find_existing": find_existing
        })
        
        try:
            # Get model deployment
            if not model_deployment:
                model_deployment = get_model_deployment_name()
            
            # Find existing agent if requested
            existing_agent = None
            if find_existing:
                existing_agent = await find_existing_agent(client, full_agent_name, application_id)
                if existing_agent:
                    logger.debug(f"Found existing agent: {existing_agent.id}")
                    add_span_attributes(span, {"agent.existing_id": existing_agent.id})
            
            # Configure search tool
            search_tool = None
            tools_list = list(additional_tools) if additional_tools else []
            
            if search_tool_config:
                search_tool = await configure_search_tool(client, search_tool_config)
                if search_tool:
                    tools_list.extend(list(search_tool.definitions))
                    logger.debug(f"Configured search tool for agent '{full_agent_name}'")
                    add_span_attributes(span, {"search_tool.configured": True})
                else:
                    logger.warning(f"Failed to configure search tool for agent '{full_agent_name}'")
                    add_span_attributes(span, {"search_tool.configured": False})
            
            # Build agent config with tool_resources from search tool if available
            tool_resources = None
            if search_tool and hasattr(search_tool, 'resources'):
                tool_resources = search_tool.resources
            
            agent_config = AgentConfig(
                name=full_agent_name,
                model_deployment=model_deployment,
                instructions=instructions,
                temperature=temperature,
                tools=tools_list if tools_list else None,
                tool_resources=tool_resources
            )
            
            # Create or update agent
            agent = await create_or_update_agent(client, agent_config, existing_agent)
            
            span.set_status(Status(StatusCode.OK))
            return AgentCreationResult(
                agent=agent,
                search_tool=search_tool,
                is_new=existing_agent is None,
                error_message=""
            )
            
        except Exception as ex:
            logger.error(f"Error creating agent with search tool: {ex}")
            span.record_exception(ex)
            span.set_status(Status(StatusCode.ERROR, str(ex)))
            raise


# =============================================================================
# THREAD MANAGEMENT
# =============================================================================

async def create_thread(client: AIProjectClient) -> str:
    """
    Create a new agent thread.
    
    Args:
        client: AI Project client
    
    Returns:
        Thread ID
    """
    thread = await client.agents.threads.create()
    logger.debug(f"Created thread: {thread.id}")
    return thread.id


async def delete_thread(client: AIProjectClient, thread_id: str) -> bool:
    """
    Delete an agent thread.
    
    Args:
        client: AI Project client
        thread_id: Thread ID to delete
    
    Returns:
        True if successful, False otherwise
    """
    try:
        await client.agents.threads.delete(thread_id=thread_id)
        logger.debug(f"Deleted thread: {thread_id}")
        return True
    except Exception as ex:
        logger.warning(f"Failed to delete thread {thread_id}: {ex}")
        return False


# =============================================================================
# ENHANCED RUN EXECUTION WITH RETRY
# =============================================================================

@dataclass
class RunResult:
    """Result of an agent run execution."""
    status: str  # 'success', 'failed', 'timeout', 'error'
    run: Optional[Any] = None
    response_text: str = ""
    parsed_json: Optional[Any] = None
    error_message: str = ""
    retry_count: int = 0
    token_usage: Optional[Dict[str, int]] = None


def extract_message_content(message) -> str:
    """
    Extract text content from an agent message.
    
    Handles different message content structures:
    - TextContent with text.value attribute
    - TextContent with text string attribute
    - Simple string content
    
    Args:
        message: Agent message object
    
    Returns:
        Extracted text content as string
    """
    content = ""
    if not hasattr(message, 'content'):
        return content
    
    for msg_content in message.content:
        if hasattr(msg_content, 'text') and hasattr(msg_content.text, 'value'):
            content += msg_content.text.value
        elif hasattr(msg_content, 'text'):
            content += str(msg_content.text)
    
    return content


def extract_json_from_text(text: str) -> Optional[Any]:
    """
    Extract JSON from text, handling markdown code blocks and partial JSON.
    
    Args:
        text: Text that may contain JSON
    
    Returns:
        Parsed JSON object or None if no valid JSON found
    """
    import json
    
    if not text:
        return None
    
    # First try to parse the whole text as JSON
    try:
        return json.loads(text)
    except Exception:
        pass
    
    # Try to extract JSON from markdown code blocks
    if "```json" in text:
        try:
            start = text.find("```json") + 7
            end = text.find("```", start)
            if end > start:
                json_str = text[start:end].strip()
                return json.loads(json_str)
        except Exception:
            pass
    
    if "```" in text:
        try:
            start = text.find("```") + 3
            # Skip language identifier if present
            newline = text.find("\n", start)
            if newline > start:
                start = newline + 1
            end = text.find("```", start)
            if end > start:
                json_str = text[start:end].strip()
                return json.loads(json_str)
        except Exception:
            pass
    
    # Try to extract JSON object or array from text
    if "{" in text and "}" in text:
        try:
            json_part = text[text.find("{"):text.rfind("}")+1]
            return json.loads(json_part)
        except Exception:
            pass
    
    if "[" in text and "]" in text:
        try:
            json_part = text[text.find("["):text.rfind("]")+1]
            return json.loads(json_part)
        except Exception:
            pass
    
    return None


async def execute_run_with_retry(
    client: AIProjectClient,
    agent_id: str,
    thread_id: str,
    prompt: str,
    context_description: str = "Agent run",
    max_wait: int = 60,
    max_retries: int = 3,
    base_delay: float = 2.0,
    parse_json: bool = False,
    track_token_usage: bool = True,
    tools: Optional[List] = None,
    tool_resources: Optional[Any] = None,
    additional_instructions: Optional[str] = None
) -> RunResult:
    """
    Execute an agent run with automatic retry on server errors.
   
    This is a comprehensive utility that handles:
    1. Sending user message
    2. Creating and polling run
    3. Retry logic on server_error or rate_limit_exceeded
    4. Token usage tracking
    5. Response extraction
    6. Optional JSON parsing
    7. Optional run-level tool overrides (for custom filters)
   
    Args:
        client: AI Project client
        agent_id: Agent ID
        thread_id: Thread ID
        prompt: User message to send
        context_description: Description for logging
        max_wait: Maximum seconds to wait for run completion
        max_retries: Maximum retry attempts on server error
        base_delay: Base delay for exponential backoff
        parse_json: Whether to attempt JSON parsing of response
        track_token_usage: Whether to track token usage
        tools: Optional list of tool definitions to override agent's default tools for this run.
               Use this to apply custom filters to search tools at run time.
        tool_resources: Optional tool resources to pass with the tools override.
               Required when using AzureAISearchTool with filter parameter.
        additional_instructions: Optional additional instructions to append for this run.
   
    Returns:
        RunResult with status, response, and optional parsed JSON
    """
    import json
    from agents.error_handler import (
        get_detailed_run_error,
        should_retry_server_error,
        retry_agent_run_on_server_error
    )
   
    tracer = get_tracer()
   
    with tracer.start_as_current_span("execute_run_with_retry") as span:
        add_span_attributes(span, {
            "agent_id": agent_id,
            "thread_id": thread_id,
            "context": context_description[:100],
            "max_wait": max_wait,
            "max_retries": max_retries
        })
       
        retry_count = 0
       
        try:
            # Send user message
            await client.agents.messages.create(
                thread_id=thread_id,
                role="user",
                content=prompt
            )
            span.add_event("user_message_created")
           
            # Create initial run with optional tool overrides
            run_kwargs = {
                "thread_id": thread_id,
                "agent_id": agent_id
            }
           
            # Add optional tool overrides for run-level filtering
            if tools is not None:
                run_kwargs["tools"] = tools
                span.add_event("tools_override_applied", {"tools_count": len(tools)})
                logger.debug(f"Applying {len(tools)} tool override(s) for run")
           
            # Add optional tool_resources for search tool configuration
            if tool_resources is not None:
                run_kwargs["tool_resources"] = tool_resources
                span.add_event("tool_resources_applied")
                logger.debug("Applying tool_resources for run")
           
            # Add optional additional instructions
            if additional_instructions:
                run_kwargs["additional_instructions"] = additional_instructions
                span.add_event("additional_instructions_applied")
           
            run = await client.agents.runs.create(**run_kwargs)
            span.add_event("run_created", {"run_id": run.id, "has_tool_override": tools is not None})
           
            # Poll for completion with transport error handling
            status, run, transport_error = await wait_for_run_completion(
                client, thread_id, run.id, max_wait
            )
           
            # Handle transport error (HTTP connection closed during polling)
            if transport_error:
                logger.error(f"Transport error during {context_description}: {transport_error}")
                span.add_event("transport_error", {"error": transport_error[:200]})
                return RunResult(
                    status="transport_error",
                    run=run,
                    error_message=transport_error,
                    retry_count=retry_count
                )
            
            # Track token usage
            token_usage = None
            if track_token_usage and hasattr(run, 'usage') and run.usage:
                token_usage = {
                    "prompt_tokens": getattr(run.usage, 'prompt_tokens', 0),
                    "completion_tokens": getattr(run.usage, 'completion_tokens', 0),
                    "total_tokens": getattr(run.usage, 'total_tokens', 0)
                }
                add_span_attributes(span, {
                    "token.prompt": token_usage["prompt_tokens"],
                    "token.completion": token_usage["completion_tokens"],
                    "token.total": token_usage["total_tokens"]
                })
            
            # Handle failed status with retry logic
            if status == "failed":
                error_msg, error_details = get_detailed_run_error(run, context_description)
                
                if should_retry_server_error(error_msg, error_details):
                    logger.warning(f"Retryable error in {context_description}, attempting retry")
                    span.add_event("retry_initiated", {"error": error_msg[:200]})
                    
                    await asyncio.sleep(0.5)  # Brief delay before retry
                    
                    retried_run, retry_succeeded = await retry_agent_run_on_server_error(
                        client, agent_id, thread_id,
                        f"{context_description} (retry)",
                        max_retries=max_retries,
                        base_delay=base_delay,
                        is_async_client=True
                    )
                    
                    if retry_succeeded and retried_run.status in ["completed", "succeeded"]:
                        run = retried_run
                        status = run.status
                        retry_count = 1  # Mark that we retried
                        logger.info(f"Retry succeeded for {context_description}")
                        span.add_event("retry_succeeded")
                    else:
                        run = retried_run if retried_run else run
                        status = run.status if run else "failed"
                        if status == "failed":
                            error_msg, error_details = get_detailed_run_error(
                                run, f"{context_description} (after retry)"
                            )
                
                # If still failed after retry
                if status == "failed":
                    logger.error(f"Final failure for {context_description}: {error_msg}")
                    span.set_status(Status(StatusCode.ERROR, error_msg[:256]))
                    return RunResult(
                        status="failed",
                        run=run,
                        error_message=error_msg,
                        retry_count=retry_count,
                        token_usage=token_usage
                    )
            
            # Handle completed status
            if status in ["completed", "succeeded"]:
                # Extract response text
                response_text = ""
                messages = client.agents.messages.list(thread_id=thread_id)
                async for message in messages:
                    if message.role == "assistant":
                        response_text = extract_message_content(message)
                        break  # Get first assistant message only
                 # Log the successful response
                logger.info(f"✅ {context_description} completed successfully")
                logger.info(f"Response length: {len(response_text)} characters")
                if response_text:
                    # Log first 500 characters for visibility, full response in debug
                    logger.info(f"Response preview: {response_text[:500]}{'...' if len(response_text) > 500 else ''}")
                    logger.debug(f"Full response: {response_text}")    
                
                # Optionally parse JSON
                parsed_json = None
                if parse_json and response_text:
                    parsed_json = extract_json_from_text(response_text)
                    if parsed_json:
                        logger.info(f"Successfully parsed JSON response")
                        logger.debug(f"Parsed JSON: {parsed_json}")
                    else:
                        logger.warning("Failed to parse JSON from response")
                
                # Log token usage if available
                if token_usage:
                    logger.info(f"Token usage - Prompt: {token_usage['prompt_tokens']}, "
                              f"Completion: {token_usage['completion_tokens']}, "
                              f"Total: {token_usage['total_tokens']}")
                
                add_span_attributes(span, {
                    "success": True,
                    "response_length": len(response_text),
                    "json_parsed": parsed_json is not None
                })
                span.set_status(Status(StatusCode.OK))
                
                return RunResult(
                    status="success",
                    run=run,
                    response_text=response_text,
                    parsed_json=parsed_json,
                    retry_count=retry_count,
                    token_usage=token_usage
                )
            
            # Handle timeout or other non-terminal statuses
            else:
                logger.warning(f"Run ended with unexpected status: {status}")
                return RunResult(
                    status="timeout" if status == "timeout" else status,
                    run=run,
                    error_message=f"Run ended with status: {status}",
                    retry_count=retry_count,
                    token_usage=token_usage
                )
                
        except Exception as ex:
            logger.error(f"Error in {context_description}: {ex}")
            span.record_exception(ex)
            span.set_status(Status(StatusCode.ERROR, str(ex)[:256]))
            return RunResult(
                status="error",
                error_message=str(ex),
                retry_count=retry_count
            )




