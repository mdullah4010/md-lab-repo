# Azure Container Apps Deployment Script for Insights API
# This script deploys the FastAPI application to Azure Container Apps
#
# Recent fixes applied:
# - Converted from bash to PowerShell for Windows compatibility
# - ACR auto-discovery from resource group
# - Automatic ACR admin and network access management
# - UTF-8 encoding fix for Azure CLI
# - Python cache cleanup before build to avoid import issues
# - Fresh builds using timestamp tags
# - Comprehensive environment variables configuration
# - Python path configuration (PYTHONPATH=/app)
# - Health endpoint testing
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
        "AZURE_EXISTING_AIPROJECT_ENDPOINT",
        "AZURE_AI_AGENT_DEPLOYMENT_NAME",
        "AZURE_SEARCH_ENDPOINT",
        "AZURE_SEARCH_INDEX",
        "AZURE_SEARCH_SEMANTIC_CONFIG",
        "AZURE_STORAGE_ACCOUNT_NAME",
        "AZURE_STORAGE_ACCOUNT_URL",
        "AZURE_TABLES_ACCOUNT_URL",
        "RESOURCE_GROUP",
        "LOCATION",
        "AZURE_INDEXING_FUNCTION_URL",
        "SCF_AZURE_SEARCH_INDEX",
        "MCP_ALLOWED_SERVERS",
        "AZURE_PRICING_MCP_URL"
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
    $REQUIRED_VARS = @(
        "RESOURCE_GROUP",
        "LOCATION",
        "AZURE_SUBSCRIPTION_ID"
    )
    
    $MISSING_VARS = @()
    foreach ($var in $REQUIRED_VARS) {
        $value = [Environment]::GetEnvironmentVariable($var)
        if ([string]::IsNullOrWhiteSpace($value)) {
            $MISSING_VARS += $var
        }
    }
    
    if ($MISSING_VARS.Count -gt 0) {
        Write-Host "[ERROR] Missing required environment variables:" -ForegroundColor Red
        $MISSING_VARS | ForEach-Object { Write-Host "   - $_" -ForegroundColor Red }
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

# Configuration variables
$CONTAINER_APP_NAME = if ($env:CONTAINER_APP_NAME) { $env:CONTAINER_APP_NAME } else { "insights-agent-api" }
$CONTAINER_APP_ENV_NAME = if ($env:CONTAINER_APP_ENV_NAME) { $env:CONTAINER_APP_ENV_NAME } else { "aca-env" }
$ACR_NAME = if ($env:ACR_NAME) { $env:ACR_NAME } else { "aiintakeacr" }
$IMAGE_NAME = if ($env:IMAGE_NAME) { $env:IMAGE_NAME } else { "insights-agent-api" }
$IMAGE_TAG = if ($env:IMAGE_TAG) { $env:IMAGE_TAG } else { "latest" }

Write-Host "Environment variables loaded from $ENV_FILE"
Write-Host "  Subscription: $env:AZURE_SUBSCRIPTION_ID"
Write-Host "  Resource Group: $env:RESOURCE_GROUP"
Write-Host "  Location: $env:LOCATION"
Write-Host "  Container App Name: $CONTAINER_APP_NAME"
Write-Host "  Container App Environment: $CONTAINER_APP_ENV_NAME"
Write-Host "  ACR Name: $ACR_NAME"
Write-Host "  Image: ${IMAGE_NAME}:${IMAGE_TAG}"
Write-Host ""

Write-Host "[DEPLOY] Starting deployment of Insights Agent API to Azure Container Apps" -ForegroundColor Green

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

# Create Azure Container Registry if it doesn't exist
Write-Host "[CREATE] Checking Azure Container Registry: $ACR_NAME" -ForegroundColor Yellow
$ACR_EXISTS = az acr show --name $ACR_NAME --resource-group $env:RESOURCE_GROUP --query name -o tsv 2>$null

if ($ACR_EXISTS) {
    Write-Host "[OK] ACR already exists in resource group: $ACR_NAME" -ForegroundColor Green
}
else {
    Write-Host "[CREATE] Creating Azure Container Registry: $ACR_NAME" -ForegroundColor Yellow
    $createResult = az acr create `
        --resource-group $env:RESOURCE_GROUP `
        --name $ACR_NAME `
        --sku Basic `
        --admin-enabled true `
        --location $env:LOCATION `
        --output table 2>&1
    
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Failed to create ACR with name: $ACR_NAME" -ForegroundColor Red
        Write-Host "[TIP] The ACR name '$ACR_NAME' is already taken globally." -ForegroundColor Yellow
        Write-Host "[NOTE] Please update the ACR_NAME variable and run again." -ForegroundColor Yellow
        exit 1
    }
}

# Get ACR login server
$ACR_LOGIN_SERVER = (az acr show --name $ACR_NAME --resource-group $env:RESOURCE_GROUP --query loginServer -o tsv).Trim()

