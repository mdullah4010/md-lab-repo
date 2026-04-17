#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Azure Container Apps Deployment Script for Indexer Service

.DESCRIPTION
    This script deploys the Indexer FastAPI application to Azure Container Apps
    
    Features:
    - Environment variable loading from .env file
    - Docker image build and push to ACR
    - Container Apps deployment with proper configuration
    - RBAC role assignments for managed identity
    - Enhanced error handling

.EXAMPLE
    .\deploy_indexer.ps1
#>

$ErrorActionPreference = "Stop"

# Colors for output
function Write-Success { Write-Host "[OK] $args" -ForegroundColor Green }
function Write-Info { Write-Host "===> $args" -ForegroundColor Cyan }
function Write-Warning { Write-Host "[WARN] $args" -ForegroundColor Yellow }
function Write-Error { Write-Host "[ERROR] $args" -ForegroundColor Red }

# Get script directory
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path

# Function to discover .env file
function Find-EnvFile {
    # Check current directory first
    if (Test-Path ".env") {
        return (Resolve-Path ".env").Path
    }
    
    # Walk up directory tree to find .env file
    $dirPath = (Get-Location).Path
    $root = [System.IO.Path]::GetPathRoot($dirPath)
    while ($dirPath -and $dirPath -ne $root) {
        $envPath = Join-Path $dirPath ".env"
        if (Test-Path $envPath) {
            return $envPath
        }
        $dirPath = Split-Path $dirPath -Parent
    }
    
    return $null
}

# Load .env file
Write-Info "Looking for .env file..."
$ENV_FILE = Find-EnvFile

if (-not $ENV_FILE) {
    Write-Warning "No .env file found in current directory or parent directories"
    Write-Warning "Checking if required infrastructure variables are already set..."
    
    # Check if core infrastructure variables are set
    $missingInfra = @()
    if ([string]::IsNullOrWhiteSpace($env:RESOURCE_GROUP)) { $missingInfra += "RESOURCE_GROUP" }
    if ([string]::IsNullOrWhiteSpace($env:LOCATION)) { $missingInfra += "LOCATION" }
    if ([string]::IsNullOrWhiteSpace($env:AZURE_SUBSCRIPTION_ID)) { $missingInfra += "AZURE_SUBSCRIPTION_ID" }
    
    if ($missingInfra.Count -gt 0) {
        Write-Error "No .env file found and required infrastructure variables are not set: $($missingInfra -join ', ')"
        Write-Host "Please either:" -ForegroundColor Yellow
        Write-Host "  1. Create a .env file with required variables" -ForegroundColor Gray
        Write-Host "  2. Set the required environment variables before running this script" -ForegroundColor Gray
        exit 1
    }
    
    Write-Success "Infrastructure variables are set, continuing without .env file"
} else {
    Write-Info "Loading environment variables from: $ENV_FILE"
    
    # Parse .env file and set environment variables
    Get-Content $ENV_FILE | ForEach-Object {
        $line = $_.Trim()
        
        # Skip empty lines and comments
        if ([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith('#')) {
            return
        }
        
        # Skip lines without =
        if ($line -notmatch '=') {
            return
        }
        
        # Extract key and value
        $parts = $line -split '=', 2
        $key = $parts[0].Trim()
        $value = $parts[1].Trim()
        
        # Remove inline comments (outside of quotes)
        if ($value -match '^"[^"]*"\s*#' -or $value -match "^'[^']*'\s*#") {
            # Quoted value with inline comment
            if ($value -match '^"([^"]*)"') {
                $value = $matches[1]
            } elseif ($value -match "^'([^']*)'") {
                $value = $matches[1]
            }
        } elseif ($value -match '^([^#]*?)\s*#') {
            # Unquoted value with inline comment
            $value = $matches[1].Trim()
        }
        
        # Remove surrounding quotes
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or 
            ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        
        # Set environment variable
        [Environment]::SetEnvironmentVariable($key, $value, [EnvironmentVariableTarget]::Process)
    }
}

# Required variables for Container App deployment
$required_vars = @(
    "AZURE_EXISTING_AIPROJECT_ENDPOINT",
    "AZURE_SEARCH_ENDPOINT",
    "AZURE_STORAGE_ACCOUNT_URL",
    "AZURE_STORAGE_ACCOUNT_NAME",
    "AZURE_OPENAI_API_VERSION",
    "AZURE_OPENAI_EMBEDDING_DEPLOYMENT",
    "AZURE_OPENAI_EMBED_DIM",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_ENDPOINT2",
    "AZURE_SEARCH_INDEX",
    "AZURE_SEARCH_SEMANTIC_CONFIG"
)

$missing_vars = @()
foreach ($var in $required_vars) {
    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($var))) {
        $missing_vars += $var
    }
}

