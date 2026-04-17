<!--
DOC_INTENT:
	surface: foundry
	page: TROUBLESHOOTING
	purpose: Provide a symptom-driven troubleshooting guide for common Foundry agent issues (setup, auth, connectivity, indexing, runtime errors).
	audience: Developers, operators
	should_cover:
		- Missing env var / config errors
		- Auth failures (local and hosted)
		- Azure service connectivity errors
		- Indexing/tooling failures and remediation
		- Where to collect logs and how to report issues
	should_not_cover:
		- Deep architecture discussion (belongs in ARCHITECTURE)
	source_refs:
		- cockpit-docs/docs/troubleshooting.md (reference style only)
-->

# Troubleshooting

This is a symptom-driven guide for the most common setup and runtime issues.

## What to collect when reporting an issue

Provide as many of these as possible:

- **Timestamp** and **timezone**
- **Environment** (local, test, hosted)
- **Operation ID** (if applicable) and **app_id**
- Request path and method (e.g., `GET /operations/...`)
- Non-secret headers used for RBAC validation:
  - `X-User-Object-Id` and/or `X-Group-Object-Id`
  - `X-Storage-Account`
  - `X-Resource-Group` (if you use it)
- Relevant env vars (names only, not values), especially:
  - `AZURE_SUBSCRIPTION_ID`, `AZURE_STORAGE_ACCOUNT_NAME`, `AZURE_STORAGE_ACCOUNT_URL`, `AZURE_TABLES_ACCOUNT_URL`
  - `AZURE_SEARCH_ENDPOINT`, `AZURE_SEARCH_INDEX`
  - `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_VERSION`, deployment name vars
- Logs/traces from Application Insights if enabled (`APPLICATIONINSIGHTS_CONNECTION_STRING`)

## Setup and configuration issues

### Health check is unhealthy (503)

**Symptoms**:

- `GET /health` returns `503` with `"status": "unhealthy"`.
- Message includes `Missing required environment variables: ...`.

**Cause**: The API health check requires these env vars to be set:

- `AZURE_EXISTING_AIPROJECT_ENDPOINT`
- `AZURE_STORAGE_ACCOUNT_URL`
- `AZURE_TABLES_ACCOUNT_URL`

**Fix**:

- Set the missing variables (see [ENVIRONMENTS.md](ENVIRONMENTS.md)).
- If you only have storage account names, ensure you provide the full account URLs (Blob + Tables) as expected by the health check.

### Error: “AZURE_SUBSCRIPTION_ID environment variable must be set”

**Cause**: RBAC helper initialization requires the subscription ID to build scopes and use ARM clients.

**Fix**:

- Set `AZURE_SUBSCRIPTION_ID` in your environment or `.env`.
- For local dev, ensure your Azure identity can access that subscription.

### Tests are being skipped

**Symptoms**:

- Pytest output shows `skipped` tests for integration/e2e/evaluation.

**Cause**: Many integration/E2E/evaluation tests are intentionally skipped unless required env vars are present.

**Fix**:

- Set the needed variables from [env.test.example](../env.test.example).
- For Foundry evaluations, configure `AZURE_AI_PROJECT_CONNECTION_STRING`.
- For API integration tests, set `API_BASE_URL`.

## Authorization and RBAC issues

### HTTP 400: `missing_identity`

**Symptoms**: API responses indicate you must provide at least one of user or group identity.

**Cause**: Endpoints requiring header-based RBAC checks need `X-User-Object-Id` and/or `X-Group-Object-Id`.

**Fix**:

- Provide at least one of those headers.
- Ensure your gateway is configured to inject/validate identity headers in production.

### HTTP 403: `access_denied` on container

**Cause**: The provided user/group identity does not have Blob roles on the target container.

**Fix**:

- Grant `Storage Blob Data Contributor` (or `Storage Blob Data Owner`) at the container scope for the `app_id` container.
- Wait for RBAC propagation if the assignment was just made.

### HTTP 403: table access issues after container access succeeds

**Cause**: The identity lacks `Storage Table Data Contributor` on the per-app tables.

**Fix**:

- Ensure the required per-app tables exist.
- Ensure the identity has `Storage Table Data Contributor` at the per-table scope.
- If the system is assigning permissions automatically, allow for propagation delay (up to ~10 minutes).

