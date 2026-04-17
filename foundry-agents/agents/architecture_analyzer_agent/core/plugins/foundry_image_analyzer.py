"""
Azure AI Foundry Image Analyzer for Architecture Diagrams

This module uses Azure AI Foundry models instead of the Vision API
for analyzing architecture diagrams.

This module also provides Semantic Kernel plugin functions for design document extraction and analysis.
"""
import json
import os
import logging
import traceback
import asyncio
import functools
import re
import base64
from typing import Dict, Any, Optional, Annotated
from datetime import datetime
from semantic_kernel.functions import kernel_function

try:
    from azure.ai.projects import AIProjectClient
except ImportError as e:
    logging.error(f"Failed to import azure.ai.projects: {e}. Please install with: pip install azure-ai-projects")
    raise

try:
    from azure.identity import DefaultAzureCredential, AzureCliCredential, ChainedTokenCredential, ManagedIdentityCredential
except ImportError as e:
    logging.error(f"Failed to import azure.identity: {e}. Please install with: pip install azure-identity")
    raise

from .plugin_utils import load_plugin_environment, get_ado_credentials, get_azure_openai_config, get_azure_storage_config, load_agent_instructions

# Import tracing configuration
try:
    from tracing_config import (
        get_tracer,
        add_span_attributes,
        record_error_details
    )
    from opentelemetry.trace import Status, StatusCode
    TRACING_AVAILABLE = True
except ImportError:
    TRACING_AVAILABLE = False
    logging.warning("Tracing not available for foundry_image_analyzer - import failed")

logger = logging.getLogger(__name__)

# Debug printing controlled by ARCH_AGENT_DEBUG or DEBUG env var
_FOUNDY_DEBUG = os.environ.get("ARCH_AGENT_DEBUG", os.environ.get("DEBUG", "false")).lower() in ("1", "true", "yes")

def _debug_print(*args, **kwargs):
    if not _FOUNDY_DEBUG:
        return
    try:
        ts = datetime.utcnow().isoformat()
        msg = ' '.join(str(arg) for arg in args)
        logger.debug(f"[FOUNDRY DEBUG] {ts} - {msg}")
    except Exception:
        try:
            msg = ' '.join(str(arg) for arg in args)
            logger.debug(msg)
        except Exception:
            pass


def get_foundry_client(project_endpoint: str) -> AIProjectClient:
    """
    Create and return Azure AI Foundry project client.
    
    Args:
        project_endpoint: Azure AI Foundry project endpoint URL
        
    Returns:
        AIProjectClient: Configured project client
    """
    try:
        # Validate endpoint format
        if not project_endpoint:
            raise ValueError("Project endpoint is required but was empty or None")
        
        if not project_endpoint.startswith("https://"):
            raise ValueError(f"Project endpoint must start with 'https://'. Got: {project_endpoint}")
        
        logger.info(f"[AUTH] Creating Azure AI Foundry client for: {project_endpoint[:50]}...")
        _debug_print("get_foundry_client called", project_endpoint[:200])
        
        # Try Azure CLI credentials first, then fall back to other methods
        from azure.identity import AzureCliCredential, ChainedTokenCredential, ManagedIdentityCredential
        
        # Create credential chain with multiple authentication methods
        try:
            credential = ChainedTokenCredential(
                AzureCliCredential(additionally_allowed_tenants=["*"]),  # Allow any tenant
                ManagedIdentityCredential(),  # For Azure resources  
                DefaultAzureCredential(exclude_shared_token_cache_credential=True)
            )
        except Exception as cred_error:
            logger.error(f"[AUTH] Failed to create credential chain: {str(cred_error)}")
            # Fallback to simple DefaultAzureCredential
            credential = DefaultAzureCredential()
        
        # Test credential by getting a token
        try:
            logger.info("[AUTH] Testing credential...")
            token = credential.get_token("https://cognitiveservices.azure.com/.default")
            logger.info(f"[AUTH] Successfully obtained token (expires: {token.expires_on})")
            _debug_print("get_foundry_client token obtained, expires", getattr(token, 'expires_on', None))
        except Exception as auth_e:
            logger.error(f"[AUTH] Failed to get authentication token: {str(auth_e)}")
            raise ValueError(f"Authentication failed. Please run 'az login' or check your Azure credentials. Details: {str(auth_e)}")
        
        # Create the AI Project client
        client = AIProjectClient(
            endpoint=project_endpoint,
            credential=credential
        )
        
        logger.info(f"[SUCCESS] Successfully created Azure AI Foundry client")
        _debug_print("Foundry client created successfully")
        return client
        
    except ValueError:
        # Re-raise validation errors as-is
        raise
    except Exception as e:
        error_message = str(e)
        logger.error(f"[ERROR] Failed to create Azure AI Foundry client: {error_message}")
        
        # Provide helpful error messages
        if "subscription" in error_message.lower():
            raise ValueError(f"Subscription access issue. Check if your account has access to the Azure AI project. Error: {error_message}")
        elif "tenant" in error_message.lower():
            raise ValueError(f"Tenant access issue. Check if you're logged into the correct Azure tenant. Error: {error_message}")
        else:
            raise ValueError(f"Failed to create Azure AI Foundry client: {error_message}")


