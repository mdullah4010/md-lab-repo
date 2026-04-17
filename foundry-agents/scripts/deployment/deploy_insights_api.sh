#!/bin/bash

# Azure Container Apps Deployment Script for AI Assessment, Design and Planning (ADP) API
# This script deploys the FastAPI application to Azure Container Apps
#
# Recent fixes applied:
# - Python cache cleanup before build to avoid import issues
# - Fresh builds using timestamp tags (ACR doesn't support --no-cache)
# - Comprehensive environment variables configuration
# - Python path configuration (PYTHONPATH=/app)
# - Health endpoint testing
# - Enhanced error handling and troubleshooting guidance

set -e

# Function to trim whitespace
trim() {
    local var="$*"
    var="${var#"${var%%[![:space:]]*}"}"   # remove leading whitespace characters
    var="${var%"${var##*[![:space:]]}"}"   # remove trailing whitespace characters
    printf '%s' "$var"
}

# Discover and load .env file
ENV_FILE=""
# Check current directory first
if [ -f ".env" ]; then
    ENV_FILE=".env"
else
    # Walk up directory tree to find .env file
    dir=$(pwd)
    while [ "$dir" != "/" ]; do
        if [ -f "$dir/.env" ]; then
            ENV_FILE="$dir/.env"
            break
        fi
        dir=$(dirname "$dir")
    done
fi

if [ -n "$ENV_FILE" ]; then
    echo "📄 Loading environment variables from: $ENV_FILE"
    
    # Create a temporary file to normalize line endings (remove \r)
    TEMP_ENV_FILE=$(mktemp)
    trap 'rm -f "$TEMP_ENV_FILE"' EXIT
    
    # Convert CRLF to LF and remove carriage returns
    tr -d '\r' < "$ENV_FILE" > "$TEMP_ENV_FILE"
    
    # Process the .env file line by line
    while IFS= read -r line || [ -n "$line" ]; do
        # Skip empty lines and comments
        if [ -z "$line" ] || [[ "$line" =~ ^[[:space:]]*# ]]; then
            continue
        fi
        
        # Trim whitespace
        line=$(trim "$line")
        
        # Skip if line doesn't contain =
        if [[ ! "$line" =~ = ]]; then
            continue
        fi
        
        # Extract key and value
        key="${line%%=*}"
        value="${line#*=}"
        
        # Trim key and value
        key=$(trim "$key")
        value=$(trim "$value")
        
        # Remove inline comments (everything after # outside of quotes)
        if [[ "$value" =~ ^\".*\"[[:space:]]*# ]] || [[ "$value" =~ ^\'.*\'[[:space:]]*# ]]; then
            # Value is quoted and has inline comment - extract the quoted part
            if [[ "$value" =~ ^\"([^\"]*)\".* ]]; then
                value="${BASH_REMATCH[1]}"
            elif [[ "$value" =~ ^\'([^\']*)\'.* ]]; then
                value="${BASH_REMATCH[1]}"
            fi
        elif [[ "$value" =~ ^([^#]*[^[:space:]])[[:space:]]*# ]]; then
            # Value is unquoted with inline comment - take everything before #
            value="${BASH_REMATCH[1]}"
        fi
        
        # Remove surrounding quotes if still present
        if [[ "$value" =~ ^\".*\"$ ]] || [[ "$value" =~ ^\'.*\'$ ]]; then
            value="${value%\"}"
            value="${value%\'}"
            value="${value#\"}"
            value="${value#\'}"
        fi
        
        # Export the variable
        export "$key=$value"
        
    done < "$TEMP_ENV_FILE"
    
    # Validate that required environment variables are set
    REQUIRED_VARS=(
        "AZURE_EXISTING_AIPROJECT_ENDPOINT"
        "AZURE_AI_AGENT_DEPLOYMENT_NAME"
        "AZURE_SEARCH_ENDPOINT"
        "AZURE_SEARCH_INDEX"
        "AZURE_SEARCH_SEMANTIC_CONFIG"
        "AZURE_STORAGE_ACCOUNT_NAME"
        "AZURE_STORAGE_ACCOUNT_URL"
        "AZURE_TABLES_ACCOUNT_URL"
        "RESOURCE_GROUP"
        "LOCATION"
        "AZURE_INDEXING_FUNCTION_URL"
        "SCF_AZURE_SEARCH_INDEX"
        "MCP_ALLOWED_SERVERS"
        "AZURE_PRICING_MCP_URL"

    )
    
    MISSING_VARS=()
    for var in "${REQUIRED_VARS[@]}"; do
        if [ -z "${!var:-}" ] || [ "${!var}" = "PLACEHOLDER_SET_THIS_VALUE" ]; then
            MISSING_VARS+=("$var")
        fi
    done
    
    if [ ${#MISSING_VARS[@]} -gt 0 ]; then
        echo "❌ Missing environment variables:"
        printf "   - %s\n" "${MISSING_VARS[@]}"
        echo ""
        echo "Please set these in your .env file before deployment."
        exit 1
    fi
    
    echo "✅ All required environment variables loaded from .env file"
else
    echo "⚠️  No .env file found. Using environment variables and defaults."
fi

# Configuration variables (set these before running)
# Note: ACR names must be globally unique across all of Azure
CONTAINER_APP_NAME="${CONTAINER_APP_NAME:-insights-agent-api}"
CONTAINER_APP_ENV_NAME="${CONTAINER_APP_ENV_NAME:-aca-env}"
ACR_NAME="${ACR_NAME:-aiintakeacr}"  # Change this if the name is taken
IMAGE_NAME="${IMAGE_NAME:-insights-agent-api}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

echo "Environment variables loaded from ${ENV_FILE}" 
echo "  Subscription: ${AZURE_SUBSCRIPTION_ID}"
echo "  Resource Group: ${RESOURCE_GROUP}"
echo "  Location: ${LOCATION}"
echo "  Container App Name: ${CONTAINER_APP_NAME}"
echo "  Container App Environment: ${CONTAINER_APP_ENV_NAME}"
echo "  ACR Name: ${ACR_NAME}"
echo "  Image: ${IMAGE_NAME}:${IMAGE_TAG}"
echo

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}🚀 Starting deployment of AI Assessment, Design and Planning (ADP) API to Azure Container Apps${NC}"

# Check if required CLI tools are installed
echo -e "${YELLOW}📋 Checking prerequisites...${NC}"
if ! command -v az &> /dev/null; then
    echo -e "${RED}❌ Azure CLI is not installed. Please install it first.${NC}"
    exit 1
fi

if ! command -v docker &> /dev/null; then
    echo -e "${RED}❌ Docker is not installed. Please install it first.${NC}"
    exit 1
fi

# Login to Azure (if not already logged in)
echo -e "${YELLOW}🔐 Checking Azure authentication...${NC}"
if ! az account show &> /dev/null; then
    echo -e "${YELLOW}Please login to Azure...${NC}"
    az login
fi

# Install Container Apps extension if not already installed
echo -e "${YELLOW}🔧 Installing Azure Container Apps extension...${NC}"
az extension add --name containerapp --upgrade --yes

# Create Azure Container Registry if it doesn't exist
echo -e "${YELLOW}🏗️ Checking Azure Container Registry: ${ACR_NAME}${NC}"
if az acr show --name $ACR_NAME --resource-group $RESOURCE_GROUP &> /dev/null; then
    echo -e "${GREEN}✅ ACR already exists in resource group: ${ACR_NAME}${NC}"
else
    echo -e "${YELLOW}🏗️ Creating Azure Container Registry: ${ACR_NAME}${NC}"
    # Try to create ACR, if name is taken globally, suggest alternatives
    if ! az acr create \
        --resource-group $RESOURCE_GROUP \
        --name $ACR_NAME \
        --sku Basic \
        --admin-enabled true \
        --location $LOCATION \
        --output table; then
        
        echo -e "${RED}❌ Failed to create ACR with name: ${ACR_NAME}${NC}"
        echo -e "${YELLOW}💡 The ACR name '${ACR_NAME}' is already taken globally.${NC}"
        echo -e "${YELLOW}📝 Please update the ACR_NAME variable and run again.${NC}"
        
        exit 1
    fi
fi

# Get ACR login server
ACR_LOGIN_SERVER=$(az acr show --name $ACR_NAME --resource-group $RESOURCE_GROUP --query loginServer --output tsv)
ACR_LOGIN_SERVER=$(echo $ACR_LOGIN_SERVER | tr -d '[:space:]')  # Remove any whitespace

# Validate ACR login server
if [[ -z "$ACR_LOGIN_SERVER" ]]; then
    echo -e "${RED}❌ Failed to get ACR login server${NC}"
    exit 1
fi

if [[ ! "$ACR_LOGIN_SERVER" =~ ^[a-zA-Z0-9]+\.azurecr\.io$ ]]; then
    echo -e "${RED}❌ Invalid ACR login server format: '$ACR_LOGIN_SERVER'${NC}"
    exit 1
fi

echo -e "${GREEN}📦 ACR Login Server: ${ACR_LOGIN_SERVER}${NC}"

# Build and push Docker image
echo -e "${YELLOW}🐳 Building and pushing Docker image...${NC}"

# Change to the insights-agent root directory where Dockerfile and .dockerignore are located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
echo -e "${YELLOW}📁 Changing to build directory: $BUILD_DIR${NC}"
cd "$BUILD_DIR"
echo -e "${YELLOW}📁 Current directory: $(pwd)${NC}"

echo -e "${YELLOW}📄 Checking .dockerignore exists...${NC}"
if [ -f ".dockerignore" ]; then
    echo -e "${GREEN}✅ .dockerignore found${NC}"
else
    echo -e "${RED}❌ .dockerignore not found - this may cause issues${NC}"
fi

# Remove any problematic files/directories before build
echo -e "${YELLOW}🧹 Cleaning up before build (including Python cache)...${NC}"
rm -rf .venv venv __pycache__ .pytest_cache 2>/dev/null || true
find . -name "*.pyc" -delete 2>/dev/null || true
find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

# Build with unique tag to ensure fresh build (ACR doesn't support --no-cache)
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
echo -e "${YELLOW}🔨 Building fresh image with timestamp: $TIMESTAMP...${NC}"
az acr build \
    --registry $ACR_NAME \
    --image $IMAGE_NAME:$IMAGE_TAG \
    --image $IMAGE_NAME:v$TIMESTAMP \
    --file Dockerfile \
    . || {
        echo -e "${RED}❌ Docker build failed. Trying alternative approach...${NC}"
        # Create a clean build directory
        mkdir -p /tmp/docker-build-${CONTAINER_APP_NAME}
        cp -r agents requirements.txt Dockerfile .dockerignore /tmp/docker-build-${CONTAINER_APP_NAME}/ 2>/dev/null || true
        cd /tmp/docker-build-${CONTAINER_APP_NAME}
        
        # Clean Python cache in temp directory too
        find . -name "*.pyc" -delete 2>/dev/null || true
        find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
        
        az acr build \
            --registry $ACR_NAME \
            --image $IMAGE_NAME:$IMAGE_TAG \
            --image $IMAGE_NAME:v$TIMESTAMP \
            --file Dockerfile \
            .
        cd - > /dev/null
        rm -rf /tmp/docker-build-${CONTAINER_APP_NAME}
    }

# Create Container Apps environment if it doesn't exist
echo -e "${YELLOW}🌍 Creating Container Apps environment if it doesn't exist: ${CONTAINER_APP_ENV_NAME}${NC}"
if az containerapp env show --name $CONTAINER_APP_ENV_NAME --resource-group $RESOURCE_GROUP &> /dev/null; then
    echo -e "${GREEN}✅ Container Apps environment already exists: ${CONTAINER_APP_ENV_NAME}${NC}"
else
    # Check if Log Analytics workspace exists
    LOG_ANALYTICS_WORKSPACE_NAME="ai-foundry-std-log-analytics"
    
    if az monitor log-analytics workspace show \
        --resource-group $RESOURCE_GROUP \
        --workspace-name $LOG_ANALYTICS_WORKSPACE_NAME &> /dev/null; then
        
        echo -e "${GREEN}✅ Using existing Log Analytics workspace: ${LOG_ANALYTICS_WORKSPACE_NAME}${NC}"
        
        # Get the workspace ID and shared key with proper validation
        echo -e "${YELLOW}📋 Retrieving Log Analytics workspace credentials...${NC}"
        
        LOG_ANALYTICS_WORKSPACE_ID=$(az monitor log-analytics workspace show \
            --resource-group $RESOURCE_GROUP \
            --workspace-name $LOG_ANALYTICS_WORKSPACE_NAME \
            --query customerId \
            --output tsv | tr -d '[:space:]')
        
        LOG_ANALYTICS_SHARED_KEY=$(az monitor log-analytics workspace get-shared-keys \
            --resource-group $RESOURCE_GROUP \
            --workspace-name $LOG_ANALYTICS_WORKSPACE_NAME \
            --query primarySharedKey \
            --output tsv | tr -d '[:space:]')
        
        # Validate workspace ID format (should be a GUID)
        if [[ ! $LOG_ANALYTICS_WORKSPACE_ID =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]]; then
            echo -e "${RED}❌ Invalid Log Analytics workspace ID format: '$LOG_ANALYTICS_WORKSPACE_ID'${NC}"
            echo -e "${YELLOW}📊 Falling back to creating Container Apps environment with new workspace...${NC}"
            
            # Create Container Apps environment (will create a new Log Analytics workspace)
            az containerapp env create \
                --name $CONTAINER_APP_ENV_NAME \
                --resource-group $RESOURCE_GROUP \
                --location $LOCATION \
                --output table
        else
            echo -e "${GREEN}✅ Log Analytics workspace ID: ${LOG_ANALYTICS_WORKSPACE_ID}${NC}"
            echo -e "${GREEN}✅ Log Analytics shared key length: ${#LOG_ANALYTICS_SHARED_KEY} characters${NC}"
            
            # Create Container Apps environment with existing Log Analytics workspace
            az containerapp env create \
                --name $CONTAINER_APP_ENV_NAME \
                --resource-group $RESOURCE_GROUP \
                --location $LOCATION \
                --logs-workspace-id $LOG_ANALYTICS_WORKSPACE_ID \
                --logs-workspace-key $LOG_ANALYTICS_SHARED_KEY \
                --output table
        fi
    else
        echo -e "${YELLOW}📊 Log Analytics workspace doesn't exist. Creating Container Apps environment with new workspace...${NC}"
        
        # Create Container Apps environment (will create a new Log Analytics workspace)
        az containerapp env create \
            --name $CONTAINER_APP_ENV_NAME \
            --resource-group $RESOURCE_GROUP \
            --location $LOCATION \
            --output table
    fi
fi

# Enable system-assigned managed identity for the container app to access ACR
echo -e "${YELLOW}🔐 Container app will use system-assigned managed identity for ACR access${NC}"
echo -e "${GREEN}📦 ACR Login Server: ${ACR_LOGIN_SERVER}${NC}"

# Debug: Print all variables
echo -e "${YELLOW}🔍 Debug - Deployment variables:${NC}"
echo -e "  ACR_NAME: ${ACR_NAME}"
echo -e "  ACR_LOGIN_SERVER: ${ACR_LOGIN_SERVER}"
echo -e "  IMAGE_NAME: ${IMAGE_NAME}"
echo -e "  IMAGE_TAG: ${IMAGE_TAG}"

# Check if Container App exists
echo -e "${YELLOW}==> Checking if Container App exists${NC}"
APP_EXISTS=$(az containerapp show \
    --name "$CONTAINER_APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --query name --output tsv 2>/dev/null || echo "")

FULL_IMAGE_NAME="${ACR_LOGIN_SERVER}/${IMAGE_NAME}:${IMAGE_TAG}"
echo -e "${YELLOW}🚀 Deploying container app: ${CONTAINER_APP_NAME}${NC}"
echo -e "${YELLOW}📦 Using ACR image: ${FULL_IMAGE_NAME}${NC}"

if [[ -z "$APP_EXISTS" ]]; then
    echo -e "${YELLOW}==> Creating new Container App${NC}"
    echo -e "${YELLOW}🔧 Creating app with environment variables from .env file${NC}"
    
    az containerapp create \
    --name $CONTAINER_APP_NAME \
    --resource-group $RESOURCE_GROUP \
    --environment $CONTAINER_APP_ENV_NAME \
    --image $FULL_IMAGE_NAME \
    --registry-server $ACR_LOGIN_SERVER \
    --registry-identity system \
    --target-port 8000 \
    --ingress external \
    --min-replicas 1 \
    --max-replicas 10 \
    --cpu 1.0 \
    --memory 2.0Gi \
    --env-vars \
        AZURE_EXISTING_AIPROJECT_ENDPOINT="${AZURE_EXISTING_AIPROJECT_ENDPOINT}" \
        AZURE_AI_AGENT_DEPLOYMENT_NAME="${AZURE_AI_AGENT_DEPLOYMENT_NAME:-gpt-4.1}" \
        AZURE_SEARCH_ENDPOINT="${AZURE_SEARCH_ENDPOINT}" \
        AZURE_SEARCH_INDEX="${AZURE_SEARCH_INDEX}" \
        AZURE_SEARCH_SEMANTIC_CONFIG="${AZURE_SEARCH_SEMANTIC_CONFIG:-DefaultSemantic}" \
        AZURE_STORAGE_ACCOUNT_NAME="${AZURE_STORAGE_ACCOUNT_NAME}" \
        AZURE_STORAGE_ACCOUNT_URL="${AZURE_STORAGE_ACCOUNT_URL}" \
        AZURE_TABLES_ACCOUNT_URL="${AZURE_TABLES_ACCOUNT_URL}" \
        AZURE_SUBSCRIPTION_ID="${AZURE_SUBSCRIPTION_ID}" \
        RESOURCE_GROUP="$RESOURCE_GROUP" \
        LOCATION="$LOCATION" \
        AZURE_INDEXING_FUNCTION_URL="${AZURE_INDEXING_FUNCTION_URL}" \
        SCF_AZURE_SEARCH_INDEX="${SCF_AZURE_SEARCH_INDEX}" \
        MCP_ALLOWED_SERVERS="${MCP_ALLOWED_SERVERS}" \
        AZURE_PRICING_MCP_URL="${AZURE_PRICING_MCP_URL}" \
        APPLICATIONINSIGHTS_CONNECTION_STRING="${APPLICATIONINSIGHTS_CONNECTION_STRING}" \
        PYTHONPATH="/app" \
        APP_VERBOSE="${APP_VERBOSE:-1}" \
        LOG_LEVEL="${LOG_LEVEL:-INFO}" \
        BUILD_NUMBER="$(date +%Y%m%d-%H%M%S)" \
        GIT_COMMIT="$(git rev-parse HEAD 2>/dev/null || echo 'unknown')" \
        DEPLOYMENT_TIME="$(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)" \
    --output table
    
    echo -e "${GREEN}✅ Container app created with ACR image and managed identity configured${NC}"
else
    echo -e "${YELLOW}==> Updating existing Container App${NC}"
    
    # Get the managed identity principal ID
    IDENTITY_OBJECT_ID=$(az containerapp identity show \
        --name $CONTAINER_APP_NAME \
        --resource-group $RESOURCE_GROUP \
        --query principalId \
        --output tsv 2>/dev/null || echo "")
    
    if [[ -z "$IDENTITY_OBJECT_ID" ]]; then
        echo -e "${YELLOW}No managed identity found, enabling system-assigned identity...${NC}"
        az containerapp identity assign \
            --name $CONTAINER_APP_NAME \
            --resource-group $RESOURCE_GROUP \
            --system-assigned
        
        # Get the new identity
        IDENTITY_OBJECT_ID=$(az containerapp identity show \
            --name $CONTAINER_APP_NAME \
            --resource-group $RESOURCE_GROUP \
            --query principalId \
            --output tsv)
    fi
    
    echo -e "${YELLOW}Managed identity principal ID: $IDENTITY_OBJECT_ID${NC}"
    
    # Ensure AcrPull role is assigned
    echo -e "${YELLOW}Assigning AcrPull role to managed identity...${NC}"
    az role assignment create \
        --assignee-object-id "$IDENTITY_OBJECT_ID" \
        --role "AcrPull" \
        --scope "/subscriptions/$AZURE_SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.ContainerRegistry/registries/$ACR_NAME" \
        2>/dev/null && echo -e "${GREEN}✓ AcrPull role assigned${NC}" || echo -e "${YELLOW}⚠ AcrPull role already assigned or failed${NC}"
    
    # Configure registry authentication
    echo -e "${YELLOW}Configuring registry authentication...${NC}"
    az containerapp registry set \
        --name $CONTAINER_APP_NAME \
        --resource-group $RESOURCE_GROUP \
        --server $ACR_LOGIN_SERVER \
        --identity system
    
    # Update Container App image and environment variables
    echo -e "${YELLOW}🔧 Updating app with environment variables from .env file${NC}"
    az containerapp update \
        --name $CONTAINER_APP_NAME \
        --resource-group $RESOURCE_GROUP \
        --image "${ACR_LOGIN_SERVER}/${IMAGE_NAME}:${IMAGE_TAG}" \
        --set-env-vars \
            AZURE_EXISTING_AIPROJECT_ENDPOINT="${AZURE_EXISTING_AIPROJECT_ENDPOINT}" \
            AZURE_AI_AGENT_DEPLOYMENT_NAME="${AZURE_AI_AGENT_DEPLOYMENT_NAME:-gpt-4.1}" \
            AZURE_SEARCH_ENDPOINT="${AZURE_SEARCH_ENDPOINT}" \
            AZURE_SEARCH_INDEX="${AZURE_SEARCH_INDEX}" \
            AZURE_SEARCH_SEMANTIC_CONFIG="${AZURE_SEARCH_SEMANTIC_CONFIG:-DefaultSemantic}" \
            AZURE_STORAGE_ACCOUNT_NAME="${AZURE_STORAGE_ACCOUNT_NAME}" \
            AZURE_STORAGE_ACCOUNT_URL="${AZURE_STORAGE_ACCOUNT_URL}" \
            AZURE_TABLES_ACCOUNT_URL="${AZURE_TABLES_ACCOUNT_URL}" \
            AZURE_SUBSCRIPTION_ID="${AZURE_SUBSCRIPTION_ID}" \
            RESOURCE_GROUP="$RESOURCE_GROUP" \
            LOCATION="$LOCATION" \
            AZURE_INDEXING_FUNCTION_URL="${AZURE_INDEXING_FUNCTION_URL}" \
            SCF_AZURE_SEARCH_INDEX="${SCF_AZURE_SEARCH_INDEX}" \
            MCP_ALLOWED_SERVERS="${MCP_ALLOWED_SERVERS}" \
            AZURE_PRICING_MCP_URL="${AZURE_PRICING_MCP_URL}" \
            APPLICATIONINSIGHTS_CONNECTION_STRING="${APPLICATIONINSIGHTS_CONNECTION_STRING}" \
            PYTHONPATH="/app" \
            APP_VERBOSE="${APP_VERBOSE:-1}" \
            LOG_LEVEL="${LOG_LEVEL:-INFO}" \
            DEPLOYMENT_TIME="$(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)"
fi

# Assign RBAC roles to the managed identity
echo -e "${YELLOW}🔐 Assigning RBAC roles to managed identity...${NC}"

# Get the managed identity principal ID
IDENTITY_OBJECT_ID=$(az containerapp identity show \
    --name "$CONTAINER_APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --query principalId \
    --output tsv | tr -d '[:space:]')

echo -e "${YELLOW}Managed identity principalId: $IDENTITY_OBJECT_ID${NC}"

if [ -n "$IDENTITY_OBJECT_ID" ]; then
    # Extract service names from environment variables
    AZURE_SEARCH_SERVICE_NAME=""
    AZURE_STORAGE_ACCOUNT_NAME=""
    
    # Extract search service name from AZURE_SEARCH_ENDPOINT
    if [ -n "${AZURE_SEARCH_ENDPOINT:-}" ]; then
        AZURE_SEARCH_SERVICE_NAME=$(echo "$AZURE_SEARCH_ENDPOINT" | sed -n 's|https://\([^.]*\)\.search\.windows\.net.*|\1|p')
    fi
    
    # Extract storage account name from AZURE_STORAGE_ACCOUNT_URL
    if [ -n "${AZURE_STORAGE_ACCOUNT_URL:-}" ]; then
        AZURE_STORAGE_ACCOUNT_NAME=$(echo "$AZURE_STORAGE_ACCOUNT_URL" | sed -n 's|https://\([^.]*\)\.blob\.core\.windows\.net.*|\1|p')
    fi

    # Log extracted RBAC variables
    echo -e "${YELLOW}🔍 Debug - RBAC Variables:${NC}"
    echo -e "  IDENTITY_OBJECT_ID: ${IDENTITY_OBJECT_ID:-<not set>}"
    echo -e "  AZURE_SEARCH_SERVICE_NAME: ${AZURE_SEARCH_SERVICE_NAME:-<not set>}"
    echo -e "  AZURE_STORAGE_ACCOUNT_NAME: ${AZURE_STORAGE_ACCOUNT_NAME:-<not set>}"
    echo -e "  AZURE_AI_PROJECT_NAME: ${AZURE_AI_PROJECT_NAME:-<not set>}"
    echo -e "  RESOURCE_GROUP: ${RESOURCE_GROUP:-<not set>}"
    echo -e "  AZURE_SUBSCRIPTION_ID: ${AZURE_SUBSCRIPTION_ID:-<not set>}"

    
    echo -e "${YELLOW}Assigning RBAC roles (will skip if already assigned)...${NC}"
    
    # Search Index Data Reader on the AI Search service
    if [ -n "$AZURE_SEARCH_SERVICE_NAME" ]; then
        az role assignment create \
            --assignee-object-id "$IDENTITY_OBJECT_ID" \
            --role "Search Index Data Reader" \
            --scope "/subscriptions/$AZURE_SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.Search/searchServices/$AZURE_SEARCH_SERVICE_NAME" \
            2>/dev/null && echo -e "${GREEN}✓ Search Index Data Reader role assigned${NC}" || echo -e "${YELLOW}⚠ Search Index role already assigned or failed${NC}"
        
        # Search Service Contributor on the AI Search service
        az role assignment create \
            --assignee-object-id "$IDENTITY_OBJECT_ID" \
            --role "Search Service Contributor" \
            --scope "/subscriptions/$AZURE_SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.Search/searchServices/$AZURE_SEARCH_SERVICE_NAME" \
            2>/dev/null && echo -e "${GREEN}✓ Search Service Contributor role assigned${NC}" || echo -e "${YELLOW}⚠ Search Service role already assigned or failed${NC}"
    else
        echo -e "${RED}⚠ Could not extract search service name from AZURE_SEARCH_ENDPOINT${NC}"
    fi
    
    # Storage roles on the Storage Account
    if [ -n "$AZURE_STORAGE_ACCOUNT_NAME" ]; then
        # Storage Blob Data Contributor
        az role assignment create \
            --assignee-object-id "$IDENTITY_OBJECT_ID" \
            --role "Storage Blob Data Contributor" \
            --scope "/subscriptions/$AZURE_SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.Storage/storageAccounts/$AZURE_STORAGE_ACCOUNT_NAME" \
            2>/dev/null && echo -e "${GREEN}✓ Storage Blob Data Contributor role assigned${NC}" || echo -e "${YELLOW}⚠ Storage Blob role already assigned or failed${NC}"
        
        # Storage Table Data Contributor
        az role assignment create \
            --assignee-object-id "$IDENTITY_OBJECT_ID" \
            --role "Storage Table Data Contributor" \
            --scope "/subscriptions/$AZURE_SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.Storage/storageAccounts/$AZURE_STORAGE_ACCOUNT_NAME" \
            2>/dev/null && echo -e "${GREEN}✓ Storage Table Data Contributor role assigned${NC}" || echo -e "${YELLOW}⚠ Storage Table role already assigned or failed${NC}"
        
        # RBAC Administrator on the Storage Account
        az role assignment create \
            --assignee-object-id "$IDENTITY_OBJECT_ID" \
            --role "Role Based Access Control Administrator" \
            --scope "/subscriptions/$AZURE_SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.Storage/storageAccounts/$AZURE_STORAGE_ACCOUNT_NAME" \
            2>/dev/null && echo -e "${GREEN}✓ Role Based Access Control Administrator role assigned${NC}" || echo -e "${YELLOW}⚠ Role Based Access Control Administrator role already assigned or failed${NC}"
    else
        echo -e "${RED}⚠ Could not extract storage account name from AZURE_STORAGE_ACCOUNT_URL${NC}"
    fi
    
    # Cognitive Services roles on the AI Foundry service
    if [ -n "${AZURE_AI_PROJECT_NAME:-}" ]; then
        # Cognitive Services OpenAI User
        az role assignment create \
            --assignee-object-id "$IDENTITY_OBJECT_ID" \
            --role "Cognitive Services OpenAI User" \
            --scope "/subscriptions/$AZURE_SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.CognitiveServices/accounts/$AZURE_AI_PROJECT_NAME" \
            2>/dev/null && echo -e "${GREEN}✓ Cognitive Services OpenAI User role assigned${NC}" || echo -e "${YELLOW}⚠ Cognitive Services OpenAI User role already assigned or failed${NC}"
        
        # Cognitive Services User
        az role assignment create \
            --assignee-object-id "$IDENTITY_OBJECT_ID" \
            --role "Cognitive Services User" \
            --scope "/subscriptions/$AZURE_SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.CognitiveServices/accounts/$AZURE_AI_PROJECT_NAME" \
            2>/dev/null && echo -e "${GREEN}✓ Cognitive Services User role assigned${NC}" || echo -e "${YELLOW}⚠ Cognitive Services User role already assigned or failed${NC}"
        
        # Azure AI User
        az role assignment create \
            --assignee-object-id "$IDENTITY_OBJECT_ID" \
            --role "Azure AI User" \
            --scope "/subscriptions/$AZURE_SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.CognitiveServices/accounts/$AZURE_AI_PROJECT_NAME" \
            2>/dev/null && echo -e "${GREEN}✓ Azure AI User role assigned${NC}" || echo -e "${YELLOW}⚠ Azure AI User role already assigned or failed${NC}"
    else
        echo -e "${RED}⚠ AZURE_AI_PROJECT_NAME not found - skipping Cognitive Services role assignments${NC}"
    fi
    
    # Reader on the Resource Group
    az role assignment create \
        --assignee-object-id "$IDENTITY_OBJECT_ID" \
        --role "Reader" \
        --scope "/subscriptions/$AZURE_SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP" \
        2>/dev/null && echo -e "${GREEN}✓ Reader role assigned on resource group${NC}" || echo -e "${YELLOW}⚠ Reader role already assigned or failed${NC}"
    
    echo -e "${GREEN}✅ RBAC role assignment completed${NC}"
else
    echo -e "${RED}❌ Failed to get managed identity principal ID${NC}"
fi

# Get the application URL
APP_URL=$(az containerapp show \
    --name $CONTAINER_APP_NAME \
    --resource-group $RESOURCE_GROUP \
    --query properties.configuration.ingress.fqdn \
    --output tsv | tr -d '\r')

echo -e "${GREEN}✅ Deployment completed successfully!${NC}"
echo -e "${GREEN}🌐 Application URL: https://${APP_URL}${NC}"
echo -e "${RED}⚠️  Health Check will FAIL until you configure environment variables: https://${APP_URL}/health${NC}"
echo -e "${GREEN}📖 API Documentation: https://${APP_URL}/docs${NC}"

echo -e "${GREEN}✅ Environment Variables Configured${NC}"
echo -e "The app was deployed with environment variables from your .env file."

echo -e "${YELLOW}💡 To update environment variables later:${NC}"
echo -e "   1. Update your .env file with new values"
echo -e "   2. Redeploy using this script, or manually update:"
echo -e "   az containerapp update --name ${CONTAINER_APP_NAME} --resource-group ${RESOURCE_GROUP} \\"
echo -e "     --set-env-vars \\"
echo -e "       AZURE_EXISTING_AIPROJECT_ENDPOINT=NEW_VALUE \\"
echo -e "       AZURE_SEARCH_ENDPOINT=NEW_VALUE \\"
echo -e "       (etc...)"

echo -e "${YELLOW}📝 Other useful commands:${NC}"
echo -e "• Monitor logs: az containerapp logs show --name ${CONTAINER_APP_NAME} --resource-group ${RESOURCE_GROUP} --follow"
echo -e "• Scale the app: az containerapp update --name ${CONTAINER_APP_NAME} --resource-group ${RESOURCE_GROUP} --min-replicas 2"
echo -e "• Check status: az containerapp show --name ${CONTAINER_APP_NAME} --resource-group ${RESOURCE_GROUP}"

echo -e "${YELLOW}🧪 Testing health endpoint:${NC}"
if command -v curl &> /dev/null; then
    echo -e "${YELLOW}Testing https://${APP_URL}/health...${NC}"
    if curl -f "https://${APP_URL}/health" -w "\nHTTP Status: %{http_code}\n" 2>/dev/null; then
        echo -e "${GREEN}✅ Health check passed!${NC}"
    else
        echo -e "${RED}❌ Health check failed${NC}"
        echo -e "${YELLOW}💡 Check logs for any startup or configuration errors:${NC}"
        echo -e "   az containerapp logs show --name ${CONTAINER_APP_NAME} --resource-group ${RESOURCE_GROUP} --tail 20"
    fi
else
    echo -e "${YELLOW}💡 curl not available - test manually: https://${APP_URL}/health${NC}"
fi

echo -e "${GREEN}🎉 Your API is deployed and configured! Environment variables loaded from .env file.${NC}"