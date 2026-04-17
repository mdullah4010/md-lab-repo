"""
Kubernetes Discovery Agent - Class-Based Implementation
This module provides a complete, standalone implementation of the Kubernetes Discovery Agent
with Azure AI Search integration and cluster discovery report generation capabilities.

Usage:
    from kubernetes_discovery_agent import KubernetesDiscoveryAgent
    
    # Initialize the agent
    agent = KubernetesDiscoveryAgent()
    
    # Create or get agent for an application
    agent_id = await agent.initialize_agent(app_id="my-cluster")
    
    # Generate discovery report
    report = await agent.generate_discovery_report(app_id="my-cluster")
    print(report)
"""

import os
import argparse
import asyncio
import aiohttp
import logging
import sys
import datetime
import json
from typing import Optional, Dict, Any, Callable
from pathlib import Path

# Azure SDK imports
from azure.ai.projects.aio import AIProjectClient
from azure.ai.projects.models import ConnectionType
from azure.identity.aio import DefaultAzureCredential
from azure.identity import DefaultAzureCredential as SyncDefaultAzureCredential
from azure.ai.agents.models import AzureAISearchTool, AzureAISearchQueryType
from azure.storage.blob.aio import BlobServiceClient as AsyncBlobServiceClient
from azure.storage.blob import ContentSettings
from dotenv import load_dotenv
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

# Import tracing and logging configuration
from agents.tracing_config import (
    get_tracer,
    trace_async_function,
    add_span_attributes,
)
from agents.logging_config import get_logger

# Import utility functions
from agents.utils.common_utils import (
    get_storage_account_url,
    get_async_blob_service_client,
    upload_file_to_container_async,
    load_instructions_from_file
)
from agents.utils.agent_utils import (
    find_existing_agent,
    check_index_exists,
    build_agent_name,
    get_search_connection,
    create_project_index,
    configure_search_tool,
    SearchToolConfig,
    create_agent_with_search_tool
)

load_dotenv()
logger = get_logger(__name__)


class KubernetesDiscoveryAgentConfig:
    """Configuration for the Kubernetes Discovery Agent"""
    
    def __init__(self):
        """Initialize configuration from environment variables"""
        # Required settings
        self.ai_project_endpoint = os.getenv("AZURE_EXISTING_AIPROJECT_ENDPOINT")
        if not self.ai_project_endpoint:
            raise ValueError("AZURE_EXISTING_AIPROJECT_ENDPOINT environment variable is required")
        
        self.ai_deployment_model_name = os.getenv("AZURE_AI_AGENT_DEPLOYMENT_NAME", "gpt-4o")
        
        # Azure Storage settings
        self.azure_storage_account_url = os.getenv("AZURE_STORAGE_ACCOUNT_URL") or os.getenv("AZURE_BLOB_ACCOUNT_URL")
        self.azure_storage_account_name = os.getenv("AZURE_STORAGE_ACCOUNT_NAME")
        if not self.azure_storage_account_url and self.azure_storage_account_name:
            self.azure_storage_account_url = f"https://{self.azure_storage_account_name}.blob.core.windows.net"
        
        # Azure AI Search settings
        self.azure_search_endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
        self.azure_search_api_key = os.getenv("AZURE_SEARCH_ADMIN_KEY") or os.getenv("AZURE_SEARCH_API_KEY")
        self.azure_search_semantic_config = os.getenv("AZURE_SEARCH_SEMANTIC_CONFIG")
        self.azure_ai_search_filter = os.getenv("AZURE_AI_SEARCH_FILTER")
        
        # Instructions file path
        self.instructions_file = os.path.join(
            os.path.dirname(__file__),
            "agent-instructions",
            "kubernetes_discovery_instructions.txt"
        )
        
        # Prompts file path
        self.prompts_file = os.path.join(
            os.path.dirname(__file__),
            "agent-instructions",
            "kubernetes_discovery_prompts.json"
        )
        
    def validate(self) -> bool:
        """Validate that required configuration is present"""
        if not self.ai_project_endpoint:
            logger.error("AZURE_EXISTING_AIPROJECT_ENDPOINT is required")
            return False
        if not self.ai_deployment_model_name:
            logger.error("AZURE_AI_AGENT_DEPLOYMENT_NAME is required")
            return False
        return True


