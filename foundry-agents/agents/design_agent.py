"""
Consolidated Design Agent - Backend Functionality Only
This module provides a complete, standalone implementation of the Design Agent
with Azure AI Search integration and architecture generation capabilities.

The Design Agent generates comprehensive Azure migration design documents in 
markdown format using section-based prompts from design_prompt.json

Usage:
    from design_agent import DesignAgent
    
    # Run the agent for an application
    result = await run_design_agent("application_id")
    print(result)
"""

import os
import sys
import json
import logging
import asyncio
import re
import time
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import shared error handling utilities
from agents.error_handler import (
    get_detailed_run_error,
    should_retry_server_error,
    retry_agent_run_on_server_error
)

from agents.orchestrator_agent import orchplugin

# Import diagram converter
try:
    from agents.diagram_converter import extract_and_convert_mermaid_from_markdown
    DIAGRAM_CONVERTER_AVAILABLE = True
except ImportError:
    DIAGRAM_CONVERTER_AVAILABLE = False
    extract_and_convert_mermaid_from_markdown = None


# Import communications matrix agent for integrated flow
try:
    from comms_matrix_agent import run_comms_matrix_agent
    COMMS_MATRIX_AVAILABLE = True
except ImportError:
    COMMS_MATRIX_AVAILABLE = False
    run_comms_matrix_agent = None


# Azure SDK imports
from azure.ai.projects.aio import AIProjectClient
from azure.ai.projects.models import ConnectionType
from azure.identity.aio import DefaultAzureCredential
from azure.identity import DefaultAzureCredential as SyncDefaultAzureCredential
from azure.ai.agents.models import (
    ToolDefinition, 
    FunctionToolDefinition,
    FunctionDefinition,
    AgentThread,
    ThreadMessage,
    AzureAISearchTool,
    AzureAISearchQueryType,
    ListSortOrder,
    McpTool
)
from azure.storage.blob import BlobServiceClient, ContentSettings
from azure.storage.blob.aio import BlobServiceClient as AsyncBlobServiceClient
from azure.search.documents.indexes.aio import SearchIndexClient
from semantic_kernel.functions import kernel_function
from dotenv import load_dotenv
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

# Import tracing configuration
from agents.tracing_config import (
    get_tracer,
    trace_async_function,
    add_span_attributes,
)

# Import logging configuration
from agents.logging_config import get_logger

# Import MCP tools utilities
from agents.mcp_tools import build_mcp_tool_definitions

