# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

"""
Integration tests for the Generate Assessment Report endpoint.

This module tests the Generate Assessment Report API endpoint with real Azure services:
- HTTP API endpoint POST /generateAssessmentReport
- Azure AI Foundry for report generation
- Azure AI Search for findings retrieval
- Azure Blob Storage for report storage

All tests call the actual HTTP API - no mocking is used.

Request Model (AssessmentReportRequest):
    - app_id: str (required, 3-63 chars)
    - storage_account_name: str (required, 3-24 chars)
    - user_object_id: Optional[str] (GUID format)
    - group_object_id: Optional[str] (GUID format)

Response Model (AssessmentReportResponse):
    - status: str ('success' or 'error')
    - app_id: str
    - operation_id: str
    - report: dict (status, application_id, message, agent_name, agent_id, reused_existing)
    - message: str

Tests Completed:
    1. test_generate_assessment_report_success
       - Validates successful assessment report generation
       - Verifies API returns 200 status code
       - Confirms report status is returned
       - Tests basic report generation workflow
    
    2. test_generate_assessment_report_incorrect_parameters
       - Tests graceful handling when incorrect parameters are specified
       - Validates error handling for missing parameters
       - Ensures appropriate status codes (200/400/404)

Usage:
    pytest tests/integration/test_generate_assessment_report.py -v -s
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
class TestGenerateAssessmentReportAPI:
    """
    Test Generate Assessment Report API endpoint with real Azure services.
    """
    
    async def test_generate_assessment_report_success(self, http_client, integration_config):
        """
        Test generating assessment report successfully.
        
        Verifies:
        - API returns 202 status code (async operation started)
        - Operation ID is returned
        - Report generation operation completes successfully
        - Report URL is returned
        """
        logger.info("Testing POST /generateAssessmentReport endpoint...")
        
        request_data = {
            "app_id": integration_config["app_id"],
            "storage_account_name": integration_config["storage_account_name"],
            "azure_region": integration_config["azure_region"],
            "user_object_id": integration_config["user_object_id"]
        }
        
        response = await http_client.post("/generateAssessmentReport", json=request_data)
        
        assert response.status_code == 202, f"Expected 202, got {response.status_code}: {response.text}"
        
        response_data = response.json()
        logger.info(f"Response: {response_data}")
        
        # Verify response structure
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
        logger.info("ASSESSMENT REPORT GENERATION RESULT:")
        logger.info("="*80)
        logger.info(json.dumps(result_data, indent=2))
        logger.info("="*80 + "\n")
        
        # Verify result structure
        assert "status" in result_data
        assert "operation_id" in result_data
        assert "app_id" in result_data
        assert "message" in result_data
        assert result_data["status"] == "success", f"Operation failed with status: {result_data.get('status')}"
        assert result_data["operation_id"] == operation_id
        assert result_data["app_id"] == request_data["app_id"]
        
        # Check for errors in the result
        if "error_details" in result_data and result_data["error_details"]:
            pytest.fail(f"Operation completed with errors: {result_data['error_details']}")
        
        # Verify message doesn't contain error indicators
        message = result_data.get("message", "")
        error_indicators = ["error", "failed", "exception", "no such file", "not found"]
        if any(indicator in message.lower() for indicator in error_indicators):
            pytest.fail(f"Operation message indicates error: {message}")
        
        # Verify report was generated successfully
        assert result_data["message"] is not None
        assert "report_url" in result_data, "Report URL missing from result"
        
        # Verify report_url is not None (report was actually generated)
        if result_data["report_url"] is None:
            pytest.fail("Report URL is None - report generation failed")
        
        logger.info("✅ Generate assessment report test passed")
    
    async def test_generate_assessment_report_incorrect_parameters(self, http_client, integration_config):
        """
        Test generating report when no analysis has been run.
        """
        logger.info("Testing POST /generateAssessmentReport with no analysis...")
        
        request_data = {
            "app_id": "incorrect-app-id",
            "storage_account_name": integration_config["storage_account_name"]
        }
        
        response = await http_client.post("/generateAssessmentReport", json=request_data)
        
        # Should handle gracefully (200 with message, 404, or 400)
        assert response.status_code == 422, f"Expected 422, got {response.status_code}"
        
        logger.info("✅ No analysis test passed")
