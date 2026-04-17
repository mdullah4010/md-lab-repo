# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

"""
Integration test fixtures for agent testing.

This module provides fixtures specific to integration testing of
individual agents with real Azure services.

All tests use real Azure connections - no mocking is used.
"""

import os
import sys
import logging
import pytest
import pytest_asyncio
import asyncio
import httpx
from pathlib import Path
from typing import Dict, Any, Optional

# Add project root for imports
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "agents"))

logger = logging.getLogger(__name__)


# =============================================================================
# Integration Test Configuration
# =============================================================================

@pytest.fixture(scope="module")
def integration_config(test_environment) -> Dict[str, Any]:
    """
    Provide integration test configuration.
    
    Returns:
        Dictionary containing integration test configuration.
    """
    config = {
        "app_id": test_environment["test_app_id"],
        "storage_account_name": test_environment["storage_account_name"],
        "storage_account_url": test_environment["storage_account_url"],
        "project_connection_string": test_environment.get("ai_project_connection_string"),
        "search_endpoint": test_environment.get("search_endpoint"),
        "openai_endpoint": test_environment.get("openai_endpoint"),
        "openai_deployment": test_environment.get("openai_deployment"),
        # API Configuration
        "api_base_url": test_environment.get("api_base_url", "http://localhost:8000"),
        # Optional RBAC fields
        "user_object_id": test_environment.get("test_user_object_id"),
        "group_object_id": test_environment.get("test_group_object_id"),
        "resource_group_name": test_environment.get("resource_group_name"),
        "azure_region": test_environment.get("azure_region", "eastus"),
    }
    
    logger.info(f"Integration test configuration loaded for app_id: {config['app_id']}")
    logger.info(f"API Base URL: {config['api_base_url']}")
    return config


# =============================================================================
# HTTP Client Fixtures
# =============================================================================

@pytest_asyncio.fixture(scope="function")
async def http_client(integration_config) -> httpx.AsyncClient:
    """
    Create an async HTTP client for API endpoint testing.
    
    The client is configured with:
    - Extended timeout for long-running agent operations
    - JSON content type headers
    - Base URL from configuration
    
    Yields:
        AsyncClient instance for making HTTP requests.
    """
    timeout = httpx.Timeout(
        connect=30.0,
        read=1800.0,  # 30 minutes for long-running agent operations
        write=60.0,
        pool=30.0
    )
    
    async with httpx.AsyncClient(
        base_url=integration_config["api_base_url"],
        timeout=timeout,
        headers={"Content-Type": "application/json"}
    ) as client:
        logger.info(f"HTTP client created for {integration_config['api_base_url']}")
        yield client
        logger.info("HTTP client closed")


@pytest.fixture
def api_request_payload(integration_config) -> Dict[str, Any]:
    """
    Create a standard ApplicationOperationRequest payload.
    
    This matches the API's ApplicationOperationRequest model:
    - app_id (required)
    - storage_account_name (required)
    - azure_region (required)
    - user_object_id (required - at least one of user_object_id or group_object_id)
    - group_object_id (optional)
    - resource_group_name (optional)
    
    Returns:
        Dictionary containing the API request payload.
    """
    payload = {
        "app_id": integration_config["app_id"],
        "storage_account_name": integration_config["storage_account_name"],
        "azure_region": integration_config["azure_region"],
    }
    
    # Add required identity field (at least one must be present)
    if integration_config.get("user_object_id"):
        payload["user_object_id"] = integration_config["user_object_id"]
    if integration_config.get("group_object_id"):
        payload["group_object_id"] = integration_config["group_object_id"]
    if integration_config.get("resource_group_name"):
        payload["resource_group_name"] = integration_config["resource_group_name"]
    
    logger.debug(f"API request payload: {payload}")
    return payload


# =============================================================================
# Azure AI Project Client Fixtures
# =============================================================================

