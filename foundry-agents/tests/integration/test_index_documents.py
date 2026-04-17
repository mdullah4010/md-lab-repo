# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

"""
Integration tests for the Index Documents endpoint.

This module tests the Index Documents API endpoint with real Azure services:
- HTTP API endpoint POST /indexDocuments
- Azure Blob Storage for document retrieval
- Azure AI Search for indexing
- Azure Functions for document processing

All tests call the actual HTTP API - no mocking is used.

Request Model (ApplicationOperationRequest):
    - app_id: str (required, 3-63 chars)
    - storage_account_name: str (required, 3-24 chars)
    - user_object_id: Optional[str] (GUID format) - required if group_object_id not provided
    - group_object_id: Optional[str] (GUID format) - required if user_object_id not provided
    - resource_group_name: Optional[str] - auto-discovered if not provided
    - folder_prefix: Optional[str] - limits indexing to a specific folder path (e.g., 'responder/input/')
                                     If not provided, indexes entire container.

Response Model (IndexDocumentsResponse):
    - status: str ('success' or 'error')
    - app_id: str
    - message: str
    - indexing_result: dict (result, data with nested status, mode, result)

Tests Completed:
    1. test_index_documents_success
       - Validates successful document indexing with specific folder prefixes
       - Verifies API returns 200 status code
       - Confirms indexing status is returned
       - Tests document processing and Azure AI Search integration
       - Parametrized with folder_prefix values: responder/input/, kubernetes-discovery/input/, code-analyzer/input/
    
    2. test_index_documents_empty_container
       - Tests graceful handling when blob container has no documents
       - Validates error handling for empty storage containers
       - Does not provide folder_prefix (tests default full-container behavior)
       - Ensures appropriate status codes (200 or 404)
    
    3. test_index_documents_invalid_app_id
       - Tests handling of non-existent application IDs
       - Validates error response for missing applications
       - Does not provide folder_prefix (tests default full-container behavior)
       - Ensures API returns 404 for invalid app references

Usage:
    pytest tests/integration/test_index_documents.py -v -s
"""

import os
import sys
import logging
import pytest
from pathlib import Path
from typing import Dict, Any

# Add project root for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

logger = logging.getLogger(__name__)


@pytest.mark.integration
@pytest.mark.asyncio
class TestIndexDocumentsAPI:
    """
    Test Index Documents API endpoint with real Azure services.
    """
    
    @pytest.mark.parametrize("folder_prefix", [
        "responder/input/",
        "kubernetes-discovery/input/",
        "code-analyzer/input/"
    ])
    async def test_index_documents_success(self, http_client, integration_config, folder_prefix):
        """
        Test indexing documents successfully with specific folder prefixes.
        
        Verifies:
        - API returns 200 status code
        - Documents are indexed in Azure AI Search for specific folder paths
        - Indexing status is returned
        
        Tests folder prefixes:
        - responder/input/
        - kubernetes-discovery/input/
        - code-analyzer/input/
        """
        logger.info(f"Testing POST /indexDocuments endpoint with folder_prefix='{folder_prefix}'...")
        
        request_data = {
            "app_id": integration_config["app_id"],
            "storage_account_name": integration_config["storage_account_name"],
            "azure_region": integration_config["azure_region"],
            "user_object_id": integration_config["user_object_id"],
            "folder_prefix": folder_prefix
        }
        
        response = await http_client.post("/indexDocuments", json=request_data)
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        response_data = response.json()
        logger.info(f"Response for folder_prefix='{folder_prefix}': {response_data}")
        
        # Verify response structure
        assert "status" in response_data
        assert "app_id" in response_data
        assert "message" in response_data
        assert "indexing_result" in response_data
        
        # Verify response values
        assert response_data["status"] == "success"
        assert response_data["app_id"] == request_data["app_id"]
        assert isinstance(response_data["indexing_result"], dict)
        assert "result" in response_data["indexing_result"]
        
        logger.info(f"✅ Index documents test passed for folder_prefix='{folder_prefix}'")
    
    async def test_index_documents_empty_container(self, http_client, integration_config):
        """
        Test indexing with no documents in container.
        
        Verifies proper handling when container has no documents.
        """
        logger.info("Testing POST /indexDocuments with empty container...")
        
        request_data = {
            "app_id": "empty-test-app",
            "storage_account_name": integration_config["storage_account_name"],
            "azure_region": integration_config["azure_region"],
            "user_object_id": integration_config["user_object_id"]
        }
        response = await http_client.post("/indexDocuments", json=request_data)
        
        # Should handle gracefully (200 with message or 404)
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        
        logger.info("✅ Empty container test passed")
    
    async def test_index_documents_invalid_app_id(self, http_client, integration_config):
        """
        Test indexing with non-existent application ID.
        """
        logger.info("Testing POST /indexDocuments with invalid app_id...")
        
        request_data = {
            "app_id": "nonexistent-app-12345",
            "storage_account_name": integration_config["storage_account_name"],
            "azure_region": integration_config["azure_region"],
            "user_object_id": integration_config["user_object_id"]
        }
        
        response = await http_client.post("/indexDocuments", json=request_data)
        
        # Should return 404 for non-existent app
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        
        logger.info("✅ Invalid app_id test passed")