# Validate ACR login server
if ([string]::IsNullOrWhiteSpace($ACR_LOGIN_SERVER)) {
    Write-Host "[ERROR] Failed to get ACR login server" -ForegroundColor Red
    exit 1
}

if ($ACR_LOGIN_SERVER -notmatch '^[a-zA-Z0-9]+\.azurecr\.io$') {
    Write-Host "[ERROR] Invalid ACR login server format: '$ACR_LOGIN_SERVER'" -ForegroundColor Red
    exit 1
}

Write-Host "[PKG] ACR Login Server: $ACR_LOGIN_SERVER" -ForegroundColor Green

# Build and push Docker image
Write-Host "[DOCKER] Building and pushing Docker image..." -ForegroundColor Yellow

# Get current ACR network rule default action
$ACR_NETWORK_DEFAULT_ACTION = (az acr show --name $ACR_NAME --resource-group $env:RESOURCE_GROUP --query "networkRuleSet.defaultAction" -o tsv 2>$null)
if ($ACR_NETWORK_DEFAULT_ACTION) {
    $ACR_NETWORK_DEFAULT_ACTION = $ACR_NETWORK_DEFAULT_ACTION.Trim()
}

# Temporarily allow public access if currently denied
if ($ACR_NETWORK_DEFAULT_ACTION -eq "Deny") {
    Write-Host "[WARN] ACR has network restrictions. Temporarily allowing public access for build..." -ForegroundColor Yellow
    az acr update --name $ACR_NAME --public-network-enabled true --default-action Allow --output none 2>$null
}

# Change to the insights-agent root directory where Dockerfile and .dockerignore are located
$SCRIPT_DIR = Split-Path -Parent $PSCommandPath
$BUILD_DIR = Resolve-Path (Join-Path $SCRIPT_DIR "../..")
Write-Host "[DIR] Changing to build directory: $BUILD_DIR" -ForegroundColor Yellow
Push-Location $BUILD_DIR
Write-Host "[DIR] Current directory: $(Get-Location)" -ForegroundColor Yellow

Write-Host "[FILE] Checking .dockerignore exists..." -ForegroundColor Yellow
if (Test-Path ".dockerignore") {
    Write-Host "[OK] .dockerignore found" -ForegroundColor Green
}
else {
    Write-Host "[ERROR] .dockerignore not found - this may cause issues" -ForegroundColor Red
}

# Remove any problematic files/directories before build
Write-Host "[CLEAN] Cleaning up before build (including Python cache)..." -ForegroundColor Yellow
Remove-Item -Path ".venv", "venv", "__pycache__", ".pytest_cache" -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem -Recurse -Filter "*.pyc" | Remove-Item -Force -ErrorAction SilentlyContinue
Get-ChildItem -Recurse -Filter "__pycache__" -Directory | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# Build with unique tag to ensure fresh build (ACR doesn't support --no-cache)
$TIMESTAMP = Get-Date -Format "yyyyMMdd-HHmmss"
Write-Host "[BUILD] Building fresh image with timestamp: $TIMESTAMP..." -ForegroundColor Yellow

# Set UTF-8 encoding for Azure CLI output
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$BUILD_SUCCESS = $false

az acr build `
    --registry $ACR_NAME `
    --image "${IMAGE_NAME}:${IMAGE_TAG}" `
    --image "${IMAGE_NAME}:v${TIMESTAMP}" `
    --file Dockerfile `
    .

if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Docker build failed. Trying alternative approach..." -ForegroundColor Red
    
    # Create a clean build directory
    $TEMP_BUILD_DIR = Join-Path $env:TEMP "docker-build-${CONTAINER_APP_NAME}"
    New-Item -ItemType Directory -Path $TEMP_BUILD_DIR -Force | Out-Null
    Copy-Item -Path "agents", "requirements.txt", "Dockerfile", ".dockerignore" -Destination $TEMP_BUILD_DIR -Recurse -Force -ErrorAction SilentlyContinue
    Push-Location $TEMP_BUILD_DIR
    
    # Clean Python cache in temp directory too
    Get-ChildItem -Recurse -Filter "*.pyc" | Remove-Item -Force -ErrorAction SilentlyContinue
    Get-ChildItem -Recurse -Filter "__pycache__" -Directory | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    
    az acr build `
        --registry $ACR_NAME `
        --image "${IMAGE_NAME}:${IMAGE_TAG}" `
        --image "${IMAGE_NAME}:v${TIMESTAMP}" `
        --file Dockerfile `
        .
    
    Pop-Location
    Remove-Item -Path $TEMP_BUILD_DIR -Recurse -Force -ErrorAction SilentlyContinue
    
    if ($LASTEXITCODE -eq 0) {
        $BUILD_SUCCESS = $true
    }
}
else {
    $BUILD_SUCCESS = $true
}

