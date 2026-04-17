"""
Agent Factory Module

Creates and configures Azure AI Agents for architecture analysis.
Centralizes agent creation logic with proper error handling and configuration.
"""

import os
from typing import Optional, List, Dict, Any
from azure.identity.aio import DefaultAzureCredential
from azure.ai.projects.aio import AIProjectClient
from azure.ai.projects.models import ConnectionType
from semantic_kernel import Kernel
from semantic_kernel.agents import AzureAIAgent
from semantic_kernel.connectors.ai.function_calling_utils import kernel_function_metadata_to_function_call_format
from azure.ai.agents.models import AzureAISearchTool, AzureAISearchQueryType
import sys
from pathlib import Path
# Add parent directory to path for logging_config and tracing_config
arch_agent_root = Path(__file__).parent.parent
if str(arch_agent_root) not in sys.path:
    sys.path.insert(0, str(arch_agent_root))
from logging_config import get_logger
from tracing_config import get_tracer, add_span_attributes
from opentelemetry.trace import Status, StatusCode

# Import agent utilities for common agent lifecycle operations
from agents.utils.agent_utils import (
    find_existing_agent,
    get_search_connection,
    build_agent_name
)

logger = get_logger(__name__)


class AgentFactory:
    """Factory class for creating and configuring Azure AI Agents."""
    
    # Class-level thread registry to track threads by app_id
    _thread_registry: Dict[str, List[str]] = {}
    
    def __init__(self):
        """Initialize the agent factory with environment configuration.
        
        Uses managed identity (DefaultAzureCredential) for Azure Search authentication.
        """
        self.foundry_endpoint = os.environ.get("AZURE_EXISTING_AIPROJECT_ENDPOINT")
        self.azure_openai_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
        self.ai_endpoint = self.foundry_endpoint or self.azure_openai_endpoint
        self.deployment_name = os.environ.get("AZURE_AI_AGENT_DEPLOYMENT_NAME", "gpt-4.1")
        self.search_endpoint = os.environ.get("AZURE_SEARCH_ENDPOINT")
        self.search_index = os.environ.get("AZURE_SEARCH_INDEX_NAME")
        
        if not self.ai_endpoint:
            raise ValueError("No Azure AI endpoint configured. Set FOUNDRY_PROJECT_ENDPOINT or AZURE_OPENAI_ENDPOINT")
    
    @classmethod
    def register_thread(cls, app_id: str, thread_id: str) -> None:
        """Register a thread ID for an app_id for later cleanup."""
        if app_id not in cls._thread_registry:
            cls._thread_registry[app_id] = []
        if thread_id not in cls._thread_registry[app_id]:
            cls._thread_registry[app_id].append(thread_id)
            logger.debug(f"Registered thread {thread_id} for app {app_id}")
    
    @classmethod
    def get_threads_for_app(cls, app_id: str) -> List[str]:
        """Get all registered thread IDs for an app_id."""
        return cls._thread_registry.get(app_id, [])
    
    @classmethod
    def clear_threads_for_app(cls, app_id: str) -> None:
        """Clear thread registry for an app_id."""
        if app_id in cls._thread_registry:
            del cls._thread_registry[app_id]
            logger.debug(f"Cleared thread registry for app {app_id}")
    
    async def create_architecture_agent(
        self,
        instructions: str,
        kernel: Kernel,
        agent_name: str = "ArchitectureAnalyzer"
    ) -> AzureAIAgent:
        """
        Create an architecture analysis agent with configured tools.
        
        Args:
            instructions: Agent instructions
            kernel: Semantic Kernel instance with plugins
            agent_name: Name for the agent
            
        Returns:
            Configured AzureAIAgent instance
        """
        tracer = get_tracer()
        with tracer.start_as_current_span("create_architecture_agent") as span:
            try:
                add_span_attributes(span, {
                    "agent.name": agent_name,
                    "agent.model": self.deployment_name,
                    "agent.endpoint": self.ai_endpoint[:100]
                })
                
                span.add_event("agent_creation_started", {"agent_name": agent_name})
                
                async with DefaultAzureCredential() as credential:
                    agent_client = AzureAIAgent.create_client(
                        credential=credential,
                        endpoint=self.ai_endpoint
                    )
                    
                    span.add_event("agent_client_created")
                    
                    # Get search connection if available using AIProjectClient
                    search_connection_id = await self._get_search_connection_id(credential)
                    
                    if search_connection_id:
                        span.add_event("search_connection_found", {"connection_id": search_connection_id[:50]})
                    
                    # Build tools from kernel plugins and search
                    tools = await self._build_tools(kernel, agent_client, search_connection_id)
                    
                    span.add_event("tools_built", {"tool_count": len(tools)})
                    
                    # Create agent definition
                    agent_definition = await agent_client.agents.create_agent(
                        model=self.deployment_name,
                        name=agent_name,
                        instructions=instructions,
                        tools=tools if tools else None
                    )
                    
                    span.add_event("agent_definition_created", {"agent_id": agent_definition.id})
                    
                    # Create and return agent
                    agent = AzureAIAgent(
                        client=agent_client,
                        definition=agent_definition,
                        kernel=kernel
                    )
                    
                    add_span_attributes(span, {
                        "agent.id": agent_definition.id,
                        "agent.tools_count": len(tools)
                    })
                    
                    span.add_event("agent_created", {
                        "agent_id": agent_definition.id,
                        "tool_count": len(tools)
                    })
                    span.set_status(Status(StatusCode.OK))
                    
                    logger.info(f"Created {agent_name} with {len(tools)} tools")
                    return agent
                    
            except Exception as ex:
                span.record_exception(ex)
                span.add_event("agent_creation_failed", {
                    "error": str(ex)[:500],
                    "agent_name": agent_name
                })
                span.set_status(Status(StatusCode.ERROR, str(ex)[:256]))
                raise
    
    @staticmethod
    async def find_existing_security_agent(client, app_id: str):
        """Find an existing Security agent by name pattern."""
        agent_name = f"Security-Agent-{app_id}"
        return await find_existing_agent(client, agent_name, app_id)
    
    @staticmethod
    async def find_existing_diagram_analyzer_agent(client, app_id: str):
        """Find an existing Diagram Analyzer agent by name pattern."""
        agent_name = f"DiagramAnalyzer-Agent-{app_id}"
        return await find_existing_agent(client, agent_name, app_id)
    
    async def create_security_analysis_agent(
        self,
        app_id: str,
        kernel: Optional[Kernel] = None
    ) -> AzureAIAgent:
        """
        Create a focused security analysis agent for an application.
        
        Args:
            app_id: Application ID (used in agent naming)
            kernel: Optional kernel (creates new one if not provided)
            
        Returns:
            Configured security analysis agent
        """
        tracer = get_tracer()
        with tracer.start_as_current_span("create_security_analysis_agent") as span:
            try:
                agent_name = f"Security-Agent-{app_id}"
                
                add_span_attributes(span, {
                    "agent.name": agent_name,
                    "agent.model": self.deployment_name,
                    "agent.app_id": app_id
                })
                
                if kernel is None:
                    kernel = Kernel()
                
                # Load security analysis instructions
                from core.plugins.plugin_utils import load_agent_instructions
                base_instructions = load_agent_instructions("security_analysis_agent")
                
                if not base_instructions:
                    base_instructions = """You are a Security Analysis Agent.

YOUR TASK:
For each component provided, use AzureAISearch to find relevant SCF controls. Extract SCF control IDs from search result titles (e.g., "Scf Data 07..." → SCF-DATA-07). Return findings as valid JSON with identified_risks, missing_controls, compliance_gaps, recommendations, and scf_control_mapping arrays.

CRITICAL: Only use SCF control IDs from actual search results. Do not invent control IDs."""
                
                span.add_event("agent_creation_started", {"agent_name": agent_name})
                
                async with DefaultAzureCredential() as credential:
                    agent_client = AzureAIAgent.create_client(
                        credential=credential,
                        endpoint=self.ai_endpoint
                    )
                    
                    # Get search connection using AIProjectClient
                    search_connection_id = await self._get_search_connection_id(credential)
                    tools = await self._build_tools(kernel, agent_client, search_connection_id)
                    
                    span.add_event("tools_built", {"tool_count": len(tools)})
                    
                    # Create agent
                    agent_definition = await agent_client.agents.create_agent(
                        model=self.deployment_name,
                        name=agent_name,
                        instructions=base_instructions,
                        tools=tools if tools else None
                    )
                    
                    agent = AzureAIAgent(
                        client=agent_client,
                        definition=agent_definition,
                        kernel=kernel
                    )
                    
                    add_span_attributes(span, {
                        "agent.id": agent_definition.id,
                        "agent.tools_count": len(tools)
                    })
                    span.add_event("agent_created", {
                        "agent_id": agent_definition.id,
                        "tool_count": len(tools)
                    })
                    span.set_status(Status(StatusCode.OK))
                    
                    logger.info(f"Created {agent_name} with {len(tools)} tools")
                    return agent
                    
            except Exception as ex:
                span.record_exception(ex)
                span.add_event("security_agent_creation_failed", {
                    "error": str(ex)[:500],
                    "app_id": app_id
                })
                span.set_status(Status(StatusCode.ERROR, str(ex)[:256]))
                raise
    
    async def create_report_generator_agent(
        self,
        template: str,
        kernel: Optional[Kernel] = None
    ) -> AzureAIAgent:
        """
        Create a report generation agent.
        
        Args:
            template: Report generation instructions/template
            kernel: Optional kernel (creates new one if not provided)
            
        Returns:
            Configured report generation agent
        """
        if kernel is None:
            kernel = Kernel()
        
        async with DefaultAzureCredential() as credential:
            agent_client = AzureAIAgent.create_client(
                credential=credential,
                endpoint=self.ai_endpoint
            )
            
            # Create agent definition (no tools needed for report generation)
            agent_definition = await agent_client.agents.create_agent(
                model=self.deployment_name,
                name="ReportGeneratorAgent",
                instructions=template,
                tools=[]
            )
            
            agent = AzureAIAgent(
                client=agent_client,
                definition=agent_definition,
                kernel=kernel
            )
            
            logger.info("Created ReportGeneratorAgent")
            return agent
    
    async def create_diagram_analyzer_agent(
        self,
        app_id: str,
        instructions: str,
        model_name: Optional[str] = None,
        kernel: Optional[Kernel] = None
    ) -> AzureAIAgent:
        """
        Create an architecture diagram analyzer agent with vision capabilities.
        
        Args:
            app_id: Application ID for agent naming
            instructions: Analysis instructions/prompt
            model_name: Vision model to use (defaults to deployment_name)
            kernel: Optional kernel (creates new one if not provided)
            
        Returns:
            Configured diagram analyzer agent
        """
        if kernel is None:
            kernel = Kernel()
        
        model = model_name or self.deployment_name
        agent_name = f"DiagramAnalyzer-Agent-{app_id}"
        
        async with DefaultAzureCredential() as credential:
            agent_client = AzureAIAgent.create_client(
                credential=credential,
                endpoint=self.ai_endpoint
            )
            
            # Create agent definition with vision model
            agent_definition = await agent_client.agents.create_agent(
                model=model,
                name=agent_name,
                instructions=instructions
            )
            
            agent = AzureAIAgent(
                client=agent_client,
                definition=agent_definition,
                kernel=kernel
            )
            
            logger.info(f"Created {agent_name} with model {model}")
            return agent
    
    async def _get_search_connection_id(self, credential: DefaultAzureCredential) -> Optional[str]:
        """Get Azure AI Search connection ID using AIProjectClient."""
        try:
            # Get endpoint from environment
            endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT") or os.environ.get("AZURE_EXISTING_AIPROJECT_ENDPOINT")
            if not endpoint:
                logger.warning("No AI Project endpoint configured for connection discovery")
                return None
            
            # Use AIProjectClient with the utility function
            client = AIProjectClient(credential=credential, endpoint=endpoint)
            connection = await get_search_connection(client)
            conn_id = connection.id if connection and hasattr(connection, 'id') else None
            
            if conn_id:
                logger.info(f"Found Azure AI Search connection ID: {conn_id}")
            else:
                logger.warning("Could not get Azure AI Search connection ID")
            
            return conn_id
        except Exception as ex:
            logger.warning(f"Could not find Azure AI Search connection: {str(ex)}")
            return None
    
    async def _build_tools(
        self,
        kernel: Kernel,
        agent_client,
        search_connection_id: Optional[str]
    ) -> List:
        """Build list of tools from kernel plugins and search."""
        tools = []
        
        # Add kernel function tools from custom plugins
        for plugin_name, plugin in kernel.plugins.items():
            for function in plugin:
                function_metadata = kernel_function_metadata_to_function_call_format(function.metadata)
                tools.append(function_metadata)
        
        # Add built-in Azure AI Search tool if available
        if search_connection_id and self.search_index:
            azure_ai_search_tool = AzureAISearchTool(
                index_connection_id=search_connection_id,
                index_name=self.search_index,
                query_type=AzureAISearchQueryType.SEMANTIC,
                top_k=5
            )
            tools.extend(azure_ai_search_tool.definitions)
            logger.info("Added AzureAISearch tool")
        elif self.search_endpoint and self.search_index:
            # Fallback: Create custom search plugin using managed identity
            search_tool = await self._create_custom_search_plugin(kernel)
            if search_tool:
                tools.append(search_tool)
        
        return tools
    
    async def _create_custom_search_plugin(self, kernel: Kernel) -> Optional[Dict]:
        """Create custom search plugin using managed identity when connection is not available."""
        try:
            from semantic_kernel.functions import kernel_function
            from azure.search.documents import SearchClient
            from azure.identity import DefaultAzureCredential
            
            credential = DefaultAzureCredential(exclude_shared_token_cache_credential=True)
            search_client = SearchClient(self.search_endpoint, self.search_index, credential)
            
            class CustomSearchPlugin:
                @kernel_function(description="Search for security compliance content", name="search_security_compliance")
                async def search_security_compliance(self, search_terms: str) -> str:
                    results = search_client.search(search_text=search_terms, top=10, query_type="semantic")
                    search_results = []
                    for result in results:
                        search_results.append({
                            "title": result.get("title", ""),
                            "content": str(result.get("content", ""))[:500],
                            "score": result.get("@search.score", 0)
                        })
                    import json
                    return json.dumps({"results": search_results})
            
            search_plugin = CustomSearchPlugin()
            kernel.add_plugin(search_plugin, "CustomSearch")
            
            for function in kernel.plugins["CustomSearch"]:
                return kernel_function_metadata_to_function_call_format(function.metadata)
        except Exception as ex:
            logger.error(f"Failed to create custom search plugin: {str(ex)}")
            return None
    
    @staticmethod
    async def cleanup_architecture_agent(design_doc_url: str = None) -> Dict[str, Any]:
        """
        Clean up architecture agent resources.
        
        Args:
            design_doc_url: Optional design document URL for cleanup context
        
        Returns:
            dict: Cleanup result containing status and details
        """
        from tracing_config import get_tracer, add_span_attributes
        from opentelemetry.trace import Status, StatusCode
        
        logger.info(f"Cleaning up architecture agent resources for design doc: {design_doc_url}")
        
        tracer = get_tracer()
        with tracer.start_as_current_span("architecture_agent_cleanup") as cleanup_span:
            add_span_attributes(cleanup_span, {
                "design_doc_url": design_doc_url[:200] if design_doc_url else "none",
                "operation": "cleanup_architecture_agent"
            })
            
            try:
                # Note: Orchestrator cleanup not needed in standalone mode
                logger.info("Architecture agent cleanup - no orchestrator cleanup required")
                cleanup_span.set_status(Status(StatusCode.OK))
                return {
                    "status": "success",
                    "message": "Architecture agent cleanup completed successfully"
                }
            except Exception as ex:
                error_msg = f"Architecture agent cleanup failed: {str(ex)}"
                logger.error(error_msg)
                cleanup_span.set_status(Status(StatusCode.ERROR, error_msg))
                cleanup_span.record_exception(ex)
                return {
                    "status": "error",
                    "message": str(ex)
                }
    
    @staticmethod
    async def cleanup_security_agent(
        app_id: str,
        agent=None,
        agent_id: str = None,
        thread_id: str = None
    ) -> Dict[str, Any]:
        """
        Clean up security agent resources (following design_agent pattern).
        
        Args:
            app_id: Application ID for identifying the agent
            agent: The agent instance to clean up
            agent_id: The agent ID (extracted from agent if not provided)
            thread_id: Optional thread ID to delete (if known)
        
        Returns:
            dict: Cleanup result containing status and details
        """
        from tracing_config import get_tracer, add_span_attributes, trace_async_function
        from opentelemetry.trace import Status, StatusCode
        
        tracer = get_tracer()
        
        with tracer.start_as_current_span("security_agent_cleanup") as cleanup_span:
            add_span_attributes(cleanup_span, {
                "application_id": app_id,
                "agent_id_provided": agent_id is not None,
                "agent_provided": agent is not None
            })
            
            agent_name = f"Security-Agent-{app_id}"
            logger.info(f"Starting cleanup for security agent: {agent_name}")
            
            try:
                # Get agent ID from agent if not provided
                if not agent_id and agent:
                    if hasattr(agent, 'definition') and hasattr(agent.definition, 'id'):
                        agent_id = agent.definition.id
                    elif hasattr(agent, 'id'):
                        agent_id = agent.id
                
                if not agent_id:
                    logger.warning(f"No agent ID available for cleanup: {agent_name}")
                    add_span_attributes(cleanup_span, {"cleanup.agent_found": False})
                    cleanup_span.set_status(Status(StatusCode.OK))
                    return {
                        "status": "success",
                        "message": "No agent ID available for cleanup",
                        "agent_deleted": False
                    }
                
                add_span_attributes(cleanup_span, {
                    "cleanup.agent_id": agent_id,
                    "cleanup.agent_name": agent_name
                })
                
                # Get the client from the agent if available
                client = None
                if agent and hasattr(agent, 'client'):
                    client = agent.client
                
                # Delete all registered threads for this app
                threads_deleted = 0
                if client:
                    # Get all threads registered for this app_id
                    registered_threads = AgentFactory.get_threads_for_app(app_id)
                    
                    # If specific thread_id provided, also include it
                    threads_to_delete = set(registered_threads)
                    if thread_id:
                        threads_to_delete.add(thread_id)
                    
                    if threads_to_delete:
                        logger.info(f"Deleting {len(threads_to_delete)} thread(s) for security agent {agent_id}")
                        for tid in threads_to_delete:
                            try:
                                await client.agents.threads.delete(thread_id=tid)
                                threads_deleted += 1
                                logger.debug(f"Successfully deleted thread: {tid}")
                            except Exception as threads_ex:
                                logger.warning(f"Warning: Error deleting thread {tid}: {str(threads_ex)}")
                                cleanup_span.add_event("thread_deletion_warning", {
                                    "thread_id": tid,
                                    "error": str(threads_ex)[:500]
                                })
                        
                        # Clear the registry for this app
                        AgentFactory.clear_threads_for_app(app_id)
                
                add_span_attributes(cleanup_span, {"cleanup.threads_deleted": threads_deleted})
                
                # Delete the agent
                if client:
                    try:
                        await client.agents.delete_agent(agent_id)
                        logger.info(f"Successfully deleted security agent: {agent_id}")
                        add_span_attributes(cleanup_span, {"cleanup.agent_deleted": True})
                        
                        cleanup_span.set_status(Status(StatusCode.OK))
                        return {
                            "status": "success",
                            "message": "Security agent cleanup completed successfully",
                            "agent_id": agent_id,
                            "agent_deleted": True,
                            "threads_deleted": threads_deleted
                        }
                    except Exception as delete_ex:
                        logger.warning(f"Failed to delete security agent {agent_id}: {str(delete_ex)}")
                        cleanup_span.add_event("agent_deletion_warning", {
                            "error": str(delete_ex)[:500]
                        })
                        return {
                            "status": "partial",
                            "message": f"Agent deletion failed: {str(delete_ex)}",
                            "agent_id": agent_id,
                            "agent_deleted": False
                        }
                else:
                    # No client available - create one for cleanup
                    logger.info("No client available from agent, creating new client for cleanup")
                    try:
                        from azure.identity.aio import DefaultAzureCredential
                        from azure.ai.projects.aio import AIProjectClient
                        
                        endpoint = os.environ.get("AZURE_EXISTING_AIPROJECT_ENDPOINT")
                        if not endpoint:
                            logger.warning("No endpoint available for cleanup")
                            return {
                                "status": "error",
                                "message": "No endpoint available for cleanup",
                                "agent_deleted": False
                            }
                        
                        async with DefaultAzureCredential(exclude_shared_token_cache_credential=True) as creds:
                            cleanup_client = AIProjectClient(credential=creds, endpoint=endpoint)
                            try:
                                await cleanup_client.agents.delete_agent(agent_id)
                                logger.info(f"Successfully deleted security agent via new client: {agent_id}")
                                return {
                                    "status": "success",
                                    "message": "Security agent cleanup completed successfully",
                                    "agent_id": agent_id,
                                    "agent_deleted": True
                                }
                            finally:
                                if hasattr(cleanup_client, 'close'):
                                    await cleanup_client.close()
                    except Exception as new_client_ex:
                        logger.error(f"Failed to create cleanup client: {str(new_client_ex)}")
                        return {
                            "status": "error",
                            "message": f"Cleanup client creation failed: {str(new_client_ex)}",
                            "agent_deleted": False
                        }
                        
            except Exception as ex:
                error_msg = f"Security agent cleanup failed: {str(ex)}"
                logger.error(error_msg)
                cleanup_span.record_exception(ex)
                cleanup_span.set_status(Status(StatusCode.ERROR, str(ex)[:256]))
                return {
                    "status": "error",
                    "message": str(ex),
                    "agent_deleted": False
                }
    
    @staticmethod
    async def cleanup_diagram_agent(
        app_id: str,
        agent=None,
        agent_id: str = None,
        thread_id: str = None
    ) -> Dict[str, Any]:
        """
        Clean up diagram analyzer agent resources.
        
        Args:
            app_id: Application ID for identifying the agent
            agent: The agent instance to clean up
            agent_id: The agent ID (extracted from agent if not provided)
            thread_id: Optional thread ID to delete (if known)
        
        Returns:
            dict: Cleanup result containing status and details
        """
        from tracing_config import get_tracer, add_span_attributes
        from opentelemetry.trace import Status, StatusCode
        
        tracer = get_tracer()
        
        with tracer.start_as_current_span("diagram_agent_cleanup") as cleanup_span:
            add_span_attributes(cleanup_span, {
                "application_id": app_id,
                "agent_id_provided": agent_id is not None,
                "agent_provided": agent is not None
            })
            
            agent_name = f"DiagramAnalyzer-Agent-{app_id}"
            logger.info(f"Starting cleanup for diagram analyzer agent: {agent_name}")
            
            try:
                # Get agent ID from agent if not provided
                if not agent_id and agent:
                    if hasattr(agent, 'definition') and hasattr(agent.definition, 'id'):
                        agent_id = agent.definition.id
                    elif hasattr(agent, 'id'):
                        agent_id = agent.id
                
                if not agent_id:
                    logger.warning(f"No agent ID available for cleanup: {agent_name}")
                    add_span_attributes(cleanup_span, {"cleanup.agent_found": False})
                    cleanup_span.set_status(Status(StatusCode.OK))
                    return {
                        "status": "success",
                        "message": "No agent ID available for cleanup",
                        "agent_deleted": False
                    }
                
                add_span_attributes(cleanup_span, {
                    "cleanup.agent_id": agent_id,
                    "cleanup.agent_name": agent_name
                })
                
                # Get the client from the agent if available
                client = None
                if agent and hasattr(agent, 'client'):
                    client = agent.client
                
                # Delete all registered threads for this app
                threads_deleted = 0
                if client:
                    # Get all threads registered for this app_id
                    registered_threads = AgentFactory.get_threads_for_app(app_id)
                    
                    # If specific thread_id provided, also include it
                    threads_to_delete = set(registered_threads)
                    if thread_id:
                        threads_to_delete.add(thread_id)
                    
                    if threads_to_delete:
                        logger.info(f"Deleting {len(threads_to_delete)} thread(s) for diagram analyzer agent {agent_id}")
                        for tid in threads_to_delete:
                            try:
                                await client.agents.threads.delete(thread_id=tid)
                                threads_deleted += 1
                                logger.debug(f"Successfully deleted thread: {tid}")
                            except Exception as threads_ex:
                                logger.warning(f"Warning: Error deleting thread {tid}: {str(threads_ex)}")
                                cleanup_span.add_event("thread_deletion_warning", {
                                    "thread_id": tid,
                                    "error": str(threads_ex)[:500]
                                })
                        
                        # Clear the registry for this app
                        AgentFactory.clear_threads_for_app(app_id)
                
                add_span_attributes(cleanup_span, {"cleanup.threads_deleted": threads_deleted})
                
                # Delete the agent
                if client:
                    try:
                        await client.agents.delete_agent(agent_id)
                        logger.info(f"Successfully deleted diagram analyzer agent: {agent_id}")
                        add_span_attributes(cleanup_span, {"cleanup.agent_deleted": True})
                        
                        cleanup_span.set_status(Status(StatusCode.OK))
                        return {
                            "status": "success",
                            "message": "Diagram analyzer agent cleanup completed successfully",
                            "agent_id": agent_id,
                            "agent_deleted": True,
                            "threads_deleted": threads_deleted
                        }
                    except Exception as delete_ex:
                        logger.warning(f"Failed to delete diagram analyzer agent {agent_id}: {str(delete_ex)}")
                        cleanup_span.add_event("agent_deletion_warning", {
                            "error": str(delete_ex)[:500]
                        })
                        return {
                            "status": "partial",
                            "message": f"Agent deletion failed: {str(delete_ex)}",
                            "agent_id": agent_id,
                            "agent_deleted": False
                        }
                else:
                    # No client available - create one for cleanup
                    logger.info("No client available from agent, creating new client for cleanup")
                    try:
                        from azure.identity.aio import DefaultAzureCredential
                        from azure.ai.projects.aio import AIProjectClient
                        
                        endpoint = os.environ.get("AZURE_EXISTING_AIPROJECT_ENDPOINT")
                        if not endpoint:
                            logger.warning("No endpoint available for cleanup")
                            return {
                                "status": "error",
                                "message": "No endpoint available for cleanup",
                                "agent_deleted": False
                            }
                        
                        async with DefaultAzureCredential(exclude_shared_token_cache_credential=True) as creds:
                            cleanup_client = AIProjectClient(credential=creds, endpoint=endpoint)
                            try:
                                await cleanup_client.agents.delete_agent(agent_id)
                                logger.info(f"Successfully deleted diagram analyzer agent via new client: {agent_id}")
                                return {
                                    "status": "success",
                                    "message": "Diagram analyzer agent cleanup completed successfully",
                                    "agent_id": agent_id,
                                    "agent_deleted": True
                                }
                            finally:
                                if hasattr(cleanup_client, 'close'):
                                    await cleanup_client.close()
                    except Exception as new_client_ex:
                        logger.error(f"Failed to create cleanup client: {str(new_client_ex)}")
                        return {
                            "status": "error",
                            "message": f"Cleanup client creation failed: {str(new_client_ex)}",
                            "agent_deleted": False
                        }
                        
            except Exception as ex:
                error_msg = f"Diagram analyzer agent cleanup failed: {str(ex)}"
                logger.error(error_msg)
                cleanup_span.record_exception(ex)
                cleanup_span.set_status(Status(StatusCode.ERROR, str(ex)[:256]))
                return {
                    "status": "error",
                    "message": str(ex),
                    "agent_deleted": False
                }
