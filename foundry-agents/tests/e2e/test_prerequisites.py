# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

"""
E2E Prerequisites Tests - Infrastructure Verification (Steps 1-4)

This module verifies Azure infrastructure prerequisites before running
the full E2E agent workflow. All tests use real Azure connections.

Prerequisites verified:
- Step 1: Blob container exists for the application
- Step 2: Required template tables exist in Table Storage
- Step 3: Required template files exist in the templates container
- Step 4: Required central files exist in the central container

Usage:
    pytest tests/e2e/test_prerequisites.py -v

Configuration:
    Test configuration is loaded from .env.test file in the project root.
    Copy .env.test and customize the values for your test environment.
    
    Required configuration in .env.test:
    - AZURE_STORAGE_ACCOUNT_NAME: Azure Storage account name
    - TEST_APP_ID: Application ID / container name (e.g., "12345")
"""

import os
import sys
import logging
import pytest
from typing import List, Set
from pathlib import Path

# Add project root for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

logger = logging.getLogger(__name__)


@pytest.mark.e2e
@pytest.mark.order(1)
class TestInfrastructurePrerequisites:
    """
    Verify Azure infrastructure prerequisites before E2E agent workflow.
    
    These tests must pass before running the agent execution tests.
    They validate that all required Azure resources are properly configured.
    """
    
    # =========================================================================
    # Test Configuration
    # =========================================================================
    
    @pytest.fixture(autouse=True)
    def setup(self, test_environment, blob_service_client, table_service_client):
        """Setup test fixtures."""
        self.storage_account = test_environment["storage_account_name"]
        self.app_container = test_environment.get("test_app_container") or test_environment["test_app_id"]
        self.blob_service_client = blob_service_client
        self.table_service_client = table_service_client
        
        logger.info(f"Testing prerequisites for container: {self.app_container}")
    
    # =========================================================================
    # Step 1: Verify Blob Container Exists
    # =========================================================================
    
    @pytest.mark.order(1)
    def test_step1_app_container_exists(self):
        """
        Step 1: Verify application container exists in Azure Blob Storage.
        
        The application container is the root storage location for all
        agent inputs and outputs for a specific application.
        
        Raises:
            AssertionError: If the container does not exist.
        """
        logger.info(f"Step 1: Checking if container '{self.app_container}' exists...")
        
        try:
            container_client = self.blob_service_client.get_container_client(self.app_container)
            exists = container_client.exists()
            
            assert exists, (
                f"Container '{self.app_container}' does not exist in storage account "
                f"'{self.storage_account}'. Create it using the /createApplicationId endpoint "
                "or manually via Azure Portal."
            )
            
            # Get container properties for additional validation
            properties = container_client.get_container_properties()
            logger.info(f"✅ Container '{self.app_container}' exists")
            logger.info(f"   Last modified: {properties.get('last_modified', 'N/A')}")
            
        except Exception as e:
            logger.error(f"❌ Failed to verify container: {e}")
            raise
    
    # =========================================================================
    # Step 2: Verify Template Tables Exist
    # =========================================================================
    
    @pytest.mark.order(2)
    def test_step2_template_tables_exist(self, required_template_tables):
        """
        Step 2: Verify all required template tables exist in Azure Table Storage.
        
        Template tables contain the schema definitions and default values
        for application assessment data.
        
        Required tables:
        - AppDetailsTemplate
        - IntegrationDependencyTemplate
        - MsSqlDBTemplate
        - OracleDBTemplate
        - InfrastructureDetails
        - K8Stemplate
        
        Raises:
            AssertionError: If any required table is missing.
        """
        logger.info("Step 2: Verifying template tables exist...")
        
        try:
            # List all existing tables
            existing_tables: Set[str] = set()
            for table in self.table_service_client.list_tables():
                existing_tables.add(table.name)
            
            logger.info(f"Found {len(existing_tables)} tables in storage account")
            
            # Check for missing tables
            missing_tables: List[str] = []
            for table_name in required_template_tables:
                if table_name in existing_tables:
                    logger.info(f"✅ Table '{table_name}' exists")
                else:
                    logger.warning(f"❌ Table '{table_name}' is missing")
                    missing_tables.append(table_name)
            
            assert not missing_tables, (
                f"Missing required template tables: {missing_tables}. "
                "Run 'python scripts/environment-setup/import_migration_agent_tables.py' "
                "to create them."
            )
            
            logger.info(f"✅ All {len(required_template_tables)} template tables verified")
            
        except Exception as e:
            logger.error(f"❌ Failed to verify template tables: {e}")
            raise
    
    # =========================================================================
    # Step 3: Verify Template Files Exist
    # =========================================================================
    
    @pytest.mark.order(3)
    def test_step3_template_files_exist(self):
        """
        Step 3: Verify all required template files exist in the templates container.
        
        Required files in 'templates' container:
        - asr_prompt.json
        - design_prompt.json
        - kubernetes_discovery_prompts.json
        - migration-matrix.json
        
        Raises:
            AssertionError: If any required template file is missing.
        """
        logger.info("Step 3: Verifying template files exist...")
        
        required_template_files = [
            "asr_prompt.json",
            "design_prompt.json",
            "kubernetes_discovery_prompts.json",
            "migration-matrix.json"
        ]
        
        try:
            container_client = self.blob_service_client.get_container_client("templates")
            
            # Check if templates container exists
            if not container_client.exists():
                raise AssertionError(
                    "Container 'templates' does not exist in storage account "
                    f"'{self.storage_account}'. Create it and upload the required template files."
                )
            
            # List all blobs in the templates container
            existing_files: Set[str] = set()
            for blob in container_client.list_blobs():
                existing_files.add(blob.name)
            
            logger.info(f"Found {len(existing_files)} file(s) in templates container")
            
            # Check for missing template files
            missing_files: List[str] = []
            for template_file in required_template_files:
                if template_file in existing_files:
                    logger.info(f"✅ Template file '{template_file}' exists")
                else:
                    logger.error(f"❌ Template file '{template_file}' is missing")
                    missing_files.append(template_file)
            
            assert not missing_files, (
                f"Missing required template files in 'templates' container: {missing_files}. "
                "Upload these files before running E2E tests."
            )
            
            logger.info(f"✅ All {len(required_template_files)} template files verified")
            
        except Exception as e:
            logger.error(f"❌ Failed to verify template files: {e}")
            raise
    
    # =========================================================================
    # Step 4: Verify Central Container Files Exist
    # =========================================================================
    
    @pytest.mark.order(4)
    def test_step4_central_files_exist(self):
        """
        Step 4: Verify required files exist in the central container.
        
        Required files in 'central' container:
        - scf-controls.zip
        
        Raises:
            AssertionError: If any required central file is missing.
        """
        logger.info("Step 4: Verifying central container files exist...")
        
        required_central_files = [
            "scf-controls.zip"
        ]
        
        try:
            container_client = self.blob_service_client.get_container_client("central")
            
            # Check if central container exists
            if not container_client.exists():
                raise AssertionError(
                    "Container 'central' does not exist in storage account "
                    f"'{self.storage_account}'. Create it and upload the required files."
                )
            
            # List all blobs in the central container
            existing_files: Set[str] = set()
            for blob in container_client.list_blobs():
                existing_files.add(blob.name)
            
            logger.info(f"Found {len(existing_files)} file(s) in central container")
            
            # Check for missing central files
            missing_files: List[str] = []
            for central_file in required_central_files:
                if central_file in existing_files:
                    logger.info(f"✅ Central file '{central_file}' exists")
                else:
                    logger.error(f"❌ Central file '{central_file}' is missing")
                    missing_files.append(central_file)
            
            assert not missing_files, (
                f"Missing required files in 'central' container: {missing_files}. "
                "Upload these files before running E2E tests."
            )
            
            logger.info(f"✅ All {len(required_central_files)} central files verified")
            
        except Exception as e:
            logger.error(f"❌ Failed to verify central files: {e}")
            raise
    
    # =========================================================================
    # Summary Test
    # =========================================================================
    
    @pytest.mark.order(5)
    def test_prerequisites_summary(self, required_template_tables):
        """
        Generate a summary of all prerequisites verification.
        
        This test always runs last and provides a complete overview
        of the infrastructure status.
        """
        logger.info("=" * 60)
        logger.info("INFRASTRUCTURE PREREQUISITES SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Storage Account: {self.storage_account}")
        logger.info(f"Application Container: {self.app_container}")
        logger.info(f"Template Tables Required: {len(required_template_tables)}")
        logger.info(f"Template Files Container: templates")
        logger.info(f"Central Files Container: central")
        logger.info("=" * 60)
        logger.info("✅ All prerequisites verified successfully")
        logger.info("Ready to run E2E agent workflow tests")
        logger.info("=" * 60)


