<!--
DOC_INTENT:
	surface: foundry
	page: DEPLOYMENT
	purpose: Document how to deploy the Foundry agents runtime to Azure (or other hosting) and how to validate a deployed instance.
	audience: Operators, developers
	should_cover:
		- Supported deployment targets (if multiple)
		- Required Azure resources and identity configuration
		- Configuration steps (env vars, endpoints)
		- Post-deploy validation checklist
	should_not_cover:
		- Detailed infrastructure reference implementation walkthrough (link to INFRA_STANDARD)
	source_refs:
		- foundry-agents/docs/INFRA_STANDARD.md
		- foundry-agents/scripts/ (deployment scripts)
-->

# Deployment (AI Foundry)

> **Audience**: Operators and developers
> 
> **Time to complete**: ~60–120 minutes (if provisioning infra) / ~20–40 minutes (if infra already exists)
> 
> **Last validated**: 2026-01-28

## Overview

This document is the deployment entrypoint for the **Insights Agent** Foundry implementation.

It covers:

1. **Platform prerequisites** — the Azure resources and identity setup required for the agents to run.
2. **Bootstrap steps** — required storage tables, prompt/templates upload, and other one-time setup.
3. **Deploy components** — where/how to deploy the API and supporting services.
4. **Validation** — how to confirm the deployment is healthy.


## Getting Started
Follow the instructions below to deploy the Insights Agent and all its sub-agents.


### 1. Deploy the Landing Zone
	- To deploy the Standard landing zone (for development), follow the steps documented in [INFRA_STANDARD.md](./INFRA_STANDARD.md)
	- To deploy the AI landing zone (for production), follow the steps documented in this [repo](https://github.com/mcaps-microsoft/ai-first-migrate-ailz/blob/main/ai-landing-zone/bicep/README.md)

### 2. Import the templates tables in the Storage Account

The following template tables are used by the Responder Agent and need to be imported into the Azure Storage Account:
   - "AppDetailsTemplate",
   - "MsSqlDBTemplate",
   - "OracleDBTemplate",
   - "IntegrationDependencyTemplate",
   - "InfrastructureDetails",
   - "K8STemplate"

CSV files with sample data for each one of these tables are located in the folder [../scripts/environment-setup/template_tables](../scripts/environment-setup/template_tables/).

You can import them into the Azure Storage Account used to store the applications artifacts by running the following script:
```bash
	python scripts/environment-setup/import_migration_agent_tables.py --storage-account <storage-account> --resource-group <resource-group> --input-dir scripts/environment-setup/template_tables --overwrite
```
> **Note:** you may need to temporarily enable network public access to the Storage Account in order to import these files from your workstation.

In case you need to export these tables into CSV files in a different environment and then import them into your environment, you can run the script:
```bash
	python scripts/environment-setup/export_migration_agent_tables.py --storage-account <storage-account> --resource-group <resource-group> --output-dir scripts/environment-setup/template_tables
```

### 3. Import the agents prompts into the storage 'templates' container

Some of the AI agents import prompts used to generate documents at runtime from the same Storage Account as above. These are the instruction/prompt files you can find in [../agents/agent-instructions](../agents/agent-instructions/). Create a blob container called **templates** in this Storage Account and upload these files into it.

### 4. Deploy the application components to Azure Container Apps

You can use either GitHub Actions workflows, bash or powershell scripts to deploy the following application components to Azure Container Apps:

| Container App | Workflow | Bash script | Powershell script |
|----------|-------------|---------|---------|
|Insights Agent API endpoints | [deploy-insights-api.yml](../../.github/workflows/deploy-insights-api.yml) | [deploy_insights_api.sh](../scripts/deployment/deploy_insights_api.sh) | [deploy_insights_api.ps1](../scripts/deployment/deploy_insights_api.ps1)
|Azure Pricing MCP Server |  [deploy-azure-pricing-mcp.yml](../../.github/workflows/deploy-azure-pricing-mcp.yml) | [deploy_azure_pricing_mcp.sh](../scripts/deployment/deploy_azure_pricing_mcp.sh) | [deploy_azure_pricing_mcp.ps1](../scripts/deployment/deploy_azure_pricing_mcp.ps1)
|Indexer |  [deploy-indexer.yml](../../.github/workflows/deploy-indexer.yml) | [deploy_indexer.sh](../scripts/deployment/deploy_indexer.sh) | [deploy_indexer.ps1](../scripts/deployment/deploy_indexer.ps1)  

The instructions for running these Action Workflows or scripts are similar to the one for the Insights Agent API, which you can find in [DEPLOY_INSIGHTS_API.md](./DEPLOY_INSIGHTS_API.md).

Note that the environment variables needed by the Container Apps deployed are automatically set through these Actions Workflows and scripts:
  - The workflows read these environment variables from the GitHub environment specified in the GitHub workflow.
  - The scripts read them from the local .env file

The RBAC roles needed by the System Managed Identity of these container apps are also automatically set by these workflows and scripts. For this reason **the identity running these workflows or scripts needs to be assigned the role "Owner" or "Role Based Access Control Administrator" at the Resource Group level**. When assigning this role, make sure to select the condition "Allow user to assign all roles (highly privileged)".

To deploy with GitHub Actions workflows, you need to install GitHub runners first (if they are not already installed) and follow the instructions in this [README](../../.github/workflows/README.md). 

### 5. Test the endpoints
You can test each API endpoint through the test integration scripts provider in the folder ../tests/integration. For information about how to run these scripts refer to this [README](../tests/README.md)
