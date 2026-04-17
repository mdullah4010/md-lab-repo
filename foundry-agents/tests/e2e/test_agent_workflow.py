# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

"""
E2E Agent Workflow Tests - Full Agent Execution Sequence (Steps 1-11)

This module tests the complete agent workflow by calling API endpoints
in the required sequence and verifying successful completion.

Note: Response quality evaluation is handled by dedicated tests in
tests/evaluation/test_response_quality.py.

Workflow Steps:
- Step 1:  POST /createApplicationId    → Initialize application storage container
- Step 2:  Upload artifacts             → Upload sample artifacts to blob storage
- Step 3:  POST /analyzeCode            → Code Analyzer Agent
- Step 4:  POST /discoverKubernetes     → Kubernetes Discovery Agent
- Step 5:  Copy outputs                 → Copy agent outputs to responder input
- Step 6:  POST /runAnalysis            → Responder Agent
- Step 7:  POST /generateAssessmentReport → ASR Agent
- Step 8:  Copy outputs                 → Copy ASR output to design input
- Step 9:  POST /generateDesign         → Design Agent
- Step 10: Copy outputs                 → Copy design output to architecture-analyzer input
- Step 11: POST /analyzeArchitecture    → Architecture Analyzer Agent
- Step 12: POST /deleteAppData          → Clean up application data

Usage:
    # Run all workflow steps in order (RECOMMENDED: stop on first failure)
    pytest tests/e2e/test_agent_workflow.py -v --order-scope=class -x
    
    # Run only specific steps (e.g., steps 1-3)
    pytest tests/e2e/test_agent_workflow.py -v --order-scope=class -x -k "step1 or step2 or step3"
    
    # Skip specific steps (e.g., skip step 3)
    pytest tests/e2e/test_agent_workflow.py -v --order-scope=class -x -k "not step3"

Parameters:
    --order-scope=class
        Ensures tests run in sequential order (1-11) as defined by @pytest.mark.order(n).
        Required because each step depends on outputs from previous steps.
        Without this flag, pytest runs tests in random order, causing failures.
    
    -x, --exitfirst
        **RECOMMENDED**: Stop execution on first test failure.
        Essential for E2E workflows where each step depends on previous steps.
        Without this flag, pytest continues running remaining tests even after failures,
        causing cascading failures and wasting time.
        
    -k "expression"
        Filter tests by name using keyword expressions.
        Useful for running or skipping specific workflow steps.
        Examples:
            -k "step1"              → Run only step 1
            -k "step1 or step2"     → Run steps 1 and 2
            -k "not step3"          → Skip step 3, run all others
            -k "not (step3 or step4)" → Skip steps 3 and 4

Configuration:
    Test configuration is loaded from .env.test file in the project root.
    Copy .env.test and customize the values for your test environment.
    
    Required configuration in .env.test:
    - API_BASE_URL: Base URL for the API (default: http://localhost:8000)
    - AZURE_STORAGE_ACCOUNT_NAME: Azure Storage account name
    - TEST_APP_ID: Application ID for testing
"""

import os
import sys
import json
import logging
import pytest
import asyncio
import time
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime

# Azure Storage imports
from azure.storage.blob import BlobServiceClient, ContainerClient
from azure.identity import DefaultAzureCredential

# Add project root for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Import helper from integration tests
from tests.integration.test_helpers import poll_operation_until_complete

# Configure logging with both console and file output
logger = logging.getLogger(__name__)

# Setup file logging
REPORTS_DIR = Path(__file__).parent / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# Create timestamped log file
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
log_file = REPORTS_DIR / f"e2e_workflow_{timestamp}.log"

# Add file handler to logger
file_handler = logging.FileHandler(log_file, mode='w', encoding='utf-8')
file_handler.setLevel(logging.INFO)
file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)

logger.info(f"Test run started - Log file: {log_file}")


