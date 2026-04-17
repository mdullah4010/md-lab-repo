# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

"""
End-to-End (E2E) test fixtures for the insights-agent.

This module provides fixtures specific to E2E testing, including:
- HTTP client for API calls
- Evaluation metrics configuration
- Test result storage
- Workflow state management

All tests use real Azure connections - no mocking is used.
"""

import os
import sys
import logging
import pytest
import pytest_asyncio
import httpx
import asyncio
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

# Configure logging for E2E tests
logger = logging.getLogger(__name__)


# =============================================================================
# E2E Test Configuration
# =============================================================================

@pytest.fixture(scope="class")
def e2e_config(test_environment) -> Dict[str, Any]:
    """
    Provide E2E-specific configuration.
    
    Returns:
        Dictionary containing E2E test configuration.
    """
    config = {
        "api_base_url": test_environment["api_base_url"],
        "app_id": test_environment["test_app_id"],
        "storage_account_name": test_environment["storage_account_name"],
        "resource_group_name": test_environment.get("resource_group_name", ""),
        "azure_region": test_environment.get("azure_region", "eastus2"),
        "user_object_id": test_environment.get("test_user_object_id"),  # Use None as default instead of ""
        "timeout_seconds": test_environment["test_timeout_seconds"],
        
        # Evaluation thresholds
        "relevance_threshold": test_environment["relevance_threshold"],
        "groundedness_threshold": test_environment["groundedness_threshold"],
        "confidence_threshold": test_environment["confidence_threshold"],
    }
    
    logger.info(f"E2E Configuration - App ID: {config['app_id']}")
    logger.info(f"E2E Configuration - API URL: {config['api_base_url']}")
    
    return config


# =============================================================================
# HTTP Client Fixtures
# =============================================================================

@pytest_asyncio.fixture(scope="function")
async def http_client(e2e_config) -> httpx.AsyncClient:
    """
    Create an async HTTP client for API calls.
    
    The client is configured with:
    - Extended timeout for long-running operations
    - JSON content type headers
    - Base URL from configuration
    
    Note: Using function scope to avoid event loop binding issues.
    
    Yields:
        AsyncClient instance for making HTTP requests.
    """
    timeout = httpx.Timeout(
        connect=30.0,
        read=1800.0,  # 30 minutes for long-running agent operations (matches integration tests)
        write=60.0,
        pool=30.0
    )
    
    async with httpx.AsyncClient(
        base_url=e2e_config["api_base_url"],
        timeout=timeout,
        headers={"Content-Type": "application/json"}
    ) as client:
        logger.info(f"HTTP client created for {e2e_config['api_base_url']}")
        yield client
        logger.info("HTTP client closed")


@pytest_asyncio.fixture
async def single_request_client(e2e_config) -> httpx.AsyncClient:
    """
    Create an HTTP client for a single request.
    
    Use this for tests that need isolated HTTP sessions.
    
    Yields:
        AsyncClient instance for a single request.
    """
    async with httpx.AsyncClient(
        base_url=e2e_config["api_base_url"],
        timeout=httpx.Timeout(connect=30.0, read=120.0, write=60.0, pool=30.0)
    ) as client:
        yield client


# =============================================================================
# Evaluation Fixtures
# =============================================================================

@pytest.fixture(scope="class")
def evaluation_model_config(test_environment) -> Dict[str, str]:
    """
    Provide Azure OpenAI model configuration for evaluation.
    
    Returns:
        Dictionary containing model configuration for evaluators.
    """
    config = {
        "azure_endpoint": test_environment["openai_endpoint"],
        "azure_deployment": test_environment["openai_deployment"],
        "api_version": test_environment["openai_api_version"],
    }
    
    if not config["azure_endpoint"]:
        pytest.skip("AZURE_OPENAI_ENDPOINT not configured for evaluation")
    
    logger.info(f"Evaluation model: {config['azure_deployment']}")
    return config


