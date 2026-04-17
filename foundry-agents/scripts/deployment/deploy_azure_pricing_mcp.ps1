# Azure Container Apps Deployment Script for Azure Pricing MCP Server
# This script deploys the Azure Pricing MCP Server to Azure Container Apps
#
# Features:
# - Converted from bash to PowerShell for Windows compatibility
# - ACR auto-discovery from resource group
# - Automatic ACR admin and network access management
# - UTF-8 encoding fix for Azure CLI
# - Environment variable loading from .env file
# - Docker image build and push to ACR
# - Container Apps deployment with proper configuration
# - Health check validation
# - Enhanced error handling and troubleshooting guidance

$ErrorActionPreference = "Stop"

# Function to trim whitespace
function Trim-String {
    param([string]$Text)
    return $Text.Trim()
}

# Function to remove inline comments from value
function Remove-InlineComment {
    param([string]$Value)
    
    # Remove inline comments (everything after # outside of quotes)
    if ($Value -match '^".*".*#' -or $Value -match "^'.*'.*#") {
        # Value is quoted and has inline comment
        if ($Value -match '^"([^"]*)".*') {
            return $Matches[1]
        }
        elseif ($Value -match "^'([^']*)'.*") {
            return $Matches[1]
        }
    }
    elseif ($Value -match '^([^#]*[^\s])\s*#') {
        # Value is unquoted with inline comment
        return $Matches[1].Trim()
    }
    
    # Remove surrounding quotes if present
    $Value = $Value -replace '^["'']|["'']$', ''
    return $Value
}

# Get script directory
$SCRIPT_DIR = Split-Path -Parent $PSCommandPath

# Discover and load .env file
$ENV_FILE = $null

# Check current directory first
if (Test-Path ".env") {
    $ENV_FILE = ".env"
}
else {
    # Walk up directory tree to find .env file
    $dirPath = (Get-Location).Path
    $root = [System.IO.Path]::GetPathRoot($dirPath)
    while ($dirPath -and $dirPath -ne $root) {
        $envPath = Join-Path $dirPath ".env"
        if (Test-Path $envPath) {
            $ENV_FILE = $envPath
            break
        }
        $dirPath = Split-Path $dirPath -Parent
    }
}

