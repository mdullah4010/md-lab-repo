import os
import asyncio
import shutil
import re
import json
import aiohttp
import hashlib
import logging
import datetime
import io
from typing import Annotated, List
from dotenv import load_dotenv
from azure.ai.agents.models import AzureAISearchTool, AzureAISearchQueryType
from azure.identity.aio import AzureCliCredential, DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings
from semantic_kernel.agents import AzureAIAgent, AzureAIAgentSettings, AzureAIAgentThread
from semantic_kernel.functions import kernel_function
from semantic_kernel.contents import ChatMessageContent, FunctionCallContent, FunctionResultContent
from semantic_kernel.exceptions import AgentInvokeException
from typing import Optional, List, Dict, Any, Set
from azure.ai.projects.aio import AIProjectClient
from azure.ai.projects.models import ConnectionType
from azure.ai.agents.models import AsyncToolSet, AzureAISearchTool, AzureAISearchQueryType
from azure.identity.aio import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.core.credentials import AzureKeyCredential
from azure.identity import DefaultAzureCredential as SyncDefaultAzureCredential
from dotenv import load_dotenv
# Import tracing configuration (Azure AI Foundry only)
from agents.tracing_config import (
    initialize_tracing_with_context,
    get_tracer, 
    get_current_span,
    trace_async_function,
    trace_function,
    add_span_attributes,
    record_agent_interaction,
    record_table_operation,
    record_llm_interaction,
    record_batch_operation,
    record_error_details,
    record_search_operation
)
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

# Import shared error handling utilities
from agents.error_handler import (
    get_detailed_run_error,
    should_retry_server_error,
    retry_agent_run_on_server_error
)
from semantic_kernel.agents import AzureAIAgent, AzureAIAgentSettings, AzureAIAgentThread
from agents.kubernetes_discovery_agent import KubernetesDiscoveryAgent

# Import utility functions
from agents.utils.common_utils import (
    sanitize_table_name,
    get_table_service_client,
    get_blob_service_client,
    upload_file_to_container,
    upload_content_to_container,
    load_instructions_from_file,
    get_unique_blob_metadata
)
from agents.utils.agent_utils import (
    find_existing_agent,
    build_agent_name,
    execute_run_with_retry,
    extract_json_from_text,
    build_metadata_filter_expression,
    create_filtered_search_tool,
    RunResult
)

# Load environment variables  
load_dotenv()

# Import logging configuration
from agents.logging_config import get_logger, set_ai_thread_id

# Create logger for this module
logger = get_logger(__name__)

# NOTE: sanitize_table_name, get_table_service_client, and get_blob_service_client
# have been moved to agents.utils.common_utils. They are imported at the top of the file.


# ===== WORKAROUND FOR AZURE-AI-AGENTS TELEMETRY BUG =====
# The azure-ai-agents SDK (version 1.2.0b4) has a bug where it crashes when
# processing 'knowledge' tool type in telemetry. This monkey patch fixes it.
try:
    from azure.ai.agents.telemetry._ai_agents_instrumentor import _AIAgentsInstrumentorPreview
    from azure.ai.agents.models import RunStep
    
    # Store the original method
    _original_process_tool_calls = _AIAgentsInstrumentorPreview._process_tool_calls
    
    def _patched_process_tool_calls(self, step: RunStep):
        """
        Patched version that handles unknown tool types (like 'knowledge') gracefully.
        
        The original code fails with KeyError when it encounters a tool type that
        doesn't have a matching key in the as_dict() result. This happens with
        the 'knowledge' tool type used by file search/vector stores.
        """
        tool_calls = []
        if not step.step_details or not hasattr(step.step_details, 'tool_calls'):
            return tool_calls
            
        for t in step.step_details.tool_calls:
            try:
                # Try to handle MCP tools specially
                if t.type == "mcp":
                    tool_calls.append({
                        "id": t.id,
                        "type": t.type,
                        "arguments": t.arguments,
                        "name": t.name,
                        "output": t.output,
                        "server_label": t.server_label or "",
                    })
                else:
                    # For other tool types, safely access the tool details
                    tool_dict = t.as_dict()
                    
                    # Check if the tool type key exists in the dictionary
                    if t.type in tool_dict:
                        tool_details = tool_dict[t.type]
                        tool_calls.append({
                            "id": t.id,
                            "type": t.type,
                            t.type: tool_details,
                        })
                    else:
                        # Handle unknown tool types - extract from 'tool_call' if available
                        if 'tool_call' in tool_dict:
                            # Use the generic tool_call data
                            tool_calls.append({
                                "id": t.id,
                                "type": t.type,
                                t.type: tool_dict['tool_call'],
                            })
                            # Log at DEBUG level for known issues like 'knowledge'
                            if t.type == 'knowledge':
                                logger.debug(f"Telemetry: Handled 'knowledge' tool type using generic tool_call data")
                            else:
                                logger.warning(f"Telemetry: Unknown tool type '{t.type}' - using tool_call fallback")
                        else:
                            # Completely unknown structure
                            logger.warning(f"Telemetry: Unknown tool type '{t.type}' with no tool_call data. Keys: {list(tool_dict.keys())}")
                            tool_calls.append({
                                "id": t.id,
                                "type": t.type,
                                "details": "Tool type not supported by telemetry instrumentation",
                            })
            except Exception as e:
                # Catch any other errors and continue processing
                logger.warning(f"Telemetry: Failed to process tool call of type '{getattr(t, 'type', 'unknown')}': {e}")
                continue
        
        return tool_calls
    
    # Apply the monkey patch
    _AIAgentsInstrumentorPreview._process_tool_calls = _patched_process_tool_calls
    logger.info("✓ Applied telemetry monkey patch for azure-ai-agents 'knowledge' tool bug")
    
except ImportError as ie:
    # Telemetry module not available
    logger.debug(f"Telemetry module not available, skipping patch: {ie}")
except Exception as e:
    # Any other error during patching
    logger.warning(f"Failed to apply telemetry patch (non-critical): {e}")
# ===== END WORKAROUND =====

logger.info("Insights Orchestrator Agent initialized")


# =============================================================================
# HELPER FUNCTIONS FOR RESPONSE PROCESSING
# =============================================================================

def _apply_no_info_confidence_adjustment(response_text: str) -> float:
    """
    Check if response contains patterns indicating no information was found.
    Returns 0.0 if no-info patterns detected, None otherwise (to preserve original confidence).
    
    Args:
        response_text: The response text to check
    
    Returns:
        0.0 if no-info patterns detected, None otherwise
    """
    if not response_text:
        return None
    
    response_lower = response_text.lower()
    
    no_info_patterns = [
        "no explicit mention",
        "no explicit",
        "not explicitly state",
        "does not explicitly state",
        "no mention of",
        "there is no mention",
        "not referenced",
        "not described",
        "not documented",
        "no explicit documentation",
        "available documentation does not",
        "documentation does not contain",
        "no explicit information",
        "no information found",
        "not found in the documentation",
        "not listed",
        "not detailed",
        "not specified",
        "documentation details",
        "available sources detail"
    ]
    
    # Check if response contains patterns indicating no information
    contains_no_info_pattern = any(pattern in response_lower for pattern in no_info_patterns)
    
    # Additional check for "but does not" or "but do not" patterns
    contains_negation = any(phrase in response_lower for phrase in [
        "but does not", "but do not", "but there is no", "but no mention"
    ])
    
    if contains_no_info_pattern or contains_negation:
        return 0.0
    
    return None


def _update_entity_from_response(ent: dict, result: 'RunResult', default_confidence: float = 0.3) -> bool:
    """
    Update entity from run result, handling success, failure, and parsing.
    
    Args:
        ent: Entity to update
        result: RunResult from execute_run_with_retry
        default_confidence: Default confidence for raw/unparsed responses
    
    Returns:
        True if entity was successfully updated with valid response
    """
    # Handle transport error
    if result.status == "transport_error":
        ent["Response"] = f"Processing interrupted: {result.error_message}"
        ent["Confidence"] = 0.0
        ent["Citation"] = "Transport timeout"
        return False
    
    # Handle failure
    if result.status == "failed":
        ent["Response"] = f"Agent run failed: {result.error_message}"
        ent["Confidence"] = 0.0
        run_id = result.run.id if result.run else "unknown"
        ent["Citation"] = f"Agent processing failed - Run ID: {run_id}"
        return False
    
    # Handle timeout or other non-success
    if result.status not in ["success", "completed", "succeeded"]:
        ent["Response"] = f"Run ended with status: {result.status}"
        ent["Confidence"] = 0.0
        ent["Citation"] = "System error"
        return False
    
    # Try to parse JSON response
    text = result.response_text or ""
    parsed_data = result.parsed_json or extract_json_from_text(text)
    
    if parsed_data:
        ent["Response"] = parsed_data.get("Response", "") or parsed_data.get("response", "")
        ent["Confidence"] = float(parsed_data.get("Confidence", 0.0))
        ent["Citation"] = parsed_data.get("Citation", "") or parsed_data.get("citation", "")
        
        # Clean citation - remove special brackets
        ent["Citation"] = re.sub(r'【[^】]*】', '', ent["Citation"]).strip()
        
        # Apply no-info confidence adjustment
        adjusted = _apply_no_info_confidence_adjustment(ent["Response"])
        if adjusted is not None:
            ent["Confidence"] = adjusted
        
        return bool(ent["Response"])
    else:
        # Use raw response as fallback
        ent["Response"] = text[:1000] if text else ""
        ent["Confidence"] = default_confidence
        ent["Citation"] = "Raw response"
        return bool(text)


