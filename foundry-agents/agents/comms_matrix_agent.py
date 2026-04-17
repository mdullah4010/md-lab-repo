# Copyright (c) Microsoft. All rights reserved.

import os
import asyncio
import json
import time
from typing import Dict, List, Any, Optional
from dotenv import load_dotenv
from azure.ai.agents.models import AzureAISearchQueryType, ListSortOrder
import pandas as pd
from datetime import datetime
import ipaddress

import re

# Import tracing configuration
from tracing_config import (
    get_tracer,
    trace_async_function,
    add_span_attributes,
)
from opentelemetry.trace import Status, StatusCode

# Import logging configuration
from logging_config import get_logger

# Import utility functions from agent_utils
from utils.agent_utils import (
    AgentClientManager,
    SearchToolConfig,
    create_agent_with_search_tool,
    cleanup_agent,
    build_agent_name,
    get_model_deployment_name,
    wait_for_run_completion,
    extract_json_from_text,
    build_metadata_filter_expression,
    create_filtered_search_tool,
    execute_run_with_retry,
)

# Import common utils for loading instructions and file upload
from utils.common_utils import (
    load_instructions_from_file,
    upload_file_to_container_async,
    download_template_from_storage,
    get_unique_blob_metadata
)

# Load environment variables
load_dotenv()

# Create logger for this module
logger = get_logger(__name__)

logger.info("Communications Matrix Agent initialized")

# Path to agent instructions file
COMMS_MATRIX_INSTRUCTIONS_FILE = os.path.join(
    os.path.dirname(__file__), "agent-instructions", "comms_matrix_agent.txt"
)

# Default instructions as fallback
DEFAULT_COMMS_MATRIX_INSTRUCTIONS = """
You are a network communications matrix expert specializing in analyzing Azure migration dependencies, 
IP configurations, and VNET hub-spoke architectures to generate standardized communication flow matrices.
Your role is to analyze indexed data and generate a comprehensive communications matrix.
OUTPUT FORMAT: Always return results as a structured JSON object with a "flows" array containing flow objects.
"""


def get_comms_matrix_instructions() -> str:
    """Load communications matrix agent instructions from file."""
    return load_instructions_from_file(
        instructions_file=COMMS_MATRIX_INSTRUCTIONS_FILE,
        default_instructions=DEFAULT_COMMS_MATRIX_INSTRUCTIONS
    )


# def build_comms_prep_filter(app_id: str) -> str:
#     """
#     Build search filter for communications prep data.
    
#     Filters search to only include files from the comms_prep folder
#     for the specified application.
    
#     Args:
#         app_id: Application ID
        
#     Returns:
#         Azure AI Search filter string
#     """
#     comms_filter = f"search.ismatch('{app_id}/comms_prep/', 'path')"
    
#     # Check if there's a global filter configured
#     global_filter = os.getenv("AZURE_AI_SEARCH_FILTER")
#     if global_filter:
#         comms_filter = f"({global_filter}) and ({comms_filter})"
    
#     logger.debug(f"Built comms_prep filter: {comms_filter}")
#     return comms_filter


