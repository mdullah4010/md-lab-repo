# Integration Tests

This folder contains integration tests that validate individual API endpoints with real Azure services. Each test file focuses on a specific endpoint and verifies its behavior against live Azure resources.

## 📋 Table of Contents

- [Overview](#overview)
- [Test Files and Execution Order](#test-files-and-execution-order)
- [Test Details](#test-details)
- [Prerequisites](#prerequisites)
- [Running the Tests](#running-the-tests)
- [Folder Structure](#folder-structure)
- [Async Operations](#async-operations)
- [Troubleshooting](#troubleshooting)
- [Related Tests](#related-tests)

---

## Overview

Integration tests differ from E2E tests in that they:
- Test **individual endpoints** in isolation
- Validate **request/response models** and error handling
- Use **real Azure connections** (no mocking)
- Can be run **independently** or in sequence

## Test Files and Execution Order

For a complete validation of the migration workflow, run the tests in the following order:

| Order | Test File | Endpoint | Description |
|-------|-----------|----------|-------------|
| 1 | `test_create_application_id.py` | `POST /createApplicationId` | Creates application container and metadata tables |
| 2 | `test_blob_storage_integration.py` | Azure Blob Storage | Validates blob storage connectivity and structure |
| 3 | `test_index_documents.py` | `POST /indexDocuments` | Indexes documents into Azure AI Search |
| 4 | `test_azure_search_integration.py` | Azure AI Search | Validates search index and document retrieval |
| 5 | `test_analyze_code.py` | `POST /analyzeCode` | Analyzes source code for migration assessment |
| 6 | `test_discover_kubernetes.py` | `POST /discoverKubernetes` | Discovers Kubernetes deployment configuration |
| 7 | `test_run_analysis.py` | `POST /runAnalysis` | Runs Responder Agent to populate template tables |
| 8 | `test_generate_assessment_report.py` | `POST /generateAssessmentReport` | Generates Migration Assessment Report (ASR) |
| 9 | `test_generate_design.py` | `POST /generateDesign` | Generates Azure migration design document |
| 10 | `test_analyze_architecture.py` | `POST /analyzeArchitecture` | Analyzes application architecture |
| 11 | `test_delete_app_data.py` | `POST /deleteAppData` | Cleans up application data (optional) |

## Test Details

### 1. test_create_application_id.py

Tests the Create Application ID endpoint that initializes the application workspace.

**Tests:**
- `test_create_application_id_success` - Validates successful container and table creation
- `test_create_application_id_invalid_format` - Tests validation of app_id format
- `test_create_application_id_missing_fields` - Tests handling of missing required fields

### 2. test_blob_storage_integration.py

Tests Azure Blob Storage connectivity and directory structure.

**Tests:**
- `test_blob_storage_connectivity` - Validates connection to Azure Storage
- `test_analysis_directory_structure` - Verifies expected directory hierarchy exists

### 3. test_index_documents.py

Tests document indexing into Azure AI Search.

**Tests:**
- `test_index_documents_success` - Validates indexing with folder prefixes
- `test_index_documents_empty_container` - Tests handling of empty containers
- `test_index_documents_invalid_app_id` - Tests handling of non-existent apps

### 4. test_azure_search_integration.py

Tests Azure AI Search index operations.

**Tests:**
- `test_search_index_exists` - Validates search index accessibility
- `test_search_index_has_documents` - Tests document count retrieval
- `test_search_retrieves_relevant_content` - Tests search query relevance

### 5. test_analyze_code.py

Tests the Code Analyzer Agent endpoint.

**Tests:**
- `test_analyze_code_from_github_repo` - Validates code analysis from GitHub
- `test_analyze_code_from_blob_url` - Tests analysis from blob storage
- `test_analyze_code_no_code_available` - Tests handling when no code exists

### 6. test_discover_kubernetes.py

Tests the Kubernetes Discovery Agent endpoint.

**Tests:**
- `test_discover_kubernetes_success` - Validates K8s manifest discovery
- `test_discover_kubernetes_no_manifests` - Tests handling when no manifests exist

### 7. test_run_analysis.py

Tests the Responder Agent endpoint.

**Tests:**
- `test_run_analysis_success` - Validates analysis with confidence score verification
- `test_run_analysis_missing_data` - Tests handling of missing input data

### 8. test_generate_assessment_report.py

Tests the ASR Agent endpoint.

**Tests:**
- `test_generate_assessment_report_success` - Validates report generation
- `test_generate_assessment_report_incorrect_parameters` - Tests parameter validation

### 9. test_generate_design.py

Tests the Design Agent endpoint.

**Tests:**
- `test_generate_design_success` - Validates design document generation
- `test_generate_design_with_incorrect_user_context` - Tests user identity validation
- `test_generate_design_no_assessment` - Tests handling when ASR is missing

### 10. test_analyze_architecture.py

Tests the Architecture Analyzer Agent endpoint.

**Tests:**
- `test_analyze_architecture_success` - Validates architecture analysis
- `test_analyze_architecture_incorrect_design_doc_url` - Tests URL validation
- `test_analyze_architecture_no_data` - Tests handling when design is missing

### 11. test_delete_app_data.py

Tests the Delete App Data endpoint for cleanup.

**Tests:**
- `test_delete_app_data_success` - Validates data deletion
- `test_delete_app_data_nonexistent_app` - Tests handling of non-existent apps
- `test_delete_app_data_invalid_app_id` - Tests app_id format validation

⚠️ **CAUTION**: This test performs actual deletion. Use test-specific app IDs only.

## Prerequisites

1. **Python 3.10+** with required packages:
   ```bash
   pip install pytest pytest-asyncio httpx azure-storage-blob azure-data-tables azure-search-documents azure-identity
   ```

2. **Azure CLI** logged in:
   ```bash
   az login
   ```

3. **API Server** running:
   ```bash
   cd foundry-agents/agents
   uvicorn api_main:app --host 0.0.0.0 --port 8000
   ```

4. **Configuration** in `.env.test`:
   ```env
   API_BASE_URL=http://localhost:8000
   AZURE_STORAGE_ACCOUNT_NAME=your_storage_account
   TEST_APP_ID=INTTEST001
   TEST_USER_OBJECT_ID=your-user-object-id
   AZURE_SEARCH_ENDPOINT=https://your-search.search.windows.net
   ```

## Running the Tests

### Run All Integration Tests (Recommended Order)

```bash
cd foundry-agents

# Run in order
pytest tests/integration/test_create_application_id.py -v
pytest tests/integration/test_blob_storage_integration.py -v
pytest tests/integration/test_index_documents.py -v
pytest tests/integration/test_azure_search_integration.py -v
pytest tests/integration/test_analyze_code.py -v
pytest tests/integration/test_discover_kubernetes.py -v
pytest tests/integration/test_run_analysis.py -v
pytest tests/integration/test_generate_assessment_report.py -v
pytest tests/integration/test_generate_design.py -v
pytest tests/integration/test_analyze_architecture.py -v
pytest tests/integration/test_delete_app_data.py -v
```

### Run All Integration Tests at Once

```bash
pytest tests/integration/ -v --order-scope=module
```

### Run a Single Test File

```bash
pytest tests/integration/test_create_application_id.py -v -s
```

### Run a Specific Test

```bash
pytest tests/integration/test_create_application_id.py::TestCreateApplicationIdAPI::test_create_application_id_success -v
```

### Run with Detailed Logging

```bash
pytest tests/integration/test_run_analysis.py -v -s --log-cli-level=INFO
```

## Folder Structure

```
tests/integration/
├── README.md                           # This file
├── conftest.py                         # Integration test fixtures
├── test_helpers.py                     # Shared helper functions
├── __init__.py
├── test_create_application_id.py       # 1. Create application
├── test_blob_storage_integration.py    # 2. Blob storage validation
├── test_index_documents.py             # 3. Document indexing
├── test_azure_search_integration.py    # 4. Search validation
├── test_analyze_code.py                # 5. Code Analyzer Agent
├── test_discover_kubernetes.py         # 6. K8s Discovery Agent
├── test_run_analysis.py                # 7. Responder Agent
├── test_generate_assessment_report.py  # 8. ASR Agent
├── test_generate_design.py             # 9. Design Agent
├── test_analyze_architecture.py        # 10. Architecture Analyzer
└── test_delete_app_data.py             # 11. Cleanup (optional)
```

## Async Operations

Several endpoints return async operations that require polling:

- `POST /analyzeCode` → Returns `operation_id`, poll `status_endpoint`
- `POST /discoverKubernetes` → Returns `operation_id`, poll `status_endpoint`
- `POST /runAnalysis` → Returns `operation_id`, poll `status_endpoint`
- `POST /generateDesign` → Returns `operation_id`, poll `status_endpoint`

Use the `poll_operation_until_complete()` helper from `test_helpers.py`:

```python
from test_helpers import poll_operation_until_complete

result = await poll_operation_until_complete(
    http_client=client,
    integration_config=config,
    operation_id=data["operation_id"],
    status_endpoint=data["status_endpoint"],
    result_endpoint=data["result_endpoint"]
)
```

## Troubleshooting

### Common Issues

1. **API Connection Failed**
   - Ensure the API server is running on the configured port
   - Check `API_BASE_URL` in `.env.test`

2. **Authentication Failed**
   - Run `az login` to refresh credentials
   - Verify RBAC roles: Storage Blob Data Contributor, Storage Table Data Contributor

3. **Search Index Not Found**
   - Run document indexing first (`test_index_documents.py`)
   - Verify `AZURE_SEARCH_ENDPOINT` is correct

4. **Operation Timeout**
   - Agent operations can take several minutes
   - Default timeout is 30 minutes for long-running operations

## Related Tests

- **E2E Tests**: `tests/e2e/` - Full workflow tests
- **Unit Tests**: `tests/unit/` - Component-level tests
- **Evaluation Tests**: `tests/evaluation/` - Response quality metrics
