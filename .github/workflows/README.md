# GitHub Actions Workflows

This directory contains GitHub Actions workflows for automating the deployment of the AI Landing Zone as well as the solution application components.

## Table of Contents

- [GitHub Actions Workflows](#github-actions-workflows)
  - [Table of Contents](#table-of-contents)
  - [Self-Hosted Runner Setup for Deployments](#self-hosted-runner-setup-for-deployments)
    - [1. Network \& VM Prerequisites](#1-network--vm-prerequisites)
    - [2. Install Required Software](#2-install-required-software)
    - [3. Create the GitHub Runner](#3-create-the-github-runner)
  - [Deployment Workflows Setup](#deployment-workflows-setup)
    - [Step 1: Create an Azure AD App Registration](#step-1-create-an-azure-ad-app-registration)
    - [Step 2: Configure Federated Credentials](#step-2-configure-federated-credentials)
      - [Option 1: Add Federated Credentials from the Azure Portal](#option-1-add-federated-credentials-from-the-azure-portal)
      - [Option 2: Add Federated Credentials via Azure CLI](#option-2-add-federated-credentials-via-azure-cli)
    - [Step 3: Grant Azure RBAC Permissions](#step-3-grant-azure-rbac-permissions)
    - [Step 4: Store Secrets in GitHub Repository](#step-4-store-secrets-in-github-repository)
    - [Step 5: Setup Environment Variables](#step-5-setup-environment-variables)
    - [Step 6: Verify Configuration](#step-6-verify-configuration)
    - [Step 7: Test the Workflows](#step-7-test-the-workflows)
    - [Security Considerations](#security-considerations)
    - [Troubleshooting](#troubleshooting)
  - [Bicep Deployment (`deploy-ailz-bicep.yml`)](#bicep-deployment-deploy-ailz-bicepyml)
  - [Terraform Deployment (`deploy-ailz-terraform.yml`)](#terraform-deployment-deploy-ailz-terraformyml)
  - [Power Platform Networking (Bicep) (`deploy-powerplatform-bicep.yml`)](#power-platform-networking-bicep-deploy-powerplatform-bicepyml)
  - [Power Platform Networking (Terraform) (`deploy-powerplatform-terraform.yml`)](#power-platform-networking-terraform-deploy-powerplatform-terraformyml)
  - [IaC Validation Workflows](#iac-validation-workflows)
    - [Bicep Validation (`bicep-validation.yml`)](#bicep-validation-bicep-validationyml)
  - [Regional Architecture](#regional-architecture)
  - [IaC Workflows Inputs](#iac-workflows-inputs)
    - [Bicep Deployment Inputs](#bicep-deployment-inputs)
    - [Terraform Deployment Inputs](#terraform-deployment-inputs)
      - [Advanced options JSON schema](#advanced-options-json-schema)
  - [IaC Troubleshooting](#iac-troubleshooting)
    - [Bicep: HTTP 413 (Request Entity Too Large)](#bicep-http-413-request-entity-too-large)
    - [Terraform: Authentication Failures](#terraform-authentication-failures)
    - [Secondary VNet Toggle Not Detected](#secondary-vnet-toggle-not-detected)
  - [Best Practices](#best-practices)
  - [Additional Resources](#additional-resources)

---

## Self-Hosted Runner Setup for Deployments

This section explains how to configure a GitHub self-hosted Linux runner inside an Azure Virtual Network to run the different workflows that deploy our services to the AI Landing Zone.

### 1. Network & VM Prerequisites

If not already deployed, provision a VM (Ubuntu recommended) with:

- Outbound access to: `login.microsoftonline.com`, `management.azure.com`, `*.azurecr.io`, `*.blob.core.windows.net`, `*.containerapps.io` (or regional FQDN), `sts.windows.net`.
- Optional private endpoints (if ACR / Container Apps environment are private); ensure DNS resolves internal FQDNs.
- Minimum size: Standard_D2s_v3 (2 vCPU / 8 GB) is sufficient for container builds via ACR Build.
- Attach a System-Assigned Managed Identity if you are not using Federated Credentials (OIDC) .

### 2. Install Required Software

Run on the VM:

```bash
sudo apt-get update -y
sudo apt-get install -y curl git unzip jq build-essential
curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash
sudo az extension add --name containerapp --upgrade --yes
sudo az extension add --name log-analytics --upgrade --yes || true
```

### 3. Create the GitHub Runner

In GitHub repo settings:

1. Settings → Actions → Runners → New self-hosted runner.
2. Choose Linux / x64.
3. Follow provided script (example):

```bash
mkdir -p ~/actions-runner && cd ~/actions-runner
curl -o actions-runner-linux-x64.tar.gz -L https://github.com/actions/runner/releases/download/v2.316.0/actions-runner-linux-x64-2.316.0.tar.gz
tar xzf actions-runner-linux-x64.tar.gz
./config.sh --url https://github.com/mcaps-microsoft/ai-first-migrate-insights-agent --token <REPO_TOKEN> --labels "self-hosted,linux" --work _work
sudo ./svc.sh install
sudo ./svc.sh start
```

## Deployment Workflows Setup

### Step 1: Create an Azure AD App Registration

```bash
# Create the app registration
az ad app create --display-name "github-client"

# Get the Application (client) ID
APP_ID=$(az ad app list --display-name "github-client" --query "[0].appId" -o tsv)
echo "Application ID: $APP_ID"

# Create a service principal for the app
az ad sp create --id $APP_ID

# Get the Object ID of the service principal (needed for role assignments)
SP_OBJECT_ID=$(az ad sp list --display-name "github-client" --query "[0].id" -o tsv)
echo "Service Principal Object ID: $SP_OBJECT_ID"
```

### Step 2: Configure Federated Credentials
  
The recommended authentication method is Federated Credentials (OIDC) over Client Secrets because of the following reasons:

- ✅ No secrets to rotate or expire
- ✅ Short-lived tokens (1 hour max)
- ✅ Scoped to specific repository/branch/environment
- ✅ Better audit trail via Entra ID sign-in logs
- ✅ No risk of secret leakage in logs

There are two options to setup Federated Credentials: from the Azure portal or from the Azure CLI.

#### Option 1: Add Federated Credentials from the Azure Portal

**Allow Any Branch (Recommended for Development)**

1. Navigate to [Azure Portal](https://portal.azure.com) → **Microsoft Entra ID** → **App registrations**
2. Search for and select your app: `github-client`
3. In the left menu, select **Certificates & secrets**
4. Click the **Federated credentials** tab
5. Click **+ Add credential**
6. Configure the credential:
   - **Federated credential scenario**: Select `GitHub Actions deploying Azure resources`
   - **Organization**: `mcaps-microsoft`
   - **Repository**: `ai-first-migrate-insights-agent`
   - **Entity type**: Select **Environment**
   - **GitHub environment name**: Leave blank or use `*` (allows any branch/environment)
   - **Name**: `github-actions-all-branches` (descriptive name)
   - **Description**: `Federated credential for GitHub Actions workflow from any branch`
7. Click **Add**

**Allow Specific Branch (More Restrictive)**

- **Entity type**: Select **Branch**
- **GitHub branch name**: `bahramr/testing` (or `main` for production)
- **Name**: `github-actions-bahramr-intake-agent`

#### Option 2: Add Federated Credentials via Azure CLI

**Allow Any Branch (Simplest - works from all branches)**

```bash
# Create a single credential that works for any branch or environment
az ad app federated-credential create \
  --id $APP_ID \
  --parameters '{
    "name": "github-actions-all-branches",
    "issuer": "https://token.actions.githubusercontent.com",
    "subject": "repo:mcaps-microsoft/ai-first-migrate-insights-agent:environment:*",
    "description": "Federated credential for deployment from any branch",
    "audiences": ["api://AzureADTokenExchange"]
  }'
```

**Allow Specific Branches (More Control)**

```bash
# For branch-based deployment (current development branch)
az ad app federated-credential create \
  --id $APP_ID \
  --parameters '{
    "name": "github-actions-bahramr-intake-agent",
    "issuer": "https://token.actions.githubusercontent.com",
    "subject": "repo:mcaps-microsoft/ai-first-migrate-insights-agent:ref:refs/heads/bahramr/testing",
    "description": "Federated credential for intake API deployment workflow",
    "audiences": ["api://AzureADTokenExchange"]
  }'

# For main branch (production)
az ad app federated-credential create \
  --id $APP_ID \
  --parameters '{
    "name": "github-actions-main",
    "issuer": "https://token.actions.githubusercontent.com",
    "subject": "repo:mcaps-microsoft/ai-first-migrate-insights-agent:ref:refs/heads/main",
    "description": "Federated credential for production deployment",
    "audiences": ["api://AzureADTokenExchange"]
  }'

# For pull request events (optional - for testing)
az ad app federated-credential create \
  --id $APP_ID \
  --parameters '{
    "name": "github-actions-pull-request",
    "issuer": "https://token.actions.githubusercontent.com",
    "subject": "repo:mcaps-microsoft/ai-first-migrate-insights-agent:pull_request",
    "description": "Federated credential for PR validation",
    "audiences": ["api://AzureADTokenExchange"]
  }'
```

**Allow Branch Wildcard Pattern (Advanced)**

```bash
# Allow any branch matching a pattern (requires environment trick)
# Note: Azure doesn't support direct wildcard in branch names,
# so using environment:* is the recommended approach for "any branch"
az ad app federated-credential create \
  --id $APP_ID \
  --parameters '{
    "name": "github-actions-environment-wildcard",
    "issuer": "https://token.actions.githubusercontent.com",
    "subject": "repo:mcaps-microsoft/ai-first-migrate-insights-agent:environment:*",
    "description": "Allows deployment from any branch using environments",
    "audiences": ["api://AzureADTokenExchange"]
  }'
```

### Step 3: Grant Azure RBAC Permissions
Grant the federated identity your created previously the following roles:

```bash
# Get your subscription ID
SUBSCRIPTION_ID=$(az account show --query id -o tsv)
echo "Subscription ID: $SUBSCRIPTION_ID"

# Option A: Grant at subscription level (if deploying to multiple resource groups)
az role assignment create \
  --assignee $SP_OBJECT_ID \
  --role "Contributor" \
  --scope "/subscriptions/$SUBSCRIPTION_ID"

# Option B: Grant at resource group level (more restricted)
RESOURCE_GROUP="rg-ai-foundry-standard"
az role assignment create \
  --assignee $SP_OBJECT_ID \
  --role "Contributor" \
  --scope "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP"

# Assign it the "Role Based Access Control Administrator" role as the workflow [deploy-insights-api](./deploy-insights-api.yml) assigns the same role to the Container App managed identity. This managed identity needs this role as it assigns RBAC permissions to the Storage Account blob containers and tables 
az role assignment create \
  --assignee $SP_OBJECT_ID \
  --role "Role Based Access Control Administrator" \
  --scope "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP"
```

### Step 4: Store Secrets in GitHub Repository
The secrets used by the GitHub Workflows should be stored scurely in the GitHub repositry.

1. In this GitHub repository click on **Issues** to create a **Temporary administrator access request** and have it approved so that you can setup secrets and environment variables in GitHub.
2. Navigate to **Settings** → **Environments** → **New environment**
3. Create an environment named after the Azure Subscription or Resource Group your are targeting with your workflows
4. Click **Add environment secret** and add the following secrets:

| Secret Name             | Value                           | How to Get                                |
| ----------------------- | ------------------------------- | ----------------------------------------- |
| `AZURE_CLIENT_ID`       | Your Application ID from Step 1 | `echo $APP_ID`                            |
| `AZURE_TENANT_ID`       | Your Azure tenant ID            | `az account show --query tenantId -o tsv` |
| `AZURE_SUBSCRIPTION_ID` | Your subscription ID            | `echo $SUBSCRIPTION_ID`                   |

### Step 5: Setup Environment Variables

1. Click **Add environment variable** and add the same environment variables as you have in your local .env file for your applications but customized for the target environment.
2. Check in your workflows (for example [./deploy-insights-api.yml](./deploy-insights-api.yml)) that:
   1. The name of the environment specified in the workflow matches the one you created previously.
   2. The **paths** and **branchs** used to trigger the workflow match what you will be using   

### Step 6: Verify Configuration

```bash
# Verify federated credentials were created
az ad app federated-credential list --id $APP_ID --query "[].{name:name, subject:subject}" -o table

# Verify role assignments
az role assignment list --assignee $SP_OBJECT_ID --query "[].{Role:roleDefinitionName, Scope:scope}" -o table

# Get all values needed for GitHub secrets
echo "=== GitHub Secrets Configuration ==="
echo "AZURE_CLIENT_ID: $APP_ID"
echo "AZURE_TENANT_ID: $(az account show --query tenantId -o tsv)"
echo "AZURE_SUBSCRIPTION_ID: $(az account show --query id -o tsv)"
```

### Step 7: Test the Workflows
Trigger a workflow manually to test:

1. GitHub → **Actions** → **Deploy Insights API to Azure Container Apps**
2. Click **Run workflow** → Select branch → **Run workflow**
   
   Optionally set `image_tag` or `force_rebuild=true`.
   
3. Monitor the `Azure Login (OIDC)` step - it should authenticate without client secrets

### Security Considerations

- Prefer OIDC over client secrets to eliminate secret rotation burden.
- Limit runner VM NSG inbound to GitHub IP ranges or via jumpbox.
- Use separate RG if blast radius concerns exist.
- Capture deployment audit via Activity Log / Defender for Cloud.
  
### Troubleshooting

**Troubleshooting Federated Credentials:**

- **Error: "No matching federated identity"**

  - If using `environment:*` - ensure the workflow doesn't explicitly set an environment name, or uses a valid environment
  - If using branch-specific - verify the branch name exactly matches: `refs/heads/bahramr/testing`
  - Check organization and repository names are correct (case-sensitive)
  - Ensure issuer is `https://token.actions.githubusercontent.com`
  - Wait 5-10 minutes after creating credential for propagation

- **Error: "Insufficient privileges"**

  - Verify role assignments with command above
  - Ensure service principal has Contributor role at correct scope
  - May need to wait 5-10 minutes for role propagation

- **Working from any branch but getting auth errors?**

  - Use Option 1 (`environment:*`) which is the most permissive
  - Remove any `environment:` declaration from your workflow file
  - Verify with: `az ad app federated-credential list --id $APP_ID`

- **To view federated credentials in Portal:**
  - Entra ID → App registrations → Your app → Certificates & secrets → Federated credentials tab

**To check on the Health of the Container App service and its Logs**

    Check health:

    ```bash
    curl -fk https://<fqdn>/health
    ```

    Tail logs:

    ```bash
    az containerapp logs show --name intake-agent-api --resource-group rg-ai-foundry-standard --follow --since 30m
    ```

**Other troubleshooting tips:**

| Symptom                      | Cause                                         | Fix                                                       |
| ---------------------------- | --------------------------------------------- | --------------------------------------------------------- |
| ACR name conflict            | Global uniqueness required                    | Change `ACR_NAME` env or create once manually             |
| Health check fails           | Missing env vars                              | Set required values via `az containerapp update`          |
| Federated login 401          | Incorrect branch/repo in federated credential | Recreate federated credential matching branch             |
| Workflow cannot resolve FQDN | Private environment without proper DNS        | Add Private DNS zone links or use public environment      |
| Logs empty                   | No ingress or container crash loop            | Check `az containerapp revision list` & describe revision |


---


## Bicep Deployment (`deploy-ailz-bicep.yml`)

Automates Azure Bicep template deployments with support for preview (what-if), validation, and deployment operations.

**Features:**

- **Three operation modes**: `preview` (what-if), `validate`, `deploy`
- **OIDC authentication**: Secure federated identity integration with Azure
- **VNet location overrides**: Configure primary and secondary virtual network regions without editing code
- **Large template handling**: Automatically detects and handles Azure RM API size limits (HTTP 413)
- **Intelligent validation**: Bypasses validation API size limits by relying on Bicep compilation success
- **Artifact capture**: Downloads what-if results, validation outputs, and compiled parameters
- **Job summaries**: Rich deployment summaries with outputs and status

**Usage:**

1. Navigate to Actions → "Deploy AI Landing Zone (Bicep)" → Run workflow
2. Select environment (`dev`, `test`, `prod`)
3. Specify resource group name
4. Choose operation: `preview`, `validate`, or `deploy`
5. (Optional) Override primary/secondary VNet locations
6. Review job summary and download artifacts

**Large Template Behavior:**
When the template exceeds Azure Resource Manager API size limits:

- **All `.bicepparam` files are automatically compiled to JSON** to reduce payload size
- **What-if operations** may still fail with HTTP 413 despite compilation (request size limits apply)
- **Validate operations** automatically detect HTTP 413 and mark as passed when Bicep compilation succeeds
- **Deploy operations** may also encounter HTTP 413 errors; compilation helps but extremely large templates may still fail
- Workflow provides clear messaging about size limits and validation bypass logic
- **Mitigation**: Consider splitting extremely large templates into multiple resource group deployments

## Terraform Deployment (`deploy-ailz-terraform.yml`)

Automates Terraform deployments with plan, apply, and destroy operations.

**Features:**

- **Three operation modes**: `plan`, `apply`, `destroy`
- **OIDC authentication**: Secure federated identity integration with Azure
- **Built-in validation**: Automatically runs `terraform fmt -check`, `terraform init`, and `terraform validate` for all operations
- **VNet location overrides**: Configure primary and secondary virtual network regions without editing code
- **State management**: Automatic Terraform state handling
- **Plan artifacts**: Captures and uploads Terraform plan files
- **Change summaries**: Displays resource additions, changes, and deletions
- **Environment protection**: Optional approval gates for production deployments

**Usage:**

1. Navigate to Actions → "Deploy AI Landing Zone (Terraform)" → Run workflow
2. Select environment (`dev`, `test`, `prod`)
3. Choose operation: `plan`, `apply`, or `destroy`
4. (Optional) Specify tfvars file or working directory
5. (Optional) Override primary/secondary VNet locations
6. Review plan summary and approve (if required)

**Operation Modes:**

- **`plan`** (Recommended first step)

  - Generates an execution plan showing what changes Terraform will make
  - Creates a preview of resource additions, modifications, and deletions
  - **Does not make any changes** to your Azure infrastructure
  - Produces artifacts (tfplan.binary, tfplan.json) for review
  - **When to use**: Always run this first to understand what will change before applying
  - Use case: Pre-deployment review, change impact analysis, approval workflows

- **`apply`**

  - Executes the Terraform plan to **create, update, or modify** Azure resources
  - **Makes actual changes** to your infrastructure based on the generated plan
  - Requires a successful plan to have been generated in the same workflow run
  - Captures Terraform outputs (resource IDs, endpoints, etc.) as artifacts
  - **When to use**: After reviewing the plan and confirming changes are correct
  - Use case: Initial deployment, infrastructure updates, configuration changes

- **`destroy`**
  - Generates a destruction plan and **deletes all managed resources**
  - **Irreversible operation** - removes infrastructure completely
  - Creates a plan showing all resources that will be destroyed
  - **When to use**: Tearing down environments, cleanup after testing, decommissioning
  - Use case: Removing dev/test environments, cost optimization, complete teardown
  - ⚠️ **Caution**: This permanently deletes resources and cannot be undone

**Validation Behavior:**
All Terraform operations automatically validate configuration before executing:

- **Terraform fmt -check**: Ensures consistent code formatting
- **Terraform init**: Initializes providers and modules
- **Terraform validate**: Validates syntax, resource types, and configuration correctness

## Power Platform Networking (Bicep) (`deploy-powerplatform-bicep.yml`)

Automates the two-VNet Power Platform network stamp using the dedicated Bicep template so Copilot Studio agents stay isolated from the core landing zone.

**Features:**

- **Validate or deploy** operation modes reuse the same steps and artifacts as the main Bicep workflow.
- **Automatic location resolution**: When you leave the primary/secondary VNet inputs blank, the workflow compiles `power-platform.bicepparam` and exports the resolved regions plus their source (parameter file vs. workflow override) to the job summary.
- **Regional verification**: Validates Virtual Network, NAT Gateway, Public IP, and Network Security Group availability in both regions before continuing.
- **Parameter safety**: Overrides are only passed to the deployment command when explicitly supplied, preventing accidental drift from the repository configuration.

**Usage:**

1. Actions → "Deploy Power Platform Network Infrastructure (Bicep)" → Run workflow
2. Choose environment, subscription, and target resource group
3. Leave VNet location fields blank to use the values baked into `power-platform.bicepparam`, or supply overrides for break-glass changes
4. Select `validate` (dry run) or `deploy`
5. Review the "Network Configuration" section in the job summary to confirm the effective locations and their origin before approving changes

## Power Platform Networking (Terraform) (`deploy-powerplatform-terraform.yml`)

Deploys the same Power Platform networking stamp via Terraform with plan/apply/destroy parity.

**Features:**

- **Three operation modes**: `plan`, `apply`, `destroy`, mirroring the main Terraform workflow.
- **Provider caching**: Restores `$HOME/.terraform.d/plugin-cache` to avoid repeated downloads.
- **python-hcl2 location parsing**: Resolves primary and secondary VNet locations straight from the selected tfvars file when overrides are blank, fails fast if tfvars are missing, and records each location's source for the summary.
- **Regional guardrails**: Uses the resolved locations to confirm VNet, NAT Gateway, Public IP, and NSG availability before Terraform runs.
- **Artifact capture**: Uploads plan/apply/destroy logs plus optional outputs for auditing.

**Usage:**

1. Actions → "Deploy Power Platform Network Infrastructure (Terraform)" → Run workflow
2. Select environment and Terraform action (`plan` recommended first)
3. Leave the VNet location inputs empty to inherit `power-platform/terraform.tfvars`, or specify overrides to deviate temporarily
4. (Optional) Point to a different tfvars file via the workflow input
5. Review the job summary to confirm the resolved regions and proceed with apply/destroy only after validating the plan artifacts

## IaC Validation Workflows

### Bicep Validation (`bicep-validation.yml`)

- Validates Bicep syntax and linting
- Runs on pull requests targeting main branch
- Fails if Bicep build fails or linting errors exist

**Note:** Terraform validation is integrated directly into the deployment workflow (`deploy-ailz-terraform.yml`) and runs automatically for all operations. A separate validation workflow is no longer needed.

## Regional Architecture

Both Bicep and Terraform support flexible multi-region deployments with independent location controls for global resources, primary VNet, secondary VNet, Fabric Capacity, and Power Platform. Each service can use its optimal region or geography-based location.

**Key Features:**

- **Global location override**: Control default region for most resources
- **Resource group name override**: Customize resource group naming
- **Primary VNet location override**: Set primary network region
- **Secondary VNet location override**: Set DR region (defaults to paired region)
- **Fabric Capacity location override**: Set region for Microsoft Fabric Capacity (preview API may not be available in all regions)
- **Power Platform location override**: Set geography-based location (unitedstates, europe, asia, etc.) - uses political geographies not Azure regions
- **Paired region support**: Automatic fallback to Azure paired regions for HA/DR

**Detailed Documentation:** See [REGIONAL_ARCHITECTURE.md](REGIONAL_ARCHITECTURE.md) for complete inheritance hierarchy, deployment examples, service availability considerations, and multi-region best practices.

## IaC Workflows Inputs

### Bicep Deployment Inputs

| Input                      | Type   | Required | Default                                       | Description                                                        |
| -------------------------- | ------ | -------- | --------------------------------------------- | ------------------------------------------------------------------ |
| `environment`              | choice | Yes      | `dev`                                         | Deployment environment                                             |
| `location`                 | string | No       | -                                             | Global deployment location override                                |
| `resource-group`           | string | Yes      | -                                             | Target resource group name                                         |
| `template-file`            | string | No       | `ai-landing-zone/bicep/infra/main.bicep`      | Bicep template path                                                |
| `parameter-file`           | string | No       | `ai-landing-zone/bicep/infra/main.bicepparam` | Parameter file path                                                |
| `primary-vnet-location`    | string | No       | -                                             | Primary VNet Azure region                                          |
| `secondary-vnet-location`  | string | No       | -                                             | Secondary VNet Azure region                                        |
| `fabric-capacity-location` | string | No       | -                                             | Fabric Capacity Azure region (must support API 2025-01-15-preview) |
| `power-platform-location`  | choice | No       | `(use default)`                               | Power Platform geography (unitedstates, europe, asia, etc.)        |
| `operation`                | choice | Yes      | `validate`                                    | Operation: `preview`, `validate`, `deploy`                         |

**Note:** Deployment names are auto-generated using the format `ai-lz-<run-id>` for consistency and traceability.

### Terraform Deployment Inputs

| Input                      | Type   | Required | Default                                     | Description                                                        |
| -------------------------- | ------ | -------- | ------------------------------------------- | ------------------------------------------------------------------ |
| `environment`              | choice | Yes      | `dev`                                       | Deployment environment                                             |
| `var-file`                 | string | No       | `ai-landing-zone/terraform/dev-full.tfvars` | Terraform variable file path                                       |
| `location`                 | string | No       | -                                           | Global deployment location override                                |
| `resource-group-name`      | string | No       | -                                           | Resource group name override                                       |
| `primary-vnet-location`    | string | No       | -                                           | Primary VNet Azure region                                          |
| `secondary-vnet-location`  | string | No       | -                                           | Secondary VNet Azure region (enable when tfvars deploys secondary) |
| `fabric-capacity-location` | string | No       | -                                           | Fabric Capacity Azure region (must support preview API)            |
| `power-platform-location`  | choice | No       | `(use default)`                             | Power Platform geography (unitedstates, europe, asia, etc.)        |
| `advanced-options-json`    | string | No       | _(blank)_                                   | JSON payload for wait overrides and emergency `replaceResources`   |
| `action`                   | choice | Yes      | `plan`                                      | Operation: `plan`, `apply`, `destroy`                              |

#### Advanced options JSON schema

Use the optional `advanced-options-json` input to provide structured overrides without exceeding the GitHub input limit. Supported properties:

```json
{
  "waitOverrides": {
    "aiFoundryIdentitySeconds": 900,
    "aiSearchIdentitySeconds": 480
  },
  "replaceResources": ["module.apim[0].azurerm_api_management.this"]
}
```

- `waitOverrides` adjusts the identity propagation waits that Terraform uses before reading Azure AI Foundry and Azure AI Search identities. Values must be positive integers ≥ 60 seconds.
- `replaceResources` preserves the original emergency recovery switch (formerly `replace-resources`). Provide one or more Terraform addresses to force replacement when Azure soft-delete conflicts persist.

## IaC Troubleshooting

### Bicep: HTTP 413 (Request Entity Too Large)

**Symptom**: Any operation (validate/preview/deploy) fails with "Expecting value: line 1 column 1 (char 0)"

**Cause**: Template + parameters exceed Azure Resource Manager API size limits

**Understanding the limits**:

- **Validation/What-if API**: ~4MB request limit (strict)
- **Deployment API**: ~4-6MB limit (slightly higher but still constrained)
- **The AI Landing Zone template**: Extremely large (50+ Azure Verified Modules, complex networking, comprehensive configuration)

**Workflow behavior**:

- ✅ **`.bicepparam` files are automatically compiled to JSON** to reduce payload size
- ✅ **Validation failures are auto-detected**: Workflow marks validation as passed when Bicep compilation succeeds
- ⚠️ **Deployment may still fail**: For extremely large templates, even the deployment API has limits

**Solutions** (in order of recommendation):

1. **Local deployment** (Recommended for large templates)
   ```powershell
   cd ai-landing-zone/bicep/infra
   az deployment group create `
     --resource-group rg-AI-Landing-Zone-v1 `
     --template-file main.bicep `
     --parameters main.bicepparam `
     --parameters primaryVNetLocation=eastus secondaryVNetLocation=westus
   ```
2. **Azure DevOps Pipelines** (Higher API limits)

   - Azure DevOps has higher ARM API thresholds than GitHub Actions
   - Consider migrating deployment automation to Azure Pipelines for very large templates

3. **Template refactoring** (Long-term solution)

   - Split into multiple resource group deployments (networking, platform, AI services)
   - Use Bicep modules with separate deployment orchestration
   - Consider deployment stacks for modular composition

4. **Parameter optimization** (Marginal benefit)
   - Remove unused parameters from `.bicepparam` file
   - Use defaults where possible instead of explicit overrides
   - Note: Compiled JSON is already optimized; further reduction requires template changes

**When validation succeeds but deployment fails**:

- This indicates the template exceeds even deployment API limits
- Bicep compilation success confirms template correctness (syntax, types, dependencies)
- **You must deploy locally or via Azure DevOps** - GitHub Actions cannot handle this template size

### Terraform: Authentication Failures

**Symptom**: "Error: Error building AzureRM Client: obtain subscription"

**Cause**: Missing or incorrect federated identity configuration

**Solution**:

1. Verify environment secrets exist: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`
2. Check federated credential subject matches: `repo:org/repo:environment:env-name`
3. Ensure service principal has Contributor role on subscription
4. Verify audience is set to `api://AzureADTokenExchange`

### Secondary VNet Toggle Not Detected

**Symptom**: Workflow logs show "Secondary VNet location override provided but toggle is disabled"

**Cause**: Parameter compilation issue with Bicep warnings interfering with JSON parsing

**Solution**: Latest workflow version (Phase 3.5) fixes this by using file-based compilation instead of stdout. Re-run workflow with latest code.

## Best Practices

1. **Use validation mode first**: Always run `validate` (Bicep) or `plan` (Terraform) before deployment
2. **Review artifacts**: Download and review what-if/plan artifacts before applying changes
3. **Enable approvals**: Require manual approval for production environments
4. **Tag runs**: Use meaningful deployment names for audit trails
5. **Monitor summaries**: Check job summaries for deployment outputs and change counts
6. **Handle size limits**: Understand that large templates may bypass validation API but are still safe to deploy when Bicep compilation succeeds

## Additional Resources

- [Bicep Deployment Guide](../../ai-landing-zone/bicep/docs/how_to_use.md)
- [Terraform Deployment Guide](../../ai-landing-zone/terraform/README.md)
- [Azure OIDC Configuration](https://learn.microsoft.com/azure/developer/github/connect-from-azure)
- [GitHub Environments Documentation](https://docs.github.com/en/actions/deployment/targeting-different-environments/using-environments-for-deployment)