@pytest.fixture(scope="class")
def evaluators(evaluation_model_config):
    """
    Initialize Azure AI Evaluation metrics.
    
    Returns:
        Dictionary containing evaluator instances for Relevance and Groundedness.
    """
    try:
        from azure.ai.evaluation import RelevanceEvaluator, GroundednessEvaluator
        
        logger.info("Initializing evaluation metrics...")
        
        evaluator_instances = {
            "relevance": RelevanceEvaluator(evaluation_model_config),
            "groundedness": GroundednessEvaluator(evaluation_model_config),
        }
        
        logger.info("Evaluation metrics initialized successfully")
        return evaluator_instances
        
    except ImportError:
        pytest.skip("azure-ai-evaluation package not installed")
    except Exception as e:
        logger.error(f"Failed to initialize evaluators: {e}")
        pytest.skip(f"Failed to initialize evaluators: {e}")


# =============================================================================
# Workflow State Fixtures
# =============================================================================

class WorkflowState:
    """
    Manages state across E2E workflow tests.
    
    This class tracks the progress and results of each step in the E2E workflow,
    allowing tests to share state and validate dependencies.
    """
    
    def __init__(self, app_id: str):
        self.app_id = app_id
        self.created_at = datetime.now(timezone.utc)
        self.steps_completed: List[str] = []
        self.step_results: Dict[str, Dict[str, Any]] = {}
        self.evaluation_scores: Dict[str, Dict[str, float]] = {}
        self.errors: List[Dict[str, Any]] = []
    
    def mark_step_completed(self, step_name: str, result: Dict[str, Any] = None):
        """Mark a workflow step as completed with optional result data."""
        self.steps_completed.append(step_name)
        if result:
            self.step_results[step_name] = result
        logger.info(f"Step completed: {step_name}")
    
    def record_evaluation(self, step_name: str, scores: Dict[str, float]):
        """Record evaluation scores for a step."""
        self.evaluation_scores[step_name] = scores
        logger.info(f"Evaluation for {step_name}: {scores}")
    
    def record_error(self, step_name: str, error: Exception):
        """Record an error that occurred during a step."""
        self.errors.append({
            "step": step_name,
            "error": str(error),
            "type": type(error).__name__,
            "timestamp": datetime.utcnow().isoformat()
        })
        logger.error(f"Error in {step_name}: {error}")
    
    def is_step_completed(self, step_name: str) -> bool:
        """Check if a specific step has been completed."""
        return step_name in self.steps_completed
    
    def get_summary(self) -> Dict[str, Any]:
        """Get a summary of the workflow execution."""
        return {
            "app_id": self.app_id,
            "created_at": self.created_at.isoformat(),
            "steps_completed": self.steps_completed,
            "total_steps": len(self.steps_completed),
            "evaluation_scores": self.evaluation_scores,
            "errors": self.errors,
            "success": len(self.errors) == 0
        }


@pytest.fixture(scope="class")
def workflow_state(e2e_config) -> WorkflowState:
    """
    Provide a workflow state manager for the E2E test class.
    
    Returns:
        WorkflowState instance for tracking test progress.
    """
    state = WorkflowState(app_id=e2e_config["app_id"])
    logger.info(f"Workflow state initialized for app_id: {state.app_id}")
    return state


# =============================================================================
# E2E Test Result Storage
# =============================================================================

@pytest.fixture(scope="session")
def e2e_reports_dir() -> Path:
    """
    Create and provide the E2E reports directory.
    
    Returns:
        Path to the E2E reports directory.
    """
    reports_dir = Path(__file__).parent / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"E2E reports directory: {reports_dir}")
    return reports_dir