if ($missing_vars.Count -gt 0) {
    Write-Error "Missing required environment variables in $ENV_FILE : $($missing_vars -join ', ')"
    exit 1
}

# Container App specific variables (with defaults matching bash script)
$INDEXER_CONTAINER_APP_NAME = if ($env:INDEXER_CONTAINER_APP_NAME) { $env:INDEXER_CONTAINER_APP_NAME } else { "indexer" }
$CONTAINER_APP_ENV_NAME = if ($env:CONTAINER_APP_ENV_NAME) { $env:CONTAINER_APP_ENV_NAME } else { "aca-env" }
$ACR_NAME = if ($env:ACR_NAME) { $env:ACR_NAME } else { "aiintakeacr" }
$IMAGE_NAME = if ($env:IMAGE_NAME) { $env:IMAGE_NAME } else { "indexer-agent-api" }
$IMAGE_TAG = if ($env:IMAGE_TAG) { $env:IMAGE_TAG } else { "latest" }

Write-Host "`nConfiguration Summary" -ForegroundColor Cyan
Write-Host "  Subscription: $env:AZURE_SUBSCRIPTION_ID"
Write-Host "  Resource Group: $env:RESOURCE_GROUP"
Write-Host "  Location: $env:LOCATION"
Write-Host "  Container App Name: $INDEXER_CONTAINER_APP_NAME"
Write-Host "  Container App Environment: $CONTAINER_APP_ENV_NAME"
Write-Host "  ACR Name: $ACR_NAME"
Write-Host "  Image: ${IMAGE_NAME}:${IMAGE_TAG}"
Write-Host ""

# Check if Container App Environment exists, create if needed
Write-Info "Checking Container App Environment"
$prevErrorAction = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
$envCheckOutput = az containerapp env show --name $CONTAINER_APP_ENV_NAME --resource-group $env:RESOURCE_GROUP --output none 2>$null
$envCheckExitCode = $LASTEXITCODE
$ErrorActionPreference = $prevErrorAction