class orchplugin:
    # Class variable to store shared thread IDs per application (persists across instances)
    # Key: application_id, Value: thread_id
    _shared_threads: Dict[str, str] = {}
    
    # Class variable to store confidence scores per application (persists across instances)
    # Key: application_id, Value: dict with 'table_confidence_scores' and 'overall_average_confidence_score'
    _confidence_scores: Dict[str, dict] = {}
    
    def __init__(self, progress_callback: Optional[callable] = None):
        """
        Initialize orchplugin with optional progress callback.
        
        Args:
            progress_callback: Optional async callback function(message: str, percentage: int)
                              for reporting progress during long-running operations
        """
        self.progress_callback = progress_callback
        self.agent_threads = {}
        self._shared_threads = {}  # Store shared threads per application_id
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self.logger.info("Orchestrator plugin initialized")
        self.original_agent_instructions = {}  # Store original instructions for agents
    
    @kernel_function(description="Clone all required template tables for the application.")
    def clone_all_templates(self, application_id: str, templates: Optional[List[str]] = None, kubrequest: Optional[bool] = False) -> str:
        logger.info(f"[PLUGIN] clone_all_templates called with application_id={application_id}, templates={templates}")
        tracer = get_tracer()
        with tracer.start_as_current_span("clone_all_templates") as span:
            try:
                default_templates = [
                    "AppDetailsTemplate",
                    "IntegrationDependencyTemplate",
                    "MsSqlDBTemplate",
                    "OracleDBTemplate",
                    "InfrastructureDetails"
                ]
                kubernetesTemplate = "K8Stemplate"
                templates_to_clone = templates or default_templates
                
                add_span_attributes(span, {
                    "application_id": application_id,
                    "template_count": len(templates_to_clone) if not kubrequest else 1,
                    "templates": ", ".join(templates_to_clone) if not kubrequest else kubernetesTemplate
                })
                
                from azure.data.tables import TableServiceClient
                from azure.identity import DefaultAzureCredential as SyncDefaultAzureCredential
                tables_url = os.getenv("AZURE_TABLES_ACCOUNT_URL")
                if not tables_url:
                    span.set_status(Status(StatusCode.ERROR, "Missing AZURE_TABLES_ACCOUNT_URL"))
                    return json.dumps({"result": "error", "message": "Missing AZURE_TABLES_ACCOUNT_URL"})
                tsc = get_table_service_client(tables_url)
                span.add_event("table_service_client_created", {"tables_url": tables_url})
                    
                results = {}
                if(kubrequest):
                    results[kubernetesTemplate] = self._clone_single_template(tsc, kubernetesTemplate, f"K8S{application_id}", application_id)
                else:
                 for template in templates_to_clone:
                    if "AppDetails" in template:
                        target = sanitize_table_name(f"AppDetails{application_id}")
                    elif "IntegrationDependency" in template:
                        target = sanitize_table_name(f"IntegrationDependency{application_id}")
                    elif "MSSQLBD" in template or "MSSQLDB" in template:
                        target = sanitize_table_name(f"MSSQLDB{application_id}")
                    elif "OracleDB" in template:
                        target = sanitize_table_name(f"OracleDB{application_id}")
                    elif "InfrastructureDetails" in template:
                        target = sanitize_table_name(f"InfrastructureDetails{application_id}")
                    else:
                        target = sanitize_table_name(f"{template.replace('Template','')}{application_id}")
                    results[template] = self._clone_single_template(tsc, template, target, application_id)
                
                span.add_event("cloning_completed", {
                    "templates_processed": len(results),
                    "successful_clones": len([r for r in results.values() if r.get("status") in ["created", "exists"]])
                })
                span.set_status(Status(StatusCode.OK))
                return json.dumps({"result": "ok", "cloned": results})
                
            except Exception as ex:
                span.record_exception(ex)
                span.set_status(Status(StatusCode.ERROR, str(ex)))
                return json.dumps({"result": "error", "message": str(ex)})

    def _clone_single_template(self, tsc, template: str, target: str, app_id: str) -> dict:
        """Helper to clone a single template table."""
        tracer = get_tracer()
        with tracer.start_as_current_span("clone_single_template") as span:
            add_span_attributes(span, {
                "template_name": template,
                "target_table": target,
                "application_id": app_id
            })
            
            try:
                # Check if target table already exists with data
                try:
                    tc_target = tsc.get_table_client(table_name=target)
                    existing_count = sum(1 for _ in tc_target.list_entities(results_per_page=1))
                    if existing_count > 0:
                        span.add_event("table_already_exists", {"target_table": target, "existing_rows": existing_count})
                        span.set_status(Status(StatusCode.OK))
                        return {"status": "exists", "table": target, "rows": existing_count}
                except Exception:
                    span.add_event("table_does_not_exist", {"target_table": target})
                
                # Create target table if it doesn't exist
                try:
                    tsc.create_table(table_name=target)
                    span.add_event("table_created", {"target_table": target})
                except Exception as e:
                    if "TableAlreadyExists" not in str(e):
                        span.add_event("table_creation_warning", {"target_table": target, "error": str(e)})
                        logger.warning(f"Create table warning: {e}")
                
                # Get template and target table clients
                tc_template = tsc.get_table_client(table_name=template)
                tc_target = tsc.get_table_client(table_name=target)
                
                # Copy all entities from template to target
                copied = 0
                for entity in tc_template.list_entities():
                    # Set PartitionKey to application_id and ensure PartitionKey and RowKey are strings (Azure Table Storage requirement)
                    entity["PartitionKey"] = str(app_id)  # Convert to string

                    # Ensure RowKey is also a string if it exists
                    if "RowKey" in entity:
                        entity["RowKey"] = str(entity["RowKey"])
                    
                    # Ensure required fields exist for Q&A tables
                    if template not in ["IntegrationDependencyTemplate", "InfrastructureDetailsTemplate", "InfrastructureDetails"]:
                        if "Response" not in entity:
                            entity["Response"] = ""
                        if "Confidence" not in entity:
                            entity["Confidence"] = 0.0
                        if "Citation" not in entity:
                            entity["Citation"] = ""
                    
                    try:
                        tc_target.upsert_entity(entity=entity)
                        copied += 1
                    except Exception as upsert_ex:
                        logger.error(f"❌ Failed to upsert entity (PartitionKey: {entity.get('PartitionKey')}, RowKey: {entity.get('RowKey')}): {upsert_ex}")
                        # Log the entity structure for debugging
                        entity_keys = {k: type(v).__name__ for k, v in entity.items() if k in ['PartitionKey', 'RowKey']}
                        logger.error(f"   Entity key types: {entity_keys}")
                        raise
                
                record_table_operation(span, target, "clone", copied, app_id)
                span.set_status(Status(StatusCode.OK))
                return {"status": "created", "table": target, "copied": copied}
                
            except Exception as ex:
                span.record_exception(ex)
                span.set_status(Status(StatusCode.ERROR, str(ex)))
                return {"status": "error", "message": str(ex)}

    @kernel_function(description="Trigger indexing service for application data and wait for completion. Use type='asr' for assessment report indexing, type='design' for design document indexing, type='planning' for app planning indexing, or folder_prefix for custom folder paths.")
    async def trigger_and_check_indexing(self, application_id: str, type: Optional[str] = None, folder_prefix: Optional[str] = None) -> str:
        """Trigger the indexing Container App service and return the result.
        
        The indexing service is deployed as an Azure Container App and accessed via standard HTTP.
        
        Args:
            application_id: The application ID to index
            type: Optional type of indexing - 'asr' for assessment reports, 'design' for design documents,
                  'planning' for app planning documents.
                  If 'asr', indexes from asr/input/ folder.
                  If 'design', indexes from design/input/ folder.
                  If 'planning', indexes from app-planning/input/ folder.
                  If None, indexes entire container (unless folder_prefix is specified).
            folder_prefix: Optional direct folder prefix path (e.g., 'uploads/2026/'). 
                          Takes precedence over type parameter if both are specified.
        """
        logger.info(f"[PLUGIN] trigger_and_check_indexing called with application_id={application_id}, type={type}, folder_prefix={folder_prefix}")
        tracer = get_tracer()
        with tracer.start_as_current_span("trigger_and_check_indexing") as span:
            add_span_attributes(span, {
                "application_id": application_id,
                "operation": "trigger_indexing",
                "indexing_type": type or "full",
                "folder_prefix": folder_prefix or "none"
            })
            
            try:
                # Get indexer service URL (Container App endpoint)
                indexer_url = os.getenv("AZURE_INDEXING_FUNCTION_URL")  # Keep env var name for backward compatibility
                if not indexer_url:
                    span.set_status(Status(StatusCode.ERROR, "AZURE_INDEXING_FUNCTION_URL not set"))
                    return json.dumps({"result": "error", "message": "AZURE_INDEXING_FUNCTION_URL not set"})
                
                # Container Apps use standard HTTP headers (no function key required)
                headers = {"Content-Type": "application/json"}
                payload = {"appId": application_id, "container": application_id}
                
                # Add folder_prefix - direct parameter takes precedence over type shortcuts
                if folder_prefix:
                    payload["folder_prefix"] = folder_prefix
                    logger.debug(f"Using explicit folder_prefix '{folder_prefix}' for indexing")
                elif type == "asr":
                    payload["folder_prefix"] = "asr/input"
                    logger.debug(f"Using folder_prefix 'asr/input/' for ASR indexing")
                elif type == "design":
                    payload["folder_prefix"] = "design/input"
                    logger.debug(f"Using folder_prefix 'design/input/' for Design indexing")
                elif type == "responder":
                    payload["folder_prefix"] = "responder/input"
                    logger.debug(f"Using folder_prefix 'responder/input/' for Responder agent indexing")
                elif type == "planning":
                    payload["folder_prefix"] = "app-planning/input"
                    logger.debug(f"Using folder_prefix 'app-planning/input/' for Planning agent indexing")
                
                add_span_attributes(span, {
                    "indexer_url": indexer_url,
                    "payload_app_id": application_id,
                    "service_type": "container_app",
                    "folder_prefix": payload.get("folder_prefix", "full_container")
                })

                logger.debug(f"Triggering indexing service for appId={application_id}, container={application_id}")
                logger.debug(f"Indexer URL: {indexer_url}")

                async with aiohttp.ClientSession() as session:
                    async with session.post(indexer_url, json=payload, headers=headers) as response:
                        try:
                            result = await response.json()
                            add_span_attributes(span, {
                                "http_status": response.status,
                                "response_type": "json"
                            })
                            
                            if response.status >= 400:
                                span.set_status(Status(StatusCode.ERROR, f"HTTP {response.status}"))
                                span.add_event("indexing_service_error", {
                                    "http_status": response.status,
                                    "result": str(result)[:500]  # Truncate long results
                                })
                                return json.dumps({"result": "error", "message": f"HTTP {response.status}: {result}"})
                            else:
                                span.add_event("indexing_service_success", {
                                    "http_status": response.status,
                                    "result_keys": list(result.keys()) if isinstance(result, dict) else "non_dict"
                                })
                                span.set_status(Status(StatusCode.OK))
                                return json.dumps({"result": "success", "data": result})
                        except Exception as parse_ex:
                            text = await response.text()
                            span.add_event("response_parse_error", {
                                "http_status": response.status,
                                "response_text_length": len(text),
                                "parse_error": str(parse_ex)
                            })
                            span.set_status(Status(StatusCode.ERROR, f"Parse error: {parse_ex}"))
                            return json.dumps({"result": "error", "raw": text, "http_status": response.status})
                            
                logger.debug(f"Indexing service response ({response.status}): {result}")
                return json.dumps(result)
                
            except Exception as ex:
                span.record_exception(ex)
                span.set_status(Status(StatusCode.ERROR, str(ex)))
                return json.dumps({"result": "error", "message": str(ex)})

    @kernel_function(description="Create or retrieve an Responder agent for the application using the responder_agent.py responder_agent function.")
    async def responder_insights_agent(self, application_id: str) -> str:
        """Create or retrieve a Responder agent for the given application ID using the responder_agent function."""
        logger.info(f"[PLUGIN] responder_insights_agent called with application_id={application_id}")
        tracer = get_tracer()
        with tracer.start_as_current_span("responder_insights_agent") as span:
            add_span_attributes(span, {
                "application_id": application_id,
                "operation": "responder_agent"
            })
            
            try:
                # Import and use the responder_agent function from responder_agent.py
                logger.debug("Importing Responder agent module")
                from agents.responder_agent import responder_agent
                
                span.add_event("importing_insights_module")
                
                # Call the responder_agent function to create/retrieve the agent
                agent_id = await responder_agent(application_id)
                logger.info(f"Responder agent created/retrieved with ID: {agent_id} for application: {application_id}")
                
                record_agent_interaction(span, agent_id, operation_type="responder_agent")
                span.add_event("agent_ensured", {"agent_id": agent_id})
                span.set_status(Status(StatusCode.OK))
                
                return json.dumps({
                    "result": "success", 
                    "agent_id": agent_id,
                    "status": "agent_ready",
                    "message": f"Insights Agent ready for application {application_id}"
                })

            except Exception as ex:
                logger.error(f"Error creating/retrieving Responder agent for {application_id}: {str(ex)}")
                span.record_exception(ex)
                span.set_status(Status(StatusCode.ERROR, str(ex)))
                return json.dumps({"result": "error", "message": str(ex)})
            


    def _sanitize_index_name(self, raw: str) -> str:
        """Sanitize arbitrary application name into a valid Azure AI Search index name."""
        try:
            s = (raw or "").lower().strip()
            s = re.sub(r"[^a-z0-9-]", "-", s)
            s = re.sub(r"-+", "-", s).strip('-')
            if not s or not s[0].isalnum():
                s = f"app-{hashlib.sha1((raw or 'x').encode()).hexdigest()[:8]}"
            if len(s) < 2:
                s = (s + "ix")[:2]
            if len(s) > 128:
                s = s[:128]
            return s
        except Exception:
            return f"app-{hashlib.sha1((raw or 'fallback').encode()).hexdigest()[:8]}"

    def _get_kv_secret(self, sc_name: str) -> Optional[str]:
        """Retrieve a single secret value from Key Vault using DefaultAzureCredential.

        Expects AZURE_KEY_VAULT_URL in environment. Returns None if not available or on failure.
        """
        try:
            try:
                from azure.keyvault.secrets import SecretClient
            except Exception:
                logger.debug("SecretClient unavailable (azure-keyvault-secrets not installed)")
                return None
            
            kv_url = os.getenv("AZURE_KEY_VAULT_URL") or os.getenv("KEY_VAULT_URL")
            if not kv_url:
                logger.debug(f"AZURE_KEY_VAULT_URL not set; skipping Key Vault secret retrieval for {sc_name}")
                return None
            
            from azure.identity import DefaultAzureCredential as SyncDefaultAzureCredential
            cred = SyncDefaultAzureCredential(exclude_shared_token_cache_credential=True)
            sc = SecretClient(vault_url=kv_url, credential=cred)
            return sc.get_secret(sc_name).value
        except Exception as ex:
            logger.debug(f"Key Vault secret fetch failed for {sc_name}: {ex}")
            # Record exception in span if available
            current_span = trace.get_current_span()
            if current_span and current_span.is_recording():
                current_span.record_exception(ex)
                current_span.add_event("keyvault_secret_fetch_failed", {"sc_name": sc_name})
            return None

    @kernel_function(description="Process all QA tables with the Responder agent using optimized bulk processing.")
    async def process_all_qa_tables(self, application_id: str, agent_id: str, storage_account_name: str) -> str:
        """Process questions in all QA tables using the Responder agent with bulk processing for AppDetails."""
        logger.info(f"[PLUGIN] process_all_qa_tables called with application_id={application_id}, agent_id={agent_id}")
        tracer = get_tracer()
        with tracer.start_as_current_span("process_all_qa_tables") as span:
            add_span_attributes(span, {
                "application_id": application_id,
                "agent_id": agent_id,
                "operation": "process_qa_tables"
            })
            
            try:
                tables_to_process = [
                    sanitize_table_name(f"AppDetails{application_id}"),
                    sanitize_table_name(f"MSSQLDB{application_id}"),      
                    sanitize_table_name(f"OracleDB{application_id}")      
                ]
                
                # Check if K8S table exists and add it to processing list
                k8s_table_name = f"K8S{application_id}"
                try:
                    from azure.data.tables import TableServiceClient
                    from azure.identity import DefaultAzureCredential as SyncDefaultAzureCredential
                    tables_url = os.getenv("AZURE_TABLES_ACCOUNT_URL")
                    if tables_url:
                        cred = SyncDefaultAzureCredential(exclude_shared_token_cache_credential=True)
                        tsc = TableServiceClient(endpoint=tables_url, credential=cred)
                        k8s_tc = tsc.get_table_client(table_name=k8s_table_name)
                        # Check if K8S table exists and has data
                        try:
                            next(k8s_tc.list_entities(results_per_page=1), None)
                            tables_to_process.append(k8s_table_name)
                            logger.info(f"K8S table found: {k8s_table_name} - adding to processing list")
                            span.add_event("k8s_table_found", {"table_name": k8s_table_name})
                        except Exception as k8s_check_ex:
                            if "TableNotFound" in str(k8s_check_ex) or "ResourceNotFound" in str(k8s_check_ex):
                                logger.info(f"K8S table not found: {k8s_table_name} - skipping")
                                span.add_event("k8s_table_not_found", {"table_name": k8s_table_name})
                            else:
                                logger.warning(f"Error checking K8S table existence: {str(k8s_check_ex)}")
                except Exception as ex:
                    logger.warning(f"Could not check for K8S table: {str(ex)}")
                
                logger.debug(f"Tables to process: {tables_to_process}")
                
                add_span_attributes(span, {
                    "table_count": len(tables_to_process),
                    "tables": ", ".join(tables_to_process),
                    "k8s_table_included": k8s_table_name in tables_to_process
                })
                
                all_results = {}
                total_processed = 0
                table_confidence_scores = {}  # Store aggregate scores per table
                total_tables = len(tables_to_process)
                
                endpoint = os.environ.get("AZURE_EXISTING_AIPROJECT_ENDPOINT")
                
                # Process each table with its own client to avoid HTTP transport timeout
                # This prevents connection timeout issues when processing takes longer than Azure's timeout
                async with DefaultAzureCredential(exclude_shared_token_cache_credential=True) as creds:
                    for idx, table_name in enumerate(tables_to_process):
                        # Create a fresh client for each table to prevent connection timeout
                        async with AIProjectClient(credential=creds, endpoint=endpoint) as ai_client:
                            
                            record_agent_interaction(span, agent_id, operation_type="process_qa_tables")
                            
                            with tracer.start_as_current_span("process_single_qa_table") as table_span:
                                add_span_attributes(table_span, {
                                    "table_name": table_name,
                                    "application_id": application_id,
                                    "agent_id": agent_id
                                })
                                
                                try:
                                    # Check if table exists
                                    from azure.data.tables import TableServiceClient
                                    from azure.identity import DefaultAzureCredential as SyncDefaultAzureCredential
                                    tables_url = os.getenv("AZURE_TABLES_ACCOUNT_URL")
                                    if not tables_url:
                                        table_span.add_event("table_url_missing", {"table_name": table_name})
                                        continue
                                    tsc = get_table_service_client(tables_url)
                                    table_span.add_event("table_service_client_created", {"tables_url": tables_url})
                                    
                                    tc = tsc.get_table_client(table_name=table_name)
                                    # Quick check if table exists and has data
                                    try:
                                        next(tc.list_entities(results_per_page=1), None)
                                        table_span.add_event("table_exists", {"table_name": table_name})
                                    except Exception as e:
                                        if "TableNotFound" in str(e) or "ResourceNotFound" in str(e):
                                            table_span.add_event("table_not_found", {"table_name": table_name, "error": str(e)})
                                            continue
                                        else:
                                            table_span.record_exception(e)
                                    
                                    # ===== NEW: Validation - Check existing confidence scores =====
                                    existing_scores = await self._check_existing_confidence_scores(
                                        tc, table_name, application_id, table_span
                                    )
                                    
                                    # Determine if this is a retry run (questions already processed)
                                    is_retry_run = existing_scores["has_scores"]
                                    
                                    if is_retry_run:
                                        logger.info(f"Table {table_name}: Detected existing scores. "
                                                   f"Low confidence count: {existing_scores['low_confidence_count']} "
                                                   f"(threshold ≤ 0.5)")
                                        table_span.add_event("retry_run_detected", {
                                            "low_confidence_count": existing_scores["low_confidence_count"],
                                            "total_questions": existing_scores["total_questions"],
                                            "average_score": existing_scores["average_score"]
                                        })
                                    # ===== END NEW =====
                                    
                                    # Message initialization
                                    result = await self._initialize_first_agent_run(
                                            ai_client, agent_id, table_name, application_id, 
                                            is_retry_run=is_retry_run, 
                                            low_confidence_entities=existing_scores.get("low_confidence_entities", [])
                                        )
                                    
                                    # Use bulk processing for AppDetails, individual for others
                                    if "AppDetails" in table_name:
                                        result = await self._process_appdetails_bulk_by_category(
                                            ai_client, agent_id, table_name, application_id, 
                                            is_retry_run=is_retry_run, storage_account_name=storage_account_name,
                                            low_confidence_entities=existing_scores.get("low_confidence_entities", [])
                                        )
                                    else:
                                        result = await self._process_questions_for_table(
                                            ai_client, agent_id, table_name, application_id,
                                            is_retry_run=is_retry_run, storage_account_name=storage_account_name,
                                            low_confidence_entities=existing_scores.get("low_confidence_entities", [])
                                        )
                                    
                                    all_results[table_name] = result
                                    
                                    # ===== NEW: Calculate aggregate confidence score for this table =====
                                    if result.get("result") == "ok":
                                        aggregate_score = await self._calculate_table_aggregate_score(
                                            tc, table_name, application_id, table_span
                                        )
                                        table_confidence_scores[table_name] = aggregate_score
                                        result["aggregate_confidence_score"] = aggregate_score
                                        
                                        logger.info(f"Table {table_name}: Aggregate confidence score = {aggregate_score:.2f}")
                                    # ===== END NEW =====
                                    
                                    if result.get("result") == "ok":
                                        processed_count = result.get("answered", 0)
                                        total_processed += processed_count
                                        
                                        record_table_operation(table_span, table_name, "process_questions", processed_count, application_id)
                                        table_span.add_event("questions_processed", {
                                            "answered": processed_count,
                                            "total_questions": result.get("total_questions", 0),
                                            "aggregate_confidence_score": result.get("aggregate_confidence_score", 0.0)
                                        })
                                        table_span.set_status(Status(StatusCode.OK))
                                    else:
                                        table_span.add_event("processing_failed", {
                                            "error_message": result.get("message", "Unknown error")
                                        })
                                        table_span.set_status(Status(StatusCode.ERROR, result.get("message", "Processing failed")))
                                        
                                except Exception as ex:
                                    all_results[table_name] = {"result": "error", "message": str(ex)}
                                    table_span.record_exception(ex)
                                    table_span.set_status(Status(StatusCode.ERROR, str(ex)))
                                    continue
                        
                        # Report progress AFTER processing each table to show completion
                        if self.progress_callback:
                            progress_pct = int(((idx + 1) / total_tables) * 100)
                            await self.progress_callback(
                                f"Completed table {idx+1}/{total_tables}: {table_name}",
                                progress_pct
                            )
                    
                    # Calculate overall average confidence score 
                    overall_average_score = 0.0
                    if table_confidence_scores:
                        overall_average_score = sum(table_confidence_scores.values()) / len(table_confidence_scores)
                        logger.info(f"Overall average confidence score across all tables: {overall_average_score:.2f}")
                    
                    add_span_attributes(span, {
                        "total_questions_processed": total_processed,
                        "tables_processed": len(all_results),
                        "successful_tables": len([r for r in all_results.values() if r.get("result") == "ok"]),
                        "overall_average_confidence_score": overall_average_score
                    })
                    
                    span.add_event("qa_processing_completed", {
                        "total_processed": total_processed,
                        "tables_count": len(all_results),
                        "results_summary": {k: v.get("result", "unknown") for k, v in all_results.items()},
                        "confidence_scores": table_confidence_scores,
                        "overall_average_score": overall_average_score
                    })
                    
                    # The shared thread will be created automatically by _get_shared_thread when first needed
                    # This allows context from QA processing to be available in later steps
                    logger.info(f"Shared thread for application {application_id} will be created on first use - will be reused by all functions")
                    
                    # Store confidence scores for this application (accessible by API)
                    self._confidence_scores[application_id] = {
                        "table_confidence_scores": table_confidence_scores,
                        "overall_average_confidence_score": round(overall_average_score, 2)
                    }
                    logger.info(f"Stored confidence scores for application {application_id}")
                    
                    span.set_status(Status(StatusCode.OK))
                    return json.dumps({
                        "result": "ok",
                        "tables_processed": len(all_results),
                        "total_questions_processed": total_processed,
                        "results": all_results,
                        "table_confidence_scores": table_confidence_scores,
                        "overall_average_confidence_score": round(overall_average_score, 2)
                    })
                
            except Exception as ex:
                span.record_exception(ex)
                span.set_status(Status(StatusCode.ERROR, str(ex)))
                return json.dumps({"result": "error", "message": str(ex)})

    @trace_async_function("check_existing_confidence_scores")
    async def _check_existing_confidence_scores(self, tc, table_name: str, partition_key: str, span) -> dict:
        """
        Check if questions in the table have existing confidence scores.
        Identifies low confidence questions (≤ 0.5) for retry processing.
        
        Args:
            tc: Table client
            table_name: Name of the table
            partition_key: Partition key (application_id)
            span: OpenTelemetry span for tracing
            
        Returns:
            Dictionary with:
                - has_scores: True if any scores exist
                - low_confidence_count: Number of questions with confidence ≤ 0.5
                - low_confidence_entities: List of entities with low confidence
                - total_questions: Total number of questions
                - average_score: Average confidence score (if scores exist)
        """
        try:
            escaped_pk = str(partition_key).replace("'", "''")
            server_filter = f"PartitionKey eq '{escaped_pk}'"
            entities = list(tc.query_entities(query_filter=server_filter))
            
            questions_with_scores = []
            low_confidence_entities = []
            total_questions = 0
            
            for ent in entities:
                question = ent.get("Question")
                if not question:
                    continue
                
                total_questions += 1
                confidence = ent.get("Confidence")
                
                # Check if confidence score exists (not None, not empty)
                if confidence is not None and confidence != "":
                    try:
                        confidence_value = float(confidence)
                        questions_with_scores.append(confidence_value)
                        
                        # Flag low confidence questions (≤ 0.5)
                        if confidence_value <= 0.5:
                            low_confidence_entities.append(ent)
                            logger.debug(f"Low confidence question flagged: {question[:50]}... (confidence: {confidence_value})")
                    except (ValueError, TypeError):
                        # Invalid confidence value - treat as needs processing
                        low_confidence_entities.append(ent)
            
            has_scores = len(questions_with_scores) > 0
            average_score = sum(questions_with_scores) / len(questions_with_scores) if questions_with_scores else 0.0
            
            result = {
                "has_scores": has_scores,
                "low_confidence_count": len(low_confidence_entities),
                "low_confidence_entities": low_confidence_entities,
                "total_questions": total_questions,
                "average_score": round(average_score, 2),
                "scored_questions": len(questions_with_scores)
            }
            
            span.add_event("confidence_scores_checked", {
                "table_name": table_name,
                "has_scores": has_scores,
                "low_confidence_count": len(low_confidence_entities),
                "total_questions": total_questions,
                "average_score": round(average_score, 2)
            })
            
            logger.info(f"Table {table_name}: Found {len(questions_with_scores)} questions with scores, "
                       f"{len(low_confidence_entities)} with low confidence (≤ 0.5), "
                       f"average score: {average_score:.2f}")
            
            return result
            
        except Exception as ex:
            logger.error(f"Error checking confidence scores: {ex}")
            span.record_exception(ex)
            return {
                "has_scores": False,
                "low_confidence_count": 0,
                "low_confidence_entities": [],
                "total_questions": 0,
                "average_score": 0.0,
                "scored_questions": 0,
                "error": str(ex)
            }

    @trace_async_function("calculate_table_aggregate_score")
    async def _calculate_table_aggregate_score(self, tc, table_name: str, partition_key: str, span) -> float:
        """
        Calculate the aggregate (average) confidence score for all questions in a table.
        
        Args:
            tc: Table client
            table_name: Name of the table
            partition_key: Partition key (application_id)
            span: OpenTelemetry span for tracing
            
        Returns:
            Average confidence score (0.0 - 1.0)
        """
        try:
            escaped_pk = str(partition_key).replace("'", "''")
            server_filter = f"PartitionKey eq '{escaped_pk}'"
            entities = list(tc.query_entities(query_filter=server_filter))
            
            confidence_scores = []
            
            for ent in entities:
                question = ent.get("Question")
                if not question:
                    continue
                
                confidence = ent.get("Confidence")
                if confidence is not None and confidence != "":
                    try:
                        confidence_value = float(confidence)
                        confidence_scores.append(confidence_value)
                    except (ValueError, TypeError):
                        # Skip invalid confidence values
                        pass
            
            if not confidence_scores:
                logger.warning(f"Table {table_name}: No valid confidence scores found")
                return 0.0
            
            average_score = sum(confidence_scores) / len(confidence_scores)
            
            span.add_event("aggregate_score_calculated", {
                "table_name": table_name,
                "average_score": round(average_score, 2),
                "scored_questions": len(confidence_scores)
            })
            
            return round(average_score, 2)
            
        except Exception as ex:
            logger.error(f"Error calculating aggregate score for {table_name}: {ex}")
            span.record_exception(ex)
            return 0.0

    async def _calculate_dependency_aggregate_score(self, tc, table_name: str, partition_key: str, span) -> float:
        """
        Calculate the aggregate (average) confidence score for all dependencies in a table.
        
        Args:
            tc: Table client
            table_name: Name of the dependency table
            partition_key: Partition key (application_id)
            span: OpenTelemetry span for tracing
            
        Returns:
            Average confidence score (0.0 - 1.0)
        """
        try:
            escaped_pk = str(partition_key).replace("'", "''")
            server_filter = f"PartitionKey eq '{escaped_pk}'"
            entities = list(tc.query_entities(query_filter=server_filter))
            
            confidence_scores = []
            
            for ent in entities:
                confidence = ent.get("Confidence")
                if confidence is not None and confidence != "":
                    try:
                        confidence_value = float(confidence)
                        confidence_scores.append(confidence_value)
                    except (ValueError, TypeError):
                        # Skip invalid confidence values
                        pass
            
            if not confidence_scores:
                logger.warning(f"Dependency table {table_name}: No valid confidence scores found")
                return 0.0
            
            average_score = sum(confidence_scores) / len(confidence_scores)
            
            span.add_event("dependency_aggregate_score_calculated", {
                "table_name": table_name,
                "average_score": round(average_score, 2),
                "scored_dependencies": len(confidence_scores)
            })
            
            return round(average_score, 2)
            
        except Exception as ex:
            logger.error(f"Error calculating dependency aggregate score for {table_name}: {ex}")
            span.record_exception(ex)
            return 0.0

    async def _calculate_infrastructure_aggregate_score(self, tc, table_name: str, partition_key: str, span) -> float:
        """
        Calculate the aggregate (average) confidence score for all infrastructure records in a table.
        
        Args:
            tc: Table client
            table_name: Name of the infrastructure table
            partition_key: Partition key (application_id)
            span: OpenTelemetry span for tracing
            
        Returns:
            Average confidence score (0.0 - 1.0)
        """
        try:
            escaped_pk = str(partition_key).replace("'", "''")
            server_filter = f"PartitionKey eq '{escaped_pk}'"
            entities = list(tc.query_entities(query_filter=server_filter))
            
            confidence_scores = []
            
            for ent in entities:
                confidence = ent.get("Confidence")
                if confidence is not None and confidence != "":
                    try:
                        confidence_value = float(confidence)
                        confidence_scores.append(confidence_value)
                    except (ValueError, TypeError):
                        # Skip invalid confidence values
                        pass
            
            if not confidence_scores:
                logger.warning(f"Infrastructure table {table_name}: No valid confidence scores found")
                return 0.0
            
            average_score = sum(confidence_scores) / len(confidence_scores)
            
            span.add_event("infrastructure_aggregate_score_calculated", {
                "table_name": table_name,
                "average_score": round(average_score, 2),
                "scored_infrastructure": len(confidence_scores)
            })
            
            return round(average_score, 2)
            
        except Exception as ex:
            logger.error(f"Error calculating infrastructure aggregate score for {table_name}: {ex}")
            span.record_exception(ex)
            return 0.0
        
    @trace_async_function("initialize_first_agent_run")
    async def _initialize_first_agent_run(self, client_obj, agent_id: str, table_name: str, partition_key: str, 
                                                   is_retry_run: bool = False, low_confidence_entities: List = None, thread_id: str = None, initializer_prompt: str = None) -> dict:
        """Initialize agent run with a warm-up message.
        
        Args:
            client_obj: AI client object
            agent_id: Agent ID for processing
            table_name: Name of the table to process
            partition_key: Partition key (application_id)
            is_retry_run: If True, only process low confidence entities
            low_confidence_entities: List of entities with low confidence scores to retry
            thread_id: Optional existing thread ID
            initializer_prompt: Optional custom initializer prompt
        """
        add_span_attributes(get_current_span(), {
            "agent_id": agent_id,
            "table_name": table_name,
            "partition_key": partition_key,
            "operation": "initialize_agent_run",
            "is_retry_run": is_retry_run,
            "low_confidence_count": len(low_confidence_entities) if low_confidence_entities else 0
        })
        
        # Get or create thread
        if thread_id is None:
            thread_id = await self._get_shared_thread(partition_key, agent_id, client_obj, "initialize_first_agent_run")

        # Default initializer prompt
        if initializer_prompt is None:
            initializer_prompt = """Initializing the agent. Are you ready for answering the assessment questions? Use only the AI Search tool. Do not answer on your own."""
        
        logger.debug(f"Sending initialiser prompt to agent {agent_id}")
        logger.debug(f"Prompt content:\n{initializer_prompt}")
        
        # Use the common utility function for run execution
        result = await execute_run_with_retry(
            client=client_obj,
            agent_id=agent_id,
            thread_id=thread_id,
            prompt=initializer_prompt,
            context_description=f"Initialize agent for {table_name}",
            max_wait=300,  # 5 minutes timeout
            max_retries=3,
            parse_json=False,
            track_token_usage=True
        )
        
        if result.status == "success":
            logger.debug(f"Agent {agent_id} response length: {len(result.response_text or '')} characters")
            if result.response_text:
                logger.debug(f"Agent response preview: {result.response_text[:200]}...")
            return {
                "result": "ok",
                "answered": "initialized",
                "total_questions": "1"
            }
        elif result.status == "failed":
            logger.error(f"Initializer message failed: {result.error_message}")
            return {
                "result": "error",
                "message": result.error_message,
                "answered": "0",
                "total_questions": "1"
            }
        elif result.status == "transport_error":
            logger.error(f"Transport error during initialization: {result.error_message}")
            return {
                "result": "error",
                "message": result.error_message,
                "answered": "0",
                "total_questions": "1"
            }
        else:
            logger.warning(f"Initialization ended with status: {result.status}")
            return {
                "result": "ok",
                "answered": "initialized",
                "total_questions": "1",
                "status": result.status
            }

    @trace_async_function("process_appdetails_bulk_by_category")
    async def _process_appdetails_bulk_by_category(self, client_obj, agent_id: str, table_name: str, partition_key: str,
                                                   storage_account_name: str,
                                                   is_retry_run: bool = False, low_confidence_entities: List = None) -> dict:
        """Process AppDetails questions in bulk grouped by existing Category column values.
        
        Args:
            client_obj: AI client object
            agent_id: Agent ID for processing
            table_name: Name of the table to process
            partition_key: Partition key (application_id)
            is_retry_run: If True, only process low confidence entities
            low_confidence_entities: List of entities with low confidence scores to retry
        """
        add_span_attributes(get_current_span(), {
            "agent_id": agent_id,
            "table_name": table_name,
            "partition_key": partition_key,
            "operation": "process_appdetails_bulk",
            "is_retry_run": is_retry_run,
            "low_confidence_count": len(low_confidence_entities) if low_confidence_entities else 0
        })
        try:
            from azure.data.tables import TableServiceClient, UpdateMode as TableUpdateMode
            from azure.identity import DefaultAzureCredential as SyncDefaultAzureCredential
            
            tables_url = os.getenv("AZURE_TABLES_ACCOUNT_URL")
            if not tables_url:
               return {"result": "error", "message": "No table storage connection"}
            tsc = get_table_service_client(tables_url)
            tc = tsc.get_table_client(table_name=table_name)
           
            # --- NEW: Get unique metadata and apply category logic ---
            container_name = partition_key  # Assuming container name is application_id
            metadata_set = get_unique_blob_metadata(container_name, storage_account_name)
            categories_found = [v for k, v in metadata_set if k == 'category']
            logger.info(f"Categories found in blob metadata: {categories_found}")
            has_completeuaq = 'completeuaq' in categories_found
            has_appdetails = 'appdetails' in categories_found
            if has_completeuaq:
                appdetails_metadata_criteria = {"category": "completeuaq"}
                logger.info("Using 'completeuaq' as AppDetails metadata_criteria.")
            elif has_appdetails:
                appdetails_metadata_criteria = {"category": "appdetails"}
                logger.info("Using 'appdetails' as AppDetails metadata_criteria.")
            else:
                appdetails_metadata_criteria = None
                logger.info(f"No 'completeuaq' or 'appdetails' found in blob metadata for container {container_name}")
            
            # Build filtered search tool if metadata criteria provided
            tools_override = None
            tool_resources_override = None
            if appdetails_metadata_criteria:
                from agents.utils.agent_utils import create_filtered_search_tool, build_metadata_filter_expression
               
                # Build filter expression using the shared utility function
                filter_expression = build_metadata_filter_expression(partition_key, appdetails_metadata_criteria)
               
                logger.info(f"Using direct metadata filtering with criteria: {appdetails_metadata_criteria}")
                get_current_span().add_event("direct_metadata_filter_applied", {
                    "criteria": str(appdetails_metadata_criteria),
                    "filter_expression": filter_expression
                })
               
                filtered_tool_result = await create_filtered_search_tool(
                    client=client_obj,
                    partition_key=partition_key,
                    filter_expression=filter_expression,
                    top_k=50  # Increase top_k for comprehensive server discovery
                )
                tools_override = filtered_tool_result.definitions
                tool_resources_override = filtered_tool_result.resources
           
            if tools_override:
                logger.debug(f"Created custom search tool with filter: {filter_expression}")
                # Build event attributes, avoiding None values (OpenTelemetry doesn't accept NoneType)
                event_attrs = {
                    "filter_expression": filter_expression,
                    "has_tool_resources": tool_resources_override is not None
                }
                if appdetails_metadata_criteria:
                    event_attrs["metadata_criteria"] = str(appdetails_metadata_criteria)
                get_current_span().add_event("custom_filter_applied", event_attrs)
           
            # Build metadata description for agent query
            metadata_desc = ""
            if appdetails_metadata_criteria:
                criteria_parts = [f"{k}={v}" for k, v in appdetails_metadata_criteria.items()]
                metadata_desc = f" with metadata [{', '.join(criteria_parts)}]"
           

            # ===== NEW: Handle retry run - only process low confidence entities =====
            if is_retry_run and low_confidence_entities:
                logger.info(f"Retry run: Processing {len(low_confidence_entities)} low confidence questions only")
                entities = low_confidence_entities
            else:
                # Get all entities with questions (normal first run)
                escaped_pk = str(partition_key).replace("'", "''")
                server_filter = f"PartitionKey eq '{escaped_pk}'"
                entities = list(tc.query_entities(query_filter=server_filter))
            # ===== END NEW =====
            
            # Group entities by existing Category column
            categories = {}
            for ent in entities:
                question = ent.get("Question")
                if not question:
                    continue
                # Use existing Category from template (manually assigned)
                category = ent.get("Category", "").strip()
                if not category:
                    category = "Uncategorized"
                if category not in categories:
                    categories[category] = []
                categories[category].append(ent)
           
            # Use shared thread for this agent to preserve context
            thread_id = await self._get_shared_thread(partition_key, agent_id, client_obj, "process_appdetails_bulk_by_category")
           
            try:
                # Agent now uses predefined BULK_PROCESSING_MODE section in instructions
                logger.info(f"Using predefined BULK_PROCESSING_MODE section for agent {agent_id}")
               
                total_answered = 0
                total_questions = sum(len(entities_list) for entities_list in categories.values())
                low_confidence_entities = []
               
                logger.debug(f"Processing {total_questions} questions across {len(categories)} categories")
               
                # Process each category in bulk
                for category, category_entities in categories.items():
                    if not category_entities:
                        continue
                   
                    logger.debug(f"Processing category '{category}' with {len(category_entities)} questions")
                   
                    # Process in batches of 10-15 questions per API call
                    batch_size = min(15, len(category_entities))
                   
                    for i in range(0, len(category_entities), batch_size):
                        batch = category_entities[i:i + batch_size]
                       
                        # Create structured JSON input for the agent
                        questions_json = {
                            "category": category,
                            "questions": []
                        }
                       
                        for idx, ent in enumerate(batch):
                            question_item = {
                                "id": idx + 1,
                                "question": ent.get("Question", ""),
                                "guidance": ent.get("Guidance", ""),
                                "row_key": ent.get("RowKey", "")
                            }
                            questions_json["questions"].append(question_item)
                        
                        # Simplified prompt - instructions are now in agent configuration
                        bulk_prompt = f"""Always use the project-index-{partition_key}/versions/1 knowledge from the AI Search tool to answer the questions. **Category:** {category}
**Questions to Answer:**
```json
{json.dumps(questions_json, indent=2)}
```"""
                        
                        # Log the prompt when debug level is enabled
                        logger.debug(f"Sending bulk prompt to agent {agent_id} for category '{category}' with {len(batch)} questions:")
                        
                        # Use execute_run_with_retry for the run processing
                        result = await execute_run_with_retry(
                            client=client_obj,
                            agent_id=agent_id,
                            thread_id=thread_id,
                            prompt=bulk_prompt,
                            context_description=f"Bulk processing AppDetails for category '{category}'",
                            max_wait=300,  # 5 minutes per batch
                            max_retries=3,
                            parse_json=False,  # We'll parse the bulk response ourselves
                            track_token_usage=True,
                            tools=tools_override,  # Apply custom filter at run level
                            tool_resources=tool_resources_override  # Required for project index filter to work
                        )
                        
                        # Handle transport error
                        if result.status == "transport_error":
                            logger.error(f"Transport error for category '{category}': {result.error_message}")
                            for ent in batch:
                                ent["Response"] = f"Processing interrupted: {result.error_message}"
                                ent["Confidence"] = 0.0
                                ent["Citation"] = "Transport timeout - requires retry"
                                low_confidence_entities.append(ent)
                                tc.upsert_entity(entity=ent, mode=TableUpdateMode.MERGE)
                            continue
                        
                        # Handle failure
                        if result.status == "failed":
                            logger.error(f"Final failure for bulk processing category '{category}': {result.error_message}")
                            run_id = result.run.id if result.run else "unknown"
                            add_span_attributes(get_current_span(), {
                                "run_status": "failed",
                                "category": category,
                                "batch_size": len(batch),
                                "run_id": run_id,
                                "failure_reason": result.error_message[:200] if result.error_message else "Unknown"
                            })
                            for ent in batch:
                                ent["Response"] = f"Agent run failed: {result.error_message}"
                                ent["Confidence"] = 0.0
                                ent["Citation"] = f"Agent processing failed - Run ID: {run_id}"
                                low_confidence_entities.append(ent)
                                tc.upsert_entity(entity=ent, mode=TableUpdateMode.MERGE)
                            continue
                        
                        # Handle success
                        if result.status == "success":
                            text = result.response_text or ""
                            logger.debug(f"Agent {agent_id} response length: {len(text)} characters")
                            
                            # Parse structured JSON response
                            try:
                                # Extract JSON from response if wrapped in markdown or other text
                                parsed_data = extract_json_from_text(text)
                                
                                if not parsed_data:
                                    raise ValueError("No JSON found in response")
                                
                                answers = parsed_data.get("answers", [])
                                logger.debug(f"Parsed {len(answers)} answers from agent response")
                                
                                # Create a mapping of row_key to entity for quick lookup
                                entity_map = {ent.get("RowKey", ""): ent for ent in batch}
                                entity_by_index = {idx + 1: ent for idx, ent in enumerate(batch)}
                                
                                # Update entities with answers using row_key mapping
                                for answer in answers:
                                    row_key = answer.get("row_key", "")
                                    answer_id = answer.get("id", 0)
                                    
                                    ent = entity_map.get(row_key) or entity_by_index.get(answer_id)
                                    
                                    if ent:
                                        response_text = answer.get("response", "")
                                        confidence = float(answer.get("confidence", 0.0))
                                        citation = answer.get("citation", "")
                                        
                                        # Clean citation
                                        citation = re.sub(r'【[^】]*】', '', citation).strip()
                                        
                                        # Apply "no information" pattern detection
                                        adjusted = _apply_no_info_confidence_adjustment(response_text)
                                        if adjusted is not None:
                                            confidence = adjusted
                                        
                                        ent["Response"] = response_text
                                        ent["Confidence"] = confidence
                                        ent["Citation"] = citation
                                        
                                        if confidence <= 0.3 or not response_text.strip():
                                            low_confidence_entities.append(ent)
                                        
                                        tc.upsert_entity(entity=ent, mode=TableUpdateMode.MERGE)
                                        total_answered += 1
                                    else:
                                        logger.warning(f"Could not find entity for row_key='{row_key}' or id={answer_id}. Skipping answer.")
                                        
                            except (json.JSONDecodeError, ValueError) as parse_ex:
                                logger.debug(f"Failed to parse bulk JSON response: {parse_ex}")
                                for ent in batch:
                                    ent["Response"] = f"Bulk processing failed - needs retry: {str(parse_ex)}"
                                    ent["Confidence"] = 0.0
                                    ent["Citation"] = "Processing error - will retry individually"
                                    low_confidence_entities.append(ent)
                                    tc.upsert_entity(entity=ent, mode=TableUpdateMode.MERGE)
                        
                        else:
                            # Handle timeout or other statuses
                            logger.debug(f"Run ended with status: {result.status}")
                            for ent in batch:
                                ent["Response"] = f"Run ended with status: {result.status}"
                                ent["Confidence"] = 0.0
                                ent["Citation"] = "Processing error - will retry individually"
                                low_confidence_entities.append(ent)
                                tc.upsert_entity(entity=ent, mode=TableUpdateMode.MERGE)
                
                # Process low confidence answers individually with guidance
                logger.debug(f"Retrying {len(low_confidence_entities)} low confidence questions individually")
                retried_count = await self._retry_low_confidence_questions(
                    client_obj, agent_id, thread_id, tc, low_confidence_entities, TableUpdateMode, partition_key
                )
                
                # Record batch operation metrics
                add_span_attributes(get_current_span(), {
                    "total_answered": total_answered,
                    "total_questions": total_questions,
                    "categories_processed": len(categories),
                    "low_confidence_retried": retried_count,
                    "success_rate": total_answered / total_questions if total_questions > 0 else 0
                })
                record_batch_operation(
                    span=get_current_span(),
                    operation_name="process_appdetails_bulk",
                    batch_size=total_questions,
                    processed_count=total_answered,
                    failed_count=len(low_confidence_entities),
                    success_rate=total_answered / total_questions if total_questions > 0 else 0
                )
                
                return {
                    "result": "ok",
                    "answered": total_answered,
                    "total_questions": total_questions,
                    "categories_processed": len(categories),
                    "low_confidence_retried": retried_count,
                    "category_breakdown": {cat: len(entities) for cat, entities in categories.items()}
                }
                
            except Exception as ex:
                logger.error(f"Error in bulk processing: {ex}")
                return {"result": "error", "message": str(ex)}
            
            finally:
                # No longer need to restore instructions since we use predefined sections
                logger.info(f"Bulk processing completed for agent {agent_id}")
                
                # Note: Shared thread is NOT deleted here - it persists for context across operations
                # Thread cleanup happens in cleanup_responder_agent when analysis is complete
            
        except Exception as ex:
            return {"result": "error", "message": str(ex)}

    @trace_async_function("retry_low_confidence_questions")
    async def _retry_low_confidence_questions(self, client_obj, agent_id: str, thread_id: str, tc, 
                                            low_confidence_entities: list, TableUpdateMode, partition_key: str) -> int:
        """Retry low confidence questions individually. Agent instructions already updated for bulk processing."""
        add_span_attributes(get_current_span(), {
            "agent_id": agent_id,
            "thread_id": thread_id,
            "partition_key": partition_key,
            "questions_to_retry": len(low_confidence_entities),
            "operation": "retry_low_confidence"
        })
        retried_count = 0
        
        # Agent now uses predefined RETRY_PROCESSING_MODE section in instructions
        logger.debug(f"Using predefined RETRY_PROCESSING_MODE section for agent {agent_id}")
        
        for ent in low_confidence_entities:
            try:
                question = ent.get("Question", "")
                guidance = ent.get("Guidance", "")
                
                if not question:
                    continue
                
                # Simplified prompt
                retry_prompt = f"""Question: {question}"""
                if guidance:
                    retry_prompt += f"\n\nGuidance: {guidance}"
                
                logger.debug(f"Sending retry prompt to agent {agent_id} for question: {question[:100]}...")
                
                # Use execute_run_with_retry for run processing
                result = await execute_run_with_retry(
                    client=client_obj,
                    agent_id=agent_id,
                    thread_id=thread_id,
                    prompt=retry_prompt,
                    context_description="Low confidence question retry",
                    max_wait=60,
                    max_retries=3,
                    parse_json=True,
                    track_token_usage=True
                )
                
                # Update entity using helper function
                success = _update_entity_from_response(ent, result, default_confidence=0.2)
                tc.upsert_entity(entity=ent, mode=TableUpdateMode.MERGE)
                
                if success:
                    retried_count += 1
                
            except Exception as retry_ex:
                logger.error(f"Failed to retry question: {retry_ex}")
                record_error_details(
                    span=get_current_span(),
                    error_type=type(retry_ex).__name__,
                    error_message=str(retry_ex),
                    error_code=None,
                    is_retryable=True
                )
                continue
        
        add_span_attributes(get_current_span(), {
            "questions_retried": retried_count,
            "success_rate": retried_count / len(low_confidence_entities) if low_confidence_entities else 0
        })
        record_batch_operation(
            span=get_current_span(),
            operation_name="retry_low_confidence_questions",
            batch_size=len(low_confidence_entities),
            processed_count=retried_count,
            failed_count=len(low_confidence_entities) - retried_count,
            success_rate=retried_count / len(low_confidence_entities) if low_confidence_entities else 0
        )
        return retried_count

    @trace_async_function("process_questions_for_table")
    async def _process_questions_for_table(self, client_obj, agent_id: str, table_name: str, partition_key: str,
                                          storage_account_name: str = None,
                                          is_retry_run: bool = False, low_confidence_entities: List = None) -> dict:
        """Process all questions for a specific table using the Responder agent.
       
        Args:
            client_obj: AI client object
            agent_id: Agent ID for processing
            table_name: Name of the table to process
            partition_key: Partition key (application_id)
            is_retry_run: If True, only process low confidence entities
            low_confidence_entities: List of entities with low confidence scores to retry
        """
        add_span_attributes(get_current_span(), {
            "agent_id": agent_id,
            "table_name": table_name,
            "partition_key": partition_key,
            "operation": "process_questions_for_table",
            "is_retry_run": is_retry_run,
            "low_confidence_count": len(low_confidence_entities) if low_confidence_entities else 0
        })
        try:
            from azure.data.tables import TableServiceClient, UpdateMode as TableUpdateMode
            from azure.identity import DefaultAzureCredential as SyncDefaultAzureCredential

            logger.info(f"Processing questions for table {table_name}")
            tables_url = os.getenv("AZURE_TABLES_ACCOUNT_URL")
            if not tables_url:
                return {"result": "error", "message": "No table storage connection"}
            tsc = get_table_service_client(tables_url)
            tc = tsc.get_table_client(table_name=table_name)

            # --- NEW: Get unique metadata and apply category logic if storage_account_name is provided ---
            tools_override = None
            tool_resources_override = None
            filter_expression = None
            has_completeuaq = False
            has_oracledb = False
            container_name = partition_key  # Assuming container name is application_id
            if storage_account_name:
                try:                 
                    metadata_set = get_unique_blob_metadata(container_name, storage_account_name)
                    categories_found = [v for k, v in metadata_set if k == 'category']
                    logger.info(f"Categories found in blob metadata: {categories_found}")
                    has_completeuaq = 'completeuaq' in categories_found
                    has_oracledb = 'oracledb' in categories_found
                    has_sqldb = 'sqldb' in categories_found
                    
                except Exception as meta_ex:
                    logger.warning(f"Metadata/tool override logic failed: {meta_ex}")

            # Handle retry run - only process low confidence entities
            if is_retry_run and low_confidence_entities:
                logger.info(f"Retry run: Processing {len(low_confidence_entities)} low confidence questions only")
                entities = low_confidence_entities
            else:
                # Get all entities with questions (normal first run)
                escaped_pk = str(partition_key).replace("'", "''")
                server_filter = f"PartitionKey eq '{escaped_pk}'"
                entities = list(tc.query_entities(query_filter=server_filter))

            # Use shared thread for this agent to preserve context across operations
            thread_id = await self._get_shared_thread(partition_key, agent_id, client_obj, "process_questions_for_table")
            prompt_suffix = ""
            if table_name.startswith("Oracle"):
                prompt_suffix = "\n\nNote: This question is related to Oracle database. Ensure to provide only Oracle-specific insights and recommendations."
                if has_completeuaq:
                        metadata_criteria = {"category": "completeuaq"}
                        logger.info("Using 'completeuaq' as metadata_criteria for table processing.")
                elif has_oracledb:
                    metadata_criteria = {"category": "oracledb"}
                    logger.info("Using 'oracledb' as metadata_criteria for table processing.")
                else:
                    metadata_criteria = None
                    logger.info(f"No 'completeuaq' or 'oracledb' found in blob metadata for container {container_name}")
                if metadata_criteria:
                    filter_expression = build_metadata_filter_expression(partition_key, metadata_criteria)
                    logger.info(f"Using direct metadata filtering with criteria: {metadata_criteria}")
                    get_current_span().add_event("direct_metadata_filter_applied", {
                        "criteria": str(metadata_criteria),
                        "filter_expression": filter_expression
                    })
                    filtered_tool_result = await create_filtered_search_tool(
                        client=client_obj,
                        partition_key=partition_key,
                        filter_expression=filter_expression,
                        top_k=50
                    )
                    tools_override = filtered_tool_result.definitions
                    tool_resources_override = filtered_tool_result.resources
            elif table_name.startswith("MsSql"):
                prompt_suffix = "\n\nNote: This question is related to Microsoft SQL Server database. Ensure to provide only SQL Server-specific insights and recommendations."
                if has_completeuaq:
                        metadata_criteria = {"category": "completeuaq"}
                        logger.info("Using 'completeuaq' as metadata_criteria for table processing.")
                elif has_sqldb:
                    metadata_criteria = {"category": "sqldb"}
                    logger.info("Using 'sqldb' as metadata_criteria for table processing.")
                else:
                    metadata_criteria = None
                    logger.info(f"No 'completeuaq' or 'sqldb' found in blob metadata for container {container_name}")

                if metadata_criteria:
                    filter_expression = build_metadata_filter_expression(partition_key, metadata_criteria)
                    logger.info(f"Using direct metadata filtering with criteria: {metadata_criteria}")
                    get_current_span().add_event("direct_metadata_filter_applied", {
                        "criteria": str(metadata_criteria),
                        "filter_expression": filter_expression
                    })
                    filtered_tool_result = await create_filtered_search_tool(
                        client=client_obj,
                        partition_key=partition_key,
                        filter_expression=filter_expression,
                        top_k=50
                    )
                    tools_override = filtered_tool_result.definitions
                    tool_resources_override = filtered_tool_result.resources
            try:
                answered = 0

                for ent in entities:
                    question = ent.get("Question")
                    if not question:
                        continue
                    question_with_context = question + prompt_suffix
                    
                    try:
                        logger.debug(f"Sending individual question to agent {agent_id}: {question_with_context[:100]}...")
                        # Use execute_run_with_retry utility for run processing
                        result = await execute_run_with_retry(
                            client=client_obj,
                            agent_id=agent_id,
                            thread_id=thread_id,
                            prompt=question_with_context,
                            context_description=f"Individual question for table: {table_name}",
                            max_wait=60,
                            max_retries=3,
                            parse_json=True,
                            track_token_usage=True,
                            tools=tools_override,
                            tool_resources=tool_resources_override
                        )

                        # Track token usage for telemetry
                        if result.token_usage:
                            record_llm_interaction(
                                span=get_current_span(),
                                model=os.getenv("AZURE_AI_AGENT_DEPLOYMENT_NAME"),
                                prompt_tokens=result.token_usage.get("prompt_tokens", 0),
                                completion_tokens=result.token_usage.get("completion_tokens", 0),
                                total_tokens=result.token_usage.get("total_tokens", 0),
                                temperature=0.7
                            )

                        # Update entity using helper function
                        success = _update_entity_from_response(ent, result, default_confidence=0.3)
                        tc.upsert_entity(entity=ent, mode=TableUpdateMode.MERGE)

                        if success:
                            answered += 1

                    except Exception as question_ex:
                        # Mark as failed
                        ent["Response"] = f"Error processing: {str(question_ex)}"
                        ent["Confidence"] = 0.0
                        ent["Citation"] = "Error"
                        tc.upsert_entity(entity=ent, mode=TableUpdateMode.MERGE)
                        record_error_details(
                            span=get_current_span(),
                            error_type=type(question_ex).__name__,
                            error_message=str(question_ex),
                            error_code=None,
                            is_retryable=True
                        )
                        continue

                total_questions = len([e for e in entities if e.get("Question")])

                # Record batch operation metrics
                add_span_attributes(get_current_span(), {
                    "total_answered": answered,
                    "total_questions": total_questions,
                    "success_rate": answered / total_questions if total_questions > 0 else 0
                })
                record_batch_operation(
                    span=get_current_span(),
                    operation_name="process_questions_for_table",
                    batch_size=total_questions,
                    processed_count=answered,
                    failed_count=total_questions - answered,
                    success_rate=answered / total_questions if total_questions > 0 else 0
                )

                return {
                    "result": "ok",
                    "answered": answered,
                    "total_questions": total_questions
                }

            except Exception as ex:
                add_span_attributes(get_current_span(), {
                    "success": False,
                    "error": str(ex)
                })
                record_error_details(
                    span=get_current_span(),
                    error_type=type(ex).__name__,
                    error_message=str(ex),
                    error_code=None,
                    is_retryable=True
                )
                return {"result": "error", "message": str(ex)}

            finally:
                # Note: Shared thread is NOT deleted here - it persists for context across operations
                # Thread cleanup happens in cleanup_responder_agent when analysis is complete
                pass

        except Exception as ex:
            return {"result": "error", "message": str(ex)}
        
    @kernel_function(description="Populate dependency table with data from search index.")
    async def populate_dependency_table(self, application_id: str, agent_id: str, storage_account_name: str) -> str:
        """Extract dependency information from search index and populate the table."""
        logger.info(f"[PLUGIN] populate_dependency_table called with application_id={application_id}, agent_id={agent_id}")
        tracer = get_tracer()
        with tracer.start_as_current_span("populate_dependency_table") as span:
            add_span_attributes(span, {
                "application_id": application_id,
                "agent_id": agent_id,
                "operation": "populate_dependencies"
            })
            
            try:
                from azure.data.tables import TableServiceClient, UpdateMode as TableUpdateMode
                from azure.identity import DefaultAzureCredential as SyncDefaultAzureCredential
                
                dep_table = sanitize_table_name(f"IntegrationDependency{application_id}")
                
                add_span_attributes(span, {
                    "target_table": dep_table
                })
                
                # Get table client with proper connection string handling
                tables_url = os.getenv("AZURE_TABLES_ACCOUNT_URL")
                if not tables_url:
                    span.set_status(Status(StatusCode.ERROR, "Missing table storage config"))
                    return json.dumps({"result": "error", "message": "Missing table storage config - need AZURE_TABLES_ACCOUNT_URL"})
                cred = SyncDefaultAzureCredential(exclude_shared_token_cache_credential=True)
                tsc = TableServiceClient(endpoint=tables_url, credential=cred)
                span.add_event("using_azure_credential", {"tables_url": tables_url})
                
                tc = tsc.get_table_client(table_name=dep_table)
                
                # Process each unique server for dependencies
                endpoint = os.environ.get("AZURE_EXISTING_AIPROJECT_ENDPOINT")
                async with DefaultAzureCredential(exclude_shared_token_cache_credential=True) as creds:
                    async with AIProjectClient(credential=creds, endpoint=endpoint) as ai_client:
                        
                        record_agent_interaction(span, agent_id, operation_type="populate_dependency_table")
                        
                        # Use shared thread for this agent to preserve context
                        thread_id = await self._get_shared_thread(application_id, agent_id, ai_client, "populate_dependency_table")
                        
                        try:
                            container_name = application_id  # Assuming container name is application_id
                            metadata_set = get_unique_blob_metadata(container_name, storage_account_name)
                            categories = [v for k, v in metadata_set if k == 'category']
                            logger.info(f"Categories found in blob metadata: {categories}")
                            has_completeuqa = 'completeuqa' in categories
                            has_network = 'network' in categories
                            if has_completeuqa:
                                network_metadata_criteria = {"category": "completeuaq"}
                                logger.info("Using 'completeuqa' as network_metadata_criteria.")
                            elif has_network:
                                network_metadata_criteria = {"category": "network"}
                                logger.info("Using 'network' as network_metadata_criteria.")
                            else:
                                network_metadata_criteria = None
                                logger.info(f"No 'completeuaq' or 'network' found in blob metadata for container {container_name}")
                            unique_servers = await self._get_unique_servers_from_index(
                                agent_id, ai_client, thread_id, application_id,
                                metadata_criteria=network_metadata_criteria
                            )
                            
                            add_span_attributes(span, {
                                "unique_servers_count": len(unique_servers),
                                "servers": ", ".join(unique_servers[:5]) + ("..." if len(unique_servers) > 5 else "")
                            })
                            
                            if not unique_servers:
                                span.add_event("no_servers_found")
                                span.set_status(Status(StatusCode.OK))
                                return json.dumps({"result": "warning", "message": "No dependency data found in index"})
                            
                            # Agent now uses predefined DEPENDENCY_EXTRACTION_MODE section in instructions
                            logger.info(f"Using predefined DEPENDENCY_EXTRACTION_MODE section for agent {agent_id}")
                            
                            populated_count = 0
                            for server_name in unique_servers:
                                # Explicit mode header to trigger correct instruction section
                                dep_prompt = f"""[DEPENDENCY_EXTRACTION_MODE]

Use Azure AI Search tool to answer.
Extract dependency information for server: {server_name}

Use the project-index-{application_id}/versions/1 knowledge.
Follow the DEPENDENCY_EXTRACTION_MODE instructions to extract ALL network connections where this server appears as source or destination.
Return the response as a JSON array with the required fields."""
                                
                                # Get dependency information from the agent using the same thread
                                dep_info = await self._query_dependencies_for_server(dep_prompt, server_name, agent_id, ai_client, thread_id, metadata_criteria=network_metadata_criteria)
                                
                                if dep_info and isinstance(dep_info, list):
                                    for idx, dep in enumerate(dep_info):
                                        # Skip if dep is not a dictionary (defensive check)
                                        if not isinstance(dep, dict):
                                            logger.warning(f"Skipping non-dict dependency item for {server_name}: {type(dep).__name__}")
                                            continue
                                            
                                        # Create entity for table
                                        entity = {
                                            "PartitionKey": application_id,
                                            "RowKey": f"{dep.get('SourceHostname', 'unknown')}_{dep.get('DestinationHostname', 'unknown')}_{populated_count}_{idx}",
                                            "SourceHostname": dep.get("SourceHostname", ""),
                                            "SourceIPAddress": dep.get("SourceIPAddress", ""),
                                            "DestinationHostname": dep.get("DestinationHostname", ""),
                                            "DestinationIPAddress": dep.get("DestinationIPAddress", ""),
                                            "InboundOrOutboundProtocol": dep.get("Protocol", ""),
                                            "InboundOrOutboundPortNumber": dep.get("Port", ""),
                                            "Description": dep.get("Description", ""),
                                            "Confidence": float(dep.get("Confidence", 0.0)),
                                            "Citation": dep.get("Citation", "")
                                        }
                                        
                                        tc.upsert_entity(entity=entity, mode=TableUpdateMode.REPLACE)
                                        populated_count += 1
                            
                            # Calculate aggregate confidence score for dependency table 
                            aggregate_confidence = 0.0
                            if populated_count > 0:
                                aggregate_confidence = await self._calculate_dependency_aggregate_score(
                                    tc, dep_table, application_id, span
                                )
                                logger.info(f"Dependency table {dep_table}: Aggregate confidence score = {aggregate_confidence:.2f}")
                            
                            # Store dependency confidence score in class variable
                            if application_id not in self._confidence_scores:
                                self._confidence_scores[application_id] = {}
                            if "table_confidence_scores" not in self._confidence_scores[application_id]:
                                self._confidence_scores[application_id]["table_confidence_scores"] = {}
                            
                            self._confidence_scores[application_id]["table_confidence_scores"]["IntegrationDependency"] = round(aggregate_confidence, 2)
                            logger.info(f"Stored dependency confidence score for application {application_id}")
                            
                            record_table_operation(span, dep_table, "populate", populated_count, application_id)
                            span.add_event("dependencies_populated", {
                                "populated_count": populated_count,
                                "servers_processed": len(unique_servers),
                                "aggregate_confidence_score": aggregate_confidence
                            })
                            span.set_status(Status(StatusCode.OK))
                            
                            return json.dumps({
                                "result": "ok",
                                "table": dep_table,
                                "populated": populated_count,
                                "servers_processed": len(unique_servers),
                                "aggregate_confidence_score": round(aggregate_confidence, 2)
                            })
                            
                        finally:
                            # No longer need to restore instructions since we use predefined sections
                            logger.info(f"Dependency extraction completed for agent {agent_id}")
                            
                            # Note: Shared thread is NOT deleted here - it persists for context across operations
                            # Thread cleanup happens in cleanup_responder_agent when analysis is complete
                
            except Exception as ex:
                span.record_exception(ex)
                span.set_status(Status(StatusCode.ERROR, str(ex)))
                return json.dumps({"result": "error", "message": str(ex)})

    @kernel_function(description="Populate infrastructure table with server details from search index.")
    async def populate_infrastructure_table(self, application_id: str, agent_id: str, storage_account_name: str) -> str:
        """Extract infrastructure information from search index and populate the table."""
        logger.info(f"[PLUGIN] populate_infrastructure_table called with application_id={application_id}, agent_id={agent_id}")
        tracer = get_tracer()
        with tracer.start_as_current_span("populate_infrastructure_table") as span:
            add_span_attributes(span, {
                "application_id": application_id,
                "agent_id": agent_id,
                "operation": "populate_infrastructure"
            })
            
            try:
                from azure.data.tables import TableServiceClient, UpdateMode as TableUpdateMode
                from azure.identity import DefaultAzureCredential as SyncDefaultAzureCredential
                
                infra_table = sanitize_table_name(f"InfrastructureDetails{application_id}")
                
                add_span_attributes(span, {
                    "target_table": infra_table
                })
                
                # Get table client with proper connection string handling
                tables_url = os.getenv("AZURE_TABLES_ACCOUNT_URL")
                if not tables_url:
                    span.set_status(Status(StatusCode.ERROR, "Missing table storage config"))
                    return json.dumps({"result": "error", "message": "Missing table storage config - need AZURE_TABLES_ACCOUNT_URL"})
                tsc = get_table_service_client(tables_url)
                span.add_event("table_service_client_created", {"tables_url": tables_url})
                
                tc = tsc.get_table_client(table_name=infra_table)
                
                # Process each unique server for infrastructure details
                endpoint = os.environ.get("AZURE_EXISTING_AIPROJECT_ENDPOINT")
                async with DefaultAzureCredential(exclude_shared_token_cache_credential=True) as creds:
                    async with AIProjectClient(credential=creds, endpoint=endpoint) as ai_client:
                        
                        record_agent_interaction(span, agent_id, operation_type="populate_infrastructure_table")
                        
                        # Use shared thread for this agent to preserve context
                        thread_id = await self._get_shared_thread(application_id, agent_id, ai_client, "populate_infrastructure_table")
                        
                        try:
                            # Get unique servers - should reuse cached results if using shared thread
                            # Apply metadata criteria to search only infrastructure-related documents
                            # with document_state=final (for finalized infrastructure data)
                            # Define metadata criteria for infrastructure documents
                            # Check blob metadata for 'category: infra' before proceeding
                            # Use storage_account_name from API argument
                            container_name = application_id  # Assuming container name is application_id
                            metadata_set = get_unique_blob_metadata(container_name, storage_account_name)
                            categories = [v for k, v in metadata_set if k == 'category']
                            logger.info(f"Categories found in blob metadata: {categories}")
                            has_completeuqa = 'completeuqa' in categories
                            has_infra = 'infra' in categories
                            if has_completeuqa:
                                infra_metadata_criteria = {"category": "completeuaq"}
                                logger.info("Using 'completeuqa' as infra_metadata_criteria.")
                            elif has_infra:
                                infra_metadata_criteria = {"category": "infra"}
                                logger.info("Using 'infra' as infra_metadata_criteria.")
                            else:
                                infra_metadata_criteria = None
                                logger.info(f"No 'completeuqa' or 'infra' found in blob metadata for container {container_name}")
                            unique_servers = await self._get_unique_servers_from_index(
                                agent_id, ai_client, thread_id, application_id,
                                metadata_criteria=infra_metadata_criteria
                            )
                            
                            add_span_attributes(span, {
                                "unique_servers_count": len(unique_servers),
                                "servers": ", ".join(unique_servers[:5]) + ("..." if len(unique_servers) > 5 else "")
                            })
                            
                            if not unique_servers:
                                span.add_event("no_servers_found")
                                span.set_status(Status(StatusCode.OK))
                                return json.dumps({"result": "warning", "message": "No server data found in index"})
                            
                            # Agent now uses predefined INFRASTRUCTURE_EXTRACTION_MODE section in instructions
                            logger.info(f"Using predefined INFRASTRUCTURE_EXTRACTION_MODE section for agent {agent_id}")
                            
                            populated_count = 0
                            for server_name in unique_servers:
                                # Explicit mode header to trigger correct instruction section
                                infra_prompt = f"""[INFRASTRUCTURE_EXTRACTION_MODE]

Extract infrastructure information for server: {server_name}

Use the project-index-{application_id}/versions/1 knowledge.
Follow the INFRASTRUCTURE_EXTRACTION_MODE instructions to extract ALL infrastructure details for this server.
Return the response as a JSON object with the required fields."""
                                
                                                               # Get infrastructure information from the agent using the same thread
                                # Pass metadata criteria to filter to infrastructure documents only
                                infra_info = await self._query_infrastructure_for_server(
                                    prompt=infra_prompt,
                                    server_name=server_name,
                                    agent_id=agent_id,
                                    ai_client=ai_client,
                                    thread_id=thread_id,
                                    partition_key=application_id,
                                    metadata_criteria=infra_metadata_criteria
                                )
                               
                                if infra_info and isinstance(infra_info, dict):
                                    # Create entity for table
                                    entity = {
                                        "PartitionKey": application_id,
                                        "RowKey": f"{server_name}_{populated_count}",
                                        "ApplicationName": application_id,
                                        "VMHostname": infra_info.get("VMHostname", server_name),
                                        "Domain": infra_info.get("Domain", ""),
                                        "IPAddress": infra_info.get("IPAddress", ""),
                                        "ServerFunction": infra_info.get("ServerFunction", ""),
                                        "OnpremSecurityZone": infra_info.get("OnpremSecurityZone", ""),
                                        "OperatingSystem": infra_info.get("OperatingSystem", ""),
                                        "vCPU": infra_info.get("vCPU", ""),
                                        "RAM": infra_info.get("RAM", ""),
                                        "DisksAndSize": infra_info.get("DisksAndSize", ""),
                                        "LunId": infra_info.get("LunId", ""),
                                        "ServerEnvironment": infra_info.get("ServerEnvironment", ""),
                                        "GeneralNotes": infra_info.get("GeneralNotes", ""),
                                        "Confidence": float(infra_info.get("Confidence", 0.0)),
                                        "Citation": infra_info.get("Citation", "")
                                    }
                                   
                                    tc.upsert_entity(entity=entity, mode=TableUpdateMode.REPLACE)
                                    populated_count += 1
                           
                            #Calculate aggregate confidence score for infrastructure table
                            aggregate_confidence = 0.0
                            if populated_count > 0:
                                aggregate_confidence = await self._calculate_infrastructure_aggregate_score(
                                    tc, infra_table, application_id, span
                                )
                                logger.info(f"Infrastructure table {infra_table}: Aggregate confidence score = {aggregate_confidence:.2f}")
                           
                            # Store infrastructure confidence score in class variable
                            if application_id not in self._confidence_scores:
                                self._confidence_scores[application_id] = {}
                            if "table_confidence_scores" not in self._confidence_scores[application_id]:
                                self._confidence_scores[application_id]["table_confidence_scores"] = {}
                           
                            self._confidence_scores[application_id]["table_confidence_scores"]["InfrastructureDetails"] = round(aggregate_confidence, 2)
                           
                            # Recalculate overall average if all tables have been processed
                            if "table_confidence_scores" in self._confidence_scores[application_id]:
                                table_scores = self._confidence_scores[application_id]["table_confidence_scores"]
                                if len(table_scores) > 0:
                                    overall_avg = sum(table_scores.values()) / len(table_scores)
                                    self._confidence_scores[application_id]["overall_average_confidence_score"] = round(overall_avg, 2)
                                    logger.info(f"Recalculated overall average confidence score for application {application_id}: {overall_avg:.2f} (across {len(table_scores)} tables)")
                           
                            logger.info(f"Stored infrastructure confidence score for application {application_id}")
                           
                            record_table_operation(span, infra_table, "populate", populated_count, application_id)
                            span.add_event("infrastructure_populated", {
                                "populated_count": populated_count,
                                "servers_processed": len(unique_servers),
                                "aggregate_confidence_score": aggregate_confidence
                            })
                            span.set_status(Status(StatusCode.OK))
                           
                            return json.dumps({
                                "result": "ok",
                                "table": infra_table,
                                "populated": populated_count,
                                "servers_processed": len(unique_servers),
                                "aggregate_confidence_score": round(aggregate_confidence, 2)
                            })
                           
                        finally:
                            # No longer need to restore instructions since we use predefined sections
                            logger.info(f"Infrastructure extraction completed for agent {agent_id}")
                           
                            # Note: Shared thread is NOT deleted here - it persists for context across operations
                            # Thread cleanup happens in cleanup_responder_agent when analysis is complete
               
            except Exception as ex:
                span.record_exception(ex)
                span.set_status(Status(StatusCode.ERROR, str(ex)))
                return json.dumps({"result": "error", "message": str(ex)})
            
    # Helper methods for dependency and infrastructure processing
    
    @trace_async_function("get_unique_servers_from_index")
    async def _get_unique_servers_from_index(
        self,
        agent_id: str,
        ai_client,
        thread_id: str,
        partition_key: str,
        metadata_filter: Optional[str] = None,
        metadata_criteria: Optional[Dict[str, str]] = None
    ) -> List[str]:
        """Query the agent to get unique servers from the dependency data using provided thread.
       
        This method supports two filtering modes:
        1. Simple metadata_filter: Uses search.ismatch to find text in metadata field
        2. metadata_criteria: Directly filters by metadata JSON key-value pairs using search.ismatch
                              Example: {"category": "infra", "document_state": "final"}
                              Builds filter: search.ismatch('"category":"infra"', 'metadata')
       
        Args:
            agent_id: The responder agent ID
            ai_client: AI Project client
            thread_id: Thread ID for the conversation
            partition_key: Application/partition key for the search index
            metadata_filter: Optional simple text filter to match in metadata (e.g., 'dependency', 'infrastructure')
            metadata_criteria: Optional dictionary of metadata key-value pairs to match documents
                              Example: {"category": "infra", "document_state": "final"}
                              Uses direct metadata filtering (more efficient than path-based filtering)
       
        Returns:
            List of unique server names found in the indexed documents
        """
        add_span_attributes(get_current_span(), {
            "agent_id": agent_id,
            "thread_id": thread_id,
            "partition_key": partition_key,
            "operation": "get_unique_servers",
            "has_metadata_filter": metadata_filter is not None,
            "has_metadata_criteria": metadata_criteria is not None
        })
        try:
            if not agent_id:
                logger.debug("No agent available to query for servers")
                return []
           
            # Create custom-filtered search tool based on filtering mode
            # This overrides the agent's default search tool filter for this specific run
            # IMPORTANT: Both tools AND tool_resources must be provided for filter to work
            tools_override = None
            tool_resources_override = None
            filter_expression = f"appId eq '{partition_key}'"
           
            # Mode 1: Use metadata_criteria for direct metadata JSON filtering (preferred - single query)
            # Uses the build_metadata_filter_expression utility for consistent filter building
            if metadata_criteria:
                from agents.utils.agent_utils import create_filtered_search_tool, build_metadata_filter_expression
               
                # Build filter expression using the shared utility function
                filter_expression = build_metadata_filter_expression(partition_key, metadata_criteria)
               
                logger.info(f"Using direct metadata filtering with criteria: {metadata_criteria}")
                get_current_span().add_event("direct_metadata_filter_applied", {
                    "criteria": str(metadata_criteria),
                    "filter_expression": filter_expression
                })
               
                filtered_tool_result = await create_filtered_search_tool(
                    client=ai_client,
                    partition_key=partition_key,
                    filter_expression=filter_expression,
                    top_k=50  # Increase top_k for comprehensive server discovery
                )
                tools_override = filtered_tool_result.definitions
                tool_resources_override = filtered_tool_result.resources
           
            # Mode 2: Use simple metadata_filter for text matching (fallback)
            elif metadata_filter:
                from agents.utils.agent_utils import create_filtered_search_tool
               
                # Use search.ismatch for partial text matching in metadata field
                filter_expression = f"appId eq '{partition_key}' and search.ismatch('{metadata_filter}', 'metadata')"
               
                filtered_tool_result = await create_filtered_search_tool(
                    client=ai_client,
                    partition_key=partition_key,
                    filter_expression=filter_expression,
                    top_k=50  # Increase top_k for comprehensive server discovery
                )
                tools_override = filtered_tool_result.definitions
                tool_resources_override = filtered_tool_result.resources
           
            if tools_override:
                logger.debug(f"Created custom search tool with filter: {filter_expression}")
                # Build event attributes, avoiding None values (OpenTelemetry doesn't accept NoneType)
                event_attrs = {
                    "filter_expression": filter_expression,
                    "has_tool_resources": tool_resources_override is not None
                }
                if metadata_filter:
                    event_attrs["metadata_filter"] = metadata_filter
                if metadata_criteria:
                    event_attrs["metadata_criteria"] = str(metadata_criteria)
                get_current_span().add_event("custom_filter_applied", event_attrs)
           
            # Build metadata description for agent query
            metadata_desc = ""
            if metadata_criteria:
                criteria_parts = [f"{k}={v}" for k, v in metadata_criteria.items()]
                metadata_desc = f" with metadata [{', '.join(criteria_parts)}]"
           
            # Query to extract server names
            server_query = f"""Analyze all the dependency tables and network connection information in the indexed documents{metadata_desc}.
             
            Use the project-index-{partition_key}/versions/1 knowledge.    
            Always perform a search query with the attached tool, even if you think the result will be empty. Never skip the tool call.
            Extract and list ALL unique server names (hostnames) that appear in the data.  
            Include servers that appear as either source or destination.  
             
            Return ONLY a JSON array of unique server names. Do not include IP addresses.      
             
            Important:  
            - Include ALL unique server names found in the index  
            - Do NOT include IP addresses  
            - Return only the JSON array, no additional text"""
           
            # Log the server query when debug level is enabled
            logger.debug(f"_get_unique_servers_from_index: sending server discovery query to agent {agent_id}:")
            logger.debug(f"Server query content:\n{server_query}")
           
            # Use execute_run_with_retry for message sending, run creation, polling, retry, and response extraction
            # Pass BOTH tools AND tool_resources if metadata filter was provided (required for filter to work)
            result = await execute_run_with_retry(
                client=ai_client,
                agent_id=agent_id,
                thread_id=thread_id,
                prompt=server_query,
                context_description="Get unique servers run",
                max_wait=30,
                max_retries=3,
                parse_json=False,  # We do custom JSON parsing for server list
                track_token_usage=True,
                tools=tools_override,  # Apply custom filter at run level
                tool_resources=tool_resources_override  # Required for project index filter to work
            )
           
            # Record LLM interaction if token usage available
            if result.token_usage:
                record_llm_interaction(
                    span=get_current_span(),
                    model=os.getenv("AZURE_AI_AGENT_DEPLOYMENT_NAME"),
                    prompt_tokens=result.token_usage.get('prompt_tokens', 0),
                    completion_tokens=result.token_usage.get('completion_tokens', 0),
                    total_tokens=result.token_usage.get('total_tokens', 0),
                    temperature=0.7  # Default temperature
                )
           
            # Handle failure cases
            if result.status in ["failed", "error", "transport_error"]:
                logger.error(f"Server discovery failed: {result.error_message}")
                get_current_span().record_exception(Exception(result.error_message))
                get_current_span().set_status(Status(StatusCode.ERROR, result.error_message))
                get_current_span().add_event("dependency_discovery_failed", {
                    "run_id": result.run.id if result.run else "unknown",
                    "failure_reason": result.error_message
                })
                return []
           
            if result.status == "timeout":
                logger.warning("Server discovery run timed out")
                get_current_span().add_event("server_discovery_incomplete", {
                    "run_status": "timeout",
                    "reason": "Run did not complete within time limit"
                })
                return []
           
            # Process successful response
            if result.status == "success" and result.response_text:
                content = result.response_text
                logger.debug(f"Complete agent response for server discovery:\n{content}")
               
                # Try to parse as JSON array
                try:
                    # Extract JSON array from the response
                    if "[" in content and "]" in content:
                        # Find the JSON array part
                        start_idx = content.find("[")
                        end_idx = content.rfind("]") + 1
                        json_part = content[start_idx:end_idx]
                        servers = json.loads(json_part)
                       
                        if isinstance(servers, list):
                            # Filter out any IP addresses that might have been included
                            filtered_servers = [                          
                                s.strip() for s in servers
                                if isinstance(s, str) and not re.match(r'^\d+\.\d+\.\d+\.\d+$', s)
                            ]
 
                            logger.debug(f"Found {len(filtered_servers)} unique servers from agent: {filtered_servers}")
                            add_span_attributes(get_current_span(), {
                                "servers_found": len(filtered_servers),
                                "success": True
                            })
                            record_search_operation(
                                span=get_current_span(),
                                index_name=f"project-index-{partition_key}",
                                query=server_query[:100],
                                result_count=len(filtered_servers)
                            )
                            return filtered_servers
                except Exception as parse_ex:
                    logger.error(f"Failed to parse server list from agent response: {parse_ex}")
                    logger.error(f"Response content: {content[:500]}")
                    record_error_details(
                        span=get_current_span(),
                        error_type=type(parse_ex).__name__,
                        error_message=str(parse_ex),
                        error_code=None,
                        is_retryable=False
                    )
           
            logger.error("Could not extract server list from agent")
            add_span_attributes(get_current_span(), {
                "servers_found": 0,
                "success": False
            })
            return []
           
        except Exception as ex:
            logger.error(f"Failed to get unique servers from agent: {ex}")
            add_span_attributes(get_current_span(), {
                "success": False,
                "error": str(ex)
            })
            record_error_details(
                span=get_current_span(),
                error_type=type(ex).__name__,
                error_message=str(ex),
                error_code=None,
                is_retryable=True
            )
            return []
 

    @trace_async_function("query_dependencies_for_server")
    async def _query_dependencies_for_server(
        self, 
        prompt: str, 
        server_name: str, 
        agent_id: str, 
        ai_client, 
        thread_id: str,
        partition_key: str = None,
        metadata_criteria: Optional[Dict[str, str]] = None
    ) -> List[Dict]:
        """Query the agent for dependency information about a specific server using provided thread.
        Note: Agent instructions already contain dependency extraction rules (appended before calling this method).
        Args:
            prompt: The query prompt for dependency extraction
            server_name: Name of the server to query
            agent_id: The responder agent ID
            ai_client: AI Project client
            thread_id: Thread ID for the conversation
            partition_key: Application/partition key for the search index (required if metadata_criteria provided)
            metadata_criteria: Optional dictionary of metadata key-value pairs to filter documents
                              Example: {"category": "network", "document_state": "final"}
        Returns:
            List of dictionaries containing dependency information for the server
        """
        from agents.utils.agent_utils import execute_run_with_retry, create_filtered_search_tool, build_metadata_filter_expression
        add_span_attributes(get_current_span(), {
            "server_name": server_name,
            "agent_id": agent_id,
            "thread_id": thread_id,
            "operation": "query_dependencies",
            "has_metadata_criteria": metadata_criteria is not None
        })
        try:
            if not agent_id:
                return []
            # Build filtered search tool if metadata criteria provided
            tools_override = None
            tool_resources_override = None
            if metadata_criteria and partition_key:
                # Build direct metadata filter expression
                filter_expression = build_metadata_filter_expression(partition_key, metadata_criteria)
                logger.debug(f"Using metadata filter for dependency query: {filter_expression}")
                get_current_span().add_event("metadata_filter_applied", {
                    "filter_expression": filter_expression,
                    "metadata_criteria": str(metadata_criteria)
                })
                filtered_tool_result = await create_filtered_search_tool(
                    client=ai_client,
                    partition_key=partition_key,
                    filter_expression=filter_expression,
                    top_k=30
                )
                tools_override = filtered_tool_result.definitions
                tool_resources_override = filtered_tool_result.resources
            logger.debug(f"Sending dependency prompt to agent {agent_id} for server '{server_name}': {prompt}")
            # Use the centralized run execution utility with retry
            result = await execute_run_with_retry(
                client=ai_client,
                agent_id=agent_id,
                thread_id=thread_id,
                prompt=prompt,
                context_description=f"Dependency query for server {server_name}",
                max_wait=60,
                max_retries=3,
                parse_json=True,
                track_token_usage=True,
                tools=tools_override,
                tool_resources=tool_resources_override
            )
            # Track token usage if available
            if result.token_usage:
                record_llm_interaction(
                    span=get_current_span(),
                    model=os.getenv("AZURE_AI_AGENT_DEPLOYMENT_NAME"),
                    prompt_tokens=result.token_usage.get('prompt_tokens', 0),
                    completion_tokens=result.token_usage.get('completion_tokens', 0),
                    total_tokens=result.token_usage.get('total_tokens', 0),
                    temperature=0.7
                )
            if result.status == "success":
                parsed = result.parsed_json
                if isinstance(parsed, list):
                    add_span_attributes(get_current_span(), {
                        "dependencies_found": len(parsed),
                        "success": True
                    })
                    record_search_operation(
                        span=get_current_span(),
                        index_name="dependency_index",
                        query=f"dependencies for {server_name}",
                        result_count=len(parsed)
                    )
                    return parsed
                if isinstance(parsed, dict) and "Response" in parsed:
                    response_text = parsed.get("Response", "")
                    try:
                        nested_parsed = json.loads(response_text)
                        add_span_attributes(get_current_span(), {
                            "dependencies_found": len(nested_parsed) if isinstance(nested_parsed, list) else 0,
                            "success": True,
                            "parse_method": "nested_json"
                        })
                        return nested_parsed
                    except Exception:
                        text_parsed = self._parse_dependency_text(response_text, server_name)
                        add_span_attributes(get_current_span(), {
                            "dependencies_found": len(text_parsed),
                            "success": True,
                            "parse_method": "text_parsing"
                        })
                        return text_parsed
                # Fallback: try parsing from raw response text
                if result.response_text:
                    text_parsed = self._parse_dependency_text(result.response_text, server_name)
                    add_span_attributes(get_current_span(), {
                        "dependencies_found": len(text_parsed),
                        "success": True,
                        "parse_method": "text_parsing_fallback"
                    })
                    return text_parsed
                return []
            elif result.status == "failed":
                logger.error(f"Final failure for dependency query for server {server_name}: {result.error_message}")
                return []
            else:
                # Timeout or other status
                logger.warning(f"Dependency extraction run for {server_name} ended with status: {result.status}")
                add_span_attributes(get_current_span(), {
                    "success": False,
                    "run_status": result.status,
                    "incomplete": True
                })
                return []
        except Exception as ex:
            logger.error(f"Failed to query dependencies for {server_name}: {ex}")
            add_span_attributes(get_current_span(), {
                "success": False,
                "error": str(ex)
            })
            record_error_details(
                span=get_current_span(),
                error_type=type(ex).__name__,
                error_message=str(ex),
                error_code=None,
                is_retryable=True
            )
        return []
    @trace_function("parse_dependency_text")
    def _parse_dependency_text(self, text: str, server_name: str) -> List[Dict]:
        """Parse dependency information from text response."""
        add_span_attributes(get_current_span(), {
            "server_name": server_name,
            "text_length": len(text),
            "operation": "parse_dependencies"
        })
        dependencies: List[Dict] = []
        lines = text.split('\n')
        for line in lines:
            if 'Source' in line and 'Destination' in line:
                continue
            if not line.strip():
                continue
            # Basic parsing pattern
            pattern = r'([^\d\s][^\t]*?)\s+([\d\.]+)\s+([^\d\s][^\t]*?)\s+([\d\.]+)\s+(\d+)/(TCP|UDP|HTTP|HTTPS)\s*(.*)?'
            match = re.match(pattern, line.strip(), re.IGNORECASE)
            if match:
                dependencies.append({
                    "SourceHostname": match.group(1).strip(),
                    "SourceIPAddress": match.group(2).strip(),
                    "DestinationHostname": match.group(3).strip(),
                    "DestinationIPAddress": match.group(4).strip(),
                    "Protocol": match.group(6).upper(),
                    "Port": match.group(5),
                    "Description": match.group(7).strip() if match.group(7) else f"Connection from {match.group(1)} to {match.group(3)}",
                    "Confidence": 0.8,
                    "Citation": "Extracted from dependency table"
                })
        add_span_attributes(get_current_span(), {
            "dependencies_parsed": len(dependencies),
            "lines_processed": len(lines)
        })
        return dependencies