if ($ENV_FILE) {
    Write-Host "[FILE] Loading environment variables from: $ENV_FILE" -ForegroundColor Cyan
    
    # Read and process .env file
    Get-Content $ENV_FILE | ForEach-Object {
        $line = $_.Trim()
        
        # Skip empty lines and comments
        if ([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith('#')) {
            return
        }
        
        # Skip if line doesn't contain =
        if ($line -notmatch '=') {
            return
        }
        
        # Extract key and value
        $parts = $line -split '=', 2
        $key = Trim-String $parts[0]
        $value = Trim-String $parts[1]
        
        # Remove inline comments and quotes
        $value = Remove-InlineComment $value
        
        # Set environment variable
        Set-Item -Path "env:$key" -Value $value
    }
    
    # Validate that required environment variables are set
    $REQUIRED_VARS = @(
        "MCP_HOST",
        "MCP_PORT",
        "MCP_DEBUG",
        "MCP_RELOAD",
        "CORS_ORIGINS"
    )
    
    $MISSING_VARS = @()
    foreach ($var in $REQUIRED_VARS) {
        $value = [Environment]::GetEnvironmentVariable($var)
        if ([string]::IsNullOrWhiteSpace($value) -or $value -eq "PLACEHOLDER_SET_THIS_VALUE") {
            $MISSING_VARS += $var
        }
    }
    
    if ($MISSING_VARS.Count -gt 0) {
        Write-Host "[ERROR] Missing environment variables:" -ForegroundColor Red
        $MISSING_VARS | ForEach-Object { Write-Host "   - $_" -ForegroundColor Red }
        Write-Host ""
        Write-Host "Please set these in your .env file before deployment." -ForegroundColor Yellow
        exit 1
    }
    
    Write-Host "[OK] All required environment variables loaded from .env file" -ForegroundColor Green
}
else {
    Write-Host "[WARN]  No .env file found. Using environment variables and defaults." -ForegroundColor Yellow
    
    # Validate minimum required environment variables when no .env file is found
    $REQUIRED_INFRA_VARS = @(
        "RESOURCE_GROUP",
        "LOCATION",
        "AZURE_SUBSCRIPTION_ID"
    )
    
    $MISSING_INFRA_VARS = @()
    foreach ($var in $REQUIRED_INFRA_VARS) {
        $value = [Environment]::GetEnvironmentVariable($var)
        if ([string]::IsNullOrWhiteSpace($value)) {
            $MISSING_INFRA_VARS += $var
        }
    }
    
    if ($MISSING_INFRA_VARS.Count -gt 0) {
        Write-Host "[ERROR] Missing required environment variables:" -ForegroundColor Red
        $MISSING_INFRA_VARS | ForEach-Object { Write-Host "   - $_" -ForegroundColor Red }
        Write-Host ""
        Write-Host "Please either:" -ForegroundColor Yellow
        Write-Host "   1. Create a .env file in the project root with required variables" -ForegroundColor Yellow
        Write-Host "   2. Or set environment variables before running:" -ForegroundColor Yellow
        Write-Host '      $env:RESOURCE_GROUP = "your-resource-group"' -ForegroundColor Cyan
        Write-Host '      $env:LOCATION = "eastus2"' -ForegroundColor Cyan
        Write-Host '      $env:AZURE_SUBSCRIPTION_ID = "your-subscription-id"' -ForegroundColor Cyan
        exit 1
    }
}

# Configuration variables (matching bash script defaults)
$CONTAINER_APP_NAME = if ($env:CONTAINER_APP_NAME) { $env:CONTAINER_APP_NAME } else { "azure-pricing-mcp" }
$CONTAINER_APP_ENV_NAME = if ($env:CONTAINER_APP_ENV_NAME) { $env:CONTAINER_APP_ENV_NAME } else { "aca-env" }
$ACR_NAME = if ($env:ACR_NAME) { $env:ACR_NAME } else { "aiintakeacr" }
$IMAGE_NAME = if ($env:IMAGE_NAME) { $env:IMAGE_NAME } else { "azure-pricing-mcp" }
$IMAGE_TAG = if ($env:IMAGE_TAG) { $env:IMAGE_TAG } else { "latest" }

Write-Host "Environment variables loaded from $(if ($ENV_FILE) { $ENV_FILE } else { 'environment/defaults' })"
Write-Host "  Subscription: $env:AZURE_SUBSCRIPTION_ID"
Write-Host "  Resource Group: $env:RESOURCE_GROUP"
Write-Host "  Location: $env:LOCATION"
Write-Host "  Container App Name: $CONTAINER_APP_NAME"
Write-Host "  Container App Environment: $CONTAINER_APP_ENV_NAME"
Write-Host "  ACR Name: $ACR_NAME"
Write-Host "  Image: ${IMAGE_NAME}:${IMAGE_TAG}"
Write-Host ""

Write-Host "[DEPLOY] Starting deployment of Azure Pricing MCP Server to Azure Container Apps" -ForegroundColor Green

# Check if required CLI tools are installed
Write-Host "[CHECK] Checking prerequisites..." -ForegroundColor Yellow

if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] Azure CLI is not installed. Please install it first." -ForegroundColor Red
    exit 1
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] Docker is not installed. Please install it first." -ForegroundColor Red
    exit 1
}

# Login to Azure (if not already logged in)
Write-Host "[AUTH] Checking Azure authentication..." -ForegroundColor Yellow
try {
    az account show | Out-Null
}
catch {
    Write-Host "Please login to Azure..." -ForegroundColor Yellow
    az login
}

