<!--
DOC_INTENT:
	surface: foundry
	page: INTEGRATION
	purpose: Describe how to integrate the Foundry agents into other systems (APIs, automation, MCP tools, and operational workflows).
	audience: Integrators, developers
	should_cover:
		- Supported integration patterns (HTTP, events/queues if applicable)
		- MCP server usage and configuration (high level)
		- Security considerations for integrations
		- Versioning/compatibility guidance (if any)
	should_not_cover:
		- Vendor-specific hard requirements unless implemented in code
	source_refs:
		- cockpit-docs/docs/integration.md (reference style only)
-->

# Integration

This page describes the supported ways to integrate Foundry agents into other systems.

For API surface details (endpoints, schemas, headers), see [API.md](API.md).

## Supported integration patterns

### 1) HTTP API (primary)

The main integration point is the Insights API (FastAPI).

Common patterns:

- **Submit work**: call an endpoint that starts an operation.
- **Track progress**: poll operation status endpoints (operation tracking is backed by storage).
- **Fetch results**: read agent outputs from storage locations referenced by the operation.

Header-based identity is used on certain GET/DELETE operations endpoints (see `X-User-Object-Id`, `X-Group-Object-Id`, `X-Storage-Account`, `X-Resource-Group` in the API docs).

### 2) Indexing via HTTP function/service

Some workflows call an indexing endpoint to populate Azure AI Search.

Two common integration shapes exist in this repo:

- **External caller → Insights API → Indexer service**: call the Insights API `/indexDocuments` endpoint. The API validates the caller’s access to the app container (via the provided identity headers/fields) and then triggers the indexer.
- **Internal component → Indexer service**: some internal agent workflows may call the indexer URL directly.

Configuration and contract:

- Configure `AZURE_INDEXING_FUNCTION_URL` with the indexer HTTP endpoint (it is implemented/treated as a Container App endpoint in code).
- When triggering the indexer directly, the request body is JSON and uses these fields:
	- `appId`: the application id
	- `container`: the container name (in practice, the same value as `appId`)
	- `folder_prefix` (optional): limit indexing to a path prefix within the container (examples in code include `asr/input`, `asr/output`, and `kubernetes-discovery/...`).

Notes:

- If `AZURE_INDEXING_FUNCTION_URL` is not set, indexing is skipped/returns an error in the triggering code path.
- The indexer derives the Azure AI Search index name from `appId` (sanitized to meet Search naming rules). Do not assume `appId` is always a valid Search index name.

### 3) MCP (Model Context Protocol) tool integration

Agents can be configured to use MCP servers to access external tools/capabilities.

High-level behavior:

- `MCP_ALLOWED_SERVERS` acts as an allowlist of which MCP servers/tools may be used.
- `MCP_ALLOWED_SERVERS` is parsed as a comma-separated list (whitespace-trimmed).
- MCP server endpoints are provided via environment variables. The mapping is implemented in code as:
	- `azurepricing` or `azure-pricing-calculator` → `AZURE_PRICING_MCP_URL`
	- `azuredevops` → `AZURE_DEVOPS_MCP_URL`
	- `microsoft_learn` → `MICROSOFT_LEARN_MCP_URL`
	- otherwise → `<LABEL>_MCP_URL` (label uppercased; dashes converted to underscores)

This keeps tool usage explicit and controlled at runtime.

### 4) Direct Python invocation (internal / advanced)

You can import and invoke agent modules directly as Python code, but this is considered an internal integration. Prefer HTTP integration unless you control the runtime and want in-process calls.

## Integration matrix

The table below summarizes the key external services the repo integrates with.