#     def _generate_infrastructure_query(self, server_name: str) -> str:
#         """Generate a specialized query for extracting infrastructure information for a specific server."""
#         return f"""Extract ALL infrastructure details for server '{server_name}' from the indexed documents.

# Find and extract the following information for '{server_name}':
# - VM Hostname (FQDN) - Server name given above or Full qualified domain name
# - Domain - Domain the server belongs to
# - IP Address - IP address(es) assigned
# - Server Function - Web, App, DB, or Others
# - On-prem Security Zone - TP/TA/TD/E1/E2/E3/Etc
# - Operating System - OS type and version
# - vCPU - Number of virtual CPUs
# - RAM - Memory in GB
# - Disks and Size - Storage configuration and sizes in GB
# - LUN ID - Storage LUN identifiers
# - Server Environment - Dev/Test/SIT/UAT/Non-Prod/Prod
# - General Notes - Any additional notes

# Return the results as a JSON object with exactly these fields:
# {{
#     "VMHostname": "server name given above or FQDN",
#     "Domain": "domain name",
#     "IPAddress": "IP address",
#     "ServerFunction": "Web/App/DB/Others",
#     "OnpremSecurityZone": "security zone",
#     "OperatingSystem": "OS details",
#     "vCPU": "number of vCPUs",
#     "RAM": "RAM in GB",
#     "DisksAndSize": "disk configuration",
#     "LunId": "LUN ID",
#     "ServerEnvironment": "Dev/Test/SIT/UAT/Non-Prod/Prod",
#     "GeneralNotes": "any notes",
#     "Confidence": 0.0-1.0,
#     "Citation": "source document reference"
# }}

