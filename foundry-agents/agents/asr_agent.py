from azure.storage.blob import BlobServiceClient
from azure.identity import DefaultAzureCredential
from azure.data.tables import TableServiceClient
import os
import sys
import logging
import datetime
from azure.ai.agents.models import McpTool

# Import utility functions
from agents.utils.common_utils import (
    upload_file_to_container,
    responses_json_to_markdown,
    download_template_from_storage,
    process_prompts_from_json,
    create_response_file,
    get_storage_account_url,
    load_instructions_from_file
)
from agents.utils.agent_utils import (
    find_existing_agent,
    cleanup_agent,
    build_agent_name,
    AgentClientManager,
    configure_search_tool,
    SearchToolConfig,
    create_agent_with_search_tool
)


def check_k8s_table_exists(application_id: str) -> bool:
    """
    Check if the K8S table exists for the given application ID.
    
    Args:
        application_id: The application ID to check
    
    Returns:
        bool: True if the K8S table exists, False otherwise
    """
    from azure.identity import DefaultAzureCredential as SyncDefaultAzureCredential
    
    k8s_table_name = f"K8S{application_id}"
    tables_url = os.getenv("AZURE_TABLES_ACCOUNT_URL")
    
    if not tables_url:
        logger.warning("AZURE_TABLES_ACCOUNT_URL not configured, assuming K8S table does not exist")
        return False
    
    try:
        credential = SyncDefaultAzureCredential(exclude_shared_token_cache_credential=True)
        table_service_client = TableServiceClient(endpoint=tables_url, credential=credential)
        table_client = table_service_client.get_table_client(table_name=k8s_table_name)
        # Check if K8S table exists by attempting to list one entity
        next(table_client.list_entities(results_per_page=1), None)
        logger.info(f"K8S table found: {k8s_table_name}")
        return True
    except Exception as ex:
        error_str = str(ex)
        if "TableNotFound" in error_str or "ResourceNotFound" in error_str:
            logger.info(f"K8S table not found: {k8s_table_name} - Kubernetes section will be skipped")
        else:
            logger.warning(f"Error checking K8S table existence for {k8s_table_name}: {error_str}")
        return False


def renumber_sections_after_removal(sections: list, removed_section_number: int) -> list:
    """
    Renumber section IDs after a section has been removed.
    
    When a section is removed (e.g., Kubernetes section "5 Kubernetes Assessment Summary"),
    all subsequent sections need their numbers decremented by 1.
    
    Args:
        sections: List of section dictionaries with 'id' field
        removed_section_number: The main section number that was removed (e.g., 5 for Kubernetes)
    
    Returns:
        List of sections with renumbered IDs
    
    Example:
        If Kubernetes section (5) is removed:
        - "6. Migration Strategy" becomes "5. Migration Strategy"
        - "6.1 Migration Pattern" becomes "5.1 Migration Pattern"
        - "7. Indicative Azure Cost" becomes "6. Indicative Azure Cost"
    """
    import re
    
    renumbered_sections = []
    
    for section in sections:
        section_id = section.get("id", "")
        
        # Match patterns like "6. Title", "6.1 Title", "6.1.2 Title", "10. Title", "10.1 Title"
        # Pattern captures: (main_number)(optional .subsection)(rest of title)
        match = re.match(r'^(\d+)((?:\.\d+)*)\s*(.*)$', section_id)
        
        if match:
            main_number = int(match.group(1))
            subsection = match.group(2)  # e.g., ".1" or ".1.2" or ""
            title_rest = match.group(3)  # e.g., "Migration Strategy"
            
            # Only renumber if this section comes after the removed section
            if main_number > removed_section_number:
                new_main_number = main_number - 1
                # Reconstruct the ID with the new number
                if subsection:
                    new_id = f"{new_main_number}{subsection} {title_rest}"
                else:
                    new_id = f"{new_main_number}. {title_rest}" if title_rest else f"{new_main_number}."
                
                # Create a copy of the section with the new ID
                updated_section = section.copy()
                updated_section["id"] = new_id
                renumbered_sections.append(updated_section)
                logger.debug(f"Renumbered section: '{section_id}' -> '{new_id}'")
            else:
                renumbered_sections.append(section)
        else:
            # If ID doesn't match expected pattern, keep as-is
            renumbered_sections.append(section)
    
    return renumbered_sections
 
def download_asr_prompt_from_storage(account_url, asr_prompt_path, migration_matrix_path):
    """
    Downloads asr_prompt.json and migration-matrix.json from the 'templates' container.
    Uses utility function for downloading.
    """
    for blob_name, local_path in [("asr_prompt.json", asr_prompt_path), ("migration-matrix.json", migration_matrix_path)]:
        try:
            download_template_from_storage(account_url, blob_name, local_path, container_name="templates")
        except Exception as e:
            logger.error(f"Error downloading blob {blob_name}: {e}")
