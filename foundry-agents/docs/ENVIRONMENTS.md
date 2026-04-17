<!--
DOC_INTENT:
	surface: foundry
	page: ENVIRONMENTS
	purpose: Define environment configuration for Foundry agents (local/dev/test/prod), including required variables and safe defaults.
	audience: Developers, operators
	should_cover:
		- Environment variable inventory and meaning
		- Local dev setup patterns (.env, VS Code, CI)
		- Non-secret vs secret configuration guidance
		- Example configs for common scenarios
	should_not_cover:
		- Credential values or secrets
	source_refs:
		- foundry-agents/env.example
		- cockpit-docs/docs/ENVIRONMENTS.md (reference style only)
-->

# Environment configuration

This repo supports multiple execution contexts:

- **Foundry Insights API** (FastAPI) and agents in `foundry-agents/agents/*`
- **Indexer** service in `foundry-agents/indexer/*` (indexes content into Azure AI Search)
- **Optional MCP servers** (e.g., Azure Pricing MCP)
- **Automated tests** under `foundry-agents/tests/*`

Configuration is provided via environment variables, optionally loaded from a `.env` file (many modules call `load_dotenv()`).

## Supported environments

### Local development

Use when running agents and/or the API locally.

- Prefer **Azure identity via `DefaultAzureCredential`** (Azure CLI login, Visual Studio, managed identity where available).
- Keep data-plane keys unset (do not set Storage account keys, Search admin keys) unless you are explicitly running in a dev-only fallback.

### CI / test

The test suite uses a small set of variables (see [env.test.example](../env.test.example)). Integration/E2E tests may be skipped when required variables are missing.

### Hosted (Container Apps / Functions)

Use managed identity and set only non-secret configuration as app settings.

Important trust-boundary note: the API uses **header-based identity inputs** for some endpoints (see `X-User-Object-Id`, `X-Group-Object-Id` in the API docs). In production, deploy behind an authenticated gateway (e.g., APIM) so callers cannot spoof identity headers.

## Environment variable reference

The tables below describe the variables used across the repo. Variables marked “Required” are required for that scenario/module.

### Core Azure context

| Variable | Required | Used by | Meaning |
|---|---:|---|---|
| `AZURE_SUBSCRIPTION_ID` | ✅ | RBAC helper, deployment scripts | Azure subscription for ARM lookups and role assignment operations. |
| `RESOURCE_GROUP` | ⚠️ | Deployment scripts | Resource group for deployment tooling (shell/PowerShell scripts). |
| `LOCATION` | ⚠️ | Deployment scripts | Azure location used by deployment scripts (e.g., `eastus2`). |
| `AZURE_RESOURCE_GROUP` | ⚠️ | Tests | Resource group name for tests and prerequisite checks. |
| `AZURE_REGION` | ⚠️ | Tests | Azure region used by tests (e.g., `eastus2`). |

Notes:

- The runtime code primarily depends on `AZURE_SUBSCRIPTION_ID` for RBAC operations.
- Deployment scripts use `RESOURCE_GROUP`/`LOCATION`; tests often use `AZURE_RESOURCE_GROUP`/`AZURE_REGION`.

### Azure Storage (Blob + Tables)

| Variable | Required | Used by | Meaning |
|---|---:|---|---|
| `AZURE_STORAGE_ACCOUNT_NAME` | ✅ | Agents, indexer, tests | Storage account name (used to construct URLs and scopes). |
| `AZURE_STORAGE_ACCOUNT_URL` | ⚠️ | Agents, indexer, tests | Blob endpoint base URL (e.g., `https://<acct>.blob.core.windows.net`). Some utilities can derive this from `AZURE_STORAGE_ACCOUNT_NAME`, but many modules expect it explicitly. |
| `AZURE_TABLES_ACCOUNT_URL` | ⚠️ | Agents | Table endpoint base URL (e.g., `https://<acct>.table.core.windows.net`). Some utilities can derive this from `AZURE_STORAGE_ACCOUNT_NAME`. |
| `AZURE_STORAGE_CONTAINER_NAME` | ❌ | Code analyzer agent | Container name for code analysis artifacts (default: `code-analysis-reports`). |
| `AZURE_STORAGE_ACCOUNT_KEY` | ❌ | RBAC auth utilities | **Dev-only bypass**: if set, some RBAC checks are bypassed and storage access may use key-based auth. Avoid in production. |
| `AZURE_STORAGE_CONNECTION_STRING` | ❌ | Environment-setup scripts | Connection string used by some setup scripts (dev/ops tooling). Avoid in production. |

Storage endpoint aliases that appear in the codebase:

- `AZURE_BLOB_ACCOUNT_URL` (alias for blob endpoint)
- `AZURE_TABLE_ACCOUNT_URL` (legacy/alternate table endpoint)

### Azure AI Search