# IMPORTANT:
# - Extract exact values from documents
# - If a field is not found, use empty string ""
# - Include confidence score based on data completeness
# - Return empty object {{}} if no infrastructure data found for this server"""

    @trace_async_function("query_infrastructure_for_server")
    async def _query_infrastructure_for_server(
        self,
        prompt: str,
        server_name: str,
        agent_id: str,
        ai_client,
        thread_id: str,
        partition_key: str = None,
        metadata_criteria: Optional[Dict[str, str]] = None
    ) -> Dict:
        """Query the agent for infrastructure information about a specific server using provided thread.
       
        Note: Agent instructions already contain infrastructure extraction rules (appended before calling this method).
       
        Args:
            prompt: The query prompt for infrastructure extraction
            server_name: Name of the server to query
            agent_id: The responder agent ID
            ai_client: AI Project client
            thread_id: Thread ID for the conversation
            partition_key: Application/partition key for the search index (required if metadata_criteria provided)
            metadata_criteria: Optional dictionary of metadata key-value pairs to filter documents
                              Example: {"category": "infra", "document_state": "final"}
       
        Returns:
            Dictionary containing infrastructure information for the server
        """
        from agents.utils.agent_utils import execute_run_with_retry, create_filtered_search_tool, build_metadata_filter_expression
       
        add_span_attributes(get_current_span(), {
            "server_name": server_name,
            "agent_id": agent_id,
            "thread_id": thread_id,
            "operation": "query_infrastructure",
            "has_metadata_criteria": metadata_criteria is not None
        })
       
        try:
            if not agent_id:
                return {}
           
            # Build filtered search tool if metadata criteria provided
            tools_override = None
            tool_resources_override = None
           
            if metadata_criteria and partition_key:
                # Build direct metadata filter expression
                filter_expression = build_metadata_filter_expression(partition_key, metadata_criteria)
               
                logger.debug(f"Using metadata filter for infrastructure query: {filter_expression}")
                get_current_span().add_event("metadata_filter_applied", {
                    "filter_expression": filter_expression,
                    "metadata_criteria": str(metadata_criteria)
                })
               
                filtered_tool_result = await create_filtered_search_tool(
                    client=ai_client,
                    partition_key=partition_key,
                    filter_expression=filter_expression,
                    top_k=30
                )
                tools_override = filtered_tool_result.definitions
                tool_resources_override = filtered_tool_result.resources
           
            logger.debug(f"Sending infrastructure prompt to agent {agent_id} for server '{server_name}': {prompt}")
           
            # Use the centralized run execution utility with retry
            result = await execute_run_with_retry(
                client=ai_client,
                agent_id=agent_id,
                thread_id=thread_id,
                prompt=prompt,
                context_description=f"Infrastructure query for server {server_name}",
                max_wait=30,
                max_retries=3,
                parse_json=True,
                track_token_usage=True,
                tools=tools_override,
                tool_resources=tool_resources_override
            )
           
            # Track token usage if available
            if result.token_usage:
                record_llm_interaction(
                    span=get_current_span(),
                    model=os.getenv("AZURE_AI_AGENT_DEPLOYMENT_NAME"),
                    prompt_tokens=result.token_usage.get('prompt_tokens', 0),
                    completion_tokens=result.token_usage.get('completion_tokens', 0),
                    total_tokens=result.token_usage.get('total_tokens', 0),
                    temperature=0.7
                )
           
            if result.status == "success":
                if result.parsed_json and isinstance(result.parsed_json, dict):
                    add_span_attributes(get_current_span(), {
                        "fields_found": len(result.parsed_json.keys()),
                        "success": True
                    })
                    record_search_operation(
                        span=get_current_span(),
                        index_name="infrastructure_index",
                        query=f"infrastructure for {server_name}",
                        result_count=1
                    )
                    return result.parsed_json
                else:
                    # Could not parse as JSON
                    add_span_attributes(get_current_span(), {
                        "success": False,
                        "parse_failed": True
                    })
                    return {
                        "VMHostname": server_name,
                        "Confidence": 0.3,
                        "Citation": "Could not parse infrastructure data"
                    }
           
            elif result.status == "failed":
                logger.error(f"Final failure for infrastructure query for server {server_name}: {result.error_message}")
                return {}
           
            else:
                # Timeout or other status
                logger.warning(f"Infrastructure extraction run for {server_name} ended with status: {result.status}")
                add_span_attributes(get_current_span(), {
                    "success": False,
                    "run_status": result.status,
                    "incomplete": True
                })
                return {
                    "VMHostname": server_name,
                    "Confidence": 0.0,
                    "Citation": f"Run incomplete with status: {result.status}"
                }
               
        except Exception as ex:
            logger.error(f"Failed to query infrastructure for {server_name}: {ex}")
            add_span_attributes(get_current_span(), {
                "success": False,
                "error": str(ex)
            })
            record_error_details(
                span=get_current_span(),
                error_type=type(ex).__name__,
                error_message=str(ex),
                error_code=None,
                is_retryable=True
            )
            return {}
    

    @kernel_function(description="Creates the ASR agent and generates the Assessment report")
    async def asr_agent(self, application_id) -> str:
        logger.info(f"[PLUGIN] asr_agent called with application_id={application_id}")
        tracer = get_tracer()
        with tracer.start_as_current_span("asr_agent_kernel_function") as span:
            add_span_attributes(span, {
                "application_id": application_id,
                "operation": "generate_assessment_report"
            })
            
            try:
                span.add_event("importing_asr_agent")
                logger.debug("Importing asr_agent module")
                from asr_agent import run_asr_agent
                
                span.add_event("running_asr_agent", {"application_id": application_id})
                result = await run_asr_agent(application_id, progress_callback=self.progress_callback)
                logger.info(f"ASR agent completed for {application_id}")
                logger.debug(f"ASR result: {result}")
                
                # Add result metadata to span
                if isinstance(result, dict):
                    add_span_attributes(span, {
                        "asr.status": result.get("status", "unknown"),
                        "asr.agent_id": result.get("agent_id", "unknown"),
                        "asr.reused_existing": result.get("reused_existing", False),
                        "asr.has_output_file": "output_file" in result,
                        "asr.has_blob_url": "blob_url" in result
                    })
                    
                    if result.get("status") == "success":
                        span.add_event("asr_agent_completed_successfully", {
                            "output_file": result.get("output_file", ""),
                            "markdown_file": result.get("markdown_file", ""),
                            "blob_url": result.get("blob_url", "")[:100] + "..." if result.get("blob_url") and len(result.get("blob_url")) > 100 else result.get("blob_url", "")
                        })
                        span.set_status(Status(StatusCode.OK))
                    else:
                        span.add_event("asr_agent_completed_with_error", {
                            "error_message": result.get("message", "Unknown error")
                        })
                        span.set_status(Status(StatusCode.ERROR, result.get("message", "ASR agent failed")))
                else:
                    span.add_event("asr_agent_unexpected_result", {
                        "result_type": type(result).__name__,
                        "result_preview": str(result)[:200]
                    })
                
                return json.dumps({"result": "ok", "results": result})
                
            except Exception as ex:
                logger.error(f"Error in ASR agent for {application_id}: {str(ex)}")
                span.record_exception(ex)
                span.set_status(Status(StatusCode.ERROR, str(ex)))
                span.add_event("asr_agent_exception", {
                    "error_type": type(ex).__name__,
                    "error_message": str(ex)
                })
                return json.dumps({"result": "error", "message": str(ex)})

    @kernel_function(description="Creates the Design agent and generates the architecture design")
    async def invoke_design_agent(self, application_id: str, storage_account_name: str) -> str:
        """
        Invoke the design agent to generate architecture design for the application.
        This function calls the run_design_agent method from design-agent.py.
        
        Args:
            application_id: The application ID to generate design for
            
        Returns:
            JSON string with design generation results
        """
        logger.info(f"[PLUGIN] invoke_design_agent called with application_id={application_id}")
        tracer = get_tracer()
        with tracer.start_as_current_span("invoke_design_agent_kernel_function") as span:
            add_span_attributes(span, {
                "application_id": application_id,
                "operation": "generate_design"
            })
            
            try:
                span.add_event("running_design_agent", {"application_id": application_id})
                logger.info(f"Starting design agent for {application_id}")
                
                from design_agent import run_design_agent
                # Call the run_design_agent function with progress callback
                result = await run_design_agent(application_id, progress_callback=self.progress_callback, storage_account_name=storage_account_name)
                logger.info(f"Design agent completed for {application_id}")
                logger.debug(f"Design result: {result}")
                
                # Add result metadata to span
                if isinstance(result, dict):
                    add_span_attributes(span, {
                        "design.status": result.get("status", "unknown"),
                        "design.agent_id": result.get("agent_id", "unknown"),
                        "design.thread_id": result.get("thread_id", "unknown"),
                        "design.response_length": result.get("response_length", 0),
                        "design.cleanup_performed": result.get("cleanup_performed", False)
                    })
                    
                    if result.get("status") == "success":
                        span.add_event("design_agent_completed_successfully", {
                            "thread_id": result.get("thread_id", ""),
                            "response_preview": result.get("response", "")[:200] + "..." if result.get("response") and len(result.get("response")) > 200 else result.get("response", "")
                        })
                        span.set_status(Status(StatusCode.OK))
                    else:
                        span.add_event("design_agent_completed_with_error", {
                            "error_message": result.get("message", "Unknown error")
                        })
                        span.set_status(Status(StatusCode.ERROR, result.get("message", "Design agent failed")))
                else:
                    span.add_event("design_agent_unexpected_result", {
                        "result_type": type(result).__name__,
                        "result_preview": str(result)[:200]
                    })
                
                return json.dumps({"result": "ok", "design_results": result})
                
            except Exception as ex:
                logger.error(f"Error in Design agent for {application_id}: {str(ex)}")
                span.record_exception(ex)
                span.set_status(Status(StatusCode.ERROR, str(ex)))
                span.add_event("design_agent_exception", {
                    "error_type": type(ex).__name__,
                    "error_message": str(ex)
                })
                return json.dumps({"result": "error", "message": str(ex)})
    
    @kernel_function(description="Creates the Planning agent and generates comprehensive migration planning documentation")
    async def invoke_planning_agent(self, application_id: str) -> str:
        """
        Invoke the planning agent to generate comprehensive migration planning documentation.
        This function calls the run_planning_agent method from planning_agent.py.
        
        Args:
            application_id: The application ID to generate planning documentation for
            
        Returns:
            JSON string with planning generation results
        """
        logger.info(f"[PLUGIN] invoke_planning_agent called with application_id={application_id}")
        tracer = get_tracer()
        with tracer.start_as_current_span("invoke_planning_agent_kernel_function") as span:
            add_span_attributes(span, {
                "application_id": application_id,
                "operation": "generate_planning"
            })
            
            try:
                span.add_event("running_planning_agent", {"application_id": application_id})
                logger.info(f"Starting planning agent for {application_id}")
                
                from planning_agent import run_planning_agent
                # Call the run_planning_agent function
                result = await run_planning_agent(application_id)
                logger.info(f"Planning agent completed for {application_id}")
                logger.debug(f"Planning result: {result}")
                
                # Add result metadata to span
                if isinstance(result, dict):
                    add_span_attributes(span, {
                        "planning.status": result.get("status", "unknown"),
                        "planning.agent_id": result.get("agent_id", "unknown"),
                        "planning.thread_id": result.get("thread_id", "unknown"),
                        "planning.blob_url": result.get("blob_url", ""),
                        "planning.cleanup_performed": result.get("cleanup_performed", False)
                    })
                    
                    if result.get("status") == "success":
                        span.add_event("planning_agent_completed_successfully", {
                            "thread_id": result.get("thread_id", ""),
                            "blob_url": result.get("blob_url", "")
                        })
                        span.set_status(Status(StatusCode.OK))
                    else:
                        span.add_event("planning_agent_completed_with_error", {
                            "error_message": result.get("message", "Unknown error")
                        })
                        span.set_status(Status(StatusCode.ERROR, result.get("message", "Planning agent failed")))
                else:
                    span.add_event("planning_agent_unexpected_result", {
                        "result_type": type(result).__name__,
                        "result_preview": str(result)[:200]
                    })
                
                return json.dumps({"result": "ok", "planning_results": result})
                
            except Exception as ex:
                logger.error(f"Error in Planning agent for {application_id}: {str(ex)}")
                span.record_exception(ex)
                span.set_status(Status(StatusCode.ERROR, str(ex)))
                span.add_event("planning_agent_exception", {
                    "error_type": type(ex).__name__,
                    "error_message": str(ex)
                })
                return json.dumps({"result": "error", "message": str(ex)})
    

    async def _check_kubernetes_files_indexed(self, application_id: str) -> bool:
        """
        Check if kubernetes files are already indexed for the given application.
        
        Args:
            application_id: The application ID to check
            
        Returns:
            True if kubernetes files are indexed, False otherwise
        """
        tracer = get_tracer()
        with tracer.start_as_current_span("check_kubernetes_files_indexed") as span:
            add_span_attributes(span, {
                "application_id": application_id,
                "operation": "check_kubernetes_index"
            })
            
            try:
                # Get Azure AI Search configuration
                search_endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
                #search_api_key = os.getenv("AZURE_SEARCH_ADMIN_KEY") or os.getenv("AZURE_SEARCH_API_KEY")
                
                if not search_endpoint:
                    logger.warning("AZURE_SEARCH_ENDPOINT not configured, skipping index check")
                    span.add_event("search_endpoint_not_configured")
                    return False
                
                # Helper function to get the appropriate credential
                def get_credential():
                    logger.debug("Creating SyncDefaultAzureCredential for Azure AI Search")
                    return SyncDefaultAzureCredential(exclude_shared_token_cache_credential=True)
                
                # First, check if the index exists
                logger.debug(f"Creating SearchIndexClient with endpoint: {search_endpoint}")
                index_client = SearchIndexClient(
                    endpoint=search_endpoint,
                    credential=get_credential()
                )
                
                try:
                    logger.debug(f"Attempting to get index '{application_id}' metadata")
                    index_client.get_index(application_id)
                    logger.debug(f"Index '{application_id}' exists")
                except Exception as index_ex:
                    error_str = str(index_ex)
                    error_type = type(index_ex).__name__
                    
                    # Check for 403 Forbidden specifically
                    is_forbidden = "Forbidden" in error_str or "403" in error_str
                    
                    if "ResourceNotFound" in error_type or "404" in error_str:
                        logger.info(f"Index '{application_id}' does not exist yet")
                        span.add_event("index_not_found", {"index_name": application_id})
                        return False
                    else:
                        # Other errors (like permission issues), log and continue
                        if is_forbidden:
                            logger.error(f"403 Forbidden error when checking index '{application_id}': {error_str}")
                            logger.error(f"Error type: {error_type}, Full exception: {repr(index_ex)}")
                        else:
                            logger.warning(f"Could not verify index existence: {index_ex}")
                        
                        span.add_event("index_check_failed", {
                            "error_type": error_type,
                            "error_message": error_str,
                            "is_forbidden": is_forbidden
                        })
                        return False
                
                # Build kubernetes filter
                agent = KubernetesDiscoveryAgent()
                kubernetes_filter = agent.build_kubernetes_input_filter(application_id)
                
                # Create search client for querying documents
                logger.debug(f"Creating SearchClient for index '{application_id}'")
                search_client = SearchClient(
                    endpoint=search_endpoint,
                    index_name=application_id,
                    credential=get_credential()
                )
                
                # Search for kubernetes files with filter
                logger.debug(f"Searching index '{application_id}' with filter: {kubernetes_filter}")
                try:
                    results = search_client.search(
                        search_text="*",
                        filter=kubernetes_filter,
                        top=1,
                        include_total_count=True
                    )
                except Exception as search_ex:
                    error_str = str(search_ex)
                    is_forbidden = "Forbidden" in error_str or "403" in error_str
                    if is_forbidden:
                        logger.error(f"403 Forbidden error when searching index '{application_id}': {error_str}")
                        logger.error(f"Error type: {type(search_ex).__name__}, Full exception: {repr(search_ex)}")
                    raise
                
                # Check if any documents were found
                total_count = results.get_count()
                indexed = total_count > 0
                
                add_span_attributes(span, {
                    "kubernetes_files_found": total_count,
                    "kubernetes_files_indexed": indexed
                })
                
                logger.info(f"Kubernetes files indexed check for {application_id}: {indexed} (found {total_count} documents)")
                span.set_status(Status(StatusCode.OK))
                return indexed
                
            except Exception as ex:
                error_msg = str(ex)
                error_type = type(ex).__name__
                
                # Provide specific guidance for common errors
                if "Forbidden" in error_msg or "403" in error_msg:
                    logger.error(f"=== 403 FORBIDDEN ERROR DETECTED ===")
                    logger.error(f"Location: _check_kubernetes_files_indexed")
                    logger.error(f"Application ID: {application_id}")
                    logger.error(f"Error Type: {error_type}")
                    logger.error(f"Error Message: {error_msg}")
                    logger.error(f"Full Exception: {repr(ex)}")
                    logger.error(f"Search Endpoint: {search_endpoint}")
                    logger.warning(f"Access denied to Azure AI Search index '{application_id}'. Possible solutions:")
                    logger.warning("  1. Set AZURE_SEARCH_ADMIN_KEY environment variable with your Azure AI Search admin key")
                    logger.warning("  2. Grant 'Search Index Data Reader' role to the managed identity")
                    logger.info("Proceeding to trigger indexing as a precaution...")
                else:
                    logger.warning(f"Failed to check kubernetes file indexing status: {ex}")
                    logger.debug(f"Error details - Type: {error_type}, Full: {repr(ex)}")
                
                span.record_exception(ex)
                span.add_event("kubernetes_index_check_failed", {
                    "error_type": error_type,
                    "error_message": error_msg,
                    "is_permission_issue": "Forbidden" in error_msg or "403" in error_msg
                })
                # Return False to trigger indexing on check failure
                return False
    
    async def _trigger_kubernetes_indexing(self, application_id: str) -> bool:
        """
        Trigger indexing specifically for kubernetes folder files.
        
        Args:
            application_id: The application ID to index
            
        Returns:
            True if indexing was successful, False otherwise
        """
        tracer = get_tracer()
        with tracer.start_as_current_span("trigger_kubernetes_indexing") as span:
            add_span_attributes(span, {
                "application_id": application_id,
                "operation": "trigger_kubernetes_indexing"
            })
            
            try:
                # Get indexer service URL
                indexer_url = os.getenv("AZURE_INDEXING_FUNCTION_URL")
                if not indexer_url:
                    logger.error("AZURE_INDEXING_FUNCTION_URL not set")
                    span.set_status(Status(StatusCode.ERROR, "AZURE_INDEXING_FUNCTION_URL not set"))
                    return False
                
                # Build kubernetes filter
                agent = KubernetesDiscoveryAgent()
                kubernetes_filter = agent.build_kubernetes_input_filter(application_id)
                
                # Prepare payload with kubernetes-specific filter
                headers = {"Content-Type": "application/json"}
                payload = {
                    "appId": application_id,
                    "container": application_id,
                    "folder_prefix": "kubernetes/"
                }
                
                add_span_attributes(span, {
                    "indexer_url": indexer_url,
                    "payload_app_id": application_id,
                    "payload_folder_prefix": "kubernetes/"})
                
                logger.info(f"Triggering kubernetes-specific indexing for {application_id}")
                logger.debug(f"Kubernetes filter: {kubernetes_filter}")
                
                async with aiohttp.ClientSession() as session:
                    async with session.post(indexer_url, json=payload, headers=headers) as response:
                        try:
                            result = await response.json()
                            
                            if response.status >= 400:
                                logger.error(f"Kubernetes indexing failed: HTTP {response.status}: {result}")
                                span.set_status(Status(StatusCode.ERROR, f"HTTP {response.status}"))
                                span.add_event("kubernetes_indexing_failed", {
                                    "http_status": response.status,
                                    "result": str(result)[:500]
                                })
                                return False
                            else:
                                logger.info(f"Kubernetes indexing triggered successfully for {application_id}")
                                span.add_event("kubernetes_indexing_success", {
                                    "http_status": response.status
                                })
                                span.set_status(Status(StatusCode.OK))
                                return True
                                
                        except Exception as parse_ex:
                            text = await response.text()
                            logger.error(f"Failed to parse kubernetes indexing response: {parse_ex}")
                            span.record_exception(parse_ex)
                            span.add_event("response_parse_error", {
                                "http_status": response.status,
                                "response_text_length": len(text)
                            })
                            return False
                            
            except Exception as ex:
                logger.error(f"Error triggering kubernetes indexing: {ex}")
                span.record_exception(ex)
                span.set_status(Status(StatusCode.ERROR, str(ex)))
                return False
            
    @kernel_function(description="Creates the Kubernetes discovery agent and generates the cluster summary report")
    async def invoke_kubernetes_discovery_agent(self, application_id: str) -> str:
        """
        Initialize Kubernetes Discovery Agent for a given cluster/application.
        This function calls the kubernetes_discovery_agent method from kubernetes-discovery-agent.py.
        
        Args:
            application_id: The application ID to generate cluster summary report for
            
        Returns:
            JSON string with kubernetes discovery agent results
        """
        logger.info(f"[PLUGIN] invoke_kubernetes_discovery_agent called with application_id={application_id}")
        tracer = get_tracer()
        with tracer.start_as_current_span("invoke_kubernetes_discovery_agent_kernel_function") as span:
            add_span_attributes(span, {
                "application_id": application_id,
                "operation": "generate_kubernetes_discovery_report"
            })
            
            try:
                # Pre-validation: Check if kubernetes files are already indexed
                span.add_event("checking_kubernetes_indexing_status", {"application_id": application_id})
                logger.info(f"Checking kubernetes file indexing status for {application_id}")
                
                kubernetes_indexed = await self._check_kubernetes_files_indexed(application_id)
                
                if not kubernetes_indexed:
                    logger.info(f"Kubernetes files not indexed for {application_id}, triggering indexing")
                    span.add_event("triggering_kubernetes_indexing", {"application_id": application_id})
                    
                    indexing_success = await self._trigger_kubernetes_indexing(application_id)
                    
                    if not indexing_success:
                        logger.warning(f"Kubernetes indexing trigger failed for {application_id}, continuing with discovery anyway")
                        span.add_event("kubernetes_indexing_trigger_failed", {
                            "application_id": application_id,
                            "continuing": True
                        })
                    else:
                        logger.info(f"Kubernetes indexing triggered successfully for {application_id}")
                        span.add_event("kubernetes_indexing_triggered", {"application_id": application_id})
                        # Wait a few seconds for indexing to complete
                        await asyncio.sleep(5)
                else:
                    logger.info(f"Kubernetes files already indexed for {application_id}, skipping indexing trigger")
                    span.add_event("kubernetes_already_indexed", {"application_id": application_id})
                
                span.add_event("running_kubernetes_discovery_agent", {"application_id": application_id})
                logger.info(f"Starting kubernetes discovery agent for {application_id}")
                
                from agents.kubernetes_discovery_agent import kubernetes_discovery_agent
                # Call the kubernetes_discovery_agent function with progress callback
                result = await kubernetes_discovery_agent(application_id, progress_callback=self.progress_callback)
                logger.info(f"Kubernetes discovery agent completed for {application_id}")
                logger.debug(f"Kubernetes discovery result: {result}")
                
                # Add result metadata to span
                if isinstance(result, dict):
                    add_span_attributes(span, {
                        "kubernetes_discovery.status": result.get("status", "unknown"),
                        "kubernetes_discovery.agent_id": result.get("agent_id", "unknown"),
                        "kubernetes_discovery.thread_id": result.get("thread_id", "unknown"),
                        "kubernetes_discovery.response_length": result.get("response_length", 0),
                        "kubernetes_discovery.cleanup_performed": result.get("cleanup_performed", False)
                    })
                    
                    if result.get("status") == "success":
                        span.add_event("kubernetes_discovery_agent_completed_successfully", {
                            "thread_id": result.get("thread_id", ""),
                            "response_preview": result.get("response", "")[:200] + "..." if result.get("response") and len(result.get("response")) > 200 else result.get("response", "")
                        })
                        span.set_status(Status(StatusCode.OK))
                    else:
                        span.add_event("kubernetes_discovery_agent_completed_with_error", {
                            "error_message": result.get("message", "Unknown error")
                        })
                        span.set_status(Status(StatusCode.ERROR, result.get("message", "Kubernetes discovery agent failed")))
                else:
                    span.add_event("kubernetes_discovery_agent_unexpected_result", {
                        "result_type": type(result).__name__,
                        "result_preview": str(result)[:200]
                    })
                
                return json.dumps({"result": "ok", "kubernetes_discovery_results": result})
                
            except Exception as ex:
                logger.error(f"Error in Kubernetes discovery agent for {application_id}: {str(ex)}")
                span.record_exception(ex)
                span.set_status(Status(StatusCode.ERROR, str(ex)))
                span.add_event("kubernetes_discovery_agent_exception", {
                    "error_type": type(ex).__name__,
                    "error_message": str(ex)
                })
                return json.dumps({"result": "error", "message": str(ex)})

    @kernel_function(description="Analyze architecture security from blob storage design document URL")
    async def analyze_architecture_security(self, design_doc_url: str, app_id: str) -> str:
        """
        Analyze architecture for security compliance from blob storage design document.
        
        This function runs a synchronous architecture security analysis using dynamic mode.
        The architecture analyzer handles:
        - SCF index validation
        - Design document reading
        - Architecture diagram discovery
        - Security analysis
        - Report generation
        
        Args:
            design_doc_url: Blob storage path to design document to analyze
            app_id: Application ID for agent naming and blob storage organization
            
        Returns:
            JSON string with complete analysis results
        """
        logger.info(f"[PLUGIN] analyze_architecture_security called with design_doc_url={design_doc_url}, app_id={app_id}")
        tracer = get_tracer()
        with tracer.start_as_current_span("analyze_architecture_security_kernel_function") as span:
            add_span_attributes(span, {
                "design_doc_url": design_doc_url[:200],
                "app_id": app_id,
                "operation": "analyze_architecture_security",
                "analysis_mode": "dynamic"
            })
            
            try:
                # Call architecture analyzer which handles all validation and analysis
                span.add_event("running_architecture_analysis", {
                    "app_id": app_id,
                    "design_doc_url": design_doc_url[:200]
                })
                logger.info(f"Starting architecture analysis for {app_id}")
                
                # Import the architecture analysis function
                from architecture_analyzer_agent import run_dynamic_architecture_analysis
                
                # Call the analysis function synchronously (is_async=False)
                # The architecture analyzer will:
                # 1. Validate SCF index
                # 2. Read design document and discover architectures
                # 3. Perform security analysis
                # 4. Generate consolidated report
                result = await run_dynamic_architecture_analysis(
                    app_id=app_id,
                    design_doc_url=design_doc_url,
                    analysis_instructions="Analyze this architecture for security compliance and generate recommendations.",
                    operation=None,  # No operation tracking for sync mode
                    is_async=False,   # SYNC mode - runs to completion
                    progress_callback=self.progress_callback  # Forward progress callback
                )
                
                logger.info(f"Architecture analysis completed for {app_id}")
                result_str = str(result)
                logger.debug(f"Architecture analysis result: {result_str[:400]}{'...' if len(result_str) > 400 else ''}")
                
                # Add result metadata to span
                if isinstance(result, dict):
                    add_span_attributes(span, {
                        "architecture_analysis.status": result.get("status", "unknown"),
                        "architecture_analysis.architectures_count": len(result.get("architecture_results", {})),
                        "architecture_analysis.total_findings": result.get("total_findings", 0),
                        "architecture_analysis.has_report_url": "consolidated_report_url" in result
                    })
                    
                    if result.get("status") == "success":
                        span.add_event("architecture_analysis_completed_successfully", {
                            "architectures_analyzed": len(result.get("architecture_results", {})),
                            "total_findings": result.get("total_findings", 0),
                            "report_url": result.get("consolidated_report_url", "")[:100]
                        })
                        span.set_status(Status(StatusCode.OK))
                    elif result.get("status") == "validation_failed":
                        # SCF validation failed - return validation error
                        span.add_event("scf_validation_failed", {
                            "error_message": result.get("error", "Unknown validation error")
                        })
                        span.set_status(Status(StatusCode.ERROR, "SCF validation failed"))
                    else:
                        span.add_event("architecture_analysis_completed_with_error", {
                            "error_message": result.get("error", "Unknown error")
                        })
                        span.set_status(Status(StatusCode.ERROR, result.get("error", "Analysis failed")))
                else:
                    span.add_event("architecture_analysis_unexpected_result", {
                        "result_type": type(result).__name__,
                        "result_preview": str(result)[:200]
                    })
                
                # Return results directly from architecture analyzer
                return json.dumps({"result": "ok", "architecture_analysis_results": result})
                
            except Exception as ex:
                logger.error(f"Error in architecture security analysis for {app_id}: {str(ex)}")
                span.record_exception(ex)
                span.set_status(Status(StatusCode.ERROR, str(ex)))
                span.add_event("architecture_analysis_exception", {
                    "error_type": type(ex).__name__,
                    "error_message": str(ex)
                })
                return json.dumps({"result": "error", "message": str(ex)})

    @kernel_function(description="Analyze code from a repository URL (GitHub, GitLab, Azure DevOps, or Azure Blob)")
    async def analyze_code_from_repo(
        self,
        application_id: str,
        repo_url: str,
        perform_security_scan: bool = True,
        operation_id: str = None
    ) -> str:
        """
        Analyze code from a repository and generate security/architecture reports.
        
        This function clones/downloads code from the provided URL, runs deterministic
        codebase analysis, and uses AI agents to generate comprehensive reports.
        
        Args:
            application_id: The application ID for tracking and RBAC
            repo_url: Repository URL (GitHub, GitLab, Azure DevOps, Bitbucket, or Azure Blob)
            perform_security_scan: Whether to scan for secrets before analysis (default: True)
            operation_id: Optional operation ID for updating operation record with results
            
        Returns:
            JSON string with code analysis results including:
            - Codebase statistics (files, lines, languages)
            - Framework detection
            - Dependency analysis
            - Security findings
            - Generated reports
        """
        logger.info(f"[PLUGIN] analyze_code_from_repo called with application_id={application_id}, repo_url={repo_url}, operation_id={operation_id}")
        tracer = get_tracer()
        with tracer.start_as_current_span("analyze_code_from_repo_kernel_function") as span:
            add_span_attributes(span, {
                "application_id": application_id,
                "repo_url": repo_url[:200] if repo_url else None,
                "perform_security_scan": perform_security_scan,
                "operation": "code_analysis"
            })

            try:
                span.add_event("starting_code_analysis", {"application_id": application_id})
                logger.info(f"Starting code analysis for {application_id} from {repo_url}")

                # Import and create the CodeAnalyzerPlugin
                from agents.code_analyzer_agent import CodeAnalyzerPlugin

                # Create plugin instance with app_id for dynamic agent naming
                plugin = CodeAnalyzerPlugin(operation_id=operation_id, app_id=application_id)

                # Run the code analysis
                result_json = await plugin.analyze_code_from_repo(
                    repo_url=repo_url,
                    perform_security_scan=perform_security_scan
                )

                logger.info(f"Code analysis completed for {application_id}")

                # Parse result for span attributes
                result = json.loads(result_json) if isinstance(result_json, str) else result_json

                # Upload the report to blob storage if analysis succeeded
                blob_url = None
                if result.get("result") == "success":
                    report_file = result.get("analysis_summary", {}).get("report_file")
                    if report_file and os.path.exists(report_file):
                        try:
                            # Read the report content
                            with open(report_file, 'r', encoding='utf-8') as f:
                                report_content = f.read()

                            # Upload to blob storage using app_id as container name
                            content_type = result.get("content_type", "code")
                            upload_result_json = await plugin.upload_code_report_to_storage(
                                markdown_content=report_content,
                                repo_url=repo_url,
                                content_type=content_type,
                                container_name=application_id  # Use app_id as container
                            )
                            upload_result = json.loads(upload_result_json) if isinstance(upload_result_json, str) else upload_result_json

                            if upload_result.get("result") == "success":
                                blob_url = upload_result.get("blob_url")
                                result["blob_url"] = blob_url
                                logger.info(f"Report uploaded to blob: {blob_url}")
                                span.add_event("report_uploaded", {"blob_url": blob_url})

                                # Update operation record directly with structured results if operation_id provided
                                if operation_id:
                                    try:
                                        from agents.operation_service import get_operation_service
                                        from agents.operation_models import OperationStatus

                                        op_service = get_operation_service()
                                        operation = await op_service.get_operation(operation_id, application_id)

                                        if operation:
                                            # Build structured result data
                                            structured_result = {
                                                "status": "success",
                                                "app_id": application_id,
                                                "repo_url": repo_url,
                                                "content_type": result.get("content_type", "unknown"),
                                                "config_folder": result.get("config_folder", "unknown"),
                                                "analysis_result": result.get("analysis_summary", {}),
                                                "repo_metadata": result.get("repo_metadata", {}),
                                                "codebase_analysis": result.get("codebase_analysis", {}),
                                                "agents_info": {
                                                    "agents_used": result.get("analysis_summary", {}).get("agents_used", []),
                                                    "orchestrator_used": True
                                                },
                                                "report_url": blob_url,
                                                "message": "Code analysis completed successfully"
                                            }

                                            # Update operation with blob_url and structured result - FINAL update
                                            operation.blob_url = blob_url
                                            operation.complete_operation(structured_result)  # Mark as completed
                                            await op_service.update_operation(operation)

                                            logger.info(f"Completed operation {operation_id} with structured results and blob_url")
                                            span.add_event("operation_completed", {"operation_id": operation_id, "blob_url": blob_url})
                                        else:
                                            logger.warning(f"Operation {operation_id} not found for update")
                                    except Exception as op_ex:
                                        logger.warning(f"Failed to update operation {operation_id}: {op_ex}")
                                        span.add_event("operation_update_failed", {"error": str(op_ex)})
                            else:
                                logger.warning(f"Failed to upload report: {upload_result.get('message')}")
                                span.add_event("report_upload_failed", {"error": upload_result.get("message")})
                        except Exception as upload_ex:
                            logger.warning(f"Failed to upload report to blob: {upload_ex}")
                            span.add_event("report_upload_error", {"error": str(upload_ex)})
                    else:
                        logger.warning(f"No report file found to upload: {report_file}")
                        span.add_event("no_report_file", {"expected_path": str(report_file)})

                if isinstance(result, dict):
                    add_span_attributes(span, {
                        "code_analysis.status": result.get("result", "unknown"),
                        "code_analysis.content_type": result.get("content_type", "unknown"),
                        "code_analysis.config_folder": result.get("config_folder", "unknown")
                    })

                    # Add codebase stats if available
                    codebase = result.get("codebase_analysis", {})
                    if codebase:
                        add_span_attributes(span, {
                            "code_analysis.total_files": codebase.get("total_files", 0),
                            "code_analysis.total_lines": codebase.get("total_lines", 0),
                            "code_analysis.frameworks_count": len(codebase.get("frameworks", []))
                        })

                    if result.get("result") == "success":
                        span.add_event("code_analysis_completed_successfully", {
                            "content_type": result.get("content_type", ""),
                            "files_processed": result.get("analysis_summary", {}).get("files_processed", 0)
                        })
                        span.set_status(Status(StatusCode.OK))
                    else:
                        span.add_event("code_analysis_completed_with_error", {
                            "error_message": result.get("message", "Unknown error")
                        })
                        span.set_status(Status(StatusCode.ERROR, result.get("message", "Code analysis failed")))

                return json.dumps({"result": "ok", "code_analysis_results": result})

            except Exception as ex:
                logger.error(f"Error in code analysis for {application_id}: {str(ex)}")
                span.record_exception(ex)
                span.set_status(Status(StatusCode.ERROR, str(ex)))
                span.add_event("code_analysis_exception", {
                    "error_type": type(ex).__name__,
                    "error_message": str(ex)
                })
                return json.dumps({"result": "error", "message": str(ex)})


    @kernel_function(description="Export all application table data to blob storage for indexing")
    async def export_app_tables_to_blob(self, application_id: str) -> str:
        """Export application-specific Azure Table entities into separate JSONL blobs for indexing with metadata."""
        logger.info(f"[PLUGIN] export_app_tables_to_blob called with application_id={application_id}")
        tracer = get_tracer()
        with tracer.start_as_current_span("export_app_tables_to_blob") as span:
            add_span_attributes(span, {
                "application_id": application_id,
                "operation": "export_tables"
            })
            
            try:
                from azure.data.tables import TableServiceClient
                from azure.identity import DefaultAzureCredential as SyncDefaultAzureCredential
                from azure.storage.blob import BlobServiceClient
                import io, datetime

                logger.debug(f"[export] Starting export for app_id={application_id}")
                span.add_event("export_started", {"application_id": application_id})
                
                # Define tables with their category metadata
                # Format: (table_name, category, include_metadata)
                table_configs = [
                    (sanitize_table_name(f"AppDetails{application_id}"), "appdetails", True),
                    (sanitize_table_name(f"MSSQLDB{application_id}"), "sqldb", True),
                    (sanitize_table_name(f"OracleDB{application_id}"), "oracledb", True),
                    (sanitize_table_name(f"IntegrationDependency{application_id}"), "network", True),
                    (sanitize_table_name(f"InfrastructureDetails{application_id}"), "infra", True),
                ]
                
                # Check if K8S table exists and add it to export list (no metadata)
                k8s_table_name = f"K8S{application_id}"
                tables_url = os.getenv("AZURE_TABLES_ACCOUNT_URL")
                if tables_url:
                    try:
                        temp_tsc = get_table_service_client(tables_url)
                        k8s_tc = temp_tsc.get_table_client(table_name=k8s_table_name)
                        # Check if K8S table exists by attempting to list one entity
                        next(k8s_tc.list_entities(results_per_page=1), None)
                        table_configs.append((k8s_table_name, "k8s", False))  # K8S table - no metadata
                        logger.info(f"[export] K8S table found: {k8s_table_name} - adding to export list (no metadata)")
                        span.add_event("k8s_table_found", {"table_name": k8s_table_name})
                    except Exception as k8s_check_ex:
                        if "TableNotFound" in str(k8s_check_ex) or "ResourceNotFound" in str(k8s_check_ex):
                            logger.info(f"[export] K8S table not found: {k8s_table_name} - skipping")
                            span.add_event("k8s_table_not_found", {"table_name": k8s_table_name})
                        else:
                            logger.warning(f"[export] Error checking K8S table existence: {str(k8s_check_ex)}")
                
                tables = [config[0] for config in table_configs]
                logger.debug(f"[export] Using table list: {tables}")
                
                add_span_attributes(span, {
                    "table_count": len(tables),
                    "tables": ", ".join(tables)
                })

                tables_url = os.getenv("AZURE_TABLES_ACCOUNT_URL")
                if not tables_url:
                    error_msg = "Missing AZURE_TABLES_ACCOUNT_URL"
                    span.set_status(Status(StatusCode.ERROR, error_msg))
                    return json.dumps({"status": "error", "message": error_msg})
                tsc = get_table_service_client(tables_url)
                logger.debug(f"[export] Initialized table client via storage account key or AAD")
                span.add_event("table_client_initialized", {
                    "tables_url": tables_url
                })

                total_records = 0
                exported_tables = []
                exported_blobs = []
                per_table_counts = {}

                for table_name, category, include_metadata in table_configs:
                    original_table = table_name
                    logger.debug(f"[export] Processing table {original_table} with category={category}")
                    
                    with tracer.start_as_current_span("export_single_table") as table_span:
                        add_span_attributes(table_span, {
                            "table_name": original_table,
                            "application_id": application_id,
                            "category": category,
                            "include_metadata": include_metadata
                        })
                        
                        try:
                            tc = tsc.get_table_client(table_name=table_name)
                            
                            # Check if table exists and get entities
                            entities = []
                            escaped = str(application_id).replace("'", "''")
                            
                            # Primary attempt: partition filter
                            try:
                                entities = list(tc.query_entities(query_filter=f"PartitionKey eq '{escaped}'"))
                                logger.debug(f"[export] Table {original_table} returned {len(entities)} entities")
                                table_span.add_event("entities_queried", {
                                    "method": "partition_filter",
                                    "entity_count": len(entities)
                                })
                            except Exception as pe:
                                logger.error(f"[export] Table {original_table} query error: {pe}")
                                table_span.add_event("partition_query_failed", {"error": str(pe)})
                                # Fallback: try without filter
                                try:
                                    all_entities = list(tc.list_entities())
                                    # Filter by application_id
                                    lowered_app = application_id.lower()
                                    entities = [r for r in all_entities if str(r.get("PartitionKey", "")).lower() == lowered_app]
                                    logger.debug(f"[export] Table {original_table} fallback returned {len(entities)} entities")
                                    table_span.add_event("entities_queried", {
                                        "method": "fallback_filter",
                                        "entity_count": len(entities),
                                        "total_entities": len(all_entities)
                                    })
                                except Exception as fallback_ex:
                                    logger.error(f"[export] Table {original_table} fallback error: {fallback_ex}")
                                    table_span.record_exception(fallback_ex)
                                    table_span.add_event("fallback_query_failed", {"error": str(fallback_ex)})
                                    entities = []

                            if not entities:
                                per_table_counts[original_table] = {"exists": True, "exported": 0, "note": "no entities found"}
                                logger.debug(f"[export] Table {original_table} no entities exported")
                                table_span.add_event("no_entities_found")
                                continue

                            # Create separate buffer for this table
                            table_buf = io.StringIO()
                            exported_count = 0
                            for e in entities:
                                # Remove service metadata for cleaner indexing
                                e.pop("etag", None)
                                e.pop("Timestamp", None)
                                e["_SourceTable"] = original_table
                                pk = e.get("PartitionKey", "")
                                rk = e.get("RowKey", "")
                                e["Key"] = f"{pk}_{rk}" if pk else rk
                                table_buf.write(json.dumps(e, ensure_ascii=False) + "\n")
                                total_records += 1
                                exported_count += 1
                            
                            # Upload this table as a separate .jsonl file
                            blob_name = f"{category}_{application_id}.jsonl"
                            data_bytes = table_buf.getvalue().encode("utf-8")
                            
                            # Build metadata if required
                            blob_metadata = None
                            if include_metadata:
                                blob_metadata = {
                                    "document_state": "final",
                                    "category": category
                                }
                                logger.debug(f"[export] Adding metadata to {blob_name}: {blob_metadata}")
                            
                            blob_url = upload_content_to_container(
                                content=data_bytes,
                                app_id=application_id,
                                blob_name=blob_name,
                                folder_prefix="asr/input/",
                                content_type="application/x-ndjson",
                                metadata=blob_metadata
                            )
                            
                            exported_tables.append(original_table)
                            exported_blobs.append({
                                "table": original_table,
                                "category": category,
                                "blob_url": blob_url,
                                "blob_name": f"asr/input/{blob_name}",
                                "records": exported_count,
                                "has_metadata": include_metadata
                            })
                            
                            per_table_counts[original_table] = {
                                "exists": True, 
                                "exported": exported_count,
                                "blob_name": blob_name,
                                "category": category,
                                "has_metadata": include_metadata
                            }
                            logger.debug(f"[export] Table {original_table} exported {exported_count} entities to {blob_name}")
                            
                            record_table_operation(table_span, original_table, "export", exported_count, application_id)
                            table_span.set_status(Status(StatusCode.OK))
                            
                        except Exception as ex_access:
                            logger.error(f"[export] Table {original_table} access error: {ex_access}")
                            per_table_counts[original_table] = {"exists": False, "exported": 0, "error": str(ex_access)}
                            table_span.record_exception(ex_access)
                            table_span.set_status(Status(StatusCode.ERROR, str(ex_access)))
                            continue

                if total_records == 0:
                    logger.debug("[export] No records exported from any table")
                    span.add_event("no_records_exported")
                    span.set_status(Status(StatusCode.ERROR, "No table data found"))
                    return json.dumps({"status": "empty", "message": "No table data found to export", "per_table": per_table_counts})
                
                add_span_attributes(span, {
                    "total_records": total_records,
                    "exported_blobs_count": len(exported_blobs),
                    "exported_tables_count": len(exported_tables)
                })
                
                span.add_event("export_completed_successfully", {
                    "total_records": total_records,
                    "blobs_created": len(exported_blobs),
                    "exported_tables": exported_tables
                })
                
                logger.debug(f"[export] Export complete: {total_records} records across {len(exported_blobs)} blobs")
                span.set_status(Status(StatusCode.OK))
                return json.dumps({
                    "status": "ok",
                    "total_records": total_records,
                    "tables_exported": exported_tables,
                    "blobs_created": exported_blobs,
                    "per_table": per_table_counts
                })
                
            except Exception as ex:
                logger.error(f"Failed to export tables: {ex}")
                span.record_exception(ex)
                span.set_status(Status(StatusCode.ERROR, str(ex)))
                return json.dumps({"status": "error", "message": str(ex)})

    @kernel_function(description="Cleanup Responder agent after analysis workflow completion")
    async def cleanup_responder_agent(self, application_id: str) -> str:
        """Cleanup the Responder agent for the given application ID after analysis is complete."""
        logger.info(f"[PLUGIN] cleanup_responder_agent called with application_id={application_id}")
        tracer = get_tracer()
        with tracer.start_as_current_span("cleanup_responder_agent") as span:
            add_span_attributes(span, {
                "application_id": application_id,
                "operation": "cleanup_responder_agent"
            })
            
            try:
                endpoint = os.getenv("AZURE_EXISTING_AIPROJECT_ENDPOINT")
                if not endpoint:
                    span.set_status(Status(StatusCode.ERROR, "AZURE_EXISTING_AIPROJECT_ENDPOINT not set"))
                    return json.dumps({"status": "error", "message": "AZURE_EXISTING_AIPROJECT_ENDPOINT not set"})
                
                async with (
                    DefaultAzureCredential(exclude_shared_token_cache_credential=True) as creds,
                    AzureAIAgent.create_client(credential=creds, endpoint=endpoint) as client,
                ):
                    # Clean up shared thread if it exists
                    shared_thread_id = self._shared_threads.get(application_id)
                    if shared_thread_id:
                        try:
                            logger.info(f"Deleting shared thread {shared_thread_id} for application {application_id}")
                            await client.agents.threads.delete(thread_id=shared_thread_id)
                            del self._shared_threads[application_id]
                            logger.info(f"Successfully deleted shared thread {shared_thread_id}")
                            span.add_event("shared_thread_deleted", {"thread_id": shared_thread_id})
                        except Exception as thread_ex:
                            logger.warning(f"Failed to delete shared thread {shared_thread_id}: {thread_ex}")
                            span.add_event("shared_thread_deletion_failed", {"thread_id": shared_thread_id, "error": str(thread_ex)})
                    
                    # Find the Responder agent
                    existing_agent = await _find_existing_responder_agent(client, application_id)
                    
                    if existing_agent:
                        agent_id = existing_agent.id
                        agent_name = getattr(existing_agent, 'name', application_id)
                        logger.debug(f"Found Responder agent to cleanup: {agent_id} with name: {agent_name}")
                        
                        # Delete the Responder agent
                        await client.agents.delete_agent(agent_id)
                        logger.debug(f"Deleted Responder agent: {agent_id}")
                        
                        result = {
                            "status": "success",
                            "message": f"Deleted Responder agent {agent_name}",
                            "agent_id": agent_id,
                            "agent_name": agent_name
                        }
                        
                        span.add_event("responder_agent_deleted", {
                            "agent_id": agent_id,
                            "agent_name": agent_name
                        })
                        add_span_attributes(span, {
                            "cleanup.agent_id": agent_id,
                            "cleanup.agent_name": agent_name,
                            "cleanup.status": "success"
                        })
                        span.set_status(Status(StatusCode.OK))
                        
                        return json.dumps(result)
                    else:
                        logger.debug(f"No Responder agent found for application: {application_id}")
                        result = {
                            "status": "not_found",
                            "message": f"No Responder agent found for application {application_id}",
                            "application_id": application_id
                        }
                        
                        span.add_event("responder_agent_not_found", {"application_id": application_id})
                        add_span_attributes(span, {"cleanup.status": "not_found"})
                        span.set_status(Status(StatusCode.OK))
                        
                        return json.dumps(result)
                        
            except Exception as ex:
                error_msg = str(ex)
                error_type = type(ex).__name__
                
                if "Forbidden" in error_msg or "403" in error_msg:
                    logger.error(f"=== 403 FORBIDDEN ERROR DETECTED ===")
                    logger.error(f"Location: cleanup_responder_agent")
                    logger.error(f"Application ID: {application_id}")
                    logger.error(f"Error Type: {error_type}")
                    logger.error(f"Error Message: {error_msg}")
                    logger.error(f"Full Exception: {repr(ex)}")
                    logger.error(f"Endpoint: {endpoint}")
                else:
                    logger.error(f"Failed to cleanup Responder agent: {ex}")
                
                span.record_exception(ex)
                span.set_status(Status(StatusCode.ERROR, str(ex)))
                return json.dumps({"status": "error", "message": str(ex)})
 

    # Thread management methods
    @trace_async_function("create_temporary_thread")
    async def _create_temporary_thread(self, agent_id: str, ai_client, function_name: str) -> str:
        """Create a temporary thread for a specific function execution."""
        thread = await ai_client.agents.threads.create()
        thread_id = thread.id
        
        # Set AI Foundry thread ID in logging context
        set_ai_thread_id(thread_id)
        
        add_span_attributes(get_current_span(), {
            "agent_id": agent_id,
            "thread_id": thread_id,
            "function_name": function_name,
            "operation": "create_thread"
        })
        logger.debug(f"Created temporary thread {thread_id} for agent {agent_id} in function {function_name}")
        return thread_id
    
    @trace_async_function("delete_temporary_thread")
    async def _delete_temporary_thread(self, thread_id: str, ai_client, function_name: str) -> None:
        """Delete a temporary thread after function completion."""
        try:
            await ai_client.agents.threads.delete(thread_id=thread_id)
            add_span_attributes(get_current_span(), {
                "thread_id": thread_id,
                "function_name": function_name,
                "operation": "delete_thread",
                "success": True
            })
            logger.debug(f"Deleted temporary thread {thread_id} from function {function_name}")
            
            # Clear AI Foundry thread ID from logging context
            set_ai_thread_id(None)
            
        except Exception as ex:
            add_span_attributes(get_current_span(), {
                "thread_id": thread_id,
                "function_name": function_name,
                "operation": "delete_thread",
                "success": False,
                "error": str(ex)
            })
            record_error_details(
                error_type=type(ex).__name__,
                error_message=str(ex),
                error_code=None,
                is_retryable=False
            )
            logger.warning(f"Could not delete temporary thread {thread_id}: {ex}")
    
    @trace_async_function("get_shared_thread")
    async def _get_shared_thread(self, application_id: str, agent_id: str, ai_client, function_name: str) -> str:
        """Get the shared thread for an application, creating it if it doesn't exist."""
        thread_created = False
        if application_id not in self._shared_threads:
            # Create thread with metadata for later cleanup/association
            thread = await ai_client.agents.threads.create(
                metadata={"application_id": application_id}
            )
            self._shared_threads[application_id] = thread.id
            thread_created = True
            logger.info(f"Created shared thread {thread.id} for application {application_id}")
        
        thread_id = self._shared_threads[application_id]
        
        # Set AI Foundry thread ID in logging context
        set_ai_thread_id(thread_id)
        
        add_span_attributes(get_current_span(), {
            "application_id": application_id,
            "agent_id": agent_id,
            "thread_id": thread_id,
            "function_name": function_name,
            "thread_created": thread_created,
            "operation": "get_shared_thread"
        })
        logger.debug(f"Using shared thread {thread_id} for agent {agent_id} in function {function_name}")
        return thread_id

    @trace_async_function("ensure_thread_for_agent")
    async def _ensure_thread_for_agent(self, agent_id: str, ai_client) -> str:
        """Ensure a thread exists for the agent and return thread ID."""
        thread_created = False
        if agent_id not in self.agent_threads:
            thread = await ai_client.agents.threads.create()
            self.agent_threads[agent_id] = thread.id
            thread_created = True
            logger.debug(f"Created persistent thread {thread.id} for agent {agent_id}")
        
        add_span_attributes(get_current_span(), {
            "agent_id": agent_id,
            "thread_id": self.agent_threads[agent_id],
            "thread_created": thread_created,
            "operation": "ensure_thread"
        })
        return self.agent_threads[agent_id]

