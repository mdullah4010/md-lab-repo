# Indexer Service - Azure Container Apps

This service processes documents and creates searchable indexes. It is deployed as a containerized application on Azure Container Apps.

## Architecture

```
indexer/
├── indexer.py              # Core indexing logic
├── indexer_api.py          # FastAPI REST API wrapper
├── tracing_config.py       # OpenTelemetry tracing configuration
├── logging_config.py       # Logging configuration
├── Dockerfile              # Container image definition
├── requirements.txt        # Python dependencies
└── __init__.py             # Package initialization
```

## Deployment

The indexer is deployed to Azure Container Apps using the deployment script:

```bash
cd scripts
./deploy_indexer_to_aca.sh
```

### Deployment Steps

1. Load environment variables from `.env`
2. Check/create Container App Environment
3. Get ACR login server
4. Build Docker image in ACR (`az acr build`)
5. Create Container App with system-assigned identity
6. Assign RBAC roles (Key Vault, Search, Storage)
7. Configure environment variables
8. Deploy container image

## Container Configuration

### Base Image
- Python 3.11 slim

### Dependencies
- FastAPI + Uvicorn for production ASGI server
- Azure SDK for Python (Search, Storage, AI)
- OpenTelemetry for tracing
- Document processing libraries (pypdf, python-docx, openpyxl)

### Ports
- **8080**: HTTP server listening port

### Environment Variables

Required configuration:
- `AZURE_EXISTING_AIPROJECT_ENDPOINT` - AI Foundry project endpoint
- `AZURE_OPENAI_ENDPOINT` - OpenAI endpoint for embeddings
- `AZURE_OPENAI_ENDPOINT2` - OpenAI base endpoint for vectorizer
- `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` - Embedding model deployment name
- `AZURE_OPENAI_API_VERSION` - API version (default: 2024-10-01-preview)
- `AZURE_OPENAI_EMBED_DIM` - Embedding dimensions (default: 3072)
- `AZURE_SEARCH_ENDPOINT` - Azure AI Search endpoint
- `AZURE_SEARCH_INDEX` - Search index name (fallback/legacy)
- `AZURE_SEARCH_SEMANTIC_CONFIG` - Semantic configuration name
- `AZURE_STORAGE_ACCOUNT_URL` - Storage account URL
- `AZURE_STORAGE_ACCOUNT_NAME` - Storage account name
- `AZURE_KEY_VAULT_URL` - Key Vault URL for secrets
- `PORT` - Server port (default: 8080)
- `LOGGING_LEVEL` - Log level (default: INFO)

### Authentication

Uses **Managed Identity** with the following RBAC roles:
- `Key Vault Secrets Officer` - Access Key Vault secrets
- `Search Index Data Contributor` - Write to search index
- `Search Service Contributor` - Manage search service
- `Storage Blob Data Contributor` - Read documents from storage

## API Endpoints

### Health Check
```bash
GET /health
```
Returns service health status.

**Response**:
```json
{
  "status": "healthy"
}
```

### Index Documents
```bash
POST /api/index
Content-Type: application/json

{
  "appId": "string",
  "container": "string"
}
```

Indexes all documents in the specified container for the given application.

**Response** (Success):
```json
{
  "status": "success",
  "mode": "container",
  "result": {
    "blobs": 10,
    "chunks": 150,
    "uploaded": 150,
    "failed": 0
  }
}
```

**Response** (Error):
```json
{
  "status": "error",
  "error": "error message"
}
```

## Usage Example

```bash
# Get the Container App URL
CONTAINER_URL="https://{container-app-name}.{region}.azurecontainerapps.io"

# Health check
curl "$CONTAINER_URL/health"

# Index documents
curl -X POST "$CONTAINER_URL/api/index" \
  -H "Content-Type: application/json" \
  -d '{
    "appId": "test-app",
    "container": "test-app"
  }'
```

## Monitoring

The service includes OpenTelemetry tracing that integrates with:
- Azure Application Insights
- Azure Monitor
- AI Foundry project tracing

Set `APP_VERBOSE=true` or `TRACING_DEBUG=true` for detailed logging.

## Document Processing

The indexer supports the following document types:
- **Text files** (.txt)
- **PDF documents** (.pdf)
- **Word documents** (.docx)
- **Excel spreadsheets** (.xlsx) - Special handling for dependency data

Documents are:
1. Downloaded from Azure Blob Storage
2. Chunked into smaller segments (max 2000 characters)
3. Embedded using Azure OpenAI embeddings
4. Uploaded to Azure AI Search with vector search enabled

## Search Index Features

- **Semantic search** with semantic ranker
- **Vector search** with HNSW algorithm
- **Hybrid search** combining keyword and vector search
- **Metadata fields**: application_id, source, chunk_id
- **Citation tracking** for source attribution

## Troubleshooting

### View Container Logs
```bash
az containerapp logs show --name {container-app-name} --resource-group {rg} --follow
```

### Get Revision Details
```bash
az containerapp revision list --name {container-app-name} --resource-group {rg}
```

### Restart Container App
```bash
az containerapp restart --name {container-app-name} --resource-group {rg}
```

### Check Container App Status
```bash
az containerapp show --name {container-app-name} --resource-group {rg} --query "properties.runningStatus"
```

## Required Environment Variables

Add to your `.env` file:

```bash
# Azure subscription and resource group
AZURE_SUBSCRIPTION_ID="your-subscription-id"
RESOURCE_GROUP="your-resource-group"
LOCATION="eastus2"

# Container Apps specific
INDEXER_CONTAINER_APP_NAME="indexer-agent-api"
CONTAINER_APP_ENV_NAME="ai-agent-env"
ACR_NAME="your-acr-name"
IMAGE_NAME="indexer-agent-api"
IMAGE_TAG="latest"

# Azure services
AZURE_EXISTING_AIPROJECT_ENDPOINT="https://..."
AZURE_OPENAI_ENDPOINT="https://..."
AZURE_OPENAI_ENDPOINT2="https://..."
AZURE_OPENAI_EMBEDDING_DEPLOYMENT="text-embedding-3-large"
AZURE_SEARCH_ENDPOINT="https://..."
AZURE_STORAGE_ACCOUNT_NAME="your-storage-account"
AZURE_STORAGE_ACCOUNT_URL="https://your-storage-account.blob.core.windows.net"
AZURE_KEY_VAULT_NAME="your-keyvault-name"
AZURE_KEY_VAULT_URL="https://your-keyvault.vault.azure.net"

# Update this after deployment
AZURE_INDEXING_FUNCTION_URL="https://indexer-agent-api.{region}.azurecontainerapps.io"
```

## Next Steps

After deployment:
1. Test the health endpoint
2. Run a test indexing job
3. Update your main application's `.env` with the new `AZURE_INDEXING_FUNCTION_URL`
4. Monitor logs and performance
5. Adjust Container App replicas/resources as needed
