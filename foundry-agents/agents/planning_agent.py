# Copyright (c) Microsoft. All rights reserved.
"""
App Planning Agent - Generates comprehensive migration planning documentation
Based on ASR Agent architecture but focused on migration planning sections.
"""

from azure.storage.blob import BlobServiceClient
from azure.identity import DefaultAzureCredential
from azure.data.tables import TableServiceClient
import os
import sys
import logging
import datetime
import asyncio
import json
import time
from typing import Annotated, List
from dotenv import load_dotenv
from azure.ai.projects.aio import AIProjectClient
from azure.ai.projects.models import ConnectionType
from azure.identity.aio import DefaultAzureCredential as AsyncDefaultAzureCredential
from azure.ai.agents.models import AzureAISearchQueryType, AzureAISearchTool, ListSortOrder, MessageRole
from azure.identity import DefaultAzureCredential as SyncDefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings
from semantic_kernel.contents import ChatMessageContent, FunctionCallContent, FunctionResultContent

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

logger.info("Planning Agent initialized")


def download_planning_prompt_from_storage(account_url: str, planning_prompt_path: str):
    """
    Downloads plan_prompt.json from the 'templates' container.
    Uses utility function for downloading.
    """
    for blob_name, local_path in [("plan_prompt.json", planning_prompt_path)]:
        try:
            download_template_from_storage(account_url, blob_name, local_path, container_name="templates")
        except Exception as e:
            logger.error(f"Error downloading blob {blob_name}: {e}")


# Planning agent-specific wrapper functions using utilities
def upload_planning_file_to_container(file_path: str, app_id: str, blob_name: str = None) -> str:
    """Upload a file to the app-planning output folder in the application's container."""
    return upload_file_to_container(file_path, app_id, blob_name, folder_prefix="app-planning/output/")


def process_planning_prompts(json_file: str) -> list:
    """Process prompts from planning JSON file."""
    return process_prompts_from_json(json_file)


def create_planning_response_file(responses: list, prompt_file: str, app_id: str) -> dict:
    """Create planning response file."""
    return create_response_file(responses, prompt_file, app_id, output_prefix="planning-report")


async def handle_streaming_intermediate_steps(message: ChatMessageContent) -> None:
    """Handle intermediate streaming steps from the agent."""
    for item in message.items or []:
        if isinstance(item, FunctionResultContent):
            logger.info(f"Planning agent function result: {item.name} returned: {str(item.result)[:500]}{'...' if len(str(item.result)) > 500 else ''}")
            logger.debug(f"Function Result:> {item.result} for function: {item.name}")
        elif isinstance(item, FunctionCallContent):
            logger.info(f"Planning agent function call: {item.name} with arguments: {item.arguments}")
            logger.debug(f"Function Call:> {item.name} with arguments: {item.arguments}")
        else:
            logger.debug(f"Planning agent intermediate message item: {item}")


@trace_async_function("find_existing_planning_agent")
async def _find_existing_planning_agent(client, application_id: str):
    """Find an existing Planning agent by name pattern using utility function."""
    agent_name = build_agent_name("PlanningAgent", application_id)
    return await find_existing_agent(client, agent_name, application_id)