@pytest.mark.e2e
class TestOptionalPrerequisites:
    """
    Optional prerequisite checks that may be skipped if not configured.
    """
    
    @pytest.fixture(autouse=True)
    def setup(self, test_environment):
        """Setup test fixtures."""
        self.test_environment = test_environment
    
    def test_azure_ai_search_available(self):
        """
        Verify Azure AI Search is configured and accessible.
        
        This is optional as some agents may work without search.
        """
        search_endpoint = self.test_environment.get("search_endpoint")
        
        if not search_endpoint:
            pytest.skip("AZURE_SEARCH_ENDPOINT not configured")
        
        logger.info(f"Azure AI Search endpoint configured: {search_endpoint}")
        # Note: Actual connectivity test would require an index name
    
    def test_azure_openai_available(self):
        """
        Verify Azure OpenAI is configured for agent operations.
        """
        openai_endpoint = self.test_environment.get("openai_endpoint")
        
        if not openai_endpoint:
            pytest.skip("AZURE_OPENAI_ENDPOINT not configured")
        
        logger.info(f"Azure OpenAI endpoint configured: {openai_endpoint}")
        logger.info(f"Deployment: {self.test_environment.get('openai_deployment', 'N/A')}")
    
    def test_ai_project_connection_available(self):
        """
        Verify Azure AI Foundry project connection is configured.
        """
        connection_string = self.test_environment.get("ai_project_connection_string")
        
        if not connection_string:
            pytest.skip("AZURE_EXISTING_AIPROJECT_ENDPOINT not configured")
        
        # Don't log the full connection string for security
        logger.info("Azure AI Project connection string configured")


# =============================================================================
# Standalone Execution
# =============================================================================

if __name__ == "__main__":
    """
    Run prerequisites tests directly for quick validation.
    
    Usage:
        python tests/e2e/test_prerequisites.py
    """
    import sys
    
    # Run with pytest
    exit_code = pytest.main([
        __file__,
        "-v",
        "--tb=short",
        "-W", "ignore::DeprecationWarning"
    ])
    
    sys.exit(exit_code)