def get_fallback_architecture_prompt() -> str:
        """
        Provide a minimal inline fallback prompt if external prompt loader is unavailable.
        This avoids NameError during runtime when load_agent_instructions fails to provide content.
        """
        return """
You are analyzing an Azure architecture diagram. Extract all components, relationships, and security elements.

Return a JSON object with this structure:
{
    "description": "Detailed description of the architecture", 
    "architecture_pattern": "3-tier | microservices | hub-spoke | other",
    "nodes": [],
    "edges": [],
    "boundaries": [],
    "security_observations": {}
}

Return ONLY the JSON object, no markdown or explanations.
"""


def analyze_architecture_diagram_with_foundry(img: Dict[str, Any], project_endpoint: str, model_name: str = "gpt-4o") -> Dict[str, Any]:
    """
    Analyzes architecture diagram using Azure AI Foundry models with Semantic Kernel AzureAIAgent.
    
    Args:
        img: Dictionary with 'base64' key containing base64-encoded image
        project_endpoint: Azure AI Foundry project endpoint URL
        model_name: Name of the model to use for analysis
    
    Returns:
        dict: Structured architecture analysis with nodes, edges, boundaries, and security observations
    """
    import asyncio
    
    # Run the async implementation
    return asyncio.run(_analyze_architecture_diagram_with_foundry_async(img, project_endpoint, model_name))