class CommsMatrixAgent:
    """
    Agent responsible for generating communication flows matrix for applications based on:
    - Azure Migrate dependency data
    - Standard IP data
    - VNET Hub spokes data
    
    Generates a JSON file with standardized communication flow fields.
    """
    
    def __init__(self, client=None):
        """
        Initialize the Communications Matrix Agent.
        
        Args:
            client: Optional Azure AI client. If None, a new client will be created.
        """
        self.client = client
        self.agent_id = None
        self.thread_id = None
        self._owns_client = client is None
    
    @trace_async_function("create_comms_matrix_agent")
    async def _create_agent(self, application_id: str, client):
        """Create the communications matrix agent using utility functions."""
        tracer = get_tracer()
        
        with tracer.start_as_current_span("comms_matrix_agent_creation") as span:
            add_span_attributes(span, {
                "comms_matrix.application_id": application_id,
                "comms_matrix.agent_type": "communications_matrix"
            })
            
            try:
                # # Build filter for comms_prep folder
                # comms_filter = build_comms_prep_filter(application_id)
                
                # Configure search tool for the application index
                search_config = SearchToolConfig(
                    index_name=application_id,
                    query_type=AzureAISearchQueryType.VECTOR_SEMANTIC_HYBRID,
                    top_k=30,
                    filter=""
                )
                
                # Build dynamic agent name with application ID
                agent_name = "CommsMatrix-Agent"
                
                # Use utility to create agent with search tool
                result = await create_agent_with_search_tool(
                    client=client,
                    agent_name=agent_name,
                    application_id=application_id,
                    instructions=get_comms_matrix_instructions(),
                    search_tool_config=search_config,
                    model_deployment=get_model_deployment_name(),
                    temperature=0.1,
                    find_existing=True
                )
                
                self.agent_id = result.agent.id
                logger.info(f"Communications matrix agent created: {self.agent_id}")
                
                add_span_attributes(span, {
                    "comms_matrix.agent_id": self.agent_id,
                    "comms_matrix.agent_created": True,
                    "comms_matrix.is_new_agent": result.is_new
                })
                
                return result.agent
                
            except Exception as ex:
                span.record_exception(ex)
                span.set_status(Status(StatusCode.ERROR, str(ex)))
                logger.error(f"Failed to create communications matrix agent: {ex}")
                raise
    
    @trace_async_function("generate_communications_matrix")
    async def generate_communications_matrix(self, application_id: str, output_folder: str, storage_account_name: str) -> Dict[str, Any]:
        """
        Generate comprehensive communications matrices for the application, one per environment.
        
        This follows the populate_dependency_table pattern:
        1. First discover all environments (prod, dev, test, pre-prod, etc.) from the knowledge base
        2. For each environment, discover unique servers in that environment
        3. For each server, extract its inbound and outbound dependencies
        4. Build flows based on connection direction with proper IP population
        5. Generate separate Excel files for each environment
        
        Args:
            application_id: The application ID to analyze (index should already exist)
            output_folder: Folder prefix for blob storage upload (default: "comms_prep/output/")
            
        Returns:
            Dict containing the communications matrices for all environments
        """
        tracer = get_tracer()
        
        with tracer.start_as_current_span("comms_matrix_generation") as span:
            add_span_attributes(span, {
                "comms_matrix.application_id": application_id,
                "comms_matrix.operation": "generate_matrix"
            })
            
            try:
                # Use AgentClientManager for proper client lifecycle management
                async with AgentClientManager(existing_client=self.client) as manager:
                    client = manager.client
                    
                    # Step 1: Create agent and thread
                    logger.info(f"Creating communications matrix agent for {application_id}")
                    await self._create_agent(application_id, client)
                    
                    thread = await client.agents.threads.create()
                    self.thread_id = thread.id
                    
                    # # Step 2: Download enablers file from blob storage
                    # logger.info(f"Step 2: Downloading enablers file from blob storage")
                    # enabler_ip_set = await self._download_and_load_enablers()
                    
                    # Step 3: Discover all environments from the knowledge base
                    logger.info(f"Discovering environments from knowledge base")
                    environments = await self._get_environments(client, application_id)
                    
                    if not environments:
                        logger.warning("No environments found, defaulting to 'production'")
                        environments = ["production"]
                    
                    logger.info(f"Found {len(environments)} environments: {environments}")
                    
                    add_span_attributes(span, {
                        "comms_matrix.environments_count": len(environments),
                        "comms_matrix.environments": ", ".join(environments)
                    })
                    
                    # Step 4: Process each environment separately
                    all_environment_results = []
                    total_servers_processed = 0
                    total_flows_generated = 0
                    
                        # Define metadata criteria for network documents
                    # This filters search results to only include network-related documents in final state
                    container_name = application_id  # Assuming container name is application_id
                    metadata_set = get_unique_blob_metadata(container_name, storage_account_name)
                    categories = [v for k, v in metadata_set if k == 'category']
                    logger.info(f"Categories found in blob metadata: {categories}")
                    has_network = 'networkjsonl' in categories
                    if has_network:
                        network_metadata_criteria = {"category": "networkjsonl"}
                        logger.info("Using 'networkjsonl' as network_metadata_criteria.")
                    else:
                        network_metadata_criteria = None
                   
                    for environment in environments:
                        logger.info(f"Processing environment: {environment}")
                       
                        # Get unique servers for this environment with metadata filtering
                        logger.info(f"Discovering servers for environment '{environment}' with network metadata filter")
                        unique_servers = await self._get_unique_servers_for_environment(
                            client, application_id, environment,
                            metadata_criteria=network_metadata_criteria
                        )
                       
                        if not unique_servers:
                            logger.warning(f"No servers found for environment '{environment}', trying fallback")
                            unique_servers = await self._get_servers_fallback_for_environment(
                                client, application_id, environment
                            )
                       
                        if not unique_servers:
                            logger.warning(f"No servers found for environment '{environment}', skipping")
                            continue
                       
                        logger.info(f"Found {len(unique_servers)} servers in '{environment}': {unique_servers[:5]}...")
                       
                        # Extract dependencies for each server in this environment with metadata filtering
                        logger.info(f"Extracting dependencies for servers in '{environment}' with network metadata filter")
                        all_flows = []
                        flow_counter = 1
                       
                        for server_name in unique_servers:
                            logger.info(f"Processing server: {server_name} (env: {environment})")
                           
                            # Get server dependency information (both inbound and outbound) with metadata filtering
                            server_deps = await self._get_server_dependencies(
                                client, application_id, server_name, environment,
                                metadata_criteria=network_metadata_criteria
                            )
                           
                            # Build flows from the dependency data with environment context
                            server_flows = self._build_flows_for_server(
                                server_name, server_deps, flow_counter, environment
                            )
                           
                            flow_counter += len(server_flows)
                            all_flows.extend(server_flows)
                           
                            logger.debug(f"Server {server_name}: {len(server_flows)} flows generated")
                       
                        # # Deduplicate flows
                        # logger.info(f"Step 4c: Deduplicating {len(all_flows)} flows for '{environment}'")
                        # deduplicated_flows = self._deduplicate_flows(all_flows)
                       
                        # # Re-number flows after deduplication
                        # for i, flow in enumerate(deduplicated_flows, 1):
                        #     flow["Flow_ID"] = f"F{i}"
                       
                        # logger.info(f"After deduplication: {len(deduplicated_flows)} unique flows for '{environment}'")
                       
                        # Compile matrix for this environment
                        logger.info(f"Step 4d: Compiling communications matrix for '{environment}'")
                        env_matrix_result = await self._compile_communications_matrix(
                            application_id, all_flows, environment
                        )
                       
                        all_environment_results.append({
                            "environment": environment,
                            "matrix": env_matrix_result
                        })
                       
                        total_servers_processed += len(unique_servers)
                        total_flows_generated += len(all_flows)
                   
                    # Step 5: Cleanup
                    await self._cleanup(client)
                   
                    add_span_attributes(span, {
                        "comms_matrix.environments_processed": len(environments),
                        "comms_matrix.total_servers_processed": total_servers_processed,
                        "comms_matrix.total_flows_generated": total_flows_generated,
                        "comms_matrix.matrix_generated": True
                    })
                   
                    logger.info(f"Communications matrix generation completed: {len(all_environment_results)} environments, {total_flows_generated} total flows")
                   
                    # Return combined result
                    return {
                        "status": "success",
                        "app_id": application_id,
                        "environments_processed": len(all_environment_results),
                        "total_servers_processed": total_servers_processed,
                        "total_flows_generated": total_flows_generated,
                        "environment_results": all_environment_results
                    }
               
            except Exception as ex:
                span.record_exception(ex)
                span.set_status(Status(StatusCode.ERROR, str(ex)))
                logger.error(f"Failed to generate communications matrix: {ex}")
                raise
    
    async def _download_and_load_enablers(self) -> set:
        """
        Download enablers_italy.json from blob storage and load IPs.
        Falls back to local file if download fails.
        """
        enabler_ip_set = set()
        
        # Try to download from blob storage
        try:
            storage_account_url = os.getenv("AZURE_STORAGE_ACCOUNT_URL", "")
            if storage_account_url:
                # Create temp directory for downloaded file
                temp_dir = os.path.join(os.path.dirname(__file__), "..", "Outputs", "temp")
                os.makedirs(temp_dir, exist_ok=True)
                local_enabler_path = os.path.join(temp_dir, "enablers_italy.json")
                
                success = download_template_from_storage(
                    account_url=storage_account_url,
                    blob_name="enablers_italy.json",
                    local_path=local_enabler_path,
                    container_name="templates"
                )
                
                if success and os.path.exists(local_enabler_path):
                    enabler_ip_set = self._load_enabler_ips(local_enabler_path)
                    logger.info(f"Loaded {len(enabler_ip_set)} enabler IPs from blob storage")
                    # Clean up temp file
                    try:
                        os.remove(local_enabler_path)
                    except Exception:
                        pass
                    return enabler_ip_set
                else:
                    logger.warning("Failed to download enablers from blob storage, trying local fallback")
            else:
                logger.warning("AZURE_STORAGE_ACCOUNT_URL not set, trying local fallback")
        except Exception as ex:
            logger.warning(f"Error downloading enablers from blob: {ex}, trying local fallback")
        
        # Fallback to local file
        local_enabler_path = os.path.join(
            os.path.dirname(__file__), "agent-instructions", "enablers_italy.json"
        )
        if os.path.exists(local_enabler_path):
            enabler_ip_set = self._load_enabler_ips(local_enabler_path)
            logger.info(f"Loaded {len(enabler_ip_set)} enabler IPs from local file")
        else:
            logger.warning("No enablers file found, proceeding without IP exclusions")
        
        return enabler_ip_set
    
    async def _get_environments(self, client, application_id: str) -> List[str]:
        """
        Query the knowledge base to discover all environments (prod, dev, test, pre-prod, etc.).
        """
        env_query = f"""Use the knowledge base project-index-{application_id}/versions/1.

Search for ALL environment names or environment types in the data.

Look for environment information in:
- Server metadata (environment field, env field)
- File paths or folder names (prod, production, dev, development, test, uat, staging, pre-prod, preprod, sit)
- Configuration data mentioning environments
- Azure Migrate data with environment classifications

Extract and list ALL unique environment names found. Common patterns include:
- production, prod, prd
- preproduction, pre-prod, preprod
- development, dev
- test, testing, tst
- staging, stg
- uat (user acceptance testing)
- sit (system integration testing)

Return ONLY a JSON array of unique environment names. Example format:
["production", "preproduction", "development", "test"]

Important:
- Include ALL unique environments found
- Normalize names where possible (e.g., "prod" -> "production")
- Return only the JSON array, no additional text

Always perform a search query with the attached tool."""

        await client.agents.messages.create(
            thread_id=self.thread_id,
            role="user",
            content=env_query
        )
        
        run = await client.agents.runs.create(
            thread_id=self.thread_id,
            agent_id=self.agent_id,
            metadata={"step": "get_environments"}
        )
        
        status, run_result, error_msg = await wait_for_run_completion(
            client, self.thread_id, run.id,
            timeout_seconds=120,
            poll_interval=2.0
        )
        
        if status != "completed":
            logger.warning(f"Environment discovery run status: {status}, error: {error_msg}")
            return []
        
        response = await self._collect_run_response(client, run.id)
        
        # Parse environments from response
        environments = self._parse_environment_list(response)
        
        return environments
    
    def _parse_environment_list(self, response: str) -> List[str]:
        """Parse environment list from agent response and normalize names."""
        environments = []
        
        parsed = extract_json_from_text(response)
        if parsed and isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, str):
                    # Normalize environment names
                    env = self._normalize_environment_name(item.strip().lower())
                    if env and env not in environments:
                        environments.append(env)
        
        return environments if environments else []
    
    def _normalize_environment_name(self, env: str) -> str:
        """Normalize environment name to standard format."""
        env = env.lower().strip()
        
        # Production variants
        if env in ["prod", "production", "prd"]:
            return "production"
        # Preproduction variants
        elif env in ["preprod", "preproduction", "pre-prod", "pre-production"]:
            return "preproduction"
        # Development variants
        elif env in ["dev", "development"]:
            return "development"
        # Test variants
        elif env in ["test", "testing", "tst"]:
            return "test"
        # Staging variants
        elif env in ["staging", "stg", "stage"]:
            return "staging"
        # UAT
        elif env in ["uat", "user acceptance testing"]:
            return "uat"
        # SIT
        elif env in ["sit", "system integration testing"]:
            return "sit"
        # Return as-is if not recognized
        elif env:
            return env
        return ""
    
    async def _get_unique_servers_for_environment(self, client, application_id: str, 
                                                   environment: str,
                                                   metadata_criteria: Optional[Dict[str, str]] = None) -> List[str]:
        """
        Query the knowledge base to get all unique server names for a specific environment.
        
        Args:
            client: AI Project client
            application_id: Application ID for the search index
            environment: Environment name (e.g., "production", "development")
            metadata_criteria: Optional dictionary of metadata key-value pairs to filter documents.
                              Example: {"category": "network", "document_state": "final"}
                              
        Returns:
            List of unique server names found in the indexed documents
        """
        # Build metadata description for the query if filtering is enabled
        metadata_desc = ""
        if metadata_criteria:
            criteria_parts = [f"{k}={v}" for k, v in metadata_criteria.items()]
            metadata_desc = f" with metadata [{', '.join(criteria_parts)}]"
        
        server_query = f"""Use the knowledge base project-index-{application_id}/versions/1.

Search for ALL server names and hostnames that belong to the "{environment}" environment{metadata_desc}.

Extract and list ALL unique server names (hostnames) that:
- Are explicitly tagged or classified as "{environment}" environment
- Appear in files or data related to "{environment}"
- Have naming patterns suggesting "{environment}" (e.g., -prod, -dev, -test in hostname)

Look in:
- Dependency data files for "{environment}"
- Inbound/outbound connection files for "{environment}"
- Azure Migrate dependency exports with environment classification
- Server metadata with environment field matching "{environment}"

Return ONLY a JSON array of unique server names for the "{environment}" environment. Example format:
["server1", "server2", "hostname.domain.com"]

Important:
- Include ONLY servers that belong to "{environment}" environment
- Do NOT include IP addresses in this list
- Include both short names and FQDNs
- Return only the JSON array, no additional text

Always perform a search query with the attached tool."""

        # Create filtered search tool if metadata_criteria is provided
        tools_override = None
        tool_resources_override = None
        
        if metadata_criteria:
            # Build filter expression using the shared utility function
            filter_expression = build_metadata_filter_expression(application_id, metadata_criteria)
            
            logger.info(f"Using metadata filtering for server discovery: {metadata_criteria}")
            logger.debug(f"Filter expression: {filter_expression}")
            
            filtered_tool_result = await create_filtered_search_tool(
                client=client,
                partition_key=application_id,
                filter_expression=filter_expression,
                top_k=50  # Increase top_k for comprehensive server discovery
            )
            tools_override = filtered_tool_result.definitions
            tool_resources_override = filtered_tool_result.resources

        # Use execute_run_with_retry for proper handling with tool overrides
        result = await execute_run_with_retry(
            client=client,
            agent_id=self.agent_id,
            thread_id=self.thread_id,
            prompt=server_query,
            context_description=f"Get unique servers for {environment}",
            max_wait=120,
            max_retries=3,
            parse_json=False,  # We do custom JSON parsing for server list
            tools=tools_override,
            tool_resources=tool_resources_override
        )
        
        if result.status not in ["success", "completed"]:
            logger.warning(f"Server discovery run status: {result.status}, error: {result.error_message}")
            return []
        
        servers = self._parse_server_list(result.response_text or "")
        
        return servers
    
    async def _get_servers_fallback_for_environment(self, client, application_id: str,
                                                     environment: str) -> List[str]:
        """Fallback method to get server names for an environment if primary method fails."""
        fallback_query = f"""Use the knowledge base project-index-{application_id}/versions/1.

Search for any dependency data, network connections, or server information related to "{environment}" environment.
List all server hostnames that appear as either source or destination in "{environment}".

Return as JSON array of server names only.
Always perform a search query with the attached tool."""

        await client.agents.messages.create(
            thread_id=self.thread_id,
            role="user",
            content=fallback_query
        )
        
        run = await client.agents.runs.create(
            thread_id=self.thread_id,
            agent_id=self.agent_id,
            metadata={"step": "get_servers_fallback", "environment": environment}
        )
        
        status, run_result, error_msg = await wait_for_run_completion(
            client, self.thread_id, run.id,
            timeout_seconds=120,
            poll_interval=2.0
        )
        
        response = await self._collect_run_response(client, run.id)
        return self._parse_server_list(response)
    
    async def _get_unique_servers(self, client, application_id: str) -> List[str]:
        """
        Query the knowledge base to get all unique server names.
        Similar to _get_unique_servers_from_index in orchestrator_agent.
        """
        server_query = f"""Use the knowledge base project-index-{application_id}/versions/1.

Search for ALL server names and hostnames in the dependency data, inbound files, and outbound files.

Extract and list ALL unique server names (hostnames) that appear in any of these contexts:
- Source servers/hosts in dependency or outbound connection data
- Destination servers/hosts in dependency or inbound connection data
- Any server mentioned in network configuration or Azure Migrate data

Return ONLY a JSON array of unique server names. Example format:
["server1", "server2", "hostname.domain.com"]

Important:
- Include ALL unique server names found
- Do NOT include IP addresses in this list
- Include both short names and FQDNs
- Return only the JSON array, no additional text

Always perform a search query with the attached tool."""

        await client.agents.messages.create(
            thread_id=self.thread_id,
            role="user",
            content=server_query
        )
        
        run = await client.agents.runs.create(
            thread_id=self.thread_id,
            agent_id=self.agent_id,
            metadata={"step": "get_unique_servers"}
        )
        
        # Wait for completion
        status, run_result, error_msg = await wait_for_run_completion(
            client, self.thread_id, run.id,
            timeout_seconds=120,
            poll_interval=2.0
        )
        
        if status != "completed":
            logger.warning(f"Server discovery run status: {status}, error: {error_msg}")
            return []
        
        # Collect and parse response
        response = await self._collect_run_response(client, run.id)
        
        # Try to extract JSON array of servers
        servers = self._parse_server_list(response)
        
        return servers
    
    async def _get_servers_fallback(self, client, application_id: str) -> List[str]:
        """Fallback method to get server names if primary method fails."""
        fallback_query = f"""Use the knowledge base project-index-{application_id}/versions/1.

Search for any dependency data, network connections, or server information.
List all server hostnames that appear as either source or destination.

Return as JSON array of server names only.
Always perform a search query with the attached tool."""

        await client.agents.messages.create(
            thread_id=self.thread_id,
            role="user",
            content=fallback_query
        )
        
        run = await client.agents.runs.create(
            thread_id=self.thread_id,
            agent_id=self.agent_id,
            metadata={"step": "get_servers_fallback"}
        )
        
        status, run_result, error_msg = await wait_for_run_completion(
            client, self.thread_id, run.id,
            timeout_seconds=120,
            poll_interval=2.0
        )
        
        response = await self._collect_run_response(client, run.id)
        return self._parse_server_list(response)
    
    def _parse_server_list(self, response: str) -> List[str]:
        """Parse server list from agent response."""
        servers = []
        
        # Try to extract JSON array
        parsed = extract_json_from_text(response)
        if parsed and isinstance(parsed, list):
            # Filter out IP addresses
            for item in parsed:
                if isinstance(item, str):
                    item = item.strip()
                    # Skip if it looks like an IP address
                    if not re.match(r'^\d+\.\d+\.\d+\.\d+(/\d+)?$', item) and item:
                        servers.append(item)
        
        return list(set(servers))  # Remove duplicates
    
    async def _get_server_dependencies(self, client, application_id: str, 
                                        server_name: str, environment: str = "production",
                                        metadata_criteria: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """
        Get all dependency information for a specific server in a specific environment.
        Returns both inbound (server is destination) and outbound (server is source) connections.
        All servers in the knowledge base are on-premises servers.
        
        Args:
            client: AI Project client
            application_id: Application ID for the search index
            server_name: Name of the server to query dependencies for
            environment: Environment name (e.g., "production", "development")
            metadata_criteria: Optional dictionary of metadata key-value pairs to filter documents.
                              Example: {"category": "network", "document_state": "final"}
                              
        Returns:
            Dictionary containing server dependency information
        """
        # Build metadata description for the query if filtering is enabled
        metadata_desc = ""
        if metadata_criteria:
            criteria_parts = [f"{k}={v}" for k, v in metadata_criteria.items()]
            metadata_desc = f" with metadata [{', '.join(criteria_parts)}]"
        
        dep_query = f"""Use the knowledge base project-index-{application_id}/versions/1.

Find ALL network connections and dependencies for server: {server_name} in the "{environment}" environment{metadata_desc}.

Search in dependency files, inbound files, and outbound files for the "{environment}" environment.

IMPORTANT: All servers in the knowledge base are ON-PREMISES servers (not yet migrated to Azure).
Extract the actual hostnames and IP addresses for all servers in the "{environment}" environment.

For server {server_name} in "{environment}", extract:

1. OUTBOUND connections (where {server_name} is the SOURCE - it connects TO other servers):
   - Destination server hostnames (the servers {server_name} connects TO)
   - Destination server IP addresses (MUST extract actual IPs)
   - Ports and protocols used

2. INBOUND connections (where {server_name} is the DESTINATION - other servers connect TO it):
   - Source server hostnames (servers that connect TO {server_name})
   - Source server IP addresses (MUST extract actual IPs)
   - Ports and protocols used

3. Server's own IP address: {server_name}'s IP address

Return as JSON:
{{
    "server_name": "{server_name}",
    "server_ip": "actual IP address of {server_name}",
    "environment": "{environment}",
    "outbound_connections": [
        {{"dest_hostname": "actual hostname", "dest_ip": "actual IP address", "port": "port number", "protocol": "TCP/UDP"}}
    ],
    "inbound_connections": [
        {{"source_hostname": "actual hostname", "source_ip": "actual IP address", "port": "port number", "protocol": "TCP/UDP"}}
    ]
}}

CRITICAL: Extract ACTUAL IP addresses for all servers. Do not leave IP fields empty if the data exists.

Always perform a search query with the attached tool."""

        # Create filtered search tool if metadata_criteria is provided
        tools_override = None
        tool_resources_override = None
        
        if metadata_criteria:
            # Build filter expression using the shared utility function
            filter_expression = build_metadata_filter_expression(application_id, metadata_criteria)
            
            logger.debug(f"Using metadata filtering for dependency query: {metadata_criteria}")
            logger.debug(f"Filter expression: {filter_expression}")
            
            filtered_tool_result = await create_filtered_search_tool(
                client=client,
                partition_key=application_id,
                filter_expression=filter_expression,
                top_k=30  # Lower top_k for specific server queries
            )
            tools_override = filtered_tool_result.definitions
            tool_resources_override = filtered_tool_result.resources

        # Use execute_run_with_retry for proper handling with tool overrides
        result = await execute_run_with_retry(
            client=client,
            agent_id=self.agent_id,
            thread_id=self.thread_id,
            prompt=dep_query,
            context_description=f"Get dependencies for {server_name}",
            max_wait=180,
            max_retries=3,
            parse_json=False,  # We do custom JSON parsing
            tools=tools_override,
            tool_resources=tool_resources_override
        )
        
        # Parse the response
        parsed = extract_json_from_text(result.response_text or "")
        
        if parsed and isinstance(parsed, dict):
            return parsed
        
        # Return empty structure if parsing failed
        # All servers in KB are on-prem and will be migrated to Azure
        return {
            "server_name": server_name,
            "server_ip": "",
            "is_azure_target": True,  # All on-prem servers are migration targets
            "azure_zone": "",
            "outbound_connections": [],
            "inbound_connections": []
        }
    
    # Environment-based security zone mapping
    ZONE_MAPPING = {
        "preproduction": {
            "presentation_layer": "E1",
            "application_layer": "E2",
            "middleware_layer": "E2",
            "database_layer": "E3"
        },
        "production": {
            "presentation_layer": "E1",
            "application_layer": "E2",
            "middleware_layer": "E2",
            "database_layer": "E3"
        },
        "other": {
            "presentation_layer": "TP",
            "application_layer": "TA",
            "middleware_layer": "TA",
            "database_layer": "TD"
        }
    }
    
    def _get_azure_zone_name(self, server_name: str, server_deps: Dict, environment: str = "production") -> str:
        """
        Generate Azure zone hostname for a server being migrated to Azure.
        
        Security Zone Mapping (based on environment):
        
        For preproduction/production:
        - Presentation Layer (Web/Frontend): E1
        - Application Layer (App/API/Service): E2
        - Middleware Layer: E2
        - Database Layer: E3
        
        For other environments:
        - Presentation Layer: TP
        - Application Layer: TA
        - Middleware Layer: TA
        - Database Layer: TD
        
        Since all servers in KB are on-prem, we generate an Azure zone name
        based on the server's characteristics and the target environment.
        """
        # Check if zone is explicitly provided in deps
        if server_deps.get("azure_zone"):
            return server_deps["azure_zone"]
        
        # Normalize environment to match zone mapping keys
        env_lower = environment.lower().strip() if environment else "production"
        if env_lower in ["prod", "production", "prd"]:
            env_key = "production"
        elif env_lower in ["preprod", "preproduction", "pre-production", "staging", "uat", "sit", "dev", "development", "test"]:
            env_key = "preproduction"
        else:
            env_key = "other"
        
        # Get zone codes for this environment
        zone_codes = self.ZONE_MAPPING.get(env_key, self.ZONE_MAPPING["other"])
        
        # Try to infer layer type from server name patterns
        server_lower = server_name.lower()
        
        # Database Layer
        if any(pattern in server_lower for pattern in ["db", "database", "sql", "oracle", "mysql", "postgres", "mongo", "redis", "cache"]):
            zone_code = zone_codes["database_layer"]
            return f"Azure Subnet (Database Layer)({zone_code})"
        
        # Presentation Layer (Web/Frontend)
        elif any(pattern in server_lower for pattern in ["web", "www", "frontend", "fe", "ui", "portal", "gateway", "lb", "loadbalancer"]):
            zone_code = zone_codes["presentation_layer"]
            return f"Azure Subnet (Presentation Layer)({zone_code})"
        
        # Middleware Layer
        elif any(pattern in server_lower for pattern in ["mq", "queue", "kafka", "rabbit", "bus", "esb", "middleware", "integration"]):
            zone_code = zone_codes["middleware_layer"]
            return f"Azure Subnet (Middleware Layer)({zone_code})"
        
        # Application Layer (default for app servers)
        elif any(pattern in server_lower for pattern in ["app", "application", "api", "svc", "service", "backend", "be"]):
            zone_code = zone_codes["application_layer"]
            return f"Azure Subnet (Application Layer)({zone_code})"
        
        # File/Storage servers - use database layer (data tier)
        elif any(pattern in server_lower for pattern in ["file", "storage", "nas", "san", "backup"]):
            zone_code = zone_codes["database_layer"]
            return f"Azure Subnet (Database Layer)({zone_code})"
        
        # Default - Application Layer
        else:
            zone_code = zone_codes["application_layer"]
            return f"Azure Subnet (Application Layer)({zone_code})"
    
    def _build_flows_for_server(self, server_name: str, server_deps: Dict, 
                                 start_flow_id: int, environment: str = "production") -> List[Dict[str, Any]]:
        """
        Build communication flows for a server being migrated to Azure.
        
        MIGRATION LOGIC:
        - The server being analyzed (server_name) is the one being migrated to Azure
        - After migration, it becomes an Azure resource with Azure zone naming and placeholder IP
        - All its dependencies (connected servers) remain on-premises with actual IPs
        
        OUTBOUND connections (server sends TO other servers):
        - Source = Azure (migrated server) with placeholder IP
        - Destination = On-prem servers with actual IPs
        
        INBOUND connections (other servers send TO this server):
        - Source = On-prem servers with actual IPs  
        - Destination = Azure (migrated server) with placeholder IP
        """
        flows = []
        flow_counter = start_flow_id
        
        # The server being analyzed will be migrated to Azure
        # Generate Azure zone name for this server with environment context
        azure_zone = self._get_azure_zone_name(server_name, server_deps, environment)
        azure_placeholder_ip = "xx.xx.xx.xx/xx"
        
        # Process OUTBOUND connections (migrated server sends TO on-prem servers)
        # After migration: Azure (source) → On-prem (destination)
        outbound = server_deps.get("outbound_connections", [])
        for conn in outbound:
            if not isinstance(conn, dict):
                continue
                
            dest_hostname = conn.get("dest_hostname", "")
            dest_ip = conn.get("dest_ip", "")
            port = conn.get("port", "")
            protocol = conn.get("protocol", "TCP")
            
            # Skip empty connections
            if not dest_hostname and not dest_ip:
                continue
            
            flow = {
                "Flow_ID": f"F{flow_counter}",
                "Sub_Flow": "",
                "Status": "Add",
                # Source is the migrated server (now Azure)
                "Source_IP_Address_Group": azure_placeholder_ip,
                "Source_Hostname": azure_zone,
                # Destination is on-prem server with actual IP
                "Destination_IP_Address_Group": dest_ip,
                "Destination_Hostname": dest_hostname,
                "Ports": str(port),
                "Protocol": protocol or "TCP",
                "Remark": f"Outbound: {server_name} (migrated to Azure) to {dest_hostname} (on-prem)",
                "Environment_Source_Zone": azure_zone,
                "Environment_Destination_Zone": "",
                "Flow_Type": "Outbound"
            }
            flows.append(flow)
            flow_counter += 1
        
        # Process INBOUND connections (on-prem servers send TO migrated server)
        # After migration: On-prem (source) → Azure (destination)
        inbound = server_deps.get("inbound_connections", [])
        for conn in inbound:
            if not isinstance(conn, dict):
                continue
                
            source_hostname = conn.get("source_hostname", "")
            source_ip = conn.get("source_ip", "")
            port = conn.get("port", "")
            protocol = conn.get("protocol", "TCP")
            
            # Skip empty connections
            if not source_hostname and not source_ip:
                continue
            
            flow = {
                "Flow_ID": f"F{flow_counter}",
                "Sub_Flow": "",
                "Status": "Add",
                # Source is on-prem server with actual IP
                "Source_IP_Address_Group": source_ip,
                "Source_Hostname": source_hostname,
                # Destination is the migrated server (now Azure)
                "Destination_IP_Address_Group": azure_placeholder_ip,
                "Destination_Hostname": azure_zone,
                "Ports": str(port),
                "Protocol": protocol or "TCP",
                "Remark": f"Inbound: {source_hostname} (on-prem) to {server_name} (migrated to Azure)",
                "Environment_Source_Zone": "",
                "Environment_Destination_Zone": azure_zone,
                "Flow_Type": "Inbound"
            }
            flows.append(flow)
            flow_counter += 1
        
        return flows
    
    def _deduplicate_flows(self, flows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove duplicate flows based on source, destination, and port combination."""
        seen = set()
        unique_flows = []
        
        for flow in flows:
            # Create a key from source, destination, and port
            key = (
                flow.get("Source_Hostname", ""),
                flow.get("Source_IP_Address_Group", ""),
                flow.get("Destination_Hostname", ""),
                flow.get("Destination_IP_Address_Group", ""),
                flow.get("Ports", ""),
                flow.get("Protocol", "")
            )
            
            if key not in seen:
                seen.add(key)
                unique_flows.append(flow)
        
        return unique_flows
    
    async def _collect_run_response(self, client, run_id: str) -> str:
        """Collect response from a completed run."""
        try:
            messages_iter = client.agents.messages.list(
                thread_id=self.thread_id,
                order=ListSortOrder.ASCENDING
            )
            
            collected_text = []
            async for msg in messages_iter:
                if getattr(msg, 'run_id', None) != run_id:
                    continue
                    
                role = getattr(msg, 'role', None)
                role_lower = str(role).lower() if role else ''
                if not any(r in role_lower for r in ("assistant", "agent")):
                    continue
                
                # Extract text content
                text_messages = getattr(msg, 'text_messages', None)
                if text_messages:
                    for tm in text_messages:
                        try:
                            text_val = getattr(getattr(tm, 'text', None), 'value', None)
                            if text_val:
                                collected_text.append(text_val)
                        except Exception:
                            continue
            
            return "\n".join(collected_text) if collected_text else "No response captured"
            
        except Exception as ex:
            logger.error(f"Failed to collect run result: {ex}")
            return f"Error collecting result: {ex}"
        
    def _load_enabler_ips(self, enabler_json_path: str) -> set:
        """Load all source/destination IPs, ranges, and subnets from enablers_italy.json."""
        with open(enabler_json_path, 'r', encoding='utf-8') as f:
            enablers = json.load(f)
        ip_set = set()
        for entry in enablers:
            for key in ["SourceIPaddress", "DestinationIPaddress"]:
                val = entry.get(key)
                if val:
                    for ip_str in val.split("\n"):
                        ip_str = ip_str.strip()
                        if not ip_str:
                            continue
                        try:
                            # Add both IP and subnet objects for comparison
                            if "/" in ip_str:
                                ip_set.add(ipaddress.ip_network(ip_str, strict=False))
                            else:
                                ip_set.add(ipaddress.ip_address(ip_str))
                        except Exception:
                            pass
        return ip_set
 
    def _is_ip_excluded(self, ip_str, ip_set):
        """Check if ip_str matches any IP/subnet in ip_set."""
        ip_str = ip_str.strip()
        if not ip_str:
            return False
        try:
            if "/" in ip_str:
                ip_obj = ipaddress.ip_network(ip_str, strict=False)
                for enabler in ip_set:
                    if isinstance(enabler, ipaddress._BaseNetwork):
                        if ip_obj.subnet_of(enabler) or ip_obj.supernet_of(enabler):
                            return True
                    elif isinstance(enabler, ipaddress._BaseAddress):
                        if ip_obj.supernet_of(ipaddress.ip_network(str(enabler) + "/32")):
                            return True
            else:
                ip_obj = ipaddress.ip_address(ip_str)
                for enabler in ip_set:
                    if isinstance(enabler, ipaddress._BaseNetwork):
                        if ip_obj in enabler:
                            return True
                    elif isinstance(enabler, ipaddress._BaseAddress):
                        if ip_obj == enabler:
                            return True
        except Exception:
            pass
        return False
 
    def _filter_excluded_flows(self, flows, ip_set):
        """Remove flows where any Source/Destination IP matches or is in excluded range/subnet."""
        filtered = []
        removed = []
        for flow in flows:
            src_ips = str(flow.get("Source_IP_Address_Group", flow.get("SourceIPaddress", ""))).split("\n")
            dst_ips = str(flow.get("Destination_IP_Address_Group", flow.get("DestinationIPaddress", ""))).split("\n")
            exclude = False
            for ip in src_ips + dst_ips:
                if self._is_ip_excluded(ip, ip_set):
                    exclude = True
                    break
            if not exclude:
                filtered.append(flow)
            else:
                removed.append(flow)
        logger.info(f"Excluded {len(removed)} flows out of {len(flows)} total flows due to enabler IP match.")
        if removed:
            logger.debug(f"Sample excluded flow(s): {json.dumps(removed[:3], indent=2)}")
        return filtered
 
    async def _compile_communications_matrix(self, application_id: str, flows: List[Dict[str, Any]], 
                                               output_folder: str,
                                               environment: str = "production", 
                                               enabler_ip_set: set = None) -> Dict[str, Any]:
        """Compile the final communications matrix from flows and upload to blob storage.
        
        Args:
            application_id: The application ID
            flows: List of flow dictionaries
            output_folder: Folder prefix for blob storage upload
            environment: Environment name (e.g., 'production', 'development')
            enabler_ip_set: Set of IPs to exclude from flows
        """""
       
        # # Exclusion logic: filter out flows matching enabler IPs/ranges
        # if enabler_ip_set:
        #     flows = self._filter_excluded_flows(flows, enabler_ip_set)
        
        # If no structured flows found, log warning
        if not flows:
            exception_msg = f"No structured flows extracted for environment '{environment}', add the required data to the index or check analysis results."
            logger.warning(exception_msg)
        
        matrix = {
            "status": "success",
            "matrix_type": "communications_flows",
            "app_id": application_id,
            "environment": environment,
            "generated_at": time.time(),
            "metadata": {
                "total_flows": len(flows),
                "environment": environment,
                "generation_method": "server_dependency_extraction",
                "data_source": "azure_migrate_dependencies"
            },
            "flows": flows
        }
        
        # Generate Excel file from the matrix data (with environment in filename)
        excel_filepath = None
        try:
            excel_filepath = self._generate_excel_from_matrix(matrix, application_id, environment)
            matrix["metadata"]["excel_file_path"] = excel_filepath
            matrix["metadata"]["excel_generated"] = True
            logger.info(f"Excel file generated and saved to: {excel_filepath}")
        except Exception as ex:
            logger.error(f"Failed to generate Excel file: {ex}")
            matrix["metadata"]["excel_generated"] = False
            matrix["metadata"]["excel_error"] = str(ex)
        
        # Upload Excel file to blob storage
        if excel_filepath and os.path.exists(excel_filepath):
            try:
                excel_filename = os.path.basename(excel_filepath)
                blob_url = await upload_file_to_container_async(
                    file_path=excel_filepath,
                    app_id=application_id,
                    blob_name=excel_filename,
                    folder_prefix=output_folder,
                    enable_versioning=True
                )
                matrix["metadata"]["blob_url"] = blob_url
                matrix["metadata"]["blob_uploaded"] = True
                logger.info(f"✅ Excel file uploaded to blob storage: {blob_url}")
                
                # Delete local file after successful upload
                try:
                    os.remove(excel_filepath)
                    matrix["metadata"]["local_file_deleted"] = True
                    matrix["metadata"]["excel_file_path"] = None  # Clear local path since file is deleted
                    logger.info(f"🗑️ Deleted local file after successful upload: {excel_filepath}")
                except Exception as del_ex:
                    matrix["metadata"]["local_file_deleted"] = False
                    logger.warning(f"Could not delete local file: {del_ex}")
                    
            except Exception as upload_ex:
                logger.warning(f"⚠️ Failed to upload Excel file to blob storage: {upload_ex}")
                matrix["metadata"]["blob_uploaded"] = False
                matrix["metadata"]["blob_error"] = str(upload_ex)
        
        logger.info(f"Compiled communications matrix for {application_id} ({environment}) with {len(flows)} flows")
        
        return matrix
    
    def _extract_flows_from_analysis(self, analysis_results: List[str]) -> List[Dict[str, Any]]:
        """Extract structured flow data from agent analysis results using utility function."""
        flows = []
        
        for result in analysis_results:
            if not result:
                continue
            
            # Use the utility function to extract JSON
            parsed_data = extract_json_from_text(result)
            
            if parsed_data:
                # Extract flows array
                if isinstance(parsed_data, dict) and "flows" in parsed_data:
                    flows.extend(parsed_data["flows"])
                elif isinstance(parsed_data, list):
                    flows.extend(parsed_data)
            else:
                # If JSON parsing fails, try to extract structured data manually
                parsed_flows = self._parse_flows_from_text(result)
                flows.extend(parsed_flows)
        
        return flows
    
    def _parse_flows_from_text(self, text: str) -> List[Dict[str, Any]]:
        """Parse flow information from unstructured text."""
        import re
        flows = []
        
        # Look for patterns like "Source: X -> Destination: Y" or "IP: X.X.X.X Port: Y"
        lines = text.split('\n')
        current_flow = {}
        flow_counter = 1
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            # Look for key patterns
            if "source" in line.lower() and ("ip" in line.lower() or "server" in line.lower()):
                if current_flow:
                    flows.append(current_flow)
                current_flow = {
                    "Flow_ID": f"F{flow_counter}",
                    "Status": "Add",
                    "Protocol": "TCP",
                    "Environment": "Production"
                }
                flow_counter += 1
                
            # Extract specific fields
            if "port" in line.lower():
                # Extract port numbers
                ports = re.findall(r'\b\d{2,5}\b', line)
                if ports:
                    current_flow["Ports"] = ports[0]
            
            if "protocol" in line.lower():
                if "tcp" in line.lower():
                    current_flow["Protocol"] = "TCP"
                elif "udp" in line.lower():
                    current_flow["Protocol"] = "UDP"
        
        # Add the last flow
        if current_flow:
            flows.append(current_flow)
            
        return flows
    
    def _generate_excel_from_matrix(self, matrix_data: Dict[str, Any], application_id: str, 
                                     environment: str = "production") -> str:
        """
        Generate an Excel file from the communications matrix data.
        
        Args:
            matrix_data: The communications matrix dictionary containing flows
            application_id: The application ID for file naming
            environment: The environment name for file naming
            
        Returns:
            str: Path to the generated Excel file
        """
        try:
            # Define the exact column headers as specified (removed: Src/Dst, IP address, Environment, Mandatory)
            column_headers = [
                "Flow ID",
                "Sub Flow", 
                "Status",
                "Source IP Address / Source Group",
                "Source Hostname",
                "Destination IP Address / Destination Group",
                "Destination Hostname",
                "Port(s)",
                "Protocol",
                "Remark",
                "Environment Source Zone",
                "Environment Destination Zone",
                "Flow Type"
            ]
            
            # Extract flows from matrix data
            flows = matrix_data.get("flows", [])
            
            # Prepare data for Excel
            excel_data = []
            
            for flow in flows:
                # Map the flow data to the column headers
                # Handle different possible key names in the flow data
                row_data = {
                    "Flow ID": flow.get("Flow_ID") or flow.get("Flow ID") or "",
                    "Sub Flow": flow.get("Sub_Flow") or flow.get("Sub Flow") or "",
                    "Status": flow.get("Status") or "Add",
                    "Source IP Address / Source Group": flow.get("Source_IP_Address_Group") or flow.get("Source IP Address / Source Group") or "",
                    "Source Hostname": flow.get("Source_Hostname") or flow.get("Source Hostname") or "",
                    "Destination IP Address / Destination Group": flow.get("Destination_IP_Address_Group") or flow.get("Destination IP Address / Destination Group") or "",
                    "Destination Hostname": flow.get("Destination_Hostname") or flow.get("Destination Hostname") or "",
                    "Port(s)": flow.get("Ports") or flow.get("Port(s)") or "",
                    "Protocol": flow.get("Protocol") or "TCP",
                    "Remark": flow.get("Remark") or "",
                    "Environment Source Zone": flow.get("Environment_Source_Zone") or flow.get("Environment Source Zone") or "",
                    "Environment Destination Zone": flow.get("Environment_Destination_Zone") or flow.get("Environment Destination Zone") or "",
                    "Flow Type": flow.get("Flow_Type") or flow.get("Flow Type") or ""
                }
                excel_data.append(row_data)
            
            # Create DataFrame
            df = pd.DataFrame(excel_data, columns=column_headers)
            
            # Generate filename with environment suffix
            # Sanitize environment name for filename
            env_suffix = environment.replace(" ", "_").replace("/", "_").replace("\\", "_")
            excel_filename = f"communications_matrix_{application_id}_{env_suffix}.xlsx"
            
            # Create output directory if it doesn't exist
            output_dir = os.path.join(os.path.dirname(__file__), "..", "Outputs")
            os.makedirs(output_dir, exist_ok=True)
            
            excel_filepath = os.path.join(output_dir, excel_filename)
            
            # Create Excel file with formatting
            with pd.ExcelWriter(excel_filepath, engine='openpyxl') as writer:
                df.to_excel(writer, sheet_name='Communications Matrix', index=False)
                
                # Get the worksheet to apply formatting
                worksheet = writer.sheets['Communications Matrix']
                
                # Auto-adjust column widths
                for column in worksheet.columns:
                    max_length = 0
                    column_letter = column[0].column_letter
                    for cell in column:
                        try:
                            if len(str(cell.value)) > max_length:
                                max_length = len(str(cell.value))
                        except:
                            pass
                    adjusted_width = min(max_length + 2, 50)  # Cap at 50 characters
                    worksheet.column_dimensions[column_letter].width = adjusted_width
                
                # Apply header formatting
                from openpyxl.styles import Font, PatternFill
                header_font = Font(bold=True)
                header_fill = PatternFill(start_color="D3D3D3", end_color="D3D3D3", fill_type="solid")
                
                for cell in worksheet[1]:
                    cell.font = header_font
                    cell.fill = header_fill
            
            logger.info(f"Excel file generated successfully: {excel_filepath}")
            return excel_filepath
            
        except Exception as ex:
            logger.error(f"Failed to generate Excel file: {ex}")
            raise Exception(f"Excel generation failed: {str(ex)}")
    
    async def _cleanup(self, client):
        """Clean up the agent and thread."""
        try:
            if self.thread_id:
                await client.agents.threads.delete(thread_id=self.thread_id)
                logger.debug(f"Deleted thread: {self.thread_id}")
            
            if self.agent_id:
                await client.agents.delete_agent(self.agent_id)
                logger.debug(f"Deleted agent: {self.agent_id}")
                
        except Exception as ex:
            logger.warning(f"Cleanup warning: {ex}")


# Standalone function for external use
@trace_async_function("run_comms_matrix_agent")
async def run_comms_matrix_agent(application_id: str, output_folder: str, client=None, storage_account_name: str = "") -> Dict[str, Any]:
    """
    Run the communications matrix agent for the specified application.
    
    Note: Indexing should be handled by the orchestrator before calling this function.
    
    Args:
        application_id: The application ID to analyze (index should already exist)
        output_folder: Folder prefix for blob storage upload (e.g., "design/output/")
        client: Optional Azure AI client
        storage_account_name: Optional storage account name for blob storage
        
    Returns:
        Dict containing the communications matrix with flows array and Excel file path
    """
    logger.info(f"Starting communications matrix agent for application_id: {application_id}, output_folder: {output_folder}")
    
    try:
        agent = CommsMatrixAgent(client)
        result = await agent.generate_communications_matrix(application_id, output_folder=output_folder, storage_account_name=storage_account_name)
        
        logger.info(f"Communications matrix generation completed successfully for application {application_id}")
        logger.info(f"Generated {len(result.get('flows', []))} communication flows")
        
        # Log Excel file generation result
        if result.get("metadata", {}).get("excel_generated"):
            logger.info(f"Excel file generated at: {result['metadata']['excel_file_path']}")
            
            # Log blob storage upload result
            if result.get("metadata", {}).get("blob_uploaded"):
                logger.info(f"Excel file uploaded to blob: {result['metadata'].get('blob_url')}")
            else:
                blob_error = result.get("metadata", {}).get("blob_error", "Unknown error")
                logger.warning(f"Excel file blob upload failed: {blob_error}")
        else:
            logger.warning("Excel file generation failed or was skipped")
        
        return result
        
    except Exception as ex:
        logger.error(f"Error in communications matrix agent for {application_id}: {str(ex)}")
        return {
            "status": "error",
            "matrix_type": "communications_flows",
            "app_id": application_id,
            "error_message": str(ex),
            "generated_at": time.time(),
            "flows": [],
            "metadata": {
                "excel_generated": False,
                "excel_error": "Agent execution failed"
            }
        }


# Cleanup utility function for external use
async def cleanup_comms_matrix_agent(application_id: str, client=None) -> Dict[str, Any]:
    """
    Clean up a communications matrix agent and associated resources.
    
    Args:
        application_id: The application ID to clean up
        client: Optional Azure AI client
        
    Returns:
        Dict containing cleanup result status
    """
    return await cleanup_agent(
        application_id=application_id,
        agent_type="CommsMatrix-Agent",
        client=client
    )


# Main function for standalone execution
async def main() -> None:
    """Main function for standalone execution"""
    try:
        # Example usage - Note: index should be created beforehand
        application_id = "comms001"
        
        result = await run_comms_matrix_agent(application_id)
        
        logger.info(f"Communications matrix generation result: {result['status']}")
        logger.info(f"Generated flows: {len(result.get('flows', []))}")
        
        # Check if Excel file was generated
        if result.get("metadata", {}).get("excel_generated"):
            print(f"✓ Excel file generated at: {result['metadata']['excel_file_path']}")
            
            # Check blob upload status
            if result.get("metadata", {}).get("blob_uploaded"):
                print(f"✓ Excel file uploaded to blob: {result['metadata'].get('blob_url')}")
            else:
                blob_error = result.get("metadata", {}).get("blob_error", "Unknown error")
                print(f"✗ Excel file blob upload failed: {blob_error}")
        else:
            excel_error = result.get("metadata", {}).get("excel_error", "Unknown error")
            print(f"✗ Excel file generation failed: {excel_error}")
        
        # Pretty print the result
        print(json.dumps(result, indent=2))
        
    except Exception as ex:
        logger.error(f"Error in communications matrix agent main execution: {str(ex)}")


if __name__ == "__main__":
    asyncio.run(main())