@pytest_asyncio.fixture(scope="module")
async def ai_project_client(integration_config):
    """
    Create Azure AI Project Client for agent operations.
    
    Returns:
        AIProjectClient instance connected to Azure AI Foundry.
    """
    connection_string = integration_config.get("project_connection_string")
    
    if not connection_string:
        pytest.skip("AZURE_EXISTING_AIPROJECT_ENDPOINT not configured")
    
    try:
        from azure.ai.projects.aio import AIProjectClient
        from azure.identity.aio import DefaultAzureCredential
        
        credential = DefaultAzureCredential(exclude_shared_token_cache_credential=True)
        
        # Parse connection string to get endpoint
        # Format: <endpoint>;subscription_id=<sub>;resource_group=<rg>;project_name=<project>
        parts = dict(p.split("=", 1) for p in connection_string.split(";") if "=" in p)
        endpoint = connection_string.split(";")[0] if ";" in connection_string else connection_string
        
        logger.info(f"Creating AI Project Client for: {endpoint}")
        
        client = AIProjectClient(
            endpoint=endpoint,
            credential=credential
        )
        
        yield client
        
        # Cleanup
        await credential.close()
        
    except ImportError:
        pytest.skip("azure-ai-projects package not installed")
    except Exception as e:
        logger.error(f"Failed to create AI Project Client: {e}")
        pytest.skip(f"Failed to create AI Project Client: {e}")


# =============================================================================
# Azure AI Search Fixtures
# =============================================================================

@pytest.fixture(scope="module")
def search_client(integration_config, azure_credential):
    """
    Create Azure AI Search client for document search operations.
    
    Returns:
        SearchClient instance for the test application index.
    """
    search_endpoint = integration_config.get("search_endpoint")
    app_id = integration_config["app_id"]
    
    if not search_endpoint:
        pytest.skip("AZURE_SEARCH_ENDPOINT not configured")
    
    try:
        from azure.search.documents import SearchClient
        
        index_name = f"{app_id}"
        
        # Use managed identity for authentication
        credential = azure_credential
        
        logger.info(f"Creating Search Client for index: {index_name}")
        
        return SearchClient(
            endpoint=search_endpoint,
            index_name=index_name,
            credential=credential
        )
        
    except ImportError:
        pytest.skip("azure-search-documents package not installed")
    except Exception as e:
        logger.error(f"Failed to create Search Client: {e}")
        pytest.skip(f"Failed to create Search Client: {e}")


@pytest.fixture(scope="module")
def search_index_client(integration_config, azure_credential):
    """
    Create Azure AI Search index client for index management.
    
    Returns:
        SearchIndexClient instance for index operations.
    """
    search_endpoint = integration_config.get("search_endpoint")
    
    if not search_endpoint:
        pytest.skip("AZURE_SEARCH_ENDPOINT not configured")
    
    try:
        from azure.search.documents.indexes import SearchIndexClient
        
        # Use managed identity for authentication
        credential = azure_credential
        
        logger.info(f"Creating Search Index Client for: {search_endpoint}")
        
        return SearchIndexClient(
            endpoint=search_endpoint,
            credential=credential
        )
        
    except ImportError:
        pytest.skip("azure-search-documents package not installed")


# =============================================================================
# Agent Import Fixtures
# =============================================================================

@pytest.fixture(scope="module")
def design_agent_module():
    """
    Import and return the design_agent module.
    
    Returns:
        The design_agent module for testing.
    """
    try:
        from agents import design_agent
        return design_agent
    except ImportError as e:
        logger.error(f"Failed to import design_agent: {e}")
        pytest.skip(f"Failed to import design_agent: {e}")


@pytest.fixture(scope="module")
def orchestrator_agent_module():
    """
    Import and return the orchestrator_agent module.
    
    Returns:
        The orchestrator_agent module for testing.
    """
    try:
        from agents import orchestrator_agent
        return orchestrator_agent
    except ImportError as e:
        logger.error(f"Failed to import orchestrator_agent: {e}")
        pytest.skip(f"Failed to import orchestrator_agent: {e}")


@pytest.fixture(scope="module")
def asr_agent_module():
    """
    Import and return the asr_agent module.
    
    Returns:
        The asr_agent module for testing.
    """
    try:
        from agents import asr_agent
        return asr_agent
    except ImportError as e:
        logger.error(f"Failed to import asr_agent: {e}")
        pytest.skip(f"Failed to import asr_agent: {e}")


