#!/bin/bash

# Azure Container Apps Deployment Script for Azure Pricing MCP Server
# This script deploys the Azure Pricing MCP Server to Azure Container Apps
#
# Features:
# - Environment variable loading from .env file
# - Docker image build and push to ACR
# - Container Apps deployment with proper configuration
# - Health check validation
# - Enhanced error handling and troubleshooting guidance

set -e

# Function to trim whitespace
trim() {
    local var="$*"
    var="${var#"${var%%[![:space:]]*}"}"   # remove leading whitespace characters
    var="${var%"${var##*[![:space:]]}"}"   # remove trailing whitespace characters
    printf '%s' "$var"
}

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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
        "MCP_HOST"
        "MCP_PORT"
        "MCP_DEBUG"
        "MCP_RELOAD"
        "CORS_ORIGINS"
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

# Configuration variables (set these before running or use .env file)
CONTAINER_APP_NAME="${CONTAINER_APP_NAME:-azure-pricing-mcp}"
CONTAINER_APP_ENV_NAME="${CONTAINER_APP_ENV_NAME:-aca-env}"
ACR_NAME="${ACR_NAME:-aiintakeacr}"
IMAGE_NAME="${IMAGE_NAME:-azure-pricing-mcp}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

echo "Environment variables loaded from ${ENV_FILE:-environment/defaults}"
echo "  Subscription: ${AZURE_SUBSCRIPTION_ID}"
echo "  Resource Group: ${RESOURCE_GROUP}"
echo "  Location: ${LOCATION}"
echo "  Container App Name: ${CONTAINER_APP_NAME}"
echo "  Container App Environment: ${CONTAINER_APP_ENV_NAME}"
echo "  ACR Name: ${ACR_NAME}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}🚀 Starting deployment of Azure Pricing MCP Server to Azure Container Apps${NC}"

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

# Check if resource group exists
echo -e "${YELLOW}📁 Checking resource group exists: ${RESOURCE_GROUP}${NC}"
if ! az group show --name $RESOURCE_GROUP &> /dev/null; then
    echo -e "${RED}❌ Resource group '${RESOURCE_GROUP}' not found${NC}"
    echo -e "${YELLOW}💡 Please create the resource group first using:${NC}"
    echo -e "   az group create --name ${RESOURCE_GROUP} --location ${LOCATION}"
    exit 1
fi

# Check if Azure Container Registry exists
echo -e "${YELLOW}🏗️ Checking Azure Container Registry exists: ${ACR_NAME}${NC}"
if ! az acr show --name $ACR_NAME --resource-group $RESOURCE_GROUP &> /dev/null; then
    echo -e "${RED}❌ Azure Container Registry '${ACR_NAME}' not found in resource group '${RESOURCE_GROUP}'${NC}"
    echo -e "${YELLOW}💡 Please create the ACR first using:${NC}"
    echo -e "   az acr create --resource-group ${RESOURCE_GROUP} --name ${ACR_NAME} --sku Basic --admin-enabled true --location ${LOCATION}"
    exit 1
fi

# Get ACR login server
ACR_LOGIN_SERVER=$(az acr show --name $ACR_NAME --resource-group $RESOURCE_GROUP --query loginServer --output tsv)
ACR_LOGIN_SERVER=$(echo $ACR_LOGIN_SERVER | tr -d '[:space:]')  # Remove any whitespace

echo -e "${GREEN}✅ ACR Login Server: ${ACR_LOGIN_SERVER}${NC}"

# Build and push Docker image
echo -e "${YELLOW}🐳 Building and pushing Docker image...${NC}"

# Change to the azure-pricing-mcp-server directory (where the Dockerfile is located)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="$(cd "${SCRIPT_DIR}/../../azure-pricing-mcp-server" && pwd)"
echo -e "${YELLOW}📁 Build directory: $BUILD_DIR${NC}"
cd "$BUILD_DIR"
echo -e "${YELLOW}📁 Current directory: $(pwd)${NC}"

echo -e "${YELLOW}📄 Checking required files...${NC}"
if [ -f "Dockerfile" ]; then
    echo -e "${GREEN}✅ Dockerfile found${NC}"
else
    echo -e "${RED}❌ Dockerfile not found${NC}"
    exit 1
fi

if [ -f "requirements.txt" ]; then
    echo -e "${GREEN}✅ requirements.txt found${NC}"
else
    echo -e "${RED}❌ requirements.txt not found${NC}"
    exit 1
fi

# Remove any problematic files/directories before build
echo -e "${YELLOW}🧹 Cleaning up before build...${NC}"
rm -rf .venv venv __pycache__ .pytest_cache 2>/dev/null || true
find . -name "*.pyc" -delete 2>/dev/null || true
find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

# Build with unique tag to ensure fresh build
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
echo -e "${YELLOW}🔨 Building fresh image with timestamp: $TIMESTAMP...${NC}"
az acr build \
    --registry $ACR_NAME \
    --image $IMAGE_NAME:$IMAGE_TAG \
    --image $IMAGE_NAME:v$TIMESTAMP \
    --file Dockerfile \
    . || {
        echo -e "${RED}❌ Docker build failed${NC}"
        exit 1
    }

# Check if Container Apps environment exists
echo -e "${YELLOW}🌍 Checking Container Apps environment exists: ${CONTAINER_APP_ENV_NAME}${NC}"
if ! az containerapp env show --name $CONTAINER_APP_ENV_NAME --resource-group $RESOURCE_GROUP &> /dev/null; then
    echo -e "${RED}❌ Container Apps environment '${CONTAINER_APP_ENV_NAME}' not found in resource group '${RESOURCE_GROUP}'${NC}"
    echo -e "${YELLOW}💡 Please create the Container Apps environment first or update CONTAINER_APP_ENV_NAME${NC}"
    exit 1
fi

echo -e "${GREEN}✅ Container Apps environment found: ${CONTAINER_APP_ENV_NAME}${NC}"

# Create the container app with ACR image directly
FULL_IMAGE_NAME="${ACR_LOGIN_SERVER}/${IMAGE_NAME}:${IMAGE_TAG}"
echo -e "${YELLOW}🚀 Deploying container app: ${CONTAINER_APP_NAME}${NC}"
echo -e "${YELLOW}📦 Using ACR image: ${FULL_IMAGE_NAME}${NC}"
echo -e "${YELLOW}🔧 Creating app with environment variables from .env file${NC}"

# Check if container app already exists
if az containerapp show --name $CONTAINER_APP_NAME --resource-group $RESOURCE_GROUP &> /dev/null; then
    echo -e "${YELLOW}🔄 Updating existing container app${NC}"
    az containerapp update \
        --name $CONTAINER_APP_NAME \
        --resource-group $RESOURCE_GROUP \
        --image $FULL_IMAGE_NAME \
        --set-env-vars \
            MCP_HOST="${MCP_HOST:-0.0.0.0}" \
            MCP_PORT="${MCP_PORT:-8080}" \
            MCP_DEBUG="${MCP_DEBUG:-false}" \
            MCP_RELOAD="${MCP_RELOAD:-false}" \
            CORS_ORIGINS="${CORS_ORIGINS:-*}" \
            LOG_LEVEL="${LOG_LEVEL:-INFO}" \
        --output table
else
    echo -e "${YELLOW}🆕 Creating new container app${NC}"
    az containerapp create \
        --name $CONTAINER_APP_NAME \
        --resource-group $RESOURCE_GROUP \
        --environment $CONTAINER_APP_ENV_NAME \
        --image $FULL_IMAGE_NAME \
        --registry-server $ACR_LOGIN_SERVER \
        --registry-identity system \
        --target-port 8080 \
        --ingress external \
        --min-replicas 1 \
        --max-replicas 5 \
        --cpu 0.5 \
        --memory 1.0Gi \
        --env-vars \
            MCP_HOST="${MCP_HOST:-0.0.0.0}" \
            MCP_PORT="${MCP_PORT:-8080}" \
            MCP_DEBUG="${MCP_DEBUG:-false}" \
            MCP_RELOAD="${MCP_RELOAD:-false}" \
            CORS_ORIGINS="${CORS_ORIGINS:-*}" \
            LOG_LEVEL="${LOG_LEVEL:-INFO}" \
        --output table
fi

echo -e "${GREEN}✅ Container app deployed successfully${NC}"

# Get the application URL
APP_URL=$(az containerapp show \
    --name $CONTAINER_APP_NAME \
    --resource-group $RESOURCE_GROUP \
    --query properties.configuration.ingress.fqdn \
    --output tsv | tr -d '\r')

echo -e "${GREEN}✅ Deployment completed successfully!${NC}"
echo -e "${GREEN}🌐 Application URL: https://${APP_URL}${NC}"
echo -e "${GREEN}📡 SSE Endpoint: https://${APP_URL}/sse${NC}"

echo -e "${YELLOW}🧪 Testing MCP server connectivity:${NC}"
if command -v curl &> /dev/null; then
    # Give the container a moment to fully start
    echo -e "${YELLOW}Waiting 5 seconds for container to fully initialize...${NC}"
    sleep 5
    
    # Test SSE endpoint with POST (MCP protocol uses POST for SSE connections)
    echo -e "${YELLOW}Testing MCP SSE endpoint (POST https://${APP_URL}/sse)...${NC}"
    HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "https://${APP_URL}/sse" \
        -H "Content-Type: application/json" \
        -H "Accept: text/event-stream" \
        --max-time 10 2>/dev/null || echo "000")
    
    if [ "$HTTP_STATUS" = "200" ] || [ "$HTTP_STATUS" = "405" ]; then
        echo -e "${GREEN}✅ MCP SSE endpoint is responding (HTTP $HTTP_STATUS)${NC}"
    elif [ "$HTTP_STATUS" = "000" ]; then
        echo -e "${YELLOW}⚠ Connection timeout - container may still be starting${NC}"
        echo -e "${YELLOW}💡 Wait a minute and test manually:${NC}"
        echo -e "   curl -X POST https://${APP_URL}/sse -H 'Content-Type: application/json'"
    else
        echo -e "${YELLOW}⚠ Unexpected response (HTTP $HTTP_STATUS) - check server logs${NC}"
        echo -e "${YELLOW}💡 Check logs for any startup or configuration errors:${NC}"
        echo -e "   az containerapp logs show --name ${CONTAINER_APP_NAME} --resource-group ${RESOURCE_GROUP} --tail 20"
    fi
    
    # Test /messages endpoint (used by MCP clients)
    echo -e "${YELLOW}Testing MCP messages endpoint (POST https://${APP_URL}/messages)...${NC}"
    HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "https://${APP_URL}/messages" \
        -H "Content-Type: application/json" \
        -d '{"jsonrpc":"2.0","id":1,"method":"ping"}' \
        --max-time 10 2>/dev/null || echo "000")
    
    if [ "$HTTP_STATUS" = "200" ] || [ "$HTTP_STATUS" = "400" ]; then
        echo -e "${GREEN}✅ MCP messages endpoint is responding (HTTP $HTTP_STATUS)${NC}"
    else
        echo -e "${YELLOW}⚠ Messages endpoint returned HTTP $HTTP_STATUS (may be expected for this MCP implementation)${NC}"
    fi