async def _analyze_architecture_diagram_with_foundry_async(
    img: Dict[str, Any], 
    project_endpoint: str, 
    model_name: str = "gpt-4o",
    diagram_agent=None,
    app_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Async implementation of architecture diagram analysis using AzureAIAgent.
    
    Args:
        img: Dictionary with 'base64' key containing base64-encoded image
        project_endpoint: Azure AI Foundry project endpoint URL
        model_name: Name of the model to use for analysis
        diagram_agent: Optional pre-created diagram analyzer agent (recommended for performance)
        app_id: Application ID for thread tracking
    
    Returns:
        dict: Structured architecture analysis with nodes, edges, boundaries, and security observations
    """
    tracer = get_tracer() if TRACING_AVAILABLE else None
    span_context = tracer.start_as_current_span("foundry.analyze_architecture_diagram") if tracer else None
    
    if span_context:
        span = span_context.__enter__()
        image_size_kb = len(img.get("base64", "")) // 1024
        add_span_attributes(span, {
            "architecture.model_name": model_name,
            "architecture.image_size_kb": image_size_kb,
            "architecture.app_id": app_id or "unknown",
            "architecture.project_endpoint": project_endpoint[:100]
        })
    
    logger.info(f"[SEARCH] Starting architecture diagram analysis with Azure AI Foundry model: {model_name}")
    logger.info(f"[CONFIG] Project endpoint: {project_endpoint[:50]}... (truncated)")
    _debug_print("analyze_architecture_diagram_with_foundry called", {"model": model_name, "endpoint": project_endpoint[:200]})
    
    # Validate input
    if "base64" not in img:
        _debug_print("analyze_architecture_diagram_with_foundry missing base64 in img")
        raise ValueError("Image must contain 'base64' key with base64-encoded data")
    
    image_data = img["base64"]
    image_size_kb = len(image_data) // 1024
    logger.info(f"[IMAGE] Image size: {image_size_kb} KB")
    _debug_print("image size kb", image_size_kb)
    
    # Validate image size (Azure AI has limits)
    if image_size_kb > 20000:  # 20MB limit
        raise ValueError(f"Image too large ({image_size_kb} KB). Maximum size is 20MB.")
    
    try:
        # Load the agent instructions
        logger.info("[PROMPT] Loading agent instructions...")
        _debug_print("Loading agent instructions for foundry_image_analyzer_agent")
        architecture_prompt = load_agent_instructions("foundry_image_analyzer_agent")
        
        if not architecture_prompt:
            logger.warning("[WARNING] Could not load agent instructions, using fallback")
            try:
                architecture_prompt = get_fallback_architecture_prompt()
            except NameError:
                # Define inline fallback if function is not available
                architecture_prompt = """
You are analyzing an Azure architecture diagram. Extract all components, relationships, and security elements.

Return a JSON object with this structure:
{
  "description": "Detailed description of the architecture", 
  "architecture_pattern": "3-tier | microservices | hub-spoke | other",
  "nodes": [],
  "edges": [],
  "boundaries": [],
  "security_observations": {}
}

Return ONLY the JSON object, no markdown or explanations.
"""
                logger.info("[FALLBACK] Using inline fallback prompt")
        
        # Call Azure AI Foundry using Semantic Kernel's AzureAIAgent with invoke method
        logger.info(f"[API] Calling Azure AI Foundry model: {model_name}")
        _debug_print("Calling foundry model with Semantic Kernel AzureAIAgent", model_name)

        # Use Semantic Kernel's AzureAIAgent with invoke for vision
        from semantic_kernel import Kernel
        from semantic_kernel.agents import AzureAIAgent
        from semantic_kernel.contents import ChatMessageContent, ImageContent, AuthorRole, TextContent
        from azure.identity.aio import DefaultAzureCredential
        
        # Import AgentFactory for centralized agent creation
        import sys
        from pathlib import Path
        arch_analyzer_path = Path(__file__).parent.parent
        if str(arch_analyzer_path) not in sys.path:
            sys.path.insert(0, str(arch_analyzer_path))
        from agent_factory import AgentFactory
        
        kernel = Kernel()
        
        # Require diagram agent to be provided (no inline fallback)
        if not diagram_agent:
            error_msg = "No diagram analyzer agent provided. Agent must be created before calling this function."
            logger.error(f"[ERROR] {error_msg}")
            raise ValueError(error_msg)
        
        agent = diagram_agent
        logger.info("[AGENT] Using provided diagram analyzer agent (agent reused)")
        _debug_print("Reusing existing diagram analyzer agent")
        
        # Create message content with image using Semantic Kernel content types
        logger.info("[MESSAGE] Creating message with image content...")
        # Include both text and image in the message
        message = ChatMessageContent(
            role=AuthorRole.USER,
            items=[
                TextContent(text="Analyze this architecture diagram and return the JSON structure as specified in your instructions."),
                ImageContent(uri=f"data:image/png;base64,{image_data}")
            ]
        )
        
        # Invoke agent with vision message
        logger.info("[INVOKE] Invoking agent with image...")
        agent_responses = []
        async for response_item in agent.invoke(
            messages=message,
            thread=None,
            temperature=0.2,
            max_completion_tokens=4096
        ):
            response = response_item.message if hasattr(response_item, 'message') else response_item
            agent_responses.append(response)
            logger.info(f"[RESPONSE] Received response item")
        
        # Extract text content from responses
        reply = ""
        for resp in agent_responses:
            if hasattr(resp, 'content'):
                if isinstance(resp.content, str):
                    reply += resp.content
                elif isinstance(resp.content, list):
                    for item in resp.content:
                        if hasattr(item, 'text'):
                            reply += str(item.text)
        
        logger.info(f"[RESPONSE] Total reply length: {len(reply)} chars")
        
        # Note: Agent cleanup is handled by the caller (extract_and_analyze_architecture) after all images are processed
        
        _debug_print("AzureAIAgent call completed, reply length", len(reply))
        
        # Validate reply
        if not reply:
            raise ValueError("Empty response from Azure AI Foundry model")
        
        # Clean and parse JSON response
        cleaned_reply = clean_json_string(reply)
        architecture_data = json.loads(cleaned_reply)
        _debug_print("Parsed architecture data keys", list(architecture_data.keys()) if isinstance(architecture_data, dict) else type(architecture_data))
        
        # Validate the response structure
        validate_architecture_data(architecture_data)
        
        logger.info(f"[SUCCESS] Successfully analyzed architecture diagram")
        _debug_print("analysis success", {"nodes": len(architecture_data.get('nodes', [])), "edges": len(architecture_data.get('edges', []))})
        logger.info(f"   - Extracted {len(architecture_data.get('nodes', []))} components")
        logger.info(f"   - Identified {len(architecture_data.get('edges', []))} relationships") 
        logger.info(f"   - Found {len(architecture_data.get('boundaries', []))} boundaries")
        
        if span_context:
            add_span_attributes(span, {
                "architecture.nodes_count": len(architecture_data.get('nodes', [])),
                "architecture.edges_count": len(architecture_data.get('edges', [])),
                "architecture.boundaries_count": len(architecture_data.get('boundaries', []))
            })
            span.set_status(Status(StatusCode.OK))
        
        return architecture_data
        
    except Exception as e:
        error_type = type(e).__name__
        error_message = str(e)
        _debug_print("analyze_architecture_diagram_with_foundry exception", error_type, error_message)
        
        if span_context:
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)[:256]))
        
        if TRACING_AVAILABLE and span_context:
            record_error_details(
                span=span,
                error_type=error_type,
                error_message=error_message,
                error_code=None,
                is_retryable=True
            )
        
        # Provide specific troubleshooting information
        if "Authentication" in error_message or "401" in error_message:
            logger.error(f"[ERROR] Authentication failed with Azure AI Foundry: {error_message}")
            logger.error("   Check: 1) Azure CLI login status, 2) FOUNDRY_PROJECT_ENDPOINT is correct, 3) Access permissions to the AI project")
        elif "403" in error_message or "Forbidden" in error_message:
            logger.error(f"[ERROR] Access forbidden to Azure AI Foundry: {error_message}")
            logger.error("   Check: 1) Project permissions, 2) Model deployment access, 3) Resource group permissions")
        elif "404" in error_message or "not found" in error_message:
            logger.error(f"[ERROR] Azure AI Foundry resource not found: {error_message}")
            logger.error("   Check: 1) FOUNDRY_PROJECT_ENDPOINT URL, 2) Project exists and is accessible")
        elif "timeout" in error_message.lower():
            logger.error(f"[ERROR] Request timed out: {error_message}")
            logger.error("   Check: 1) Network connectivity, 2) Image size (may be too large), 3) Model availability")
        elif "json" in error_message.lower():
            logger.error(f"[ERROR] Invalid JSON response from model: {error_message}")
            logger.error("   Check: 1) Model configuration, 2) Prompt may need adjustment, 3) Try different model")
        else:
            logger.error(f"[ERROR] Failed to analyze architecture diagram with Azure AI Foundry: {error_type} - {error_message}")
        
        raise
    finally:
        if span_context:
            span_context.__exit__(None, None, None)


def validate_architecture_data(data: Dict[str, Any]) -> None:
    """
    Validate the structure of the architecture data response.
    
    Args:
        data: The parsed architecture data dictionary
        
    Raises:
        ValueError: If the data structure is invalid
    """
    required_keys = ["nodes", "edges", "boundaries", "security_observations"]
    
    for key in required_keys:
        if key not in data:
            raise ValueError(f"Missing required key: {key}")
    
    # Validate nodes have required fields
    for i, node in enumerate(data.get("nodes", [])):
        required_node_fields = ["id", "type", "label", "category"]
        for field in required_node_fields:
            if field not in node:
                raise ValueError(f"Node {i} missing required field: {field}")
    
    # Validate edges reference valid nodes
    node_ids = {node["id"] for node in data.get("nodes", [])}
    for i, edge in enumerate(data.get("edges", [])):
        if "source" not in edge or "target" not in edge:
            raise ValueError(f"Edge {i} missing source or target")
        
        if edge["source"] not in node_ids:
            logger.warning(f"Edge {i} references unknown source node: {edge['source']}")
        
        if edge["target"] not in node_ids:
            logger.warning(f"Edge {i} references unknown target node: {edge['target']}")


def clean_json_string(json_string: str) -> str:
    """
    Remove markdown code fences from JSON string if present.
    
    Args:
        json_string: Raw JSON string that might contain markdown formatting
        
    Returns:
        str: Cleaned JSON string
    """
    if json_string.startswith("```json"):
        json_string = json_string[len("```json"):].strip()
    elif json_string.startswith("```"):
        json_string = json_string[len("```"):].strip()
    
    if json_string.endswith("```"):
        json_string = json_string[:-len("```")].strip()
    
    return json_string


def analyze_architecture_diagram(img: Dict[str, Any], project_endpoint: str, model_name: str = "gpt-4o") -> Dict[str, Any]:
    """
    Main entry point for architecture diagram analysis using Azure AI Foundry.
    
    This function maintains compatibility with the existing interface while
    using Azure AI Foundry models instead of the Vision API.
    
    Args:
        img: Dictionary with 'base64' key containing base64-encoded image
        project_endpoint: Azure AI Foundry project endpoint URL (replaces vision_endpoint)
        model_name: Model name to use (replaces vision_api_key parameter)
    
    Returns:
        dict: Structured architecture analysis
    """
    return analyze_architecture_diagram_with_foundry(img, project_endpoint, model_name)


# ============================================================================
# Semantic Kernel Plugin Functions for Design Document Extraction
# ============================================================================

def retry_on_failure(max_retries=3, delay=1.0):
    """Decorator to retry function calls on failure with exponential backoff."""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        wait_time = delay * (2 ** attempt)  # Exponential backoff
                        logger.warning(f"Attempt {attempt + 1}/{max_retries} failed for {func.__name__}: {str(e)}. Retrying in {wait_time}s...")
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error(f"All {max_retries} attempts failed for {func.__name__}: {str(e)}")
            raise last_exception
        return wrapper
    return decorator


@kernel_function(
    description="Extract and analyze architecture diagrams from blob storage design documents, returning structured component data",
    name="extract_and_analyze_architecture"
)
@retry_on_failure(max_retries=3, delay=2.0)
async def extract_and_analyze_architecture(
    design_doc_url: Annotated[str, "The blob storage path or URL to the design document containing architecture diagrams (e.g., 'design-docs/project1/architecture.md')"],
    app_id: Annotated[str, "Application ID for agent naming"] = "unknown"
) -> Annotated[str, "JSON string containing analyzed architecture components, relationships, and security observations"]:
    """
    Extract design document content from blob storage and analyze any architecture diagrams found.
    
    Args:
        design_doc_url: The blob storage path or URL to the design document
        app_id: Application ID for agent naming (defaults to 'unknown')
        
    Returns:
        JSON string with architecture analysis results including components, relationships, and security findings
    """
    tracer = get_tracer() if TRACING_AVAILABLE else None
    span_context = tracer.start_as_current_span("foundry.extract_and_analyze_architecture") if tracer else None
    
    try:
        if span_context:
            span = span_context.__enter__()
            add_span_attributes(span, {
                "architecture.design_doc_url": design_doc_url[:200],
                "architecture.app_id": app_id,
                "architecture.operation": "extract_and_analyze"
            })
        
        logger.info(f"[BUILD] Extracting and analyzing architecture from blob storage: {design_doc_url}")
        _debug_print("extract_and_analyze_architecture called", design_doc_url)
        
        # Load environment configuration
        load_plugin_environment()
        
        # Get required configuration
        openai_config = get_azure_openai_config()
        storage_config = get_azure_storage_config()
        
        foundry_endpoint = openai_config["foundry_endpoint"]
        foundry_model = openai_config["foundry_model"]
        
        if not foundry_endpoint:
            raise ValueError("FOUNDRY_PROJECT_ENDPOINT environment variable is required for architecture analysis")
        
        # Get blob storage configuration
        storage_account_name = storage_config["account_name"]
        
        logger.info(f"[INFO] Reading from blob storage: account={storage_account_name}, path={design_doc_url}")
        
        # Initialize blob service client
        from azure.storage.blob import BlobServiceClient
        from azure.identity import DefaultAzureCredential
        
        account_url = f"https://{storage_account_name}.blob.core.windows.net"
        blob_service_client = BlobServiceClient(account_url=account_url, credential=DefaultAzureCredential())
        
        # Extract blob content and images
        # If design_doc_url is a full URL, parse it; otherwise treat as blob name and use app_id as container
        if design_doc_url.startswith("http"):
            # Parse blob URL
            from urllib.parse import urlparse
            parsed_url = urlparse(design_doc_url)
            path_parts = parsed_url.path.strip('/').split('/', 1)
            if len(path_parts) >= 2:
                container_name = path_parts[0]
                blob_name = path_parts[1]
            else:
                # Fallback: use app_id as container if path parsing fails
                container_name = app_id
                blob_name = design_doc_url
        else:
            # design_doc_url is a blob path, use app_id as container name
            container_name = app_id
            blob_name = design_doc_url
        
        logger.info(f"[INFO] Using container: {container_name}, blob: {blob_name}")
        
        # Read the main design document (markdown)
        if span_context:
            span.add_event("reading_design_document")
        
        try:
            blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_name)
            blob_data = blob_client.download_blob()
            
            # Check if blob_data is None (blob doesn't exist)
            if blob_data is None:
                error_msg = f"Blob does not exist: {blob_name} in container {container_name}"
                logger.error(f"[ERROR] {error_msg}")
                return json.dumps({
                    "status": "error",
                    "error": "BlobNotFound",
                    "message": error_msg,
                    "container": container_name,
                    "blob": blob_name
                })
            
            # Read blob content
            blob_content = blob_data.readall()
            if blob_content is None:
                error_msg = f"Blob exists but content is empty or unreadable: {blob_name}"
                logger.error(f"[ERROR] {error_msg}")
                return json.dumps({
                    "status": "error",
                    "error": "BlobReadError",
                    "message": error_msg,
                    "container": container_name,
                    "blob": blob_name
                })
            
            all_text = blob_content.decode('utf-8')
            
        except Exception as blob_error:
            error_msg = f"Failed to read design document from blob storage: {str(blob_error)}"
            logger.error(f"[ERROR] {error_msg}")
            return json.dumps({
                "status": "error",
                "error": "BlobNotFound" if "BlobNotFound" in str(blob_error) or "does not exist" in str(blob_error).lower() else "BlobReadError",
                "message": error_msg,
                "container": container_name,
                "blob": blob_name,
                "exception": str(blob_error)
            })
        
        logger.info(f"[SUCCESS] Read design document: {len(all_text)} characters")
        _debug_print("extract_and_analyze_architecture: read blob", {"text_chars": len(all_text)})
        
        if span_context:
            add_span_attributes(span, {
                "architecture.design_doc_length": len(all_text),
                "architecture.container_name": container_name,
                "architecture.blob_name": blob_name[:200]
            })
            span.add_event("design_document_read", {"content_length": len(all_text)})
        
        # Extract image references from markdown and download them
        all_images = []
        
        # Find markdown image syntax: ![alt](image_path) or <img src="image_path">
        # Also extract surrounding text context for each image
        image_patterns = [
            r'!\[([^\]]*)\]\(([^\)]+)\)',  # Markdown syntax
            r'<img[^>]+src=["\']([^"\'>]+)["\']',  # HTML syntax
        ]
        
        for pattern in image_patterns:
            matches = re.finditer(pattern, all_text)
            for match in matches:
                image_path = match.group(2) if '!' in pattern else match.group(1)
                alt_text = match.group(1) if '!' in pattern else ''
                
                # Extract surrounding text context
                match_start = match.start()
                match_end = match.end()
                
                # Extract the full paragraph before the image (likely the description)
                text_before_match = all_text[:match_start]
                # Find last paragraph break before the image
                last_para_break = max(
                    text_before_match.rfind('\n\n'),
                    text_before_match.rfind('\n#'),  # Heading
                    0
                )
                paragraph_before = text_before_match[last_para_break:].strip()
                
                # Extract the full paragraph after the image (could be caption or continuation)
                text_after_match = all_text[match_end:]
                # Find next paragraph break after the image
                next_para_break = text_after_match.find('\n\n')
                if next_para_break == -1:
                    next_para_break = text_after_match.find('\n#')  # Next heading
                if next_para_break == -1:
                    next_para_break = len(text_after_match)
                paragraph_after = text_after_match[:next_para_break].strip()
                
                # Combine paragraphs to form complete image description
                image_description_parts = []
                if paragraph_before:
                    image_description_parts.append(paragraph_before)
                if paragraph_after and paragraph_after != paragraph_before:
                    image_description_parts.append(paragraph_after)
                image_description = '\n\n'.join(image_description_parts)
                
                # Also extract larger context (1000 chars before/after for additional context)
                context_before = all_text[max(0, match_start - 1000):match_start].strip()
                context_after = all_text[match_end:min(len(all_text), match_end + 1000)].strip()
                
                # Extract the section heading that contains this image
                section_heading = ""
                # Find the last heading before the image (markdown heading syntax: # Heading)
                heading_matches = list(re.finditer(r'^#{1,6}\s+(.+)$', text_before_match, re.MULTILINE))
                if heading_matches:
                    section_heading = heading_matches[-1].group(1).strip()
                
                # Check if image is base64 encoded
                if image_path.startswith('data:image'):
                    # Extract base64 data and format
                    try:
                        # Format: data:image/png;base64,iVBORw0KGgoAAAANS...
                        parts = image_path.split(',', 1)
                        if len(parts) == 2:
                            header = parts[0]
                            base64_data = parts[1]
                            
                            # Extract format from header (e.g., data:image/png;base64)
                            format_match = re.search(r'data:image/([^;]+)', header)
                            image_format = format_match.group(1) if format_match else 'png'
                            
                            image_index = len(all_images)
                            all_images.append({
                                "base64": base64_data,
                                "format": image_format,
                                "filename": f"embedded_image_{image_index}.{image_format}",
                                "url": f"embedded://image_{image_index}",
                                "alt_text": alt_text,
                                "section_heading": section_heading,
                                "image_description": image_description,
                                "paragraph_before": paragraph_before,
                                "paragraph_after": paragraph_after,
                                "context_before": context_before,
                                "context_after": context_after
                            })
                            logger.info(f"[SUCCESS] Extracted base64 embedded image with description: {len(image_description)} chars")
                    except Exception as e:
                        logger.warning(f"[WARNING] Failed to extract base64 image: {str(e)}")
                    continue
                
                # Skip external URLs
                if image_path.startswith('http'):
                    continue
                
                # Construct blob path for image (relative to document location)
                doc_dir = '/'.join(blob_name.split('/')[:-1]) if '/' in blob_name else ''
                image_blob_path = f"{doc_dir}/{image_path}" if doc_dir else image_path
                image_blob_path = image_blob_path.lstrip('/')
                
                try:
                    # Download image as base64
                    image_blob_client = blob_service_client.get_blob_client(container=container_name, blob=image_blob_path)
                    image_blob_data = image_blob_client.download_blob()
                    
                    # Check if blob download succeeded
                    if image_blob_data is None:
                        logger.warning(f"[WARNING] Image blob download returned None: {image_blob_path}")
                        continue
                    
                    image_data = image_blob_data.readall()
                    if image_data is None:
                        logger.warning(f"[WARNING] Image blob data is None: {image_blob_path}")
                        continue
                    
                    # Convert to base64
                    image_base64 = base64.b64encode(image_data).decode('utf-8')
                    
                    # Determine format from file extension
                    image_format = 'png'
                    if image_blob_path.lower().endswith('.jpg') or image_blob_path.lower().endswith('.jpeg'):
                        image_format = 'jpeg'
                    elif image_blob_path.lower().endswith('.gif'):
                        image_format = 'gif'
                    
                    all_images.append({
                        "base64": image_base64,
                        "format": image_format,
                        "filename": image_blob_path,
                        "url": f"blob://{container_name}/{image_blob_path}",
                        "alt_text": alt_text,
                        "section_heading": section_heading,
                        "image_description": image_description,
                        "paragraph_before": paragraph_before,
                        "paragraph_after": paragraph_after,
                        "context_before": context_before,
                        "context_after": context_after
                    })
                    
                    logger.info(f"[SUCCESS] Downloaded image: {image_blob_path} with description ({len(image_description)} chars)")
                except Exception as img_error:
                    logger.warning(f"[WARNING] Failed to download image {image_blob_path}: {str(img_error)}")
        
        _debug_print("extract_and_analyze_architecture: extracted", {"text_chars": len(all_text), "images": len(all_images)})
        
        if not all_images:
            return json.dumps({
                "status": "success",
                "design_doc_url": design_doc_url,
                "design_doc_content": all_text,
                "text_content_length": len(all_text),
                "total_images": 0,
                "architecture_analyses": [],
                "summary": {
                    "total_architectures_analyzed": 0,
                    "total_images_processed": 0
                }
            })
        
        # Create a SINGLE diagram analyzer agent for all images (performance optimization)
        logger.info(f"[AGENT] Creating diagram analyzer agent for {len(all_images)} images with app_id={app_id}")
        
        # Import AgentFactory
        import sys
        from pathlib import Path
        arch_analyzer_path = Path(__file__).parent.parent
        if str(arch_analyzer_path) not in sys.path:
            sys.path.insert(0, str(arch_analyzer_path))
        from agent_factory import AgentFactory
        from semantic_kernel import Kernel
        
        agent_factory = AgentFactory()
        original_endpoint = agent_factory.ai_endpoint
        agent_factory.ai_endpoint = foundry_endpoint
        
        # Load instructions once
        architecture_prompt = load_agent_instructions("foundry_image_analyzer_agent")
        if not architecture_prompt:
            architecture_prompt = get_fallback_architecture_prompt()
        
        kernel = Kernel()
        
        # AgentFactory will find existing or create new (handles auth internally)
        diagram_agent = await agent_factory.create_diagram_analyzer_agent(
            app_id=app_id,
            instructions=architecture_prompt,
            model_name=foundry_model,
            kernel=kernel
        )
        
        agent_factory.ai_endpoint = original_endpoint
        logger.info(f"[AGENT] Diagram analyzer agent ready: DiagramAnalyzer-Agent-{app_id}")
        logger.info(f"[AGENT] Agent will be used for all {len(all_images)} images")
        
        # Analyze each architecture diagram
        architecture_analyses = []
        azure_services = set()
        all_components = set()
        total_components = 0
        total_relationships = 0
        
        for idx, img in enumerate(all_images):
            try:
                filename = img.get('filename', 'unknown')
                img_size_mb = img.get('size_mb', 'unknown')
                logger.info(f"   [INFO] Analyzing diagram {idx + 1}/{len(all_images)}: {filename} ({img_size_mb} MB)")
                _debug_print(f"Analyzing image {idx+1}", filename, "size_mb=", img_size_mb)
                
                # Analyze the architecture diagram with shared agent
                logger.info(f"   [CALL] Calling _analyze_architecture_diagram_with_foundry_async with shared agent...")
                architecture_data = await _analyze_architecture_diagram_with_foundry_async(
                    img, 
                    foundry_endpoint, 
                    foundry_model,
                    diagram_agent=diagram_agent,
                    app_id=app_id
                )
                logger.info(f"   [SUCCESS] analyze_architecture_diagram completed")
                _debug_print(f"analyze_architecture_diagram returned for image {idx+1}", "nodes=", len(architecture_data.get('nodes', [])))
                
                # Extract Azure services from nodes
                nodes = architecture_data.get('nodes', [])
                edges = architecture_data.get('edges', [])
                
                # Collect components for THIS specific architecture
                arch_components = []
                arch_azure_services = []
                
                # Collect ALL component names
                for node in nodes:
                    node_label = node.get('label', '').strip()
                    if node_label:
                        arch_components.append(node_label)
                        all_components.add(node_label)
                        # Also keep track of specifically Azure services
                        if 'azure' in node_label.lower():
                            arch_azure_services.append(node_label)
                            azure_services.add(node_label)
                
                total_components += len(nodes)
                total_relationships += len(edges)
                
                architecture_analyses.append({
                    "image_index": idx,
                    "image_name": img['filename'],
                    "image_filename": img['filename'],
                    "image_url": img['url'],
                    "alt_text": img.get('alt_text', ''),
                    "section_heading": img.get('section_heading', ''),
                    "image_description": img.get('image_description', ''),
                    "paragraph_before": img.get('paragraph_before', ''),
                    "paragraph_after": img.get('paragraph_after', ''),
                    "context_before": img.get('context_before', ''),
                    "context_after": img.get('context_after', ''),
                    "architecture_data": architecture_data,
                    "components": arch_components,
                    "azure_services": arch_azure_services,
                    "nodes_count": len(nodes),
                    "edges_count": len(edges),
                    "boundaries_count": len(architecture_data.get('boundaries', []))
                })
                
                logger.info(f"   [SUCCESS] Analysis {idx + 1} complete: {len(nodes)} components, {len(edges)} relationships")
                _debug_print(f"Analysis complete for image {idx+1}", {"nodes": len(nodes), "edges": len(edges)})
                
            except Exception as e:
                error_type = type(e).__name__
                error_message = str(e)
                
                logger.error(f"   [ERROR] Failed to analyze diagram {idx + 1}: {error_type} - {error_message}")
                _debug_print(f"Error analyzing image {idx+1}", error_type, error_message)
                
                # Provide more specific error context
                suggestion = "Unknown error occurred during image analysis"
                if "Authentication" in error_message or "401" in error_message:
                    suggestion = "Authentication failed - check Azure AI Foundry access credentials"
                elif "403" in error_message:
                    suggestion = "Access forbidden - check Azure AI Foundry permissions"
                elif "404" in error_message:
                    suggestion = "Project or model not found - check configuration"
                elif "timeout" in error_message.lower():
                    suggestion = "Request timed out - try with smaller images"
                elif "json" in error_message.lower():
                    suggestion = "Model response was not valid JSON"
                
                architecture_analyses.append({
                    "image_index": idx,
                    "image_filename": img.get('filename', 'unknown'),
                    "image_url": img.get('url', 'unknown'),
                    "error": error_message,
                    "error_type": error_type,
                    "suggestion": suggestion,
                    "timestamp": str(datetime.now())
                })
        
        successful_analyses = len([a for a in architecture_analyses if "architecture_data" in a])
        failed_analyses = len([a for a in architecture_analyses if "error" in a])
        
        # Determine overall status
        if successful_analyses > 0:
            overall_status = "success"
        elif failed_analyses > 0 and failed_analyses < len(all_images):
            overall_status = "partial_success"
        else:
            overall_status = "success"
        
        result = {
            "status": overall_status,
            "design_doc_url": design_doc_url,
            "design_doc_content": all_text,
            "text_content_length": len(all_text),
            "total_images": len(all_images),
            "architecture_analyses": architecture_analyses,
            "summary": {
                "total_architectures_analyzed": successful_analyses,
                "total_images_processed": len(all_images),
                "failed_analyses": failed_analyses,
                "success_rate": f"{(successful_analyses/max(len(all_images), 1)*100):.1f}%",
                "all_components": list(all_components),
                "unique_azure_services": list(azure_services),
                "total_components_extracted": total_components,
                "total_relationships_extracted": total_relationships
            },
            "diagnostic_info": {
                "foundry_endpoint_configured": bool(foundry_endpoint),
                "blob_storage_extraction": True,
                "text_extraction_successful": len(all_text) > 0,
                "images_found_in_document": len(all_images) > 0
            }
        }
        
        logger.info(f"[COMPONENTS] Extracted {len(all_components)} total components")
        logger.info(f"[SUCCESS] Architecture analysis complete: {successful_analyses}/{len(all_images)} diagrams analyzed")
        
        # Cleanup the diagram analyzer agent and ALL its threads
        if diagram_agent:
            from agent_factory import AgentFactory
            
            # try:
            #     logger.info(f"[CLEANUP] Listing all threads for diagram agent")
            #     threads_to_delete = []
            #     async for thread in diagram_agent.client.agents.threads.list():
            #         threads_to_delete.append(thread.id)
                
            #     if threads_to_delete:
            #         logger.info(f"[CLEANUP] Found {len(threads_to_delete)} total threads, deleting them")
            #         for thread_id in threads_to_delete:
            #             try:
            #                 await diagram_agent.client.agents.threads.delete(thread_id=thread_id)
            #             except Exception as del_ex:
            #                 logger.warning(f"[CLEANUP] Could not delete thread {thread_id}: {del_ex}")
            # except Exception as list_ex:
            #     logger.warning(f"[CLEANUP] Error listing threads: {list_ex}")
            
            cleanup_result = await AgentFactory.cleanup_diagram_agent(
                app_id=app_id,
                agent=diagram_agent
            )
            logger.info(f"Diagram agent cleanup: {cleanup_result.get('message', 'completed')}")
        
        if span_context:
            add_span_attributes(span, {
                "architecture.total_architectures": successful_analyses,
                "architecture.total_images": len(all_images),
                "architecture.failed_analyses": failed_analyses,
                "architecture.success_rate": f"{(successful_analyses/max(len(all_images), 1)*100):.1f}%"
            })
            span.set_status(Status(StatusCode.OK))
        
        return json.dumps(result)
        
    except Exception as e:
        error_type = type(e).__name__
        error_details = {
            "error_type": error_type,
            "error_message": str(e),
            "design_doc_url": design_doc_url
        }
        
        logger.error(f"[ERROR] Architecture analysis failed: {error_type} - {str(e)}")
        
        if span_context:
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)[:256]))
        
        if TRACING_AVAILABLE and span_context:
            record_error_details(
                span=span,
                error_type=error_type,
                error_message=str(e),
                error_code=None,
                is_retryable=True
            )
        
        return json.dumps({
            "status": "error",
            "error_details": error_details,
            "design_doc_url": design_doc_url
        })
    finally:
        if span_context:
            span_context.__exit__(None, None, None)