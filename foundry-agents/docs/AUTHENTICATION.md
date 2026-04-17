<!--
DOC_INTENT:
	surface: foundry
	page: AUTHENTICATION
	purpose: Explain how the Foundry agents authenticate to Azure resources and (if applicable) how callers authenticate to the agent API.
	audience: Developers, operators
	should_cover:
		- Local dev auth options
		- Hosted auth (managed identity/service principal)
		- Token/credential flow at a high level
		- Common auth failure modes and fixes
	should_not_cover:
		- Tenant-specific secrets or screenshots with sensitive IDs
	source_refs:
		- cockpit-docs/docs/authentication.md (reference style only)
-->

# Authentication

> **Last validated**: 2026-01-29

This document explains how the Foundry agents runtime authenticates to Azure services and how requests to the Foundry API are authorized.

## Summary

- **Users** are Microsoft Entra ID identities.
- **Service-to-Azure** authentication is designed to use **managed identity** (and the code primarily uses `DefaultAzureCredential` / `ManagedIdentityCredential`).
- **API authorization** is enforced by checking whether the caller’s Entra user (or group) has Azure RBAC access to the application’s Storage container and UAQ tables.
- **API caller authentication** is expected to be enforced by an upstream gateway (for example **APIM**) or another trusted caller; the API does not currently validate bearer tokens itself.

## Trust boundaries

There are two distinct identity contexts:

1) **The caller (human / client app)**
	 - Represented by a Microsoft Entra user object ID and/or a group object ID.
	 - Used for authorization decisions (RBAC checks).

2) **The runtime identity (service)**
	 - A managed identity (recommended) or service principal used by the API/agents to:
		 - query Azure RBAC role assignments
		 - create or remove Azure RBAC role assignments (during onboarding/repair flows)
		 - access Azure services needed by agents (Storage, Key Vault, Azure AI Foundry dependencies, etc.)

## API caller authentication (north-south)

### Design intent

All users are authenticated through their Microsoft Entra ID accounts.

Access to solution endpoints can be authorized at two levels:

- **APIM** (optional): if the customer chooses to implement APIM in the landing zone.
- **Within the application**: by checking user access to the Storage containers and tables.

### Current implementation

The Foundry API does **not** currently validate `Authorization: Bearer ...` tokens.

Instead, endpoints accept the caller identity context as explicit request inputs:

- Many endpoints use request bodies containing `user_object_id` and/or `group_object_id`.
- Some read these values from headers (for example operation status endpoints):
	- `X-User-Object-Id`
	- `X-Group-Object-Id`
	- `X-Storage-Account`
	- `X-Resource-Group` (optional)

Because these IDs can be spoofed if the API is publicly reachable, **you must deploy the API behind an authenticated gateway** (for example APIM with Entra ID) or another trusted caller that derives these IDs from validated tokens.

## Authorization model (RBAC)

Authorization is evaluated by verifying Azure RBAC permissions on the application’s data plane:

- **Blob container access** (application container named by `app_id`)
	- Required roles checked: `Storage Blob Data Contributor` or `Storage Blob Data Owner`
- **Table access** (UAQ tables and derived tables)
	- Required role checked: `Storage Table Data Contributor`
	- The implementation supports **table-level scopes** for granular RBAC.

The authorization logic is implemented in:

- `user_authentication(...)` and `validate_container_only_access(...)` in `foundry-agents/agents/rbac_auth.py`
- `RBACHelper` in `foundry-agents/agents/rbac_helper.py`

### Inputs required by the workflow

The authorization workflow expects the following parameters (as request body fields or headers, depending on endpoint):

- `app_id`: application ID (also used as the blob container name)
- `azure_region`: region for the Insights/assessment resources (not required for RBAC checks, but part of request context)
- `resource_group_name`: resource group containing the Storage Account (optional in some flows; the service can auto-discover)
- `storage_account_name`: Azure Storage Account name
- `user_object_id` (required if `group_object_id` is not provided): Entra object ID of the user
- `group_object_id` (required if `user_object_id` is not provided): Entra object ID of a security group

### High-level workflow

For most endpoints, the API performs a unified validation flow:

1) **Validate identity inputs**
	 - At least one of `user_object_id` or `group_object_id` must be present.

2) **Validate the application container exists**
	 - If the blob container named `app_id` does not exist: return `404`.

3) **Validate container RBAC access**
	 - Check role assignments at the container scope:
		 - `/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Storage/storageAccounts/<account>/blobServices/default/containers/<app_id>`
	 - If neither the user nor group has the required blob role: return `403`.
	 - If tables exist, the service may also remove “orphaned” table permissions when container access is missing.

