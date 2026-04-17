# Automated Testing Guide for AI Foundry Agents

This guide provides an overview of the testing infrastructure for the Insights Agent and links to detailed documentation for each test category.

## Table of Contents

- [Overview](#overview)
- [Test Categories](#test-categories)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Test Directory Structure](#test-directory-structure)
- [Running Tests](#running-tests)
- [CI/CD Integration](#cicd-integration)
- [Troubleshooting](#troubleshooting)

---

## Overview

The `insights-agent` project contains multiple AI Foundry agents that require comprehensive testing:

| Agent | Purpose | Location |
|-------|---------|----------|
| `orchestrator_agent` | Coordinates multi-agent workflows | `agents/orchestrator_agent.py` |
| `asr_agent` | Migration assessment report generation | `agents/asr_agent.py` |
| `design_agent` | Azure migration design generation | `agents/design_agent.py` |
| `code_analyzer_agent` | Source code analysis | `agents/code_analyzer_agent.py` |
| `kubernetes_discovery_agent` | K8s infrastructure discovery | `agents/kubernetes_discovery_agent.py` |
| `architecture_analyzer_agent` | Architecture analysis | `agents/architecture_analyzer_agent/` |
| `responder_agent` | Response formatting | `agents/responder_agent.py` |

---

## Test Categories

We use a **multi-layered testing approach**:

```
┌─────────────────────────────────────────────────────────────┐
│                   End-to-End Tests                          │
│   (Full workflow: Infrastructure → Agents → Evaluation)     │
├─────────────────────────────────────────────────────────────┤
│                    Evaluation Tests                         │
│         (LLM response quality, relevance, groundedness)     │
├─────────────────────────────────────────────────────────────┤
│                   Integration Tests                         │
│        (Agent-to-service interactions, Azure APIs)          │
├─────────────────────────────────────────────────────────────┤
│                      Unit Tests                             │
│    (Functions, utilities, prompts, models, helpers)         │
└─────────────────────────────────────────────────────────────┘
```

| Test Type | Directory | Documentation | Purpose |
|-----------|-----------|---------------|---------|
| **Unit Tests** | `tests/unit/` | [Unit Testing Guide](unit/README.md) | Isolated component testing (no external dependencies) |
| **Integration Tests** | `tests/integration/` | [Integration Test Guide](integration/README.md) | Individual endpoint testing with real Azure services |
| **Evaluation Tests** | `tests/evaluation/` | [Evaluation Framework Guide](evaluation/README.md) | LLM response quality and safety metrics |
| **End-to-End Tests** | `tests/e2e/` | [E2E Test Guide](e2e/README.md) | Complete workflow validation |

> **📖 See the linked README files in each sub-folder for detailed documentation on each test type.**

---

## Prerequisites

### System Requirements

| Requirement | Version | Purpose |
|-------------|---------|---------|
| Python | 3.10+ | Runtime environment for tests |
| pip | Latest | Package management |
| Git | Latest | Version control (optional) |

### Step 1: Install Python

**Windows:**

1. Download Python 3.10+ from [python.org](https://www.python.org/downloads/)
2. Run the installer and **check "Add Python to PATH"**
3. Verify installation:
   ```powershell
   python --version
   ```

**macOS:**

```bash
# Using Homebrew
brew install python@3.11

# Verify installation
python3 --version
```

**Linux (Ubuntu/Debian):**

```bash
sudo apt update
sudo apt install python3.11 python3.11-venv python3-pip

# Verify installation
python3 --version
```

### Step 2: Create and Activate Virtual Environment

Navigate to the `foundry-agents` directory and create a virtual environment:

**Windows (PowerShell):**

```powershell
cd foundry-agents
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**macOS/Linux:**

```bash
cd foundry-agents
python3 -m venv .venv
source .venv/bin/activate
```

> **Note:** You should see `(.venv)` in your terminal prompt when the virtual environment is active.

### (Optional) Running Tests Globally Without a Virtual Environment

If you prefer to run tests without activating a virtual environment, you can install dependencies globally:


```powershell
# Install test dependencies globally
pip install -r tests/test-requirements.txt

# Run tests using python -m pytest (recommended)
python -m pytest tests/e2e/test_prerequisites.py -v

# Or run pytest directly (if in PATH)
pytest tests/ -v
```

> **⚠️ WARNING: Why You Should Avoid Global Installation**
>
> Using a virtual environment is **strongly recommended** over global installation as that could lead to dependency conflicts and other issues.

### Step 3: Install Project Dependencies

With the virtual environment activated, install the main project requirements:

```bash
pip install -r requirements.txt
```

### Step 4: Install Test Dependencies

Install all testing dependencies:

```bash
pip install -r tests/test-requirements.txt
```

**Or install key packages manually:**

```bash
pip install pytest pytest-asyncio pytest-dotenv pytest-order httpx
pip install azure-identity azure-storage-blob azure-data-tables azure-search-documents
pip install azure-ai-evaluation azure-ai-projects
```

### Step 5: Verify Installation

Run the environment verification script:

```bash
python tests/verify_test_environment.py
```

### Required Python Packages

**Key Dependencies:**

| Package | Version | Purpose |
|---------|---------|---------|
| `pytest` | ≥8.0.0 | Core testing framework |
| `pytest-asyncio` | ≥0.24.0 | **CRITICAL**: Async test support |
| `pytest-dotenv` | ≥0.5.2 | Automatic `.env.test` file loading |
| `pytest-order` | ≥1.2.0 | Test execution ordering |
| `httpx` | ≥0.27.0 | Async HTTP client for API testing |
| `azure-ai-evaluation` | ≥1.0.0 | LLM response quality evaluation |
| `azure-ai-projects` | ≥1.1.0b4 | Microsoft Foundry SDK |
| `azure-identity` | ≥1.16.0 | Azure authentication |
| `azure-storage-blob` | ≥12.20.0 | Blob storage integration |
| `azure-data-tables` | ≥12.7.0 | Table storage integration |
| `azure-search-documents` | ≥11.6.0b12 | AI Search integration |

> **⚠️ CRITICAL**: `pytest-asyncio` must be version 0.24.0+. Older versions will cause "fixture not found" errors.

### Azure Resources

Integration and E2E tests connect to **real Azure services** (no mocking):

| Service | Purpose | Required For |
|---------|---------|--------------|
| Azure AI Foundry Project | Agent execution | All agent tests |
| Azure OpenAI Service | LLM inference | All agent tests |
| Azure Blob Storage | Document storage | Integration, E2E tests |
| Azure AI Search | Vector search | Integration, E2E tests |
| Azure Table Storage | Metadata storage | Integration tests |

### Environment Configuration

Create a `.env.test` file in the project root:

```bash
# Azure AI Foundry Configuration
AZURE_EXISTING_AIPROJECT_ENDPOINT=https://your-foundry.services.ai.azure.com/api/projects/your-project
AZURE_OPENAI_ENDPOINT=https://your-endpoint.openai.azure.com/
AZURE_AI_AGENT_DEPLOYMENT_NAME=gpt-4o

# Azure Storage
AZURE_STORAGE_ACCOUNT_NAME=your_storage_account
AZURE_STORAGE_ACCOUNT_URL=https://your-storage.blob.core.windows.net

# Test Configuration
TEST_APP_ID=test-app-50000
API_BASE_URL=https://your-api.azurecontainerapps.io
```

> **Note**: The `.env.test` file is **automatically loaded** by pytest via the `pytest-dotenv` plugin.

---

## Quick Start

### 1. Install Dependencies

```bash
pip install -r tests/test-requirements.txt
```

### 2. Verify Environment

```bash
python tests/verify_test_environment.py
```

### 3. Run Tests

```bash
# Run all tests
pytest tests/ -v

# Run by category
pytest tests/unit/ -v                    # Unit tests
pytest tests/integration/ -v             # Integration tests
pytest tests/evaluation/ -v              # Evaluation tests
pytest tests/e2e/ -v                     # End-to-end tests

# Run prerequisites check first (recommended for E2E)
pytest tests/e2e/test_prerequisites.py -v
```

---

## Test Directory Structure

```
tests/
├── README.md                          # This file (overview)
├── conftest.py                        # Root-level shared fixtures
├── test-requirements.txt              # Testing dependencies
├── verify_test_environment.py         # Environment validation script
│
├── unit/                              # Unit tests
│   ├── README.md                      # 📖 Unit Testing Guide
│   └── test_operation_tracking.py
│
├── integration/                       # Integration tests
│   ├── README.md                      # 📖 Integration Test Guide
│   ├── conftest.py
│   ├── test_helpers.py
│   ├── test_create_application_id.py
│   ├── test_blob_storage_integration.py
│   ├── test_index_documents.py
│   ├── test_azure_search_integration.py
│   ├── test_analyze_code.py
│   ├── test_discover_kubernetes.py
│   ├── test_run_analysis.py
│   ├── test_generate_assessment_report.py
│   ├── test_generate_design.py
│   ├── test_analyze_architecture.py
│   └── test_delete_app_data.py
│
├── e2e/                               # End-to-end workflow tests
│   ├── README.md                      # 📖 E2E Test Guide
│   ├── conftest.py
│   ├── test_prerequisites.py
│   ├── test_agent_workflow.py
│   ├── reports/
│   └── sample-artifacts/
│
└── evaluation/                        # LLM response quality tests
    ├── README.md                      # 📖 Evaluation Framework Guide
    ├── conftest.py
    ├── agent_runner.py
    ├── test_response_quality.py
    ├── datasets/
    ├── baselines/
    └── responses/
```

---

## Running Tests

### By Test Type

```bash
# Unit tests (fast, no external dependencies)
pytest tests/unit/ -v

# Integration tests (requires Azure services)
pytest tests/integration/ -v -m integration

# Evaluation tests (LLM quality metrics)
pytest tests/evaluation/ -v -m evaluation

# E2E tests (full workflow)
pytest tests/e2e/ -v -m e2e
```

### Test Markers

| Marker | Description |
|--------|-------------|
| `@pytest.mark.unit` | Fast unit tests, no external dependencies |
| `@pytest.mark.integration` | Tests requiring Azure services |
| `@pytest.mark.e2e` | Full end-to-end workflow tests |
| `@pytest.mark.evaluation` | LLM response quality tests |
| `@pytest.mark.asyncio` | Async tests |
| `@pytest.mark.slow` | Long-running tests |

### Examples

```bash
# Run specific test file
pytest tests/integration/test_create_application_id.py -v

# Run specific test class
pytest tests/unit/test_operation_tracking.py::TestOperationRecord -v

# Run evaluation for specific endpoint
pytest tests/evaluation/test_response_quality.py -k "[design]" -v

# Exclude slow tests
pytest tests/ -v -m "not slow"

# With coverage
pytest tests/ -v --cov=agents --cov-report=html
```

---

## CI/CD Integration

### GitHub Actions

The project includes an automated test workflow that runs all test categories on push, pull requests, or manual trigger.

**Workflow File:** [`.github/workflows/run-tests.yml`](../../.github/workflows/run-tests.yml)

#### What the Workflow Does

The workflow executes all four test categories in a sequential pipeline:

1. **Unit Tests** (10 min timeout)
   - Fast isolated component tests
   - No external dependencies
   - Generates code coverage reports

2. **Integration Tests** (60 min timeout)
   - Tests agent-to-Azure service interactions
   - Requires Azure authentication via OIDC
   - Validates API endpoints individually

3. **Evaluation Tests** (90 min timeout)
   - LLM response quality metrics
   - Relevance, groundedness, coherence scoring
   - Runs in parallel with E2E tests

4. **End-to-End Tests** (120 min timeout)
   - Full workflow validation (12-step pipeline)
   - Creates test logs in `tests/e2e/reports/`
   - Stops on first failure (`-x` flag)

#### Selective Test Execution

The workflow provides three ways to control which test categories run:

**1. Manual Workflow Trigger (GitHub Actions UI)**

When running the workflow manually, you can select which categories to execute via checkboxes:
- ☑️ Run unit tests
- ☑️ Run integration tests
- ☑️ Run evaluation tests
- ☑️ Run end-to-end tests

**2. Repository Variables (Persistent Control)**

Set these variables in **Settings → Secrets and variables → Actions → Variables** to control default behavior:

| Variable | Default | Effect |
|----------|---------|--------|
| `ENABLE_UNIT_TESTS` | (not set) | Must be `'true'` to enable unit tests |
| `ENABLE_INTEGRATION_TESTS` | (not set) | Must be `'true'` to enable integration tests |
| `ENABLE_EVALUATION_TESTS` | (not set) | Must be `'true'` to enable evaluation tests |
| `ENABLE_E2E_TESTS` | `'true'` | Set to `'false'` to disable E2E tests |

**Default Behavior (No Variables Set):**
- ✅ E2E tests run (enabled by default)
- ❌ Unit, Integration, and Evaluation tests skip (disabled by default)

**Example: Enable All Tests**
- Set `ENABLE_UNIT_TESTS` = `true`
- Set `ENABLE_INTEGRATION_TESTS` = `true`
- Set `ENABLE_EVALUATION_TESTS` = `true`
- Leave `ENABLE_E2E_TESTS` unset (or set to `true`)

**3. Automatic Triggers (Push/PR)**

On push or pull request to `main`, test execution follows the repository variables configuration above.

#### Parallel Test Execution

To optimize workflow duration, **test categories run independently and in parallel** based on their enable flags:

```                       
When Only E2E Enabled (default):
    E2E Tests (120 min) → Test Summary
    
When Multiple Categories Enabled:
    Tests run in parallel based on dependencies:
    - Integration waits for Unit (if both enabled)
    - Evaluation runs independently
    - E2E runs independently
```

**Key Benefits:**

1. **Independent Execution** - Evaluation and E2E tests start immediately without waiting for other tests
2. **Flexible Configuration** - Enable only the tests you need without blocking others
3. **Time Savings** - Parallel execution reduces total pipeline time:
   - All tests sequential: ~280 minutes (4h 40m)
   - All tests parallel: ~120 minutes (2h 0m)
   - **Time Saved: 160 minutes (57% faster)**

**Why They Run in Parallel:**
- Evaluation tests generate their own agent responses using test datasets
- E2E tests validate the complete 12-step workflow pipeline
- Integration tests validate individual API endpoints
- None depend on each other's outputs, allowing simultaneous execution

#### Artifacts

The workflow uploads test results and reports as downloadable artifacts:

- `unit-test-results` - JUnit XML + coverage reports (HTML and XML)
- `integration-test-results` - JUnit XML test results
- `evaluation-test-results` - JUnit XML + evaluation response files
- `e2e-test-results` - JUnit XML + timestamped execution logs

**What is JUnit XML?**

JUnit XML is a standardized XML format for reporting test results. It provides machine-readable test execution data including:
- Test counts (passed, failed, skipped)
- Execution duration for each test
- Failure messages and stack traces
- Test hierarchy (suites, classes, test cases)

**How GitHub Displays Test Results:**

GitHub Actions automatically processes JUnit XML files to provide:

1. **Test Summary Annotations** - Built-in display in workflow summary showing pass/fail counts and highlighting failures. GitHub automatically detects JUnit XML artifacts and creates check run annotations.

2. **Pull Request Comments** - For PRs, the workflow automatically posts a comment summarizing all test results using GitHub CLI:
   ```
   🧪 Test Results Summary
   
   Unit Tests: ✅ 45 passed in 2.4m
   Integration Tests: ✅ 23 passed in 12.5m  
   Evaluation Tests: ⚠️ 8 passed, 2 failed in 45.3m
   E2E Tests: ✅ 12 passed in 118.7m
   
   ---
   View detailed results in Actions
   ```
   
   This gives PR reviewers immediate visibility into test health without navigating to the Actions tab. The comment updates automatically on each push to the PR branch.

**Downloading Artifacts:**

To download test results:
1. Go to the workflow run in GitHub Actions
2. Scroll to the "Artifacts" section at the bottom
3. Click to download any artifact (results remain available for 90 days)

#### Configuration

Required Azure roles, GitHub secrets and variables:

**Azure Roles required by the service principal running the workflow**

The following two Azure roles must be assigned to the Service Principal running the workflow:
  - Storage Blob Data Contributor
  - Storage Table Data Contributor

**Secrets (Settings → Secrets and variables → Actions → Secrets):**

| Secret | Purpose |
|--------|---------|
| `AZURE_CLIENT_ID` | OIDC authentication (service principal client ID) |
| `AZURE_TENANT_ID` | Azure tenant ID |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID |

**Variables (Settings → Secrets and variables → Actions → Variables):**

| Variable | Purpose | Example |
|----------|---------|---------|
| `RESOURCE_GROUP` | Azure resource group name | `rg-insights-agent-dev` |
| `LOCATION` | Azure region | `eastus2` |
| `PYTHON_VERSION` | Python version for tests | `3.11` |
| `AZURE_EXISTING_AIPROJECT_ENDPOINT` | AI Foundry project endpoint | `https://...foundry.services.ai.azure.com/` |
| `AZURE_AI_AGENT_DEPLOYMENT_NAME` | AI agent deployment name | `gpt-4o` |
| `AZURE_SEARCH_ENDPOINT` | Azure AI Search endpoint | `https://...search.windows.net` |
| `AZURE_SEARCH_INDEX` | Search index name | `insights-index` |
| `AZURE_SEARCH_SEMANTIC_CONFIG` | Semantic search config name | `default` |
| `AZURE_STORAGE_ACCOUNT_NAME` | Storage account name | `stinsightsagent` |
| `AZURE_STORAGE_ACCOUNT_URL` | Storage account URL | `https://...blob.core.windows.net` |
| `AZURE_TABLES_ACCOUNT_URL` | Table storage URL | `https://...table.core.windows.net` |
| `API_BASE_URL` | Container App API URL | `https://insights-api.azurecontainerapps.io` |
| `TEST_APP_ID` | Test application ID | `test-app-50000` |
| `LOG_LEVEL` | Logging level | `INFO` |

**Optional Test Control Variables:**

| Variable | Default | Purpose |
|----------|---------|---------|
| `ENABLE_UNIT_TESTS` | (disabled) | Set to `true` to run unit tests on push/PR |
| `ENABLE_INTEGRATION_TESTS` | (disabled) | Set to `true` to run integration tests on push/PR |
| `ENABLE_EVALUATION_TESTS` | (disabled) | Set to `true` to run evaluation tests on push/PR |
| `ENABLE_E2E_TESTS` | (enabled) | Set to `false` to skip E2E tests on push/PR |

---

## Troubleshooting

### Common Issues

| Issue | Solution |
|-------|----------|
| **Fixture 'http_client' not found** | Install `pytest-asyncio>=0.24.0` |
| **Async def functions not supported** | Upgrade `pytest-asyncio` to version 0.24.0+ |
| `DefaultAzureCredential` fails | Run `az login` or set service principal environment variables |
| Container not found | Create container via Azure Portal or API |
| Timeout errors | Increase `timeout` in test fixtures or environment |
| Evaluation SDK errors | Ensure `azure-ai-evaluation>=1.0.0` is installed |

### pytest-asyncio Version Fix

```bash
# Check current version
python -m pip show pytest-asyncio

# Force reinstall correct version
python -m pip install --force-reinstall pytest-asyncio==0.24.0

# Verify
python -m pip show pytest-asyncio | grep Version
```

### Debug Mode

```bash
# Enable debug logging
LOG_LEVEL=DEBUG pytest tests/ -v -s

# Show all print statements
pytest tests/ -v -s --capture=no
```

### Checking Test Environment

```python
# Quick environment check
from azure.identity import DefaultAzureCredential
credential = DefaultAzureCredential()
token = credential.get_token("https://management.azure.com/.default")
print(f"Token acquired: {token.token[:20]}...")
```

---

## Best Practices

### 1. Test Data Management

- Use dedicated test containers in Azure Storage
- Clean up test data after E2E runs
- Use unique `app_id` per test run to avoid conflicts

### 2. Real Service Connections

All tests use **real Azure connections** (no mocking) to ensure:
- Tests reflect actual production behavior
- Configuration issues are caught early
- Azure SDK compatibility is validated

### 3. Evaluation Thresholds

Default thresholds (1-5 scale):

| Metric | Threshold |
|--------|-----------|
| Relevance | ≥ 4.0 |
| Groundedness | ≥ 4.0 |
| Coherence | ≥ 4.0 |
| Fluency | ≥ 4.0 |
| Similarity | ≥ 3.5 |

---

## References

### Azure AI & Foundry

- [Microsoft Foundry SDK Documentation](https://learn.microsoft.com/python/api/overview/azure/ai-projects-readme)
- [Azure AI Evaluation SDK Documentation](https://learn.microsoft.com/azure/ai-studio/how-to/develop/evaluate-sdk)
- [Azure AI Foundry Projects](https://learn.microsoft.com/azure/ai-studio/how-to/create-projects)

### Testing Frameworks

- [pytest Documentation](https://docs.pytest.org/)
- [pytest-asyncio](https://pytest-asyncio.readthedocs.io/)
- [Azure SDK for Python](https://learn.microsoft.com/azure/developer/python/sdk/azure-sdk-overview)

---

## Contributing

When adding new tests:

1. Follow existing patterns in the source files
2. Add appropriate markers (`@pytest.mark.e2e`, `@pytest.mark.integration`, etc.)
3. Include docstrings with usage instructions
4. Use proper logging (`logger = logging.getLogger(__name__)`)
5. Update the relevant sub-folder README if adding new test types or fixtures

---