# Import utility functions
from agents.utils.common_utils import (
    upload_file_to_container,
    download_template_from_storage,
    responses_json_to_markdown,
    create_response_file,
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

load_dotenv()

# Configure logging using the shared logging configuration
logger = get_logger(__name__)

# Log diagram converter availability
if not DIAGRAM_CONVERTER_AVAILABLE:
    logger.warning("⚠️ Diagram converter not available. Mermaid diagrams will not be converted to images.")
    logger.warning("Install mermaid-py with: pip install mermaid-py")


def download_design_prompt_from_storage(account_url: str, design_prompt_path: str):
    """
    Downloads design_prompt.json from the 'templates' container.
    Uses utility function for downloading.
    """
    try:
        download_template_from_storage(account_url, "design_prompt.json", design_prompt_path, container_name="templates")
    except Exception as e:
        logger.warning(f"Blob 'design_prompt.json' not found in container 'templates'. Using local file if exists.")


# Use utility function for JSON to Markdown conversion with design-specific defaults
def design_responses_json_to_markdown(json_path: str, app_id: str, md_path: str = None) -> str:
    """
    Convert design responses JSON to markdown format.
    
    Args:
        json_path: Path to the responses JSON file
        app_id: Application ID
        md_path: Optional output markdown path (auto-generated if not provided)
    
    Returns:
        Path to the generated markdown file
    """
    try:
        if md_path is None:
            md_path = f"design-{app_id}.md"
        
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        title = data.get("title", "Azure Migration Design Document")
        sections = data.get("sections_array", [])
        lines = [f"# {title}\n"]
        lines.append(f"**Application ID:** {app_id}\n")
        lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        lines.append("---\n")
        
        for section in sections:
            sec_id = section.get("id", "Section")
            response = section.get("response", "")
            
            # Parse section numbering for proper heading level
            match = re.match(r"(\d+(?:\.\d+)*)(?:\s+)(.*)", sec_id)
            if match:
                numbering = match.group(1)
                heading_text = match.group(2)
                level = numbering.count('.') + 1
                heading = f"{'#' * (level + 1)} {sec_id}"
            else:
                heading = f"## {sec_id}"
            
            lines.append(f"{heading}\n")
            if response:
                lines.append(f"{response}\n")
        
        md_content = "\n".join(lines)
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(md_content)
        
        return md_path
        
    except Exception as ex:
        # Record exception in span if available
        current_span = trace.get_current_span()
        if current_span and current_span.is_recording():
            current_span.record_exception(ex)
            current_span.set_status(Status(StatusCode.ERROR, str(ex)))
            current_span.add_event("design_responses_json_to_markdown_failed", {
                "json_path": json_path,
                "app_id": app_id,
                "md_path": md_path
            })
        raise


def create_design_response_file(responses: list, prompt_file: str, app_id: str) -> dict:
    """
    Create a response file with design agent responses.
    
    Args:
        responses: List of responses for each section
        prompt_file: Path to the original prompt file
        app_id: Application ID
    
    Returns:
        dict: Result containing status and output file path
    """
    try:
        output_file = f"design-document-{app_id}.json"
        with open(prompt_file, 'r', encoding='utf-8') as infile:
            data = json.load(infile)
        
        sections = data.get("sections_array", [])
        for section, response in zip(sections, responses):
            section["response"] = response
        
        with open(output_file, 'w', encoding='utf-8') as outfile:
            json.dump(data, outfile, indent=4)
        
        return {"status": "success", "output_file": output_file}
        
    except Exception as ex:
        # Record exception in span if available
        current_span = trace.get_current_span()
        if current_span and current_span.is_recording():
            current_span.record_exception(ex)
            current_span.set_status(Status(StatusCode.ERROR, str(ex)))
            current_span.add_event("create_design_response_file_failed", {
                "prompt_file": prompt_file,
                "app_id": app_id
            })
        return {"status": "error", "message": str(ex)}


def is_architecture_diagram_section(section_id: str) -> bool:
    """
    Check if a section is an architecture diagram section (section 4.x).
    Architecture sections contain Mermaid diagrams that need to be converted.
    
    Args:
        section_id: Section ID string (e.g., "4. Architecture Diagrams", "4.1 High-Level Architecture")
    
    Returns:
        bool: True if section is an architecture diagram section
    """
    if not section_id:
        return False
    # Match sections starting with "4." or exactly "4 "
    return section_id.startswith("4.") or section_id.startswith("4 ")


def section_needs_mcp_tools(section: dict, mcp_tool_definitions: dict) -> tuple[list, bool]:
    """
    Determine if a section needs MCP tools based on its 'mcp' array in knowledge field.
    
    Args:
        section: Section dictionary from design_prompt.json
        mcp_tool_definitions: Dict mapping MCP tool labels to their tool definitions
    
    Returns:
        tuple: (list of MCP tool definitions to add, bool indicating if any MCP tools added)
    """
    section_mcp_tools = []
    knowledge = section.get("knowledge", {})
    mcp_labels = knowledge.get("mcp", []) if isinstance(knowledge, dict) else []
    
    if not mcp_labels:
        return [], False
    
    # Add MCP tools based on labels in the section's mcp array
    for label in mcp_labels:
        label_lower = label.lower()
        # Map common label variations to our MCP tool definition keys
        if label_lower in ("azurepricing", "azure-pricing-calculator", "azure_pricing"):
            if "azurepricing" in mcp_tool_definitions:
                section_mcp_tools.extend(mcp_tool_definitions["azurepricing"])
        elif label_lower in ("microsoft_learn", "microsoft-learn", "mslearn"):
            if "microsoft_learn" in mcp_tool_definitions:
                section_mcp_tools.extend(mcp_tool_definitions["microsoft_learn"])
        # Add more MCP tool mappings here as needed
    
    return section_mcp_tools, len(section_mcp_tools) > 0


# NOTE: upload_file_to_container has been moved to agents.utils.common_utils
# Design-specific wrapper for uploading to design output folder
def upload_design_file_to_container(file_path: str, app_id: str, blob_name: str = None) -> str:
    """Upload a file to the design output folder in the application's container."""
    return upload_file_to_container(file_path, app_id, blob_name, folder_prefix="design/output/")


# Design-specific wrapper functions using utilities
@trace_async_function("find_existing_design_agent")
async def _find_existing_design_agent(client, application_id: str):
    """Find an existing Design agent by name pattern using utility function."""
    agent_name = build_agent_name("Design-Agent", application_id)
    return await find_existing_agent(client, agent_name, application_id)


@trace_async_function("cleanup_design_agent")
async def cleanup_design_agent(application_id: str, thread_id: str, client=None, agent_id: str = None) -> dict:
    """
    Clean up the Design agent and all associated threads using utility function.
    
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
        agent_type="Design-Agent",
        thread_id=thread_id,
        client=client,
        agent_id=agent_id,
        find_existing_fn=_find_existing_design_agent
    )


@trace_async_function("run_design_agent")
async def run_design_agent(application_id: str, client=None, thread=None, progress_callback: Optional[Callable] = None, storage_account_name: str = None) -> dict:
    """
    Run the Design agent with the provided application ID using section-based prompts.
    
    Args:
        application_id: The application ID to process (used as index name)
        client: (Optional) The Azure AI client. If None, a new client will be created.
        thread: Optional thread to use (if None, a new one will be created)
        progress_callback: Optional async callback function(message: str, percentage: float) for progress updates
        storage_account_name: Optional storage account name for design output
    Returns:
        dict: Result containing status, output files, and blob URL
    """
    logger.info(f"Starting Design agent for application_id: {application_id}")
    thread_id = None
    tracer = get_tracer()

    # Use AgentClientManager for proper resource cleanup
    async with AgentClientManager(existing_client=client) as manager:
        client = manager.client
        
        default_conn = await client.connections.get_default(ConnectionType.AZURE_AI_SEARCH)
        conn_id = default_conn.id if default_conn and hasattr(default_conn, 'id') else None
        if not conn_id:
            raise RuntimeError("Could not get Azure AI Search connection")
        logger.debug(f"Using connection ID for AI search {conn_id}")

        with tracer.start_as_current_span("design_agent_initialization") as init_span:
            add_span_attributes(init_span, {
                "design.application_id": application_id,
                "design.thread_provided": thread is not None
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
                instruction_file = os.path.join(current_dir, "agent-instructions", "design_agent.txt")
                base_instructions = load_instructions_from_file(instruction_file)

                # Prepare MCP tool definitions for Cost Estimation and Target Architecture sections
                allowed_mcp_raw = os.getenv("MCP_ALLOWED_SERVERS", "").strip()
                allowed_mcp = [s.strip() for s in allowed_mcp_raw.split(',') if s.strip()] if allowed_mcp_raw else []
                
                # Pre-build MCP tool definitions for sections that need them
                mcp_tool_definitions = {}
                if allowed_mcp:
                    try:
                        # Build Azure Pricing MCP for Cost Estimation (section 6)
                        if any(label.lower() in ("azurepricing", "azure-pricing-calculator") for label in allowed_mcp):
                            pricing_tools, pricing_labels = build_mcp_tool_definitions(["azurepricing"])
                            if pricing_labels:
                                mcp_tool_definitions["azurepricing"] = pricing_tools
                                logger.info(f"[Design Agent] Azure Pricing MCP tool prepared for Cost Estimation section")
                        
                        # Build Microsoft Learn MCP for Target Architecture (section 4.2)
                        mslearn_tools, mslearn_labels = build_mcp_tool_definitions(["microsoft_learn"])
                        if mslearn_labels:
                            mcp_tool_definitions["microsoft_learn"] = mslearn_tools
                            logger.info(f"[Design Agent] Microsoft Learn MCP tool prepared for Target Architecture section")
                    except Exception as mcp_ex:
                        logger.error(f"[Design Agent] Failed to prepare MCP tool definitions: {mcp_ex}")
                else:
                    logger.warning("[Design Agent] No MCP_ALLOWED_SERVERS configured; MCP tools disabled.")

                logger.debug(f"Agent instructions length: {len(base_instructions)} characters")
                logger.debug(f"Base agent tools: Azure AI Search only (MCP tools added per-section)")
                
                # Create or update agent using utility (includes search tool configuration)
                agent_result = await create_agent_with_search_tool(
                    client=client,
                    agent_name="Design-Agent",
                    application_id=application_id,
                    instructions=base_instructions,
                    search_tool_config=search_config,
                    temperature=0.1,
                    find_existing=True
                )
                agent_definition = agent_result.agent
                ai_search = agent_result.search_tool
                
                if agent_result.is_new:
                    logger.debug(f"Created Design agent {agent_definition.id} with search tool")
                else:
                    logger.debug(f"Updated Design agent {agent_definition.id} with search tool")

                # Create (or reuse) a thread
                if thread is None:
                    thread = await client.agents.threads.create()
                    thread_id = thread.id
                    logger.debug(f"Created new thread with id: {thread.id}")

                initializer_prompt = f"""Initializing the agent. Are you ready for answering the design related questions? Use only the AI Search tool. Do not answer on your own.
```"""
                #Initialise the agent with a dummy first message
                result = await orchplugin()._initialize_first_agent_run(
                    client, agent_definition.id, "initialize_agent_run", application_id, thread_id=thread_id, initializer_prompt=initializer_prompt
                )
                logger.debug(f"Initializer method ran {result}")
 
                # Load design prompt file
                prompt_file = "design_prompt.json"
                app_id = application_id
 
                # Try to download the prompt file from Azure Storage
                account_url = os.getenv("AZURE_STORAGE_ACCOUNT_URL")
                if account_url:
                    download_design_prompt_from_storage(account_url, prompt_file)
                
                # If prompt file doesn't exist, use the local one from agent-instructions
                if not os.path.exists(prompt_file):
                    local_prompt_file = os.path.join(current_dir, "agent-instructions", "design_prompt.json")
                    if os.path.exists(local_prompt_file):
                        import shutil
                        shutil.copy(local_prompt_file, prompt_file)
                        logger.info(f"Using local design_prompt.json from {local_prompt_file}")
                    else:
                        raise RuntimeError(f"Design prompt file not found: {prompt_file}")
 
                # Load prompts and table names
                with open(prompt_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                sections = data.get("sections_array", [])
                total_sections = len(sections)
                responses = []

                # Create initial prompt prefix
                initial_prompt = f"Using the attached AI Search knowledge project-index-{application_id}/versions/1, always perform a search query with the attached tool, even if you think the result will be empty. Never skip the tool call. "

                for section_idx, section in enumerate(sections):
                    prompt = section.get("prompt", "")

                    if prompt:
                        prompt = initial_prompt + prompt
                    section_id = section.get('id', 'unknown')
                    
                    # Get the knowledge object
                    knowledge = section.get("knowledge", {})
                    knowledge_files = knowledge.get("document", []) if isinstance(knowledge, dict) else []
                    knowledge_content = {}
                    for fname in knowledge_files:
                        try:
                            with open(fname, 'r', encoding='utf-8') as kf:
                                knowledge_content[fname] = kf.read()
                        except Exception as kex:
                            knowledge_content[fname] = f"Error reading file: {kex}"
                    
                    logger.debug(f"[Design Agent] Processing section {section_id}")
                    
                    if not prompt:
                        responses.append("")
                        continue
                    
                    # Append knowledge content to the user message if present
                    if knowledge_content:
                        prompt += "\n\nKnowledge Content:\n"
                        for fname, content in knowledge_content.items():
                            prompt += f"\n--- {fname} ---\n{content}\n"
                    
                    logger.info(f"Sending prompt to Design agent (length: {len(prompt)} chars) for section: {section_id}")
                    logger.debug(f"# User: {prompt}")
                  
                    # Ensure thread exists
                    if thread is None:
                        thread = await client.agents.threads.create()
                        thread_id = thread.id
                        logger.debug(f"Created thread id: {thread.id}")
                    else:
                        thread_id = thread.id if hasattr(thread, 'id') else thread_id
                        logger.debug(f"Reusing thread id: {thread.id}")
                    
                    # Create user message
                    created_msg = await client.agents.messages.create(
                        thread_id=thread_id,
                        role="user",
                        content=prompt
                    )
                    logger.debug(f"Created user message id: {getattr(created_msg, 'id', 'unknown')}")
                    
                    # Check if this is an architecture diagram section
                    is_arch_section = is_architecture_diagram_section(section_id)
                    
                    # Prepare tools for this specific section
                    section_tools = list(ai_search.definitions)
                    
                    # Use section_needs_mcp_tools to determine MCP tools based on section's 'mcp' array
                    section_mcp_tools, mcp_enabled_for_section = section_needs_mcp_tools(section, mcp_tool_definitions)
                    
                    if mcp_enabled_for_section:
                        section_tools.extend(section_mcp_tools)
                        mcp_labels_for_section = section.get("knowledge", {}).get("mcp", [])
                        logger.info(f"[Design Agent] ✅ Added MCP tools for section {section_id}: {mcp_labels_for_section}")
                    else:
                        logger.debug(f"[Design Agent] Using only Azure AI Search for section {section_id} ({len(section_tools)} total tools)")

                    # Create and run
                    try:
                        run = await client.agents.runs.create(
                            thread_id=thread_id,
                            agent_id=agent_definition.id,
                            tools=section_tools,
                            metadata={
                                "section_id": section_id, 
                                "is_architecture": str(is_arch_section),
                                "mcp_enabled": str(mcp_enabled_for_section),
                                "mcp_tools": str(section.get("knowledge", {}).get("mcp", []))
                            }
                        )
                        logger.info(f"Run created for section {section_id}: {getattr(run, 'id', 'unknown')} (Arch: {is_arch_section}, MCP: {mcp_enabled_for_section})")
                    except Exception as run_ex:
                        logger.error(f"Failed to create run for section {section_id}: {run_ex}")
                        responses.append(f"Error creating run for section {section_id}: {run_ex}")
                        continue

                    # Poll run status
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

                            # Handle required MCP tool approvals (for Cost Estimation section)
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
                            logger.debug(f"Run {run.id} for section {section_id} status: {last_status}")
                            # Timeout (5 minutes)
                            if time.time() - poll_start > 300:
                                logger.error(f"Run timeout after 300s for section {section_id}")
                                last_status = "timeout"
                                break
                            await asyncio.sleep(2)

                        if last_status in {"completed", "succeeded"}:
                            run_completed_successfully = True
                        elif last_status == "failed":
                            error_msg, error_details = get_detailed_run_error(current, f"Design agent run for section {section_id}")
                            
                            if should_retry_server_error(error_msg, error_details):
                                logger.warning(f"Retryable error detected for section {section_id}, attempting retry")
                                await asyncio.sleep(0.5)

                                retried_run, retry_succeeded = await retry_agent_run_on_server_error(
                                    client, agent_definition.id, thread_id,
                                    f"Design agent run for section {section_id}",
                                    max_retries=3, base_delay=2.0,
                                    metadata={
                                        "section_id": section_id, 
                                        "is_architecture": str(is_arch_section),
                                        "mcp_enabled": str(mcp_enabled_for_section),
                                        "mcp_tools": str(section.get("knowledge", {}).get("mcp", []))
                                    },
                                    is_async_client=True,
                                    tools=section_tools
                                )
                            
                                if retry_succeeded:
                                    logger.info(f"✅ Retry successful for section {section_id}")
                                    run = retried_run
                                    run_completed_successfully = True
                                else:
                                    logger.error(f"❌ Retry failed for section {section_id}: {error_msg}")
                                    responses.append(f"Run for section {section_id} failed after retries: {error_msg}")
                                    continue
                            else:
                                logger.error(f"Non-retryable error for section {section_id}: {error_msg}")
                                responses.append(f"Run for section {section_id} failed: {error_msg}")
                                continue
                        else:
                            logger.error(f"Run ended with status {last_status} for section {section_id}")
                            responses.append(f"Run for section {section_id} ended with status {last_status}")
                            continue
                    except Exception as poll_ex:
                        logger.error(f"Exception while polling run for section {section_id}: {poll_ex}")
                        responses.append(f"Polling error for section {section_id}: {poll_ex}")
                        continue

                    if not run_completed_successfully:
                        continue

                    # Retrieve assistant/agent messages generated by this run
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
                                    print(f"[Design Agent RunStep] Section {section_id} Step {step_id} status: {step_status}")
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
                                            print(f"  [Design Agent ToolCall] id={call_id} type={call_type}")
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
                                            # MCP tool specific details (for Cost Estimation sections)
                                            mcp_tool_details = None
                                            if isinstance(call, dict):
                                                mcp_tool_details = call.get('mcp')
                                            else:
                                                mcp_tool_details = getattr(call, 'mcp', None)
                                            if mcp_tool_details:
                                                tool_name = mcp_tool_details.get('name') if isinstance(mcp_tool_details, dict) else getattr(mcp_tool_details, 'name', None)
                                                tool_input = mcp_tool_details.get('input') if isinstance(mcp_tool_details, dict) else getattr(mcp_tool_details, 'input', None)
                                                tool_output = mcp_tool_details.get('output') if isinstance(mcp_tool_details, dict) else getattr(mcp_tool_details, 'output', None)
                                                print(f"    [MCPTool] name: {tool_name}")
                                                print(f"    [MCPTool] input: {tool_input}")
                                                print(f"    [MCPTool] output: {str(tool_output)[:200] if tool_output else None}")
                                                logger.info(f"MCP tool call name={tool_name} input={tool_input} output={str(tool_output)[:200] if tool_output else None}")
                                        except Exception as call_ex:
                                            logger.debug(f"Failed to log tool call: {call_ex}")
                                except Exception as step_ex:
                                    logger.debug(f"Failed to process run step: {step_ex}")
                        except Exception as steps_ex:
                            logger.debug(f"Could not list run steps for section {section_id}: {steps_ex}")
                        
                        # Get messages
                        messages_iter = client.agents.messages.list(
                            thread_id=thread_id,
                            order=ListSortOrder.ASCENDING if 'ASCENDING' in dir(ListSortOrder) else None
                        )
                        async for msg in messages_iter:
                            try:
                                if getattr(msg, 'run_id', None) != run.id:
                                    continue
                                role = getattr(msg, 'role', None)
                                role_lower = str(role).lower() if role else ''
                                if not any(r in role_lower for r in ("assistant", "agent")):
                                    continue

                                # Handle citation replacement
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

                                # Extract text
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
                        
                        # Report progress after section completion (20-85% range mapped across sections)
                        if progress_callback:
                            try:
                                idx = section_idx + 1
                                # Map section progress to 20-85% range (65% total span)
                                section_progress_pct = 20 + int((idx / total_sections) * 65)
                                await progress_callback(
                                    f"Completed design section {idx}/{total_sections}: {section_id}",
                                    section_progress_pct
                                )
                                logger.debug(f"Progress callback: {section_progress_pct}% - section {idx}/{total_sections}")
                            except Exception as progress_ex:
                                logger.warning(f"Failed to report progress for section {section_id}: {progress_ex}")
                    except Exception as msg_list_ex:
                        logger.error(f"Failed to list/parse messages for section {section_id}: {msg_list_ex}")
                        responses.append(f"Error retrieving messages for section {section_id}: {msg_list_ex}")  

                # Save responses to design-{app_id}.json
                logger.info(f"Processing {len(responses)} responses for application {app_id}")
                logger.debug(f"All responses collected: {[f'Section {i+1}: {resp[:100]}...' if len(resp) > 100 else f'Section {i+1}: {resp}' for i, resp in enumerate(responses)]}")
                
                # Log architecture sections count
                arch_sections = sum(1 for section in sections if is_architecture_diagram_section(section.get('id', '')))
                total_sections = len(sections)
                
                logger.debug(f"[Design Agent] Processing summary:")
                logger.debug(f"Total sections: {total_sections}")
                logger.debug(f"Architecture diagram sections: {arch_sections}")
                
                # Process architecture diagram sections (4.x) - convert Mermaid to images
                if DIAGRAM_CONVERTER_AVAILABLE and extract_and_convert_mermaid_from_markdown:
                    logger.info("Processing architecture diagram sections for Mermaid conversion...")
                    for idx, section in enumerate(sections):
                        section_id = section.get('id', '')
                        if is_architecture_diagram_section(section_id) and idx < len(responses):
                            original_response = responses[idx]
                            if original_response and '```mermaid' in original_response.lower():
                                try:
                                    logger.info(f"Converting Mermaid diagrams in section {section_id}...")
                                    converted_response = extract_and_convert_mermaid_from_markdown(
                                        markdown_content=original_response,
                                        app_id=app_id,
                                        output_format="png",
                                        embed_method="base64"
                                    )
                                    responses[idx] = converted_response
                                    logger.info(f"✅ Mermaid diagrams converted for section {section_id}")
                                except Exception as conv_ex:
                                    logger.warning(f"Failed to convert Mermaid diagrams in section {section_id}: {conv_ex}")
                                    # Keep original response if conversion fails
                else:
                    if arch_sections > 0:
                        logger.warning("Diagram converter not available. Mermaid diagrams will remain as code blocks.")

                result = create_design_response_file(responses, prompt_file, app_id)
                if result.get("status") == "success":
                    responses_json_path = result.get("output_file")
                    logger.info(f"Response file created successfully: {responses_json_path}")
                    md_path = design_responses_json_to_markdown(responses_json_path, app_id)
                    logger.info(f"Markdown file created: {md_path}")
     
                    # Upload to blob storage with automatic versioning (handled by utility)
                    file_name = os.path.basename(md_path)
                    logger.info(f"Uploading markdown file to blob storage: {file_name}")
                    blob_url = upload_design_file_to_container(md_path, app_id, file_name)
                    logger.info(f"Design document successfully uploaded to blob storage: {blob_url}")
     
                    # Delete local response JSON and markdown files after upload
                    try:
                        if os.path.exists(responses_json_path):
                            os.remove(responses_json_path)
                        if os.path.exists(md_path):
                            os.remove(md_path)
                    except Exception as del_ex:
                        logger.warning(f"Could not delete local files: {del_ex}")
                    # =========================================================
                    # INTEGRATED: Run Communications Matrix Agent after design
                    # Upload directly to design/output folder
                    # =========================================================
                    comms_matrix_result = None
                    comms_matrix_status = "skipped"
                    
                    if COMMS_MATRIX_AVAILABLE and run_comms_matrix_agent:
                        logger.info(f"Starting integrated communications matrix generation for {application_id}")
                        init_span.add_event("starting_comms_matrix_agent", {
                            "application_id": application_id,
                            "output_folder": "design/output/"
                        })
                        
                        try:
                            # Pass output_folder to upload directly to design/output
                            comms_matrix_result = await run_comms_matrix_agent(
                                application_id, 
                                "design/output/",
                                storage_account_name=storage_account_name
                            )
                            
                            if isinstance(comms_matrix_result, dict):
                                if comms_matrix_result.get("status") == "success":
                                    comms_matrix_status = "success"
                                    metadata = comms_matrix_result.get("metadata", {})
                                    blob_url = metadata.get('blob_url', '')
                                    logger.info(
                                        f"✅ Communications matrix generated successfully: "
                                        f"{metadata.get('total_flows', 0)} flows, "
                                        f"blob_url: {blob_url or 'N/A'}"
                                    )
                                    init_span.add_event("comms_matrix_completed_successfully", {
                                        "total_flows": metadata.get("total_flows", 0),
                                        "blob_uploaded": metadata.get("blob_uploaded", False),
                                        "blob_url": blob_url[:200] if blob_url else ""
                                    })
                                else:
                                    comms_matrix_status = "partial_failure"
                                    error_msg = comms_matrix_result.get("error_message", "Unknown error")
                                    logger.warning(f"⚠️ Communications matrix completed with issues: {error_msg}")
                                    init_span.add_event("comms_matrix_partial_failure", {
                                        "error_message": error_msg
                                    })
                            else:
                                comms_matrix_status = "unexpected_result"
                                logger.warning(f"⚠️ Communications matrix returned unexpected result type: {type(comms_matrix_result)}")
                                
                        except Exception as comms_ex:
                            comms_matrix_status = "error"
                            comms_matrix_result = {
                                "status": "error",
                                "error_message": str(comms_ex)
                            }
                            logger.warning(f"⚠️ Communications matrix agent failed (design still successful): {str(comms_ex)}")
                            init_span.add_event("comms_matrix_exception", {
                                "error_type": type(comms_ex).__name__,
                                "error_message": str(comms_ex)
                            })
                    else:
                        logger.info("Communications matrix agent not available, skipping integration")
                        init_span.add_event("comms_matrix_skipped", {
                            "reason": "agent_not_available"
                        })
                    #Perform cleanup
                    try:
                        cleanup_result = await cleanup_design_agent(
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

                    success_result = {
                        "status": "success",
                        "agent_id": agent_definition.id,
                        "thread_id": thread_id,
                        "output_file": responses_json_path,
                        "markdown_file": md_path,
                        "blob_url": blob_url,
                        "cleanup_performed": True,
                        # Integrated comms matrix results
                        "comms_matrix": {
                            "status": comms_matrix_status,
                            "result": comms_matrix_result
                        }
                    }
                    combined_responses = " ".join(responses) if responses else ""
                    logger.info(f"🎉 Design agent completed successfully for application {application_id}!")
                    logger.info(f"Full response preview (first 200 chars): {combined_responses[:200]}{'...' if len(combined_responses) > 200 else ''}")
                    logger.info(f"Final result: {success_result}")
                    return success_result
                else:
                    # Response file creation failed
                    logger.info("Starting cleanup after error")
                    try:
                        cleanup_result = await cleanup_design_agent(
                            application_id=application_id,
                            thread_id=thread_id,
                            client=client,
                            agent_id=agent_definition.id
                        )
                        logger.info(f"Cleanup result: {cleanup_result}")
                    except Exception as cleanup_ex:
                        logger.warning(f"Cleanup failed: {str(cleanup_ex)}")
                    
                    error_result = {
                        "status": "error", 
                        "message": "Failed to create response file",
                        "thread_id": thread_id,
                        "cleanup_performed": True
                    }
                    logger.error(f"Design agent failed for application {application_id}: {error_result}")
                    return error_result
                   
            except Exception as e:
                logger.error(f"Error in Design agent execution for {application_id}: {str(e)}")
                init_span.record_exception(e)
                init_span.set_status(Status(StatusCode.ERROR, str(e)))
                init_span.add_event("design_agent_execution_failed", {
                    "application_id": application_id,
                    "error_message": str(e)
                })
                
                # Attempt cleanup even after error
                if thread_id:
                    logger.info("Starting cleanup after exception")
                    try:
                        cleanup_result = await cleanup_design_agent(
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
                logger.error(f"Design agent execution failed with exception for application {application_id}: {exception_result}")
                return exception_result


# Example usage
async def main():
    """Main function for standalone execution"""
    try:
        application_id = "2001app"
        result = await run_design_agent(application_id)
        logger.info(f"Design Agent execution result: {result}")
        logger.info("Processing done")
    except Exception as ex:
        logger.error(f"Error in Design agent main execution: {str(ex)}")
        current_span = trace.get_current_span()
        if current_span and current_span.is_recording():
            current_span.record_exception(ex)
            current_span.set_status(Status(StatusCode.ERROR, str(ex)))
            current_span.add_event("design_main_execution_failed")
        logger.error(f"Error in main execution: {str(ex)}")


if __name__ == "__main__":
    asyncio.run(main())