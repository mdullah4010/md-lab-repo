from typing import Optional

import argparse
import asyncio
import logging
import os
import sys

from azure.ai.projects.aio import AIProjectClient
from azure.ai.projects.models import ConnectionType
from azure.ai.agents.models import AsyncToolSet, AzureAISearchTool, AzureAISearchQueryType
from azure.identity.aio import DefaultAzureCredential
from dotenv import load_dotenv

from agents.logging_config import get_logger
from agents.tracing_config import (
    get_tracer, 
    trace_async_function, 
    add_span_attributes
)
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

# Import utility functions
from agents.utils.common_utils import sanitize_index_name, load_instructions_from_file
from agents.utils.agent_utils import (
    check_index_exists,
    check_semantic_config,
    check_vector_fields,
    determine_optimal_query_type,
    find_existing_agent
)


load_dotenv()
logger = get_logger(__name__)


@trace_async_function("responder_agent")
async def responder_agent(agent_name_input: Optional[str]) -> str:
    """Find an agent by name or create a new one with that name. Returns the agent ID."""
    tracer = get_tracer()
    
    with tracer.start_as_current_span("agent_initialization") as init_span:
        try:
            endpoint = os.environ.get("AZURE_EXISTING_AIPROJECT_ENDPOINT")
            if not endpoint:
                init_span.set_status(Status(StatusCode.ERROR, "AZURE_EXISTING_AIPROJECT_ENDPOINT is not set"))
                raise RuntimeError("AZURE_EXISTING_AIPROJECT_ENDPOINT is not set")

            # Resolve the desired agent name: CLI input takes priority, then env var
            agent_name_modified = "Responder-Agent-" + (agent_name_input if agent_name_input else "")
            agent_name = agent_name_modified or os.environ.get("AZURE_AI_AGENT_NAME")
            model_deployment = os.environ.get("AZURE_AI_AGENT_DEPLOYMENT_NAME")
            if not model_deployment:
                init_span.set_status(Status(StatusCode.ERROR, "AZURE_AI_AGENT_DEPLOYMENT_NAME must be set"))
                raise RuntimeError("AZURE_AI_AGENT_DEPLOYMENT_NAME must be set to create or use an agent")
            if not agent_name:
                init_span.set_status(Status(StatusCode.ERROR, "Agent name must be provided"))
                raise RuntimeError("Agent name must be provided via --agent-name (or --application-id) or AZURE_AI_AGENT_NAME")

            # Add tracing attributes
            add_span_attributes(init_span, {
                "agent.name": agent_name,
                "agent.endpoint": endpoint,
                "agent.model_deployment": model_deployment
            })
            
        except Exception as ex:
            init_span.record_exception(ex)
            init_span.set_status(Status(StatusCode.ERROR, str(ex)))
            raise

    async with DefaultAzureCredential(exclude_shared_token_cache_credential=True) as creds:
        async with AIProjectClient(credential=creds, endpoint=endpoint) as ai_client:
            with tracer.start_as_current_span("agent_search") as search_span:
                try:
                    # Try to find an agent by name if a name is provided
                    existing_agent = None
                    if agent_name:
                        add_span_attributes(search_span, {"agent.search.name": agent_name})
                        agent_list = ai_client.agents.list_agents()
                        if agent_list:
                            async for agent_obj in agent_list:
                                if agent_obj.name == agent_name:
                                    existing_agent = agent_obj
                                    logger.debug(f"Found existing agent named '{agent_obj.name}', ID: {agent_obj.id}")
                                    os.environ["AZURE_EXISTING_AGENT_ID"] = agent_obj.id
                                    add_span_attributes(search_span, {
                                        "agent.found": True,
                                        "agent.id": agent_obj.id
                                    })
                                    break
                        
                        if not existing_agent:
                            add_span_attributes(search_span, {"agent.found": False})
                            
                except Exception as ex:
                    search_span.record_exception(ex)
                    search_span.set_status(Status(StatusCode.ERROR, str(ex)))
                    logger.warning(f"Agent search failed: {ex}")

            # Configure tool (optional) and instructions; create or update agent accordingly
            search_tool = None
            with tracer.start_as_current_span("search_tool_configuration") as tool_span:
                try:
                    # Get default AI Search connection
                    default_conn = await ai_client.connections.get_default(ConnectionType.AZURE_AI_SEARCH)
                    if default_conn and getattr(default_conn, "id", None):
                        # Sanitize agent name for use as search index name (using utility function)
                        index_name = sanitize_index_name(agent_name_input)
                        if index_name != agent_name:
                            logger.debug(f"Sanitized index name '{index_name}' from agent name '{agent_name}'")

                        add_span_attributes(tool_span, {
                            "search.connection_id": default_conn.id,
                            "search.index_name": index_name,
                            "search.original_agent_name": agent_name
                        })

                        # Verify the index exists using utility function
                        exists = check_index_exists(index_name)
                    if exists is True:
                        # Use utility functions for semantic/vector capability detection
                        semantic_config = check_semantic_config(index_name)
                        has_vector_fields = check_vector_fields(index_name)
                        
                        # Use utility function to determine optimal query type
                        query_type = determine_optimal_query_type(index_name)

                        # Dynamic filter: default to appId == agent_name
                        dynamic_filter = os.environ.get("AZURE_AI_SEARCH_FILTER") or f"appId eq '{agent_name_input}'"
                        logger.debug(f"Using search filter: {dynamic_filter}")

                        try:
                            # Create/update project index following Microsoft's pattern
                            # This registers the existing index with the AI project
                            project_index_name = f"project-index-{index_name}"
                            index_version = "1"
                            
                            # Define field mapping for semantic hybrid search
                            field_mapping = {
                                "contentFields": ["content", "metadata"],  # Multiple content fields for better semantic search
                                "titleField": "title",
                                "urlField": "path",  # Use path as URL field
                                "vectorFields": ["contentVector"] if has_vector_fields else []
                            }
                            
                            logger.debug(f"Creating/updating project index '{project_index_name}' with field mapping")
                            
                            # Get connection name - try different possible properties
                            connection_name = "default"
                            if hasattr(default_conn, 'name'):
                                connection_name = default_conn.name
                            elif hasattr(default_conn, 'connection_name'):
                                connection_name = default_conn.connection_name
                            elif hasattr(default_conn, 'id'):
                                # Use ID as connection name if name is not available
                                connection_name = default_conn.id
                            
                            logger.debug(f"Using connection name: {connection_name}")
                            
                            project_index = await ai_client.indexes.create_or_update(
                                name=project_index_name,
                                version=index_version,
                                index={
                                    "connectionName": connection_name,
                                    "indexName": index_name,  # Reference your existing index
                                    "type": "AzureSearch",
                                    "fieldMapping": field_mapping
                                }
                            )
                            
                            # Create search tool using the project index asset
                            # For project index approach: use index_asset_id only, with empty connection details
                            search_tool = AzureAISearchTool(
                                index_connection_id="",  # Empty for project index approach
                                index_name="",  # Empty for project index approach
                                query_type=query_type,
                                filter=dynamic_filter,
                                top_k=20,  # Return top 5 results for better quality
                                index_asset_id=f"{project_index.name}/versions/{project_index.version}"  # Project index reference
                            )
                            
                            logger.debug(
                                "Configured Azure AI Search tool with project index '%s' (query_type=%s, semantic=%s, vector=%s)",
                                project_index_name,
                                getattr(query_type, "name", str(query_type)),
                                "enabled" if semantic_config else "disabled",
                                "enabled" if has_vector_fields else "disabled"
                            )
                        except Exception as tool_ex:
                            logger.warning(f"Failed to create project index or search tool: {tool_ex}")
                            # Fallback to legacy connection-based approach
                            try:
                                logger.debug("Falling back to legacy search tool configuration")
                                search_tool = AzureAISearchTool(
                                    index_connection_id=default_conn.id,
                                    index_name=index_name,
                                    query_type=query_type,  # Use the optimally selected query type
                                    filter=dynamic_filter,
                                    top_k=5
                                )
                                logger.warning("Configured fallback search tool with connection ID")
                            except Exception as fallback_ex:
                                logger.error(f"Fallback search tool creation also failed: {fallback_ex}")
                                search_tool = None
                    else:
                        logger.warning(
                            "Azure AI Search index '%s' not found or could not be verified; skipping tool attachment.",
                            index_name,
                        )
                except Exception as ex:
                    tool_span.record_exception(ex)
                    tool_span.set_status(Status(StatusCode.ERROR, str(ex)))
                    logger.error("Error in search tool configuration: %s", ex)
                    search_tool = None

            # Load instructions from file using utility
            instructions_file = os.path.join(os.path.dirname(__file__), "agent-instructions", "responder_agent.txt")
            default_instructions = (
                "You are the answer agent for application id '{{agent_name_input}}'. "
                "You MUST NOT invent answers. Only answer if you can cite an authoritative source you have access to; "
                "otherwise leave Response and Citation empty and set Confidence to 0. "
                "For every question, return ONLY a strict JSON object with the exact keys: "
                "Response (string), Confidence (number 0..1), Citation (string). "
                "Put where you found the answer in Citation. Do not include any text outside the JSON."
            )
            instructions_text = load_instructions_from_file(
                instructions_file,
                placeholder_replacements={"agent_name_input": agent_name_input or 'unknown'},
                default_instructions=default_instructions
            )
            logger.debug(f"Loaded responder instructions from file: {instructions_file}")
            
            if search_tool is not None:
                logger.debug(f"Agent instructions for {agent_name_input} with search tool (length: {len(instructions_text)} chars)")
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(f"Instructions content:\n{instructions_text}")
            else:
                # No search tool available; use simplified instructions
                base_json_contract = (
                    "For every question, return ONLY a strict JSON object with the exact keys: "
                    "Response (string), Confidence (number 0..1), Citation (string). "
                    "Put where you found the answer in Citation. Do not include any text outside the JSON."
                )
                instructions_text = (
                    f"You are the answer agent for application with application Id: '{agent_name_input}'. "
                    "You MUST NOT invent answers. Only answer if you can cite an authoritative source you have access to; otherwise leave Response and Citation empty and set Confidence to 0. "
                    + base_json_contract
                )
                
                logger.debug(f"Agent instructions for {agent_name_input} (length: {len(instructions_text)} chars)")
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(f"Instructions content:\n{instructions_text}")

            if existing_agent is not None:
                # Update existing agent with new instructions/toolset so it uses retrieval
                with tracer.start_as_current_span("update_agent") as update_span:
                    try:
                        update_kwargs = dict(
                            agent_id=existing_agent.id,
                            instructions=instructions_text,
                            temperature=0.1  # Set low temperature for deterministic responses
                        )
                        if search_tool is not None:
                            # Use tools and tool_resources following Microsoft's pattern
                            update_kwargs["tools"] = search_tool.definitions
                            update_kwargs["tool_resources"] = search_tool.resources
                        
                        add_span_attributes(update_span, {
                            "agent.id": existing_agent.id,
                            "agent.name": agent_name,
                            "agent.has_search_tool": search_tool is not None
                        })
                        
                        await ai_client.agents.update_agent(**update_kwargs)
                        logger.debug("Updated existing agent instructions/tools for semantic hybrid search")
                        return existing_agent.id
                    except Exception as ex:
                        update_span.record_exception(ex)
                        update_span.set_status(Status(StatusCode.ERROR, str(ex)))
                        logger.warning(f"Update agent failed, will reuse as-is: {ex}")
                        return existing_agent.id
            else:
                # Create new agent with semantic hybrid search capabilities
                with tracer.start_as_current_span("create_agent") as create_span:
                    try:
                        logger.debug("Creating new agent with semantic hybrid search capabilities")
                        create_kwargs = dict(
                            model=model_deployment,
                            name=agent_name,
                            instructions=instructions_text,
                            temperature=0.1  # Set low temperature for deterministic responses
                        )
                        if search_tool is not None:
                            # Use tools and tool_resources following Microsoft's pattern
                            create_kwargs["tools"] = search_tool.definitions
                            create_kwargs["tool_resources"] = search_tool.resources

                        add_span_attributes(create_span, {
                            "agent.name": agent_name,
                            "agent.model": model_deployment,
                            "agent.has_search_tool": search_tool is not None
                        })

                        agent = await ai_client.agents.create_agent(**create_kwargs)
                        logger.debug(f"Created agent with semantic hybrid search, ID: {agent.id}")
                        os.environ["AZURE_EXISTING_AGENT_ID"] = agent.id
                        return agent.id
                    except Exception as ex:
                        create_span.record_exception(ex)
                        create_span.set_status(Status(StatusCode.ERROR, str(ex)))
                        logger.error("Failed to create agent: %s", ex)
                        raise


