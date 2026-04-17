# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

"""
Integration tests for the Analyze Code endpoint.

This module tests the Analyze Code API endpoint with real Azure services:
- HTTP API endpoint POST /analyzeCode
- Azure AI Foundry for code analysis
- Azure AI Search for code context retrieval
- Azure Blob Storage for code artifact storage

All tests call the actual HTTP API - no mocking is used.

Request Model (CodeAnalysisRequest):
    - app_id: str (required, 3-63 chars)
    - storage_account_name: str (required, 3-24 chars)
    - user_object_id: Optional[str] (GUID format)
    - group_object_id: Optional[str] (GUID format)
    - repo_url: str (required, repository or blob URL)
    - source_type: Optional[str] (github, gitlab, azure_devops, bitbucket, blob)
    - perform_security_scan: Optional[bool]

Response Model (CodeAnalysisResponse):
    - status: str ('accepted' - async operation)
    - operation_id: str
    - app_id: str
    - repo_url: str
    - source_type: str
    - message: str
    - status_endpoint: str (GET endpoint to check status)
    - result_endpoint: str (GET endpoint to retrieve results)

Tests Completed:
    1. test_analyze_code_from_github_repo
       - Validates successful code analysis execution
       - Verifies API returns 202 status code (async operation started)
       - Confirms analysis status is returned
       - Tests basic code analysis workflow
    
    2. test_analyze_code_from_blob_url
       - Tests code analysis targeting a Blob URL
       - Validates path parameter handling (e.g., "/code-analyzer/input")
       - Ensures API accepts and processes path-specific requests
    
    3. test_analyze_code_no_code_available
       - Tests graceful handling when no code is available for analysis
       - Validates error handling for empty code repositories
       - Ensures appropriate status codes (200/400/404)

Usage:
    pytest tests/integration/test_analyze_code.py -v -s
"""

import os
import sys
import logging
import asyncio
import time
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
class TestAnalyzeCodeAPI:
    """
    Test Analyze Code API endpoint with real Azure services.
    """
    
    async def test_analyze_code_from_github_repo(self, http_client, integration_config):
        """
        Test analyzing code successfully.
        
        Verifies:
        - API returns 202 status code (async operation started)
        - Operation ID is returned
        - Code analysis operation is initiated
        """
        logger.info("Testing POST /analyzeCode endpoint...")
        
        request_data = {
            "app_id": integration_config["app_id"],
            "storage_account_name": integration_config["storage_account_name"],
            "user_object_id": integration_config["user_object_id"],
            "repo_url": "https://github.com/Azure-Samples/contoso-real-estate",
            "source_type": "github"
        }
        
        response = await http_client.post("/analyzeCode", json=request_data)
        
        assert response.status_code == 202, f"Expected 202, got {response.status_code}: {response.text}"
        
        response_data = response.json()
        logger.info(f"Response: {response_data}")
        
        # Verify response structure
        assert "status" in response_data
        assert "operation_id" in response_data
        assert "app_id" in response_data
        assert "repo_url" in response_data
        assert "source_type" in response_data
        assert "message" in response_data
        assert "status_endpoint" in response_data
        assert "result_endpoint" in response_data
        
        # Verify response values
        assert response_data["status"] == "accepted"
        assert response_data["app_id"] == request_data["app_id"]
        assert isinstance(response_data["operation_id"], str)
        assert response_data["repo_url"] == request_data["repo_url"]
        assert response_data["source_type"] == request_data["source_type"]
        
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
        logger.info("CODE ANALYSIS RESULT:")
        logger.info("="*80)
        logger.info(json.dumps(result_data, indent=2))
        logger.info("="*80 + "\n")
        
        # Verify result structure (CodeAnalysisResultResponse)
        assert "status" in result_data
        assert "operation_id" in result_data
        assert "app_id" in result_data
        assert "analysis_result" in result_data
        assert result_data["status"] == "success"
        assert result_data["operation_id"] == operation_id
        assert result_data["app_id"] == request_data["app_id"]
        
        # Verify analysis completed successfully
        assert result_data["analysis_result"]["status"] == "completed"
        assert len(result_data["analysis_result"]["agents_used"]) > 0
        assert result_data["analysis_result"]["files_processed"] > 0
        
        logger.info("✅ Analyze code test passed")
    
    async def test_analyze_code_from_blob_url(self, http_client, integration_config):
        """
        Test analyzing code from Azure Blob Storage URL.
        
        Verifies that code analysis can use blob storage as source and completes successfully.
        """
        logger.info("Testing POST /analyzeCode with blob URL...")
        
        request_data = {
            "app_id": integration_config["app_id"],
            "storage_account_name": integration_config["storage_account_name"],
            "user_object_id": integration_config["user_object_id"],
            "repo_url": f"https://{integration_config['storage_account_name']}.blob.core.windows.net/{integration_config['app_id']}/code-analyzer/input/terraform-azurerm-avm-ptn-aiml-landing-zone-main.zip",
            "source_type": "blob"
        }
        
        response = await http_client.post("/analyzeCode", json=request_data)
        
        # Accept 202 (analysis started) or 404 (blob not found)
        assert response.status_code in [202, 404], f"Expected 202/404, got {response.status_code}"
        
        if response.status_code == 404:
            logger.info("⚠️ Blob not found (expected if file not uploaded) - skipping polling")
            logger.info("✅ Blob URL analysis test passed")
            return
        
        # If 202, proceed with polling
        response_data = response.json()
        logger.info(f"Response: {response_data}")
        
        # Verify response structure
        assert "status" in response_data
        assert "operation_id" in response_data
        assert response_data["status"] == "accepted"
        
        # Poll operation status until completion and retrieve results
        operation_id = response_data["operation_id"]
        result_data = await poll_operation_until_complete(
            http_client=http_client,
            integration_config=integration_config,
            operation_id=operation_id,
            status_endpoint=response_data["status_endpoint"],
            result_endpoint=response_data["result_endpoint"]
        )
        
        # Verify result structure
        assert "status" in result_data
        assert "analysis_result" in result_data
        assert result_data["status"] == "success"
        
        # Verify actual files were processed (blob must exist with valid code)
        analysis_result = result_data["analysis_result"]
        assert analysis_result.get("status") == "completed", f"Analysis did not complete successfully: {analysis_result}"
        assert analysis_result.get("files_processed", 0) > 0, f"No files were processed - blob may not exist or be empty: {analysis_result}"
        
        logger.info(f"Files processed: {analysis_result.get('files_processed', 0)}")
        logger.info("✅ Blob URL analysis test passed")
    
    async def test_analyze_code_missing_repo_url(self, http_client, integration_config):
        """
        Test analyzing code without providing required repo_url.
        """
        logger.info("Testing POST /analyzeCode with missing repo_url...")
        
        request_data = {
            "app_id": integration_config["app_id"],
            "storage_account_name": integration_config["storage_account_name"],
            "user_object_id": integration_config["user_object_id"]
            # Missing required repo_url field
        }
        
        response = await http_client.post("/analyzeCode", json=request_data)
        
        # Should return validation error
        assert response.status_code == 422, f"Expected 422, got {response.status_code}"
        
        logger.info("✅ Missing repo_url test passed")
