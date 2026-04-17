# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

"""
Shared pytest fixtures for the insights-agent test suite.

This module provides common fixtures used across all test types:
- Unit tests
- Integration tests
- End-to-end (E2E) tests
- Evaluation tests

All tests use real Azure connections - no mocking is used.
"""

import os
import sys
import logging
import subprocess
import pytest
import asyncio
from pathlib import Path
from typing import Generator, Dict, Any, Optional
from datetime import datetime
from dotenv import load_dotenv

# Add project root to Python path for imports
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "agents"))

# Load test environment from .env.test file
env_test_path = PROJECT_ROOT / ".env.test"
if env_test_path.exists():
    load_dotenv(env_test_path, override=True)
    logging.info(f"Loaded test configuration from {env_test_path}")
else:
    logging.warning(f".env.test file not found at {env_test_path}, using environment variables")

# Configure test logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("test-run.log", mode="a", encoding="utf-8")
    ]
)

# Suppress verbose Azure SDK logging during tests
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure.identity").setLevel(logging.WARNING)
logging.getLogger("azure.ai.projects").setLevel(logging.WARNING)
logging.getLogger("azure.ai.agents").setLevel(logging.WARNING)
logging.getLogger("azure.storage").setLevel(logging.WARNING)
logging.getLogger("azure.data.tables").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


# =============================================================================
# Pytest Configuration
# =============================================================================

def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line("markers", "slow: marks tests as slow running")
    config.addinivalue_line("markers", "e2e: marks tests as end-to-end tests")
    config.addinivalue_line("markers", "integration: marks tests as integration tests")
    config.addinivalue_line("markers", "evaluation: marks tests as evaluation tests")
    config.addinivalue_line("markers", "unit: marks tests as unit tests")


def pytest_collection_modifyitems(config, items):
    """Modify test collection to add markers based on path."""
    for item in items:
        # Auto-add markers based on test file location
        if "/e2e/" in str(item.fspath):
            item.add_marker(pytest.mark.e2e)
        elif "/integration/" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
        elif "/evaluation/" in str(item.fspath):
            item.add_marker(pytest.mark.evaluation)
        elif "/unit/" in str(item.fspath):
            item.add_marker(pytest.mark.unit)


# =============================================================================
# Environment Configuration Fixtures
# =============================================================================

@pytest.fixture(scope="session")
def test_environment() -> Dict[str, Optional[str]]:
    """
    Load and validate test environment configuration.
    
    Returns:
        Dictionary containing all environment configuration values.
        
    Raises:
        pytest.skip: If required environment variables are missing.
    """
    env_config = {
        # Azure Storage Configuration
        "storage_account_name": os.environ.get("AZURE_STORAGE_ACCOUNT_NAME"),
        "storage_account_url": os.environ.get(
            "AZURE_STORAGE_ACCOUNT_URL",
            f"https://{os.environ.get('AZURE_STORAGE_ACCOUNT_NAME', '')}.blob.core.windows.net"
        ),
        "tables_account_url": os.environ.get(
            "AZURE_TABLES_ACCOUNT_URL",
            f"https://{os.environ.get('AZURE_STORAGE_ACCOUNT_NAME', '')}.table.core.windows.net"
        ),
        
        # Azure AI Foundry Configuration
        "ai_project_connection_string": os.environ.get("AZURE_EXISTING_AIPROJECT_ENDPOINT"),
        "openai_endpoint": os.environ.get("AZURE_OPENAI_ENDPOINT"),
        "openai_deployment": os.environ.get("AZURE_AI_AGENT_DEPLOYMENT_NAME", "gpt-4.1"),
        "openai_api_version": os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-15-preview"),
        
        # Foundry Portal Tracking
        "enable_foundry_tracking": os.environ.get("ENABLE_FOUNDRY_TRACKING", "false"),
        
        # Azure AI Search Configuration
        "search_endpoint": os.environ.get("AZURE_SEARCH_ENDPOINT"),
        "search_key": os.environ.get("AZURE_SEARCH_KEY"),
        
        # Test Configuration
        "test_app_id": os.environ.get("TEST_APP_ID", f"TEST{datetime.now().strftime('%Y%m%d%H%M%S')}"),
        "test_app_container": os.environ.get("TEST_APP_ID", f"TEST{datetime.now().strftime('%Y%m%d%H%M%S')}"),
        "resource_group_name": os.environ.get("RESOURCE_GROUP"),
        "azure_region": os.environ.get("AZURE_REGION", "eastus2"),
        "test_user_object_id": os.environ.get("TEST_USER_OBJECT_ID"),
        "test_group_object_id": os.environ.get("TEST_GROUP_OBJECT_ID"),
        
        # API Configuration
        "api_base_url": os.environ.get("API_BASE_URL", "http://localhost:8000"),
        
        # Evaluation Thresholds
        "relevance_threshold": float(os.environ.get("RELEVANCE_THRESHOLD", "4.0")),
        "groundedness_threshold": float(os.environ.get("GROUNDEDNESS_THRESHOLD", "4.0")),
        "confidence_threshold": float(os.environ.get("CONFIDENCE_THRESHOLD", "0.7")),
        
        # Timeouts
        "test_timeout_seconds": int(os.environ.get("TEST_TIMEOUT_SECONDS", "120")),
    }
    
    logger.info("Test environment configuration loaded")
    logger.debug(f"Storage Account: {env_config['storage_account_name']}")
    logger.debug(f"Test App ID: {env_config['test_app_id']}")
    logger.debug(f"API Base URL: {env_config['api_base_url']}")
    
    return env_config


