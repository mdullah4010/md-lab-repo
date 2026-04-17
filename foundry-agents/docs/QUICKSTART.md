<!--
DOC_INTENT:
	surface: foundry
	page: QUICKSTART
	purpose: Get a developer/operator from zero to running the Foundry agents locally, with clear prerequisites and a minimal validation workflow.
	audience: Developers, operators
	should_cover:
		- Prerequisites (Python, Azure auth for local dev)
		- Required environment variables and where to set them
		- How to run the agent API/orchestrator locally
		- How to run a minimal smoke test
		- Where to go next (architecture, deployment)
	should_not_cover:
		- Full production infra hardening (link to INFRA_STANDARD instead)
	source_refs:
		- cockpit-docs/docs/quickstart.md (reference style only)
		- foundry-agents/docs/INFRA_STANDARD.md
-->

# Developer Quickstart (AI Foundry)

> **Audience**: Developers and operators
> 
> **Time to complete**: ~20–30 minutes (local dev after Azure prerequisites)
> 
> **Last validated**: 2026-01-28

## Overview

This guide takes you from prerequisites to a working local dev loop for the **Insights Agent** (Foundry pro-code implementation).

It covers:

1. **Prerequisites** — the Azure environment and permissions you need before local dev.
2. **Local setup** — create a virtual environment, install dependencies, configure `.env`.
3. **Run locally** — start the API service.
4. **Validate** — confirm the service is healthy and endpoints are reachable.


## Deploy the Platform and Application Components
Complete the deployment prerequisites in [DEPLOYMENT.md](./DEPLOYMENT.md) (or ensure an equivalent environment already exists) before proceeding.


## Run the application locally on your workstation
This section provides the instructions for running the application components locally on your workstation during development instead of deploying them on Azure Container Apps. 

### 1. Permissions required

To run this application locally, you can use any of the following identities:
  - A Service Principal that you create in Microsoft Entra ID, or
  - Your own identity
  
You need to assign this identity the following Azure roles: 
   - "Cognitive Services User" on the AI Foundry service 
   - "Cognitive Services OpenAI User" on the AI Foundry service 
   - "Azure AI User" on the AI Foundry service 
   - "Storage Blob Data Contributor" on the Storage Account
   - "Storage Table Data Contributor" on the Storage Account
   - "Role Based Access Control Administrator" on the Storage Account
   - "Search Service Contributor" on the AI Search service

### 2. Authenticate to Azure
If you want to use a Service Principal to run this agent from the CLI, first create that SP in your tenant and then set its information in the following environment variables:
   ```bash
   export AZURE_TENANT_ID=<your Microsoft Entra ID tenant>
   export AZURE_CLIENT_SECRET=<the secret for the SP you created>
   export AZURE_CLIENT_ID=<the Client ID of the SP you created>
   ```

If you want to run this agent under your own identity, make sure you **unset** the environment variables above and then log in to your Azure subscription by running:

   ```bash
   az login
   ```

### 3. Navigate to the agents directory
   ```bash
   cd foundry-agents
   ```
   
### 4. Create and activate virtual environment
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

### 5. Install dependencies
   ```bash
   pip install -r requirements.txt
   ```

### 6. Configure the environment variables
   ```bash
   # Copy and customize the environment variables
   cp env.example .env
   ```
   
   **Logging Configuration:**
   - All log entries are automatically written to **both** the console (stdout) **and** a log file
   - Configure logging with these environment variables in your `.env` file:
     - `LOG_LEVEL`: Set to `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL` (default: `INFO`)
     - `LOG_FILE`: Specify the log file name (default: `insights-agent.log`)
   - Example: `LOG_LEVEL=DEBUG` for detailed troubleshooting, `LOG_LEVEL=INFO` for normal operation

### 7. Start the server
   ```bash
   python -m uvicorn agents.api_main:app --host 0.0.0.0 --port 8000 --reload
   ```

### 8. Test the API endpoints

#### Setup the application container and artifacts
To test the API endpoints, you need to set up a container for the application you are migrating as follows:
  1. Create a container in the Storage Account named after the ID of the application
  2. Grant the user who will be running the tests the Azure role 'Storage Blob Data Contributor' on the container you created previously. Note that this user object-id will also have to be specified as one of the parameters of the API endpoints.
  3. Upload the artifacts about the application that the customer shared with you in this container

#### Call the API endpoints
You can call the Insights API endpoints either from a web browser or from the command line.
  - To test them from a web browser, go to http://localhost:8000/docs and select the endpoints displayed.
  - To test them from the command line, some examples are provided below. Note that the user_object_id should be the object ID of the user running the commands and who was granted the Azure role 'Storage Blob Data Contributor' on the container '1000'.

   ```bash
   curl -X POST http://localhost:8000/createApplicationId \
     -H "Content-Type: application/json" \
     -d '{
       "app_id": "1000",
       "azure_region": "eastus2",
       "resource_group_name": "AIFirstDev",
       "storage_account_name": "ai1stimggenaisarbfk",
       "user_object_id": "8b4c4a15-ff5a-42fc-a1d8-68e242c34332"
     }'

   curl -X POST http://localhost:8000/indexDocuments \
     -H "Content-Type: application/json" \
     -d '{
       "app_id": "1000",
       "azure_region": "eastus2",
       "resource_group_name": "AIFirstDev",
       "storage_account_name": "ai1stimggenaisarbfk",
       "user_object_id": "8b4c4a15-ff5a-42fc-a1d8-68e242c34332"
     }'

   curl -X POST http://localhost:8000/runAnalysis \
     -H "Content-Type: application/json" \
     -d '{
       "app_id": "1000",
       "azure_region": "eastus2",
       "resource_group_name": "AIFirstDev",
       "storage_account_name": "ai1stimggenaisarbfk",
       "user_object_id": "8b4c4a15-ff5a-42fc-a1d8-68e242c34332"
     }'

   curl -X POST http://localhost:8000/generateAssessmentReport \
     -H "Content-Type: application/json" \
     -d '{
       "app_id": "1000",
       "azure_region": "eastus2",
       "resource_group_name": "AIFirstDev",
       "storage_account_name": "ai1stimggenaisarbfk",
       "user_object_id": "8b4c4a15-ff5a-42fc-a1d8-68e242c34332"
     }'

   ```

  To call the Operations endpoints, refer to the document [OPERATION_TRACKING.md](./OPERATION_TRACKING.md)

The API endpoints need to be called in the following order:
   1.	POST /createApplicationId
   2.	POST /indexDocuments
   3. POST /analyzeCode
   4.	POST /discoverKubernetes
   5.	POST /runAnalysis
   6.	POST /generateAssessmentReport
   7.	POST /generateDesign
   8.	POST /analyzeArchitecture
  9.	GET /operations/*
  10.	GET /health
  11.	POST /deleteAppData 

  For details about these endpoints, refer to this [README](../agents/README.md).

### 9. Cleanup

After running the endpoint /deleteAppData, go to the Azure AI Foundry portal and make sure that all the agents created and all their respective threads have been automatically deleted.
Also make sure that the index created for the application has been deleted. 

