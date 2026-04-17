# Insights API - Deployment

Below you will find the instructions for deploying the Insights API as a containerized application using different deployment models.

You can use the same steps to deploy other solution components, such as the indexer or the Azure Pricing MCP server.

## 📋 Prerequisites

Before deploying, ensure you have:

1. **Azure CLI** installed and configured
   ```bash
   az --version
   az login
   ```

2. **Docker** installed for local building (optional)
   ```bash
   docker --version
   ```

3. **Required Azure Resources**:
   - Azure AI Project/OpenAI Service
   - Azure Cognitive Search
   - Azure Storage Account  
   - Azure Tables
   - Azure Container App Environment

## 🏗️ Deployment Options

### Option 1: Automated Deployment through Actions Workflow
The easiest way to deploy the Insights Agent API endpoints to Azure Container Apps is through the provided Actions Workflow:

1. **Set environment variables**:
   
   Make sure the environment variables in the file [env.example](../env.example) are set in GitHub

2. **Customize and run the Actions workflow**:
   
   Update the trigger in the Actions Workflow [deploy-insights-api.yml](../../../.github/workflows/deploy-insights-api.yml) so that it triggers when you commit to your branch.
   Then commit some changes to trigger it.
   
   Note that you can also trigger this workflow manually.

### Option 2: Automated Deployment Script

If you don't have GitHub runners in your subscriptions, use the deployment script [deploy_insights_api.sh](../scripts/deployment/deploy_insights_api.sh)

1. **Set environment variables**:
```bash
   # Create .env file with your configuration
   cp env.example .env
   # Update .env with your values
```   
2. **Make sure the environment variables in the section "Configuration variables" of the script are also set.**
   
3. **Run the deployment script**:
```bash
./deploy_insights_api.sh
```

### Option 3: Manual Docker Deployment

1. **Build the Docker image**:
   ```bash
   docker build -t insights-agent-api:latest .
   ```

2. **Test locally**:
   ```bash
   # Create .env file with your configuration
   cp env.example .env
   # Edit .env with your values
   
   # Run container
   docker run -p 8000:8000 --env-file .env insights-agent-api:latest
   ```

3. **Push to your container registry**:
   ```bash
   # Tag for your registry
   docker tag insights-agent-api:latest youracr.azurecr.io/insights-agent-api:latest
   
   # Push to registry
   docker push youracr.azurecr.io/insights-agent-api:latest
   ```

## 🔧 Configuration

### Required Environment Variables
The container requires the following environment variables, which are read from your local .env file:

| Variable | Description | Example |
|----------|-------------|---------|
| `AZURE_EXISTING_AIPROJECT_ENDPOINT` | Azure AI Foundry project endpoint | `https://<ai-foundry-resource>.services.ai.azure.com/api/projects/<project-name>` |
| `AZURE_AI_AGENT_DEPLOYMENT_NAME` | AI model deployment name | `gpt-4.1` |
| `AZURE_SEARCH_ENDPOINT` | Azure Cognitive Search endpoint | `https://your-search.search.windows.net` |
| `AZURE_STORAGE_ACCOUNT_URL` | Storage account URL | `https://yourstorageaccount.blob.core.windows.net/` |
| `AZURE_TABLES_ACCOUNT_URL` | Tables storage URL | `https://yourstorageaccount.table.core.windows.net/` |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID | `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` |
| `RESOURCE_GROUP` | Resource group name | `your-resource-group` |
| `LOCATION` | Location | `your-resources-location` |
| `AZURE_OPENAI_API_VERSION` | Azure OpenAI API Version | `your-openai-version` |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | Azure OpenAI Embedding | `your-openai-embedding` |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI Endpoint | `your-openai-endpoint` |
| `AZURE_SEARCH_INDEX_NAME` | AI Search index name | `your-ai-search-index` |
| `AZURE_SEARCH_API_VERSION` | AI Search API Version | `your-search-api-version` |
| `AZURE_SEARCH_SEMANTIC_CONFIG` | AI Search semantic config | `DefaultSemantic` |
| `FUNCTION_APP_NAME` | Azure Function App Name | `your-function-app` |
| `FUNCTION_NAME` | Azure Function Name | `your-function-name` |
| `AZURE_INDEXING_FUNCTION_URL` | Azure Function Indexing URL | `your-indexing-function-url` |
| `PYTHONPATH` | Python path | `/app` |
| `LOG_LEVEL` | Log level | `INFO` |

### Authentication & Authorization
The application uses **Azure Managed Identity** for authentication. Ensure the Container App has the necessary permissions listed in the Insights Agent [README.md](../README.md).

