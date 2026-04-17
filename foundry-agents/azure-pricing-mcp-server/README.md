# Azure Pricing MCP Server Deployment Guide

This guide helps you deploy the Azure MCP Pricing Server to Azure Container Apps using Azure CLI.

## Prerequisites

1. **Azure CLI installed**: Download from https://aka.ms/installazurecli
2. **Azure subscription**: Active Azure subscription
3. **PowerShell**: For running the deployment script

## Azure Container Apps Deployment

Azure Container Apps provides a serverless container platform with automatic scaling and simplified management.

### 1. Login to Azure
```bash
az login
```

### 2. Set Variables
```bash
resourceGroup="rg-ai-foundry-standard"
location="estus2"
containerAppEnv="ai-agents-env"
containerRegistry="aiintakeacr"
containerApp="azure-pricing-mcp-server"
```

### 3. Create Resource Group if you do not already have one
```bash
az group create --name $resourceGroup --location $location
```

### 4. Create Container Apps Environment if you do not already have one 
```bash
az containerapp env create `
    --name $containerAppEnv `
    --resource-group $resourceGroup `
    --location $location
```

### 5. Create Azure Container Registry if you do not already have one 
```bash
az acr create `
    --resource-group $resourceGroup `
    --name $containerRegistry `
    --sku Basic `
    --admin-enabled true
```

### 6. Build and Push Container Image
```bash
# Build the image using ACR Build
az acr build --registry $containerRegistry --image mcp-pricing:latest .

# Or build and push it manually using docker
docker build -t $containerRegistry.azurecr.io/mcp-pricing:latest .
az acr login --name $containerRegistry
docker push $containerRegistry.azurecr.io/mcp-pricing:latest
```

### 7. Create Container App
```bash
az containerapp create \
    --name $containerApp \
    --resource-group $resourceGroup \
    --environment $containerAppEnv \
    --image "$containerRegistry.azurecr.io/mcp-pricing:latest" \
    --target-port 8080 \
    --ingress external \
    --registry-server "$containerRegistry.azurecr.io" \
    --min-replicas 1 \
    --max-replicas 5 \
    --cpu 0.5 \
    --memory 1Gi \
    --env-vars \
        MCP_HOST="0.0.0.0" \
        MCP_PORT="8080" \
        MCP_DEBUG="false" \
        CORS_ORIGINS="*" \
        LOG_LEVEL="INFO"
```

### 8. Update Container App (for subsequent deployments)
```bash
# Build new image version
az acr build --registry $containerRegistry --image mcp-pricing:latest .

# Update the container app
az containerapp update \
    --name $containerApp \
    --resource-group $resourceGroup \
    --image "$containerRegistry.azurecr.io/mcp-pricing:latest"
```

### 9. View Container App Logs
```bash
az containerapp logs show --name $containerApp --resource-group $resourceGroup --tail 10
```

## Testing Your Deployment

```bash
# Get the Container App URL
appUrl=$(az containerapp show --name $containerApp --resource-group $resourceGroup --query properties.configuration.ingress.fqdn -o tsv | tr -d '\r\n')

# Test endpoints
curl "https://$appUrl/sse"      # MCP protocol endpoint
curl "https://$appUrl/tools"    # Tools listing
```

## Monitoring and Troubleshooting

```bash
# View logs
az containerapp logs show --name $containerApp --resource-group $resourceGroup --follow --tail 20

# Get app status
az containerapp show --name $containerApp --resource-group $resourceGroup  --query properties.runningStatus

# Scale manually (if needed)
az containerapp update --name $containerApp --resource-group $resourceGroup --min-replicas 1 --max-replicas 3
```

## Environment Variables

The following environment variables are configured automatically:

- `MCP_HOST`: Set to `0.0.0.0`
- `MCP_PORT`: Set to `8080`
- `MCP_DEBUG`: Set to `false` for production
- `MCP_RELOAD`: Set to `false` for production
- `CORS_ORIGINS`: Set to `*` (configure as needed)
- `LOG_LEVEL`: Set to `INFO`

## Useful Commands

### Container Registry Management:
```bash
# List images in ACR
az acr repository list --name mcppricingregistry8682

# List tags for a specific image
az acr repository show-tags --name mcppricingregistry8682 --repository mcp-pricing

# Delete old image versions
az acr repository delete --name mcppricingregistry8682 --image mcp-pricing:old-tag --yes
```

### Container Apps Management:
```bash
# List all container apps
az containerapp list --resource-group $resourceGroup -o table

# Get ingress URL
az containerapp show --name $containerApp--resource-group $resourceGroup --query properties.configuration.ingress.fqdn

# Update environment variables
az containerapp update --name $containerApp--resource-group $resourceGroup --set-env-vars NEW_VAR=value
```

## Security Considerations

1. **Configure CORS properly**: Update `CORS_ORIGINS` to specific domains in production
2. **Enable HTTPS**: Azure Container Apps provides HTTPS by default
3. **Authentication**: Consider adding authentication if needed
4. **Networking**: Configure virtual networks if required

## Cost Optimization

- **Serverless pricing**: Pay only for what you use
- **Automatic scaling**: Scales to zero when not in use
- **Resource efficiency**: Optimized container resource allocation
- **Monitor usage**: Use Azure Cost Management to track costs

## Cleanup

```bash
# Delete the entire resource group (removes everything)
az group delete --name $resourceGroup --yes --no-wait

# Or delete individual resources
az containerapp delete --name $containerApp--resource-group $resourceGroup --yes
az acr delete --name mcppricingregistry8682 --resource-group $resourceGroup --yes
az containerapp env delete --name mcp-pricing-env --resource-group $resourceGroup --yes
```

## Common Issues and Troubleshooting

**Issue: 404 errors on all endpoints**
```bash
# Check if the app is running
az containerapp show --name $containerApp--resource-group $resourceGroup --query properties.runningStatus

# Check logs for startup errors
az containerapp logs show --name $containerApp--resource-group $resourceGroup --tail 50
```

**Issue: Container fails to start**
```bash
# Verify the image exists
az acr repository show --name mcppricingregistry8682 --repository mcp-pricing

# Check environment variables
az containerapp show --name $containerApp--resource-group $resourceGroup --query properties.template.containers[0].env
```

**Issue: Build failures**
```bash
# Clean build with verbose output
az acr build --registry mcppricingregistry8682 --image mcp-pricing:debug . --verbose
```

## Additional Resources

- [Azure Container Apps Documentation](https://docs.microsoft.com/en-us/azure/container-apps/)
- [Azure Container Registry Documentation](https://docs.microsoft.com/en-us/azure/container-registry/)
- [FastMCP Documentation](https://github.com/modelcontextprotocol/python-sdk)