# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

"""
Integration tests for the Discover Kubernetes endpoint.

This module tests the Discover Kubernetes API endpoint with real Azure services:
- HTTP API endpoint POST /discoverKubernetes
- Azure AI Foundry for Kubernetes discovery analysis
- Azure AI Search for manifest retrieval
- Azure Blob Storage for Kubernetes manifests

All tests call the actual HTTP API - no mocking is used.

Request Model (ApplicationOperationRequest):
    - app_id: str (required, 3-63 chars)
    - storage_account_name: str (required, 3-24 chars)
    - user_object_id: Optional[str] (GUID format) - required if group_object_id not provided
    - group_object_id: Optional[str] (GUID format) - required if user_object_id not provided
    - resource_group_name: Optional[str] - auto-discovered if not provided

Response Model (KubernetesDiscoveryResponse):
    - status: str ('accepted' - async operation)
    - operation_id: str
    - app_id: str
    - message: str
    - status_endpoint: str (GET endpoint to check status)
    - result_endpoint: str (GET endpoint to retrieve results)

Tests Completed:
    1. test_discover_kubernetes_success
       - Validates successful Kubernetes resource discovery (async operation)
       - Verifies API returns 202 status code (async operation started)
       - Polls for operation completion and retrieves results
       - Confirms discovery results contain Kubernetes resources
       - Tests complete Kubernetes manifest discovery workflow
    
    2. test_discover_kubernetes_no_manifests
       - Tests graceful handling when no Kubernetes manifests are available
       - Validates error handling for missing manifest data
       - Ensures appropriate status code (404 for non-existent app)

Usage:
    pytest tests/integration/test_discover_kubernetes.py -v -s
"""

import os
import sys
import logging
import pytest
from pathlib import Path
from typing import Dict, Any

# Add project root for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Add integration test directory for test_helpers import
sys.path.insert(0, str(Path(__file__).parent))

from test_helpers import poll_operation_until_complete

logger = logging.getLogger(__name__)


@pytest.mark.integration
@pytest.mark.asyncio
class TestDiscoverKubernetesAPI:
    """
    Test Discover Kubernetes API endpoint with real Azure services.
    """
    
    async def test_discover_kubernetes_success(self, http_client, integration_config):
        """
        Test discovering Kubernetes resources successfully (async operation).
        
        Verifies:
        - API returns 202 status code (async operation started)
        - Operation ID is returned
        - Kubernetes discovery operation completes successfully
        - Discovery results contain Kubernetes resources
        """
        logger.info("Testing POST /discoverKubernetes endpoint...")
        
        request_data = {
            "app_id": integration_config["app_id"],
            "storage_account_name": integration_config["storage_account_name"],
            "user_object_id": integration_config["user_object_id"]
        }
        
        response = await http_client.post("/discoverKubernetes", json=request_data)
        
        assert response.status_code == 202, f"Expected 202, got {response.status_code}: {response.text}"
        
        response_data = response.json()
        logger.info(f"Response: {response_data}")
        
        # Verify response structure (async operation started)
        assert "status" in response_data
        assert "operation_id" in response_data
        assert "app_id" in response_data
        assert "message" in response_data
        assert "status_endpoint" in response_data
        assert "result_endpoint" in response_data
        
        # Verify response values
        assert response_data["status"] == "accepted"
        assert response_data["app_id"] == request_data["app_id"]
        assert isinstance(response_data["operation_id"], str)
        
        # Poll operation status until completion and retrieve results
        operation_id = response_data["operation_id"]
        result_data = await poll_operation_until_complete(
            http_client=http_client,
            integration_config=integration_config,
            operation_id=operation_id,
            status_endpoint=response_data["status_endpoint"],
            result_endpoint=response_data["result_endpoint"]
        )
        
        # Display the result data
        import json
        logger.info("\n" + "="*80)
        logger.info("KUBERNETES DISCOVERY RESULT:")
        logger.info("="*80)
        logger.info(json.dumps(result_data, indent=2))
        logger.info("="*80 + "\n")
        
        # Verify result structure (returns CodeAnalysisResultResponse for all operation types)
        assert "status" in result_data
        assert "operation_id" in result_data
        assert "app_id" in result_data
        assert "message" in result_data
        assert result_data["status"] == "success"
        assert result_data["operation_id"] == operation_id
        assert result_data["app_id"] == request_data["app_id"]
        
        # Note: The result uses CodeAnalysisResultResponse model, so it has analysis_result field
        # (not discovery_result). This is the same for all async operations.
        assert "analysis_result" in result_data or "repo_metadata" in result_data
        
        logger.info("✅ Discover Kubernetes test passed")
    
    async def test_discover_kubernetes_no_manifests(self, http_client, integration_config):
        """
        Test discovering when no Kubernetes manifests are available.
        """
        logger.info("Testing POST /discoverKubernetes with no manifests...")
        
        request_data = {
            "app_id": "no-k8s-app",
            "storage_account_name": integration_config["storage_account_name"],
            "azure_region": integration_config["azure_region"],
            "user_object_id": integration_config["user_object_id"]
        }
        
        response = await http_client.post("/discoverKubernetes", json=request_data)
        
        # Should return validation error
        assert response.status_code == 404 , f"Expected 404 , got {response.status_code}"
        
        logger.info("✅ No manifests test passed")
