# Environment Setup Scripts

This README explains how to run the scripts located in this folder.

## Table of Contents

- [Scripts](#scripts)
  - [export\_migration\_agent\_tables.py](#export_migration_agent_tablespy)
    - [Requirements](#requirements)
    - [Prerequisites](#prerequisites)
    - [Usage](#usage)
    - [Parameters](#parameters)
    - [Default Tables Exported](#default-tables-exported)
  - [import\_migration\_agent\_tables.py](#import_migration_agent_tablespy)
    - [Requirements](#requirements-1)
    - [Prerequisites](#prerequisites-1)
    - [Usage](#usage-1)
    - [Parameters](#parameters-1)
    - [Default Tables Imported](#default-tables-imported)
  - [copy\_github\_env\_vars.py](#copy_github_env_varspy)
    - [Requirements](#requirements-2)
    - [Finding Your Repository Owner and Name](#finding-your-repository-owner-and-name)
    - [Usage](#usage-2)
    - [Parameters](#parameters-2)
    - [GitHub Token Permissions](#github-token-permissions)
    - [Creating a GitHub Personal Access Token](#creating-a-github-personal-access-token)
    - [Important Notes](#important-notes)
    - [Common Errors](#common-errors)
    - [Example Output - Import from .env File](#example-output---import-from-env-file)
    - [Example Output - List Repository Variables](#example-output---list-repository-variables)
    - [Example Output - List Environments](#example-output---list-environments)
    - [Example Output - Copy Variables](#example-output---copy-variables)
  - [setup\_virtual\_directories.py](#setup_virtual_directoriespy)
    - [Requirements](#requirements-3)
    - [Prerequisites](#prerequisites-2)
    - [Usage](#usage-3)
    - [Parameters](#parameters-3)
    - [Virtual Directory Structure](#virtual-directory-structure)
- [Security Best Practices](#security-best-practices)

## Scripts

### export_migration_agent_tables.py

Exports AI-First Migration template tables from Azure Table Storage to CSV files using Azure AD identity. This script reads the template tables used by the migration agents and exports each table to a CSV file.

**Requirements:**
```bash
pip install azure-identity azure-data-tables
```

**Prerequisites:**
- Azure CLI logged in (`az login`)
- The signed-in identity needs `Storage Table Data Reader` access on the Storage Account

**Usage:**

```bash
# Basic export to default directory (./TableExports)
python export_migration_agent_tables.py \
    --storage-account mystorageaccount \
    --resource-group my-resource-group

# Export to custom directory
python export_migration_agent_tables.py \
    --storage-account mystorageaccount \
    --resource-group my-resource-group \
    --output-dir ./my-exports

# Export with table prefix (e.g., dev-prefixed tables)
python export_migration_agent_tables.py \
    --storage-account mystorageaccount \
    --resource-group my-resource-group \
    --output-dir ./TableExports \
    --table-prefix dev

# Verbose output for debugging
python export_migration_agent_tables.py \
    --storage-account mystorageaccount \
    --resource-group my-resource-group \
    --verbose
```

**Parameters:**

- `--storage-account`: Azure Storage account name **(required)**
- `--resource-group`: Resource group name (optional, helps with manifest tracking)
- `--output-dir`: Output directory for CSV files (default: `./TableExports`)
- `--table-prefix`: Prefix to add to table names when querying
- `--verbose`: Enable verbose/debug logging

**Default Tables Exported:**

- `AppDetailsTemplate`
- `IntegrationDependencyTemplate`
- `MsSqlDBTemplate`
- `OracleDBTemplate`
- `InfrastructureDetails`

---

### import_migration_agent_tables.py

Imports AI-First Migration template tables from CSV files into Azure Table Storage using Azure AD identity. This script takes the CSV files produced by `export_migration_agent_tables.py` and restores their contents into Azure Table Storage.

**Requirements:**
```bash
pip install azure-identity azure-data-tables
```

**Prerequisites:**
- Azure CLI logged in (`az login`)
- The signed-in identity needs `Storage Table Data Contributor` or higher access on the Storage Account
- CSV files with `PartitionKey` and `RowKey` columns

> **Note:** You may need to temporarily enable network public access to the Storage Account in order to import these files from your workstation.

**Usage:**

```bash
# Basic import from default directory (./TableExports)
python import_migration_agent_tables.py \
    --storage-account mystorageaccount \
    --resource-group my-resource-group \
    --input-dir ./template_tables

# Import with overwrite (replace existing entities)
python import_migration_agent_tables.py \
    --storage-account mystorageaccount \
    --resource-group my-resource-group \
    --input-dir ./template_tables \
    --overwrite

# Import with table prefix (e.g., dev-prefixed tables)
python import_migration_agent_tables.py \
    --storage-account mystorageaccount \
    --resource-group my-resource-group \
    --input-dir ./template_tables \
    --table-prefix dev \
    --overwrite

# Verbose output for debugging
python import_migration_agent_tables.py \
    --storage-account mystorageaccount \
    --resource-group my-resource-group \
    --input-dir ./template_tables \
    --verbose
```

**Parameters:**

- `--storage-account`: Azure Storage account name **(required)**
- `--resource-group`: Resource group name (optional, helps with manifest tracking)
- `--input-dir`: Input directory containing CSV files (default: `./TableExports`)
- `--table-prefix`: Prefix to add to table names when creating/updating
- `--overwrite`: Overwrite existing entities if they exist
- `--verbose`: Enable verbose/debug logging

**Default Tables Imported:**

- `AppDetailsTemplate`
- `IntegrationDependencyTemplate`
- `MsSqlDBTemplate`
- `OracleDBTemplate`
- `InfrastructureDetails`
- `K8Stemplate`

---

### copy_github_env_vars.py

Copies environment variables from one GitHub environment to another.

**Requirements:**
```bash
pip install requests
```

**Finding Your Repository Owner and Name:**

The repository owner and name are in your GitHub URL:
- URL format: `https://github.com/OWNER/REPO`
- Example: `https://github.com/mcaps-microsoft/ai-first-migrate-insights-agent`
  - Owner: `mcaps-microsoft`
  - Repo: `ai-first-migrate-insights-agent`

You can also run: `git remote -v` to see the remote URL.

**Usage:**

```bash
# Step 1: List repository-level variables (recommended starting point)
export GITHUB_TOKEN=ghp_xxxxxxxxxxxxx
python copy_github_env_vars.py \
  --owner mcaps-microsoft \
  --repo ai-first-migrate-insights-agent \
  --list-repo-vars

# Step 2: List GitHub Environments (if configured)
python copy_github_env_vars.py \
  --owner mcaps-microsoft \
  --repo ai-first-migrate-insights-agent \
  --list-environments

# Step 3: Import variables from .env file to repository
python copy_github_env_vars.py \
  --owner mcaps-microsoft \
  --repo ai-first-migrate-insights-agent \
  --from-env-file .env

# Step 4: Import variables from .env file to specific environment
python copy_github_env_vars.py \
  --owner mcaps-microsoft \
  --repo ai-first-migrate-insights-agent \
  --from-env-file .env \
  --target production

# Step 5: Copy environment variables (if environments exist)
python copy_github_env_vars.py \
  --owner mcaps-microsoft \
  --repo ai-first-migrate-insights-agent \
  --source production \
  --target staging

# Dry run (preview .env import without applying)
python copy_github_env_vars.py \
  --owner mcaps-microsoft \
  --repo ai-first-migrate-insights-agent \
  --from-env-file .env \
  --dry-run

# Save results to JSON file
python copy_github_env_vars.py \
  --owner mcaps-microsoft \
  --repo ai-first-migrate-insights-agent \
  --from-env-file .env \
  --output results.json
```

**Parameters:**

- `--owner`: GitHub repository owner (organization or username) **(required)**
  - Find in GitHub URL: `github.com/OWNER/repo`
  - Example: `mcaps-microsoft` (use hyphen, not underscore)
- `--repo`: Repository name **(required for most operations)**
  - Find in GitHub URL: `github.com/owner/REPO`
  - Example: `ai-first-migrate-insights-agent`
- `--source`: Source environment name (required for environment copy)
- `--target`: Target environment name (for environment copy or .env import to environment)
- `--token`: GitHub Personal Access Token (or set `GITHUB_TOKEN` env var)
- `--from-env-file`: Path to .env file to import variables from
- `--list-repo-vars`: List repository-level variables and secrets
- `--list-environments`: List GitHub Environments (if configured)
- `--dry-run`: Preview changes without applying them
- `--output`: Save results to a JSON file

**GitHub Token Permissions:**

The Personal Access Token requires the following scopes:
- `repo` - Full control of private repositories
- `workflow` - Update GitHub Action workflows **(REQUIRED for variables/secrets)**

⚠️ **Important**: Both scopes are required. The `workflow` scope is necessary to read/write GitHub Actions variables and secrets.

**Creating a GitHub Personal Access Token:**

1. Go to GitHub Settings → Developer settings → Personal access tokens → Tokens (classic)
2. Click "Generate new token" → "Generate new token (classic)"
3. Give it a descriptive name (e.g., "Environment Variables Copy Script")
4. **Select BOTH required scopes:**
   - ✅ `repo` - Full control of private repositories
   - ✅ `workflow` - Update GitHub Action workflows
5. Click "Generate token"
6. Copy the token immediately (you won't be able to see it again)

**Important Notes:**

⚠️ **Secrets Limitation**: GitHub API does not allow reading secret values. The script will:
- List all secret names found in the source environment
- Provide instructions for manual secret copying
- Copy only non-secret environment variables

✅ **Variables**: All non-secret environment variables will be copied with their values.

**Comm3 Forbidden - Missing Token Permissions**
```
ERROR - 403 Client Error: Forbidden for url: https://api.github.com/repos/.../actions/variables
```
**Solution**: Your token is missing the `workflow` scope. Generate a new token with BOTH `repo` AND `workflow` scopes.
1. Go to https://github.com/settings/tokens
2. Click your token or create a new one
3. Ensure both `repo` and `workflow` are checked
4. Update token and regenerate if needed

❌ **40on Errors:**

❌ **404 Not Found - Wrong Owner/Repo Name**
```
ERROR - Repository 'bahramr_microsoft/ai-first-migrate-insights-agent' not found
```
**Solution**: Check your GitHub URL. Use hyphens, not underscores. Example: `mcaps-microsoft` not `mcaps_microsoft`

❌ **No Environments Found**
```
WARNING - No GitHub Environments configured
```
**Solution**: Use `--list-repo-vars` instead to see repository-level variables.

**Example Output - Import from .env File:**

```
2026-01-15 15:00:00 - INFO - ============================================================
2026-01-15 15:00:00 - INFO - IMPORTING VARIABLES TO REPOSITORY: mcaps-microsoft/ai-first-migrate-insights-agent
2026-01-15 15:00:00 - INFO - ============================================================
2026-01-15 15:00:00 - INFO - Parsing .env file: .env
2026-01-15 15:00:01 - INFO - Found 29 variables in .env file
2026-01-15 15:00:01 - INFO -
Uploading 29 variables to repository: mcaps-microsoft/ai-first-migrate-insights-agent
2026-01-15 15:00:02 - INFO - ✅ Created variable: API_KEY
2026-01-15 15:00:03 - INFO - ✅ Updated variable: DATABASE_URL
2026-01-15 15:00:04 - INFO - ✅ Created variable: AZURE_SUBSCRIPTION_ID
...

============================================================
UPLOAD SUMMARY
============================================================
Source: .env
Target Repository: mcaps-microsoft/ai-first-migrate-insights-agent
Dry Run: False

Variables:
   Total: 29
   Uploaded: 29
   Failed: 0

Uploaded Variables:
   ✅ API_KEY
   ✅ DATABASE_URL
   ✅ AZURE_SUBSCRIPTION_ID
   ...
============================================================
```

**Example Output - List Repository Variables:**

```
2026-01-15 10:30:00 - INFO - Fetching repository variables from: mcaps-microsoft/ai-first-migrate-insights-agent
2026-01-15 10:30:01 - INFO - Found 25 repository variables
============================================================
REPOSITORY VARIABLES IN mcaps-microsoft/ai-first-migrate-insights-agent
============================================================

📋 Variables (25):
   AZURE_SUBSCRIPTION_ID = 12345678-1234-1234-1234-123456789abc
   AZURE_REGION = eastus
   RESOURCE_GROUP_NAME = rg-ai-lz-bicep-int-eastus2-v1_9_7
   ...

🔒 Secrets (5):
   AZURE_CLIENT_SECRET = <hidden>
   GITHUB_TOKEN = <hidden>
   ...

============================================================
Total Variables: 25
Total Secrets: 5
============================================================
```

**Example Output - List Environments:**

```
2026-01-15 10:30:00 - INFO - Fetching environments for mcaps-microsoft/ai-first-migrate-insights-agent
2026-01-15 10:30:01 - INFO - Found 3 environments
============================================================
ENVIRONMENTS IN myorg/myrepo
============================================================

📦 production
   ID: 123456
   Protection Rules: 2
   Deployment Policy: {'protected_branches': True, 'custom_branch_policies': False}

📦 staging
   ID: 123457
   Protection Rules: 1

📦 development
   ID: 123458

============================================================
Total Environments: 3
============================================================
```

**Example Output - Copy Variables:**

```
2026-01-15 10:30:00 - INFO - Fetching variables from environment: production
2026-01-15 10:30:01 - INFO - Found 15 variables in production
2026-01-15 10:30:01 - INFO - Fetching secret names from environment: production
2026-01-15 10:30:02 - INFO - Found 3 secrets in production

Copying 15 variables from 'production' to 'staging'...
2026-01-15 10:30:03 - INFO - ✅ Created variable: API_BASE_URL
2026-01-15 10:30:04 - INFO - ✅ Updated variable: DATABASE_HOST
2026-01-15 10:30:05 - INFO - ✅ Created variable: CACHE_ENABLED

============================================================
⚠️  SECRETS DETECTED (Cannot be copied via API)
============================================================
The following 3 secrets exist in 'production':
   - DATABASE_PASSWORD
   - API_SECRET_KEY
   - OAUTH_CLIENT_SECRET

You must manually copy secret values:
   1. Go to: https://github.com/myorg/myrepo/settings/environments
   2. Open environment: staging
   3. Add each secret with its value from production
============================================================

============================================================
COPY SUMMARY
============================================================
Source Environment: production
Target Environment: staging
Dry Run: False

Variables:
   Total: 15
   Copied: 15
   Failed: 0

Secrets Found: 3

Copied Variables:
   ✅ API_BASE_URL
   ✅ DATABASE_HOST
   ✅ CACHE_ENABLED
   ...
============================================================
```

---

### setup_virtual_directories.py

Creates virtual directory structure in Azure Blob Storage for the Insights Agent API.

**Requirements:**
```bash
pip install azure-storage-blob azure-identity
```

Or using the Python launcher on Windows:
```powershell
py -m pip install azure-storage-blob azure-identity
```

**Prerequisites:**
- Azure CLI logged in (`az login`) OR
- Environment variables for service principal authentication OR
- Connection string for storage account

**Usage:**

```bash
# Using command line arguments
python setup_virtual_directories.py \
    --app-id my-application-001 \
    --storage-account mystorageaccount

# Using environment variables
export AZURE_STORAGE_ACCOUNT_NAME="mystorageaccount"
python setup_virtual_directories.py --app-id my-application-001

# Using connection string
python setup_virtual_directories.py \
    --app-id my-application-001 \
    --connection-string "DefaultEndpointsProtocol=https;..."

# Dry run (preview without creating)
python setup_virtual_directories.py --app-id my-app --dry-run

# Create directories for specific endpoints only
python setup_virtual_directories.py \
    --app-id my-app-001 \
    --endpoints /generateDesign /analyzeCode
```

**Parameters:**

- `--app-id`: Application ID (becomes the container name) **(required)**
- `--storage-account`: Azure Storage account name (or set `AZURE_STORAGE_ACCOUNT_NAME` env var)
- `--connection-string`: Azure Storage connection string (or set `AZURE_STORAGE_CONNECTION_STRING` env var)
- `--dry-run`: Preview changes without creating directories
- `--endpoints`: Specific endpoints to set up (e.g., `/generateDesign /analyzeCode`)
- `--verbose`: Enable verbose logging

**Virtual Directory Structure:**

The script creates the following structure per app:

```
[app-id]/
├── design/input/              ← /generateDesign
├── design/output/
├── asr/input/                 ← /generateAssessmentReport
├── asr/output/
├── app-planning/input/        ← /generateAppPlan
├── app-planning/output/
├── architecture-analyzer/     ← /analyzeArchitecture
├── code-analyzer/             ← /analyzeCode
├── kubernetes-discovery/      ← /discoverKubernetes
└── responder/                 ← /runAnalysis
```

---

## Security Best Practices

1. **Never commit tokens to version control**
2. **Use environment variables for sensitive data**
3. **Rotate tokens regularly**
4. **Use minimal required permissions**
5. **Delete tokens when no longer needed**
6. **Review copied variables before applying in production**
