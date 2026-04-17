<!--
DOC_INTENT:
	surface: foundry
	page: API
	purpose: Describe the public API surface for interacting with the Foundry agents runtime (endpoints, auth expectations, and integration patterns).
	audience: Integrators, developers
	should_cover:
		- Primary HTTP endpoints and what they do
		- Request/response shapes at a high level (no secrets)
		- Auth headers/identity expectations
		- Error handling and retry guidance
	should_not_cover:
		- Full OpenAPI spec or exhaustive field-by-field reference unless it exists in codegen
	source_refs:
		- cockpit-docs/docs/api.md (reference style only)
		- foundry-agents/agents/api_main.py (if applicable)
-->


# Insights Agent API Endpoints

Comprehensive reference for all FastAPI routes defined in [api_main.py](../agents/api_main.py). All endpoints consume and produce `application/json`. Role-Based Access Control (RBAC) is enforced either via JSON request bodies or HTTP headers depending on the endpoint.

The following diagram depicts the API endpoints and the corresponding agents and plugins.

<img src="./media/insights-api.jpg" alt="Diagram of Insights API" width="100%">
---

## Table of Contents

1. [Conventions](#conventions)
2. [Health Check](#1-health-check)
3. [Application Setup & Core Workflow](#2-application-setup--core-workflow)
   - [Create Application ID](#21-post-createapplicationid)
   - [Index Documents](#22-post-indexdocuments)
   - [Run Analysis](#23-post-runanalysis)
   - [Generate Assessment Report](#24-post-generateassessmentreport)
   - [Generate Design](#25-post-generatedesign)
   - [Discover Kubernetes](#26-post-discoverkubernetes)
   - [Delete App Data](#27-post-deleteappdata)
4. [Architecture Security Analysis (Async)](#3-architecture-security-analysis-async)
5. [Code Analysis (Async)](#4-code-analysis-async)
6. [Operations Tracking & Management](#5-operations-tracking--management)
   - [List Operations Status](#51-get-operationsstatus)
   - [Operations Summary](#52-get-operationssummary)
   - [Get Specific Operation Status](#53-get-operationsoperation_idstatus)
   - [Get Operation Result](#54-get-operationsoperation_idresult)
   - [Cleanup Operations](#55-delete-operationscleanup)
7. [Data Models Reference](#6-data-models-reference)
8. [Error Handling](#7-error-handling)

---

## Endpoints Summary

| # | Endpoint | Method | Type | Description |
|---|----------|--------|------|-------------|
| 1 | `/health` | GET | Sync | Liveness/readiness probe for health monitoring |
| 2 | `/createApplicationId` | POST | Sync | Initialize app RBAC and template tables (container must already exist) |
| 3 | `/indexDocuments` | POST | Sync | Index documents to Azure AI Search for RAG |
| 4 | `/runAnalysis` | POST | Async (202) | Execute full analysis pipeline via Responder agent |
| 5 | `/generateAssessmentReport` | POST | Async (202) | Generate assessment report document |
| 6 | `/generateDesign` | POST | Async (202) | Generate Azure architecture design |
| 7 | `/discoverKubernetes` | POST | Async (202) | Discover Kubernetes resources for AKS migration |
| 8 | `/deleteAppData` | POST | Sync | Delete all app data (agents, threads, storage, index) |
| 9 | `/analyzeArchitecture` | POST | Async (202) | Security analysis of architecture diagrams |
| 10 | `/analyzeCode` | POST | Async (202) | Code analysis for repositories or blob storage |
| 11 | `/operations/status` | GET | Sync | List operations with filtering and pagination |
| 12 | `/operations/summary` | GET | Sync | Aggregate statistics and recent operations |
| 13 | `/operations/{id}/status` | GET | Sync | Detailed status of a specific operation |
| 14 | `/operations/{id}/result` | GET | Sync | Final results of completed code analysis |
| 15 | `/operations/cleanup` | DELETE | Sync | Delete stored operation records |

---

## Conventions

| Aspect | Details |
|--------|---------|
| **Base URL** | Service root, e.g., `http://localhost:8000` (local dev) or your Azure Container App URL |
| **Content-Type** | `application/json` for all requests and responses |
| **Authentication** | Provide at least one of `user_object_id` or `group_object_id` (Azure AD GUIDs) |
| **Header-based RBAC** | For GET/DELETE operations endpoints: `X-User-Object-Id` or `X-Group-Object-Id`, `X-Storage-Account` (required), `X-Resource-Group` (optional) |
| **Async Operations** | `analyzeArchitecture` and `analyzeCode` return `operation_id`; poll via operations endpoints |
| **Models** | Request/response schemas defined in [models.py](../agents/models.py) |

---

## 1. Health Check

### GET /health

**Purpose:** Liveness and readiness probe for Azure Container Apps health monitoring. Validates that required environment variables are configured.

**Source:** [api_main.py#L291](../agents/api_main.py#L291)

#### Request

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| *(none)* | — | — | — | No input parameters |

#### Response

**Success (200 OK):**

```json
{
  "status": "healthy",
  "message": "AI Assessment, Design and Planning (ADP) API is running",
  "version": "0.1.0",
  "build": "local",
  "commit": "abc12345",
  "deployed_at": "2025-12-12T10:00:00.000000",
  "timestamp": 1702382400.123,
  "tracing_enabled": true,
  "logging_level": "INFO",
  "trace_level": "ENABLED"
}
```

**Failure (503 Service Unavailable):**

```json
{
  "status": "unhealthy",
  "message": "Missing required environment variables: AZURE_EXISTING_AIPROJECT_ENDPOINT",
  "timestamp": 1702382400.123
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | `"healthy"` or `"unhealthy"` |
| `message` | string | Human-readable status description |
| `version` | string | Semantic version of the API |
| `build` | string | Build number or `"local"` |
| `commit` | string | First 8 characters of Git commit hash |
| `deployed_at` | string | ISO 8601 deployment timestamp |
| `timestamp` | float | Unix timestamp of health check |
| `tracing_enabled` | boolean | Whether OpenTelemetry tracing is active |
| `logging_level` | string | Current log level (DEBUG, INFO, WARNING, ERROR, CRITICAL) |
| `trace_level` | string | `"ENABLED"` or `"DISABLED"` |

---

## 2. Application Setup & Core Workflow

These endpoints follow the migration assessment lifecycle: create application → index documents → run analysis → generate report → generate design → complete assessment.

### 2.1 POST /createApplicationId

**Purpose:** Initialize a new application with proper RBAC permissions. Validates the per-app blob container (it must already exist), then clones template tables (if missing) and ensures the caller has table-level `Storage Table Data Contributor` on those tables.

**Source:** [api_main.py#L355](../agents/api_main.py#L355)

#### Request Body (CreateApplicationRequest)

| Field | Type | Required | Constraints | Description |
|-------|------|----------|-------------|-------------|
| `app_id` | string | ✅ | 3-63 chars, lowercase, alphanumeric + hyphens, no consecutive hyphens, cannot end with hyphen | Unique application identifier (becomes container name) |
| `storage_account_name` | string | ✅ | 3-24 chars, lowercase letters and numbers only | Azure Storage account name |
| `azure_region` | string | ✅ | min 1 char | Azure region (e.g., `"eastus"`, `"westus2"`) |
| `user_object_id` | string (UUID) | ⚠️ | GUID format | Azure AD user object ID (required if `group_object_id` not provided) |
| `group_object_id` | string (UUID) | ⚠️ | GUID format | Azure AD group object ID (required if `user_object_id` not provided) |
| `resource_group_name` | string | ❌ | — | Resource group name (auto-discovered if omitted) |

**Example Request:**

```json
{
  "app_id": "myapp-001",
  "storage_account_name": "mystorageaccount",
  "azure_region": "eastus",
  "user_object_id": "12345678-1234-1234-1234-123456789012",
  "resource_group_name": "my-resource-group"
}
```

#### Response (CreateApplicationResponse)

**Success (200 OK):**

```json
{
  "status": "success",
  "app_id": "myapp-001",
  "container": {
    "status": "exists_with_permissions",
    "container_name": "myapp-001",
    "storage_account": "mystorageaccount",
    "exists": true
  },
  "permissions": {
    "blob_permissions": {
      "status": "verified",
      "role": "Storage Blob Data Contributor",
      "scope": "container",
      "details": {}
    },
    "table_permissions": {
      "cloned_tables": {
        "status": "completed",
        "role": "Storage Table Data Contributor",
        "scope": "table",
        "tables": ["AppDetailsTemplatemyapp-001", "MsSqlDBTemplatemyapp-001", "OracleDBTemplatemyapp-001", "IntegrationDependencyTemplatemyapp-001", "InfrastructureDetailsmyapp-001"],
        "successful": 5,
        "failed": 0,
        "details": {}
      }
    }
  },
  "tables": {
    "status": "completed",
    "cloned_tables": ["AppDetailsTemplatemyapp-001", "MsSqlDBTemplatemyapp-001"]
  },
  "message": "Application 'myapp-001' setup completed with table-level RBAC. RBAC changes may take up to 10 minutes to propagate."
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | `"success"`, `"partial_success"`, or `"error"` |
| `app_id` | string | The application ID that was created |
| `container` | object | Container creation details including status and existence |
| `permissions` | object | RBAC assignment results for blob and table permissions |
| `tables` | object | Template table cloning results |
| `message` | string | Human-readable summary with RBAC propagation warning |

**Errors:**
- `400 Bad Request`: Validation errors (invalid app_id format, missing required fields)
- `500 Internal Server Error`: Orchestration or RBAC assignment failures

---

### 2.2 POST /indexDocuments

**Purpose:** Trigger document indexing for an application. Uploads documents from the application's blob container to Azure AI Search for retrieval-augmented generation (RAG).

**Source:** [api_main.py#L550](../agents/api_main.py#L550)

#### Request Body (ApplicationOperationRequest)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `app_id` | string | ✅ | Application ID to index documents for |
| `storage_account_name` | string | ✅ | Azure Storage account name |
| `user_object_id` | string (UUID) | ⚠️ | Azure AD user object ID |
| `group_object_id` | string (UUID) | ⚠️ | Azure AD group object ID |
| `resource_group_name` | string | ❌ | Resource group name |

**Example Request:**

```json
{
  "app_id": "myapp-001",
  "storage_account_name": "mystorageaccount",
  "user_object_id": "12345678-1234-1234-1234-123456789012"
}
```

#### Response (IndexDocumentsResponse)

**Success (200 OK):**

```json
{
  "status": "success",
  "app_id": "myapp-001",
  "indexing_result": {
    "result": "success",
    "documents_indexed": 150,
    "duration_seconds": 45,
    "index_name": "myapp-001-index"
  },
  "message": "Documents indexed successfully for application 'myapp-001'"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | `"success"`, `"in_progress"`, or `"failed"` |
| `app_id` | string | Application ID |
| `indexing_result` | object | Detailed indexing operation results |
| `message` | string | Human-readable status message |

**Errors:**
- `400 Bad Request`: Authentication/validation errors
- `500 Internal Server Error`: Indexing or orchestration failures

---

### 2.3 POST /runAnalysis

**Purpose:** Execute the full analysis pipeline (async operation). Creates a Responder agent and processes all QA tables, dependencies, and infrastructure data to generate insights and recommendations. Returns immediately with an operation_id for tracking progress.

**Source:** [api_main.py#L650](../agents/api_main.py#L650)

**Response Code:** `202 Accepted` (async operation)

#### Request Body (ApplicationOperationRequest)

Same as [indexDocuments](#22-post-indexdocuments).

#### Response (AnalysisResponse)

**Success (200 OK):**

```json
{
  "status": "success",
  "app_id": "myapp-001",
  "analysis_result": {
    "tables_processed": 5,
    "issues_found": 12,
    "recommendations": 8,
    "summary": "Analysis completed successfully"
  },
  "message": "Analysis completed successfully for application 'myapp-001'",
  "table_confidence_scores": {
    "AppDetailsmyapp-001": 0.85,
    "MsSqlDBmyapp-001": 0.78,
    "OracleDBmyapp-001": 0.92
  },
  "overall_average_confidence_score": 0.85
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Operation status |
| `app_id` | string | Application ID |
| `analysis_result` | object | Detailed analysis results including tables processed, issues, recommendations |
| `message` | string | Human-readable status message |
| `table_confidence_scores` | object | Optional: Confidence scores (0.0-1.0) per analyzed table |
| `overall_average_confidence_score` | float | Optional: Average confidence score across all tables |

---

### 2.4 POST /generateAssessmentReport

**Purpose:** Generate the assessment report after analysis is complete (async operation). Creates a formatted report document and stores it in blob storage. Returns immediately with an operation_id for tracking progress.

**Source:** [api_main.py#L737](../agents/api_main.py#L737)

**Response Code:** `202 Accepted` (async operation)

#### Request Body (ApplicationOperationRequest)

Same as [indexDocuments](#22-post-indexdocuments).

#### Response (AssessmentReportResponse)

**Success (200 OK):**

```json
{
  "status": "success",
  "app_id": "myapp-001",
  "operation_id": "op-12345678",
  "report": {
    "report_url": "https://mystorageaccount.blob.core.windows.net/myapp-001/assessment-report.pdf",
    "report_format": "PDF",
    "generated_at": "2025-12-12T10:30:00Z"
  },
  "message": "Assessment report generated successfully for application 'myapp-001'"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Operation status |
| `app_id` | string | Application ID |
| `operation_id` | string | Optional: Unique operation identifier |
| `report` | object | Report details including URL, format, and generation timestamp |
| `message` | string | Human-readable status message |

---

### 2.5 POST /generateDesign

**Purpose:** Generate architecture design output based on assessment results (async operation). Creates recommended Azure architecture and migration design documents. Returns immediately with an operation_id for tracking progress.

**Source:** [api_main.py#L824](../agents/api_main.py#L824)

**Response Code:** `202 Accepted` (async operation)

#### Request Body (ApplicationOperationRequest)

Same as [indexDocuments](#22-post-indexdocuments).

#### Response (DesignResponse)

**Success (200 OK):**

```json
{
  "status": "success",
  "app_id": "myapp-001",
  "design_result": {
    "design_url": "https://mystorageaccount.blob.core.windows.net/myapp-001/design.json",
    "generated_at": "2025-12-12T10:30:00Z",
    "thread_id": "thread_abc123"
  },
  "message": "Architecture design generated successfully for application 'myapp-001'"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Operation status |
| `app_id` | string | Application ID |
| `design_result` | object | Design generation details including URL and agent thread ID |
| `message` | string | Human-readable status message |

---

### 2.6 POST /discoverKubernetes

**Purpose:** Initialize Kubernetes Discovery Agent for cluster analysis (async operation). Creates or reuses an Azure AI Agent for discovering Kubernetes resources and configurations. Returns immediately with an operation_id for tracking progress.

**Source:** [api_main.py#L911](../agents/api_main.py#L911)

**Response Code:** `202 Accepted` (async operation)

#### Request Body (ApplicationOperationRequest)

Same as [indexDocuments](#22-post-indexdocuments).

#### Response (KubernetesDiscoveryResponse)

**Success (200 OK):**

```json
{
  "status": "success",
  "app_id": "my-aks-cluster",
  "agent_id": "asst_abc123xyz",
  "message": "Kubernetes discovery completed successfully for application 'my-aks-cluster'"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Operation status (`"success"` or `"error"`) |
| `app_id` | string | Application/Cluster ID |
| `agent_id` | string | Optional: Azure AI Agent ID for this discovery session |
| `message` | string | Human-readable status message |

---

### 2.7 POST /deleteAppData

**Purpose:** Delete all application data including agents, threads, storage container, and search index. This endpoint performs comprehensive cleanup of all resources associated with an application.

**Source:** [api_main.py#L996](../agents/api_main.py#L996)

#### Request Body (ApplicationOperationRequest)

Same as [indexDocuments](#22-post-indexdocuments).

#### Response (DeleteAppDataResponse)

**Success (200 OK):**

```json
{
  "status": "success",
  "app_id": "myapp-001",
  "deletion_result": {
    "agents_cleaned": 3,
    "threads_deleted": 5,
    "resources_released": true
  },
  "message": "All app data deleted successfully for application 'myapp-001'"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Operation status |
| `app_id` | string | Application ID |
| `deletion_result` | object | Details of deletion operations performed |
| `message` | string | Human-readable status message |

---

## 3. Architecture Security Analysis (Async)

### POST /analyzeArchitecture

**Purpose:** Initiate asynchronous architecture security analysis of a design document from blob storage. Uses dynamic mode to auto-discover all architecture diagrams and analyze them for security compliance.

**Source:** [api_main.py#L1072](../agents/api_main.py#L1072)

**Response Code:** `202 Accepted` (async operation)

#### Request Body (ArchitectureAnalysisRequest)

| Field | Type | Required | Constraints | Description |
|-------|------|----------|-------------|-------------|
| `app_id` | string | ✅ | 3-63 chars, Azure naming rules | Application ID for tracking |
| `design_doc_url` | string | ✅ | min 3 chars, blob path or full URL | Path to design document (e.g., `"design-docs/myapp/architecture.md"`) |
| `storage_account_name` | string | ✅ | 3-24 chars | Azure Storage account name |
| `user_object_id` | string (UUID) | ⚠️ | GUID format | Azure AD user object ID |
| `group_object_id` | string (UUID) | ⚠️ | GUID format | Azure AD group object ID |
| `resource_group_name` | string | ❌ | — | Resource group name |

**Example Request:**

```json
{
  "app_id": "myapp-001",
  "design_doc_url": "design-docs/myapp-001/architecture.md",
  "storage_account_name": "mystorageaccount",
  "user_object_id": "12345678-1234-1234-1234-123456789012"
}
```

#### Response (ArchitectureAnalysisResponse)

**Success (202 Accepted):**

```json
{
  "status": "accepted",
  "app_id": "myapp-001",
  "operation_id": "123e4567-e89b-12d3-a456-426614174000",
  "design_doc_url": "design-docs/myapp-001/architecture.md",
  "message": "Architecture security analysis started in background. Use operation_id '123e4567-e89b-12d3-a456-426614174000' to track progress via /operations/{operation_id}/status?app_id={app_id}&include_results=true"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | `"accepted"` indicates async operation started |
| `app_id` | string | Application ID |
| `operation_id` | string (UUID) | Unique identifier for tracking progress |
| `design_doc_url` | string | Design document being analyzed |
| `message` | string | Instructions for polling status |

#### Polling for Results

Use `GET /operations/{operation_id}/status?app_id={app_id}&include_results=true` to check progress:

- **202**: Still in progress
- **200**: Completed with results including `total_architectures`, `total_findings`, `consolidated_report_url`
- **500**: Failed

---

## 4. Code Analysis (Async)

### POST /analyzeCode

**Purpose:** Start asynchronous code analysis for a repository or blob storage. Supports multiple source types and performs comprehensive code review, security scanning, and best practices analysis.

**Source:** [api_main.py#L1926](../agents/api_main.py#L1926)

#### Supported Source Types

| Source | URL Pattern Example |
|--------|---------------------|
| GitHub | `https://github.com/owner/repo` |
| GitLab | `https://gitlab.com/owner/repo` |
| Azure DevOps | `https://dev.azure.com/org/project/_git/repo` |
| Bitbucket | `https://bitbucket.org/owner/repo` |
| Azure Blob | `https://account.blob.core.windows.net/container/code.zip` |

#### Request Body (CodeAnalysisRequest)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `app_id` | string | ✅ | Application ID for RBAC and storage |
| `repo_url` | string | ✅ | Repository URL or blob storage URL |
| `storage_account_name` | string | ✅ | Azure Storage account name |
| `user_object_id` | string (UUID) | ⚠️ | Azure AD user object ID |
| `group_object_id` | string (UUID) | ⚠️ | Azure AD group object ID |
| `resource_group_name` | string | ❌ | Resource group name |
| `perform_security_scan` | boolean | ❌ | Whether to scan for secrets (default: `true`) |
| `analysis_options` | object | ❌ | Additional analysis configuration |

**Example Request:**

```json
{
  "app_id": "myapp-001",
  "repo_url": "https://github.com/microsoft/azure-sdk-for-python",
  "storage_account_name": "mystorageaccount",
  "user_object_id": "12345678-1234-1234-1234-123456789012",
  "perform_security_scan": true,
  "analysis_options": {
    "include_code_metrics": true,
    "check_best_practices": true
  }
}
```

#### Response (CodeAnalysisResponse)

**Success (200 with status "accepted"):**

```json
{
  "status": "accepted",
  "operation_id": "op-98765432",
  "app_id": "myapp-001",
  "repo_url": "https://github.com/microsoft/azure-sdk-for-python",
  "source_type": "github",
  "message": "Code analysis started. Use operation_id to check status and retrieve results.",
  "status_endpoint": "/operations/op-98765432/status?app_id=myapp-001",
  "result_endpoint": "/operations/op-98765432/result?app_id=myapp-001"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | `"accepted"` indicates async operation started |
| `operation_id` | string | Unique identifier for tracking |
| `app_id` | string | Application ID |
| `repo_url` | string | Repository URL being analyzed |
| `source_type` | string | Auto-detected source type (`github`, `gitlab`, `azure_devops`, `bitbucket`, `blob`) |
| `message` | string | Status message |
| `status_endpoint` | string | Relative URL to check operation status |
| `result_endpoint` | string | Relative URL to retrieve results when complete |

---

## 5. Operations Tracking & Management

For deeper implementation details (operation model, storage requirements, RBAC, and monitoring guidance), see [OPERATION_TRACKING.md](./OPERATION_TRACKING.md).

### 5.1 GET /operations/status

**Purpose:** List operations for an application with optional filtering and pagination.

**Source:** [api_main.py#L1276](../agents/api_main.py#L1276)

#### Request

**Query Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `app_id` | string | ✅ | — | Application ID to filter by |
| `operation_type` | string | ❌ | — | Filter by type: `create_application`, `index_documents`, `run_analysis`, `generate_report`, `generate_design`, `kubernetes_discovery`, `assessment_complete`, `architecture_analysis`, `code_analysis` |
| `status` | string | ❌ | — | Filter by status: `pending`, `in_progress`, `completed`, `failed`, `cancelled` |
| `limit` | integer | ❌ | 1 | Maximum results (1-100) |
| `offset` | integer | ❌ | 0 | Results offset for pagination |

**Required Headers:**

| Header | Required | Description |
|--------|----------|-------------|
| `X-User-Object-Id` | ⚠️ | Azure AD user object ID (required if X-Group-Object-Id not provided) |
| `X-Group-Object-Id` | ⚠️ | Azure AD group object ID (required if X-User-Object-Id not provided) |
| `X-Storage-Account` | ✅ | Storage account name for RBAC validation |
| `X-Resource-Group` | ❌ | Resource group name |

#### Response (OperationStatusResponse)

**Success (200 OK):**

```json
{
  "operations": [
    {
      "operation_id": "op-12345678",
      "app_id": "myapp-001",
      "operation_type": "code_analysis",
      "status": "completed",
      "current_step": "Analysis complete",
      "progress_percentage": 100,
      "created_at": "2025-12-12T10:00:00Z",
      "updated_at": "2025-12-12T10:05:00Z",
      "completed_at": "2025-12-12T10:05:00Z"
    }
  ],
  "total_count": 1,
  "limit": 1,
  "offset": 0
}
```

---

### 5.2 GET /operations/summary

**Purpose:** Get aggregate statistics and recent operations for monitoring and dashboards.

**Source:** [api_main.py#L1397](../agents/api_main.py#L1397)

#### Request

**Query Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `app_id` | string | ✅ | — | Application ID (required for RBAC validation) |
| `days` | integer | ❌ | 7 | Number of days to include (1-365) |

**Headers:** Same as [GET /operations/status](#51-get-operationsstatus).

#### Response (OperationSummaryResponse)

**Success (200 OK):**

```json
{
  "summary": {
    "total_operations": 150,
    "by_status": {
      "completed": 120,
      "failed": 10,
      "in_progress": 15,
      "pending": 5
    },
    "by_type": {
      "code_analysis": 50,
      "architecture_analysis": 30,
      "run_analysis": 40,
      "generate_report": 30
    },
    "recent_operations": [...]
  }
}
```

---

### 5.3 GET /operations/{operation_id}/status

**Purpose:** Retrieve detailed status of a specific operation, optionally including results for completed operations.

**Source:** [api_main.py#L1473](../agents/api_main.py#L1473)

#### Request

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `operation_id` | string | Unique operation identifier |

**Query Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `app_id` | string | ✅ | — | Application ID for lookup |
| `include_results` | boolean | ❌ | false | Include operation results (for architecture analysis) |

**Headers:** Same as [GET /operations/status](#51-get-operationsstatus).

#### Response

**Success (200 OK) - Standard:**

Returns `OperationRecord` object with full operation details.

**Success (200 OK) - Architecture Analysis with `include_results=true`:**

```json
{
  "status": "completed",
  "operation_id": "123e4567-e89b-12d3-a456-426614174000",
  "operation_type": "architecture_analysis",
  "design_doc_url": "design-docs/myapp/architecture.md",
  "total_architectures": 5,
  "total_findings": 23,
  "consolidated_report_url": "https://mystorageaccount.blob.core.windows.net/reports/consolidated_report.md",
  "message": "Architecture security analysis completed successfully"
}
```

**In Progress (202 Accepted):**

```json
{
  "status": "in_progress",
  "operation_id": "123e4567-e89b-12d3-a456-426614174000",
  "operation_type": "architecture_analysis",
  "design_doc_url": "design-docs/myapp/architecture.md",
  "current_step": "Analyzing security compliance",
  "progress_percentage": 60,
  "message": "Architecture analysis still in progress: Analyzing security compliance (60% complete)"
}
```

**Errors:**
- `404 Not Found`: Operation not found
- `500 Internal Server Error`: Operation failed

---

### 5.4 GET /operations/{operation_id}/result

**Purpose:** Retrieve final results of a completed code analysis operation.

**Source:** [api_main.py#L1604](../agents/api_main.py#L1604)

#### Request

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `operation_id` | string | Unique operation identifier |

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `app_id` | string | ✅ | Application ID for lookup |

**Headers:** Same as [GET /operations/status](#51-get-operationsstatus).

#### Response (CodeAnalysisResultResponse)

**Success (200 OK):**

```json
{
  "status": "success",
  "operation_id": "op-98765432",
  "app_id": "myapp-001",
  "repo_url": "https://github.com/microsoft/azure-sdk-for-python",
  "content_type": "python",
  "config_folder": "kinfosec",
  "analysis_result": {
    "security_scan": {
      "performed": true,
      "secrets_found": 0,
      "files_excluded": 2
    },
    "files_processed": 150,
    "issues_found": 12,
    "recommendations": 8
  },
  "repo_metadata": {
    "language": "Python",
    "size_kb": 5200,
    "file_count": 350
  },
  "agents_info": {
    "agents_used": ["Python_Expert", "Security_Expert"],
    "orchestrator_used": true
  },
  "message": "Code analysis completed successfully",
  "report_url": "https://mystorageaccount.blob.core.windows.net/myapp-001/analysis-report.json"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | `"success"` or `"failed"` |
| `operation_id` | string | Operation identifier |
| `app_id` | string | Application ID |
| `repo_url` | string | Analyzed repository URL |
| `content_type` | string | Detected content type (`terraform`, `python`, `java`, `javascript`, etc.) |
| `config_folder` | string | Configuration folder used (`terrasec` or `kinfosec`) |
| `analysis_result` | object | Detailed analysis findings |
| `repo_metadata` | object | Repository metadata |
| `agents_info` | object | Information about agents that performed analysis |
| `message` | string | Status message |
| `report_url` | string | URL to full analysis report in blob storage |

**Errors:**
- `202 Accepted`: Operation still pending or in progress
- `404 Not Found`: Operation not found
- `410 Gone`: Operation was cancelled
- `500 Internal Server Error`: Operation failed or no results found

---

### 5.5 DELETE /operations/cleanup

**Purpose:** Delete stored operation records. Supports cleaning all operations for an app or pruning old operations.

**Source:** [api_main.py#L2104](../agents/api_main.py#L2104)

#### Request

**Query Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `confirm` | boolean | ✅ | — | Must be `true` to perform deletion |
| `app_id` | string | ✅ | — | Application ID (required for RBAC validation) |
| `all_for_app` | boolean | ❌ | false | Delete ALL operations for the app (ignores `days`) |
| `days` | integer | ❌ | 30 | Delete operations older than this (7-365) |

**Headers:** Same as [GET /operations/status](#51-get-operationsstatus).

**Examples:**

```bash
# Delete ALL operations for an app
DELETE /operations/cleanup?confirm=true&app_id=myapp-001&all_for_app=true

# Delete operations older than 60 days for an app
DELETE /operations/cleanup?confirm=true&app_id=myapp-001&days=60
```

#### Response

**Success (200 OK):**

```json
{
  "status": "success",
  "deleted_count": 25,
  "app_id": "myapp-001",
  "cleanup_type": "all_operations_for_app",
  "message": "Successfully deleted ALL 25 operations for app myapp-001"
}
```

---

## 6. Data Models Reference

### Common Request Models

| Model | Used By | Key Fields |
|-------|---------|------------|
| `CreateApplicationRequest` | `/createApplicationId` | `app_id`, `storage_account_name`, `azure_region`, `user_object_id`/`group_object_id` |
| `ApplicationOperationRequest` | Most POST endpoints | `app_id`, `storage_account_name`, `user_object_id`/`group_object_id` |
| `ArchitectureAnalysisRequest` | `/analyzeArchitecture` | Adds `design_doc_url` |
| `CodeAnalysisRequest` | `/analyzeCode` | Adds `repo_url`, `perform_security_scan`, `analysis_options` |

### Common Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Operation status: `"success"`, `"error"`, `"accepted"`, `"in_progress"`, `"completed"`, `"failed"` |
| `app_id` | string | Application identifier |
| `message` | string | Human-readable description |
| `operation_id` | string | Unique operation identifier (async operations) |

### Operation Types

| Value | Description |
|-------|-------------|
| `create_application` | Application creation with RBAC |
| `index_documents` | Document indexing |
| `run_analysis` | Analysis pipeline |
| `generate_report` | Report generation |
| `generate_design` | Design generation |
| `kubernetes_discovery` | Kubernetes cluster discovery |
| `delete_app_data` | Application data deletion and cleanup |
| `architecture_analysis` | Architecture security analysis |
| `code_analysis` | Code repository analysis |

### Operation Statuses

| Value | Description |
|-------|-------------|
| `pending` | Queued, not yet started |
| `in_progress` | Currently executing |
| `completed` | Successfully finished |
| `failed` | Failed with error |
| `cancelled` | Cancelled by user |

---

## 7. Error Handling

All endpoints follow consistent error response format:

```json
{
  "detail": "Error message describing what went wrong"
}
```

### HTTP Status Codes

| Code | Meaning | Common Causes |
|------|---------|---------------|
| `200` | Success | Operation completed successfully |
| `202` | Accepted | Async operation started (poll for results) |
| `400` | Bad Request | Validation errors, missing required fields, invalid formats |
| `404` | Not Found | Resource (operation, app) does not exist |
| `410` | Gone | Operation was cancelled |
| `500` | Internal Server Error | Orchestration failures, Azure service errors |
| `503` | Service Unavailable | Missing environment variables, service unhealthy |

### Validation Rules

| Field | Rules |
|-------|-------|
| `app_id` | 3-63 chars, lowercase, alphanumeric + hyphens, no `--`, cannot end with `-` |
| `storage_account_name` | 3-24 chars, lowercase letters and numbers only |
| `user_object_id` / `group_object_id` | Valid UUID format, at least one required |
| `days` (cleanup) | 7-365 |
| `limit` (pagination) | 1-100 |