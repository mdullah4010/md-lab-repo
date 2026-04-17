#!/bin/bash

# Azure Container Apps Deployment Script for Indexer Service
# This script deploys the Indexer FastAPI application to Azure Container Apps
#
# Features:
# - Environment variable loading from .env file
# - Docker image build and push to ACR
# - Container Apps deployment with proper configuration
# - RBAC role assignments for managed identity
# - Enhanced error handling

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
else
    echo "ERROR: No .env file found in current directory or parent directories"
    exit 1
fi

# Required variables for Container App deployment
required_vars=(
    "AZURE_EXISTING_AIPROJECT_ENDPOINT"
    "AZURE_SEARCH_ENDPOINT"
    "AZURE_STORAGE_ACCOUNT_URL"
    "AZURE_STORAGE_ACCOUNT_NAME"
    "AZURE_OPENAI_API_VERSION"
    "AZURE_OPENAI_EMBEDDING_DEPLOYMENT"
    "AZURE_OPENAI_EMBED_DIM"
    "AZURE_OPENAI_ENDPOINT"
    "AZURE_OPENAI_ENDPOINT2"
    "AZURE_SEARCH_INDEX"
    "AZURE_SEARCH_SEMANTIC_CONFIG"
)

missing_vars=()
for var in "${required_vars[@]}"; do
  if [[ -z "${!var:-}" ]]; then
    missing_vars+=("${var}")
  fi
done