# Copyright (c) Microsoft. All rights reserved.
 
import os
import asyncio
import shutil
import re
import json
import time
from typing import Annotated, List, Optional
from dotenv import load_dotenv
from azure.ai.projects.aio import AIProjectClient
from azure.ai.projects.models import ConnectionType
from azure.identity.aio import DefaultAzureCredential
from azure.ai.agents.models import AzureAISearchQueryType, AzureAISearchTool, ListSortOrder, MessageRole
from azure.identity.aio import AzureCliCredential
from azure.identity import DefaultAzureCredential as SyncDefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings
from semantic_kernel.contents import ChatMessageContent, FunctionCallContent, FunctionResultContent  # retained for compatibility with existing handler signatures
import contextlib
 
# Import tracing configuration
from agents.tracing_config import (
    get_tracer,
    trace_async_function,
    add_span_attributes,
)
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

# Import shared error handling utilities
from agents.error_handler import (
    get_detailed_run_error,
    should_retry_server_error,
    retry_agent_run_on_server_error
)

# Load environment variables
load_dotenv()

# Import logging configuration
sys.path.insert(0, os.path.dirname(__file__))
from agents.logging_config import get_logger

# Create logger for this module
logger = get_logger(__name__)

logger.info("ASR Agent initialized")
 
# NOTE: upload_file_to_container, responses_json_to_markdown, process_prompts, 
# and create_response_file have been moved to agents.utils.common_utils
# Import them at the top of the file using the utility imports.

# Create wrapper functions that use the utilities with ASR-specific defaults
def upload_asr_file_to_container(file_path: str, app_id: str, blob_name: str = None) -> str:
    """Upload a file to the ASR output folder in the application's container."""
    return upload_file_to_container(file_path, app_id, blob_name, folder_prefix="asr/output/")


def process_asr_prompts(json_file: str) -> list:
    """Process prompts from ASR JSON file."""
    return process_prompts_from_json(json_file)


def create_asr_response_file(responses: list, prompt_file: str, app_id: str) -> dict:
    """Create ASR response file."""
    return create_response_file(responses, prompt_file, app_id, output_prefix="asr-report")


 
 
 
 
 
"""The ASR agent now relies solely on the unified search index that already contains
exported table rows (via JSONL snapshot) and uploaded documents. Direct table reads
have been removed to avoid duplication and reduce latency."""
 
async def handle_streaming_intermediate_steps(message: ChatMessageContent) -> None:
    for item in message.items or []:
        if isinstance(item, FunctionResultContent):
            logger.info(f"ASR agent function result: {item.name} returned: {str(item.result)[:500]}{'...' if len(str(item.result)) > 500 else ''}")
            logger.debug(f"Function Result:> {item.result} for function: {item.name}")
        elif isinstance(item, FunctionCallContent):
            logger.info(f"ASR agent function call: {item.name} with arguments: {item.arguments}")
            logger.debug(f"Function Call:> {item.name} with arguments: {item.arguments}")
        else:
            logger.debug(f"ASR agent intermediate message item: {item}")
            logger.debug(f"{item}")

# ASR-specific wrapper functions using utilities
@trace_async_function("find_existing_asr_agent")
async def _find_existing_asr_agent(client, application_id: str):
    """Find an existing ASR agent by name pattern using utility function."""
    agent_name = build_agent_name("ASRAgent", application_id)
    return await find_existing_agent(client, agent_name, application_id)


@trace_async_function("cleanup_asr_agent")
async def cleanup_asr_agent(application_id: str, thread_id: str, client=None, agent_id: str = None) -> dict:
    """
    Clean up the ASR agent and all associated threads using utility function.
    
    Args:
        application_id: The application ID to clean up
        thread_id: The thread ID to delete
        client: (Optional) The Azure AI client
        agent_id: (Optional) The specific agent ID to delete
    
    Returns:
        dict: Result containing status and cleanup details
    """
    return await cleanup_agent(
        application_id=application_id,
        agent_type="ASRAgent",
        thread_id=thread_id,
        client=client,
        agent_id=agent_id,
        find_existing_fn=_find_existing_asr_agent
    )
        # finally:
        #     # Close client if we created it
        #     if client_created and hasattr(client, 'close'):
        #         try:
        #             await client.close()
        #         except:
        #             pass