| Variable | Required | Used by | Meaning |
|---|---:|---|---|
| `AZURE_SEARCH_ENDPOINT` | ⚠️ | Agents, indexer, tests | Search service endpoint (e.g., `https://<service>.search.windows.net`). |
| `AZURE_SEARCH_INDEX` | ⚠️ | Agents, indexer | Search index name (legacy/fallback in some modules). |
| `SCF_AZURE_SEARCH_INDEX` | ❌ | Agents | Azure AI Search index name containing SCF security controls. |
| `AZURE_SEARCH_SEMANTIC_CONFIG` | ❌ | Agents, indexer | Semantic configuration name used for hybrid/semantic search. |
| `AZURE_AI_SEARCH_FILTER` | ❌ | Agents | Optional OData filter expression used by some search calls. |
| `AZURE_SEARCH_ADMIN_KEY` | ❌ | Indexer | Admin API key fallback for Search. Prefer managed identity in hosted environments. |
| `AZURE_SEARCH_API_KEY` | ❌ | Agents/plugins | API key used by some search utilities (fallback). Prefer managed identity where possible. |

Legacy aliases that may appear in code:

- `SEARCH_ENDPOINT`
- `SEARCH_INDEX_NAME`

### Azure AI Foundry / project endpoint

| Variable | Required | Used by | Meaning |
|---|---:|---|---|
| `AZURE_EXISTING_AIPROJECT_ENDPOINT` | ⚠️ | Agents, tracing | Foundry project endpoint used by SDK integration and telemetry enrichment. |
| `FOUNDRY_PROJECT_ENDPOINT` | ❌ | Some agents/utilities | Alias for a Foundry project endpoint (used as a fallback in some code paths). |
| `AZURE_AI_PROJECT_CONNECTION_STRING` | ⚠️ | Tests | Foundry project connection string used by evaluations/integration tests. |
| `AZURE_AI_PROJECT_NAME` | ⚠️ | Deployment scripts | Foundry/AI service name used by deployment scripts (role assignment scope building). |

Notes:

- The tests expect a connection string that starts with an endpoint and may include metadata. The format used in the test harness is:
	- `<endpoint>;subscription_id=<sub>;resource_group=<rg>;project_name=<project>`
- Avoid logging the full connection string in CI/CD logs.

### Azure OpenAI / model configuration

| Variable | Required | Used by | Meaning |
|---|---:|---|---|
| `AZURE_OPENAI_ENDPOINT` | ⚠️ | Agents, indexer, tests | Azure OpenAI endpoint (base URL, or full embeddings URL in some indexer contexts). |
| `AZURE_OPENAI_ENDPOINT2` | ❌ | Indexer | Base endpoint used by Azure AI Search vectorizer configuration. |
| `AZURE_OPENAI_API_VERSION` | ⚠️ | Agents, indexer | API version for Azure OpenAI calls. |
| `AZURE_OPENAI_DEPLOYMENT` | ⚠️ | Tests | Chat model deployment name (tests). |
| `AZURE_AI_AGENT_DEPLOYMENT_NAME` | ⚠️ | Agents | Chat model deployment name used by agents (often overlaps with OpenAI deployment name). |
| `AZURE_OPENAI_CHAT_DEPLOYMENT` | ❌ | Agents (fallback) | Alternate name for chat model deployment. |
| `AZURE_OPENAI_DEPLOYMENT_NAME` | ❌ | Agents (fallback) | Alternate name for chat model deployment. |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | ⚠️ | Indexer | Embeddings deployment name. |
| `AZURE_OPENAI_EMBED_DIM` | ❌ | Indexer | Embedding vector dimensions (default: `3072`). |
| `USE_MANAGED_IDENTITY_FOR_AOAI` | ❌ | Indexer | If `true`, use managed identity for Azure OpenAI (default: `true`). |

### Indexer behavior

| Variable | Required | Used by | Meaning |
|---|---:|---|---|
| `AZURE_INDEXING_FUNCTION_URL` | ⚠️ | Agents | URL for the indexing endpoint (HTTP) invoked by orchestrator flows. |
| `USE_MANAGED_IDENTITY` | ❌ | Indexer | If `true`, use managed identity for Search when no admin key is provided (default: `true`). |
| `SEARCH_MAX_DOCS_PER_BATCH` | ❌ | Indexer | Max documents per indexing batch (default: `500`). |
| `SEARCH_MAX_BYTES_PER_BATCH` | ❌ | Indexer | Max payload size per indexing batch (default: `12582912`). |
| `ENABLE_SENSITIVE_SCAN` | ❌ | Indexer | Enable detect-secrets scan (default: `true`). |
| `SKIP_SENSITIVE_DOCUMENTS` | ❌ | Indexer | If `true`, skip documents that appear sensitive (default: `true`). |

### Telemetry & logging

| Variable | Required | Used by | Meaning |
|---|---:|---|---|
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | ❌ | Agents, indexer | Enables Application Insights/OpenTelemetry export. |
| `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` | ❌ | Agents | If `true`, may capture model prompt/response content (privacy-sensitive). |
| `LOG_LEVEL` | ❌ | Agents, indexer, MCP server | Log level (e.g., `DEBUG`, `INFO`). |
| `LOG_FILE` | ❌ | Agents, indexer, MCP server | Optional log file output path/name. |
| `APP_VERBOSE` | ❌ | Agents | Verbose logging toggle (string/flag). |

