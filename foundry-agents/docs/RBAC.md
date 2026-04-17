<!--
DOC_INTENT:
	surface: foundry
	page: RBAC
	purpose: List the minimum Azure roles/permissions required for the Foundry agents to run, and how to scope them safely.
	audience: Operators, security reviewers
	should_cover:
		- Required Azure RBAC roles per resource (Storage, Search, AI services)
		- Recommended scoping (resource group vs resource)
		- Least-privilege guidance
		- Validation steps to confirm RBAC is working
	should_not_cover:
		- Organization-specific role assignments
	source_refs:
		- cockpit-docs/docs/rbac.md (reference style only)
-->

# RBAC (Azure role-based access control)

This repo uses Azure RBAC to control access to:

- **Per-app Azure Blob containers** (artifact and output storage)
- **Per-app Azure Table Storage tables** (UAQ and derived tables)
- **Azure AI Search** (querying and indexing)
- **Azure AI / Azure OpenAI** (model access)

## Two identities matter

### 1) Caller identity (user/group)

Some HTTP API endpoints accept an Entra user object ID and/or group object ID (via headers) and validate that identity has access to the target app container/tables.

This is used for authorization decisions like “is this caller allowed to read app X’s operation status?”.

Important:

- This API trusts the provided object IDs as identity context; it is not acting as a full OAuth2 resource server in these flows.
- In production, put the API behind an authenticated gateway (APIM / App Gateway / etc.) that validates the caller and injects/verifies the identity headers.

### 2) Runtime identity (managed identity / service principal)

The API and background components authenticate to Azure using `DefaultAzureCredential`.

The runtime identity must be allowed to:

- Access Storage and Tables (data-plane) so it can read/write data
- **Manage role assignments** (control-plane) if you use the built-in behavior that assigns table permissions automatically

The RBAC helper also relies on control-plane discovery:

- `AZURE_SUBSCRIPTION_ID` must be set.
- If `resource_group_name` is not provided, the service attempts to discover the storage account’s resource group by listing storage accounts in the subscription.

## Storage RBAC model (implemented)

The RBAC enforcement and assignment logic is implemented in:

- `agents/rbac_auth.py` (authorization checks)
- `agents/rbac_helper.py` (role assignment utilities)

Built-in role IDs referenced by the code:

- `Storage Blob Data Contributor`
- `Storage Blob Data Owner`
- `Storage Table Data Contributor`
- `Storage Table Data Reader`

### Minimum roles for end-user / group (data-plane)

| Resource | Scope | Minimum role(s) | Why |
|---|---|---|---|
| Blob container per `app_id` | Container scope | `Storage Blob Data Contributor` (or `Storage Blob Data Owner`) | Required for container-level access checks and blob read/write operations. |
| Tables for an app (e.g., `AppDetails{app_id}` after sanitization) | Table scope | `Storage Table Data Contributor` | Required for table-level access checks and for reading/writing app data and operation tracking entities. |

Notes:

- The unified validation checks container existence and container RBAC first; if the container doesn’t exist, requests fail with `container_not_found` (it does not auto-create containers as part of validation).
- Container RBAC checks are performed by listing role assignments at the container resource scope. For compatibility with the current validator, prefer assigning blob roles at the container scope (not only at broader parent scopes).
- For tables, the system checks for the template-derived tables and (for most endpoints) expects them to already exist.
- Table RBAC checks are performed at each table’s resource scope; for compatibility with the current validator, prefer assigning table roles at the table scope.
- If a caller lacks container permission but has table permissions, the service may attempt to remove “orphaned” table role assignments as a cleanup step.

### Table naming (important for RBAC scopes)

Table names are derived from a fixed set of prefixes and the `app_id`, then sanitized to meet Azure Table naming rules (alphanumeric only; must start with a letter; length 3–63). This means:

- If your `app_id` contains hyphens or other non-alphanumeric characters, the corresponding table names will not be a literal concatenation of `{prefix}{app_id}`.
- Role assignments for tables must match the actual sanitized table resource names used by the service.

### Minimum roles for the runtime identity

| Resource | Scope | Minimum role(s) | Why |
|---|---|---|---|
| Storage account (Blob) | Storage account scope | `Storage Blob Data Contributor` | Runtime reads/writes blobs. |
| Storage account (Tables) | Storage account scope | `Storage Table Data Contributor` | Runtime reads/writes tables. |
| Role assignment operations | Subscription, resource group, or resource scope | `Owner` or `User Access Administrator` (or equivalent custom role) | Required when the runtime assigns RBAC roles to users/groups (roleAssignments write). |

Notes:

- The built-in RBAC helper uses ARM management APIs (Authorization + Storage management). Ensure the runtime identity also has sufficient read permissions to discover storage accounts/resource groups if you rely on auto-discovery.

## Azure AI Search RBAC (implemented by scripts + tests)

Deployment scripts and tests reference these roles:

- `Search Index Data Reader` (query index)
- `Search Index Data Contributor` (write/index documents)

Recommended minimums:

| Component | Scope | Minimum role(s) |
|---|---|---|
| Insights API / agents that query Search | Search service scope | `Search Index Data Reader` |
| Indexer service | Search service scope | `Search Index Data Contributor` |

## Azure AI / Azure OpenAI roles (implemented by scripts)

Deployment scripts reference these roles on the AI/Foundry service scope:

- `Cognitive Services OpenAI User`
- `Cognitive Services User`

Recommended minimums:

| Component | Scope | Minimum role(s) |
|---|---|---|
| Runtime identity calling Azure OpenAI | Azure AI / OpenAI resource scope | `Cognitive Services OpenAI User` |
| Runtime identity accessing general AI services | Azure AI resource scope | `Cognitive Services User` |

## Recommended scoping and least privilege

1. **Prefer resource-level scopes** over subscription scopes.
2. For Storage:
	- Grant end-user roles at the **container** scope (Blob) and **table** scope (Tables) where possible.
   - Grant runtime identity roles at the **storage account** scope.
3. For Search:
   - Grant query identities **Reader**; grant the indexer identity **Contributor**.
4. Avoid using account keys or admin keys in hosted environments.

## Propagation delay

RBAC role assignment changes can take time to propagate.

- The codebase expects table-level RBAC assignments may take up to ~10 minutes to become effective.
- If you see intermittent `access_denied` immediately after role assignment, wait and retry.

## Validation steps

### Validate Storage access

0. Confirm `AZURE_SUBSCRIPTION_ID` is set for environments where the service performs RBAC checks/assignments.
1. Confirm the per-app container exists.
2. Ensure the caller identity has `Storage Blob Data Contributor` (or Owner) on that container.
3. Ensure the required per-app tables exist (or initialize via the create/init flow).
4. Ensure the caller identity has `Storage Table Data Contributor` on the relevant per-app tables (table scope).

### Validate Search access

1. Confirm `AZURE_SEARCH_ENDPOINT` is configured.
2. Ensure the runtime identity has `Search Index Data Reader` (query) and/or `Search Index Data Contributor` (indexer) on the Search service.

### Validate OpenAI access

1. Confirm `AZURE_OPENAI_ENDPOINT`, deployment name, and API version are configured.
2. Ensure the runtime identity has `Cognitive Services OpenAI User` on the AI/OpenAI resource.