if ($envCheckExitCode -eq 0) {
    Write-Success "Container App Environment exists: $CONTAINER_APP_ENV_NAME"
} else {
    Write-Info "Creating Container App Environment..."
    $prevErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    $createEnvOutput = az containerapp env create `
        --name $CONTAINER_APP_ENV_NAME `
        --resource-group $env:RESOURCE_GROUP `
        --location $env:LOCATION 2>$null
    $createExitCode = $LASTEXITCODE
    $ErrorActionPreference = $prevErrorAction
    if ($createExitCode -ne 0) {
        Write-Error "Failed to create Container App Environment"
        Write-Host $createEnvOutput
        exit 1
    }
    Write-Success "Container App Environment created"
}

# Get ACR login server
Write-Info "Getting ACR login server"
$ACR_LOGIN_SERVER = (az acr show --name $ACR_NAME --resource-group $env:RESOURCE_GROUP --query loginServer --output tsv).Trim()
Write-Host "ACR Login Server: $ACR_LOGIN_SERVER"

# Get current user's object ID and assign AcrPush role
Write-Info "Ensuring current user has AcrPush permissions on ACR..."
try {
    $currentUser = az ad signed-in-user show --query id -o tsv 2>$null
    if ($currentUser) {
        $currentUser = $currentUser.Trim()
        Write-Host "Current user Object ID: $currentUser"
        
        # Check if role assignment exists
        $existingRole = az role assignment list `
            --assignee $currentUser `
            --role "AcrPush" `
            --scope "/subscriptions/$env:AZURE_SUBSCRIPTION_ID/resourceGroups/$env:RESOURCE_GROUP/providers/Microsoft.ContainerRegistry/registries/$ACR_NAME" `
            --query "[0].id" -o tsv 2>$null
        
        if ([string]::IsNullOrWhiteSpace($existingRole)) {
            Write-Info "Assigning AcrPush role to current user..."
            az role assignment create `
                --assignee-object-id $currentUser `
                --role "AcrPush" `
                --scope "/subscriptions/$env:AZURE_SUBSCRIPTION_ID/resourceGroups/$env:RESOURCE_GROUP/providers/Microsoft.ContainerRegistry/registries/$ACR_NAME" `
                --output none
            Write-Success "AcrPush role assigned. Waiting 10 seconds for propagation..."
            Start-Sleep -Seconds 10
        } else {
            Write-Success "AcrPush role already assigned"
        }
    }
} catch {
    Write-Warning "Could not assign AcrPush role: $_"
    Write-Warning "If build fails, manually assign yourself the 'AcrPush' role on ACR: $ACR_NAME"
}

# Temporarily allow public access for ACR build
Write-Info "Checking ACR network configuration..."
$acrNetworkConfig = az acr show --name $ACR_NAME --query "networkRuleSet.defaultAction" -o tsv
$needsRestore = $false

if ($acrNetworkConfig -eq "Deny") {
    Write-Info "ACR has network restrictions. Temporarily allowing public access for build..."
    az acr update --name $ACR_NAME --default-action Allow --output none
    if ($LASTEXITCODE -eq 0) {
        Write-Success "Temporarily enabled public access"
        $needsRestore = $true
        Start-Sleep -Seconds 5  # Wait for propagation
    } else {
        Write-Warning "Could not modify ACR network rules. Build may fail."
    }
}

# Build Dockerfile for the indexer service
$INDEXER_DIR = Join-Path (Split-Path -Parent (Split-Path -Parent $SCRIPT_DIR)) "indexer"
$INDEXER_DIR = (Resolve-Path $INDEXER_DIR).Path
$DOCKERFILE_PATH = Join-Path $INDEXER_DIR "Dockerfile"

Write-Info "Using existing FastAPI app wrapper and Dockerfile for indexer service"
Write-Host "    Indexer directory: $INDEXER_DIR"
Write-Host "    [OK] app.py and Dockerfile already exist in source code"
Write-Host "    Note: Dockerfile and app.py are maintained in the source repository"

# Change to the indexer directory for build
Write-Info "Changing to indexer directory for build"
Push-Location $INDEXER_DIR
Write-Host "    Build directory: $(Get-Location)"

# Clean up before build
Write-Info "Cleaning up before build"
Remove-Item -Path ".venv", "venv", "__pycache__", ".pytest_cache" -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem -Recurse -Filter "*.pyc" -File | Remove-Item -Force -ErrorAction SilentlyContinue
Get-ChildItem -Recurse -Filter "__pycache__" -Directory | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# Build Docker image in ACR
Write-Info "Building Docker image in ACR (this may take a few minutes...)"

# Set UTF-8 encoding to avoid Azure CLI output encoding issues on Windows
$prevOutputEncoding = [Console]::OutputEncoding
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

# Use --no-logs to avoid streaming log encoding issues, then fetch logs separately if needed
$buildResult = az acr build `
    --registry $ACR_NAME `
    --image "${IMAGE_NAME}:${IMAGE_TAG}" `
    --file Dockerfile `
    --no-logs `
    .

$buildExitCode = $LASTEXITCODE

# Restore encoding
[Console]::OutputEncoding = $prevOutputEncoding
Remove-Item Env:\PYTHONIOENCODING -ErrorAction SilentlyContinue

# Restore network restrictions if they were modified
if ($needsRestore) {
    Write-Info "Restoring ACR network restrictions..."
    az acr update --name $ACR_NAME --default-action Deny --output none
    Write-Success "Network restrictions restored"
}

if ($buildExitCode -ne 0) {
    Write-Error "Docker build failed"
    Pop-Location
    exit 1
}

Pop-Location
Write-Success "Docker image built successfully"

# Check if Container App exists
Write-Info "Checking if Container App exists"
$prevErrorAction = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
$APP_EXISTS = az containerapp show `
    --name $INDEXER_CONTAINER_APP_NAME `
    --resource-group $env:RESOURCE_GROUP `
    --query name --output tsv 2>$null
$appExistsExitCode = $LASTEXITCODE
$ErrorActionPreference = $prevErrorAction

if ([string]::IsNullOrWhiteSpace($APP_EXISTS) -or $appExistsExitCode -ne 0) {
    Write-Info "Creating new Container App"
    
    # Debug: Print all variables
    Write-Host "[DEBUG] Deployment variables:" -ForegroundColor Yellow
    Write-Host "  ACR_NAME: $ACR_NAME"
    Write-Host "  ACR_LOGIN_SERVER: $ACR_LOGIN_SERVER"
    Write-Host "  IMAGE_NAME: $IMAGE_NAME"
    Write-Host "  IMAGE_TAG: $IMAGE_TAG"
    
    # Create the container app with ACR image directly
    $FULL_IMAGE_NAME = "${ACR_LOGIN_SERVER}/${IMAGE_NAME}:${IMAGE_TAG}"
    Write-Host "[DEPLOY] Deploying container app: $INDEXER_CONTAINER_APP_NAME" -ForegroundColor Green
    Write-Host "[IMAGE] Using ACR image: $FULL_IMAGE_NAME" -ForegroundColor Green
    
    $DEPLOYMENT_TIME = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.ffffffZ")
    
    $prevErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    $createOutput = az containerapp create `
        --name $INDEXER_CONTAINER_APP_NAME `
        --resource-group $env:RESOURCE_GROUP `
        --environment $CONTAINER_APP_ENV_NAME `
        --image $FULL_IMAGE_NAME `
        --registry-server $ACR_LOGIN_SERVER `
        --registry-identity system `
        --target-port 8080 `
        --ingress external `
        --min-replicas 1 `
        --max-replicas 3 `
        --cpu 1.0 `
        --memory 2.0Gi `
        --env-vars `
            "AZURE_EXISTING_AIPROJECT_ENDPOINT=$env:AZURE_EXISTING_AIPROJECT_ENDPOINT" `
            "AZURE_SEARCH_ENDPOINT=$env:AZURE_SEARCH_ENDPOINT" `
            "AZURE_STORAGE_ACCOUNT_URL=$env:AZURE_STORAGE_ACCOUNT_URL" `
            "AZURE_STORAGE_ACCOUNT_NAME=$env:AZURE_STORAGE_ACCOUNT_NAME" `
            "AZURE_OPENAI_API_VERSION=$(if ($env:AZURE_OPENAI_API_VERSION) { $env:AZURE_OPENAI_API_VERSION } else { '2023-05-15' })" `
            "AZURE_OPENAI_EMBEDDING_DEPLOYMENT=$(if ($env:AZURE_OPENAI_EMBEDDING_DEPLOYMENT) { $env:AZURE_OPENAI_EMBEDDING_DEPLOYMENT } else { 'text-embedding-3-large' })" `
            "AZURE_OPENAI_EMBED_DIM=$(if ($env:AZURE_OPENAI_EMBED_DIM) { $env:AZURE_OPENAI_EMBED_DIM } else { '3072' })" `
            "AZURE_OPENAI_ENDPOINT=$env:AZURE_OPENAI_ENDPOINT" `
            "AZURE_OPENAI_ENDPOINT2=$env:AZURE_OPENAI_ENDPOINT2" `
            "AZURE_SEARCH_INDEX=$env:AZURE_SEARCH_INDEX" `
            "AZURE_SEARCH_SEMANTIC_CONFIG=$(if ($env:AZURE_SEARCH_SEMANTIC_CONFIG) { $env:AZURE_SEARCH_SEMANTIC_CONFIG } else { 'DefaultSemantic' })" `
            "USE_MANAGED_IDENTITY=$(if ($env:USE_MANAGED_IDENTITY) { $env:USE_MANAGED_IDENTITY } else { 'true' })" `
            "USE_MANAGED_IDENTITY_FOR_AOAI=$(if ($env:USE_MANAGED_IDENTITY_FOR_AOAI) { $env:USE_MANAGED_IDENTITY_FOR_AOAI } else { 'true' })" `
            "APPLICATIONINSIGHTS_CONNECTION_STRING=$env:APPLICATIONINSIGHTS_CONNECTION_STRING" `
            "PYTHONPATH=/app" `
            "APP_VERBOSE=$(if ($env:APP_VERBOSE) { $env:APP_VERBOSE } else { '1' })" `
            "LOG_LEVEL=$(if ($env:LOG_LEVEL) { $env:LOG_LEVEL } else { 'INFO' })" `
            "DEPLOYMENT_TIME=$DEPLOYMENT_TIME" `
        --output table 2>$null
    $createExitCode = $LASTEXITCODE
    $ErrorActionPreference = $prevErrorAction
    
    if ($createExitCode -ne 0) {
        Write-Error "Failed to create Container App"
        exit 1
    }
    
    Write-Success "Container app created with ACR image and managed identity configured"
} else {
    Write-Info "Updating existing Container App"
    
    # Get the managed identity principal ID
    $prevErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    $IDENTITY_OBJECT_ID = az containerapp identity show `
        --name $INDEXER_CONTAINER_APP_NAME `
        --resource-group $env:RESOURCE_GROUP `
        --query principalId `
        --output tsv 2>$null
    $ErrorActionPreference = $prevErrorAction
    if ($IDENTITY_OBJECT_ID) { $IDENTITY_OBJECT_ID = $IDENTITY_OBJECT_ID.Trim() }
    
    if ([string]::IsNullOrWhiteSpace($IDENTITY_OBJECT_ID)) {
        Write-Info "No managed identity found, enabling system-assigned identity..."
        $prevErrorAction = $ErrorActionPreference
        $ErrorActionPreference = "SilentlyContinue"
        $identityOutput = az containerapp identity assign `
            --name $INDEXER_CONTAINER_APP_NAME `
            --resource-group $env:RESOURCE_GROUP `
            --system-assigned 2>$null
        
        # Get the new identity
        $IDENTITY_OBJECT_ID = az containerapp identity show `
            --name $INDEXER_CONTAINER_APP_NAME `
            --resource-group $env:RESOURCE_GROUP `
            --query principalId `
            --output tsv 2>$null
        $ErrorActionPreference = $prevErrorAction
        if ($IDENTITY_OBJECT_ID) { $IDENTITY_OBJECT_ID = $IDENTITY_OBJECT_ID.Trim() }
    }
    
    Write-Host "Managed identity principal ID: $IDENTITY_OBJECT_ID"
    
    # Ensure AcrPull role is assigned
    Write-Info "Assigning AcrPull role to managed identity..."
    $prevErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    az role assignment create `
        --assignee-object-id $IDENTITY_OBJECT_ID `
        --assignee-principal-type ServicePrincipal `
        --role "AcrPull" `
        --scope "/subscriptions/$env:AZURE_SUBSCRIPTION_ID/resourceGroups/$env:RESOURCE_GROUP/providers/Microsoft.ContainerRegistry/registries/$ACR_NAME" `
        2>$null | Out-Null
    $acrPullExitCode = $LASTEXITCODE
    $ErrorActionPreference = $prevErrorAction
    if ($acrPullExitCode -eq 0) {
        Write-Success "AcrPull role assigned"
    } else {
        Write-Warning "AcrPull role already assigned or failed"
    }
    
    # Configure registry authentication
    Write-Info "Configuring registry authentication..."
    $prevErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    $registryOutput = az containerapp registry set `
        --name $INDEXER_CONTAINER_APP_NAME `
        --resource-group $env:RESOURCE_GROUP `
        --server $ACR_LOGIN_SERVER `
        --identity system 2>$null
    $ErrorActionPreference = $prevErrorAction
    
    # Update Container App image and environment variables
    $DEPLOYMENT_TIME = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.ffffffZ")
    
    $prevErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    $updateOutput = az containerapp update `
        --name $INDEXER_CONTAINER_APP_NAME `
        --resource-group $env:RESOURCE_GROUP `
        --image "${ACR_LOGIN_SERVER}/${IMAGE_NAME}:${IMAGE_TAG}" `
        --set-env-vars `
            "AZURE_EXISTING_AIPROJECT_ENDPOINT=$env:AZURE_EXISTING_AIPROJECT_ENDPOINT" `
            "AZURE_SEARCH_ENDPOINT=$env:AZURE_SEARCH_ENDPOINT" `
            "AZURE_STORAGE_ACCOUNT_URL=$env:AZURE_STORAGE_ACCOUNT_URL" `
            "AZURE_STORAGE_ACCOUNT_NAME=$env:AZURE_STORAGE_ACCOUNT_NAME" `
            "AZURE_OPENAI_API_VERSION=$(if ($env:AZURE_OPENAI_API_VERSION) { $env:AZURE_OPENAI_API_VERSION } else { '2023-05-15' })" `
            "AZURE_OPENAI_EMBEDDING_DEPLOYMENT=$(if ($env:AZURE_OPENAI_EMBEDDING_DEPLOYMENT) { $env:AZURE_OPENAI_EMBEDDING_DEPLOYMENT } else { 'text-embedding-3-large' })" `
            "AZURE_OPENAI_EMBED_DIM=$(if ($env:AZURE_OPENAI_EMBED_DIM) { $env:AZURE_OPENAI_EMBED_DIM } else { '3072' })" `
            "AZURE_OPENAI_ENDPOINT=$env:AZURE_OPENAI_ENDPOINT" `
            "AZURE_OPENAI_ENDPOINT2=$env:AZURE_OPENAI_ENDPOINT2" `
            "AZURE_SEARCH_INDEX=$env:AZURE_SEARCH_INDEX" `
            "AZURE_SEARCH_SEMANTIC_CONFIG=$(if ($env:AZURE_SEARCH_SEMANTIC_CONFIG) { $env:AZURE_SEARCH_SEMANTIC_CONFIG } else { 'DefaultSemantic' })" `
            "USE_MANAGED_IDENTITY=$(if ($env:USE_MANAGED_IDENTITY) { $env:USE_MANAGED_IDENTITY } else { 'true' })" `
            "USE_MANAGED_IDENTITY_FOR_AOAI=$(if ($env:USE_MANAGED_IDENTITY_FOR_AOAI) { $env:USE_MANAGED_IDENTITY_FOR_AOAI } else { 'true' })" `
            "APPLICATIONINSIGHTS_CONNECTION_STRING=$env:APPLICATIONINSIGHTS_CONNECTION_STRING" `
            "PYTHONPATH=/app" `
            "APP_VERBOSE=$(if ($env:APP_VERBOSE) { $env:APP_VERBOSE } else { '1' })" `
            "LOG_LEVEL=$(if ($env:LOG_LEVEL) { $env:LOG_LEVEL } else { 'INFO' })" `
            "DEPLOYMENT_TIME=$DEPLOYMENT_TIME" 2>$null
    $updateExitCode = $LASTEXITCODE
    $ErrorActionPreference = $prevErrorAction
    
    if ($updateExitCode -ne 0) {
        Write-Error "Failed to update Container App"
        exit 1
    }
    Write-Success "Container app updated successfully"
}

# Get managed identity principal ID
Write-Info "Getting managed identity principal ID"
$prevErrorAction = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
$IDENTITY_OBJECT_ID = az containerapp identity show `
    --name $INDEXER_CONTAINER_APP_NAME `
    --resource-group $env:RESOURCE_GROUP `
    --query principalId `
    --output tsv 2>$null
$ErrorActionPreference = $prevErrorAction
if ($IDENTITY_OBJECT_ID) {
    $IDENTITY_OBJECT_ID = $IDENTITY_OBJECT_ID.Trim()
}
Write-Host "Managed identity principal ID: $IDENTITY_OBJECT_ID"

# Extract search service name from endpoint URL
$AZURE_SEARCH_SERVICE_NAME = ""
if (![string]::IsNullOrWhiteSpace($env:AZURE_SEARCH_ENDPOINT)) {
    if ($env:AZURE_SEARCH_ENDPOINT -match 'https://([^.]+)\.search\.windows\.net') {
        $AZURE_SEARCH_SERVICE_NAME = $Matches[1]
        Write-Host "Extracted search service name: $AZURE_SEARCH_SERVICE_NAME"
    }
}

# Assign RBAC roles
Write-Info "Assigning RBAC roles (will skip if already assigned)"

# Azure AI User
$prevErrorAction = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
az role assignment create `
    --assignee-object-id $IDENTITY_OBJECT_ID `
    --assignee-principal-type ServicePrincipal `
    --role "Azure AI User" `
    --scope "/subscriptions/$env:AZURE_SUBSCRIPTION_ID/resourceGroups/$env:RESOURCE_GROUP/providers/Microsoft.CognitiveServices/accounts/$env:AZURE_AI_PROJECT_NAME" `
    2>$null | Out-Null