## 🏥 Health Monitoring

The container includes a health check endpoint at `/health` that:

- Verifies required environment variables are set
- Returns service status and version information
- Used by Azure Container Apps for health probes

Access health check: `https://your-app.azurecontainerapps.io/health`

## 📊 API Documentation

Once deployed, access the interactive API documentation:

- **Swagger UI**: `https://your-app.azurecontainerapps.io/docs`
- **ReDoc**: `https://your-app.azurecontainerapps.io/redoc`

## 🔍 Monitoring and Logs

### View Container Logs
```bash
az containerapp logs show \
  --name insights-agent-api \
  --resource-group insights-agent-rg \
  --follow
```

### Scale the Application
```bash
az containerapp update \
  --name insights-agent-api \
  --resource-group insights-agent-rg \
  --min-replicas 2 \
  --max-replicas 20
```

### Update Environment Variables
```bash
az containerapp update \
  --name insights-agent-api \
  --resource-group insights-agent-rg \
  --set-env-vars AZURE_AI_AGENT_DEPLOYMENT_NAME=gpt-4o
```

### Rebuild and redeploy the container image
```bash
echo "🐳 Rebuilding Docker image with import fixes..."
az acr build \
 --registry aiintakeacr \
 --image insights-agent-api:latest \
 --file Dockerfile \
 .

echo "🔄 Updating container app with new image..."
az containerapp update \
 --name insights-agent-api \
 --resource-group rg-ai-foundry-standard \
 --image aiintakeacr.azurecr.io/insights-agent-api:latest
```

## 🚀 API Endpoints

The deployed API provides these endpoints:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check for container health monitoring |
| `/createApplicationId` | POST | Create new application with RBAC setup |
| `/indexDocuments` | POST | Index uploaded documents for an application |
| `/runAnalysis` | POST | Run assessment analysis for an application |
| `/generateAssessmentReport` | POST | Generate assessment report for an application |
| `/assessmentComplete` | POST | Complete assessment and cleanup resources |
| `/operations/status` | GET | Get status of operations with filtering options |
| `/operations/summary` | GET | Get summary statistics for operations |
| `/operations/{operation_id}/status` | GET | Get status of a specific operation by ID |
| `/operations/cleanup` | DELETE | Clean up operations with flexible options |

## 🔐 Security Considerations

- The container runs as a non-root user (`appuser`)
- Secrets are managed through Azure Container Apps secrets
- All HTTP traffic is encrypted with TLS
- Managed Identity is used for Azure service authentication
- RBAC permissions are validated for each request

## 🛠️ Troubleshooting

### Common Issues

1. **Container fails to start**:
   - Check environment variables are set correctly
   - Verify Azure resource permissions
   - Check container registry authentication

2. **Health check fails**:
   - Verify required environment variables
   - Check Azure service connectivity
   - Review container logs

3. **API requests fail**:
   - Verify RBAC permissions
   - Check Azure service endpoints
   - Review authentication configuration

### Debug Commands

```bash
# Check container app status
az containerapp show --name insights-agent-api --resource-group insights-agent-rg

# View recent logs
az containerapp logs show --name insights-agent-api --resource-group insights-agent-rg --tail 100

# Check revisions
az containerapp revision list --name insights-agent-api --resource-group insights-agent-rg

# Test health endpoint
curl https://your-app.azurecontainerapps.io/health
```

## 📝 Development

### Local Development with Docker

1. **Create local environment file**:
   ```bash
   cp env.example .env
   # Edit .env with your development values
   ```

2. **Build and run locally**:
   ```bash
   docker build -t insights-agent-api:dev .
   docker run -p 8000:8000 --env-file .env insights-agent-api:dev
   ```

3. **Access local API**:
   - API: `http://localhost:8000`
   - Health: `http://localhost:8000/health`
   - Docs: `http://localhost:8000/docs`

### Updating the Deployment

1. **Make code changes**
2. **Rebuild and deploy**:
   ```bash
   # Using deployment script
   export IMAGE_TAG="v1.1"
   ./deploy-to-aca.sh
   
   # Or using Azure CLI
   az containerapp update \
     --name insights-agent-api \
     --resource-group insights-agent-rg \
     --image youracr.azurecr.io/insights-agent-api:v1.1
   ```

## 📞 Support

For issues with deployment or configuration:

1. Check the troubleshooting section above
2. Review Azure Container Apps documentation
3. Check application logs for specific error messages
4. Verify all Azure resource permissions and configuration