4) **Validate UAQ / derived tables exist**
	 - Tables are expected to exist for “operation” endpoints.
	 - For the “create” onboarding endpoint, missing tables can be created by cloning templates.

5) **Validate table RBAC access and repair if needed**
	 - For each expected table, check role assignments at the table scope:
		 - `/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Storage/storageAccounts/<account>/tableServices/default/tables/<table>`
	 - If the caller lacks access to one or more tables, the service can assign table-level `Storage Table Data Contributor`.
	 - **Propagation delay**: table-level RBAC assignments can take up to ~10 minutes to become effective.

### Special case: container-only validation

Some flows validate container access without table checks (for example shared/special containers). This uses `validate_container_only_access(...)`.

## How the runtime authenticates to Azure (east-west)

### Hosted (recommended): managed identity

Most components use Azure Identity credentials that support managed identity:

- `DefaultAzureCredential(exclude_shared_token_cache_credential=True)` is used broadly in agent code.
- Some hosted components use `ManagedIdentityCredential()` directly for better reliability in container environments.

Typical requirements for the runtime identity:

- **To query and manage RBAC assignments**: permissions to read/write role assignments at the relevant scopes.
	- In practice this often means granting the runtime identity `User Access Administrator` or `Owner` at the resource group or subscription scope containing the Storage Account.
- **To access Storage data plane**: the runtime identity may also need data-plane roles depending on what the agent does (Blob/Table roles).
- **To read Key Vault secrets (optional)**: assign an appropriate Key Vault role (for example `Key Vault Secrets User`) when Key Vault is used.

### Local development

The repo primarily relies on Azure Identity’s standard developer flows:

- **Azure CLI**: `az login` then run the API locally.
- Some components intentionally prefer `AzureCliCredential` in local/dev to avoid managed identity timeouts.

Minimum required environment configuration for RBAC operations:

- `AZURE_SUBSCRIPTION_ID` is required by `RBACHelper`.

## Shared keys / connection strings

### Design intent

Azure services should communicate through managed identities only, and the use of shared keys should be disabled.

### Current code behavior

There are a few key-based fallbacks present in the repo that are intended for bootstrap, testing, or emergency troubleshooting:

- The RBAC auth module supports bypassing RBAC checks if `AZURE_STORAGE_ACCOUNT_KEY` is set.
- Some environment-setup scripts accept `AZURE_STORAGE_CONNECTION_STRING`.
- The indexer supports an Azure AI Search API key via `AzureKeyCredential` (with managed identity preferred).
- The indexer can also operate against a blob account URL containing a SAS token.

Operational guidance:

- In production, keep `AZURE_STORAGE_ACCOUNT_KEY` and connection strings **unset**.
- Prefer managed identity for Storage, Search, and Azure OpenAI.
- If your landing zone disables shared keys at the Storage Account level, these fallbacks will fail (by design).

## Token scopes (high level)

Depending on which Azure services are used in a flow, the runtime identity will request tokens for one or more standard scopes:

- Azure Resource Manager: `https://management.azure.com/.default`
- Azure Storage: `https://storage.azure.com/.default`
- Azure Key Vault: `https://vault.azure.net/.default`
- Azure Cognitive Services (used for Azure OpenAI auth flows): `https://cognitiveservices.azure.com/.default`
- Azure AI Search: `https://search.azure.com/.default`

## Common failure modes and fixes

- **`AZURE_SUBSCRIPTION_ID environment variable must be set`**
	- The runtime needs this to build role assignment scopes. Set it in your environment.

- **403: access denied to container**
	- The caller’s user/group object ID does not have `Storage Blob Data Contributor` (or `Storage Blob Data Owner`) on the app container.
	- Fix by granting the correct RBAC role at the container scope.

- **403: access denied to tables / missing table permissions**
	- The caller lacks `Storage Table Data Contributor` on one or more tables.
	- If role assignments were just created, wait up to ~10 minutes for propagation.

- **404: container not found**
	- The `app_id` container does not exist in the provided Storage Account.
	- Create the container (typically via the onboarding workflow) and re-try.

- **Role assignment create failures**
	- The runtime identity lacks permission to create Azure RBAC assignments.
	- Grant the runtime identity `User Access Administrator` or `Owner` at an appropriate scope.

- **Local dev hangs/timeouts when using `DefaultAzureCredential`**
	- Some environments attempt managed identity endpoints first.
	- Prefer `az login`, ensure you’re authenticated, and consider using `AzureCliCredential` where supported.