$aiUserExitCode = $LASTEXITCODE
$ErrorActionPreference = $prevErrorAction
if ($aiUserExitCode -eq 0) {
    Write-Success "Azure AI User role assigned"
} else {
    Write-Warning "Azure AI User role already assigned or failed"
}

# Search Index Data Contributor (only if search service name was extracted)
if (![string]::IsNullOrWhiteSpace($AZURE_SEARCH_SERVICE_NAME)) {
    $prevErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    az role assignment create `
        --assignee-object-id $IDENTITY_OBJECT_ID `
        --assignee-principal-type ServicePrincipal `
        --role "Search Index Data Contributor" `
        --scope "/subscriptions/$env:AZURE_SUBSCRIPTION_ID/resourceGroups/$env:RESOURCE_GROUP/providers/Microsoft.Search/searchServices/$AZURE_SEARCH_SERVICE_NAME" `
        2>$null | Out-Null
    $searchIndexExitCode = $LASTEXITCODE
    $ErrorActionPreference = $prevErrorAction
    if ($searchIndexExitCode -eq 0) {
        Write-Success "Search Index Data Contributor role assigned"
    } else {
        Write-Warning "Search Index role already assigned or failed"
    }
    
    # Search Service Contributor
    $prevErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    az role assignment create `
        --assignee-object-id $IDENTITY_OBJECT_ID `
        --assignee-principal-type ServicePrincipal `
        --role "Search Service Contributor" `
        --scope "/subscriptions/$env:AZURE_SUBSCRIPTION_ID/resourceGroups/$env:RESOURCE_GROUP/providers/Microsoft.Search/searchServices/$AZURE_SEARCH_SERVICE_NAME" `
        2>$null | Out-Null
    $searchServiceExitCode = $LASTEXITCODE
    $ErrorActionPreference = $prevErrorAction
    if ($searchServiceExitCode -eq 0) {
        Write-Success "Search Service Contributor role assigned"
    } else {
        Write-Warning "Search Service role already assigned or failed"
    }
} else {
    Write-Warning "Could not extract search service name from AZURE_SEARCH_ENDPOINT - skipping Search role assignments"
}