@trace_async_function("cleanup_planning_agent")
async def cleanup_planning_agent(application_id: str, thread_id: str, client=None, agent_id: str = None) -> dict:
    """
    Clean up the Planning agent and all associated threads using utility function.
    
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
        agent_type="PlanningAgent",
        thread_id=thread_id,
        client=client,
        agent_id=agent_id,
        find_existing_fn=_find_existing_planning_agent
    )


@trace_async_function("run_planning_agent")
async def run_planning_agent(application_id: str, client=None, thread=None) -> dict:
    """
    Run the Planning agent with the provided application ID and thread.
   
    Args:
        application_id: The application ID to process
        client: (Optional) The Azure AI client. If None, a new client will be created inside the function.
        thread: Optional thread to use (if None, a new one will be created)
   
    Returns:
        dict: Result containing status, output files, and blob URL
    """
    logger.info(f"Starting Planning agent for application_id: {application_id}")
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

        with tracer.start_as_current_span("planning_agent_initialization") as init_span:
            add_span_attributes(init_span, {
                "planning.application_id": application_id,
                "planning.thread_provided": thread is not None
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
                instruction_file = os.path.join(current_dir, "agent-instructions", "planning_agent.txt")
                base_instructions = load_instructions_from_file(
                    instruction_file,
                    placeholder_replacements={"application_id": application_id}
                )

                logger.debug(f"Agent instructions length: {len(base_instructions)} characters")
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(f"Agent instructions:\n{base_instructions}")
                
                # Create or update agent using utility (includes search tool configuration)
                agent_result = await create_agent_with_search_tool(
                    client=client,
                    agent_name="PlanningAgent",
                    application_id=application_id,
                    instructions=base_instructions,
                    search_tool_config=search_config,
                    temperature=0.1,
                    find_existing=True
                )
                agent_definition = agent_result.agent
                ai_search = agent_result.search_tool
                
                if agent_result.is_new:
                    logger.debug(f"Created Planning agent {agent_definition.id} with search tool")
                else:
                    logger.debug(f"Updated Planning agent {agent_definition.id} with search tool")

                # Create (or reuse) a thread using async API
                if thread is None:
                    thread = await client.agents.threads.create()
                    thread_id = thread.id

                prompt_file = "plan_prompt.json"
                app_id = application_id

                # Download the prompt file from Azure Storage templates container
                account_url = os.getenv("AZURE_STORAGE_ACCOUNT_URL")
                download_planning_prompt_from_storage(account_url, prompt_file)

                # Load prompts and sections
                with open(prompt_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                sections = data.get("sections_array", [])
                
                responses = []

                # Create initial prompt with dynamic application_id
                initial_prompt = f"Using the attached AI Search knowledge, Always perform a search query with the attached tool, even if you think the result will be empty. Never skip the tool call,"

                for section in sections:
                    prompt = section.get("prompt", "")

                    if prompt:
                        prompt = initial_prompt + " " + prompt
                    section_id = section.get('id', 'unknown')
                    
                    # Get knowledge files from the section
                    knowledge = section.get("knowledge", {})
                    knowledge_files = knowledge.get("document", []) if isinstance(knowledge, dict) else []
                    knowledge_content = {}
                    for fname in knowledge_files:
                        try:
                            with open(fname, 'r', encoding='utf-8') as kf:
                                knowledge_content[fname] = kf.read()
                        except Exception as kex:
                            knowledge_content[fname] = f"Error reading file: {kex}"
                    
                    if not prompt:
                        responses.append("")
                        continue

                    # Append knowledge content to the user message if present
                    if knowledge_content:
                        prompt += "\n\nKnowledge Content:\n"
                        for fname, content in knowledge_content.items():
                            prompt += f"\n--- {fname} ---\n{content}\n"

                    logger.info(f"Sending prompt to Planning agent (length: {len(prompt)} chars) for section: {section_id}")
                    logger.debug(f"# User: {prompt}")
                  
                    # Ensure thread exists
                    if thread is None:
                        thread = await client.agents.threads.create()
                        thread_id = thread.id
                    else:
                        thread_id = thread.id if hasattr(thread, 'id') else thread_id

                    # Create user message explicitly via messages API
                    created_msg = await client.agents.messages.create(
                        thread_id=thread_id,
                        role="user",
                        content=prompt
                    )
                    logger.debug(f"Created user message id: {getattr(created_msg, 'id', 'unknown')}")

                    # Create a run for this section (search tools only, no MCP)
                    section_tools = list(ai_search.definitions)
                    logger.debug(f"[Planning Agent] Using search-only tools for section {section_id} ({len(section_tools)} total tools)")

                    try:
                        run = await client.agents.runs.create(
                            thread_id=thread_id,
                            agent_id=agent_definition.id,
                            tools=section_tools,
                            metadata={"section_id": section_id}
                        )
                        logger.info(f"Run created for section {section_id}: {getattr(run, 'id', 'unknown')}")
                    except Exception as run_ex:
                        logger.error(f"Failed to create run for section {section_id}: {run_ex}")
                        responses.append(f"Error creating run for section {section_id}: {run_ex}")
                        continue

                    # Poll run status
                    poll_start = time.time()
                    terminal_statuses = {"completed", "failed", "cancelled", "succeeded"}
                    last_status = None
                    run_completed_successfully = False

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

                            if last_status in terminal_statuses:
                                break

                            logger.debug(f"Run {run.id} for section {section_id} status: {last_status}")
                            
                            # Timeout after 5 minutes
                            if time.time() - poll_start > 300:
                                logger.error(f"Run timeout after 300s for section {section_id}")
                                last_status = "timeout"
                                break
                            await asyncio.sleep(2)

                        if last_status in {"completed", "succeeded"}:
                            run_completed_successfully = True
                        elif last_status == "failed":
                            # Enhanced error handling with retry mechanism
                            error_msg, error_details = get_detailed_run_error(current, f"Planning agent run for section {section_id}")
                        
                            if should_retry_server_error(error_msg, error_details):
                                logger.warning(f"Retryable error detected for section {section_id}, attempting retry")
                                await asyncio.sleep(0.5)

                                retried_run, retry_succeeded = await retry_agent_run_on_server_error(
                                    client, agent_definition.id, thread_id, 
                                    f"Planning agent run for section {section_id}",
                                    max_retries=3, base_delay=2.0,
                                    metadata={"section_id": section_id},
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

                    # Retrieve assistant messages generated by this run
                    try:
                        collected_text_parts = []
                        
                        # Log run steps for debugging
                        try:
                            run_steps = client.agents.run_steps.list(
                                thread_id=thread_id,
                                run_id=run.id
                            )
                            async for step in run_steps:
                                try:
                                    step_id = getattr(step, 'id', 'unknown')
                                    step_status = getattr(step, 'status', 'unknown')
                                    logger.info(f"Run step {step_id} status {step_status} for section {section_id}")
                                    step_details = getattr(step, 'step_details', None) or {}
                                    tool_calls = []
                                    if isinstance(step_details, dict):
                                        tool_calls = step_details.get('tool_calls', [])
                                    elif hasattr(step_details, 'tool_calls'):
                                        tool_calls = getattr(step_details, 'tool_calls') or []
                                    for call in tool_calls:
                                        try:
                                            call_id = call.get('id') if isinstance(call, dict) else getattr(call, 'id', 'unknown')
                                            call_type = call.get('type') if isinstance(call, dict) else getattr(call, 'type', 'unknown')
                                            logger.info(f"Tool call in section {section_id}: id={call_id} type={call_type}")
                                        except Exception as call_ex:
                                            logger.debug(f"Failed to log tool call: {call_ex}")
                                except Exception as step_ex:
                                    logger.debug(f"Failed to process run step: {step_ex}")
                        except Exception as steps_ex:
                            logger.debug(f"Could not list run steps for section {section_id}: {steps_ex}")

                        # Get messages in ascending order
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

                                # Extract text from messages
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
                                    # Fallback to content parsing
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
                    except Exception as msg_list_ex:
                        logger.error(f"Failed to list/parse messages for section {section_id}: {msg_list_ex}")
                        responses.append(f"Error retrieving messages for section {section_id}: {msg_list_ex}")  

                # Save responses to planning-report-{app_id}.json
                logger.info(f"Processing {len(responses)} responses for application {app_id}")
                logger.debug(f"All responses collected: {[f'Section {i+1}: {resp[:100]}...' if len(resp) > 100 else f'Section {i+1}: {resp}' for i, resp in enumerate(responses)]}")

                result = create_response_file(responses, prompt_file, app_id, output_file="planning-report")
                if result.get("status") == "success":
                    responses_json_path = result.get("output_file")
                    logger.info(f"Response file created successfully: {responses_json_path}")
                    md_path = responses_json_to_markdown(responses_json_path, app_id, report_type="planning")
                    logger.info(f"Markdown file created: {md_path}")
     
                    # Upload to blob storage
                    file_name = os.path.basename(md_path)
                    logger.info(f"Uploading markdown file to blob storage: {file_name}")
                    blob_url = upload_planning_file_to_container(md_path, app_id, file_name)
                    logger.info(f"Planning report successfully uploaded to blob storage: {blob_url}")
     
                    # Delete local files after upload
                    try:
                        if os.path.exists(responses_json_path):
                            os.remove(responses_json_path)
                        if os.path.exists(md_path):
                            os.remove(md_path)
                    except Exception as del_ex:
                        logger.warning(f"Could not delete local files: {del_ex}")
     
                    # Perform cleanup
                    try:
                        cleanup_result = await cleanup_planning_agent(
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
                        "cleanup_performed": True
                    }
                    combined_responses = " ".join(responses) if responses else ""
                    logger.info(f"🎉 Planning agent completed successfully for application {application_id}!")
                    logger.info(f"📄 Full response preview (first 200 chars): {combined_responses[:200]}{'...' if len(combined_responses) > 200 else ''}")
                    logger.info(f"📊 Final result: {success_result}")
                    return success_result
                else:
                    # Attempt cleanup even on failure
                    logger.info("Starting cleanup after error")
                    try:
                        cleanup_result = await cleanup_planning_agent(
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
                    logger.error(f"Planning agent failed for application {application_id}: {error_result}")
                    return error_result
                   
            except Exception as e:
                logger.error(f"Error in Planning agent execution for {application_id}: {str(e)}")
                init_span.record_exception(e)
                init_span.set_status(Status(StatusCode.ERROR, str(e)))
                init_span.add_event("planning_agent_execution_failed", {
                    "application_id": application_id,
                    "error_message": str(e)
                })
                
                # Attempt cleanup even after error
                if thread_id:
                    logger.info("Starting cleanup after exception")
                    try:
                        cleanup_result = await cleanup_planning_agent(
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
                logger.error(f"Planning agent execution failed with exception for application {application_id}: {exception_result}")
                return exception_result


async def main() -> None:
    """Main function for standalone execution"""
    try:
        application_id = "50000"
        result = await run_planning_agent(application_id)
        logger.info(f"Planning Agent execution result: {result}")
        logger.info("Processing done")
    except Exception as ex:
        logger.error(f"Error in Planning agent main execution: {str(ex)}")
        current_span = trace.get_current_span()
        if current_span and current_span.is_recording():
            current_span.record_exception(ex)
            current_span.set_status(Status(StatusCode.ERROR, str(ex)))
            current_span.add_event("planning_main_execution_failed")
        logger.error(f"Error in main execution: {str(ex)}")


if __name__ == "__main__":
    asyncio.run(main())