async def _find_existing_orchestrator_agent(client, application_id: str) -> Optional[Any]:
    """Find an existing orchestrator agent by name pattern."""
    tracer = get_tracer()
    with tracer.start_as_current_span("find_existing_orchestrator_agent") as span:
        agent_name = f"Insights-Orchestrator-Agent-{application_id}"
        add_span_attributes(span, {
            "application_id": application_id,
            "agent_name": agent_name,
            "operation": "search_existing_agent"
        })
        
        try:
            logger.debug(f"Looking for existing orchestrator agent: {agent_name}")
            span.add_event("searching_agents", {"agent_name": agent_name})
            
            # List all agents and find the one with matching name
            agents = client.agents.list_agents()
            agent_count = 0
            
            async for agent in agents:
                agent_count += 1
                if hasattr(agent, 'name') and agent.name == agent_name:
                    logger.debug(f"Found existing orchestrator agent: {agent.id} with name: {agent.name}")
                    add_span_attributes(span, {
                        "found_agent": True,
                        "agent_id": agent.id,
                        "agents_searched": agent_count
                    })
                    span.add_event("existing_agent_found", {
                        "agent_id": agent.id,
                        "agent_name": agent.name,
                        "agents_searched": agent_count
                    })
                    span.set_status(Status(StatusCode.OK))
                    return agent
            
            logger.debug(f"No existing orchestrator agent found with name: {agent_name}")
            add_span_attributes(span, {
                "found_agent": False,
                "agents_searched": agent_count
            })
            span.add_event("no_existing_agent", {
                "agent_name": agent_name,
                "agents_searched": agent_count
            })
            span.set_status(Status(StatusCode.OK))
            return None
            
        except Exception as ex:
            logger.error(f"Error searching for existing orchestrator agent: {ex}")
            span.record_exception(ex)
            span.set_status(Status(StatusCode.ERROR, str(ex)))
            span.add_event("search_failed", {
                "error_type": type(ex).__name__,
                "error_message": str(ex)
            })
            return None