@pytest.mark.e2e
@pytest.mark.usefixtures("verify_api_health", "refresh_token_before_test")
class TestE2EAgentWorkflow:
    """
    End-to-end test suite for the complete agent workflow.
    
    Tests execute in order (Steps 1-11) and share state through
    the workflow_state fixture. Response quality evaluation is
    handled by dedicated tests in tests/evaluation/.
    """
    
    # =========================================================================
    # Configuration
    # =========================================================================
    
    CONFIDENCE_THRESHOLD = 0.7  # 70% for responder agent
    
    # Sample artifacts paths (relative to tests/e2e/)
    SAMPLE_ARTIFACTS_BASE = Path(__file__).parent / "sample-artifacts"
    
    # =========================================================================
    # Test Setup
    # =========================================================================
    
    @pytest.fixture(autouse=True)
    def setup(self, e2e_config, http_client, workflow_state, operation_poller, blob_service_client):
        """Setup test fixtures for each test."""
        self.config = e2e_config
        self.http_client = http_client
        self.workflow_state = workflow_state
        self.poller = operation_poller
        self.blob_service_client = blob_service_client
        
        # Update thresholds from config if provided
        self.CONFIDENCE_THRESHOLD = e2e_config.get("confidence_threshold", self.CONFIDENCE_THRESHOLD)
    
    # =========================================================================
    # Helper Methods
    # =========================================================================
    
    def _get_payload(self, **extra_fields) -> Dict[str, Any]:
        """Build standard API payload with app configuration."""
        payload = {
            "app_id": self.config["app_id"],
            "storage_account_name": self.config["storage_account_name"],
            "azure_region": self.config.get("azure_region", "eastus"),
        }
        
        if self.config.get("resource_group_name"):
            payload["resource_group_name"] = self.config["resource_group_name"]
        
        if self.config.get("user_object_id"):
            payload["user_object_id"] = self.config["user_object_id"]
        
        payload.update(extra_fields)
        return payload
    
    def _get_container_client(self) -> ContainerClient:
        """Get container client for the application container."""
        app_id = self.config["app_id"]
        return self.blob_service_client.get_container_client(app_id)
    
    def _upload_local_folder_to_blob(
        self, 
        local_folder: Path, 
        blob_prefix: str
    ) -> int:
        """
        Upload all files from a local folder to blob storage.
        
        Args:
            local_folder: Path to local folder containing files to upload
            blob_prefix: Blob path prefix (e.g., "code-analyzer/input/")
            
        Returns:
            Number of files uploaded
        """
        container_client = self._get_container_client()
        uploaded_count = 0
        
        if not local_folder.exists():
            logger.warning(f"Local folder does not exist: {local_folder}")
            return 0
        
        for file_path in local_folder.rglob("*"):
            if file_path.is_file():
                # Calculate relative path from local folder
                relative_path = file_path.relative_to(local_folder)
                blob_name = f"{blob_prefix}{relative_path.as_posix()}"
                
                logger.info(f"Uploading: {file_path} -> {blob_name}")
                
                with open(file_path, "rb") as data:
                    container_client.upload_blob(
                        name=blob_name,
                        data=data,
                        overwrite=True
                    )
                uploaded_count += 1
        
        return uploaded_count
    
    def _copy_blobs_between_folders(
        self,
        source_prefix: str,
        destination_prefix: str
    ) -> int:
        """
        Copy all blobs from one folder to another within the same container.
        
        Args:
            source_prefix: Source blob path prefix (e.g., "code-analyzer/output/")
            destination_prefix: Destination blob path prefix (e.g., "responder/input/")
            
        Returns:
            Number of blobs copied
        """
        container_client = self._get_container_client()
        copied_count = 0
        
        # List all blobs with the source prefix
        blobs = container_client.list_blobs(name_starts_with=source_prefix)
        
        for blob in blobs:
            source_blob_name = blob.name
            # Calculate destination blob name
            relative_path = source_blob_name[len(source_prefix):]
            dest_blob_name = f"{destination_prefix}{relative_path}"
            
            logger.info(f"Copying: {source_blob_name} -> {dest_blob_name}")
            
            # Get source blob URL
            source_blob_client = container_client.get_blob_client(source_blob_name)
            dest_blob_client = container_client.get_blob_client(dest_blob_name)
            
            # Copy blob
            dest_blob_client.start_copy_from_url(source_blob_client.url)
            copied_count += 1
        
        return copied_count
    
    def _list_blobs_in_folder(self, prefix: str) -> List[str]:
        """List all blobs in a folder."""
        container_client = self._get_container_client()
        blobs = container_client.list_blobs(name_starts_with=prefix)
        return [blob.name for blob in blobs]
    
    # =========================================================================
    # Step 1: Initialize Application Storage Container
    # =========================================================================
    
    @pytest.mark.asyncio
    @pytest.mark.order(1)
    async def test_step1_create_application_id(self):
        """
        Step 1: Initialize the application (POST /createApplicationId).
        
        This endpoint:
        - Creates the application container in Blob Storage
        - Sets up RBAC permissions
        - Creates tables and folder structure required by agents
        
        Success Criteria:
        - HTTP 200 response
        - Response contains success status or container info
        """
        step_start_time = time.time()
        logger.info("=" * 60)
        logger.info("STEP 1: Creating Application ID")
        logger.info("=" * 60)
        
        payload = self._get_payload()
        logger.info(f"Creating application with ID: {payload['app_id']}")
        logger.info(f"Payload: {json.dumps(payload, indent=2)}")
        
        try:
            response = await self.http_client.post("/createApplicationId", json=payload)
        except Exception as e:
            logger.error(f"HTTP request failed with exception: {type(e).__name__}: {e}")
            raise
        
        logger.info(f"Response status: {response.status_code}")
        logger.info(f"Response text: {response.text[:1000] if response.text else 'No response body'}")
        
        assert response.status_code == 200, (
            f"Failed to create application: {response.status_code} - {response.text}"
        )
        
        data = response.json()
        logger.info(f"Response data: {json.dumps(data, indent=2)}")
        
        # Validate response
        is_success = (
            data.get("status") == "success" or
            data.get("container", {}).get("status") in ["created", "already_exists"]
        )
        
        assert is_success, f"Unexpected response: {data}"
        
        # Record step completion
        self.workflow_state.mark_step_completed("create_application_id", data)
        
        step_duration = time.time() - step_start_time
        logger.info(f"⏱️  Step 1 duration: {step_duration:.2f} seconds ({step_duration/60:.2f} minutes)")
        logger.info("✅ Step 1 completed successfully")
    
    # =========================================================================
    # Step 2: Upload Application Artifacts
    # =========================================================================
    
    @pytest.mark.asyncio
    @pytest.mark.order(2)
    async def test_step2_upload_artifacts(self):
        """
        Step 2: Upload application artifacts to Azure Storage Account container.
        
        Uploads from:
        - tests/e2e/sample-artifacts/code-analyzer/ -> [app-id]/code-analyzer/input/
        - tests/e2e/sample-artifacts/kubernetes-discovery/ -> [app-id]/kubernetes-discovery/input/
        - tests/e2e/sample-artifacts/responder/ -> [app-id]/responder/input/
        
        Success Criteria:
        - All artifacts uploaded successfully
        - Files exist in blob storage
        """
        step_start_time = time.time()
        logger.info("=" * 60)
        logger.info("STEP 2: Uploading Application Artifacts")
        logger.info("=" * 60)
        
        app_id = self.config["app_id"]
        total_uploaded = 0
        upload_results = {}
        
        # Define upload mappings
        uploads = [
            {
                "local_folder": self.SAMPLE_ARTIFACTS_BASE / "code-analyzer",
                "blob_prefix": "code-analyzer/input/",
                "name": "code-analyzer"
            },
            {
                "local_folder": self.SAMPLE_ARTIFACTS_BASE / "kubernetes-discovery",
                "blob_prefix": "kubernetes-discovery/input/",
                "name": "kubernetes-discovery"
            },
            {
                "local_folder": self.SAMPLE_ARTIFACTS_BASE / "responder",
                "blob_prefix": "responder/input/",
                "name": "responder"
            }
        ]
        
        for upload in uploads:
            logger.info(f"\nUploading {upload['name']} artifacts...")
            logger.info(f"  From: {upload['local_folder']}")
            logger.info(f"  To: {app_id}/{upload['blob_prefix']}")
            
            count = self._upload_local_folder_to_blob(
                local_folder=upload["local_folder"],
                blob_prefix=upload["blob_prefix"]
            )
            
            upload_results[upload["name"]] = count
            total_uploaded += count
            logger.info(f"  Uploaded {count} file(s)")
        
        logger.info(f"\nTotal files uploaded: {total_uploaded}")
        logger.info(f"Upload summary: {json.dumps(upload_results, indent=2)}")
        
        # Verify at least some files were uploaded
        assert total_uploaded > 0, (
            f"No artifacts were uploaded. Ensure sample-artifacts folders exist at: "
            f"{self.SAMPLE_ARTIFACTS_BASE}"
        )
        
        self.workflow_state.mark_step_completed("upload_artifacts", {
            "total_uploaded": total_uploaded,
            "details": upload_results
        })
        
        step_duration = time.time() - step_start_time
        logger.info(f"⏱️  Step 2 duration: {step_duration:.2f} seconds ({step_duration/60:.2f} minutes)")
        logger.info("✅ Step 2 completed successfully")
    
    # =========================================================================
    # Step 3: Code Analyzer Agent
    # =========================================================================
    
    @pytest.mark.asyncio
    @pytest.mark.order(3)
    async def test_step3_code_analyzer_agent(self):
        """
        Step 3: Run Code Analyzer Agent (POST /analyzeCode).
        
        Analyzes the source code and generates a migration assessment report.
        """
        step_start_time = time.time()
        logger.info("=" * 60)
        logger.info("STEP 3: Code Analyzer Agent")
        logger.info("=" * 60)
        
        payload = {
            "app_id": self.config["app_id"],
            "storage_account_name": self.config["storage_account_name"],
            "user_object_id": self.config["user_object_id"],
            "repo_url": "https://github.com/Azure-Samples/contoso-real-estate",
            "source_type": "github"
        }
       
        logger.info(f"Running code analysis for: {payload['app_id']}")
        logger.info(f"Repo URL: {payload['repo_url']}")
        
        response = await self.http_client.post("/analyzeCode", json=payload)
        
        logger.info(f"Response status: {response.status_code}")
        logger.info(f"Response text: {response.text[:1000] if response.text else 'No response body'}")
        
        assert response.status_code in [200, 202], (
            f"Code analysis failed: {response.status_code} - {response.text}"
        )
        
        data = response.json()
        logger.info(f"Response data: {json.dumps(data, indent=2)}")
        
        # Handle async operation (202 response)
        if response.status_code == 202 and data.get("operation_id"):
            logger.info(f"Polling for operation: {data['operation_id']}")
            result_data = await poll_operation_until_complete(
                http_client=self.http_client,
                integration_config={
                    "app_id": self.config["app_id"],
                    "user_object_id": self.config.get("user_object_id", ""),
                    "storage_account_name": self.config["storage_account_name"]
                },
                operation_id=data["operation_id"],
                status_endpoint=data.get("status_endpoint", ""),
                result_endpoint=data.get("result_endpoint", "")
            )
            data = result_data
        
        # Log report size
        analysis_result = data.get("analysis_result", data)
        report_content = str(analysis_result)
        logger.info(f"Report length: {len(report_content)} characters")
        
        self.workflow_state.mark_step_completed("code_analyzer", data)
        
        step_duration = time.time() - step_start_time
        logger.info(f"⏱️  Step 3 duration: {step_duration:.2f} seconds ({step_duration/60:.2f} minutes)")
        logger.info("✅ Step 3 completed successfully")
    
    # =========================================================================
    # Step 4: Kubernetes Discovery Agent
    # =========================================================================
    
    @pytest.mark.asyncio
    @pytest.mark.order(4)
    async def test_step4_kubernetes_discovery_agent(self):
        """
        Step 4: Run Kubernetes Discovery Agent (POST /discoverKubernetes).
        
        Discovers and analyzes Kubernetes deployment configuration.
        """
        step_start_time = time.time()
        logger.info("=" * 60)
        logger.info("STEP 4: Kubernetes Discovery Agent")
        logger.info("=" * 60)
        
        payload = self._get_payload()
        logger.info(f"Running K8s discovery for: {payload['app_id']}")
        
        response = await self.http_client.post("/discoverKubernetes", json=payload)
        
        logger.info(f"Response status: {response.status_code}")
        logger.info(f"Response text: {response.text[:1000] if response.text else 'No response body'}")
        
        assert response.status_code in [200, 202], (
            f"Kubernetes discovery failed: {response.status_code} - {response.text}"
        )
        
        data = response.json()
        logger.info(f"Response data: {json.dumps(data, indent=2)}")
        
        # Handle async operation (202 response)
        if response.status_code == 202 and data.get("operation_id"):
            logger.info(f"Polling for operation: {data['operation_id']}")
            result_data = await poll_operation_until_complete(
                http_client=self.http_client,
                integration_config={
                    "app_id": self.config["app_id"],
                    "user_object_id": self.config.get("user_object_id", ""),
                    "storage_account_name": self.config["storage_account_name"]
                },
                operation_id=data["operation_id"],
                status_endpoint=data.get("status_endpoint", ""),
                result_endpoint=data.get("result_endpoint", "")
            )
            data = result_data
        
        report_content = (
            data.get("report") or 
            data.get("discovery_result") or 
            data.get("result", "")
        )
        
        if report_content:
            logger.info(f"Discovery report length: {len(str(report_content))} characters")
        
        self.workflow_state.mark_step_completed("kubernetes_discovery", data)
        
        step_duration = time.time() - step_start_time
        logger.info(f"⏱️  Step 4 duration: {step_duration:.2f} seconds ({step_duration/60:.2f} minutes)")
        logger.info("✅ Step 4 completed successfully")
    
    # =========================================================================
    # Step 5: Copy Agent Outputs to Responder Input
    # =========================================================================
    
    @pytest.mark.asyncio
    @pytest.mark.order(5)
    async def test_step5_copy_outputs_to_responder(self):
        """
        Step 5: Copy agent outputs to responder input folder.
        
        Copies:
        - [app-id]/code-analyzer/output/ -> [app-id]/responder/input/
        - [app-id]/kubernetes-discovery/output/ -> [app-id]/responder/input/
        """
        step_start_time = time.time()
        logger.info("=" * 60)
        logger.info("STEP 5: Copying Agent Outputs to Responder Input")
        logger.info("=" * 60)
        
        app_id = self.config["app_id"]
        total_copied = 0
        copy_results = {}
        
        # Define copy operations
        copies = [
            {
                "source": "code-analyzer/output/",
                "destination": "responder/input/",
                "name": "code-analyzer -> responder"
            },
            {
                "source": "kubernetes-discovery/output/",
                "destination": "responder/input/",
                "name": "kubernetes-discovery -> responder"
            }
        ]
        
        for copy_op in copies:
            logger.info(f"\nCopying {copy_op['name']}...")
            logger.info(f"  From: {app_id}/{copy_op['source']}")
            logger.info(f"  To: {app_id}/{copy_op['destination']}")
            
            # List source blobs first
            source_blobs = self._list_blobs_in_folder(copy_op["source"])
            logger.info(f"  Found {len(source_blobs)} blob(s) in source")
            
            count = self._copy_blobs_between_folders(
                source_prefix=copy_op["source"],
                destination_prefix=copy_op["destination"]
            )
            
            copy_results[copy_op["name"]] = count
            total_copied += count
            logger.info(f"  Copied {count} blob(s)")
        
        logger.info(f"\nTotal blobs copied: {total_copied}")
        logger.info(f"Copy summary: {json.dumps(copy_results, indent=2)}")
        
        self.workflow_state.mark_step_completed("copy_to_responder", {
            "total_copied": total_copied,
            "details": copy_results
        })
        
        step_duration = time.time() - step_start_time
        logger.info(f"⏱️  Step 5 duration: {step_duration:.2f} seconds ({step_duration/60:.2f} minutes)")
        logger.info("✅ Step 5 completed successfully")
    
    # =========================================================================
    # Step 6: Responder Agent
    # =========================================================================
    
    @pytest.mark.asyncio
    @pytest.mark.order(6)
    async def test_step6_responder_agent(self):
        """
        Step 6: Run Responder Agent (POST /runAnalysis).
        
        Analyzes input documents and populates template tables
        with extracted information and confidence scores.
        """
        step_start_time = time.time()
        logger.info("=" * 60)
        logger.info("STEP 6: Responder Agent")
        logger.info("=" * 60)
        
        payload = self._get_payload()
        logger.info(f"Running analysis for: {payload['app_id']}")
        
        response = await self.http_client.post("/runAnalysis", json=payload)
        
        logger.info(f"Response status: {response.status_code}")
        logger.info(f"Response text: {response.text[:1000] if response.text else 'No response body'}")
        
        assert response.status_code in [200, 202], (
            f"Responder analysis failed: {response.status_code} - {response.text}"
        )
        
        data = response.json()
        logger.info(f"Response data: {json.dumps(data, indent=2)}")
        
        # Handle async operation (202 response)
        if response.status_code == 202 and data.get("operation_id"):
            logger.info(f"Polling for operation: {data['operation_id']}")
            result_data = await poll_operation_until_complete(
                http_client=self.http_client,
                integration_config={
                    "app_id": self.config["app_id"],
                    "user_object_id": self.config.get("user_object_id", ""),
                    "storage_account_name": self.config["storage_account_name"]
                },
                operation_id=data["operation_id"],
                status_endpoint=data.get("status_endpoint", ""),
                result_endpoint=data.get("result_endpoint", "")
            )
            data = result_data
        
        logger.info(f"Analysis completed: {data.get('status', 'N/A')}")
        
        # Check confidence levels
        confidence_scores = data.get("confidence_scores", {})
        all_confidences = []
        
        for table_name, scores in confidence_scores.items():
            if isinstance(scores, dict):
                avg_confidence = scores.get("average", 0)
            else:
                avg_confidence = float(scores) if scores else 0
            all_confidences.append(avg_confidence)
        
        if all_confidences:
            avg_overall = sum(all_confidences) / len(all_confidences)
            logger.info(f"Overall confidence: {avg_overall:.2%}")
            self.workflow_state.record_evaluation("responder", {"confidence": avg_overall})
        
        self.workflow_state.mark_step_completed("responder", data)
        
        step_duration = time.time() - step_start_time
        logger.info(f"⏱️  Step 6 duration: {step_duration:.2f} seconds ({step_duration/60:.2f} minutes)")
        logger.info("✅ Step 6 completed successfully")
    
    # =========================================================================
    # Step 7: ASR Agent
    # =========================================================================
    
    @pytest.mark.asyncio
    @pytest.mark.order(7)
    async def test_step7_asr_agent(self):
        """
        Step 7: Run ASR Agent (POST /generateAssessmentReport).
        
        Generates a comprehensive Migration Assessment Report.
        """
        step_start_time = time.time()
        logger.info("=" * 60)
        logger.info("STEP 7: ASR Agent")
        logger.info("=" * 60)
        
        payload = self._get_payload()
        logger.info(f"Generating ASR report for: {payload['app_id']}")
        
        response = await self.http_client.post("/generateAssessmentReport", json=payload)
        
        logger.info(f"Response status: {response.status_code}")
        logger.info(f"Response text: {response.text[:1000] if response.text else 'No response body'}")
        
        assert response.status_code in [200, 202], (
            f"ASR report generation failed: {response.status_code} - {response.text}"
        )
        
        data = response.json()
        logger.info(f"Response data: {json.dumps(data, indent=2)}")
        
        # Handle async operation (202 response)
        if response.status_code == 202 and data.get("operation_id"):
            logger.info(f"Polling for operation: {data['operation_id']}")
            result_data = await poll_operation_until_complete(
                http_client=self.http_client,
                integration_config={
                    "app_id": self.config["app_id"],
                    "user_object_id": self.config.get("user_object_id", ""),
                    "storage_account_name": self.config["storage_account_name"]
                },
                operation_id=data["operation_id"],
                status_endpoint=data.get("status_endpoint", ""),
                result_endpoint=data.get("result_endpoint", "")
            )
            data = result_data
        
        report_content = (
            data.get("report") or 
            data.get("assessment_report") or 
            data.get("result", "")
        )
        
        if report_content:
            logger.info(f"ASR report length: {len(str(report_content))} characters")
        
        self.workflow_state.mark_step_completed("asr", data)
        
        step_duration = time.time() - step_start_time
        logger.info(f"⏱️  Step 7 duration: {step_duration:.2f} seconds ({step_duration/60:.2f} minutes)")
        logger.info("✅ Step 7 completed successfully")
    
    # =========================================================================
    # Step 8: Copy ASR Output to Design Input
    # =========================================================================
    
    @pytest.mark.asyncio
    @pytest.mark.order(8)
    async def test_step8_copy_asr_to_design(self):
        """
        Step 8: Copy ASR output to Design input folder.
        
        Copies: [app-id]/asr/output/ -> [app-id]/design/input/
        """
        step_start_time = time.time()
        logger.info("=" * 60)
        logger.info("STEP 8: Copying ASR Output to Design Input")
        logger.info("=" * 60)
        
        app_id = self.config["app_id"]
        source = "asr/output/"
        destination = "design/input/"
        
        logger.info(f"Copying from: {app_id}/{source}")
        logger.info(f"Copying to: {app_id}/{destination}")
        
        # List source blobs first
        source_blobs = self._list_blobs_in_folder(source)
        logger.info(f"Found {len(source_blobs)} blob(s) in source")
        
        count = self._copy_blobs_between_folders(
            source_prefix=source,
            destination_prefix=destination
        )
        
        logger.info(f"Copied {count} blob(s)")
        
        self.workflow_state.mark_step_completed("copy_asr_to_design", {
            "copied": count
        })
        
        step_duration = time.time() - step_start_time
        logger.info(f"⏱️  Step 8 duration: {step_duration:.2f} seconds ({step_duration/60:.2f} minutes)")
        logger.info("✅ Step 8 completed successfully")
    
    # =========================================================================
    # Step 9: Design Agent
    # =========================================================================
    
    @pytest.mark.asyncio
    @pytest.mark.order(9)
    async def test_step9_design_agent(self):
        """
        Step 9: Run Design Agent (POST /generateDesign).
        
        Generates an Azure migration design document.
        """
        step_start_time = time.time()
        logger.info("=" * 60)
        logger.info("STEP 9: Design Agent")
        logger.info("=" * 60)
        
        payload = self._get_payload()
        logger.info(f"Generating design document for: {payload['app_id']}")
        
        response = await self.http_client.post("/generateDesign", json=payload)
        
        logger.info(f"Response status: {response.status_code}")
        logger.info(f"Response text: {response.text[:1000] if response.text else 'No response body'}")
        
        assert response.status_code in [200, 202], (
            f"Design generation failed: {response.status_code} - {response.text}"
        )
        
        data = response.json()
        logger.info(f"Response data: {json.dumps(data, indent=2)}")
        
        # Handle async operation (202 response)
        if response.status_code == 202 and data.get("operation_id"):
            logger.info(f"Polling for operation: {data['operation_id']}")
            result_data = await poll_operation_until_complete(
                http_client=self.http_client,
                integration_config={
                    "app_id": self.config["app_id"],
                    "user_object_id": self.config.get("user_object_id", ""),
                    "storage_account_name": self.config["storage_account_name"]
                },
                operation_id=data["operation_id"],
                status_endpoint=data.get("status_endpoint", ""),
                result_endpoint=data.get("result_endpoint", "")
            )
            data = result_data
        
        report_content = (
            data.get("design_document") or 
            data.get("result") or 
            data.get("design", "")
        )
        
        if report_content:
            logger.info(f"Design document length: {len(str(report_content))} characters")
        
        self.workflow_state.mark_step_completed("design", data)
        
        step_duration = time.time() - step_start_time
        logger.info(f"⏱️  Step 9 duration: {step_duration:.2f} seconds ({step_duration/60:.2f} minutes)")
        logger.info("✅ Step 9 completed successfully")
    
    # =========================================================================
    # Step 10: Copy Design Output to Architecture Analyzer Input
    # =========================================================================
    
    @pytest.mark.asyncio
    @pytest.mark.order(10)
    async def test_step10_copy_design_to_architecture(self):
        """
        Step 10: Copy Design output to Architecture Analyzer input folder.
        
        Copies: [app-id]/design/output/ -> [app-id]/architecture-analyzer/input/
        """
        step_start_time = time.time()
        logger.info("=" * 60)
        logger.info("STEP 10: Copying Design Output to Architecture Analyzer Input")
        logger.info("=" * 60)
        
        app_id = self.config["app_id"]
        source = "design/output/"
        destination = "architecture-analyzer/input/"
        
        logger.info(f"Copying from: {app_id}/{source}")
        logger.info(f"Copying to: {app_id}/{destination}")
        
        # List source blobs first
        source_blobs = self._list_blobs_in_folder(source)
        logger.info(f"Found {len(source_blobs)} blob(s) in source")
        
        count = self._copy_blobs_between_folders(
            source_prefix=source,
            destination_prefix=destination
        )
        
        logger.info(f"Copied {count} blob(s)")
        
        self.workflow_state.mark_step_completed("copy_design_to_architecture", {
            "copied": count
        })
        
        step_duration = time.time() - step_start_time
        logger.info(f"⏱️  Step 10 duration: {step_duration:.2f} seconds ({step_duration/60:.2f} minutes)")
        logger.info("✅ Step 10 completed successfully")
    
    # =========================================================================
    # Step 11: Architecture Analyzer Agent
    # =========================================================================
    
    @pytest.mark.asyncio
    @pytest.mark.order(11)
    async def test_step11_architecture_analyzer_agent(self):
        """
        Step 11: Run Architecture Analyzer Agent (POST /analyzeArchitecture).
        
        Analyzes application architecture for cloud migration readiness.
        """
        step_start_time = time.time()
        logger.info("=" * 60)
        logger.info("STEP 11: Architecture Analyzer Agent")
        logger.info("=" * 60)
        
        # Construct design_doc_url dynamically
        storage_account_name = self.config["storage_account_name"]
        app_id = self.config["app_id"]
        design_doc_url = f"https://{storage_account_name}.blob.core.windows.net/{app_id}/design/output/design-{app_id}.md"
        
        payload = self._get_payload(
            design_doc_url=design_doc_url
        )
        logger.info(f"Running architecture analysis for: {payload['app_id']}")
        logger.info(f"Design doc URL: {design_doc_url}")
        
        response = await self.http_client.post("/analyzeArchitecture", json=payload)
        
        logger.info(f"Response status: {response.status_code}")
        logger.info(f"Response text: {response.text[:1000] if response.text else 'No response body'}")
        
        assert response.status_code in [200, 202], (
            f"Architecture analysis failed: {response.status_code} - {response.text}"
        )
        
        data = response.json()
        logger.info(f"Response data: {json.dumps(data, indent=2)}")
        
        # Handle async operation (202 response)
        if response.status_code == 202 and data.get("operation_id"):
            logger.info(f"Polling for operation: {data['operation_id']}")
            
            # Construct status and result endpoints
            operation_id = data["operation_id"]
            status_endpoint = f"/operations/{operation_id}/status"
            result_endpoint = f"/operations/{operation_id}/result"
            
            result_data = await poll_operation_until_complete(
                http_client=self.http_client,
                integration_config={
                    "app_id": self.config["app_id"],
                    "user_object_id": self.config.get("user_object_id", ""),
                    "storage_account_name": self.config["storage_account_name"]
                },
                operation_id=operation_id,
                status_endpoint=status_endpoint,
                result_endpoint=result_endpoint
            )
            data = result_data
        
        report_content = (
            data.get("analysis") or 
            data.get("result") or 
            data.get("report", "")
        )
        
        if report_content:
            logger.info(f"Architecture analysis length: {len(str(report_content))} characters")
        
        self.workflow_state.mark_step_completed("architecture_analyzer", data)
        
        step_duration = time.time() - step_start_time
        logger.info(f"⏱️  Step 11 duration: {step_duration:.2f} seconds ({step_duration/60:.2f} minutes)")
        logger.info("✅ Step 11 completed successfully")
    
    # =========================================================================
    # Step 12: Delete Application Data
    # =========================================================================
    
    @pytest.mark.asyncio
    @pytest.mark.order(12)
    async def test_step12_delete_app_data(self):
        """
        Step 12: Clean up application data (POST /deleteAppData).
        
        Deletes all application data including:
        - All Agents for the app
        - All threads from the agents
        - Blob container and all artifacts
        - Table storage data
        - Search index entries
        
        Success Criteria:
        - HTTP 200 if container exists and is deleted
        - HTTP 404 if container doesn't exist (already deleted)
        """
        step_start_time = time.time()
        logger.info("=" * 60)
        logger.info("STEP 12: Delete Application Data")
        logger.info("=" * 60)
        
        payload = self._get_payload()
        logger.info(f"Deleting application data for: {payload['app_id']}")
        logger.warning("⚠️  This will delete all data for this application!")
        
        response = await self.http_client.post("/deleteAppData", json=payload)
        
        logger.info(f"Response status: {response.status_code}")
        logger.info(f"Response text: {response.text[:1000] if response.text else 'No response body'}")
        
        # Accept both 200 (deleted) and 404 (doesn't exist)
        assert response.status_code in [200, 404], (
            f"Delete app data failed: {response.status_code} - {response.text}"
        )
        
        data = response.json()
        logger.info(f"Response data: {json.dumps(data, indent=2)}")
        
        if response.status_code == 200:
            # Verify response structure
            assert "app_id" in data
            assert "message" in data
            assert "status" in data
            assert data["app_id"] == payload["app_id"]
            assert data["status"] in ["success", "partial_success"]
            
            # Log deletion details
            if "deletion_result" in data:
                deletion_result = data["deletion_result"]
                logger.info(f"Deletion result: {deletion_result}")
                
                if "errors" in deletion_result and deletion_result["errors"]:
                    logger.warning(f"Deletion had errors: {deletion_result['errors']}")
            
            logger.info("✅ Application data deleted successfully")
        else:
            # 404 - container doesn't exist
            logger.info("ℹ️  Container did not exist (may have been deleted already)")
        
        self.workflow_state.mark_step_completed("delete_app_data", data)
        
        step_duration = time.time() - step_start_time
        logger.info(f"⏱️  Step 12 duration: {step_duration:.2f} seconds ({step_duration/60:.2f} minutes)")
        logger.info("✅ Step 12 completed successfully")
    
    # =========================================================================
    # Workflow Summary
    # =========================================================================
    
    @pytest.mark.asyncio
    @pytest.mark.order(13)
    async def test_workflow_summary(self, test_report_file):
        """
        Generate and save E2E workflow summary.
        
        This test runs last and provides a complete overview
        of all agent executions and their completion status.
        """
        logger.info("=" * 60)
        logger.info("E2E WORKFLOW SUMMARY")
        logger.info("=" * 60)
        
        summary = self.workflow_state.get_summary()
        
        logger.info(f"Application ID: {summary['app_id']}")
        logger.info(f"Steps Completed: {summary['total_steps']}")
        logger.info(f"Errors: {len(summary['errors'])}")
        logger.info(f"Success: {summary['success']}")
        
        # Save report to file
        try:
            with open(test_report_file, "w") as f:
                json.dump(summary, f, indent=2, default=str)
            logger.info(f"\nReport saved to: {test_report_file}")
        except Exception as e:
            logger.warning(f"Failed to save report: {e}")
        
        logger.info("=" * 60)
        
        # Assert overall success
        assert summary["success"], (
            f"Workflow completed with {len(summary['errors'])} error(s): "
            f"{[e['step'] for e in summary['errors']]}"
        )


# =============================================================================
# Standalone Execution
# =============================================================================

if __name__ == "__main__":
    """
    Run E2E workflow tests directly.
    
    Usage:
        python tests/e2e/test_agent_workflow.py
    """
    import sys
    
    exit_code = pytest.main([
        __file__,
        "-v",
        "--order-scope=class",
        "-x",  # Stop on first failure
        "--tb=short",
        "-W", "ignore::DeprecationWarning"
    ])
    
    sys.exit(exit_code)