@pytest.fixture(scope="module")
def kubernetes_discovery_agent_module():
    """
    Import and return the kubernetes_discovery_agent module.
    
    Returns:
        The kubernetes_discovery_agent module for testing.
    """
    try:
        from agents import kubernetes_discovery_agent
        return kubernetes_discovery_agent
    except ImportError as e:
        logger.error(f"Failed to import kubernetes_discovery_agent: {e}")
        pytest.skip(f"Failed to import kubernetes_discovery_agent: {e}")


# =============================================================================
# Blob Container Fixtures
# =============================================================================

@pytest.fixture(scope="module")
def container_client(blob_service_client, integration_config):
    """
    Get container client for the test application.
    
    Returns:
        ContainerClient for the test application container.
    """
    app_id = integration_config["app_id"]
    
    try:
        container = blob_service_client.get_container_client(app_id)
        
        if not container.exists():
            logger.warning(f"Container '{app_id}' does not exist, creating...")
            container.create_container()
        
        logger.info(f"Container client ready for: {app_id}")
        return container
        
    except Exception as e:
        logger.error(f"Failed to get container client: {e}")
        pytest.skip(f"Failed to get container client: {e}")


# =============================================================================
# Test Data Fixtures for Integration Tests
# =============================================================================

@pytest.fixture
def sample_design_query() -> str:
    """
    Sample query for design agent testing.
    
    Returns:
        A typical design agent query.
    """
    return "Generate Azure migration design for the application"


@pytest.fixture
def sample_kubernetes_query() -> str:
    """
    Sample query for Kubernetes discovery testing.
    
    Returns:
        A typical K8s discovery query.
    """
    return "Analyze the Kubernetes deployment configuration"


@pytest.fixture
def sample_orchestrator_queries() -> list:
    """
    Sample queries for orchestrator agent testing.
    
    Returns:
        List of test queries for the orchestrator.
    """
    return [
        "Generate a migration design document",
        "Analyze the Kubernetes deployment",
        "Create a migration assessment report",
        "What is the current infrastructure status?",
    ]


# =============================================================================
# Performance Tracking Fixtures
# =============================================================================

class PerformanceTracker:
    """
    Tracks performance metrics for integration tests.
    """
    
    def __init__(self):
        self.metrics = {}
    
    def record(self, test_name: str, duration: float, success: bool):
        """Record a test execution metric."""
        self.metrics[test_name] = {
            "duration_seconds": duration,
            "success": success
        }
        logger.info(f"Performance: {test_name} - {duration:.2f}s - {'✅' if success else '❌'}")
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all recorded metrics."""
        total = len(self.metrics)
        successful = sum(1 for m in self.metrics.values() if m["success"])
        total_duration = sum(m["duration_seconds"] for m in self.metrics.values())
        
        return {
            "total_tests": total,
            "successful": successful,
            "failed": total - successful,
            "total_duration_seconds": total_duration,
            "average_duration_seconds": total_duration / total if total > 0 else 0,
            "metrics": self.metrics
        }


@pytest.fixture(scope="module")
def performance_tracker() -> PerformanceTracker:
    """
    Provide a performance tracker for the test module.
    
    Returns:
        PerformanceTracker instance.
    """
    tracker = PerformanceTracker()
    yield tracker
    
    # Log summary at end of module
    summary = tracker.get_summary()
    logger.info("=" * 50)
    logger.info("INTEGRATION TEST PERFORMANCE SUMMARY")
    logger.info(f"Total Tests: {summary['total_tests']}")
    logger.info(f"Successful: {summary['successful']}")
    logger.info(f"Failed: {summary['failed']}")
    logger.info(f"Total Duration: {summary['total_duration_seconds']:.2f}s")
    logger.info(f"Average Duration: {summary['average_duration_seconds']:.2f}s")
    logger.info("=" * 50)


# =============================================================================
# Async Test Helpers
# =============================================================================

@pytest.fixture
def async_timeout():
    """
    Provide default timeout for async operations.
    
    Returns:
        Timeout in seconds.
    """
    return int(os.environ.get("TEST_TIMEOUT_SECONDS", "120"))