@pytest.fixture(scope="class")
def test_report_file(e2e_reports_dir, e2e_config) -> Path:
    """
    Provide a file path for storing test results.
    
    Returns:
        Path to the test report file.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_file = e2e_reports_dir / f"e2e_report_{e2e_config['app_id']}_{timestamp}.json"
    logger.info(f"Test report file: {report_file}")
    return report_file


# =============================================================================
# API Health Check Fixture
# =============================================================================

@pytest_asyncio.fixture(scope="function")
async def verify_api_health(http_client):
    """
    Verify the API server is running and healthy before E2E tests.
    
    Retries up to 3 times before failing the test.
    
    Raises:
        AssertionError: If the API server is not reachable after 3 retries.
    """
    max_retries = 3
    retry_delay = 2  # seconds
    
    for attempt in range(1, max_retries + 1):
        try:
            # Try to reach the API root or health endpoint
            logger.info(f"API health check attempt {attempt}/{max_retries}...")
            response = await http_client.get("/")
            
            if response.status_code in [200, 404]:
                # 404 is acceptable if there's no root endpoint defined
                logger.info("API server is reachable")
                return  # Success - exit fixture
            else:
                logger.warning(f"API returned unexpected status: {response.status_code}")
                
        except httpx.ConnectError as e:
            logger.warning(
                f"Attempt {attempt}/{max_retries}: Cannot connect to API at {http_client.base_url}. "
                f"Connection error: {e}"
            )
            if attempt < max_retries:
                logger.info(f"Retrying in {retry_delay} seconds...")
                await asyncio.sleep(retry_delay)
            else:
                pytest.fail(
                    f"Cannot connect to API at {http_client.base_url} after {max_retries} attempts. "
                    f"Last error: {e}. "
                    "Ensure the API server is running with: uvicorn api_main:app --host 0.0.0.0 --port 8000"
                )
                
        except httpx.TimeoutException as e:
            logger.warning(
                f"Attempt {attempt}/{max_retries}: Timeout connecting to API at {http_client.base_url}. "
                f"Timeout error: {e}"
            )
            if attempt < max_retries:
                logger.info(f"Retrying in {retry_delay} seconds...")
                await asyncio.sleep(retry_delay)
            else:
                pytest.fail(
                    f"Timeout connecting to API at {http_client.base_url} after {max_retries} attempts. "
                    f"Last error: {e}"
                )
                
        except Exception as e:
            logger.warning(
                f"Attempt {attempt}/{max_retries}: API health check failed: {type(e).__name__}: {e}"
            )
            if attempt < max_retries:
                logger.info(f"Retrying in {retry_delay} seconds...")
                await asyncio.sleep(retry_delay)
            else:
                pytest.fail(
                    f"API health check failed after {max_retries} attempts: {type(e).__name__}: {e}"
                )


# =============================================================================
# Polling Helper Fixture
# =============================================================================

class OperationPoller:
    """
    Helper class for polling async operation status.
    
    Used for operations that return 202 Accepted and require
    polling to check completion status.
    """
    
    def __init__(self, http_client: httpx.AsyncClient, max_retries: int = 30, poll_interval: float = 10.0):
        self.http_client = http_client
        self.max_retries = max_retries
        self.poll_interval = poll_interval
        self.logger = logging.getLogger(f"{__name__}.OperationPoller")
    
    async def wait_for_completion(
        self,
        operation_id: str,
        status_endpoint: str = "/operations/status"
    ) -> Dict[str, Any]:
        """
        Poll for operation completion.
        
        Args:
            operation_id: The operation ID to poll
            status_endpoint: The endpoint to check status
            
        Returns:
            Final operation result dictionary
            
        Raises:
            TimeoutError: If operation doesn't complete within max retries
        """
        self.logger.info(f"Polling for operation {operation_id} completion...")
        
        for attempt in range(self.max_retries):
            try:
                response = await self.http_client.get(
                    status_endpoint,
                    params={"operation_id": operation_id}
                )
                
                if response.status_code == 200:
                    data = response.json()
                    status = data.get("status", "").lower()
                    
                    if status in ["completed", "success"]:
                        self.logger.info(f"Operation {operation_id} completed successfully")
                        return data
                    elif status in ["failed", "error"]:
                        self.logger.error(f"Operation {operation_id} failed: {data}")
                        raise RuntimeError(f"Operation failed: {data.get('error', 'Unknown error')}")
                    else:
                        self.logger.debug(f"Attempt {attempt + 1}/{self.max_retries}: status={status}")
                
                await asyncio.sleep(self.poll_interval)
                
            except httpx.RequestError as e:
                self.logger.warning(f"Request error during polling: {e}")
                await asyncio.sleep(self.poll_interval)
        
        raise TimeoutError(f"Operation {operation_id} did not complete within {self.max_retries * self.poll_interval} seconds")


@pytest.fixture(scope="function")
def operation_poller(http_client) -> OperationPoller:
    """
    Provide an operation poller for async operations.
    
    Returns:
        OperationPoller instance.
    """
    return OperationPoller(http_client)