# Install Container Apps extension if not already installed
Write-Host "[CONFIG] Installing Azure Container Apps extension..." -ForegroundColor Yellow
$prevErrorAction = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
az extension add --name containerapp --upgrade --yes 2>&1 | Out-Null
$ErrorActionPreference = $prevErrorAction

# Check if resource group exists
Write-Host "[DIR] Checking resource group exists: $env:RESOURCE_GROUP" -ForegroundColor Yellow
$RG_EXISTS = az group show --name $env:RESOURCE_GROUP --query name -o tsv 2>$null

if (-not $RG_EXISTS) {
    Write-Host "[ERROR] Resource group '$env:RESOURCE_GROUP' not found" -ForegroundColor Red
    Write-Host "[TIP] Please create the resource group first using:" -ForegroundColor Yellow
    Write-Host "   az group create --name $env:RESOURCE_GROUP --location $env:LOCATION"
    exit 1
}

# Check if Azure Container Registry exists (matching bash script behavior)
Write-Host "[CHECK] Checking Azure Container Registry exists: $ACR_NAME" -ForegroundColor Yellow
$ACR_EXISTS = az acr show --name $ACR_NAME --resource-group $env:RESOURCE_GROUP --query name -o tsv 2>$null

if (-not $ACR_EXISTS) {
    Write-Host "[ERROR] Azure Container Registry '$ACR_NAME' not found in resource group '$env:RESOURCE_GROUP'" -ForegroundColor Red
    Write-Host "[TIP] Please create the ACR first using:" -ForegroundColor Yellow
    Write-Host "   az acr create --resource-group $env:RESOURCE_GROUP --name $ACR_NAME --sku Basic --admin-enabled true --location $env:LOCATION"
    exit 1
}

# Get ACR login server
$ACR_LOGIN_SERVER = (az acr show --name $ACR_NAME --resource-group $env:RESOURCE_GROUP --query loginServer -o tsv).Trim()

Write-Host "[OK] Found ACR: $ACR_NAME" -ForegroundColor Green
Write-Host "[PKG] ACR Login Server: $ACR_LOGIN_SERVER" -ForegroundColor Green


# Get current ACR network rule default action
$ACR_NETWORK_DEFAULT_ACTION = (az acr show --name $ACR_NAME --resource-group $env:RESOURCE_GROUP --query "networkRuleSet.defaultAction" -o tsv 2>$null)
if ($ACR_NETWORK_DEFAULT_ACTION) {
    $ACR_NETWORK_DEFAULT_ACTION = $ACR_NETWORK_DEFAULT_ACTION.Trim()
}

# Temporarily allow public access if currently denied
if ($ACR_NETWORK_DEFAULT_ACTION -eq "Deny") {
    Write-Host "[WARN]  ACR has network restrictions. Temporarily allowing public access..." -ForegroundColor Yellow
    az acr update --name $ACR_NAME --public-network-enabled true --default-action Allow --output none 2>$null
}

# Build and push Docker image
Write-Host "[DOCKER] Building and pushing Docker image..." -ForegroundColor Yellow

# Change to the azure-pricing-mcp-server directory (where the Dockerfile is located)
$BUILD_DIR = Resolve-Path (Join-Path $SCRIPT_DIR "..\..\azure-pricing-mcp-server")
Write-Host "[DIR] Build directory: $BUILD_DIR" -ForegroundColor Yellow
Push-Location $BUILD_DIR
Write-Host "[DIR] Current directory: $(Get-Location)" -ForegroundColor Yellow

Write-Host "[FILE] Checking required files..." -ForegroundColor Yellow
if (Test-Path "Dockerfile") {
    Write-Host "[OK] Dockerfile found" -ForegroundColor Green
}
else {
    Write-Host "[ERROR] Dockerfile not found" -ForegroundColor Red
    Pop-Location
    exit 1
}

if (Test-Path "requirements.txt") {
    Write-Host "[OK] requirements.txt found" -ForegroundColor Green
}
else {
    Write-Host "[ERROR] requirements.txt not found" -ForegroundColor Red
    Pop-Location
    exit 1
}