else
    echo -e "${YELLOW}💡 curl not available - test manually with an MCP client${NC}"
fi

echo -e ""
echo -e "${GREEN}✅ MCP Server Deployed Successfully!${NC}"
echo -e "The MCP server was deployed with environment variables from your .env file."
echo -e ""
echo -e "${YELLOW}📡 To connect from an MCP client, use:${NC}"
echo -e "   SSE URL: https://${APP_URL}/sse"
echo -e ""

echo -e "${YELLOW}💡 To update environment variables later:${NC}"
echo -e "   1. Update your .env file with new values"
echo -e "   2. Redeploy using this script, or manually update:"
echo -e "   az containerapp update --name ${CONTAINER_APP_NAME} --resource-group ${RESOURCE_GROUP} \\"
echo -e "     --set-env-vars \\"
echo -e "       MCP_HOST=NEW_VALUE \\"
echo -e "       MCP_PORT=NEW_VALUE \\"
echo -e "       (etc...)"

echo -e "${YELLOW}📝 Other useful commands:${NC}"
echo -e "• Monitor logs: az containerapp logs show --name ${CONTAINER_APP_NAME} --resource-group ${RESOURCE_GROUP} --follow"
echo -e "• Scale the app: az containerapp update --name ${CONTAINER_APP_NAME} --resource-group ${RESOURCE_GROUP} --min-replicas 2"
echo -e "• Check status: az containerapp show --name ${CONTAINER_APP_NAME} --resource-group ${RESOURCE_GROUP}"

echo -e "${GREEN}🎉 Azure Pricing MCP Server is deployed and configured! Environment variables loaded from .env file.${NC}"