Notes:

- The Python code reads `LOG_FILE` (with an underscore). Some example files may show `LOG_File`; prefer `LOG_FILE`.

### Key Vault (optional)

| Variable | Required | Used by | Meaning |
|---|---:|---|---|
| `AZURE_KEY_VAULT_URL` | ❌ | Orchestrator | Key Vault URL used by some orchestration flows. |
| `KEY_VAULT_URL` | ❌ | Orchestrator (alias) | Alias for `AZURE_KEY_VAULT_URL`. |

### MCP (Model Context Protocol) integration

| Variable | Required | Used by | Meaning |
|---|---:|---|---|
| `MCP_ALLOWED_SERVERS` | ❌ | Agents | Comma-separated allowlist of MCP servers/tools the agent may load. |
| `AZURE_PRICING_MCP_URL` | ❌ | Agents | URL for an Azure Pricing MCP server (SSE endpoint). |
| `MICROSOFT_LEARN_MCP_URL` | ❌ | Agents | URL for Microsoft Learn MCP server (`https://learn.microsoft.com/api/mcp`). |
| `AZURE_DEVOPS_MCP_URL` | ❌ | Agents | URL for an Azure DevOps MCP server, if used. |
| `MCP_HOST` | ❌ | MCP servers | Host binding for a self-hosted MCP server. |
| `MCP_PORT` | ❌ | MCP servers | Port for a self-hosted MCP server. |
| `MCP_DEBUG` | ❌ | MCP servers | Debug toggle for MCP server runtime. |
| `MCP_RELOAD` | ❌ | MCP servers | Auto-reload toggle for MCP server runtime. |
| `CORS_ORIGINS` | ❌ | MCP servers | CORS allowed origins (use restrictive values in production). |

### Test-only variables

| Variable | Required | Used by | Meaning |
|---|---:|---|---|
| `API_BASE_URL` | ⚠️ | Integration/E2E tests | Base URL for the hosted Insights API under test. |
| `TEST_APP_ID` | ⚠️ | Tests | App/container identifier used during tests. |
| `TEST_USER_OBJECT_ID` | ⚠️ | Tests | User object ID used for header-based RBAC tests. |
| `TEST_GROUP_OBJECT_ID` | ❌ | Tests | Group object ID used for header-based RBAC tests. |
| `ENABLE_FOUNDRY_TRACKING` | ❌ | Tests | Enables optional tracking behaviors in tests. |

## Secrets vs non-secrets guidance

### Non-secrets (safe to store as app settings)

- Resource names, endpoints, index names, feature flags.

### Secrets (do not commit; avoid in production when possible)

- `AZURE_STORAGE_ACCOUNT_KEY`
- `AZURE_STORAGE_CONNECTION_STRING`
- `AZURE_SEARCH_ADMIN_KEY`, `AZURE_SEARCH_API_KEY`
- Any bearer tokens or client secrets (not expected/required for managed identity)

In hosted environments, prefer **managed identity** for:

- Azure Storage data-plane access
- Azure AI Search data-plane access
- Azure OpenAI access

## Example configurations

### Local dev (agents + API)

Create a `.env` file in `foundry-agents/` (do not commit it) based on [env.example](../env.example).

Minimum commonly-used variables:

```bash
AZURE_SUBSCRIPTION_ID="<subscription-guid>"

AZURE_STORAGE_ACCOUNT_NAME="<storage-account-name>"
AZURE_STORAGE_ACCOUNT_URL="https://<storage-account-name>.blob.core.windows.net"
AZURE_TABLES_ACCOUNT_URL="https://<storage-account-name>.table.core.windows.net"

AZURE_SEARCH_ENDPOINT="https://<search-service>.search.windows.net"
AZURE_SEARCH_INDEX="<index-name>"

AZURE_OPENAI_ENDPOINT="https://<aoai-resource>.openai.azure.com/"
AZURE_AI_AGENT_DEPLOYMENT_NAME="<chat-deployment-name>"
AZURE_OPENAI_API_VERSION="2023-05-15"
```

### Tests

Use [env.test.example](../env.test.example) as the baseline.

- Integration/E2E tests require `API_BASE_URL` and test identity headers (`TEST_USER_OBJECT_ID`, etc.).
- Foundry evaluations require `AZURE_AI_PROJECT_CONNECTION_STRING`.

## Preflight checklist

- `AZURE_SUBSCRIPTION_ID` set for RBAC operations.
- Storage account exists and the per-app container exists (or has been provisioned via onboarding).
- The runtime identity (managed identity / developer identity) has data-plane access to Storage.
- If using Search features, `AZURE_SEARCH_ENDPOINT` is set and the identity has at least **Search Index Data Reader** on the Search service.
- If using indexing, `AZURE_INDEXING_FUNCTION_URL` is set and reachable.
- If telemetry is desired, `APPLICATIONINSIGHTS_CONNECTION_STRING` is set.