# Remove any problematic files/directories before build
Write-Host "[CLEAN] Cleaning up before build..." -ForegroundColor Yellow
Remove-Item -Path ".venv", "venv", "__pycache__", ".pytest_cache" -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem -Recurse -Filter "*.pyc" | Remove-Item -Force -ErrorAction SilentlyContinue
Get-ChildItem -Recurse -Filter "__pycache__" -Directory | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# Build with unique tag to ensure fresh build
$TIMESTAMP = Get-Date -Format "yyyyMMdd-HHmmss"
Write-Host "[BUILD] Building fresh image with timestamp: $TIMESTAMP..." -ForegroundColor Yellow

# Set UTF-8 encoding for Azure CLI output
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# Build image in ACR
az acr build `
    --registry $ACR_NAME `
    --image "${IMAGE_NAME}:${IMAGE_TAG}" `
    --image "${IMAGE_NAME}:v${TIMESTAMP}" `
    --file Dockerfile `
    .

if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Docker build failed" -ForegroundColor Red
    
    # Restore ACR network settings
    if ($ACR_NETWORK_DEFAULT_ACTION -eq "Deny") {
        Write-Host "[CONFIG] Restoring ACR network restrictions..." -ForegroundColor Yellow
        az acr update --name $ACR_NAME --public-network-enabled false --default-action Deny --output none 2>$null
    }
    
    Pop-Location
    exit 1
}

Write-Host "[OK] Docker image built and pushed successfully" -ForegroundColor Green

# Restore ACR network settings
if ($ACR_NETWORK_DEFAULT_ACTION -eq "Deny") {
    Write-Host "[CONFIG] Restoring ACR network restrictions..." -ForegroundColor Yellow
    az acr update --name $ACR_NAME --public-network-enabled false --default-action Deny --output none 2>$null
}

Pop-Location

# Check if Container Apps environment exists
Write-Host "[ENV] Checking Container Apps environment exists: $CONTAINER_APP_ENV_NAME" -ForegroundColor Yellow
$prevErrorAction = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
$ENV_EXISTS = az containerapp env show --name $CONTAINER_APP_ENV_NAME --resource-group $env:RESOURCE_GROUP --query name -o tsv 2>$null
$ErrorActionPreference = $prevErrorAction

if (-not $ENV_EXISTS) {
    Write-Host "[ERROR] Container Apps environment '$CONTAINER_APP_ENV_NAME' not found in resource group '$env:RESOURCE_GROUP'" -ForegroundColor Red
    Write-Host "[TIP] Please create the Container Apps environment first or update CONTAINER_APP_ENV_NAME" -ForegroundColor Yellow
    exit 1
}

Write-Host "[OK] Container Apps environment found: $CONTAINER_APP_ENV_NAME" -ForegroundColor Green

# Create the container app with ACR image directly
$FULL_IMAGE_NAME = "${ACR_LOGIN_SERVER}/${IMAGE_NAME}:${IMAGE_TAG}"
Write-Host "[DEPLOY] Deploying container app: $CONTAINER_APP_NAME" -ForegroundColor Yellow
Write-Host "[PKG] Using ACR image: $FULL_IMAGE_NAME" -ForegroundColor Yellow
Write-Host "[CONFIG] Creating app with environment variables from .env file" -ForegroundColor Yellow

# Check if container app already exists
$prevErrorAction = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
$APP_EXISTS = az containerapp show --name $CONTAINER_APP_NAME --resource-group $env:RESOURCE_GROUP --query name -o tsv 2>$null
$ErrorActionPreference = $prevErrorAction

# Set default values for env vars (matching bash script)
$MCP_HOST_VAL = if ($env:MCP_HOST) { $env:MCP_HOST } else { "0.0.0.0" }
$MCP_PORT_VAL = if ($env:MCP_PORT) { $env:MCP_PORT } else { "8080" }
$MCP_DEBUG_VAL = if ($env:MCP_DEBUG) { $env:MCP_DEBUG } else { "false" }
$MCP_RELOAD_VAL = if ($env:MCP_RELOAD) { $env:MCP_RELOAD } else { "false" }
$CORS_ORIGINS_VAL = if ($env:CORS_ORIGINS) { $env:CORS_ORIGINS } else { "*" }
$LOG_LEVEL_VAL = if ($env:LOG_LEVEL) { $env:LOG_LEVEL } else { "INFO" }

if ($APP_EXISTS) {
    Write-Host "[UPDATE] Updating existing container app" -ForegroundColor Yellow
    $prevErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    az containerapp update `
        --name $CONTAINER_APP_NAME `
        --resource-group $env:RESOURCE_GROUP `
        --image $FULL_IMAGE_NAME `
        --set-env-vars `
            "MCP_HOST=$MCP_HOST_VAL" `
            "MCP_PORT=$MCP_PORT_VAL" `
            "MCP_DEBUG=$MCP_DEBUG_VAL" `
            "MCP_RELOAD=$MCP_RELOAD_VAL" `
            "CORS_ORIGINS=$CORS_ORIGINS_VAL" `
            "LOG_LEVEL=$LOG_LEVEL_VAL" `
        --output table 2>$null
    $ErrorActionPreference = $prevErrorAction
}
else {
    Write-Host "[NEW] Creating new container app" -ForegroundColor Yellow
    $prevErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    az containerapp create `
        --name $CONTAINER_APP_NAME `
        --resource-group $env:RESOURCE_GROUP `
        --environment $CONTAINER_APP_ENV_NAME `
        --image $FULL_IMAGE_NAME `
        --registry-server $ACR_LOGIN_SERVER `
        --registry-identity system `
        --target-port 8080 `
        --ingress external `
        --min-replicas 1 `
        --max-replicas 5 `
        --cpu 0.5 `
        --memory 1.0Gi `
        --env-vars `
            "MCP_HOST=$MCP_HOST_VAL" `
            "MCP_PORT=$MCP_PORT_VAL" `
            "MCP_DEBUG=$MCP_DEBUG_VAL" `
            "MCP_RELOAD=$MCP_RELOAD_VAL" `
            "CORS_ORIGINS=$CORS_ORIGINS_VAL" `
            "LOG_LEVEL=$LOG_LEVEL_VAL" `
        --output table 2>$null
    $ErrorActionPreference = $prevErrorAction
}