@trace_async_function("run_asr_agent")
async def run_asr_agent(application_id: str, client=None, thread=None, progress_callback: Optional[callable] = None) -> dict:
    """
    Run the ASR agent with the provided application ID and thread.
   
    Args:
        application_id: The application ID to process
        client: (Optional) The Azure AI client. If None, a new client will be created inside the function.
        thread: Optional thread to use (if None, a new one will be created)
        progress_callback: Optional async callback function(message: str, percentage: int)
                          for reporting progress during long-running operations
   
    Returns:
        dict: Result containing status, output files, and blob URL
    """
    logger.info(f"Starting ASR agent for application_id: {application_id}")
    thread_id = None  # initialize early for exception paths
    tracer = get_tracer()

    # Use AgentClientManager for proper resource cleanup
    async with AgentClientManager(existing_client=client) as manager:
        client = manager.client
        
        default_conn = await client.connections.get_default(ConnectionType.AZURE_AI_SEARCH)
        conn_id = default_conn.id if default_conn and hasattr(default_conn, 'id') else None
        if not conn_id:
            raise RuntimeError("Could not get Azure AI Search connection")
        logger.debug(f"Using connection ID for AI search {conn_id}")

        with tracer.start_as_current_span("asr_agent_initialization") as init_span:
            add_span_attributes(init_span, {
                "asr.application_id": application_id,
                "asr.thread_provided": thread is not None
            })
           
            try:
                # Configure search tool
                index_name = f"{application_id}"
                search_config = SearchToolConfig(
                    index_name=index_name,
                    query_type=AzureAISearchQueryType.SEMANTIC,
                    top_k=20,
                    filter="",
                    field_mapping={
                        "contentFields": ["content", "metadata"],
                        "titleField": "title",
                        "urlField": "source",
                        "vectorFields": ["contentVector"]
                    }
                )
                
                # Load instructions using utility
                current_dir = os.path.dirname(__file__)
                instruction_file = os.path.join(current_dir, "agent-instructions", "asr_agent.txt")
                base_instructions = load_instructions_from_file(
                    instruction_file,
                    placeholder_replacements={"application_id": application_id}
                )
                
                # Prepare MCP tool definitions for conditional use 
                allowed_mcp_raw = os.getenv("MCP_ALLOWED_SERVERS", "").strip()
                allowed_mcp = [s.strip() for s in allowed_mcp_raw.split(',') if s.strip()] if allowed_mcp_raw else []
                mcp_tool_definitions = []
                mcp_labels_added = []
                if allowed_mcp:
                    try:
                        from agents.mcp_tools import build_mcp_tool_definitions
                        mcp_tool_definitions, mcp_labels_added = build_mcp_tool_definitions(allowed_mcp)
                        if mcp_labels_added:
                            logger.debug(f"[ASR Agent] MCP tools prepared for conditional use: {', '.join(mcp_labels_added)}")
                        else:
                            logger.debug("[ASR Agent] No MCP tools available after evaluation.")
                    except Exception as mcp_ex:
                        logger.error(f"[ASR Agent] Failed to prepare MCP tool definitions: {mcp_ex}")
                else:
                    logger.warning("[ASR Agent] No MCP_ALLOWED_SERVERS configured; MCP tools disabled.")

                logger.debug(f"Agent instructions length: {len(base_instructions)} characters")
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(f"Agent instructions:\n{base_instructions}")
                
                # Create or update agent using utility (includes search tool configuration)
                agent_result = await create_agent_with_search_tool(
                    client=client,
                    agent_name="ASRAgent",
                    application_id=application_id,
                    instructions=base_instructions,
                    search_tool_config=search_config,
                    temperature=0.1,
                    find_existing=True
                )
                agent_definition = agent_result.agent
                ai_search = agent_result.search_tool
                
                if agent_result.is_new:
                    logger.debug(f"Created ASR agent {agent_definition.id} with search tool")
                else:
                    logger.debug(f"Updated ASR agent {agent_definition.id} with search tool")

                # 2. Create (or reuse) a thread using async API
                if thread is None:
                    # Using async AIProjectClient with await
                    thread = await client.agents.threads.create()
                    thread_id = thread.id
 
                import json
 
 
                prompt_file = "asr_prompt.json"
                app_id = application_id
 
                # Download the prompt file from Azure Storage if not present
               
                account_url = os.getenv("AZURE_STORAGE_ACCOUNT_URL")
                   
                migration_matrix_file = "migration-matrix.json"
                download_asr_prompt_from_storage(account_url, prompt_file, migration_matrix_file)
 
                # Load prompts and table names
                with open(prompt_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                sections = data.get("sections_array", [])
                
                # Check if K8S table exists for this application
                k8s_table_exists = check_k8s_table_exists(application_id)
                
                # Filter out Kubernetes section if K8S table doesn't exist and renumber subsequent sections
                if not k8s_table_exists:
                    original_count = len(sections)
                    
                    # Find the Kubernetes section number before filtering (e.g., "5 Kubernetes..." -> 5)
                    kubernetes_section_number = None
                    for section in sections:
                        section_id = section.get("id", "")
                        if "kubernetes" in section_id.lower():
                            # Extract the main section number (e.g., "5 Kubernetes Assessment Summary" -> 5)
                            import re
                            match = re.match(r'^(\d+)', section_id)
                            if match:
                                kubernetes_section_number = int(match.group(1))
                            break
                    
                    # Filter out Kubernetes section(s)
                    sections = [
                        section for section in sections 
                        if "kubernetes" not in section.get("id", "").lower()
                    ]
                    filtered_count = original_count - len(sections)
                    
                    if filtered_count > 0:
                        logger.info(f"Filtered out {filtered_count} Kubernetes section(s) - K8S table not found for app {application_id}")
                        
                        # Renumber subsequent sections if we found and removed the Kubernetes section
                        if kubernetes_section_number is not None:
                            sections = renumber_sections_after_removal(sections, kubernetes_section_number)
                            logger.info(f"Renumbered sections after Kubernetes section {kubernetes_section_number} removal")
                else:
                    logger.info(f"K8S table exists - including Kubernetes section for app {application_id}")
                
                responses = []
                total_sections = len(sections)

                def section_needs_mcp_tools(section):
                    """Check if a section requires MCP tools based on its knowledge configuration"""
                    knowledge = section.get("knowledge", {})
                    if not isinstance(knowledge, dict):
                        return False
                    
                    # Check if 'mcp' field exists and has azurepricing
                    mcp_tools = knowledge.get("mcp", [])
                    if not isinstance(mcp_tools, list):
                        return False
                    
                    # Look for azurepricing or azure-pricing in the mcp array
                    mcp_tools_lower = [tool.lower() for tool in mcp_tools if isinstance(tool, str)]
                    needs_mcp = any(tool in ["azurepricing", "azure-pricing", "azure-pricing-calculator"] 
                                   for tool in mcp_tools_lower)
                    
                    if needs_mcp:
                        section_id = section.get('id', 'unknown')
                        logger.debug(f"[ASR Agent] Section {section_id} requires MCP tools: {mcp_tools}")
                    
                    return needs_mcp

                # Create initial prompt with dynamic application_id
                initial_prompt = f"Using the attached AI Search knowledge, Always perform a search query with the attached tool, even if you think the result will be empty. Never skip the tool call,"
           
 
                for section_idx, section in enumerate(sections):
                    prompt = section.get("prompt", "")

                    if prompt:
                        prompt = initial_prompt + " " + prompt
                    section_id = section.get('id', 'unknown')
                    
                    # Get the knowledge object (dict with "document" and "mcp" fields)
                    knowledge = section.get("knowledge", {})
                    # Extract files from the "document" field
                    knowledge_files = knowledge.get("document", []) if isinstance(knowledge, dict) else []
                    knowledge_content = {}
                    for fname in knowledge_files:
                        try:
                            with open(fname, 'r', encoding='utf-8') as kf:
                                knowledge_content[fname] = kf.read()
                        except Exception as kex:
                            knowledge_content[fname] = f"Error reading file: {kex}"
                    
                    # Check if section needs MCP and log the configuration
                    mcp_config = knowledge.get("mcp", []) if isinstance(knowledge, dict) else []
                    logger.debug(f"[ASR Agent] Processing section {section_id} - MCP config: {mcp_config}")
                    
                    if not prompt:
                        responses.append("")
                        continue
                    # guidance_suffix = (
                    #     "\n\nInstructions: Use the search tool to retrieve only relevant facts. "
                    #     "Search iteratively if needed. Base all content strictly on retrieved passages. "
                    #     "If nothing is found, respond with: No relevant information found in the provided data."
                    # )
                    # if guidance_suffix not in prompt:
                    #     bulk_prompt  = prompt + guidance_suffix
                    # else:
                    #     bulk_prompt  = prompt
                    # Append knowledge content to the user message if present
                    if knowledge_content:
                        prompt  += "\n\nKnowledge Content:\n"
                        for fname, content in knowledge_content.items():
                          prompt  += f"\n--- {fname} ---\n{content}\n"
                    logger.info(f"Sending prompt to ASR agent (length: {len(prompt )} chars) for section: {section.get('id', 'unknown')}")
                    logger.debug(f"# User: {prompt }")
                  
                    # Ensure thread exists (create if first iteration and none passed)
                    if thread is None:
                        thread = await client.agents.threads.create()
                        thread_id = thread.id
                    else:
                        thread_id = thread.id if hasattr(thread, 'id') else thread_id
                    # Create user message explicitly via messages API using async
                    created_msg = await client.agents.messages.create(
                        thread_id=thread_id,
                        role="user",
                        content=prompt
                    )
                    logger.debug(f"Created user message id: {getattr(created_msg, 'id', 'unknown')}")
                    # Create a run for this section and poll until completion
                    section_id = section.get('id', 'unknown')
                    
                    # Check if this section needs MCP tools
                    section_requires_mcp = section_needs_mcp_tools(section)
                    
                    # Prepare tools for this specific section
                    section_tools = list(ai_search.definitions)  # Always include search tools
                    
                    if section_requires_mcp and mcp_tool_definitions:
                        section_tools.extend(mcp_tool_definitions)
                        logger.debug(f"[ASR Agent] Adding MCP tools for section {section_id} ({len(section_tools)} total tools)")
                    else:
                        logger.debug(f"[ASR Agent] Using search-only tools for section {section_id} ({len(section_tools)} total tools)")

                    # Try to create and run with retry mechanism for server errors
                    try:
                        run = await client.agents.runs.create(
                            thread_id=thread_id,
                            agent_id=agent_definition.id,
                            tools=section_tools,  # Section-specific tools
                            metadata={"section_id": section_id, "mcp_enabled": str(section_requires_mcp)}
                        )
                        logger.info(f"Run created for section {section_id}: {getattr(run, 'id', 'unknown')} (MCP enabled: {section_requires_mcp})")
                    except Exception as run_ex:
                        logger.error(f"Failed to create run for section {section_id}: {run_ex}")
                        responses.append(f"Error creating run for section {section_id}: {run_ex}")
                        continue

                    # Poll run status with enhanced MCP tool handling
                    poll_start = time.time()
                    terminal_statuses = {"completed", "failed", "cancelled", "succeeded"}
                    last_status = None
                    run_completed_successfully = False

                    # Optional headers for MCP tool approvals (JSON map in env var MCP_APPROVAL_HEADERS)
                    approval_headers = {}
                    headers_env = os.getenv("MCP_APPROVAL_HEADERS", "").strip()
                    if headers_env:
                        try:
                            approval_headers = json.loads(headers_env)
                        except Exception as hdr_ex:
                            logger.warning(f"Could not parse MCP_APPROVAL_HEADERS as JSON: {hdr_ex}")
                            
                    try:
                        while True:
                            try:
                                current = await client.agents.runs.get(
                                    thread_id=thread_id,
                                    run_id=run.id
                                )
                                last_status = getattr(current, 'status', None)
                            except Exception as get_ex:
                                logger.warning(f"Error polling run status for section {section_id}: {get_ex}")
                                await asyncio.sleep(2)
                                continue

                            # Handle required MCP tool approvals
                            if last_status == "requires_action":
                                try:
                                    from agents.mcp_tools import approve_mcp_required_actions
                                    approved = await approve_mcp_required_actions(
                                        client.agents,
                                        thread_id=thread_id,
                                        run=current,
                                        headers=approval_headers,
                                        auto_approve=True,
                                    )
                                    if approved:
                                        logger.info(f"Approved MCP tool calls for run {run.id} (section {section_id})")
                                    else:
                                        logger.warning(f"Run {run.id} requires action but no approvals submitted (section {section_id})")
                                except Exception as mcp_ex:
                                    logger.error(f"Failed handling MCP required_action for run {run.id}: {mcp_ex}")
                                # After approval attempt, wait briefly then continue polling
                                await asyncio.sleep(2)
                                continue

                            if last_status in terminal_statuses:
                                break
                            # Optional: log transitions
                            logger.debug(f"Run {run.id} for section {section_id} status: {last_status}")
                            # Simple timeout (5 minutes)
                            if time.time() - poll_start > 300:
                                logger.error(f"Run timeout after 300s for section {section_id}")
                                last_status = "timeout"
                                break
                            await asyncio.sleep(2)

                        if last_status in {"completed", "succeeded"}:
                            run_completed_successfully = True
                        elif last_status == "failed":
                            # Enhanced error handling with retry mechanism for server errors
                            error_msg, error_details = get_detailed_run_error(current, f"ASR agent run for section {section_id}")
                        
                            # Check if this is a retryable error
                            if should_retry_server_error(error_msg, error_details):
                                logger.warning(f"Retryable error detected for section {section_id}, attempting retry")
                            
                                # Brief delay to ensure Azure backend has fully processed the failed run state
                                logger.debug(f"Run {run.id} failed (status: {run.status}), waiting briefly before retry")
                                await asyncio.sleep(0.5)

                                # Try retry mechanism with section-specific tools (now supported)
                                retried_run, retry_succeeded = await retry_agent_run_on_server_error(
                                  client, agent_definition.id, thread_id, 
                                  f"ASR agent run for section {section_id}",
                                  max_retries=3, base_delay=2.0,
                                  metadata={"section_id": section_id, "mcp_enabled": str(section_requires_mcp)},
                                  is_async_client=True,  # asr_agent uses async client
                                  tools=section_tools  # Pass the section-specific tools
                                )
                            
                                if retry_succeeded:
                                    logger.info(f"✅ Retry successful for section {section_id}")
                                    run = retried_run  # Use the successful retry run
                                    run_completed_successfully = True
                                else:
                                    logger.error(f"❌ Retry failed for section {section_id}: {error_msg}")
                                    responses.append(f"Run for section {section_id} failed after retries: {error_msg}")
                                    continue
                            else:
                                # Non-retryable error
                                logger.error(f"Non-retryable error for section {section_id}: {error_msg}")
                                responses.append(f"Run for section {section_id} failed: {error_msg}")
                                continue
                        else:
                            # Other terminal statuses (cancelled, timeout, etc.)
                            logger.error(f"Run ended with status {last_status} for section {section_id}")
                            responses.append(f"Run for section {section_id} ended with status {last_status}")
                            continue
                    except Exception as poll_ex:
                        logger.error(f"Exception while polling run for section {section_id}: {poll_ex}")
                        responses.append(f"Polling error for section {section_id}: {poll_ex}")
                        continue

                    # Only proceed if the run completed successfully (either on first try or after retry)
                    if not run_completed_successfully:
                        continue

                    # Retrieve assistant/agent messages generated by this run (SDK sample pattern)
                    try:
                        collected_text_parts = []
                        # After successful run completion, list run steps to log tool invocations
                        try:
                            run_steps = client.agents.run_steps.list(
                                thread_id=thread_id,
                                run_id=run.id
                            )
                            # Async iterate through the paged results
                            async for step in run_steps:
                                try:
                                    step_id = getattr(step, 'id', 'unknown')
                                    step_status = getattr(step, 'status', 'unknown')
                                    print(f"[RunStep] Section {section_id} Step {step_id} status: {step_status}")
                                    logger.info(f"Run step {step_id} status {step_status} for section {section_id}")
                                    step_details = getattr(step, 'step_details', None) or {}
                                    tool_calls = []
                                    # step_details may be dict-like or object with tool_calls attr
                                    if isinstance(step_details, dict):
                                        tool_calls = step_details.get('tool_calls', [])
                                    elif hasattr(step_details, 'tool_calls'):
                                        tool_calls = getattr(step_details, 'tool_calls') or []
                                    for call in tool_calls:
                                        try:
                                            call_id = call.get('id') if isinstance(call, dict) else getattr(call, 'id', 'unknown')
                                            call_type = call.get('type') if isinstance(call, dict) else getattr(call, 'type', 'unknown')
                                            print(f"  [ToolCall] id={call_id} type={call_type}")
                                            logger.info(f"Tool call in section {section_id}: id={call_id} type={call_type}")
                                            # Azure AI Search specific details
                                            azure_ai_search_details = None
                                            if isinstance(call, dict):
                                                azure_ai_search_details = call.get('azure_ai_search')
                                            else:
                                                azure_ai_search_details = getattr(call, 'azure_ai_search', None)
                                            if azure_ai_search_details:
                                                # Accept both dict and object attribute forms
                                                search_input = azure_ai_search_details.get('input') if isinstance(azure_ai_search_details, dict) else getattr(azure_ai_search_details, 'input', None)
                                                search_output = azure_ai_search_details.get('output') if isinstance(azure_ai_search_details, dict) else getattr(azure_ai_search_details, 'output', None)
                                                logger.info(f"AzureAISearch tool call input={search_input} output={str(search_output)[:200] if search_output else None}")
                                        except Exception as call_ex:
                                            logger.debug(f"Failed to log tool call: {call_ex}")
                                except Exception as step_ex:
                                    logger.debug(f"Failed to process run step: {step_ex}")
                        except Exception as steps_ex:
                            logger.debug(f"Could not list run steps for section {section_id}: {steps_ex}")
                        # Prefer ordered ascending to reconstruct conversation
                        messages_iter = client.agents.messages.list(
                            thread_id=thread_id,
                            order=ListSortOrder.ASCENDING if 'ASCENDING' in dir(ListSortOrder) else None
                        )
                        # Async iterate through the paged results
                        async for msg in messages_iter:
                            try:
                                # Filter by run id first
                                if getattr(msg, 'run_id', None) != run.id:
                                    continue
                                role = getattr(msg, 'role', None)
                                # Accept both MessageRole.AGENT and assistant variants
                                role_lower = str(role).lower() if role else ''
                                if not any(r in role_lower for r in ("assistant", "agent")):
                                    continue

                                # Handle citation replacement if available
                                placeholder_annotations = {}
                                url_citations = getattr(msg, 'url_citation_annotations', None)
                                if url_citations:
                                    for annotation in url_citations:
                                        try:
                                            key_text = getattr(annotation, 'text', '')
                                            citation = getattr(annotation, 'url_citation', None)
                                            if key_text and citation and getattr(citation, 'title', None):
                                                placeholder_annotations[key_text] = f" [see {citation.title}] ({getattr(citation, 'url', '')})"
                                        except Exception:
                                            continue

                                # Preferred path: iterate text_messages if present
                                text_messages = getattr(msg, 'text_messages', None)
                                if text_messages:
                                    for tm in text_messages:
                                        try:
                                            raw_val = getattr(getattr(tm, 'text', None), 'value', None)
                                            if not raw_val:
                                                continue
                                            for k, v in placeholder_annotations.items():
                                                raw_val = raw_val.replace(k, v)
                                            collected_text_parts.append(raw_val)
                                        except Exception as tm_ex:
                                            logger.debug(f"Skipping text_message due to parse error: {tm_ex}")
                                else:
                                    # Fallback to previous generic content parsing
                                    for part in getattr(msg, 'content', []) or []:
                                        text_val = None
                                        if hasattr(part, 'text'):
                                            text_attr = getattr(part, 'text')
                                            if hasattr(text_attr, 'value') and text_attr.value:
                                                text_val = text_attr.value
                                            elif isinstance(text_attr, str):
                                                text_val = text_attr
                                        if not text_val and hasattr(part, 'type') and getattr(part, 'type') == 'output_text':
                                            maybe_text = getattr(part, 'value', None)
                                            if isinstance(maybe_text, str):
                                                text_val = maybe_text
                                        if not text_val:
                                            try:
                                                text_val = str(part)
                                            except Exception:
                                                text_val = None
                                        if text_val:
                                            for k, v in placeholder_annotations.items():
                                                text_val = text_val.replace(k, v)
                                            collected_text_parts.append(text_val)
                            except Exception as msg_ex:
                                logger.debug(f"Skipping message due to parse error: {msg_ex}")

                        section_response = "\n".join(collected_text_parts) if collected_text_parts else "No assistant response captured."
                        responses.append(section_response)
                        logger.info(f"✅ Section {section_id} completed successfully - Response preview (first 200 chars): {section_response[:200]}{'...' if len(section_response) > 200 else ''}")
                        
                        # Report progress after processing each section (map to 20-85% range)
                        if progress_callback:
                            total_sections = len(sections)
                            if total_sections > 0:
                                # Calculate progress: start at 20%, end at 85%, distribute evenly across sections (65% span)
                                progress_pct = 20 + int(((section_idx + 1) / total_sections) * 65)
                                await progress_callback(
                                    f"Completed section {section_idx + 1}/{total_sections}: {section_id}",
                                    progress_pct
                                )
                    except Exception as msg_list_ex:
                        logger.error(f"Failed to list/parse messages for section {section_id}: {msg_list_ex}")
                        responses.append(f"Error retrieving messages for section {section_id}: {msg_list_ex}")  

                # Save responses to asr-report-{app_id}.json
                logger.info(f"Processing {len(responses)} responses for application {app_id}")
                logger.debug(f"All responses collected: {[f'Section {i+1}: {resp[:100]}...' if len(resp) > 100 else f'Section {i+1}: {resp}' for i, resp in enumerate(responses)]}")
                
                # Log MCP optimization results
                mcp_enabled_sections = sum(1 for section in sections if section_needs_mcp_tools(section))
                total_sections = len(sections)
                
                logger.debug(f"[MCP Optimization] Results:")
                logger.debug(f"Total sections: {total_sections}")
                logger.debug(f"Sections with MCP tools: {mcp_enabled_sections}")

                result = create_response_file(responses, prompt_file, app_id)
                if result.get("status") == "success":
                    responses_json_path = result.get("output_file")
                    logger.info(f"Response file created successfully: {responses_json_path}")
                    md_path = responses_json_to_markdown(responses_json_path, app_id, report_type="asr")
                    logger.info(f"Markdown file created: {md_path}")
     
                    # Upload to blob storage with automatic versioning (handled by utility)
                    file_name = os.path.basename(md_path)
                    logger.info(f"Uploading markdown file to blob storage: {file_name}")
                    blob_url = upload_asr_file_to_container(md_path, app_id, file_name)
                    logger.info(f"Assessment report successfully uploaded to blob storage: {blob_url}")
     
                    # Delete local response JSON and markdown files after upload
                    try:
                        if os.path.exists(responses_json_path):
                            os.remove(responses_json_path)
                        if os.path.exists(md_path):
                            os.remove(md_path)
                    except Exception as del_ex:
                        logger.warning(f"Could not delete local files: {del_ex}")
     
                    # Perform cleanup: delete thread and agent
                    # print("\n=== Starting Automatic Cleanup ===")
                    try:
                        cleanup_result = await cleanup_asr_agent(
                            application_id=application_id,
                            thread_id=thread_id,
                            client=client,
                            agent_id=agent_definition.id
                        )
                    except Exception as cleanup_ex:
                        logger.warning(f"Cleanup failed but continuing: {str(cleanup_ex)}")
                        init_span.add_event("cleanup_warning", {
                            "error": str(cleanup_ex)
                        })
                    # print("=== Cleanup Finished ===\n")

                    success_result = {
                        "status": "success",
                        "agent_id": agent_definition.id,
                        "thread_id": thread_id,
                        "output_file": responses_json_path,
                        "markdown_file": md_path,
                        "blob_url": blob_url,
                        "cleanup_performed": True
                    }
                    # Log successful completion with response preview
                    combined_responses = " ".join(responses) if responses else ""
                    logger.info(f"🎉 ASR agent completed successfully for application {application_id}!")
                    logger.info(f"📄 Full response preview (first 200 chars): {combined_responses[:200]}{'...' if len(combined_responses) > 200 else ''}")
                    logger.info(f"📊 Final result: {success_result}")
                    return success_result
                else:
                    # Even if response file creation failed, attempt cleanup
                    logger.info("Starting cleanup after error")
                    try:
                        cleanup_result = await cleanup_asr_agent(
                            application_id=application_id,
                            thread_id=thread_id,
                            client=client,
                            agent_id=agent_definition.id
                        )
                        logger.info(f"Cleanup result: {cleanup_result}")
                    except Exception as cleanup_ex:
                        logger.warning(f"Cleanup failed: {str(cleanup_ex)}")
                    logger.info("Cleanup finished")
                    
                    error_result = {
                        "status": "error", 
                        "message": "Failed to create response file",
                        "thread_id": thread_id,
                        "cleanup_performed": True
                    }
                    logger.error(f"ASR agent failed for application {application_id}: {error_result}")
                    return error_result
                   
            except Exception as e:
                logger.error(f"Error in ASR agent execution for {application_id}: {str(e)}")
                # Record exception in the initialization span
                init_span.record_exception(e)
                init_span.set_status(Status(StatusCode.ERROR, str(e)))
                init_span.add_event("asr_agent_execution_failed", {
                    "application_id": application_id,
                    "error_message": str(e)
                })
                
                # Attempt cleanup even after error
                if thread_id:
                    logger.info("Starting cleanup after exception")
                    try:
                        cleanup_result = await cleanup_asr_agent(
                            application_id=application_id,
                            thread_id=thread_id,
                            client=client,
                            agent_id=agent_definition.id if 'agent_definition' in locals() else None
                        )
                        logger.info(f"Cleanup result: {cleanup_result}")
                    except Exception as cleanup_ex:
                        logger.warning(f"Cleanup failed: {str(cleanup_ex)}")
                    logger.info("Cleanup finished")
                
                exception_result = {
                    "status": "error", 
                    "message": str(e),
                    "thread_id": thread_id,
                    "cleanup_performed": thread_id is not None
                }
                logger.error(f"ASR agent execution failed with exception for application {application_id}: {exception_result}")
                return exception_result
 
 
async def main() -> None:
    """Main function for standalone execution"""
    try:
            application_id = "50000"
            result = await run_asr_agent(application_id)
            logger.info(f"ASR Agent execution result: {result}")
            logger.info("Processing done")
    except Exception as ex:
        logger.error(f"Error in ASR agent main execution: {str(ex)}")
        # Record exception in span if available
        current_span = trace.get_current_span()
        if current_span and current_span.is_recording():
            current_span.record_exception(ex)
            current_span.set_status(Status(StatusCode.ERROR, str(ex)))
            current_span.add_event("asr_main_execution_failed")
        logger.error(f"Error in main execution: {str(ex)}")
 
 
if __name__ == "__main__":
    asyncio.run(main())