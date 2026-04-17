# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

"""
Integration tests for the Generate Design endpoint.

This module tests the Design Generation API endpoint with real Azure services:
- HTTP API endpoint POST /generateDesign
- Azure AI Foundry for design agent execution
- Azure AI Search for document retrieval
- Azure Blob Storage for design report storage

All tests call the actual HTTP API - no mocking is used.

Request Model (ApplicationOperationRequest):
    - app_id: str (required, 3-63 chars)
    - storage_account_name: str (required, 3-24 chars)
    - user_object_id: Optional[str] (GUID format) - required if group_object_id not provided
    - group_object_id: Optional[str] (GUID format) - required if user_object_id not provided
    - resource_group_name: Optional[str] - auto-discovered if not provided

Response Model (DesignResponse):
    - status: str ('accepted' - async operation)
    - operation_id: str
    - app_id: str
    - message: str
    - status_endpoint: str (GET endpoint to check status)
    - result_endpoint: str (GET endpoint to retrieve results)

Tests Completed:
    1. test_generate_design_success
       - Validates successful design generation (async operation)
       - Verifies API returns 202 status code (async operation started)
       - Polls for operation completion and retrieves results
       - Confirms design generation status is returned
       - Tests complete design generation workflow with Azure AI Foundry
    
    2. test_generate_design_with_incorrect_user_context
       - Tests design generation with incorrect user (or group) object IDs
       - Verifies API validates the user identity in Entra ID
       - Ensures API returns proper error message
    
    3. test_generate_design_no_assessment
       - Tests graceful handling when prerequisite assessment is missing
       - Validates error handling for incomplete workflow
       - Ensures appropriate status codes (200/400/404)

Usage:
    pytest tests/integration/test_generate_design.py -v -s
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
class TestGenerateDesignAPI:
    """
    Test Generate Design API endpoint with real Azure services.
    """
    
    async def test_generate_design_success(self, http_client, integration_config):
        """
        Test generating design successfully (async operation).
        
        Verifies:
        - API returns 202 status code (async operation started)
        - Operation ID is returned
        - Design generation operation completes successfully
        - Design results are returned
        """
        logger.info("Testing POST /generateDesign endpoint...")
        
        request_data = {
            "app_id": integration_config["app_id"],
            "storage_account_name": integration_config["storage_account_name"],
            "user_object_id": integration_config["user_object_id"]
        }
        
        response = await http_client.post("/generateDesign", json=request_data)
        
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
        logger.info("DESIGN GENERATION RESULT:")
        logger.info("="*80)
        logger.info(json.dumps(result_data, indent=2))
        logger.info("="*80 + "\n")
        
        # Verify result structure
        assert "status" in result_data
        assert "operation_id" in result_data
        assert "app_id" in result_data
        assert result_data["status"] == "success"
        assert result_data["operation_id"] == operation_id
        assert result_data["app_id"] == request_data["app_id"]
        
        logger.info(f"Design generation completed for operation {operation_id}")
        logger.info("✅ Generate design test passed")
    
    async def test_generate_design_with_incorrect_user_context(self, http_client, integration_config):
        """
        Test generating design with incorrect user (or group) context.
        
        Verifies that the proper error is returned.
        """
        logger.info("Testing POST /generateDesign with incorrect user context...")
        
        request_data = {
            "app_id": integration_config["app_id"],
            "storage_account_name": integration_config["storage_account_name"],
            "user_object_id": "12345678-1234-1234-1234-123456789012"
        }
        
        response = await http_client.post("/generateDesign", json=request_data)
        
        # Should return validation error
        assert response.status_code == 403 , f"Expected 403 , got {response.status_code}"
        
        logger.info("✅ Test with incorrect User context design test passed")
    
    async def test_generate_design_no_assessment(self, http_client, integration_config):
        """
        Test generating design when application doesn't exist.
        """
        logger.info("Testing POST /generateDesign with non-existent app...")
        
        request_data = {
            "app_id": "no-assessment-app",
            "storage_account_name": integration_config["storage_account_name"],
            "user_object_id": integration_config["user_object_id"]
        }
        
        response = await http_client.post("/generateDesign", json=request_data)
        
        # Should return 404 for non-existent app
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        logger.info("✅ No assessment test passed")