async def _find_existing_responder_agent(client, application_id: str) -> Optional[Any]:
    """Find an existing Insights Agent by name pattern."""
    tracer = get_tracer()
    with tracer.start_as_current_span("find_existing_responder_agent") as span:
        agent_name = f"Responder-Agent-{application_id}"  # Insights Agent uses application_id as name
        add_span_attributes(span, {
            "application_id": application_id,
            "agent_name": agent_name,
            "operation": "search_responder_insights_agent"
        })
        
        try:
            logger.debug(f"Looking for existing Responder agent: {agent_name}")
            span.add_event("searching_responder_agents", {"agent_name": agent_name})
            
            # List all agents and find the one with matching name
            agents = client.agents.list_agents()
            agent_count = 0
            
            async for agent in agents:
                agent_count += 1
                if hasattr(agent, 'name') and agent.name == agent_name:
                    logger.debug(f"Found existing Responder agent: {agent.id} with name: {agent.name}")
                    add_span_attributes(span, {
                        "found_agent": True,
                        "agent_id": agent.id,
                        "agents_searched": agent_count
                    })
                    span.add_event("existing_responder_agent_found", {
                        "agent_id": agent.id,
                        "agent_name": agent.name,
                        "agents_searched": agent_count
                    })
                    span.set_status(Status(StatusCode.OK))
                    return agent
            
            logger.debug(f"No existing Responder agent found with name: {agent_name}")
            add_span_attributes(span, {
                "found_agent": False,
                "agents_searched": agent_count
            })
            span.add_event("no_existing_responder_agent", {
                "agent_name": agent_name,
                "agents_searched": agent_count
            })
            span.set_status(Status(StatusCode.OK))
            return None
            
        except Exception as ex:
            span.record_exception(ex)
            span.set_status(Status(StatusCode.ERROR, str(ex)))
            span.add_event("search_responder_agent_failed", {
                "agent_name": agent_name,
                "error": str(ex)
            })
            logger.error(f"Error searching for existing Responder agent: {ex}")
            return None

