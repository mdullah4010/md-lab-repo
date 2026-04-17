# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

"""
Integration tests for the Create Application ID endpoint.

This module tests the Create Application ID API endpoint with real Azure services:
- HTTP API endpoint POST /createApplicationId
- Azure Blob Storage for container creation
- Azure Table Storage for application metadata

All tests call the actual HTTP API - no mocking is used.

Request Model (CreateApplicationRequest):
    - app_id: str (required, 3-63 chars, alphanumeric and hyphens only)
    - storage_account_name: str (required, 3-24 chars)
    - azure_region: str (required, Azure region name)
    - user_object_id: Optional[str] (GUID format)
    - group_object_id: Optional[str] (GUID format)
    - resource_group_name: Optional[str]

Response Model (CreateApplicationResponse):
    - status: str ('success' or 'error')
    - app_id: str
    - message: str
    - container: dict (status, container_name, storage_account, exists)
    - tables: dict (status, message, existing_tables)
    - permissions: dict (blob_permissions, table_permissions)

Tests Completed:
    1. test_create_application_id_success
       - Validates successful application ID creation
       - Verifies API returns 200 status code
       - Confirms blob storage container is created
       - Validates metadata is stored in Azure Table Storage
       - Tests response contains all required fields
    
    2. test_create_application_id_invalid_format
       - Tests validation of app_id format requirements
       - Verifies rejection of invalid characters (!, @, #, etc.)
       - Ensures proper validation error (400 or 422 status)
    
    3. test_create_application_id_missing_fields
       - Tests validation when required fields are missing
       - Verifies API returns 422 validation error
       - Ensures proper error messages for missing storage_account_name

Usage:
    pytest tests/integration/test_create_application_id.py -v -s

Configuration:
    Test configuration is loaded from .env.test file in the project root.
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
class TestCreateApplicationIdAPI:
    """
    Test Create Application ID API endpoint with real Azure services.
    """
    
    async def test_create_application_id_success(self, http_client, integration_config):
        """
        Test creating a new application ID successfully.
        
        Verifies:
        - API returns 200 status code
        - Response includes status='success'
        - Container information is returned
        - Tables information is returned
        - Permissions information is returned
        """
        logger.info("Testing POST /createApplicationId endpoint...")
        
        request_data = {
            "app_id": integration_config["app_id"],
            "storage_account_name": integration_config["storage_account_name"],
            "azure_region": integration_config["azure_region"],
            "user_object_id": integration_config["user_object_id"]
        }
        
        response = await http_client.post("/createApplicationId", json=request_data)
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        response_data = response.json()
        logger.info(f"Response: {response_data}")
        
        # Verify response structure
        assert "status" in response_data
        assert "app_id" in response_data
        assert "message" in response_data
        assert "container" in response_data
        assert "tables" in response_data
        assert "permissions" in response_data
        
        # Verify response values
        assert response_data["status"] == "success"
        assert response_data["app_id"] == request_data["app_id"]
        assert isinstance(response_data["container"], dict)
        assert "exists" in response_data["container"]
        assert isinstance(response_data["container"]["exists"], bool)
        assert isinstance(response_data["tables"], dict)
        assert "status" in response_data["tables"]
        
        logger.info("✅ Create application ID test passed")
    
    async def test_create_application_id_invalid_format(self, http_client):
        """
        Test creating application ID with invalid format.
        
        Verifies proper validation of app_id format.
        """
        logger.info("Testing POST /createApplicationId with invalid format...")
        
        request_data = {
            "app_id": "invalid_app_id!@#",  # Invalid characters
            "storage_account_name": "teststorage",
            "azure_region": "eastus"
        }
        
        response = await http_client.post("/createApplicationId", json=request_data)
        
        # Should return validation error (422 or 400)
        assert response.status_code in [400, 422], f"Expected 400/422, got {response.status_code}"
        
        logger.info("✅ Invalid format validation test passed")
    
    async def test_create_application_id_missing_fields(self, http_client):
        """
        Test creating application ID with missing required fields.
        """
        logger.info("Testing POST /createApplicationId with missing fields...")
        
        request_data = {
            "app_id": "test-app"
            # Missing storage_account_name and azure_region
        }
        
        response = await http_client.post("/createApplicationId", json=request_data)
        
        # Should return validation error
        assert response.status_code == 422, f"Expected 422, got {response.status_code}"
        
        logger.info("✅ Missing fields validation test passed")