| Integration | Purpose | Data in/out | Auth method (runtime) | Key configuration |
|---|---|---|---|---|
| Azure Storage (Blob) | Per-app artifacts and outputs | Reads/writes blobs | `DefaultAzureCredential` (managed identity / dev identity). Dev fallback exists via account key. | `AZURE_STORAGE_ACCOUNT_NAME`, `AZURE_STORAGE_ACCOUNT_URL` (and aliases like `AZURE_BLOB_ACCOUNT_URL`) |
| Azure Storage (Tables) | UAQ + derived tables, operation tracking | Reads/writes table entities | `DefaultAzureCredential` (managed identity / dev identity) | `AZURE_TABLES_ACCOUNT_URL` (and aliases like `AZURE_TABLE_ACCOUNT_URL`) |
| Azure AI Search (per-app content index) | Search over indexed app content | Indexer writes documents; agents query the index | Prefer managed identity; admin key fallback supported | `AZURE_SEARCH_ENDPOINT`, `AZURE_SEARCH_ADMIN_KEY` (fallback). Index name is derived from `appId` by the indexer (`AZURE_SEARCH_INDEX` exists as a legacy/fallback value in some paths). |
| Azure AI Search (SCF index) | Architecture/code analysis reference corpus | Query/read index | Prefer managed identity; key fallback supported | `SCF_AZURE_SEARCH_INDEX` (or `SEARCH_INDEX_NAME` legacy), `AZURE_SEARCH_ENDPOINT`, `AZURE_SEARCH_SEMANTIC_CONFIG` |
| Azure OpenAI | LLM calls + embeddings | Prompts/responses; embeddings vectors | Prefer managed identity; API version and deployment configurable | `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_VERSION`, `AZURE_AI_AGENT_DEPLOYMENT_NAME`, `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` |
| Azure AI Foundry project | Project-scoped AI resources and telemetry enrichment | Project metadata | Uses endpoint/connection string for some scenarios | `AZURE_EXISTING_AIPROJECT_ENDPOINT`, `AZURE_AI_PROJECT_CONNECTION_STRING` |
| MCP servers | Tool access (pricing, docs, etc.) | Tool requests/responses | Depends on server; URL-based integration | `MCP_ALLOWED_SERVERS`, `AZURE_PRICING_MCP_URL`, `AZURE_DEVOPS_MCP_URL`, `MICROSOFT_LEARN_MCP_URL`, `<LABEL>_MCP_URL` |

## Security considerations

### Protect the API trust boundary

Some endpoints accept user/group identity via headers. The API itself does not act as a full OAuth resource server in this flow.

Production guidance:

- Put the API behind a gateway that authenticates callers and injects/verifies identity headers.
- Restrict direct public access to the API to prevent header spoofing.

### Protect the indexer endpoint

The indexer URL configured by `AZURE_INDEXING_FUNCTION_URL` is called using standard HTTP headers (no function key is attached in the calling code). Treat it as an internal service endpoint:

- Prefer private networking (VNet/internal ingress) or a gateway in front of the indexer.
- Avoid exposing the indexer endpoint publicly without compensating controls.

### Prefer managed identity over keys

This repo includes key-based fallbacks (e.g., Storage account key, Search admin key). These should be treated as dev-only escape hatches.

- In hosted environments, keep key env vars unset.
- Use RBAC roles scoped to the minimum required resources.

### Data segregation

The standard model is per-application storage segregation (per-app blob containers and per-app table naming).

## Compatibility and versioning

### Azure OpenAI API versions

Several modules use `AZURE_OPENAI_API_VERSION`. Treat API version as part of your deployment contract and roll it forward intentionally.

### Environment variable compatibility

Some modules still check legacy variable names (e.g., `SEARCH_ENDPOINT`, `SEARCH_INDEX_NAME`). Prefer the `AZURE_*` variants, but keep aliases in mind when migrating older configs.

## Notes on event/queue integrations

This repo currently integrates primarily via HTTP (Insights API + indexer HTTP endpoint) and Azure service SDK calls. There is no first-class events/queues integration implemented in the Python code at this time.

## Operational workflow integration

Typical operational flow for an external system:

1. Provision or identify an `app_id` (used as the storage container name).
2. Submit an operation through the HTTP API.
3. Poll operation status endpoints until completion.
4. Collect outputs from storage and/or downstream indices.


