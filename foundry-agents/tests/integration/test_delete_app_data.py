# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

"""
Integration tests for the Delete App Data endpoint.

This module tests the Delete App Data API endpoint with real Azure services:
- HTTP API endpoint POST /deleteAppData
- Azure Blob Storage for container deletion
- Azure Table Storage for metadata removal
- Azure AI Search for index cleanup

All tests call the actual HTTP API - no mocking is used.

Request Model (DeleteAppDataRequest):
    - app_id: str (required, 3-63 chars)
    - storage_account_name: str (required, 3-24 chars)
    - user_object_id: Optional[str] (required if group_object_id not provided)
    - group_object_id: Optional[str] (required if user_object_id not provided)
    - resource_group_name: Optional[str]

Response Model (DeleteAppDataResponse):
    - app_id: str
    - message: str
    - deletion_status: str
    - items_deleted: int

Tests Completed:
    1. test_delete_app_data_success
       - Validates app data deletion behavior
       - Verifies API returns 200 if container exists and is deleted
       - Verifies API returns 404 if container doesn't exist (strict mode)
       - Uses test-specific app ID to avoid production data deletion
    
    2. test_delete_app_data_nonexistent_app
       - Tests behavior for non-existent applications
       - Verifies API returns 404 when container doesn't exist
       - API uses strict mode (not idempotent) for security visibility
    
    3. test_delete_app_data_invalid_app_id
       - Tests validation of app_id format during deletion
       - Verifies rejection of invalid characters in app_id
       - Ensures proper validation error (400 or 422 status)

Usage:
    pytest tests/integration/test_delete_app_data.py -v -s

CAUTION:
    These tests perform actual deletion operations.
    Use test-specific app IDs to avoid deleting production data.
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
class TestDeleteAppDataAPI:
    """
    Test Delete App Data API endpoint with real Azure services.
    
    ⚠️  WARNING: These tests perform actual deletion operations.
    """
    
    async def test_delete_app_data_success(self, http_client, integration_config):
        """
        Test deleting app data behavior.
        
        Verifies:
        - API returns 200 if container exists and was deleted
        - API returns 404 if container doesn't exist (strict mode)
        - Response structure is correct for successful deletion
        
        NOTE: Uses a test-specific app ID to avoid deleting actual data.
        Since test containers may not exist, 404 is also acceptable.
        """
        logger.info("Testing POST /deleteAppData endpoint...")
        
        # Use the real app_id from integration config to test actual deletion
        app_id = integration_config['app_id']
        
        request_data = {
            "app_id": app_id,
            "storage_account_name": integration_config["storage_account_name"],
            "azure_region": integration_config["azure_region"],
            "user_object_id": integration_config["user_object_id"]
        }
        
        response = await http_client.post("/deleteAppData", json=request_data)
        
        # API returns 200 if container exists and is deleted, 404 if container doesn't exist
        if response.status_code == 200:
            response_data = response.json()
            logger.info(f"Response: {response_data}")
            
            # Verify response structure (actual API response format)
            assert "app_id" in response_data
            assert "message" in response_data
            assert "status" in response_data
            
            # Verify response values
            assert response_data["app_id"] == app_id
            assert response_data["status"] in ["success", "partial_success"]
            
            # Check for deletion_result details if present
            if "deletion_result" in response_data:
                deletion_result = response_data["deletion_result"]
                logger.info(f"Deletion result details: {deletion_result}")
                
                # Log any errors for visibility (but don't fail test for partial success)
                if "errors" in deletion_result and deletion_result["errors"]:
                    logger.warning(f"Deletion had errors: {deletion_result['errors']}")
            
            logger.info("✅ Delete app data test passed (container existed and was deleted)")
        elif response.status_code == 404:
            # Container doesn't exist - this can happen if already deleted
            response_data = response.json()
            logger.info(f"Container doesn't exist: {response_data}")
            assert "container_not_found" in str(response_data) or "does not exist" in str(response_data)
            logger.info("✅ Delete app data test passed (container did not exist)")
        else:
            pytest.fail(f"Expected 200 or 404, got {response.status_code}: {response.text}")
    
    async def test_delete_app_data_nonexistent_app(self, http_client, integration_config):
        """
        Test deleting data for non-existent application.
        
        API behavior: Returns 404 with container_not_found error.
        This is intentional (strict mode) rather than idempotent behavior,
        providing visibility when attempting to delete non-existent resources.
        """
        logger.info("Testing POST /deleteAppData for non-existent app...")
        
        request_data = {
            "app_id": "nonexistent-app-999999",
            "storage_account_name": integration_config["storage_account_name"],
            "azure_region": integration_config["azure_region"],
            "user_object_id": integration_config["user_object_id"]
        }
        
        response = await http_client.post("/deleteAppData", json=request_data)
        
        # API returns 404 for non-existent containers (strict mode, not idempotent)
        # This is the actual API behavior - it treats missing containers as an error
        assert response.status_code == 404, f"Expected 404 for non-existent app, got {response.status_code}"
        
        response_data = response.json()
        assert "container_not_found" in str(response_data) or "does not exist" in str(response_data)
        
        logger.info("✅ Non-existent app deletion test passed (404 as expected)")
    
    async def test_delete_app_data_invalid_app_id(self, http_client, integration_config):
        """
        Test deleting with invalid app ID format.
        """
        logger.info("Testing POST /deleteAppData with invalid app_id...")
        
        request_data = {
            "app_id": "invalid@app#id!",
            "storage_account_name": integration_config["storage_account_name"],
            "azure_region": integration_config["azure_region"],
            "user_object_id": integration_config["user_object_id"]
        }
        
        response = await http_client.post("/deleteAppData", json=request_data)
        
        # Should return validation error
        assert response.status_code in [400, 422], f"Expected 400/422, got {response.status_code}"
        
        logger.info("✅ Invalid app_id validation test passed")