@trace_async_function("call_orchestrator")
async def call_orchestrator(
    message: str, 
    application_id: str,
    progress_callback: Optional[callable] = None
) -> Dict[str, Any]:
    """
    Call the orchestrator chat loop with tracing context.
    
    Args:
        message: The orchestrator message
        application_id: Application ID
        progress_callback: Optional async callback function(message: str, percentage: int) 
                          for reporting progress during long-running operations
    """
    tracer = get_tracer()
    with tracer.start_as_current_span("orchestrator_call") as span:
        add_span_attributes(span, {
            "orchestrator.application_id": application_id,
            "orchestrator.cleanup": False,
            "orchestrator.message_length": len(message),
            "orchestrator.message_preview": message[:100] + "..." if len(message) > 100 else message
        })

        # Helper to capture response metadata safely
        def _serialize_response(resp: Any) -> dict:
            meta = {}
            try:
                meta["type"] = type(resp).__name__
                if hasattr(resp, "name"):
                    meta["name"] = resp.name
                if hasattr(resp, "id"):
                    meta["id"] = getattr(resp, "id", None)
                if hasattr(resp, "content"):
                    # Some SDKs expose list-like content
                    c = getattr(resp, "content")
                    if isinstance(c, (list, tuple)):
                        meta["content_item_types"] = [type(x).__name__ for x in c]
                        meta["content_length_sum"] = sum(len(getattr(x, "text", "")) if hasattr(x, "text") else len(str(x)) for x in c)
                    else:
                        meta["content_repr_preview"] = str(c)[:200]
                if hasattr(resp, "tool_calls"):
                    meta["tool_call_count"] = len(getattr(resp, "tool_calls"))
                if hasattr(resp, "status"):
                    meta["status"] = getattr(resp, "status")
            except Exception as ser_ex:
                meta["serialization_error"] = str(ser_ex)
            return meta

        try:
            endpoint = os.getenv("AZURE_EXISTING_AIPROJECT_ENDPOINT")
            if not endpoint:
                span.set_status(Status(StatusCode.ERROR, "AZURE_EXISTING_AIPROJECT_ENDPOINT not set"))
                return {"status": "error", "message": "AZURE_EXISTING_AIPROJECT_ENDPOINT not set"}

            span.add_event("initializing_clients", {"endpoint": endpoint})

            try:
                initialize_tracing_with_context(project_endpoint=endpoint)
                span.add_event("tracing_initialized")
            except Exception as trace_ex:
                span.add_event("tracing_initialization_warning", {"error": str(trace_ex)})
                logger.warning(f"Warning: Could not initialize tracing context: {trace_ex}")

            async with (
                DefaultAzureCredential() as creds,
                AzureAIAgent.create_client(credential=creds, endpoint=endpoint) as client,
            ):
                agent_name = f"Insights-Orchestrator-Agent-{application_id}"
                span.add_event("searching_for_existing_agent", {"agent_name": agent_name})

                existing_agent = await _find_existing_orchestrator_agent(client, application_id)
                if existing_agent:
                    logger.debug(f"Reusing existing orchestrator agent: {existing_agent.id}")
                    agent_definition = existing_agent
                    span.add_event("existing_agent_found", {
                        "agent_id": existing_agent.id,
                        "agent_name": getattr(existing_agent, 'name', 'unknown')
                    })
                else:
                    logger.debug(f"Creating new orchestrator agent: {agent_name}")
                    span.add_event("creating_new_agent", {"agent_name": agent_name})
                    model_deployment = (
                        os.environ.get("AZURE_AI_AGENT_DEPLOYMENT_NAME")
                        or os.environ.get("AZURE_OPENAI_CHAT_DEPLOYMENT")
                        or os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME")
                    )
                    logger.debug(f"Using model deployment: {model_deployment}")

                    add_span_attributes(span, {
                        "agent.model_deployment": model_deployment,
                        "agent.name": agent_name
                    })

                    instructions_file = os.path.join(os.path.dirname(__file__), "agent-instructions", "orchestrator_agent.txt")
                    orchestrator_instructions = load_instructions_from_file(
                        instructions_file,
                        default_instructions="You are an AI assistant that helps to orchestrate tasks related to application intake and assessment."
                    )
                    logger.debug(f"Loaded orchestrator instructions from file: {instructions_file}")

                    agent_definition = await client.agents.create_agent(
                        model=model_deployment,
                        name=agent_name,
                        instructions=orchestrator_instructions,
                    )
                    span.add_event("new_agent_created", {"agent_id": agent_definition.id})

                span.add_event("creating_semantic_kernel_agent")
                agent = AzureAIAgent(
                    client=client,
                    definition=agent_definition,
                    plugins=[orchplugin(progress_callback=progress_callback)],
                )
                # Log plugin function names
                try:
                    plugin_names = [p.__class__.__name__ for p in agent.plugins]
                    span.add_event("plugins_attached", {"plugins": ", ".join(plugin_names)})
                except Exception:
                    pass

                record_agent_interaction(span, agent_definition.id, operation_type="orchestrator_invoke")

                thread = None
                response_content = ""
                message_count = 0

                span.add_event("starting_agent_invocation", {
                    "message": message[:200] + "..." if len(message) > 200 else message
                })
                logger.info(f"Sending message to orchestrator agent (length: {len(message)} chars): {message[:200]}{'...' if len(message) > 200 else ''}")

                # Direct agent invocation without retry logic
                logger.info(f"Invoking orchestrator agent for application {application_id}")
                
                span.add_event("agent_invoke", {
                    "application_id": application_id
                })
                
                try:
                    # Direct streaming loop without retry wrapper
                    # Add heartbeat timeout detection to prevent infinite hanging
                    import time
                    last_response_time = time.time()
                    heartbeat_timeout = 600  # 10 minutes without response = stuck
                    
                    async for response in agent.invoke(
                        messages=message,
                        thread=thread,
                        parallel_tool_calls=False,
                        timeout=1800  # 30 minutes for long-running operations
                    ):
                        try:
                            response_content = str(response.content) if hasattr(response, 'content') else str(response)
                        except Exception as rc_ex:
                            logger.error(f"Failed to extract response content: {rc_ex}")
                            response_content = f"<content extraction failed: {rc_ex}>"

                        thread = response.thread
                        message_count += 1
                        last_response_time = time.time()  # Update heartbeat

                        # Enhanced logging with serialized metadata
                        meta = _serialize_response(response)
                        logger.info(f"Orchestrator agent response #{message_count}: {response_content}")
                        logger.debug(f"Orchestrator response metadata: {meta}")

                        span.add_event("agent_response_received", {
                            "response_name": meta.get("name", getattr(response, 'name', 'unknown')),
                            "response_length": len(response_content),
                            "message_count": message_count,
                            "thread_id": getattr(thread, 'id', 'unknown') if thread else None,
                            "meta": json.dumps(meta)[:500]
                        })
                        
                        # Check for heartbeat timeout (agent stuck without responses)
                        elapsed_since_last = time.time() - last_response_time
                        if elapsed_since_last > heartbeat_timeout:
                            logger.warning(f"Orchestrator agent heartbeat timeout - no response for {elapsed_since_last:.0f}s")
                            span.add_event("heartbeat_timeout_detected", {
                                "elapsed_seconds": elapsed_since_last,
                                "message_count": message_count,
                                "last_response_preview": response_content[:200]
                            })
                            break  # Exit loop to prevent infinite hang
                    
                    logger.debug(f"Orchestrator agent invocation completed successfully for application {application_id}")

                except KeyError as ke:
                    # Specific handling for agent API KeyError issues
                    missing_key = ke.args[0] if ke.args else "unknown"
                    logger.error(f"KeyError during orchestrator streaming - missing key: {missing_key}", exc_info=True)
                    span.record_exception(ke)
                    span.add_event("keyerror_in_stream", {
                        "missing_key": missing_key,
                        "hint": "Check azure-ai-agents / azure-ai-projects version compatibility and search tool configuration"
                    })
                    add_span_attributes(span, {
                        "orchestrator.success": False,
                        "orchestrator.error_type": "KeyError",
                        "orchestrator.missing_key": missing_key
                    })
                    span.set_status(Status(StatusCode.ERROR, f"Missing key: {missing_key}"))
                    return {
                        "status": "error",
                        "application_id": application_id,
                        "message": f"Missing key in agent stream: {missing_key}",
                        "error_type": "KeyError",
                        "missing_key": missing_key,
                        "suggestion": "Upgrade azure-ai-agents & azure-ai-projects to matching beta versions and verify search tool asset registration."
                    }

                except AgentInvokeException as agent_ex:
                    error_msg = str(agent_ex)
                    thread_id = "unknown"
                    if "thread" in error_msg:
                        import re
                        thread_match = re.search(r'thread[`\s]+([a-zA-Z0-9_-]+)', error_msg)
                        if thread_match:
                            thread_id = thread_match.group(1)

                    logger.error(f"Agent invocation failed for application {application_id}: {error_msg}")
                    span.record_exception(agent_ex)
                    span.add_event("agent_invocation_failed", {
                        "error": error_msg[:500],
                        "thread_id": thread_id,
                        "agent_name": agent_name,
                        "application_id": application_id
                    })
                    add_span_attributes(span, {
                        "orchestrator.success": False,
                        "orchestrator.error_type": "AgentInvokeException",
                        "orchestrator.thread_id": thread_id
                    })
                    record_error_details(
                        span=span,
                        error_type="AgentInvokeException",
                        error_message=error_msg,
                        error_code="RunStatus.FAILED",
                        is_retryable=False
                    )
                    span.set_status(Status(StatusCode.ERROR, "Agent invocation failed"))
                    return {
                        "status": "error",
                        "application_id": application_id,
                        "message": f"Azure AI Foundry agent failed: {error_msg}",
                        "error_type": "AgentInvokeException",
                        "thread_id": thread_id,
                        "is_retryable": False
                    }

                except Exception as stream_ex:
                    # Generic fallback for streaming errors
                    error_msg = str(stream_ex)
                    error_type = type(stream_ex).__name__
                    
                    # Check for specific error types for better diagnostics
                    is_http_transport_error = "HTTP transport" in error_msg or "transport has already been closed" in error_msg.lower()
                    is_thread_not_found = error_type == "ResourceNotFoundError" and "thread" in error_msg.lower()
                    
                    # If thread not found error occurs AFTER we have response content, treat as warning not error
                    # This indicates the thread was cleaned up during streaming, but we already got the results
                    if is_thread_not_found and response_content:
                        logger.warning(f"Thread cleanup race condition for application {application_id}: {stream_ex}")
                        logger.warning("Thread was deleted during streaming, but response was already received. Continuing with success.")
                        span.add_event("thread_cleanup_race_condition", {
                            "error_type": error_type,
                            "error_message": error_msg[:500],
                            "has_response": len(response_content) > 0,
                            "response_length": len(response_content),
                            "application_id": application_id,
                            "severity": "warning"
                        })
                        # Don't fail the operation - we have the results already
                        # Break out of exception handling to continue to normal success flow
                    else:
                        # Real streaming error - log and return error status
                        logger.error(f"Streaming exception for application {application_id}: {stream_ex}", exc_info=True)
                        span.record_exception(stream_ex)
                        
                        span.add_event("stream_exception", {
                            "error_type": error_type,
                            "error_message": error_msg[:500],
                            "is_http_transport_error": is_http_transport_error,
                            "application_id": application_id
                        })
                        add_span_attributes(span, {
                            "orchestrator.success": False,
                            "orchestrator.error_type": error_type,
                            "orchestrator.is_http_transport_error": is_http_transport_error
                        })
                        span.set_status(Status(StatusCode.ERROR, error_msg))
                        
                        # Provide helpful context for HTTP transport errors
                        if is_http_transport_error:
                            logger.warning("HTTP transport error detected. This may indicate a connection timeout or premature client closure.")
                            logger.warning("Suggestion: Check if long-running operations are completing within timeout limits.")
                        
                        return {
                            "status": "error",
                            "application_id": application_id,
                            "message": f"Streaming error: {stream_ex}",
                            "error_type": error_type
                        }

                add_span_attributes(span, {
                    "orchestrator.total_messages": message_count,
                    "orchestrator.response_length": len(response_content),
                    "orchestrator.success": True
                })
                span.add_event("orchestrator_completed_successfully", {
                    "total_messages": message_count,
                    "final_response_preview": response_content[:200] + "..." if len(response_content) > 200 else response_content
                })

                if thread:
                    with tracer.start_as_current_span("cleanup_orchestrator_thread") as thread_span:
                        try:
                            thread_id = getattr(thread, 'id', None)
                            if thread_id:
                                await client.agents.threads.delete(thread_id)
                                logger.debug(f"Deleted orchestrator thread: {thread_id}")
                                thread_span.add_event("orchestrator_thread_deleted", {"thread_id": thread_id})
                                add_span_attributes(thread_span, {"thread.id": thread_id, "thread.cleanup_status": "success"})
                            else:
                                logger.debug("No thread ID found for cleanup")
                                thread_span.add_event("no_thread_id_for_cleanup")
                        except Exception as thread_ex:
                            logger.warning(f"Warning: Could not delete orchestrator thread: {thread_ex}")
                            thread_span.record_exception(thread_ex)
                            thread_span.add_event("thread_cleanup_failed", {"error": str(thread_ex)})

                span.set_status(Status(StatusCode.OK))
                orchestrator_result = {
                    "status": "success",
                    "application_id": application_id,
                    "message": response_content,
                    "agent_name": agent_name,
                    "agent_id": agent_definition.id if hasattr(agent_definition, 'id') else None,
                    "reused_existing": existing_agent is not None
                }
                logger.info(f"Orchestrator agent completed successfully for application {application_id}: {orchestrator_result}")
                return orchestrator_result

        except Exception as outer_ex:
            # High-level fallback
            error_result = {
                "status": "error",
                "message": str(outer_ex),
                "error_type": type(outer_ex).__name__
            }
            logger.error(f"Orchestrator agent failed for application {application_id}: {error_result}", exc_info=True)
            span.record_exception(outer_ex)
            span.set_status(Status(StatusCode.ERROR, str(outer_ex)))
            span.add_event("orchestrator_outer_error", {
                "error_type": type(outer_ex).__name__,
                "error_message": str(outer_ex)
            })
            return error_result