@pytest.fixture(scope="session")
def require_azure_credentials(test_environment):
    """
    Ensure Azure credentials are available for tests requiring Azure connections.
    
    Raises:
        pytest.skip: If Azure credentials are not configured.
    """
    required_vars = ["storage_account_name"]
    missing = [var for var in required_vars if not test_environment.get(var)]
    
    if missing:
        pytest.skip(f"Missing required Azure configuration: {', '.join(missing)}. Run 'az login' and set environment variables.")


# =============================================================================
# Azure Credential Fixtures
# =============================================================================

@pytest.fixture(scope="session")
def is_ci_environment() -> bool:
    """Check if running in CI/CD environment."""
    return bool(os.getenv('CI') or os.getenv('GITHUB_ACTIONS') or os.getenv('TF_BUILD'))


def refresh_azure_cli_token():
    """Refresh Azure CLI token to prevent expiration during long tests."""
    logger = logging.getLogger(__name__)
    try:
        # Get current account to verify authentication
        result = subprocess.run(
            ["az", "account", "show"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30
        )
        logger.info("✅ Azure CLI token refreshed successfully")
        return True
    except subprocess.TimeoutExpired:
        logger.warning("⚠️  Azure CLI token refresh timed out after 30 seconds")
        return False
    except subprocess.CalledProcessError as e:
        logger.warning(f"⚠️  Failed to refresh Azure CLI token: {e.stderr}")
        return False
    except FileNotFoundError:
        logger.warning("⚠️  Azure CLI (az) not found in PATH")
        return False


@pytest.fixture(scope="session")
def azure_credential(is_ci_environment):
    """
    Create Azure DefaultAzureCredential for authentication.
    
    In CI/CD environments (GitHub Actions), this will use Azure CLI credentials
    from the azure/login action. Locally, it tries multiple authentication methods.
    
    Returns:
        DefaultAzureCredential instance for synchronous operations.
    """
    from azure.identity import DefaultAzureCredential, AzureCliCredential
    
    logger.info("Creating Azure DefaultAzureCredential")
    
    if is_ci_environment:
        logger.info("Running in CI/CD environment - prioritizing Azure CLI authentication")
        # In CI/CD, the azure/login action sets up Azure CLI credentials
        # Refresh token before creating credential to ensure it's valid
        refresh_azure_cli_token()
        
        # Try Azure CLI first, then fall back to other methods
        try:
            credential = AzureCliCredential()
            # Test the credential by getting a token
            credential.get_token("https://storage.azure.com/.default")
            logger.info("✅ Using Azure CLI credentials from CI/CD login")
            return credential
        except Exception as e:
            logger.warning(f"Azure CLI credential failed: {e}, falling back to DefaultAzureCredential")
    
    # For local development or if Azure CLI fails in CI/CD
    credential = DefaultAzureCredential(
        exclude_shared_token_cache_credential=False,
        additionally_allowed_tenants=['*']  # Allow multi-tenant access if needed
    )
    return credential


@pytest.fixture(scope="session")
def async_azure_credential():
    """
    Create async Azure DefaultAzureCredential for authentication.
    
    Returns:
        Async DefaultAzureCredential instance for asynchronous operations.
    """
    from azure.identity.aio import DefaultAzureCredential
    
    logger.info("Creating async Azure DefaultAzureCredential")
    credential = DefaultAzureCredential(exclude_shared_token_cache_credential=True)
    return credential


@pytest.fixture(scope="function")
def refresh_token_before_test(is_ci_environment):
    """Refresh Azure CLI token before each test in CI/CD to prevent expiration."""
    if is_ci_environment:
        refresh_azure_cli_token()
    yield


# =============================================================================
# Azure Storage Fixtures
# =============================================================================

@pytest.fixture(scope="session")
def blob_service_client(test_environment, azure_credential):
    """
    Create BlobServiceClient for Azure Blob Storage operations.
    
    Returns:
        BlobServiceClient instance connected to the test storage account.
    """
    from azure.storage.blob import BlobServiceClient
    
    storage_account = test_environment["storage_account_name"]
    if not storage_account:
        pytest.skip("AZURE_STORAGE_ACCOUNT_NAME not configured")
    
    account_url = f"https://{storage_account}.blob.core.windows.net"
    logger.info(f"Creating BlobServiceClient for {account_url}")
    
    return BlobServiceClient(account_url=account_url, credential=azure_credential)


@pytest.fixture(scope="session")
def table_service_client(test_environment, azure_credential):
    """
    Create TableServiceClient for Azure Table Storage operations.
    
    Returns:
        TableServiceClient instance connected to the test storage account.
    """
    from azure.data.tables import TableServiceClient
    
    storage_account = test_environment["storage_account_name"]
    if not storage_account:
        pytest.skip("AZURE_STORAGE_ACCOUNT_NAME not configured")
    
    endpoint = f"https://{storage_account}.table.core.windows.net"
    logger.info(f"Creating TableServiceClient for {endpoint}")
    
    return TableServiceClient(endpoint=endpoint, credential=azure_credential)


# =============================================================================
# Test Data Fixtures
# =============================================================================

@pytest.fixture
def sample_app_data() -> Dict[str, Any]:
    """
    Provide sample application data for testing.
    
    Returns:
        Dictionary containing sample application configuration.
    """
    return {
        "app_id": "TEST12345",
        "app_name": "Sample Test Application",
        "description": "A sample .NET application for migration testing",
        "technology_stack": {
            "language": ".NET Framework 4.8",
            "database": "SQL Server 2019",
            "web_server": "IIS 10.0"
        },
        "infrastructure": {
            "environment": "on-premises",
            "servers": 3,
            "database_size_gb": 50
        }
    }


@pytest.fixture
def sample_kubernetes_config() -> Dict[str, Any]:
    """
    Provide sample Kubernetes configuration for testing.
    
    Returns:
        Dictionary containing sample K8s configuration.
    """
    return {
        "cluster_name": "test-aks-cluster",
        "namespace": "default",
        "deployments": [
            {
                "name": "web-app",
                "replicas": 3,
                "containers": [
                    {"name": "nginx", "image": "nginx:latest", "ports": [80]}
                ]
            }
        ],
        "services": [
            {"name": "web-app-svc", "type": "LoadBalancer", "port": 80}
        ]
    }


# =============================================================================
# Template Table Fixtures
# =============================================================================

@pytest.fixture(scope="session")
def required_template_tables() -> list:
    """
    List of required template tables that must exist in Azure Table Storage.
    
    Returns:
        List of table names required for the application.
    """
    return [
        "AppDetailsTemplate",
        "IntegrationDependencyTemplate",
        "MsSqlDBTemplate",
        "OracleDBTemplate",
        "InfrastructureDetails",
        "K8Stemplate"
    ]


@pytest.fixture(scope="session")
def required_virtual_directories() -> list:
    """
    List of required virtual directories in the application blob container.
    
    Note: The container name IS the app_id. These are relative paths within
    that container.
    
    Each endpoint reads from an input directory and writes to an output directory:
    - /generateDesign         → design/input, design/output
    - /generateAssessmentReport → asr/input, asr/output
    - /generateAppPlan        → app-planning/input, app-planning/output
    - /analyzeArchitecture    → architecture-analyzer/input, architecture-analyzer/output
    - /analyzeCode            → code-analyzer/input, code-analyzer/output
    - /discoverKubernetes     → kubernetes-discovery/input, kubernetes-discovery/output
    - /runAnalysis            → responder/input, responder/output
    
    Returns:
        List of virtual directory paths (14 total: 7 endpoints × 2 dirs each).
    """
    return [
        # /generateDesign endpoint
        "design/input",
        "design/output",
        # /generateAssessmentReport endpoint
        "asr/input",
        "asr/output",
        # /generateAppPlan endpoint
        "app-planning/input",
        "app-planning/output",
        # /analyzeArchitecture endpoint
        "architecture-analyzer/input",
        "architecture-analyzer/output",
        # /analyzeCode endpoint
        "code-analyzer/input",
        "code-analyzer/output",
        # /discoverKubernetes endpoint
        "kubernetes-discovery/input",
        "kubernetes-discovery/output",
        # /runAnalysis endpoint
        "responder/input",
        "responder/output",
    ]


# =============================================================================
# Test Reporting Fixtures
# =============================================================================

@pytest.fixture(scope="session", autouse=True)
def test_session_info(request):
    """
    Log test session information at the start and end of the test run.
    """
    start_time = datetime.now()
    logger.info("=" * 60)
    logger.info("TEST SESSION STARTED")
    logger.info(f"Start Time: {start_time.isoformat()}")
    logger.info(f"Python Version: {sys.version}")
    logger.info("=" * 60)
    
    yield
    
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    logger.info("=" * 60)
    logger.info("TEST SESSION COMPLETED")
    logger.info(f"End Time: {end_time.isoformat()}")
    logger.info(f"Duration: {duration:.2f} seconds")
    logger.info("=" * 60)


@pytest.fixture
def test_run_logger(request):
    """
    Create a logger specific to the current test.
    
    Returns:
        Logger instance configured for the current test.
    """
    test_logger = logging.getLogger(f"test.{request.node.name}")
    test_logger.info(f"Starting test: {request.node.name}")
    
    yield test_logger
    
    test_logger.info(f"Completed test: {request.node.name}")
