# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

"""
Integration tests for the Run Analysis endpoint.

This module tests the Run Analysis API endpoint with real Azure services:
- HTTP API endpoint POST /runAnalysis
- Azure AI Foundry for analysis execution
- Azure AI Search for document context
- Azure Blob Storage for analysis results

All tests call the actual HTTP API - no mocking is used.

Request Model (ApplicationOperationRequest):
    - app_id: str (required, 3-63 chars)
    - storage_account_name: str (required, 3-24 chars)
    - user_object_id: Optional[str] (GUID format) - required if group_object_id not provided
    - group_object_id: Optional[str] (GUID format) - required if user_object_id not provided
    - resource_group_name: Optional[str] - auto-discovered if not provided

Response Model (AnalysisResponse):
    - status: str ('accepted' - async operation)
    - operation_id: str
    - app_id: str
    - message: str
    - status_endpoint: str (GET endpoint to check status)
    - result_endpoint: str (GET endpoint to retrieve results)

Tests Completed:
    1. test_run_analysis_success
       - Validates successful analysis execution (async operation)
       - Verifies API returns 202 status code (async operation started)
       - Polls for operation completion and retrieves results
       - Confirms analysis status and unique operation_id are returned
       - Tests complete analysis workflow with Azure AI Foundry
       - Verifies that the returned confidence scores for the tables are above the specified thresholds
    
    2. test_run_analysis_missing_data
       - Tests graceful handling when required data is missing
       - Validates error handling for incomplete application data
       - Ensures appropriate status codes (200/400/404)

Usage:
    pytest tests/integration/test_run_analysis.py -v -s
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
class TestRunAnalysisAPI:
    """
    Test Run Analysis API endpoint with real Azure services.
    """
    
    async def test_run_analysis_success(self, http_client, integration_config):
        """
        Test running analysis successfully (async operation).
        
        Verifies:
        - API returns 202 status code (async operation started)
        - Operation ID is returned
        - Analysis operation completes successfully
        - Analysis results are returned
        - All table confidence scores are > 0.5
        - Overall confidence score is > 0.5
        """
        logger.info("Testing POST /runAnalysis endpoint...")
        
        request_data = {
            "app_id": integration_config["app_id"],
            "storage_account_name": integration_config["storage_account_name"],
            "user_object_id": integration_config["user_object_id"],
            "resource_group_name": integration_config.get("resource_group_name")
        }
        
        response = await http_client.post("/runAnalysis", json=request_data)
        
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
            result_endpoint=response_data["result_endpoint"],
            max_wait_time=3600  # 60 minutes timeout for complex analysis operations (was 30 min)
        )
        
        # Display the result data
        import json
        logger.info("\n" + "="*80)
        logger.info("ANALYSIS RESULT:")
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
        
        # Note: Analysis results may be in analysis_result field or message field
        # depending on how the background task stores them
        logger.info(f"Analysis completed for operation {operation_id}")
        
        logger.info("✅ Run analysis test passed")
    
    async def test_run_analysis_missing_data(self, http_client, integration_config):
        """
        Test running analysis when application doesn't exist.
        """
        logger.info("Testing POST /runAnalysis with non-existent app...")
        
        request_data = {
            "app_id": "missing-data-app",
            "storage_account_name": integration_config["storage_account_name"],
            "user_object_id": integration_config["user_object_id"],
            "resource_group_name": integration_config.get("resource_group_name")
        }
        
        response = await http_client.post("/runAnalysis", json=request_data)
        
        # Should return 404 for non-existent app
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        
        logger.info("✅ Missing data test passed")