# Restore ACR network settings after build
if ($ACR_NETWORK_DEFAULT_ACTION -eq "Deny") {
    Write-Host "[CONFIG] Restoring ACR network restrictions..." -ForegroundColor Yellow
    az acr update --name $ACR_NAME --public-network-enabled false --default-action Deny --output none 2>$null
}


if (-not $BUILD_SUCCESS) {
    Write-Host "[ERROR] Docker build failed" -ForegroundColor Red
    Pop-Location
    exit 1
}

Write-Host "[OK] Docker image built and pushed successfully" -ForegroundColor Green

Pop-Location

# Create Container Apps environment if it doesn't exist
Write-Host "[ENV] Checking Container Apps environment: $CONTAINER_APP_ENV_NAME" -ForegroundColor Yellow

$prevErrorAction = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
$ENV_EXISTS = az containerapp env show --name $CONTAINER_APP_ENV_NAME --resource-group $env:RESOURCE_GROUP --query name -o tsv 2>$null
$ErrorActionPreference = $prevErrorAction

if ($ENV_EXISTS -and $ENV_EXISTS -notmatch "ERROR") {
    Write-Host "[OK] Container Apps environment already exists: $CONTAINER_APP_ENV_NAME" -ForegroundColor Green
}
else {
    Write-Host "[CREATE] Creating Container Apps environment: $CONTAINER_APP_ENV_NAME" -ForegroundColor Yellow
    
    # Check if Log Analytics workspace exists
    $LOG_ANALYTICS_WORKSPACE_NAME = "ai-foundry-std-log-analytics"
    
    $WORKSPACE_EXISTS = az monitor log-analytics workspace show `
        --resource-group $env:RESOURCE_GROUP `
        --workspace-name $LOG_ANALYTICS_WORKSPACE_NAME `
        --query name -o tsv 2>$null
    
    if ($WORKSPACE_EXISTS) {
        Write-Host "[OK] Using existing Log Analytics workspace: $LOG_ANALYTICS_WORKSPACE_NAME" -ForegroundColor Green
        
        # Get the workspace ID and shared key
        Write-Host "[CHECK] Retrieving Log Analytics workspace credentials..." -ForegroundColor Yellow
        
        $LOG_ANALYTICS_WORKSPACE_ID = (az monitor log-analytics workspace show `
            --resource-group $env:RESOURCE_GROUP `
            --workspace-name $LOG_ANALYTICS_WORKSPACE_NAME `
            --query customerId -o tsv).Trim()
        
        $LOG_ANALYTICS_SHARED_KEY = (az monitor log-analytics workspace get-shared-keys `
            --resource-group $env:RESOURCE_GROUP `
            --workspace-name $LOG_ANALYTICS_WORKSPACE_NAME `
            --query primarySharedKey -o tsv).Trim()
        
        # Validate workspace ID format (should be a GUID)
        if ($LOG_ANALYTICS_WORKSPACE_ID -match '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$') {
            Write-Host "[OK] Log Analytics workspace ID: $LOG_ANALYTICS_WORKSPACE_ID" -ForegroundColor Green
            Write-Host "[OK] Log Analytics shared key length: $($LOG_ANALYTICS_SHARED_KEY.Length) characters" -ForegroundColor Green
            
            # Create Container Apps environment with existing Log Analytics workspace
            az containerapp env create `
                --name $CONTAINER_APP_ENV_NAME `
                --resource-group $env:RESOURCE_GROUP `
                --location $env:LOCATION `
                --logs-workspace-id $LOG_ANALYTICS_WORKSPACE_ID `
                --logs-workspace-key $LOG_ANALYTICS_SHARED_KEY `
                --output table
        }
        else {
            Write-Host "[ERROR] Invalid Log Analytics workspace ID format: $LOG_ANALYTICS_WORKSPACE_ID" -ForegroundColor Red
            Write-Host "[LOG] Falling back to creating Container Apps environment with new workspace..." -ForegroundColor Yellow
            
            # Create Container Apps environment (will create a new Log Analytics workspace)
            az containerapp env create `
                --name $CONTAINER_APP_ENV_NAME `
                --resource-group $env:RESOURCE_GROUP `
                --location $env:LOCATION `
                --output table
        }
    }
    else {
        Write-Host "[LOG] Log Analytics workspace doesn't exist. Creating Container Apps environment with new workspace..." -ForegroundColor Yellow
        
        # Create Container Apps environment (will create a new Log Analytics workspace)
        az containerapp env create `
            --name $CONTAINER_APP_ENV_NAME `
            --resource-group $env:RESOURCE_GROUP `
            --location $env:LOCATION `
            --output table
    }
}

# Enable system-assigned managed identity for the container app to access ACR
Write-Host "[AUTH] Container app will use system-assigned managed identity for ACR access" -ForegroundColor Yellow

# Debug: Print all variables
Write-Host "[DEBUG] Debug - Deployment variables:" -ForegroundColor Yellow
Write-Host "  ACR_NAME: $ACR_NAME"
Write-Host "  ACR_LOGIN_SERVER: $ACR_LOGIN_SERVER"
Write-Host "  IMAGE_NAME: $IMAGE_NAME"
Write-Host "  IMAGE_TAG: $IMAGE_TAG"

# Check if Container App exists
Write-Host "==> Checking if Container App exists" -ForegroundColor Yellow
$prevErrorAction = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
$APP_EXISTS = az containerapp show `
    --name $CONTAINER_APP_NAME `
    --resource-group $env:RESOURCE_GROUP `
    --query name -o tsv 2>$null
$ErrorActionPreference = $prevErrorAction

$FULL_IMAGE_NAME = "${ACR_LOGIN_SERVER}/${IMAGE_NAME}:${IMAGE_TAG}"
Write-Host "[DEPLOY] Deploying container app: $CONTAINER_APP_NAME" -ForegroundColor Yellow
Write-Host "[PKG] Using ACR image: $FULL_IMAGE_NAME" -ForegroundColor Yellow

# Get deployment timestamp and git commit
$DEPLOYMENT_TIME = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.ffffffZ")
$GIT_COMMIT = try { git rev-parse HEAD 2>$null } catch { "unknown" }

if (-not $APP_EXISTS) {
    Write-Host "==> Creating new Container App" -ForegroundColor Yellow
    Write-Host "[CONFIG] Creating app with environment variables from .env file" -ForegroundColor Yellow
    
    # Set default values for optional environment variables
    $AZURE_AI_AGENT_DEPLOYMENT_NAME_VAL = if ($env:AZURE_AI_AGENT_DEPLOYMENT_NAME) { $env:AZURE_AI_AGENT_DEPLOYMENT_NAME } else { "gpt-4.1" }
    $AZURE_SEARCH_SEMANTIC_CONFIG_VAL = if ($env:AZURE_SEARCH_SEMANTIC_CONFIG) { $env:AZURE_SEARCH_SEMANTIC_CONFIG } else { "DefaultSemantic" }
    $APP_VERBOSE_VAL = if ($env:APP_VERBOSE) { $env:APP_VERBOSE } else { "1" }
    $LOG_LEVEL_VAL = if ($env:LOG_LEVEL) { $env:LOG_LEVEL } else { "INFO" }
    
    az containerapp create `
        --name $CONTAINER_APP_NAME `
        --resource-group $env:RESOURCE_GROUP `
        --environment $CONTAINER_APP_ENV_NAME `
        --image $FULL_IMAGE_NAME `
        --registry-server $ACR_LOGIN_SERVER `
        --registry-identity system `
        --target-port 8000 `
        --ingress external `
        --min-replicas 1 `
        --max-replicas 10 `
        --cpu 1.0 `
        --memory 2.0Gi `
        --env-vars `
            "AZURE_EXISTING_AIPROJECT_ENDPOINT=$env:AZURE_EXISTING_AIPROJECT_ENDPOINT" `
            "AZURE_AI_AGENT_DEPLOYMENT_NAME=$AZURE_AI_AGENT_DEPLOYMENT_NAME_VAL" `
            "AZURE_SEARCH_ENDPOINT=$env:AZURE_SEARCH_ENDPOINT" `
            "AZURE_SEARCH_INDEX=$env:AZURE_SEARCH_INDEX" `
            "AZURE_SEARCH_SEMANTIC_CONFIG=$AZURE_SEARCH_SEMANTIC_CONFIG_VAL" `
            "AZURE_STORAGE_ACCOUNT_NAME=$env:AZURE_STORAGE_ACCOUNT_NAME" `
            "AZURE_STORAGE_ACCOUNT_URL=$env:AZURE_STORAGE_ACCOUNT_URL" `
            "AZURE_TABLES_ACCOUNT_URL=$env:AZURE_TABLES_ACCOUNT_URL" `
            "AZURE_SUBSCRIPTION_ID=$env:AZURE_SUBSCRIPTION_ID" `
            "RESOURCE_GROUP=$env:RESOURCE_GROUP" `
            "LOCATION=$env:LOCATION" `
            "AZURE_INDEXING_FUNCTION_URL=$env:AZURE_INDEXING_FUNCTION_URL" `
            "SCF_AZURE_SEARCH_INDEX=$env:SCF_AZURE_SEARCH_INDEX" `
            "MCP_ALLOWED_SERVERS=$env:MCP_ALLOWED_SERVERS" `
            "AZURE_PRICING_MCP_URL=$env:AZURE_PRICING_MCP_URL" `
            "APPLICATIONINSIGHTS_CONNECTION_STRING=$env:APPLICATIONINSIGHTS_CONNECTION_STRING" `
            "PYTHONPATH=/app" `
            "APP_VERBOSE=$APP_VERBOSE_VAL" `
            "LOG_LEVEL=$LOG_LEVEL_VAL" `
            "BUILD_NUMBER=$TIMESTAMP" `
            "GIT_COMMIT=$GIT_COMMIT" `
            "DEPLOYMENT_TIME=$DEPLOYMENT_TIME" `
        --output table
    
    Write-Host "[OK] Container app created with ACR image and managed identity configured" -ForegroundColor Green
}
else {
    Write-Host "==> Updating existing Container App" -ForegroundColor Yellow
    
    # Get the managed identity principal ID
    $prevErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    $IDENTITY_OBJECT_ID = (az containerapp identity show `
        --name $CONTAINER_APP_NAME `
        --resource-group $env:RESOURCE_GROUP `
        --query principalId -o tsv 2>$null)
    $ErrorActionPreference = $prevErrorAction
    if ($IDENTITY_OBJECT_ID) { $IDENTITY_OBJECT_ID = $IDENTITY_OBJECT_ID.Trim() }
    
    if ([string]::IsNullOrWhiteSpace($IDENTITY_OBJECT_ID)) {
        Write-Host "No managed identity found, enabling system-assigned identity..." -ForegroundColor Yellow
        $prevErrorAction = $ErrorActionPreference
        $ErrorActionPreference = "SilentlyContinue"
        az containerapp identity assign `
            --name $CONTAINER_APP_NAME `
            --resource-group $env:RESOURCE_GROUP `
            --system-assigned 2>$null | Out-Null
        
        # Get the new identity
        $IDENTITY_OBJECT_ID = (az containerapp identity show `
            --name $CONTAINER_APP_NAME `
            --resource-group $env:RESOURCE_GROUP `
            --query principalId -o tsv 2>$null)
        $ErrorActionPreference = $prevErrorAction
        if ($IDENTITY_OBJECT_ID) { $IDENTITY_OBJECT_ID = $IDENTITY_OBJECT_ID.Trim() }
    }
    
    Write-Host "Managed identity principal ID: $IDENTITY_OBJECT_ID" -ForegroundColor Yellow
    
    # Ensure AcrPull role is assigned
    Write-Host "Assigning AcrPull role to managed identity..." -ForegroundColor Yellow
    try {
        az role assignment create `
            --assignee-object-id $IDENTITY_OBJECT_ID `
            --role "AcrPull" `
            --scope "/subscriptions/$env:AZURE_SUBSCRIPTION_ID/resourceGroups/$env:RESOURCE_GROUP/providers/Microsoft.ContainerRegistry/registries/$ACR_NAME" `
            2>$null
        Write-Host "[OK] AcrPull role assigned" -ForegroundColor Green
    }
    catch {
        Write-Host "[WARN] AcrPull role already assigned or failed" -ForegroundColor Yellow
    }
    
    # Configure registry authentication
    Write-Host "Configuring registry authentication..." -ForegroundColor Yellow
    az containerapp registry set `
        --name $CONTAINER_APP_NAME `
        --resource-group $env:RESOURCE_GROUP `
        --server $ACR_LOGIN_SERVER `
        --identity system
    
    # Set default values for optional environment variables
    $AZURE_AI_AGENT_DEPLOYMENT_NAME_VAL = if ($env:AZURE_AI_AGENT_DEPLOYMENT_NAME) { $env:AZURE_AI_AGENT_DEPLOYMENT_NAME } else { "gpt-4.1" }
    $AZURE_SEARCH_SEMANTIC_CONFIG_VAL = if ($env:AZURE_SEARCH_SEMANTIC_CONFIG) { $env:AZURE_SEARCH_SEMANTIC_CONFIG } else { "DefaultSemantic" }
    $APP_VERBOSE_VAL = if ($env:APP_VERBOSE) { $env:APP_VERBOSE } else { "1" }
    $LOG_LEVEL_VAL = if ($env:LOG_LEVEL) { $env:LOG_LEVEL } else { "INFO" }
    
    # Update Container App image and environment variables
    Write-Host "[CONFIG] Updating app with environment variables from .env file" -ForegroundColor Yellow
    az containerapp update `
        --name $CONTAINER_APP_NAME `
        --resource-group $env:RESOURCE_GROUP `
        --image $FULL_IMAGE_NAME `
        --set-env-vars `
            "AZURE_EXISTING_AIPROJECT_ENDPOINT=$env:AZURE_EXISTING_AIPROJECT_ENDPOINT" `
            "AZURE_AI_AGENT_DEPLOYMENT_NAME=$AZURE_AI_AGENT_DEPLOYMENT_NAME_VAL" `
            "AZURE_SEARCH_ENDPOINT=$env:AZURE_SEARCH_ENDPOINT" `
            "AZURE_SEARCH_INDEX=$env:AZURE_SEARCH_INDEX" `
            "AZURE_SEARCH_SEMANTIC_CONFIG=$AZURE_SEARCH_SEMANTIC_CONFIG_VAL" `
            "AZURE_STORAGE_ACCOUNT_NAME=$env:AZURE_STORAGE_ACCOUNT_NAME" `
            "AZURE_STORAGE_ACCOUNT_URL=$env:AZURE_STORAGE_ACCOUNT_URL" `
            "AZURE_TABLES_ACCOUNT_URL=$env:AZURE_TABLES_ACCOUNT_URL" `
            "AZURE_SUBSCRIPTION_ID=$env:AZURE_SUBSCRIPTION_ID" `
            "RESOURCE_GROUP=$env:RESOURCE_GROUP" `
            "LOCATION=$env:LOCATION" `
            "AZURE_INDEXING_FUNCTION_URL=$env:AZURE_INDEXING_FUNCTION_URL" `
            "SCF_AZURE_SEARCH_INDEX=$env:SCF_AZURE_SEARCH_INDEX" `
            "MCP_ALLOWED_SERVERS=$env:MCP_ALLOWED_SERVERS" `
            "AZURE_PRICING_MCP_URL=$env:AZURE_PRICING_MCP_URL" `
            "APPLICATIONINSIGHTS_CONNECTION_STRING=$env:APPLICATIONINSIGHTS_CONNECTION_STRING" `
            "PYTHONPATH=/app" `
            "APP_VERBOSE=$APP_VERBOSE_VAL" `
            "LOG_LEVEL=$LOG_LEVEL_VAL" `
            "DEPLOYMENT_TIME=$DEPLOYMENT_TIME"
}

# Assign RBAC roles to the managed identity
Write-Host "[AUTH] Assigning RBAC roles to managed identity..." -ForegroundColor Yellow

# Get the managed identity principal ID
$prevErrorAction = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
$IDENTITY_OBJECT_ID = (az containerapp identity show `
    --name $CONTAINER_APP_NAME `
    --resource-group $env:RESOURCE_GROUP `
    --query principalId -o tsv 2>$null)
$ErrorActionPreference = $prevErrorAction
if ($IDENTITY_OBJECT_ID) { $IDENTITY_OBJECT_ID = $IDENTITY_OBJECT_ID.Trim() }

Write-Host "Managed identity principalId: $IDENTITY_OBJECT_ID" -ForegroundColor Yellow

if (![string]::IsNullOrWhiteSpace($IDENTITY_OBJECT_ID)) {
    # Extract service names from environment variables
    $AZURE_SEARCH_SERVICE_NAME = ""
    $STORAGE_ACCOUNT_NAME = ""
    
    # Extract search service name from AZURE_SEARCH_ENDPOINT
    if (![string]::IsNullOrWhiteSpace($env:AZURE_SEARCH_ENDPOINT)) {
        if ($env:AZURE_SEARCH_ENDPOINT -match 'https://([^.]+)\.search\.windows\.net') {
            $AZURE_SEARCH_SERVICE_NAME = $Matches[1]
        }
    }
    
    # Extract storage account name from AZURE_STORAGE_ACCOUNT_URL
    if (![string]::IsNullOrWhiteSpace($env:AZURE_STORAGE_ACCOUNT_URL)) {
        if ($env:AZURE_STORAGE_ACCOUNT_URL -match 'https://([^.]+)\.blob\.core\.windows\.net') {
            $STORAGE_ACCOUNT_NAME = $Matches[1]
        }
    }

    # Log extracted RBAC variables
    Write-Host "[DEBUG] Debug - RBAC Variables:" -ForegroundColor Yellow
    Write-Host "  IDENTITY_OBJECT_ID: $IDENTITY_OBJECT_ID"
    Write-Host "  AZURE_SEARCH_SERVICE_NAME: $AZURE_SEARCH_SERVICE_NAME"
    Write-Host "  STORAGE_ACCOUNT_NAME: $STORAGE_ACCOUNT_NAME"
    Write-Host "  AZURE_AI_PROJECT_NAME: $env:AZURE_AI_PROJECT_NAME"
    Write-Host "  RESOURCE_GROUP: $env:RESOURCE_GROUP"
    Write-Host "  AZURE_SUBSCRIPTION_ID: $env:AZURE_SUBSCRIPTION_ID"
    
    Write-Host "Assigning RBAC roles (will skip if already assigned)..." -ForegroundColor Yellow
    
    # Search Index Data Reader on the AI Search service
    if (![string]::IsNullOrWhiteSpace($AZURE_SEARCH_SERVICE_NAME)) {
        try {
            az role assignment create `
                --assignee-object-id $IDENTITY_OBJECT_ID `
                --role "Search Index Data Reader" `
                --scope "/subscriptions/$env:AZURE_SUBSCRIPTION_ID/resourceGroups/$env:RESOURCE_GROUP/providers/Microsoft.Search/searchServices/$AZURE_SEARCH_SERVICE_NAME" `
                2>$null
            Write-Host "[OK] Search Index Data Reader role assigned" -ForegroundColor Green
        }
        catch {
            Write-Host "[WARN] Search Index role already assigned or failed" -ForegroundColor Yellow
        }
        
        # Search Service Contributor on the AI Search service
        try {
            az role assignment create `
                --assignee-object-id $IDENTITY_OBJECT_ID `
                --role "Search Service Contributor" `
                --scope "/subscriptions/$env:AZURE_SUBSCRIPTION_ID/resourceGroups/$env:RESOURCE_GROUP/providers/Microsoft.Search/searchServices/$AZURE_SEARCH_SERVICE_NAME" `
                2>$null
            Write-Host "[OK] Search Service Contributor role assigned" -ForegroundColor Green
        }
        catch {
            Write-Host "[WARN] Search Service role already assigned or failed" -ForegroundColor Yellow
        }
    }
    else {
        Write-Host "[WARN] Could not extract search service name from AZURE_SEARCH_ENDPOINT" -ForegroundColor Red
    }
    
    # Storage roles on the Storage Account
    if (![string]::IsNullOrWhiteSpace($STORAGE_ACCOUNT_NAME)) {
        # Storage Blob Data Contributor
        try {
            az role assignment create `
                --assignee-object-id $IDENTITY_OBJECT_ID `
                --role "Storage Blob Data Contributor" `
                --scope "/subscriptions/$env:AZURE_SUBSCRIPTION_ID/resourceGroups/$env:RESOURCE_GROUP/providers/Microsoft.Storage/storageAccounts/$STORAGE_ACCOUNT_NAME" `
                2>$null
            Write-Host "[OK] Storage Blob Data Contributor role assigned" -ForegroundColor Green
        }
        catch {
            Write-Host "[WARN] Storage Blob role already assigned or failed" -ForegroundColor Yellow
        }
        
        # Storage Table Data Contributor
        try {
            az role assignment create `
                --assignee-object-id $IDENTITY_OBJECT_ID `
                --role "Storage Table Data Contributor" `
                --scope "/subscriptions/$env:AZURE_SUBSCRIPTION_ID/resourceGroups/$env:RESOURCE_GROUP/providers/Microsoft.Storage/storageAccounts/$STORAGE_ACCOUNT_NAME" `
                2>$null
            Write-Host "[OK] Storage Table Data Contributor role assigned" -ForegroundColor Green
        }
        catch {
            Write-Host "[WARN] Storage Table role already assigned or failed" -ForegroundColor Yellow
        }
        
        # RBAC Administrator on the Storage Account
        try {
            az role assignment create `
                --assignee-object-id $IDENTITY_OBJECT_ID `
                --role "Role Based Access Control Administrator" `
                --scope "/subscriptions/$env:AZURE_SUBSCRIPTION_ID/resourceGroups/$env:RESOURCE_GROUP/providers/Microsoft.Storage/storageAccounts/$STORAGE_ACCOUNT_NAME" `
                2>$null
            Write-Host "[OK] Role Based Access Control Administrator role assigned" -ForegroundColor Green
        }
        catch {
            Write-Host "[WARN] Role Based Access Control Administrator role already assigned or failed" -ForegroundColor Yellow
        }
    }
    else {
        Write-Host "[WARN] Could not extract storage account name from AZURE_STORAGE_ACCOUNT_URL" -ForegroundColor Red
    }
    
    # Cognitive Services roles on the AI Foundry service
    if (![string]::IsNullOrWhiteSpace($env:AZURE_AI_PROJECT_NAME)) {
        # Cognitive Services OpenAI User
        try {
            az role assignment create `
                --assignee-object-id $IDENTITY_OBJECT_ID `
                --role "Cognitive Services OpenAI User" `
                --scope "/subscriptions/$env:AZURE_SUBSCRIPTION_ID/resourceGroups/$env:RESOURCE_GROUP/providers/Microsoft.CognitiveServices/accounts/$env:AZURE_AI_PROJECT_NAME" `
                2>$null
            Write-Host "[OK] Cognitive Services OpenAI User role assigned" -ForegroundColor Green
        }
        catch {
            Write-Host "[WARN] Cognitive Services OpenAI User role already assigned or failed" -ForegroundColor Yellow
        }
        
        # Cognitive Services User
        try {
            az role assignment create `
                --assignee-object-id $IDENTITY_OBJECT_ID `
                --role "Cognitive Services User" `
                --scope "/subscriptions/$env:AZURE_SUBSCRIPTION_ID/resourceGroups/$env:RESOURCE_GROUP/providers/Microsoft.CognitiveServices/accounts/$env:AZURE_AI_PROJECT_NAME" `
                2>$null
            Write-Host "[OK] Cognitive Services User role assigned" -ForegroundColor Green
        }
        catch {
            Write-Host "[WARN] Cognitive Services User role already assigned or failed" -ForegroundColor Yellow
        }
        
        # Azure AI User
        try {
            az role assignment create `
                --assignee-object-id $IDENTITY_OBJECT_ID `
                --role "Azure AI User" `
                --scope "/subscriptions/$env:AZURE_SUBSCRIPTION_ID/resourceGroups/$env:RESOURCE_GROUP/providers/Microsoft.CognitiveServices/accounts/$env:AZURE_AI_PROJECT_NAME" `
                2>$null
            Write-Host "[OK] Azure AI User role assigned" -ForegroundColor Green
        }
        catch {
            Write-Host "[WARN] Azure AI User role already assigned or failed" -ForegroundColor Yellow
        }
    }
    else {
        Write-Host "[WARN] AZURE_AI_PROJECT_NAME not found - skipping Cognitive Services role assignments" -ForegroundColor Red
    }
    
    # Reader on the Resource Group
    try {
        az role assignment create `
            --assignee-object-id $IDENTITY_OBJECT_ID `
            --role "Reader" `
            --scope "/subscriptions/$env:AZURE_SUBSCRIPTION_ID/resourceGroups/$env:RESOURCE_GROUP" `
            2>$null
        Write-Host "[OK] Reader role assigned on resource group" -ForegroundColor Green
    }
    catch {
        Write-Host "[WARN] Reader role already assigned or failed" -ForegroundColor Yellow
    }
    
    Write-Host "[OK] RBAC role assignment completed" -ForegroundColor Green
}
else {
    Write-Host "[ERROR] Failed to get managed identity principal ID" -ForegroundColor Red
}

# Get the application URL
$prevErrorAction = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
$APP_URL = (az containerapp show `
    --name $CONTAINER_APP_NAME `
    --resource-group $env:RESOURCE_GROUP `
    --query properties.configuration.ingress.fqdn -o tsv 2>$null)
$ErrorActionPreference = $prevErrorAction
if ($APP_URL) { $APP_URL = $APP_URL.Trim() }

Write-Host "[OK] Deployment completed successfully!" -ForegroundColor Green
Write-Host "[URL] Application URL: https://${APP_URL}" -ForegroundColor Green
Write-Host "[WARN]  Health Check will FAIL until environment variables are configured: https://${APP_URL}/health" -ForegroundColor Red
Write-Host "[DOCS] API Documentation: https://${APP_URL}/docs" -ForegroundColor Green

Write-Host "[OK] Environment Variables Configured" -ForegroundColor Green
Write-Host "The app was deployed with environment variables from your .env file."

Write-Host "[TIP] To update environment variables later:" -ForegroundColor Yellow
Write-Host "   1. Update your .env file with new values"
Write-Host "   2. Redeploy using this script, or manually update:"
Write-Host "   az containerapp update --name $CONTAINER_APP_NAME --resource-group $env:RESOURCE_GROUP \"
Write-Host "     --set-env-vars \"
Write-Host "       AZURE_EXISTING_AIPROJECT_ENDPOINT=NEW_VALUE \"
Write-Host "       AZURE_SEARCH_ENDPOINT=NEW_VALUE \"
Write-Host "       (etc...)"

Write-Host "[NOTE] Other useful commands:" -ForegroundColor Yellow
Write-Host "- Monitor logs: az containerapp logs show --name $CONTAINER_APP_NAME --resource-group $env:RESOURCE_GROUP --follow"
Write-Host "- Scale the app: az containerapp update --name $CONTAINER_APP_NAME --resource-group $env:RESOURCE_GROUP --min-replicas 2"
Write-Host "- Check status: az containerapp show --name $CONTAINER_APP_NAME --resource-group $env:RESOURCE_GROUP"

Write-Host "[TEST] Testing health endpoint:" -ForegroundColor Yellow
if (Get-Command curl -ErrorAction SilentlyContinue) {
    Write-Host "Testing https://${APP_URL}/health..." -ForegroundColor Yellow
    try {
        $response = curl -f "https://${APP_URL}/health" -w "`nHTTP Status: %{http_code}`n" 2>$null
        Write-Host $response
        Write-Host "[OK] Health check passed!" -ForegroundColor Green
    }
    catch {
        Write-Host "[ERROR] Health check failed" -ForegroundColor Red
        Write-Host "[TIP] Check logs for any startup or configuration errors:" -ForegroundColor Yellow
        Write-Host "   az containerapp logs show --name $CONTAINER_APP_NAME --resource-group $env:RESOURCE_GROUP --tail 20"
    }
}
else {
    Write-Host "[TIP] curl not available - test manually: https://${APP_URL}/health" -ForegroundColor Yellow
}

Write-Host "[SUCCESS] Your API is deployed and configured! Environment variables loaded from .env file." -ForegroundColor Green