class KubernetesDiscoveryAgent:
    """
    Kubernetes Discovery Agent with full backend functionality.
    
    This agent provides:
    - Azure AI Search integration for kubernetes documentation access
    - Cluster discovery and analysis capabilities
    - Report generation and upload to Azure Blob Storage
    - Agent lifecycle management (create/update/reuse)
    """
    
    def __init__(self, config: Optional[KubernetesDiscoveryAgentConfig] = None):
        """
        Initialize the Kubernetes Discovery Agent
        
        Args:
            config: Optional configuration object. If not provided, will be created from environment.
        """
        self.config = config or KubernetesDiscoveryAgentConfig()
        if not self.config.validate():
            raise ValueError("Invalid configuration")
        
        # Azure clients (initialized on first use)
        self._credential: Optional[DefaultAzureCredential] = None
        self._ai_project_client: Optional[AIProjectClient] = None
        self._blob_service_client: Optional[AsyncBlobServiceClient] = None
        self._agent = None
        self._agent_id = None
        
        logger.info("Kubernetes Discovery Agent initialized")
    
    async def _get_credential(self) -> DefaultAzureCredential:
        """Get or create Azure credential"""
        if self._credential is None:
            self._credential = DefaultAzureCredential(exclude_shared_token_cache_credential=True)
        return self._credential
    
    async def _get_ai_project_client(self) -> AIProjectClient:
        """Get or create AI Project client"""
        if self._ai_project_client is None:
            credential = await self._get_credential()
            self._ai_project_client = AIProjectClient(
                credential=credential,
                endpoint=self.config.ai_project_endpoint
            )
            logger.info("AI Project client initialized")
        return self._ai_project_client
    
    async def _get_blob_service_client(self) -> AsyncBlobServiceClient:
        """Get or create Blob Service client"""
        if self._blob_service_client is None:
            if not self.config.azure_storage_account_url:
                raise ValueError("Azure Storage Account URL not configured")
            credential = await self._get_credential()
            self._blob_service_client = AsyncBlobServiceClient(
                account_url=self.config.azure_storage_account_url,
                credential=credential
            )
            logger.info("Blob Service client initialized")
        return self._blob_service_client
    
    async def _find_existing_agent(self, agent_name: str) -> Optional[Any]:
        """
        Find an existing agent by name using utility function.
        
        Args:
            agent_name: Name of the agent to find
            
        Returns:
            Agent object if found, None otherwise
        """
        client = await self._get_ai_project_client()
        return await find_existing_agent(client, agent_name)
    
    async def _check_index_exists(self, index_name: str) -> Optional[bool]:
        """
        Check if a search index exists using utility function.
        
        Args:
            index_name: Name of the index to check
            
        Returns:
            True if exists, False if not found, None if check failed
        """
        # Use the utility function
        return check_index_exists(index_name)
    
    async def _configure_search_tool(self, app_id: str) -> Optional[AzureAISearchTool]:
        """
        Configure Azure AI Search tool for the agent.
        
        Args:
            app_id: Application ID (used as index name)
            
        Returns:
            AzureAISearchTool if configured successfully, None otherwise
        """
        tracer = get_tracer()
        client = await self._get_ai_project_client()
        
        with tracer.start_as_current_span("search_tool_configuration") as tool_span:
            try:
                index_name = app_id
                
                add_span_attributes(tool_span, {
                    "search.index_name": index_name
                })
                
                # Check if index exists
                exists = await self._check_index_exists(index_name)
                logger.info(f"Index '{index_name}' exists check result: {exists}")
                
                if exists is True or exists is None:
                    # Build kubernetes-specific filter
                    kubernetes_filter = self.build_kubernetes_input_filter(app_id)
                    
                    # Configure search tool using utility
                    search_config = SearchToolConfig(
                        index_name=index_name,
                        query_type=AzureAISearchQueryType.SEMANTIC,
                        top_k=50,
                        filter=kubernetes_filter,
                        field_mapping={
                            "contentFields": ["content"],
                            "titleField": "title",
                            "urlField": "url"
                        }
                    )
                    
                    search_tool = await configure_search_tool(client, search_config)
                    if search_tool:
                        logger.debug(f"Configured Azure AI Search tool with filter: {kubernetes_filter}")
                        return search_tool
                    else:
                        # Fallback to legacy approach
                        logger.warning("Failed to configure search tool with utility, using legacy approach")
                        default_conn = await get_search_connection(client)
                        if default_conn:
                            search_tool = AzureAISearchTool(
                                index_connection_id=default_conn.id,
                                index_name=index_name,
                                query_type=AzureAISearchQueryType.SEMANTIC,
                                filter=kubernetes_filter,
                                top_k=50
                            )
                            logger.warning("Configured fallback search tool with connection ID")
                            return search_tool
                        return None
                else:
                    logger.warning(f"Azure AI Search index '{index_name}' not found or could not be verified")
                    return None
                    
            except Exception as ex:
                tool_span.record_exception(ex)
                tool_span.set_status(Status(StatusCode.ERROR, str(ex)))
                logger.error(f"Error in search tool configuration: {ex}")
                return None

    def build_kubernetes_input_filter(self, app_id):
        # Filter for YAML files in kubernetes folder using wildcard pattern
        # Matches: {app_id}/kubernetes/*/*.yaml and {app_id}/kubernetes/*/*.yml
        # This covers files extracted from zip: {app_id}/kubernetes/kubernetes-input-{app_id}.zip/deployment.yaml
        kubernetes_filter = f"search.ismatch('{app_id}/kubernetes-discovery/*/*.yaml', 'path') or search.ismatch('{app_id}/kubernetes-discovery/*/*.yml', 'path')"
        if self.config.azure_ai_search_filter:
            kubernetes_filter = f"({self.config.azure_ai_search_filter}) and ({kubernetes_filter})"
        return kubernetes_filter
    
    def _load_instructions(self, app_id: str) -> str:
        """
        Load agent instructions from file and replace placeholders.
        
        Args:
            app_id: Application ID to insert into instructions
            
        Returns:
            Formatted instructions string
        """
        default_instructions = (
            "You are the answer agent for application id '{{app_id_input}}'. "
            "You MUST NOT invent answers. Only answer if you can cite an authoritative source you have access to; "
            "otherwise leave Response and Citation empty and set Confidence to 0. "
            "For every question, return ONLY a strict JSON object with the exact keys: "
            "Response (string), Confidence (number 0..1), Citation (string). "
            "Put where you found the answer in Citation. Do not include any text outside the JSON."
        )
        instructions_text = load_instructions_from_file(
            self.config.instructions_file,
            placeholder_replacements={"app_id_input": app_id or 'unknown'},
            default_instructions=default_instructions
        )
        logger.debug(f"Loaded kubernetes discovery instructions from file: {self.config.instructions_file}")
        return instructions_text
    
    @trace_async_function("initialize_agent")
    async def initialize_agent(self, app_id: str) -> str:
        """
        Create or update an agent for the given application.
        
        Args:
            app_id: Application ID
            
        Returns:
            Agent ID
        """
        tracer = get_tracer()
        
        with tracer.start_as_current_span("agent_initialization") as init_span:
            try:
                add_span_attributes(init_span, {
                    "agent.endpoint": self.config.ai_project_endpoint,
                    "agent.model_deployment": self.config.ai_deployment_model_name,
                    "application_id": app_id
                })
                
                logger.info(f"Initializing agent for app_id: {app_id}")
                
                # Load instructions
                instructions_text = self._load_instructions(app_id)
                
                # Build kubernetes-specific filter for search tool
                kubernetes_filter = self.build_kubernetes_input_filter(app_id)
                
                # Configure search tool config
                search_config = SearchToolConfig(
                    index_name=app_id,
                    query_type=AzureAISearchQueryType.SEMANTIC,
                    top_k=50,
                    filter=kubernetes_filter,
                    field_mapping={
                        "contentFields": ["content"],
                        "titleField": "title",
                        "urlField": "url"
                    }
                )
                
                # Get client for agent creation
                client = await self._get_ai_project_client()
                
                # Create or update agent using utility
                agent_result = await create_agent_with_search_tool(
                    client=client,
                    agent_name="Kubernetes-Discovery-Agent",
                    application_id=app_id,
                    instructions=instructions_text,
                    search_tool_config=search_config,
                    model_deployment=self.config.ai_deployment_model_name,
                    temperature=0.0,  # Maximum factual accuracy
                    find_existing=True
                )
                
                self._agent_id = agent_result.agent.id
                
                if agent_result.is_new:
                    logger.debug(f"Created Kubernetes Discovery agent {self._agent_id} with search tool")
                else:
                    logger.debug(f"Updated Kubernetes Discovery agent {self._agent_id} with search tool")
                
                add_span_attributes(init_span, {
                    "agent.id": self._agent_id,
                    "agent.is_new": agent_result.is_new,
                    "agent.has_search_tool": agent_result.search_tool is not None
                })
                
                return self._agent_id
                
            except Exception as ex:
                init_span.record_exception(ex)
                init_span.set_status(Status(StatusCode.ERROR, str(ex)))
                raise
    
    async def _trigger_report_indexing(self, app_id: str, report_blob_name: str) -> bool:
        """
        Trigger indexing specifically for the uploaded kubernetes discovery report.
        
        Args:
            app_id: Application ID (used as container name)
            report_blob_name: Name of the report blob file (e.g., 'kubernetes-report-1029aks.md')
            
        Returns:
            True if indexing was triggered successfully, False otherwise
        """
        tracer = get_tracer()
        with tracer.start_as_current_span("trigger_report_indexing") as span:
            add_span_attributes(span, {
                "application_id": app_id,
                "report_blob_name": report_blob_name,
                "operation": "trigger_report_indexing"
            })
            
            try:
                # Get indexer service URL
                indexer_url = os.getenv("AZURE_INDEXING_FUNCTION_URL")
                if not indexer_url:
                    logger.warning("AZURE_INDEXING_FUNCTION_URL not set, skipping indexing")
                    span.set_status(Status(StatusCode.ERROR, "AZURE_INDEXING_FUNCTION_URL not set"))
                    return False
                
                # Build filter for the specific report file
                # The report is in kubernetes/ folder: kubernetes/kubernetes-report-{app_id}.md
                report_path = f"{app_id}//kubernetes-discovery/{report_blob_name}"
                
                # Prepare payload for indexing the specific report
                headers = {"Content-Type": "application/json"}
                payload = {
                    "appId": app_id,
                    "container": app_id,
                    "folder_prefix": f"kubernetes-discovery/{report_blob_name}"
                }
                
                add_span_attributes(span, {
                    "indexer_url": indexer_url,
                    "report_path": report_path
                })
                
                logger.info(f"Triggering indexing for kubernetes report: {report_blob_name}")
                logger.debug(f"Indexing payload: {json.dumps(payload, indent=2)}")
                
                async with aiohttp.ClientSession() as session:
                    async with session.post(indexer_url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as response:
                        try:
                            response_text = await response.text()
                            
                            if response.status == 200 or response.status == 202:
                                logger.info(f"✅ Indexing triggered successfully (status: {response.status})")
                                logger.debug(f"Indexer response: {response_text[:200]}")
                                span.set_status(Status(StatusCode.OK))
                                add_span_attributes(span, {
                                    "indexing_status": response.status,
                                    "indexing_success": True
                                })
                                return True
                            else:
                                logger.warning(f"Indexing request returned status {response.status}: {response_text[:200]}")
                                span.set_status(Status(StatusCode.ERROR, f"HTTP {response.status}"))
                                add_span_attributes(span, {
                                    "indexing_status": response.status,
                                    "indexing_success": False,
                                    "error_message": response_text[:200]
                                })
                                return False
                                
                        except Exception as parse_ex:
                            logger.error(f"Failed to parse indexer response: {parse_ex}")
                            span.record_exception(parse_ex)
                            return False
                            
            except asyncio.TimeoutError:
                logger.error("Indexing request timed out after 60 seconds")
                span.set_status(Status(StatusCode.ERROR, "Timeout"))
                return False
            except Exception as ex:
                logger.error(f"Error triggering report indexing: {ex}")
                span.record_exception(ex)
                span.set_status(Status(StatusCode.ERROR, str(ex)))
                return False
    
    async def upload_report_to_blob(self, file_path: str, app_id: str, blob_name: str = None) -> str:
        """
        Upload discovery report to Azure Blob Storage with automatic versioning.
        
        Args:
            file_path: Path to the file to upload
            app_id: Application ID (used as container name)
            blob_name: Optional blob name (will be placed in kubernetes/ folder)
        
        Returns:
            URL of the uploaded blob
        """
        try:
            if not blob_name:
                blob_name = os.path.basename(file_path)
            
            # Use utility function with kubernetes-discovery output folder prefix and versioning
            blob_url = await upload_file_to_container_async(
                file_path=file_path,
                app_id=app_id,
                blob_name=blob_name,
                folder_prefix="kubernetes-discovery/output/",
                enable_versioning=True
            )
            logger.info(f"Uploaded report to blob storage: {blob_url}")
            return blob_url
        except Exception as ex:
            current_span = trace.get_current_span()
            if current_span and current_span.is_recording():
                current_span.record_exception(ex)
                current_span.set_status(Status(StatusCode.ERROR, str(ex)))
            logger.error(f"Failed to upload report to blob storage: {ex}")
            raise
    
    @trace_async_function("generate_discovery_report")
    async def generate_discovery_report(self, app_id: str, agent_id: str = None, progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
        """
        Generate a comprehensive cluster discovery report.
        
        Args:
            app_id: Application/cluster name
            agent_id: Optional agent ID (will be created if not provided)
            progress_callback: Optional async callback function(message: str, percentage: float) for progress updates
            
        Returns:
            dict: Result containing status, output files, and blob URLs
        """
        tracer = get_tracer()
        
        with tracer.start_as_current_span("cluster_discovery_report_generation") as report_span:
            add_span_attributes(report_span, {
                "cluster_name": app_id,
                "operation": "generate_discovery_report"
            })
            
            try:
                # Get or create agent
                if not agent_id:
                    agent_id = await self.initialize_agent(app_id)
                    logger.info(f"Using agent ID: {agent_id}")
                
                add_span_attributes(report_span, {"agent_id": agent_id})
                
                client = await self._get_ai_project_client()
                
                # Create a thread for the conversation
                thread = await client.agents.threads.create()
                thread_id = thread.id
                logger.info(f"Created thread ID: {thread_id}")
                
                # Load discovery prompts
                discovery_prompts, prompt_sections = await self._load_discovery_prompts(app_id)
                total_prompts = len(discovery_prompts)
                
                responses = []
                
                for prompt_idx, prompt in enumerate(discovery_prompts):
                    i = prompt_idx + 1
                    logger.info(f"Processing discovery prompt {i}/{total_prompts}")
                    
                    # Create user message
                    created_msg = await client.agents.messages.create(
                        thread_id=thread_id,
                        role="user",
                        content=prompt
                    )
                    logger.debug(f"Created message: {getattr(created_msg, 'id', 'unknown')}")
                    
                    # Create and wait for run completion
                    run = await client.agents.runs.create(
                        thread_id=thread_id,
                        agent_id=agent_id
                    )
                    logger.info(f"Run created: {getattr(run, 'id', 'unknown')}")
                    
                    # Poll for completion
                    import time
                    poll_start = time.time()
                    terminal_statuses = {"completed", "failed", "cancelled", "succeeded"}
                    last_status = None
                    
                    while True:
                        current = await client.agents.runs.get(thread_id=thread_id, run_id=run.id)
                        last_status = getattr(current, 'status', None)
                        
                        if last_status in terminal_statuses:
                            break
                        
                        if time.time() - poll_start > 300:  # 5 minute timeout
                            logger.error(f"Run timeout after 300s for prompt {i}")
                            break
                        
                        await asyncio.sleep(2)
                    
                    if last_status not in {"completed", "succeeded"}:
                        logger.error(f"Run ended with status {last_status} for prompt {i}")
                        error_details = ""
                        if hasattr(current, 'last_error') and current.last_error:
                            error_code = getattr(current.last_error, 'code', 'unknown')
                            error_message = getattr(current.last_error, 'message', 'no message')
                            error_details = f" - Error: {error_code}: {error_message}"
                            logger.error(f"Run error details: {error_code}: {error_message}")
                        responses.append(f"Error: Run ended with status {last_status}{error_details}")
                        continue
                    
                    # Collect response
                    collected_text = []
                    messages_iter = client.agents.messages.list(thread_id=thread_id)
                    
                    async for msg in messages_iter:
                        if getattr(msg, 'run_id', None) == run.id:
                            role = str(getattr(msg, 'role', '')).lower()
                            if 'assistant' in role or 'agent' in role:
                                text_messages = getattr(msg, 'text_messages', None)
                                if text_messages:
                                    for tm in text_messages:
                                        text_val = getattr(getattr(tm, 'text', None), 'value', None)
                                        if text_val:
                                            collected_text.append(text_val)
                    
                    response_text = "\n".join(collected_text) if collected_text else "No response captured."
                    responses.append(response_text)
                    logger.info(f"Collected response for prompt {i}: {response_text[:100]}...")
                    
                    # Report progress after prompt completion (20-85% range mapped across prompts)
                    if progress_callback:
                        try:
                            # Map prompt progress to 20-85% range (65% total span)
                            progress_pct = 20 + int((i / total_prompts) * 65)
                            await progress_callback(
                                f"Completed discovery prompt {i}/{total_prompts}",
                                progress_pct
                            )
                            logger.debug(f"Progress callback: {progress_pct}% - prompt {i}/{total_prompts}")
                        except Exception as progress_ex:
                            logger.warning(f"Failed to report progress for prompt {i}: {progress_ex}")
                
                # Generate markdown report
                md_path = await self._generate_markdown_report(app_id, responses, prompt_sections)
                
                # Upload to blob storage with automatic versioning (handled by upload_report_to_blob)
                blob_url = None
                try:
                    file_name = os.path.basename(md_path)
                    logger.info(f"Uploading markdown file to blob storage: {file_name}")
                    blob_url = await self.upload_report_to_blob(md_path, app_id, file_name)
                    logger.info(f"✅ Uploaded report to: {blob_url}")
                    
                    # Extract the versioned file name from the blob URL for indexing
                    versioned_file_name = blob_url.split('/')[-1] if blob_url else file_name
                    
                    # Trigger indexing for the uploaded report
                    try:
                        indexing_result = await self._trigger_report_indexing(app_id, versioned_file_name)
                        if indexing_result:
                            logger.info(f"✅ Triggered indexing for report: {versioned_file_name}")
                        else:
                            logger.warning(f"⚠️  Failed to trigger indexing for report: {versioned_file_name}")
                    except Exception as idx_ex:
                        logger.warning(f"⚠️  Indexing trigger failed (report uploaded successfully): {idx_ex}")
                    
                    # Delete local markdown file after successful upload
                    try:
                        if os.path.exists(md_path):
                            os.remove(md_path)
                            logger.debug(f"Deleted local file: {md_path}")
                    except Exception as del_ex:
                        logger.warning(f"Could not delete local files: {del_ex}")
                    
                except Exception as blob_ex:
                    logger.warning(f"⚠️  Failed to upload to blob storage (report still available locally): {blob_ex}")
                    blob_url = f"local://{os.path.abspath(md_path)}"
                
                #Cleanup thread
                try:
                    await client.agents.threads.delete(thread_id=thread_id)
                    logger.debug(f"Deleted thread: {thread_id}")
                except Exception as cleanup_ex:
                    logger.warning(f"Failed to cleanup thread: {cleanup_ex}")
                
                try:
                    await client.agents.delete_agent(agent_id)
                    logger.debug(f"Successfully deleted Design agent: {agent_id}")
                except Exception as agent_ex:
                    return {
                    "status": "error",
                    "message": f"Failed to delete agent: {str(agent_ex)}",
                    }
            
                report_span.set_status(Status(StatusCode.OK))
                
                result = {
                    "status": "success",
                    "cluster_name": app_id,
                    "agent_id": agent_id,
                    "thread_id": thread_id,
                    "blob_url": blob_url,
                    "markdown_file": os.path.abspath(md_path)
                }

                    
                
                logger.info(f"✅ Report generation completed successfully!")
                logger.info(f"📄 Markdown report: {result['markdown_file']}")
                if blob_url and not blob_url.startswith("local://"):
                    logger.info(f"☁️  Blob URL: {blob_url}")
                
                return result
                
            except Exception as ex:
                report_span.record_exception(ex)
                report_span.set_status(Status(StatusCode.ERROR, str(ex)))
                logger.error(f"Failed to generate cluster discovery report: {ex}")
                return {
                    "status": "error",
                    "message": str(ex)
                }
    
    async def _load_discovery_prompts(self, cluster_name: str):
        """Load discovery prompts from JSON file."""
        # Create initial prompt with dynamic application_id - CRITICAL for ensuring tool usage
        initial_prompt = (
            f"Using the attached AI Search knowledge project-index-{cluster_name}/versions/1, Always perform a search query with the attached tool, even if you think the result will be empty. Never skip the tool call."
        )
        
        try:
            with open(self.config.prompts_file, 'r', encoding='utf-8') as f:
                prompts_config = json.load(f)
                
            prompt_items = sorted(prompts_config.get("discovery_prompts", []), key=lambda x: x.get("order", 0))
            discovery_prompts = []
            prompt_sections = []
            
            for item in prompt_items:
                if item.get("required", True):
                    prompt_text = item.get("prompt", "")
                    prompt_text = prompt_text.replace("{{cluster_name}}", cluster_name)
                    
                    # Prepend initial_prompt to each prompt
                    if prompt_text:
                        prompt_text = initial_prompt + " " + prompt_text
                    
                    discovery_prompts.append(prompt_text)
                    prompt_sections.append(item.get("section", f"section_{item.get('id')}"))
            
            logger.info(f"Loaded {len(discovery_prompts)} discovery prompts from {self.config.prompts_file}")
            return discovery_prompts, prompt_sections
            
        except Exception as prompt_ex:
            logger.warning(f"Failed to load prompts from {self.config.prompts_file}: {prompt_ex}, using default prompts")
            # Apply initial_prompt to default prompts as well
            discovery_prompts = [
                initial_prompt + " " + f"Provide a comprehensive cluster summary for '{cluster_name}' including node count, total CPU, memory, and resource utilization.",
                initial_prompt + " " + f"List all namespaces in '{cluster_name}' with their applications and resources in structured format.",
                initial_prompt + " " + f"For each application in '{cluster_name}', provide detailed deployment, service, and configuration information.",
                initial_prompt + " " + f"Provide Azure target recommendations for '{cluster_name}' based on Microsoft's Well-Architected Framework for AKS."
            ]
            prompt_sections = ["cluster_summary", "namespaces", "application_details", "azure_recommendations"]
            return discovery_prompts, prompt_sections
    
    async def _generate_markdown_report(self, cluster_name: str, responses: list, prompt_sections: list) -> str:
        """Generate markdown report from responses."""
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        md_path = f"kubernetes-report-{cluster_name}.md"
        
        lines = [f"# Kubernetes Cluster Discovery Report: {cluster_name}\n\n"]
        lines.append(f"**Generated:** {datetime.datetime.now().isoformat()}\n")
        lines.append("---\n\n")
        
        section_titles = {
            "cluster_summary": "## 🏗️ Cluster Summary\n\n",
            "namespaces": "## 📦 Namespaces and Applications\n\n",
            "application_details": "## 🔧 Application Details\n\n",
            "azure_recommendations": "## ☁️ Azure Target Recommendations\n\n"
        }
        
        for idx, response in enumerate(responses):
            if idx < len(prompt_sections):
                section_key = prompt_sections[idx]
                lines.append(section_titles.get(section_key, f"## Section {idx+1}\n\n"))
            else:
                lines.append(f"## Section {idx+1}\n\n")
            
            if response and isinstance(response, str):
                try:
                    if response.strip().startswith('{'):
                        parsed = json.loads(response)
                        if 'Response' in parsed and parsed['Response']:
                            lines.append(f"{parsed['Response']}\n\n")
                        else:
                            lines.append(f"{response}\n\n")
                    else:
                        lines.append(f"{response}\n\n")
                except Exception as parse_ex:
                    logger.debug(f"Failed to parse response as JSON: {parse_ex}")
                    lines.append(f"{response}\n\n")
            else:
                lines.append("_No data available_\n\n")
        
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write("".join(lines))
        
        logger.info(f"Generated Markdown report: {md_path}")
        return md_path
    
    async def cleanup(self):
        """Cleanup resources"""
        if self._ai_project_client:
            await self._ai_project_client.close()
        if self._blob_service_client:
            await self._blob_service_client.close()
        if self._credential:
            await self._credential.close()
        logger.info("Kubernetes Discovery Agent resources cleaned up")    

@trace_async_function("kubernetes_discovery_agent")
async def kubernetes_discovery_agent(app_id_input: Optional[str], progress_callback: Optional[Callable] = None) -> dict:
    """
    Legacy function for backward compatibility with orchestrator.
    Initializes agent and generates discovery report.
    
    Args:
        app_id_input: Application/cluster ID
        progress_callback: Optional async callback function for progress updates
    
    Returns dict with status, agent_id, and report details.
    """
    agent = KubernetesDiscoveryAgent()
    try:
        # Initialize agent
        agent_id = await agent.initialize_agent(app_id_input)
        logger.info(f"Agent initialized: {agent_id}")
        
        # Generate discovery report with progress callback
        report_result = await agent.generate_discovery_report(app_id_input, agent_id, progress_callback)
        
        # Return dict format expected by orchestrator
        return {
            "status": report_result.get("status", "error"),
            "agent_id": agent_id,
            "application_id": app_id_input,
            "thread_id": report_result.get("thread_id", ""),
            "blob_url": report_result.get("blob_url", ""),
            "markdown_file": report_result.get("markdown_file", ""),
            "message": f"Kubernetes discovery report generated for {app_id_input}" if report_result.get("status") == "success" else report_result.get("message", ""),
            "response": f"Successfully generated cluster discovery report for {app_id_input}. Report available at: {report_result.get('blob_url', 'local file')}" if report_result.get("status") == "success" else f"Error: {report_result.get('message', 'Unknown error')}",
            "response_length": len(str(report_result.get("markdown_file", ""))),
            "cleanup_performed": True
        }
    except Exception as ex:
        logger.error(f"Error in kubernetes_discovery_agent: {ex}")
        return {
            "status": "error",
            "agent_id": "",
            "application_id": app_id_input,
            "thread_id": "",
            "message": str(ex),
            "response": f"Error generating Kubernetes discovery report: {str(ex)}",
            "response_length": 0,
            "cleanup_performed": False
        }
    finally:
        await agent.cleanup()


async def generate_cluster_discovery_report(cluster_name: str, client=None, agent_id: str = None) -> dict:
    """
    Legacy function for backward compatibility.
    Generate a comprehensive cluster discovery report.
    """
    agent = KubernetesDiscoveryAgent()
    try:
        report = await agent.generate_discovery_report(cluster_name, agent_id)
        return report
    finally:
        await agent.cleanup()


@trace_async_function("main")
async def main() -> None:
    """Main entry point for CLI usage"""
    tracer = get_tracer()
    with tracer.start_as_current_span("main_execution") as main_span:
        try:
            parser = argparse.ArgumentParser(description="Kubernetes Discovery Agent CLI")
            parser.add_argument("--app-id-input", "-a", "--application-id", dest="app_id_input", required=False, help="Application ID to use")
            parser.add_argument("--generate-report", "-r", action="store_true", help="Generate a comprehensive discovery report")
            args = parser.parse_args()

            app_id_input = args.app_id_input or os.getenv("AZURE_AI_AGENT_NAME")
            
            if not app_id_input and sys.stdin.isatty():
                try:
                    app_id_input = input("Enter Application ID (or press Enter for default 'KubernetesDiscoveryAgent'): ").strip()
                    if not app_id_input:
                        app_id_input = "KubernetesDiscoveryAgent"
                except Exception as input_ex:
                    main_span.add_event("input_error", {"error": str(input_ex)})
                    app_id_input = "KubernetesDiscoveryAgent"
            
            if not app_id_input:
                app_id_input = "KubernetesDiscoveryAgent"
                logger.info(f"No app_id_input provided, using default: {app_id_input}")

            add_span_attributes(main_span, {"app_id_input": app_id_input, "generate_report": args.generate_report})

            # Create agent instance
            agent = KubernetesDiscoveryAgent()
            
            try:
                logger.info("Initializing agent...")
                agent_id = await agent.initialize_agent(app_id_input)
                logger.info(f"Agent ID: {agent_id}")
                
                add_span_attributes(main_span, {"agent_id": agent_id})
                
                # Generate discovery report if requested
                if args.generate_report:
                    logger.info("Generating cluster discovery report...")
                    report_result = await agent.generate_discovery_report(app_id_input, agent_id)
                    
                    if report_result.get("status") == "success":
                        logger.info(f"✅ Discovery report generated successfully!")
                        logger.info(f"   Blob URL: {report_result.get('blob_url')}")
                        print(f"\n{'='*60}")
                        print(f"✅ Cluster Discovery Report Generated")
                        print(f"{'='*60}")
                        print(f"Cluster: {app_id_input}")
                        print(f"Report URL: {report_result.get('blob_url')}")
                        print(f"{'='*60}\n")
                    else:
                        logger.error(f"❌ Failed to generate report: {report_result.get('message')}")
                        print(f"\n❌ Report generation failed: {report_result.get('message')}\n")
            finally:
                await agent.cleanup()
            
        except Exception as ex:
            main_span.record_exception(ex)
            main_span.set_status(Status(StatusCode.ERROR, str(ex)))
            logger.error("Main execution failed: %s", ex)
            raise


if __name__ == "__main__":
    asyncio.run(main())