if (( ${#missing_vars[@]} > 0 )); then
  printf 'Missing required environment variables in %s: %s\n' "${ENV_FILE}" "${missing_vars[*]}" >&2
  exit 1
fi

# Container App specific variables (with defaults)
INDEXER_CONTAINER_APP_NAME="${INDEXER_CONTAINER_APP_NAME:-indexer}"
CONTAINER_APP_ENV_NAME="${CONTAINER_APP_ENV_NAME:-aca-env}"
ACR_NAME="${ACR_NAME:-aiintakeacr}"
IMAGE_NAME="${IMAGE_NAME:-indexer-agent-api}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

echo "Environment variables loaded from ${ENV_FILE}" 
echo "  Subscription: ${AZURE_SUBSCRIPTION_ID}"
echo "  Resource Group: ${RESOURCE_GROUP}"
echo "  Location: ${LOCATION}"
echo "  Container App Name: ${INDEXER_CONTAINER_APP_NAME}"
echo "  Container App Environment: ${CONTAINER_APP_ENV_NAME}"
echo "  ACR Name: ${ACR_NAME}"
echo "  Image: ${IMAGE_NAME}:${IMAGE_TAG}"
echo

# Check if Container App Environment exists, create if needed
echo "==> Checking Container App Environment"
if ! az containerapp env show --name "$CONTAINER_APP_ENV_NAME" --resource-group "$RESOURCE_GROUP" > /dev/null 2>&1; then
    echo "Creating Container App Environment..."
    az containerapp env create \
        --name "$CONTAINER_APP_ENV_NAME" \
        --resource-group "$RESOURCE_GROUP" \
        --location "$LOCATION"
else
    echo "✓ Container App Environment exists: $CONTAINER_APP_ENV_NAME"
fi

# Get ACR login server
echo "==> Getting ACR login server"
ACR_LOGIN_SERVER=$(az acr show --name $ACR_NAME --resource-group $RESOURCE_GROUP --query loginServer --output tsv)
ACR_LOGIN_SERVER=$(echo $ACR_LOGIN_SERVER | tr -d '[:space:]')  # Remove any whitespace
echo "ACR Login Server: $ACR_LOGIN_SERVER"

# Build Dockerfile for the indexer service
INDEXER_DIR="$(cd "${SCRIPT_DIR}/../../indexer" && pwd)"
DOCKERFILE_PATH="${INDEXER_DIR}/Dockerfile"

echo "==> Using existing FastAPI app wrapper and Dockerfile for indexer service"
echo "    Indexer directory: $INDEXER_DIR"
echo "    ✓ app.py and Dockerfile already exist in source code"
echo "    Note: Dockerfile and app.py are maintained in the source repository"

# Change to the indexer directory for build (same approach as deploy_insights_api.sh)
echo "==> Changing to indexer directory for build"
cd "$INDEXER_DIR"
echo "    Build directory: $(pwd)"

# Clean up before build
echo "==> Cleaning up before build"
rm -rf .venv venv __pycache__ .pytest_cache 2>/dev/null || true
find . -name "*.pyc" -delete 2>/dev/null || true
find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

# Build Docker image in ACR using relative paths (WSL-compatible approach)
echo "==> Building Docker image in ACR"
az acr build \
    --registry "$ACR_NAME" \
    --image "${IMAGE_NAME}:${IMAGE_TAG}" \
    --file Dockerfile \
    .

# Check if Container App exists
echo "==> Checking if Container App exists"
APP_EXISTS=$(az containerapp show \
    --name "$INDEXER_CONTAINER_APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --query name --output tsv 2>/dev/null || echo "")

if [[ -z "$APP_EXISTS" ]]; then
    echo "==> Creating new Container App"
    
    # Debug: Print all variables
    echo "🔍 Debug - Deployment variables:"
    echo "  ACR_NAME: ${ACR_NAME}"
    echo "  ACR_LOGIN_SERVER: ${ACR_LOGIN_SERVER}"
    echo "  IMAGE_NAME: ${IMAGE_NAME}"
    echo "  IMAGE_TAG: ${IMAGE_TAG}"
    
    # Create the container app with ACR image directly (matching deploy_insights_api.sh pattern)
    FULL_IMAGE_NAME="${ACR_LOGIN_SERVER}/${IMAGE_NAME}:${IMAGE_TAG}"
    echo "🚀 Deploying container app: $INDEXER_CONTAINER_APP_NAME"
    echo "📦 Using ACR image: $FULL_IMAGE_NAME"
    
    az containerapp create \
        --name $INDEXER_CONTAINER_APP_NAME \
        --resource-group $RESOURCE_GROUP \
        --environment $CONTAINER_APP_ENV_NAME \
        --image $FULL_IMAGE_NAME \
        --registry-server $ACR_LOGIN_SERVER \
        --registry-identity system \
        --target-port 8080 \
        --ingress external \
        --min-replicas 1 \
        --max-replicas 3 \
        --cpu 1.0 \
        --memory 2.0Gi \
        --env-vars \
            AZURE_EXISTING_AIPROJECT_ENDPOINT="${AZURE_EXISTING_AIPROJECT_ENDPOINT}" \
            AZURE_SEARCH_ENDPOINT="${AZURE_SEARCH_ENDPOINT}" \
            AZURE_STORAGE_ACCOUNT_URL="${AZURE_STORAGE_ACCOUNT_URL}" \
            AZURE_STORAGE_ACCOUNT_NAME="${AZURE_STORAGE_ACCOUNT_NAME}" \
            AZURE_OPENAI_API_VERSION="${AZURE_OPENAI_API_VERSION:-2023-05-15}" \
            AZURE_OPENAI_EMBEDDING_DEPLOYMENT="${AZURE_OPENAI_EMBEDDING_DEPLOYMENT:-text-embedding-3-large}" \
            AZURE_OPENAI_EMBED_DIM="${AZURE_OPENAI_EMBED_DIM:-3072}" \
            AZURE_OPENAI_ENDPOINT="${AZURE_OPENAI_ENDPOINT}" \
            AZURE_OPENAI_ENDPOINT2="${AZURE_OPENAI_ENDPOINT2}" \
            AZURE_SEARCH_INDEX="${AZURE_SEARCH_INDEX}" \
            AZURE_SEARCH_SEMANTIC_CONFIG="${AZURE_SEARCH_SEMANTIC_CONFIG:-DefaultSemantic}" \
            USE_MANAGED_IDENTITY="${USE_MANAGED_IDENTITY:-true}" \
            USE_MANAGED_IDENTITY_FOR_AOAI="${USE_MANAGED_IDENTITY_FOR_AOAI:-true}" \
            APPLICATIONINSIGHTS_CONNECTION_STRING="${APPLICATIONINSIGHTS_CONNECTION_STRING}" \
            PYTHONPATH="/app" \
            APP_VERBOSE="${APP_VERBOSE:-1}" \
            LOG_LEVEL="${LOG_LEVEL:-INFO}"  \
            DEPLOYMENT_TIME="$(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)" \
        --output table
    
    echo "✅ Container app created with ACR image and managed identity configured"
else
    echo "==> Updating existing Container App"
    
    # Get the managed identity principal ID
    IDENTITY_OBJECT_ID=$(az containerapp identity show \
        --name $INDEXER_CONTAINER_APP_NAME \
        --resource-group $RESOURCE_GROUP \
        --query principalId \
        --output tsv 2>/dev/null || echo "")
    
    if [[ -z "$IDENTITY_OBJECT_ID" ]]; then
        echo "No managed identity found, enabling system-assigned identity..."
        az containerapp identity assign \
            --name $INDEXER_CONTAINER_APP_NAME \
            --resource-group $RESOURCE_GROUP \
            --system-assigned
        
        # Get the new identity
        IDENTITY_OBJECT_ID=$(az containerapp identity show \
            --name $INDEXER_CONTAINER_APP_NAME \
            --resource-group $RESOURCE_GROUP \
            --query principalId \
            --output tsv)
    fi
    
    echo "Managed identity principal ID: $IDENTITY_OBJECT_ID"
    
    # Ensure AcrPull role is assigned
    echo "Assigning AcrPull role to managed identity..."
    az role assignment create \
        --assignee-object-id "$IDENTITY_OBJECT_ID" \
        --role "AcrPull" \
        --scope "/subscriptions/$AZURE_SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.ContainerRegistry/registries/$ACR_NAME" \
        2>/dev/null && echo "✓ AcrPull role assigned" || echo "⚠ AcrPull role already assigned or failed"
    
    # Configure registry authentication
    echo "Configuring registry authentication..."
    az containerapp registry set \
        --name $INDEXER_CONTAINER_APP_NAME \
        --resource-group $RESOURCE_GROUP \
        --server $ACR_LOGIN_SERVER \
        --identity system
    
    # Update Container App image and environment variables
    az containerapp update \
        --name $INDEXER_CONTAINER_APP_NAME \
        --resource-group $RESOURCE_GROUP \
        --image "${ACR_LOGIN_SERVER}/${IMAGE_NAME}:${IMAGE_TAG}" \
        --set-env-vars \
            AZURE_EXISTING_AIPROJECT_ENDPOINT="${AZURE_EXISTING_AIPROJECT_ENDPOINT}" \
            AZURE_SEARCH_ENDPOINT="${AZURE_SEARCH_ENDPOINT}" \
            AZURE_STORAGE_ACCOUNT_URL="${AZURE_STORAGE_ACCOUNT_URL}" \
            AZURE_STORAGE_ACCOUNT_NAME="${AZURE_STORAGE_ACCOUNT_NAME}" \
            AZURE_OPENAI_API_VERSION="${AZURE_OPENAI_API_VERSION:-2023-05-15}" \
            AZURE_OPENAI_EMBEDDING_DEPLOYMENT="${AZURE_OPENAI_EMBEDDING_DEPLOYMENT:-text-embedding-3-large}" \
            AZURE_OPENAI_EMBED_DIM="${AZURE_OPENAI_EMBED_DIM:-3072}" \
            AZURE_OPENAI_ENDPOINT="${AZURE_OPENAI_ENDPOINT}" \
            AZURE_OPENAI_ENDPOINT2="${AZURE_OPENAI_ENDPOINT2}" \
            AZURE_SEARCH_INDEX="${AZURE_SEARCH_INDEX}" \
            AZURE_SEARCH_SEMANTIC_CONFIG="${AZURE_SEARCH_SEMANTIC_CONFIG:-DefaultSemantic}" \
            USE_MANAGED_IDENTITY="${USE_MANAGED_IDENTITY:-true}" \
            USE_MANAGED_IDENTITY_FOR_AOAI="${USE_MANAGED_IDENTITY_FOR_AOAI:-true}" \
            APPLICATIONINSIGHTS_CONNECTION_STRING="${APPLICATIONINSIGHTS_CONNECTION_STRING}" \
            PYTHONPATH="/app" \
            APP_VERBOSE="${APP_VERBOSE:-1}" \
            LOG_LEVEL="${LOG_LEVEL:-INFO}" \
            DEPLOYMENT_TIME="$(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)"
fi

# Get managed identity principal ID
echo "==> Getting managed identity principal ID"
IDENTITY_OBJECT_ID=$(az containerapp identity show \
    --name "$INDEXER_CONTAINER_APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --query principalId \
    --output tsv)
echo "Managed identity principal ID: $IDENTITY_OBJECT_ID"

# Assign RBAC roles
echo "==> Assigning RBAC roles (will skip if already assigned)"

# Azure AI User
az role assignment create \
  --assignee-object-id "$IDENTITY_OBJECT_ID" \
  --role "Azure AI User" \
  --scope "/subscriptions/$AZURE_SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.CognitiveServices/accounts/$AZURE_AI_PROJECT_NAME" \
  2>/dev/null && echo -e "${GREEN}✓ Azure AI User role assigned${NC}" || echo -e "${YELLOW}⚠ Azure AI User role already assigned or failed${NC}"

# Search Index Data Contributor
az role assignment create \
  --assignee-object-id "$IDENTITY_OBJECT_ID" \
  --role "Search Index Data Contributor" \
  --scope "/subscriptions/$AZURE_SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.Search/searchServices/$AZURE_SEARCH_INDEX" \
  2>/dev/null && echo -e "${GREEN}✓ Search Index Data Contributor role assigned${NC}" || echo -e "${YELLOW}⚠ Search Index role already assigned or failed${NC}"

# Search Service Contributor
az role assignment create \
  --assignee-object-id "$IDENTITY_OBJECT_ID" \
  --role "Search Service Contributor" \
  --scope "/subscriptions/$AZURE_SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.Search/searchServices/$AZURE_SEARCH_INDEX" \
  2>/dev/null && echo -e "${GREEN}✓ Search Service Contributor role assigned${NC}" || echo -e "${YELLOW}⚠ Search Service role already assigned or failed${NC}"

# Storage Blob Data Reader (for reading blobs to index)
az role assignment create \
  --assignee-object-id "$IDENTITY_OBJECT_ID" \
  --role "Storage Blob Data Reader" \
  --scope "/subscriptions/$AZURE_SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.Storage/storageAccounts/$AZURE_STORAGE_ACCOUNT_NAME" \
  2>/dev/null && echo -e "${GREEN}✓ Storage Blob Data Reader role assigned${NC}" || echo -e "${YELLOW}⚠ Storage Blob role already assigned or failed${NC}"

# Get the Container App URL
echo "==> Getting Container App URL"
APP_FQDN=$(az containerapp show \
    --name "$INDEXER_CONTAINER_APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --query properties.configuration.ingress.fqdn \
    --output tsv | tr -d '\r')

echo ""
echo "=========================================="
echo "✓ Indexer service deployed successfully!"
echo "=========================================="
echo ""
echo "Container App URL: https://${APP_FQDN}"
echo "Health Check: https://${APP_FQDN}/health"
echo "Index Endpoint: https://${APP_FQDN}/api/index"
echo ""
echo "To test the indexer, send a POST request:"
echo "  curl -X POST https://${APP_FQDN}/api/index \\"
echo "       -H 'Content-Type: application/json' \\"
echo "       -d '{\"appId\": \"your-app-id\", \"container\": \"your-app-id\"}'"
echo ""
echo "Update your .env file with:"
echo "  AZURE_INDEXING_FUNCTION_URL=https://${APP_FQDN}/api/index"
echo "  (Note: Variable name kept for backward compatibility)"
echo ""
