# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

"""
Agent Runner Utility for Evaluation Testing.

This module provides utilities to run agents and collect responses
for evaluation using Azure AI Evaluation SDK.

It supports:
- Running individual agent queries
- Batch processing from JSONL datasets
- Response collection and storage
- Integration with Azure AI Foundry agents

All operations use real Azure connections - no mocking is used.

Usage:
    # Run as script
    python tests/evaluation/agent_runner.py --dataset datasets/design_queries.jsonl
    
    # Import as module
    from tests.evaluation.agent_runner import AgentRunner
    runner = AgentRunner(api_url="https://your-api.azurecontainerapps.io")
    responses = await runner.collect_responses("design_queries.jsonl")

Prerequisites:
    - Azure AI Foundry project configured
    - Insights API deployed and accessible
    - Azure credentials configured (DefaultAzureCredential)

Environment Variables:
    API_BASE_URL: URL of the deployed Insights API
    AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_TENANT_ID: Service principal credentials
"""

import os
import sys
import json
import asyncio
import logging
import argparse
import httpx
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

# Load environment variables from .env.test file
env_file = Path(__file__).parent.parent.parent / ".env.test"
if env_file.exists():
    load_dotenv(env_file)
    print(f"✓ Loaded environment variables from {env_file}")
else:
    print(f"⚠ .env.test not found at {env_file}, using system environment variables")

# Add project root for imports
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Import test helpers
from tests.integration.test_helpers import poll_operation_until_complete

# Configure logging
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# =============================================================================
# Virtual Directory Mapping
# =============================================================================

# Maps each API endpoint to its Azure Storage virtual directories
# Note: The container name IS the app_id. These are relative paths within that container.
# Each endpoint reads from its input directory and writes to its output directory
ENDPOINT_VIRTUAL_DIRECTORIES: Dict[str, Dict[str, str]] = {
    "/generateDesign": {
        "input": "design/input",
        "output": "design/output",
    },
    "/generateAssessmentReport": {
        "input": "asr/input",
        "output": "asr/output",
    },
    "/generateAppPlan": {
        "input": "app-planning/input",
        "output": "app-planning/output",
    },
    "/analyzeArchitecture": {
        "input": "architecture-analyzer/input",
        "output": "architecture-analyzer/output",
    },
    "/analyzeCode": {
        "input": "code-analyzer/input",
        "output": "code-analyzer/output",
    },
    "/discoverKubernetes": {
        "input": "kubernetes-discovery/input",
        "output": "kubernetes-discovery/output",
    },
    "/runAnalysis": {
        "input": "responder/input",
        "output": "responder/output",
    },
}


def get_virtual_directories_for_endpoint(endpoint: str) -> Dict[str, str]:
    """
    Get the virtual directories for a given endpoint.
    
    Args:
        endpoint: The API endpoint (e.g., "/generateDesign")
        
    Returns:
        Dict with 'input' and 'output' directory paths
        
    Raises:
        KeyError: If the endpoint is not recognized
    """
    # Normalize endpoint - ensure it starts with /
    normalized = endpoint if endpoint.startswith("/") else f"/{endpoint}"
    
    if normalized not in ENDPOINT_VIRTUAL_DIRECTORIES:
        raise KeyError(
            f"Unknown endpoint: {endpoint}. "
            f"Valid endpoints: {list(ENDPOINT_VIRTUAL_DIRECTORIES.keys())}"
        )
    
    return ENDPOINT_VIRTUAL_DIRECTORIES[normalized]


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class AgentRequest:
    """
    Represents a structured API request to an agent endpoint.
    
    This class matches the actual API parameter structure instead of using
    free-form queries.
    """
    app_id: str
    storage_account_name: str
    endpoint: str  # The target API endpoint
    user_object_id: Optional[str] = None
    group_object_id: Optional[str] = None
    resource_group_name: Optional[str] = None
    azure_region: Optional[str] = None
    design_doc_url: Optional[str] = None
    repo_url: Optional[str] = None
    perform_security_scan: bool = False
    analysis_options: Optional[Dict[str, Any]] = None
    test_scenario: Optional[str] = None
    expected_status: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    
    def to_api_payload(self) -> Dict[str, Any]:
        """
        Convert the request to an API payload dictionary.
        
        Only includes non-None fields that are valid API parameters.
        """
        # Common fields for all endpoints
        payload = {
            "app_id": self.app_id,
            "storage_account_name": self.storage_account_name,
        }
        
        # Add authentication field (user_object_id or group_object_id)
        if self.user_object_id:
            payload["user_object_id"] = self.user_object_id
        if self.group_object_id:
            payload["group_object_id"] = self.group_object_id
        
        # Optional common fields
        if self.resource_group_name:
            payload["resource_group_name"] = self.resource_group_name
        
        # Endpoint-specific fields
        if self.azure_region:
            payload["azure_region"] = self.azure_region
        if self.design_doc_url:
            payload["design_doc_url"] = self.design_doc_url
        if self.repo_url:
            payload["repo_url"] = self.repo_url
        if self.perform_security_scan:
            payload["perform_security_scan"] = self.perform_security_scan
        if self.analysis_options:
            payload["analysis_options"] = self.analysis_options
        
        return payload


@dataclass
class AgentQuery:
    """[DEPRECATED] Represents a legacy query to an agent. Use AgentRequest instead."""
    query: str
    app_id: str
    context: Optional[str] = None
    expected_agent: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class AgentResponse:
    """Represents a response from an agent."""
    query: str  # The actual query/request sent to the agent
    response: str  # Can be empty if response_file is used
    app_id: str
    endpoint: str
    timestamp: str
    duration_ms: float
    success: bool
    error: Optional[str] = None
    operation_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    response_file: Optional[str] = None  # Path to downloaded file (relative to responses dir)
    
    @property
    def agent_name(self) -> str:
        """Backwards compatibility - returns endpoint without leading slash."""
        return self.endpoint.lstrip("/")