@trace_async_function
async def main() -> None:
    tracer = get_tracer()
    with tracer.start_as_current_span("main_execution") as main_span:
        try:
            parser = argparse.ArgumentParser(description="Create or reuse an Azure AI Agent by name.")
            parser.add_argument("--application-id", "-a", "--agent-name", dest="agent_name", help="Agent name to use. If exists, it will be reused; otherwise a new agent will be created.")
            args = parser.parse_args()

            agent_name = args.agent_name
            if not agent_name and sys.stdin.isatty():
                try:
                    agent_name = input("Enter Agent Name (required): ").strip() or None
                except Exception as input_ex:
                    main_span.add_event("input_error", {"error": str(input_ex)})
                    agent_name = None
            
            if not agent_name:
                error_msg = "Agent name is required. Provide --agent-name (or --application-id) or set AZURE_AI_AGENT_NAME."
                main_span.set_status(Status(StatusCode.ERROR, error_msg))
                logger.error(error_msg)
                sys.exit(1)

            add_span_attributes(main_span, {"agent_name": agent_name})

            logger.info("Initializing agent...")
            agent_id = await responder_agent(agent_name)
            logger.info(f"Agent ID: {agent_id}")
            
            add_span_attributes(main_span, {"agent_id": agent_id})
            
        except Exception as ex:
            main_span.record_exception(ex)
            main_span.set_status(Status(StatusCode.ERROR, str(ex)))
            logger.error("Main execution failed: %s", ex)
            raise


if __name__ == "__main__":
    asyncio.run(main())