# Storage Blob Data Reader (for reading blobs to index)
$prevErrorAction = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
az role assignment create `
    --assignee-object-id $IDENTITY_OBJECT_ID `
    --assignee-principal-type ServicePrincipal `
    --role "Storage Blob Data Reader" `
    --scope "/subscriptions/$env:AZURE_SUBSCRIPTION_ID/resourceGroups/$env:RESOURCE_GROUP/providers/Microsoft.Storage/storageAccounts/$env:AZURE_STORAGE_ACCOUNT_NAME" `
    2>$null | Out-Null
$storageBlobExitCode = $LASTEXITCODE
$ErrorActionPreference = $prevErrorAction
if ($storageBlobExitCode -eq 0) {
    Write-Success "Storage Blob Data Reader role assigned"
} else {
    Write-Warning "Storage Blob role already assigned or failed"
}

# Get the Container App URL
Write-Info "Getting Container App URL"
$prevErrorAction = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
$APP_FQDN = az containerapp show `
    --name $INDEXER_CONTAINER_APP_NAME `
    --resource-group $env:RESOURCE_GROUP `
    --query properties.configuration.ingress.fqdn `
    --output tsv 2>$null
$ErrorActionPreference = $prevErrorAction
if ($APP_FQDN) {
    $APP_FQDN = $APP_FQDN.Trim()
}

Write-Host ""
Write-Host "==========================================" -ForegroundColor Green
Write-Host "[OK] Indexer service deployed successfully!" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Container App URL: https://$APP_FQDN" -ForegroundColor Cyan
Write-Host "Health Check: https://$APP_FQDN/health" -ForegroundColor Cyan
Write-Host "Index Endpoint: https://$APP_FQDN/api/index" -ForegroundColor Cyan
Write-Host ""
Write-Host "To test the indexer, send a POST request:" -ForegroundColor Yellow
Write-Host "  curl -X POST https://$APP_FQDN/api/index \" -ForegroundColor Gray
Write-Host "       -H 'Content-Type: application/json' \" -ForegroundColor Gray
Write-Host "       -d '{`"appId`": `"your-app-id`", `"container`": `"your-app-id`"}'" -ForegroundColor Gray
Write-Host ""
Write-Host "Update your .env file with:" -ForegroundColor Yellow
Write-Host "  AZURE_INDEXING_FUNCTION_URL=https://$APP_FQDN/api/index" -ForegroundColor Cyan
Write-Host "  (Note: Variable name kept for backward compatibility)" -ForegroundColor Gray
Write-Host ""