### “Storage account key detected - bypassing RBAC checks” in logs

**Cause**: `AZURE_STORAGE_ACCOUNT_KEY` is set. Some RBAC validation paths bypass RBAC checks in this case.

**Fix**:

- For production, remove `AZURE_STORAGE_ACCOUNT_KEY` from the environment.
- Prefer managed identity and RBAC roles.

## Storage and data issues

### HTTP 404: `container_not_found`

**Cause**: The per-app blob container does not exist in the configured storage account.

**Fix**:

- Verify `AZURE_STORAGE_ACCOUNT_NAME` is correct.
- Ensure the `app_id` container is provisioned (often via onboarding/portal workflows).

### HTTP 404: `tables_not_found`

**Cause**: The per-app tables (derived from templates) have not been created yet.

**Fix**:

- Run the application initialization flow (e.g., create/init endpoint) that clones template tables.
- Verify `AZURE_TABLES_ACCOUNT_URL` points to the correct account.

## Search and indexing issues

### Search is configured but queries fail

**Symptoms**:

- Errors indicating unauthorized access to Azure AI Search.

**Fix**:

- Prefer managed identity for Search.
- Ensure the runtime identity has `Search Index Data Reader` on the Search service.
- If using an API key fallback, set `AZURE_SEARCH_ADMIN_KEY` (preferred) or `AZURE_SEARCH_API_KEY` (legacy) and rotate appropriately.

### Indexing trigger fails: “AZURE_INDEXING_FUNCTION_URL not set”

**Cause**: The service tried to trigger the indexer via HTTP, but `AZURE_INDEXING_FUNCTION_URL` is not configured.

**Fix**:

- Set `AZURE_INDEXING_FUNCTION_URL` to the indexer HTTP endpoint URL.
- Confirm the endpoint is reachable from the API runtime network.

### Indexer fails to write documents

**Cause**: Missing Search write permissions or misconfiguration of index name.

**Fix**:

- Ensure the indexer identity has `Search Index Data Contributor` on the Search service.
- Verify `AZURE_SEARCH_ENDPOINT` is set (the indexer hard-requires it).
- For per-app indexing, the Search index name is derived from `appId` (sanitized). `AZURE_SEARCH_INDEX` exists as a legacy/fallback value in some paths but should not be relied on as the primary contract.
- For SCF-based analysis queries, verify `SCF_AZURE_SEARCH_INDEX` (or legacy `SEARCH_INDEX_NAME`) is configured.
- If indexing via HTTP, verify `AZURE_INDEXING_FUNCTION_URL` is reachable.

### Indexer is skipping documents as “sensitive”

**Cause**: Sensitive scan is enabled and documents match detect-secrets patterns.

**Fix**:

- Review `ENABLE_SENSITIVE_SCAN` and `SKIP_SENSITIVE_DOCUMENTS`.
- If you must ingest those documents, disable skipping in a controlled environment and ensure downstream policies allow it.

## Azure OpenAI issues

### Azure OpenAI requests fail (401/403)

**Cause**: Identity does not have the required role on the Azure AI/OpenAI resource, or endpoint/deployment is wrong.

**Fix**:

- Verify `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_VERSION`, and deployment name variables.
- Ensure the runtime identity has `Cognitive Services OpenAI User` on the AI/OpenAI resource.

## Where to look for logs

### Local

- Console output (log level controlled by `LOG_LEVEL`)
- Optional file output if `LOG_FILE` is set

### Hosted

- Container App logs
- Application Insights traces/requests (when `APPLICATIONINSIGHTS_CONNECTION_STRING` is configured)

## MCP tool issues

### MCP tools are unexpectedly disabled

**Symptoms**:

- Logs include `No MCP_ALLOWED_SERVERS configured; MCP tools disabled.`

**Cause**: `MCP_ALLOWED_SERVERS` is empty or unset.

**Fix**:

- Set `MCP_ALLOWED_SERVERS` to a comma-separated allowlist (for example: `azurepricing,microsoft_learn`).
- Ensure the corresponding MCP server URL env vars are set (see [INTEGRATION.md](INTEGRATION.md)).