@dataclass
class CollectionResult:
    """Result of a response collection operation."""
    total_requests: int
    successful: int
    failed: int
    responses: List[AgentResponse]
    duration_seconds: float
    
    # Legacy compatibility
    @property
    def total_queries(self) -> int:
        """Backwards compatibility."""
        return self.total_requests


# =============================================================================
# Agent Runner Class
# =============================================================================

class AgentRunner:
    """
    Utility class to run agents and collect responses for evaluation.
    
    This class provides methods to:
    - Send queries to deployed agents via the Insights API
    - Collect and store responses in JSONL format
    - Track timing and success metrics
    - Download generated output files from Azure Blob Storage
    
    Args:
        api_url: URL of the deployed Insights API
        storage_account_name: Azure Storage Account name for downloading agent outputs
        timeout: Request timeout in seconds (default: 120)
        credential: Azure credential for authentication (optional)
    """
    
    # Blob storage path mappings for each endpoint
    BLOB_OUTPUT_PATHS = {
        "/runAnalysis": "responder/output",
        "/generateAssessmentReport": "asr/output",
        "/generateDesign": "design/output",
        "/generateAppPlan": "app-planning/output",
        "/discoverKubernetes": "kubernetes-discovery/output",
        "/analyzeCode": "code-analyzer/output",
        "/analyzeArchitecture": "architecture-analyzer/output"
    }
    
    def __init__(
        self,
        api_url: Optional[str] = None,
        storage_account_name: Optional[str] = None,
        timeout: float = 120.0,
        credential: Optional[DefaultAzureCredential] = None
    ):
        self.api_url = api_url or os.environ.get("API_BASE_URL")
        if not self.api_url:
            raise ValueError(
                "API URL required. Set API_BASE_URL environment variable "
                "or pass api_url parameter."
            )
        
        self.storage_account_name = storage_account_name or os.environ.get("AZURE_STORAGE_ACCOUNT_NAME")
        self.timeout = timeout
        self.credential = credential or DefaultAzureCredential()
        
        # Initialize blob service client if storage account is configured
        self.blob_service_client = None
        if self.storage_account_name:
            account_url = f"https://{self.storage_account_name}.blob.core.windows.net"
            self.blob_service_client = BlobServiceClient(
                account_url=account_url,
                credential=self.credential
            )
            logger.info(f"Blob Storage client initialized with managed identity: {account_url}")
        else:
            logger.warning("No storage account configured - will use API response body only")
        
        logger.info(f"AgentRunner initialized")
        logger.info(f"  API URL: {self.api_url}")
        logger.info(f"  Timeout: {self.timeout}s")
        
        # Queue for pre-fetched blob files (used when collecting multiple responses)
        self.blob_queue = []
    
    def _prefetch_blob_files(
        self,
        app_id: str,
        endpoint: str,
        count: int
    ) -> List[str]:
        """
        Pre-fetch N most recent blob files for an endpoint.
        
        This ensures that when processing multiple requests, each request gets
        a unique file (newest to latest request, 2nd newest to 2nd-to-last, etc.)
        
        Args:
            app_id: Application ID (container name)
            endpoint: API endpoint to get blob path for
            count: Number of most recent blobs to fetch
            
        Returns:
            List of blob names, sorted from oldest to newest of the selected N
        """
        if not self.blob_service_client:
            logger.warning("Blob service client not configured")
            return []
        
        try:
            output_path = self.BLOB_OUTPUT_PATHS.get(endpoint)
            if not output_path:
                logger.warning(f"No blob path mapping for endpoint: {endpoint}")
                return []
            
            blob_prefix = f"{output_path}/"
            container_client = self.blob_service_client.get_container_client(app_id)
            
            # List all blobs
            blobs = list(container_client.list_blobs(name_starts_with=blob_prefix))
            
            if not blobs:
                logger.warning(f"No blobs found in {app_id}/{blob_prefix}")
                return []
            
            # Sort by last_modified (newest first)
            blobs_sorted_desc = sorted(blobs, key=lambda b: b.last_modified, reverse=True)
            
            # Take top N (most recent)
            selected_blobs = blobs_sorted_desc[:count]
            
            # Return in oldest-to-newest order (so first request gets oldest of selected N)
            selected_blobs.reverse()
            
            blob_names = [b.name for b in selected_blobs]
            
            logger.info(f"Pre-fetched {len(blob_names)} blob(s) from {app_id}/{blob_prefix}")
            for i, name in enumerate(blob_names):
                logger.debug(f"  [{i+1}] {name}")
            
            return blob_names
            
        except Exception as e:
            logger.error(f"Failed to pre-fetch blobs: {e}")
            return []
    
    def _download_output_from_blob(
        self,
        app_id: str,
        endpoint: str,
        save_to_file: bool = True,
        blob_name: Optional[str] = None
    ) -> tuple:
        """
        Download the generated output file from Azure Blob Storage.
        
        Args:
            app_id: Application ID (used as container name)
            endpoint: Endpoint path (e.g., "/analyzeCode")
            save_to_file: If True, save to local file and return path; if False, return content
            blob_name: Optional specific blob name to download (for pre-fetched blobs)
            
        Returns:
            Tuple of (content, file_path) - either can be None depending on save_to_file
        """
        if not self.blob_service_client:
            logger.warning("Blob storage not configured - cannot download output files")
            return None, None
        
        try:
            output_path = self.BLOB_OUTPUT_PATHS.get(endpoint)
            if not output_path:
                logger.warning(f"No blob path mapping for endpoint: {endpoint}")
                return None, None
            
            # Container name is the app_id (e.g., "50000")
            # Blob path is: {output_path}/ (e.g., "code-analyzer/output/")
            blob_prefix = f"{output_path}/"
            
            container_client = self.blob_service_client.get_container_client(app_id)
            
            # Use provided blob_name if available, otherwise find most recent
            if blob_name:
                logger.info(f"Using pre-assigned blob: {blob_name}")
            else:
                # List blobs in the output directory
                blobs = list(container_client.list_blobs(name_starts_with=blob_prefix))
                
                if not blobs:
                    logger.warning(f"No output files found in blob path: {app_id}/{blob_prefix}")
                    return None, None
                
                # Get the most recent blob (by last_modified)
                latest_blob = max(blobs, key=lambda b: b.last_modified)
                blob_name = latest_blob.name
            
            logger.info(f"Downloading output from blob: {app_id}/{blob_name}")
            
            blob_client = container_client.get_blob_client(blob_name)
            blob_data = blob_client.download_blob()
            content = blob_data.readall().decode('utf-8')
            
            if save_to_file:
                # Create downloaded directory if it doesn't exist
                downloaded_dir = Path("tests/evaluation/responses/downloaded")
                downloaded_dir.mkdir(parents=True, exist_ok=True)
                
                # Generate filename: endpoint_appid_timestamp.md
                endpoint_name = endpoint.lstrip("/").replace("/", "_")
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"{endpoint_name}_{app_id}_{timestamp}.md"
                file_path = downloaded_dir / filename
                
                # Save content to file
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)
                
                # Return relative path from responses directory
                relative_path = f"downloaded/{filename}"
                logger.info(f"Saved {len(content)} characters to {relative_path}")
                return None, relative_path
            else:
                logger.info(f"Downloaded {len(content)} characters from {blob_name}")
                return content, None
            
        except Exception as e:
            logger.error(f"Failed to download output from blob storage: {e}")
            return None, None
    
    async def _get_auth_token(self) -> str:
        """Get authentication token for API requests."""
        try:
            # Get token for Azure management scope
            token = self.credential.get_token("https://management.azure.com/.default")
            return token.token
        except Exception as e:
            logger.warning(f"Failed to get auth token: {e}")
            return ""
    
    def _build_query_string(self, request: AgentRequest, endpoint: str) -> str:
        """Build a human-readable query string from the request for evaluation."""
        if request.test_scenario:
            return request.test_scenario
        
        # Build query based on endpoint type
        if endpoint in ["/generateDesign", "design"]:
            return f"Generate Azure migration design for application {request.app_id}"
        elif endpoint in ["/generateAssessmentReport", "asr"]:
            return f"Generate migration assessment report for application {request.app_id}"
        elif endpoint in ["/analyzeCode", "code"]:
            repo_hint = f" from {request.repo_url}" if request.repo_url else ""
            return f"Analyze code for application {request.app_id}{repo_hint}"
        elif endpoint in ["/analyzeArchitecture", "architecture"]:
            design_hint = f" using design document" if request.design_doc_url else ""
            return f"Analyze architecture for application {request.app_id}{design_hint}"
        else:
            return f"Process request for application {request.app_id} via {endpoint}"
    
    async def _send_request(
        self,
        client: httpx.AsyncClient,
        endpoint: str,
        payload: Dict[str, Any],
        timeout: Optional[float] = None
    ) -> Dict[str, Any]:
        """Send a request to the API endpoint."""
        url = f"{self.api_url.rstrip('/')}/{endpoint.lstrip('/')}"
        
        token = await self._get_auth_token()
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        
        # Use provided timeout or default to 30 seconds for initial requests
        request_timeout = timeout if timeout is not None else 30.0
        
        logger.debug(f"Sending POST to: {url}")
        logger.debug(f"  Timeout: {request_timeout}s")
        logger.debug(f"  Payload: {payload}")
        
        try:
            response = await client.post(
                url,
                json=payload,
                headers=headers,
                timeout=request_timeout
            )
            
            logger.debug(f"Response status: {response.status_code}")
            
            response.raise_for_status()
            result = response.json()
            logger.debug(f"Response data: {result}")
            
            return result
            
        except httpx.TimeoutException as e:
            logger.error(f"Request timeout after {request_timeout}s: {url}")
            raise
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error {e.response.status_code}: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Request failed: {type(e).__name__}: {e}")
            raise
    
    async def run_design_agent(
        self,
        client: httpx.AsyncClient,
        request: AgentRequest
    ) -> AgentResponse:
        """Run the design agent with structured request parameters and poll for completion."""
        start_time = datetime.now()
        
        try:
            payload = request.to_api_payload()
            
            # Initial request returns operation_id
            result = await self._send_request(client, "/generateDesign", payload)
            
            operation_id = result.get("operation_id")
            if not operation_id:
                raise ValueError("No operation_id returned from design endpoint")
            
            logger.info(f"Design operation started: {operation_id}")
            
            # Build integration config for polling
            integration_config = {
                "app_id": request.app_id,
                "user_object_id": request.user_object_id,
                "storage_account_name": request.storage_account_name
            }
            
            # Build endpoint URLs for polling
            status_endpoint = f"{self.api_url.rstrip('/')}/operations/{operation_id}/status"
            result_endpoint = f"{self.api_url.rstrip('/')}/operations/{operation_id}/result"
            
            # Poll until operation completes
            final_result = await poll_operation_until_complete(
                http_client=client,
                integration_config=integration_config,
                operation_id=operation_id,
                status_endpoint=status_endpoint,
                result_endpoint=result_endpoint,
                max_wait_time=3600,  # 1 hour
                poll_interval=5
            )
            
            duration = (datetime.now() - start_time).total_seconds() * 1000
            
            # Download the generated design output from blob storage
            content, response_file = self._download_output_from_blob(
                app_id=request.app_id,
                endpoint="/generateDesign",
                save_to_file=True  # Save to file instead of storing in JSONL
            )
            
            # If blob download failed, fall back to API response
            if not response_file:
                logger.warning("Blob download failed, falling back to API response body")
                design_response = final_result.get("data", {}).get("design_response", "")
                if not design_response:
                    design_response = final_result.get("message", "")
            else:
                design_response = ""  # Empty since content is in file
                logger.info(f"Saved design output to {response_file}")
            
            return AgentResponse(
                query=self._build_query_string(request, "/generateDesign"),
                response=design_response,  # Empty if saved to file
                app_id=request.app_id,
                endpoint="/generateDesign",
                timestamp=datetime.now().isoformat(),
                duration_ms=duration,
                success=True,
                operation_id=operation_id,
                metadata=request.metadata,
                response_file=response_file  # Reference to downloaded file
            )
            
        except Exception as e:
            duration = (datetime.now() - start_time).total_seconds() * 1000
            logger.error(f"Design agent failed: {e}")
            
            return AgentResponse(
                query=self._build_query_string(request, "/generateDesign"),
                response="",
                app_id=request.app_id,
                endpoint="/generateDesign",
                timestamp=datetime.now().isoformat(),
                duration_ms=duration,
                success=False,
                error=str(e),
                metadata=request.metadata
            )
    
    async def run_asr_agent(
        self,
        client: httpx.AsyncClient,
        request: AgentRequest
    ) -> AgentResponse:
        """Run the ASR (Migration Assessment Report) agent with polling for completion."""
        start_time = datetime.now()
        
        try:
            payload = request.to_api_payload()
            
            # Initial request returns operation_id
            result = await self._send_request(
                client, 
                "/generateAssessmentReport", 
                payload
            )
            
            operation_id = result.get("operation_id")
            if not operation_id:
                raise ValueError("No operation_id returned from ASR endpoint")
            
            logger.info(f"ASR operation started: {operation_id}")
            
            # Build integration config for polling
            integration_config = {
                "app_id": request.app_id,
                "user_object_id": request.user_object_id,
                "storage_account_name": request.storage_account_name
            }
            
            # Build endpoint URLs for polling
            status_endpoint = f"{self.api_url.rstrip('/')}/operations/{operation_id}/status"
            result_endpoint = f"{self.api_url.rstrip('/')}/operations/{operation_id}/result"
            
            # Poll until operation completes
            final_result = await poll_operation_until_complete(
                http_client=client,
                integration_config=integration_config,
                operation_id=operation_id,
                status_endpoint=status_endpoint,
                result_endpoint=result_endpoint,
                max_wait_time=3600,  # 1 hour
                poll_interval=5
            )
            
            duration = (datetime.now() - start_time).total_seconds() * 1000
            
            # Download the generated ASR report from blob storage
            content, response_file = self._download_output_from_blob(
                app_id=request.app_id,
                endpoint="/generateAssessmentReport",
                save_to_file=True  # Save to file instead of storing in JSONL
            )
            
            # If blob download failed, fall back to API response
            if not response_file:
                logger.warning("Blob download failed, falling back to API response body")
                asr_response = final_result.get("data", {}).get("asr_response", "")
                if not asr_response:
                    asr_response = final_result.get("message", "")
            else:
                asr_response = ""  # Empty since content is in file
                logger.info(f"Saved ASR report to {response_file}")
            
            return AgentResponse(
                query=self._build_query_string(request, "/generateAssessmentReport"),
                response=asr_response,  # Empty if saved to file
                app_id=request.app_id,
                endpoint="/generateAssessmentReport",
                timestamp=datetime.now().isoformat(),
                duration_ms=duration,
                success=True,
                operation_id=operation_id,
                metadata=request.metadata,
                response_file=response_file  # Reference to downloaded file
            )
            
        except Exception as e:
            duration = (datetime.now() - start_time).total_seconds() * 1000
            logger.error(f"ASR agent failed: {e}")
            
            return AgentResponse(
                query=self._build_query_string(request, "/generateAssessmentReport"),
                response="",
                app_id=request.app_id,
                endpoint="/generateAssessmentReport",
                timestamp=datetime.now().isoformat(),
                duration_ms=duration,
                success=False,
                error=str(e),
                metadata=request.metadata
            )
    
    async def run_analysis_agent(
        self,
        client: httpx.AsyncClient,
        request: AgentRequest
    ) -> AgentResponse:
        """Run the analysis (orchestrator) agent with structured request parameters and poll for completion."""
        start_time = datetime.now()
        
        try:
            payload = request.to_api_payload()
            
            # Initial request returns operation_id
            result = await self._send_request(client, "/runAnalysis", payload)
            
            operation_id = result.get("operation_id")
            if not operation_id:
                raise ValueError("No operation_id returned from analysis endpoint")
            
            logger.info(f"Analysis operation started: {operation_id}")
            
            # Build integration config for polling
            integration_config = {
                "app_id": request.app_id,
                "user_object_id": request.user_object_id,
                "storage_account_name": request.storage_account_name
            }
            
            # Build endpoint URLs for polling
            status_endpoint = f"{self.api_url.rstrip('/')}/operations/{operation_id}/status"
            result_endpoint = f"{self.api_url.rstrip('/')}/operations/{operation_id}/result"
            
            # Poll until operation completes
            final_result = await poll_operation_until_complete(
                http_client=client,
                integration_config=integration_config,
                operation_id=operation_id,
                status_endpoint=status_endpoint,
                result_endpoint=result_endpoint,
                max_wait_time=3600,  # 1 hour
                poll_interval=5
            )
            
            duration = (datetime.now() - start_time).total_seconds() * 1000
            
            # Download the generated analysis output from blob storage
            content, response_file = self._download_output_from_blob(
                app_id=request.app_id,
                endpoint="/runAnalysis",
                save_to_file=True  # Save to file instead of storing in JSONL
            )
            
            # If blob download failed, fall back to API response
            if not response_file:
                logger.warning("Blob download failed, falling back to API response body")
                analysis_response = final_result.get("data", {}).get("analysis_response", "")
                if not analysis_response:
                    analysis_response = final_result.get("message", "")
            else:
                analysis_response = ""  # Empty since content is in file
                logger.info(f"Saved analysis output to {response_file}")
            
            return AgentResponse(
                query=self._build_query_string(request, "/runAnalysis"),
                response=analysis_response,  # Empty if saved to file
                app_id=request.app_id,
                endpoint="/runAnalysis",
                timestamp=datetime.now().isoformat(),
                duration_ms=duration,
                success=True,
                operation_id=operation_id,
                metadata=request.metadata,
                response_file=response_file  # Reference to downloaded file
            )
            
        except Exception as e:
            duration = (datetime.now() - start_time).total_seconds() * 1000
            logger.error(f"Analysis agent failed: {e}")
            
            return AgentResponse(
                query=self._build_query_string(request, "/runAnalysis"),
                response="",
                app_id=request.app_id,
                endpoint="/runAnalysis",
                timestamp=datetime.now().isoformat(),
                duration_ms=duration,
                success=False,
                error=str(e),
                metadata=request.metadata
            )
    
    async def run_architecture_analysis(
        self,
        client: httpx.AsyncClient,
        request: AgentRequest
    ) -> AgentResponse:
        """Run architecture analysis with structured request parameters and poll for completion."""
        start_time = datetime.now()
        
        try:
            payload = request.to_api_payload()
            
            # Initial request returns operation_id
            result = await self._send_request(client, "/analyzeArchitecture", payload)
            
            operation_id = result.get("operation_id")
            if not operation_id:
                raise ValueError("No operation_id returned from architecture analysis endpoint")
            
            logger.info(f"Architecture analysis operation started: {operation_id}")
            
            # Build integration config for polling
            integration_config = {
                "app_id": request.app_id,
                "user_object_id": request.user_object_id,
                "storage_account_name": request.storage_account_name
            }
            
            # Build endpoint URLs for polling
            status_endpoint = f"{self.api_url.rstrip('/')}/operations/{operation_id}/status"
            result_endpoint = f"{self.api_url.rstrip('/')}/operations/{operation_id}/result"
            
            # Poll until operation completes
            final_result = await poll_operation_until_complete(
                http_client=client,
                integration_config=integration_config,
                operation_id=operation_id,
                status_endpoint=status_endpoint,
                result_endpoint=result_endpoint,
                max_wait_time=3600,  # 1 hour
                poll_interval=5
            )
            
            duration = (datetime.now() - start_time).total_seconds() * 1000
            
            # Download the generated architecture analysis output from blob storage
            content, response_file = self._download_output_from_blob(
                app_id=request.app_id,
                endpoint="/analyzeArchitecture",
                save_to_file=True  # Save to file instead of storing in JSONL
            )
            
            # If blob download failed, fall back to API response
            if not response_file:
                logger.warning("Blob download failed, falling back to API response body")
                architecture_response = final_result.get("data", {}).get("architecture_analysis", "")
                if not architecture_response:
                    architecture_response = final_result.get("message", "")
            else:
                architecture_response = ""  # Empty since content is in file
                logger.info(f"Saved architecture analysis output to {response_file}")
            
            return AgentResponse(
                query=self._build_query_string(request, "/analyzeArchitecture"),
                response=architecture_response,  # Empty if saved to file
                app_id=request.app_id,
                endpoint="/analyzeArchitecture",
                timestamp=datetime.now().isoformat(),
                duration_ms=duration,
                success=True,
                operation_id=operation_id,
                metadata=request.metadata,
                response_file=response_file  # Reference to downloaded file
            )
            
        except Exception as e:
            duration = (datetime.now() - start_time).total_seconds() * 1000
            logger.error(f"Architecture analysis failed: {e}")
            
            return AgentResponse(
                query=self._build_query_string(request, "/analyzeArchitecture"),
                response="",
                app_id=request.app_id,
                endpoint="/analyzeArchitecture",
                timestamp=datetime.now().isoformat(),
                duration_ms=duration,
                success=False,
                error=str(e),
                metadata=request.metadata
            )
    
    async def run_code_analysis(
        self,
        client: httpx.AsyncClient,
        request: AgentRequest
    ) -> AgentResponse:
        """Run code analysis with structured request parameters and poll for completion."""
        start_time = datetime.now()
        
        try:
            payload = request.to_api_payload()
            
            # Initial request returns operation_id
            result = await self._send_request(client, "/analyzeCode", payload)
            
            operation_id = result.get("operation_id")
            if not operation_id:
                raise ValueError("No operation_id returned from code analysis endpoint")
            
            logger.info(f"Code analysis operation started: {operation_id}")
            
            # Build configuration for polling
            integration_config = {
                "app_id": request.app_id,
                "user_object_id": request.user_object_id,
                "storage_account_name": request.storage_account_name
            }
            
            # Build endpoint URLs for polling
            status_endpoint = f"{self.api_url.rstrip('/')}/operations/{operation_id}/status"
            result_endpoint = f"{self.api_url.rstrip('/')}/operations/{operation_id}/result"
            
            # Poll until operation completes (up to 10 minutes)
            final_result = await poll_operation_until_complete(
                http_client=client,
                integration_config=integration_config,
                operation_id=operation_id,
                status_endpoint=status_endpoint,
                result_endpoint=result_endpoint,
                max_wait_time=3600,  # 1 hour for code analysis
                poll_interval=5
            )
            
            duration = (datetime.now() - start_time).total_seconds() * 1000
            
            # Get pre-assigned blob name if available (ensures correct file-to-request mapping)
            assigned_blob = None
            if self.blob_queue:
                assigned_blob = self.blob_queue.pop(0)  # Get next blob in queue (oldest first)
                logger.info(f"Using pre-assigned blob: {assigned_blob}")
            
            # Download the generated output file from blob storage and save to file
            content, response_file = self._download_output_from_blob(
                app_id=request.app_id,
                endpoint="/analyzeCode",
                save_to_file=True,  # Save to file instead of storing in JSONL
                blob_name=assigned_blob  # Use pre-assigned blob if available
            )
            
            # If blob download failed, fall back to API response (stored inline)
            if not response_file:
                logger.warning("Blob download failed, falling back to API response body")
                code_response = final_result.get("data", {}).get("code_analysis", "")
                if not code_response:
                    code_response = final_result.get("message", "")
            else:
                code_response = ""  # Empty since content is in file
                logger.info(f"Saved code analysis to {response_file}")
            
            return AgentResponse(
                query=self._build_query_string(request, "/analyzeCode"),
                response=code_response,  # Empty if saved to file
                app_id=request.app_id,
                endpoint="/analyzeCode",
                timestamp=datetime.now().isoformat(),
                duration_ms=duration,
                success=True,
                operation_id=operation_id,
                metadata=request.metadata,
                response_file=response_file  # Reference to downloaded file
            )
            
        except Exception as e:
            duration = (datetime.now() - start_time).total_seconds() * 1000
            logger.error(f"Code analysis failed: {e}")
            
            return AgentResponse(
                query=self._build_query_string(request, "/analyzeCode"),
                response="",
                app_id=request.app_id,
                endpoint="/analyzeCode",
                timestamp=datetime.now().isoformat(),
                duration_ms=duration,
                success=False,
                error=str(e),
                metadata=request.metadata
            )
    
    async def run_kubernetes_discovery(
        self,
        client: httpx.AsyncClient,
        request: AgentRequest
    ) -> AgentResponse:
        """Run Kubernetes discovery with structured request parameters and poll for completion."""
        start_time = datetime.now()
        
        try:
            payload = request.to_api_payload()
            
            # Initial request returns operation_id
            result = await self._send_request(client, "/kubernetesDiscovery", payload)
            
            operation_id = result.get("operation_id")
            if not operation_id:
                raise ValueError("No operation_id returned from kubernetes discovery endpoint")
            
            logger.info(f"Kubernetes discovery operation started: {operation_id}")
            
            # Build integration config for polling
            integration_config = {
                "app_id": request.app_id,
                "user_object_id": request.user_object_id,
                "storage_account_name": request.storage_account_name
            }
            
            # Build endpoint URLs for polling
            status_endpoint = f"{self.api_url.rstrip('/')}/operations/{operation_id}/status"
            result_endpoint = f"{self.api_url.rstrip('/')}/operations/{operation_id}/result"
            
            # Poll until operation completes
            final_result = await poll_operation_until_complete(
                http_client=client,
                integration_config=integration_config,
                operation_id=operation_id,
                status_endpoint=status_endpoint,
                result_endpoint=result_endpoint,
                max_wait_time=3600,  # 1 hour
                poll_interval=5
            )
            
            duration = (datetime.now() - start_time).total_seconds() * 1000
            
            # Download the generated kubernetes discovery output from blob storage
            # Note: The blob path uses /discoverKubernetes not /kubernetesDiscovery
            content, response_file = self._download_output_from_blob(
                app_id=request.app_id,
                endpoint="/discoverKubernetes",  # Use blob path mapping key
                save_to_file=True  # Save to file instead of storing in JSONL
            )
            
            # If blob download failed, fall back to API response
            if not response_file:
                logger.warning("Blob download failed, falling back to API response body")
                discovery_response = final_result.get("data", {}).get("discovery_response", "")
                if not discovery_response:
                    discovery_response = final_result.get("message", "")
            else:
                discovery_response = ""  # Empty since content is in file
                logger.info(f"Saved kubernetes discovery output to {response_file}")
            
            return AgentResponse(
                query=self._build_query_string(request, "/kubernetesDiscovery"),
                response=discovery_response,  # Empty if saved to file
                app_id=request.app_id,
                endpoint="/kubernetesDiscovery",
                timestamp=datetime.now().isoformat(),
                duration_ms=duration,
                success=True,
                operation_id=operation_id,
                metadata=request.metadata,
                response_file=response_file  # Reference to downloaded file
            )
            
        except Exception as e:
            duration = (datetime.now() - start_time).total_seconds() * 1000
            logger.error(f"Kubernetes discovery failed: {e}")
            
            return AgentResponse(
                query=self._build_query_string(request, "/kubernetesDiscovery"),
                response="",
                app_id=request.app_id,
                endpoint="/kubernetesDiscovery",
                timestamp=datetime.now().isoformat(),
                duration_ms=duration,
                success=False,
                error=str(e),
                metadata=request.metadata
            )
    
    async def run_request(
        self,
        client: httpx.AsyncClient,
        request: AgentRequest
    ) -> AgentResponse:
        """
        Run a request against the appropriate endpoint.
        
        Args:
            client: HTTP client instance
            request: The structured API request
            
        Returns:
            AgentResponse with the result
        """
        endpoint_map = {
            "/generateDesign": self.run_design_agent,
            "/generateAssessmentReport": self.run_asr_agent,
            "/runAnalysis": self.run_analysis_agent,
            "/analyzeArchitecture": self.run_architecture_analysis,
            "/analyzeCode": self.run_code_analysis,
            "/kubernetesDiscovery": self.run_kubernetes_discovery,
        }
        
        endpoint = request.endpoint
        if endpoint not in endpoint_map:
            raise ValueError(f"Unknown endpoint: {endpoint}. Valid endpoints: {list(endpoint_map.keys())}")
        
        return await endpoint_map[endpoint](client, request)
    
    async def collect_responses(
        self,
        dataset_path: Path,
        agent_name: str = "orchestrator",
        limit: Optional[int] = None
    ) -> CollectionResult:
        """
        Collect responses for all requests in a dataset.
        
        Supports both new request-based datasets (*_requests.jsonl) and
        legacy query-based datasets (*_queries.jsonl).
        
        Args:
            dataset_path: Path to JSONL dataset file
            agent_name: Agent/endpoint to use (used for legacy datasets)
            limit: Maximum number of requests to process
            
        Returns:
            CollectionResult with all responses
        """
        start_time = datetime.now()
        
        # Detect dataset type and load appropriately
        is_request_based = "_requests" in dataset_path.stem
        
        if is_request_based:
            requests = self._load_request_dataset(dataset_path)
        else:
            # Legacy query-based dataset
            logger.warning(f"Using legacy query-based dataset: {dataset_path.name}")
            requests = self._load_legacy_dataset(dataset_path, agent_name)
        
        if limit:
            requests = requests[:limit]
        
        logger.info(f"Processing {len(requests)} requests from {dataset_path.name}")
        
        # Pre-fetch blob files if this is a code analysis endpoint
        # This ensures each request gets a unique file in order
        if requests and requests[0].endpoint == "/analyzeCode" and len(requests) > 0:
            app_id = requests[0].app_id  # All requests should use same app_id
            self.blob_queue = self._prefetch_blob_files(
                app_id=app_id,
                endpoint="/analyzeCode",
                count=len(requests)
            )
            logger.info(f"Pre-fetched {len(self.blob_queue)} blob files for {len(requests)} requests")
        else:
            self.blob_queue = []
        
        responses = []
        successful = 0
        failed = 0
        
        async with httpx.AsyncClient() as client:
            for i, request in enumerate(requests, 1):
                scenario = request.test_scenario or request.app_id
                logger.info(f"Processing request {i}/{len(requests)}: {scenario[:50]}...")
                
                response = await self.run_request(client, request)
                responses.append(response)
                
                if response.success:
                    successful += 1
                    logger.info(f"  ✅ Success ({response.duration_ms:.0f}ms)")
                else:
                    failed += 1
                    logger.error(f"  ❌ Failed: {response.error}")
                
                # Small delay between requests
                await asyncio.sleep(1)
        
        duration = (datetime.now() - start_time).total_seconds()
        
        return CollectionResult(
            total_requests=len(requests),
            successful=successful,
            failed=failed,
            responses=responses,
            duration_seconds=duration
        )
    
    def _load_request_dataset(self, dataset_path: Path) -> List[AgentRequest]:
        """Load requests from a new-format JSONL dataset file."""
        requests = []
        
        # Infer endpoint from filename
        endpoint = self._infer_endpoint_from_filename(dataset_path.stem)
        
        with open(dataset_path) as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                    
                try:
                    data = json.loads(line)
                    
                    request = AgentRequest(
                        app_id=data.get("app_id", f"test-app-{line_num:03d}"),
                        storage_account_name=data.get("storage_account_name", "teststorage"),
                        endpoint=data.get("endpoint", endpoint),
                        user_object_id=data.get("user_object_id"),
                        group_object_id=data.get("group_object_id"),
                        resource_group_name=data.get("resource_group_name"),
                        azure_region=data.get("azure_region"),
                        design_doc_url=data.get("design_doc_url"),
                        repo_url=data.get("repo_url"),
                        perform_security_scan=data.get("perform_security_scan", False),
                        analysis_options=data.get("analysis_options"),
                        test_scenario=data.get("test_scenario"),
                        expected_status=data.get("expected_status"),
                        metadata=data.get("metadata")
                    )
                    requests.append(request)
                    
                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse line {line_num}: {e}")
        
        logger.info(f"Loaded {len(requests)} requests from {dataset_path.name}")
        return requests
    
    def _load_legacy_dataset(self, dataset_path: Path, agent_name: str) -> List[AgentRequest]:
        """Load legacy query-based dataset and convert to AgentRequest format."""
        requests = []
        
        endpoint_mapping = {
            "design": "/generateDesign",
            "asr": "/generateAssessmentReport",
            "orchestrator": "/runAnalysis",
        }
        endpoint = endpoint_mapping.get(agent_name, "/runAnalysis")
        
        with open(dataset_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                    
                try:
                    data = json.loads(line)
                    
                    # Convert legacy query to AgentRequest
                    request = AgentRequest(
                        app_id=data.get("app_id", "TEST001"),
                        storage_account_name="legacy-storage",
                        endpoint=endpoint,
                        test_scenario=data.get("query", data.get("context", "")),
                        metadata=data.get("metadata")
                    )
                    requests.append(request)
                    
                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse line: {e}")
        
        return requests
    
    def _infer_endpoint_from_filename(self, filename: str) -> str:
        """Infer the API endpoint from the dataset filename."""
        name_lower = filename.lower()
        
        if "design" in name_lower:
            return "/generateDesign"
        elif "asr" in name_lower or "assessment" in name_lower:
            return "/generateAssessmentReport"
        elif "architecture" in name_lower:
            return "/analyzeArchitecture"
        elif "code" in name_lower:
            return "/analyzeCode"
        elif "kubernetes" in name_lower or "k8s" in name_lower:
            return "/kubernetesDiscovery"
        else:
            return "/runAnalysis"
    
    def save_responses(
        self,
        responses: List[AgentResponse],
        output_path: Path
    ):
        """Save responses to a JSONL file."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, "w") as f:
            for response in responses:
                f.write(json.dumps(asdict(response)) + "\n")
        
        logger.info(f"Saved {len(responses)} responses to {output_path}")


# =============================================================================
# Batch Response Collector
# =============================================================================

class BatchResponseCollector:
    """
    Collects responses from multiple endpoints for evaluation datasets.
    
    This class orchestrates the collection of responses across multiple
    endpoints and datasets, suitable for comprehensive evaluation testing.
    
    Supports both new request-based datasets (*_requests.jsonl) and
    legacy query-based datasets (*_queries.jsonl).
    """
    
    def __init__(
        self,
        api_url: Optional[str] = None,
        output_dir: Optional[Path] = None
    ):
        self.runner = AgentRunner(api_url=api_url)
        self.output_dir = output_dir or Path(__file__).parent / "responses"
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    async def collect_all(
        self,
        datasets_dir: Path,
        limit_per_dataset: Optional[int] = None,
        pattern: str = "*_requests.jsonl"
    ) -> Dict[str, CollectionResult]:
        """
        Collect responses for all datasets in a directory.
        
        Args:
            datasets_dir: Directory containing JSONL dataset files
            limit_per_dataset: Maximum requests per dataset
            pattern: Glob pattern for dataset files (default: *_requests.jsonl)
            
        Returns:
            Dictionary mapping dataset names to collection results
        """
        results = {}
        
        # Find all matching JSONL files
        datasets = list(datasets_dir.glob(pattern))
        
        # Also include legacy query datasets if no request datasets found
        if not datasets:
            datasets = list(datasets_dir.glob("*.jsonl"))
        
        logger.info(f"Found {len(datasets)} datasets in {datasets_dir}")
        
        for dataset_path in datasets:
            dataset_name = dataset_path.stem
            
            # Infer endpoint from dataset name
            endpoint = self._infer_endpoint_name(dataset_name)
            
            logger.info(f"\n{'='*50}")
            logger.info(f"Collecting responses for: {dataset_name}")
            logger.info(f"Target endpoint: {endpoint}")
            logger.info(f"{'='*50}")
            
            result = await self.runner.collect_responses(
                dataset_path=dataset_path,
                agent_name=endpoint,  # Used for legacy datasets
                limit=limit_per_dataset
            )
            
            # Save responses
            output_path = self.output_dir / f"{dataset_name}_responses.jsonl"
            self.runner.save_responses(result.responses, output_path)
            
            results[dataset_name] = result
            
            logger.info(f"Completed {dataset_name}:")
            logger.info(f"  Total: {result.total_requests}")
            logger.info(f"  Successful: {result.successful}")
            logger.info(f"  Failed: {result.failed}")
            logger.info(f"  Duration: {result.duration_seconds:.1f}s")
        
        return results
    
    def _infer_endpoint_name(self, dataset_name: str) -> str:
        """Infer endpoint name from dataset filename."""
        name_lower = dataset_name.lower()
        
        if "design" in name_lower:
            return "design"
        elif "asr" in name_lower or "assessment" in name_lower:
            return "asr"
        elif "architecture" in name_lower:
            return "architecture"
        elif "code" in name_lower:
            return "code"
        elif "kubernetes" in name_lower or "k8s" in name_lower:
            return "kubernetes"
        else:
            return "orchestrator"
    
    def generate_summary_report(
        self,
        results: Dict[str, CollectionResult]
    ) -> str:
        """Generate a summary report of the collection run."""
        lines = [
            "# Response Collection Summary",
            f"\nTimestamp: {datetime.now().isoformat()}",
            f"Output Directory: {self.output_dir}",
            "\n## Results by Dataset\n",
        ]
        
        total_requests = 0
        total_successful = 0
        total_failed = 0
        total_duration = 0
        
        for dataset_name, result in results.items():
            lines.append(f"### {dataset_name}")
            lines.append(f"- Total Requests: {result.total_requests}")
            lines.append(f"- Successful: {result.successful}")
            lines.append(f"- Failed: {result.failed}")
            lines.append(f"- Duration: {result.duration_seconds:.1f}s")
            lines.append("")
            
            total_requests += result.total_requests
            total_successful += result.successful
            total_failed += result.failed
            total_duration += result.duration_seconds
        
        lines.extend([
            "## Overall Summary",
            f"- Total Requests: {total_requests}",
            f"- Successful: {total_successful}",
            f"- Failed: {total_failed}",
            f"- Success Rate: {(total_successful/total_requests*100) if total_requests > 0 else 0:.1f}%",
            f"- Total Duration: {total_duration:.1f}s",
        ])
        
        return "\n".join(lines)


# =============================================================================
# Command Line Interface
# =============================================================================

async def main():
    """Command line interface for the agent runner."""
    parser = argparse.ArgumentParser(
        description="Collect agent responses for evaluation testing"
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        help="Path to JSONL dataset file"
    )
    parser.add_argument(
        "--datasets-dir",
        type=Path,
        default=Path(__file__).parent / "datasets",
        help="Directory containing JSONL datasets"
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output file for responses"
    )
    parser.add_argument(
        "--endpoint",
        choices=["design", "asr", "orchestrator", "architecture", "code", "kubernetes"],
        default="orchestrator",
        help="Endpoint to use (for legacy datasets without endpoint in filename)"
    )
    parser.add_argument(
        "--agent",
        choices=["design", "asr", "orchestrator"],
        help="[DEPRECATED] Use --endpoint instead"
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum number of requests to process"
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Process all datasets in the datasets directory"
    )
    parser.add_argument(
        "--pattern",
        default="*_requests.jsonl",
        help="Glob pattern for batch mode (default: *_requests.jsonl)"
    )
    parser.add_argument(
        "--api-url",
        help="Insights API URL (or set API_BASE_URL env var)"
    )
    
    args = parser.parse_args()
    
    # Handle deprecated --agent flag
    endpoint = args.endpoint
    if args.agent:
        logger.warning("--agent is deprecated. Use --endpoint instead.")
        endpoint = args.agent
    
    try:
        if args.batch:
            # Batch mode: process all datasets
            collector = BatchResponseCollector(
                api_url=args.api_url,
                output_dir=args.output.parent if args.output else None
            )
            
            results = await collector.collect_all(
                datasets_dir=args.datasets_dir,
                limit_per_dataset=args.limit,
                pattern=args.pattern
            )
            
            # Generate and print summary
            summary = collector.generate_summary_report(results)
            print(summary)
            
            # Save summary to file
            summary_path = collector.output_dir / "collection_summary.md"
            with open(summary_path, "w") as f:
                f.write(summary)
            logger.info(f"Summary saved to: {summary_path}")
            
        elif args.dataset:
            # Single dataset mode
            runner = AgentRunner(api_url=args.api_url)
            
            result = await runner.collect_responses(
                dataset_path=args.dataset,
                agent_name=endpoint,
                limit=args.limit
            )
            
            # Save responses
            output_path = args.output or Path(
                f"responses_{args.dataset.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
            )
            runner.save_responses(result.responses, output_path)
            
            print(f"\nCollection Complete:")
            print(f"  Total: {result.total_requests}")
            print(f"  Successful: {result.successful}")
            print(f"  Failed: {result.failed}")
            print(f"  Duration: {result.duration_seconds:.1f}s")
            print(f"  Output: {output_path}")
            
        else:
            parser.print_help()
            print("\nError: Either --dataset or --batch must be specified")
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