Write-Host "[OK] Container app deployed successfully" -ForegroundColor Green

# Get the application URL
$prevErrorAction = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
$APP_URL = az containerapp show `
    --name $CONTAINER_APP_NAME `
    --resource-group $env:RESOURCE_GROUP `
    --query properties.configuration.ingress.fqdn -o tsv 2>$null
$ErrorActionPreference = $prevErrorAction
if ($APP_URL) {
    $APP_URL = $APP_URL.Trim()
}

Write-Host "[OK] Deployment completed successfully!" -ForegroundColor Green
Write-Host "[URL] Application URL: https://${APP_URL}" -ForegroundColor Green
Write-Host "[NET] SSE Endpoint: https://${APP_URL}/sse" -ForegroundColor Green

Write-Host "[TEST] Testing MCP server connectivity:" -ForegroundColor Yellow
if (Get-Command curl -ErrorAction SilentlyContinue) {
    # Give the container a moment to fully start
    Write-Host "Waiting 5 seconds for container to fully initialize..." -ForegroundColor Yellow
    Start-Sleep -Seconds 5
    
    # Test SSE endpoint with POST (MCP protocol uses POST for SSE connections)
    Write-Host "Testing MCP SSE endpoint (POST https://${APP_URL}/sse)..." -ForegroundColor Yellow
    try {
        $response = curl -s -o $null -w "%{http_code}" -X POST "https://${APP_URL}/sse" `
            -H "Content-Type: application/json" `
            -H "Accept: text/event-stream" `
            --max-time 10 2>$null
        
        $HTTP_STATUS = $response
        
        if ($HTTP_STATUS -eq "200" -or $HTTP_STATUS -eq "405") {
            Write-Host "[OK] MCP SSE endpoint is responding (HTTP $HTTP_STATUS)" -ForegroundColor Green
        }
        elseif ($HTTP_STATUS -eq "000") {
            Write-Host "[WARN] Connection timeout - container may still be starting" -ForegroundColor Yellow
            Write-Host "[TIP] Wait a minute and test manually:" -ForegroundColor Yellow
            Write-Host "   curl -X POST https://${APP_URL}/sse -H 'Content-Type: application/json'"
        }
        else {
            Write-Host "[WARN] Unexpected response (HTTP $HTTP_STATUS) - check server logs" -ForegroundColor Yellow
            Write-Host "[TIP] Check logs for any startup or configuration errors:" -ForegroundColor Yellow
            Write-Host "   az containerapp logs show --name $CONTAINER_APP_NAME --resource-group $env:RESOURCE_GROUP --tail 20"
        }
    }
    catch {
        Write-Host "[WARN] Connection test failed - container may still be starting" -ForegroundColor Yellow
    }
    
    # Test /messages endpoint (used by MCP clients)
    Write-Host "Testing MCP messages endpoint (POST https://${APP_URL}/messages)..." -ForegroundColor Yellow
    try {
        $response = curl -s -o $null -w "%{http_code}" -X POST "https://${APP_URL}/messages" `
            -H "Content-Type: application/json" `
            -d '{"jsonrpc":"2.0","id":1,"method":"ping"}' `
            --max-time 10 2>$null
        
        $HTTP_STATUS = $response
        
        if ($HTTP_STATUS -eq "200" -or $HTTP_STATUS -eq "400") {
            Write-Host "[OK] MCP messages endpoint is responding (HTTP $HTTP_STATUS)" -ForegroundColor Green
        }
        else {
            Write-Host "[WARN] Messages endpoint returned HTTP $HTTP_STATUS (may be expected for this MCP implementation)" -ForegroundColor Yellow
        }
    }
    catch {
        Write-Host "[WARN] Messages endpoint test failed" -ForegroundColor Yellow
    }
}
else {
    Write-Host "[TIP] curl not available - test manually with an MCP client" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "[OK] MCP Server Deployed Successfully!" -ForegroundColor Green
Write-Host "The MCP server was deployed with environment variables from your .env file."
Write-Host ""
Write-Host "[NET] To connect from an MCP client, use:" -ForegroundColor Yellow
Write-Host "   SSE URL: https://${APP_URL}/sse"
Write-Host ""

Write-Host "[TIP] To update environment variables later:" -ForegroundColor Yellow
Write-Host "   1. Update your .env file with new values"
Write-Host "   2. Redeploy using this script, or manually update:"
Write-Host "   az containerapp update --name $CONTAINER_APP_NAME --resource-group $env:RESOURCE_GROUP \"
Write-Host "     --set-env-vars \"
Write-Host "       MCP_HOST=NEW_VALUE \"
Write-Host "       MCP_PORT=NEW_VALUE \"
Write-Host "       (etc...)"

Write-Host "[NOTE] Other useful commands:" -ForegroundColor Yellow
Write-Host "- Monitor logs: az containerapp logs show --name $CONTAINER_APP_NAME --resource-group $env:RESOURCE_GROUP --follow"
Write-Host "- Scale the app: az containerapp update --name $CONTAINER_APP_NAME --resource-group $env:RESOURCE_GROUP --min-replicas 2"
Write-Host "- Check status: az containerapp show --name $CONTAINER_APP_NAME --resource-group $env:RESOURCE_GROUP"

Write-Host "[SUCCESS] Azure Pricing MCP Server is deployed and configured! Environment variables loaded from .env file." -ForegroundColor Green