def get_confidence_scores(application_id: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve stored confidence scores for an application.
    
    Args:
        application_id: Application identifier
        
    Returns:
        Dictionary containing 'table_confidence_scores' and 'overall_average_confidence_score',
        or None if no scores are stored for this application
    """
    return orchplugin._confidence_scores.get(application_id)


@trace_async_function("cleanup_agents")
async def cleanup_agents(application_id: str) -> Dict[str, Any]:
    """Complete cleanup for application assessment: orchestrator agent, threads, and search index."""
    tracer = get_tracer()
    with tracer.start_as_current_span("cleanup_agents_complete") as span:
        add_span_attributes(span, {
            "application_id": application_id,
            "operation": "complete_cleanup"
        })
        
        cleanup_results = {
            "status": "partial_success",
            "application_id": application_id,
            "orchestrator_agent": {"status": "not_found"},
            "search_index": {"status": "not_found"},
            "errors": []
        }
        
        try:
            endpoint = os.getenv("AZURE_EXISTING_AIPROJECT_ENDPOINT")
            if not endpoint:
                error_msg = "AZURE_EXISTING_AIPROJECT_ENDPOINT not set"
                span.set_status(Status(StatusCode.ERROR, error_msg))
                return {"status": "error", "message": error_msg}
            
            async with (
                DefaultAzureCredential(exclude_shared_token_cache_credential=True) as creds,
                AzureAIAgent.create_client(credential=creds, endpoint=endpoint) as client,
            ):
                span.add_event("cleanup_started", {"application_id": application_id})
                
                # 1. Cleanup orchestrator agent
                with tracer.start_as_current_span("cleanup_orchestrator_agent") as agent_span:
                    try:
                        existing_agent = await _find_existing_orchestrator_agent(client, application_id)
                        
                        if existing_agent:
                            agent_id = existing_agent.id
                            logger.debug(f"Found existing orchestrator agent: {agent_id}")
                            
                            # Delete the orchestrator agent
                            await client.agents.delete_agent(agent_id)
                            logger.debug(f"Deleted orchestrator agent: {agent_id}")
                            
                            cleanup_results["orchestrator_agent"] = {
                                "status": "success",
                                "agent_id": agent_id,
                                "agent_name": getattr(existing_agent, 'name', 'unknown')
                            }
                            
                            agent_span.add_event("orchestrator_agent_deleted", {
                                "agent_id": agent_id,
                                "agent_name": getattr(existing_agent, 'name', 'unknown')
                            })
                        else:
                            logger.warning(f"No existing orchestrator agent found for application: {application_id}")
                            cleanup_results["orchestrator_agent"] = {"status": "not_found"}
                            
                    except Exception as agent_ex:
                        cleanup_results["orchestrator_agent"] = {"status": "error", "message": str(agent_ex)}
                        cleanup_results["errors"].append(f"Orchestrator agent cleanup failed: {str(agent_ex)}")
                        agent_span.record_exception(agent_ex)
                        agent_span.set_status(Status(StatusCode.ERROR, str(agent_ex)))
                
                # Note: Thread cleanup removed - threads are now cleaned up after each orchestrator execution
                
                # 2. Cleanup search index
                with tracer.start_as_current_span("cleanup_search_index") as index_span:
                    try:
                        # Get search service configuration
                        search_endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
                        api_key = os.getenv("AZURE_SEARCH_ADMIN_KEY") or os.getenv("AZURE_SEARCH_API_KEY")
                        
                        if search_endpoint:
                            from azure.search.documents.indexes import SearchIndexClient
                            from azure.core.credentials import AzureKeyCredential
                            from azure.identity import DefaultAzureCredential as SyncDefaultAzureCredential
                            
                            # Use API key if available, otherwise AAD
                            if api_key:
                                search_cred = AzureKeyCredential(api_key)
                            else:
                                search_cred = SyncDefaultAzureCredential(exclude_shared_token_cache_credential=True)
                            
                            search_client = SearchIndexClient(endpoint=search_endpoint, credential=search_cred)
                            
                            # Index name is the same as application_id
                            index_name = application_id.lower()  # Ensure lowercase
                            
                            try:
                                # Check if index exists
                                search_client.get_index(index_name)
                                
                                # Delete the index
                                search_client.delete_index(index_name)
                                logger.debug(f"Deleted search index: {index_name}")
                                
                                cleanup_results["search_index"] = {
                                    "status": "success",
                                    "index_name": index_name
                                }
                                
                                index_span.add_event("search_index_deleted", {
                                    "index_name": index_name,
                                    "search_endpoint": search_endpoint
                                })
                                
                            except Exception as idx_ex:
                                if "not found" in str(idx_ex).lower():
                                    cleanup_results["search_index"] = {"status": "not_found", "index_name": index_name}
                                    logger.error(f"Search index {index_name} not found (already deleted or never existed)")
                                else:
                                    raise idx_ex
                        else:
                            cleanup_results["search_index"] = {"status": "skipped", "message": "No search endpoint configured"}
                            logger.warning("No search endpoint configured, skipping index cleanup")
                            
                    except Exception as index_ex:
                        cleanup_results["search_index"] = {"status": "error", "message": str(index_ex)}
                        cleanup_results["errors"].append(f"Search index cleanup failed: {str(index_ex)}")
                        index_span.record_exception(index_ex)
                        index_span.set_status(Status(StatusCode.ERROR, str(index_ex)))
                
                # Determine overall status
                success_count = sum(1 for component in [cleanup_results["orchestrator_agent"], cleanup_results["search_index"]] 
                                  if component["status"] in ["success", "not_found"])
                
                if success_count == 2 and not cleanup_results["errors"]:
                    cleanup_results["status"] = "success"
                    span.set_status(Status(StatusCode.OK))
                elif cleanup_results["errors"]:
                    cleanup_results["status"] = "partial_success"
                    span.add_event("cleanup_completed_with_errors", {"error_count": len(cleanup_results["errors"])})
                else:
                    cleanup_results["status"] = "failed"
                    span.set_status(Status(StatusCode.ERROR, "Multiple cleanup operations failed"))
                
                add_span_attributes(span, {
                    "cleanup.orchestrator_status": cleanup_results["orchestrator_agent"]["status"],
                    "cleanup.index_status": cleanup_results["search_index"]["status"],
                    "cleanup.overall_status": cleanup_results["status"]
                })
                
                span.add_event("cleanup_completed", {
                    "overall_status": cleanup_results["status"],
                    "components_cleaned": success_count,
                    "errors": len(cleanup_results["errors"])
                })
                
                # Clear stored confidence scores and shared threads for this application
                if application_id in orchplugin._confidence_scores:
                    del orchplugin._confidence_scores[application_id]
                    logger.debug(f"Cleared confidence scores for application {application_id}")
                
                if application_id in orchplugin._shared_threads:
                    del orchplugin._shared_threads[application_id]
                    logger.debug(f"Cleared shared thread for application {application_id}")
                
                logger.debug(f"Cleanup completed for application {application_id}: {cleanup_results['status']}")
                return cleanup_results
                
        except Exception as ex:
            span.record_exception(ex)
            span.set_status(Status(StatusCode.ERROR, str(ex)))
            logger.error(f"Failed to cleanup agents for application {application_id}: {ex}")
            return {"status": "error", "message": str(ex), "application_id": application_id}


@trace_async_function("delete_all_app_data")
async def delete_all_app_data(application_id: str) -> Dict[str, Any]:
    """
    Comprehensive deletion of all application data including:
    - All agents (orchestrator, ASR, design, responder, architecture, security, diagram)
    - All threads associated with the agents
    - Storage container for the application [This is not getting deleted currently]
    - Search index for the application
    
    Args:
        application_id: The application ID to delete all data for
        
    Returns:
        Dictionary containing deletion status for each component
    """
    tracer = get_tracer()
    with tracer.start_as_current_span("delete_all_app_data") as span:
        add_span_attributes(span, {
            "application_id": application_id,
            "operation": "delete_all_app_data"
        })
        
        deletion_results = {
            "status": "partial_success",
            "application_id": application_id,
            "agents": {},
            "threads": {"status": "not_found", "deleted_count": 0},
            # "storage_container": {"status": "not_found"},
            "search_index": {"status": "not_found"},
            "errors": []
        }
        
        # Agent name patterns to search and delete
        agent_patterns = [
            ("orchestrator", f"OrchestratorAgent{application_id}"),
            ("asr", f"ASRAgent{application_id}"),
            ("design", f"DesignAgent{application_id}"),
            ("responder", f"ResponderAgent{application_id}"),
            ("architecture", f"ArchitectureAgent"),  # May have different naming
            ("security", f"SecurityAgent"),
            ("diagram", f"DiagramAgent"),
        ]
        
        try:
            endpoint = os.getenv("AZURE_EXISTING_AIPROJECT_ENDPOINT")
            if not endpoint:
                error_msg = "AZURE_EXISTING_AIPROJECT_ENDPOINT not set"
                span.set_status(Status(StatusCode.ERROR, error_msg))
                return {"status": "error", "message": error_msg}
            
            logger.debug(f"Creating DefaultAzureCredential for delete_all_app_data")
            logger.debug(f"Endpoint: {endpoint}")
            
            async with (
                DefaultAzureCredential(exclude_shared_token_cache_credential=True) as creds,
                AzureAIAgent.create_client(credential=creds, endpoint=endpoint) as client,
            ):
                logger.debug(f"AzureAIAgent client created successfully for deletion")
                span.add_event("delete_started", {"application_id": application_id})
                
                # 1. Delete all agents
                with tracer.start_as_current_span("delete_all_agents") as agents_span:
                    try:
                        # List all agents and filter by application_id patterns
                        agents_response = client.agents.list_agents()
                        
                        deleted_agents = []
                        async for agent in agents_response:
                            agent_name = getattr(agent, 'name', '') or ''
                            agent_id = getattr(agent, 'id', '')
                            
                            # Check if agent belongs to this application
                            should_delete = False
                            agent_type = None
                            
                            for pattern_type, pattern in agent_patterns:
                                if pattern in agent_name or application_id in agent_name:
                                    should_delete = True
                                    agent_type = pattern_type
                                    break
                            
                            if should_delete:
                                try:
                                    await client.agents.delete_agent(agent_id)
                                    deleted_agents.append({
                                        "agent_id": agent_id,
                                        "agent_name": agent_name,
                                        "agent_type": agent_type
                                    })
                                    logger.debug(f"Deleted {agent_type} agent: {agent_name} ({agent_id})")
                                except Exception as del_ex:
                                    deletion_results["errors"].append(f"Failed to delete agent {agent_name}: {str(del_ex)}")
                        
                        deletion_results["agents"] = {
                            "status": "success" if deleted_agents else "not_found",
                            "deleted_count": len(deleted_agents),
                            "deleted_agents": deleted_agents
                        }
                        
                        agents_span.add_event("agents_deleted", {
                            "deleted_count": len(deleted_agents)
                        })
                        
                    except Exception as agents_ex:
                        deletion_results["agents"] = {"status": "error", "message": str(agents_ex)}
                        deletion_results["errors"].append(f"Agent deletion failed: {str(agents_ex)}")
                        agents_span.record_exception(agents_ex)
                
                # 2. Delete threads (list and delete threads associated with application)
                with tracer.start_as_current_span("delete_threads") as threads_span:
                    try:
                        deleted_threads = 0
                        
                        # First, try to delete from in-memory shared threads cache
                        if application_id in orchplugin._shared_threads:
                            thread_id = orchplugin._shared_threads[application_id]
                            try:
                                await client.agents.threads.delete(thread_id)
                                deleted_threads += 1
                                logger.debug(f"Deleted shared thread from cache: {thread_id}")
                            except Exception as thread_ex:
                                if "not found" not in str(thread_ex).lower():
                                    deletion_results["errors"].append(f"Failed to delete thread {thread_id}: {str(thread_ex)}")
                            del orchplugin._shared_threads[application_id]
                        
                        # Also try to list all threads and delete those with matching metadata
                        # Note: Threads created without metadata cannot be associated with apps
                        try:
                            threads_found = 0
                            async for thread in client.agents.threads.list():
                                threads_found += 1
                                thread_metadata = getattr(thread, 'metadata', {}) or {}
                                thread_app_id = thread_metadata.get('application_id', '')
                                
                                # Delete if thread has matching application_id in metadata
                                if thread_app_id == application_id:
                                    try:
                                        await client.agents.threads.delete(thread.id)
                                        deleted_threads += 1
                                        logger.debug(f"Deleted thread with matching metadata: {thread.id}")
                                    except Exception as del_ex:
                                        if "not found" not in str(del_ex).lower():
                                            logger.warning(f"Failed to delete thread {thread.id}: {del_ex}")
                            
                            logger.debug(f"Scanned {threads_found} threads, deleted {deleted_threads} for app {application_id}")
                        except Exception as list_ex:
                            logger.warning(f"Could not list threads: {list_ex}")
                        
                        deletion_results["threads"] = {
                            "status": "success" if deleted_threads > 0 else "not_found",
                            "deleted_count": deleted_threads
                        }
                        
                        threads_span.add_event("threads_deleted", {"deleted_count": deleted_threads})
                        
                    except Exception as threads_ex:
                        deletion_results["threads"] = {"status": "error", "message": str(threads_ex)}
                        deletion_results["errors"].append(f"Thread deletion failed: {str(threads_ex)}")
                        threads_span.record_exception(threads_ex)
                
                # # 3. Delete storage container
                # with tracer.start_as_current_span("delete_storage_container") as storage_span:
                #     try:
                #         account_url = os.getenv("AZURE_STORAGE_ACCOUNT_URL") or os.getenv("AZURE_BLOB_ACCOUNT_URL")
                #         tables_url = os.getenv("AZURE_TABLES_ACCOUNT_URL")
                        
                #         if not account_url and tables_url:
                #             account_url = tables_url.replace(".table.", ".blob.")
                        
                #         if account_url:
                #             bsc = get_blob_service_client(account_url)
                #             container_name = str(application_id).lower()
                            
                #             try:
                #                 container_client = bsc.get_container_client(container_name)
                #                 container_client.delete_container()
                #                 logger.info(f"Deleted storage container: {container_name}")
                                
                #                 deletion_results["storage_container"] = {
                #                     "status": "success",
                #                     "container_name": container_name
                #                 }
                                
                #                 storage_span.add_event("storage_container_deleted", {
                #                     "container_name": container_name
                #                 })
                                
                #             except Exception as container_ex:
                #                 if "ContainerNotFound" in str(container_ex) or "not found" in str(container_ex).lower():
                #                     deletion_results["storage_container"] = {
                #                         "status": "not_found",
                #                         "container_name": container_name
                #                     }
                #                     logger.info(f"Storage container {container_name} not found (already deleted or never existed)")
                #                 else:
                #                     raise container_ex
                #         else:
                #             deletion_results["storage_container"] = {
                #                 "status": "skipped",
                #                 "message": "No storage account URL configured"
                #             }
                            
                #     except Exception as storage_ex:
                #         deletion_results["storage_container"] = {"status": "error", "message": str(storage_ex)}
                #         deletion_results["errors"].append(f"Storage container deletion failed: {str(storage_ex)}")
                #         storage_span.record_exception(storage_ex)
                
                # 4. Delete search index
                with tracer.start_as_current_span("delete_search_index") as index_span:
                    try:
                        search_endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
                        api_key = os.getenv("AZURE_SEARCH_ADMIN_KEY") or os.getenv("AZURE_SEARCH_API_KEY")
                        
                        if search_endpoint:
                            from azure.search.documents.indexes import SearchIndexClient
                            from azure.core.credentials import AzureKeyCredential
                            from azure.identity import DefaultAzureCredential as SyncDefaultAzureCredential
                            
                            if api_key:
                                logger.debug("Using AzureKeyCredential for search index deletion")
                                search_cred = AzureKeyCredential(api_key)
                            else:
                                logger.debug("Using SyncDefaultAzureCredential (managed identity) for search index deletion")
                                search_cred = SyncDefaultAzureCredential(exclude_shared_token_cache_credential=True)
                            
                            logger.debug(f"Creating SearchIndexClient for deletion with endpoint: {search_endpoint}")
                            search_client = SearchIndexClient(endpoint=search_endpoint, credential=search_cred)
                            index_name = application_id.lower()
                            
                            try:
                                logger.debug(f"Attempting to get index '{index_name}' for deletion")
                                search_client.get_index(index_name)
                                logger.debug(f"Index '{index_name}' exists, proceeding with deletion")
                                search_client.delete_index(index_name)
                                logger.info(f"Deleted search index: {index_name}")
                                
                                deletion_results["search_index"] = {
                                    "status": "success",
                                    "index_name": index_name
                                }
                                
                                index_span.add_event("search_index_deleted", {
                                    "index_name": index_name
                                })
                                
                            except Exception as idx_ex:
                                error_str = str(idx_ex)
                                is_forbidden = "Forbidden" in error_str or "403" in error_str
                                
                                if is_forbidden:
                                    logger.error(f"=== 403 FORBIDDEN ERROR DETECTED ===")
                                    logger.error(f"Location: delete_all_app_data (search_index deletion)")
                                    logger.error(f"Application ID: {application_id}")
                                    logger.error(f"Index Name: {index_name}")
                                    logger.error(f"Error Type: {type(idx_ex).__name__}")
                                    logger.error(f"Error Message: {error_str}")
                                    logger.error(f"Full Exception: {repr(idx_ex)}")
                                    logger.error(f"Search Endpoint: {search_endpoint}")
                                    logger.error(f"Using API Key: {bool(api_key)}")
                                    raise idx_ex
                                elif "not found" in error_str.lower():
                                    deletion_results["search_index"] = {
                                        "status": "not_found",
                                        "index_name": index_name
                                    }
                                    logger.info(f"Search index {index_name} not found (already deleted or never existed)")
                                else:
                                    raise idx_ex
                        else:
                            deletion_results["search_index"] = {
                                "status": "skipped",
                                "message": "No search endpoint configured"
                            }
                            
                    except Exception as index_ex:
                        deletion_results["search_index"] = {"status": "error", "message": str(index_ex)}
                        deletion_results["errors"].append(f"Search index deletion failed: {str(index_ex)}")
                        index_span.record_exception(index_ex)
                
                # Clear stored confidence scores
                if application_id in orchplugin._confidence_scores:
                    del orchplugin._confidence_scores[application_id]
                    logger.debug(f"Cleared confidence scores for application {application_id}")
                
                # Determine overall status
                component_statuses = [
                    deletion_results["agents"].get("status", "not_found"),
                    deletion_results["threads"].get("status", "not_found"),
                    # deletion_results["storage_container"].get("status", "not_found"),
                    deletion_results["search_index"].get("status", "not_found")
                ]
                
                success_count = sum(1 for s in component_statuses if s in ["success", "not_found", "skipped"])
                total_components = len(component_statuses)
                
                if success_count == total_components and not deletion_results["errors"]:
                    deletion_results["status"] = "success"
                    span.set_status(Status(StatusCode.OK))
                elif deletion_results["errors"]:
                    deletion_results["status"] = "partial_success"
                    span.add_event("deletion_completed_with_errors", {"error_count": len(deletion_results["errors"])})
                else:
                    deletion_results["status"] = "failed"
                    span.set_status(Status(StatusCode.ERROR, "Multiple deletion operations failed"))
                
                add_span_attributes(span, {
                    "deletion.agents_status": deletion_results["agents"].get("status"),
                    "deletion.threads_status": deletion_results["threads"].get("status"),
                    # "deletion.storage_status": deletion_results["storage_container"].get("status"),
                    "deletion.index_status": deletion_results["search_index"].get("status"),
                    "deletion.overall_status": deletion_results["status"]
                })
                
                span.add_event("deletion_completed", {
                    "overall_status": deletion_results["status"],
                    "components_deleted": success_count,
                    "errors": len(deletion_results["errors"])
                })
                
                logger.info(f"Delete all app data completed for application {application_id}: {deletion_results['status']}")
                return deletion_results
                
        except Exception as ex:
            span.record_exception(ex)
            span.set_status(Status(StatusCode.ERROR, str(ex)))
            logger.error(f"Failed to delete all app data for application {application_id}: {ex}")
            return {"status": "error", "message": str(ex), "application_id": application_id}