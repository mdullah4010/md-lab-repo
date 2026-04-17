# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

"""
Integration tests for Azure Blob Storage infrastructure.

This module tests Azure Blob Storage integration with real Azure services:
- Blob container accessibility
- Directory structure validation
- Blob operations (list, read, write)
- Storage account connectivity

All tests connect to real Azure Blob Storage - no mocking is used.

Tests Completed:
    1. test_blob_storage_connectivity
       - Validates connection to Azure Storage account
       - Verifies network accessibility from test environment
       - Ensures proper authentication with Azure credentials
       - Tests storage service availability and health
    
    2. test_analysis_directory_structure
       - Tests complete analysis directory hierarchy
       - Ensures all required virtual directories are accessible in the Storage Blob Container
       - Confirms proper directory organization for analysis artifacts

Usage:
    pytest tests/integration/test_blob_storage_integration.py -v -s

Configuration:
    Test configuration is loaded from .env.test file in the project root.
    
    Required configuration in .env.test:
    - AZURE_STORAGE_ACCOUNT_NAME: Azure Storage account name
    - AZURE_STORAGE_ACCOUNT_URL: Azure Storage account URL
    - TEST_APP_ID: Application ID for testing
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
class TestAzureBlobStorageIntegration:
    """
    Test integration with Azure Blob Storage.
    """
    
    @pytest.fixture(autouse=True)
    def setup(self, integration_config, container_client):
        """Setup test fixtures."""
        self.config = integration_config
        self.container_client = container_client
        self.app_id = integration_config["app_id"]
    
    def test_blob_storage_connectivity(self):
        """
        Test basic connectivity to Azure Blob Storage.
        """
        logger.info("Testing Azure Blob Storage connectivity...")
        
        try:
            # Check if container exists
            exists = self.container_client.exists()
            if exists:
                logger.info("✅ Azure Blob Storage connectivity successful")
            else:
                logger.warning("⚠️  Container does not exist")
                pytest.skip("Container does not exist")
        except Exception as e:
            logger.error(f"❌ Azure Blob Storage connectivity failed: {e}")
            raise
      
    
    def test_analysis_directory_structure(self):
        """
        Test that expected directory structure exists for analysis operations.
        """
        logger.info("Checking analysis directory structure...")
        
        expected_directories = [
            "responder/input/",
            "asr/output/",
            "design/output",
            "code-analyzer/input/",
            "code-analyzer/output/",
            "kubernetes-discovery/input",
            "kubernetes-discovery/output",
            "architecture-analyzer/output",
            "app-planning/output"
        ]
        
        missing_directories = []
        
        for directory in expected_directories:
            try:
                blobs = list(self.container_client.list_blobs(name_starts_with=directory))
                blob_count = len(blobs)
                
                if blob_count > 0:
                    logger.info(f"✅ Directory '{directory}': {blob_count} blob(s)")
                else:
                    logger.error(f"❌ Directory '{directory}': 0 blob(s) - directory is empty or doesn't exist")
                    missing_directories.append(directory)
            except Exception as e:
                logger.error(f"❌ Failed to access directory '{directory}': {e}")
                missing_directories.append(directory)
        
        # Assert that all expected directories exist with content
        assert len(missing_directories) == 0, \
            f"Missing or empty directories: {', '.join(missing_directories)}"


# =============================================================================
# Standalone Execution
# =============================================================================

if __name__ == "__main__":
    """
    Run Azure Blob Storage integration tests directly.
    
    Usage:
        python tests/integration/test_blob_storage_integration.py
    """
    import sys
    
    exit_code = pytest.main([
        __file__,
        "-v",
        "--tb=short",
        "-W", "ignore::DeprecationWarning"
    ])
    
    sys.exit(exit_code)
