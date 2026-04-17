# Container App Logging Guide

This guide documents all the working methods to read and search logs from Azure Container Apps.

## Table of Contents

- [Container App Logging Guide](#container-app-logging-guide)
  - [Table of Contents](#table-of-contents)
  - [Prerequisites](#prerequisites)
    - [Environment Variables](#environment-variables)
  - [Verify Logging Configuration](#verify-logging-configuration)
    - [Check Container App Environment Logging Setup](#check-container-app-environment-logging-setup)
    - [Verify Log Analytics Workspace Connection](#verify-log-analytics-workspace-connection)
    - [Check Application Insights Integration](#check-application-insights-integration)
  - [Method 1: Real-time Container App Logs](#method-1-real-time-container-app-logs)
    - [Get Recent Logs (Most Recent)](#get-recent-logs-most-recent)
    - [Get Maximum Recent Logs (300 lines max)](#get-maximum-recent-logs-300-lines-max)
    - [Stream Live Logs (Follow Mode)](#stream-live-logs-follow-mode)
    - [Search for Specific Error Patterns in Recent Logs](#search-for-specific-error-patterns-in-recent-logs)
  - [Method 2: Log Analytics Workspace Queries](#method-2-log-analytics-workspace-queries)
    - [Get Workspace Information](#get-workspace-information)
    - [List Container Apps in Resource Group](#list-container-apps-in-resource-group)
    - [Discover Available Log Tables](#discover-available-log-tables)
  - [Method 3: KQL Queries for Structured Logging](#method-3-kql-queries-for-structured-logging)
    - [Application Traces (Structured Logs)](#application-traces-structured-logs)
    - [HTTP Requests Analysis](#http-requests-analysis)
    - [Error Analysis Queries](#error-analysis-queries)
      - [HTTP Error Status Codes (4xx, 5xx)](#http-error-status-codes-4xx-5xx)
      - [Search for Specific Error Messages](#search-for-specific-error-messages)
  - [Method 4: Performance and Monitoring Queries](#method-4-performance-and-monitoring-queries)
    - [Request Performance Analysis](#request-performance-analysis)
    - [Application Health Summary](#application-health-summary)
  - [Method 5: Azure Portal Access and Verification](#method-5-azure-portal-access-and-verification)
    - [Verify Logging Configuration via Azure Portal](#verify-logging-configuration-via-azure-portal)
      - [1. Check Container App Environment Logging Setup](#1-check-container-app-environment-logging-setup)
      - [2. Verify Container App is Using the Environment](#2-verify-container-app-is-using-the-environment)
      - [3. Verify Log Analytics Workspace Connection](#3-verify-log-analytics-workspace-connection)
      - [4. Test Log Flow with Live Data](#4-test-log-flow-with-live-data)
      - [5. Verify Application Insights Integration (Optional)](#5-verify-application-insights-integration-optional)
    - [Direct Portal Links for Quick Access](#direct-portal-links-for-quick-access)
    - [Visual Indicators of Proper Log Flow](#visual-indicators-of-proper-log-flow)
      - [✅ **Healthy Logging Setup - What to Look For:**](#-healthy-logging-setup---what-to-look-for)
      - [❌ **Logging Issues - Warning Signs:**](#-logging-issues---warning-signs)
    - [Troubleshooting via Portal](#troubleshooting-via-portal)
      - [If No Logs Appear:](#if-no-logs-appear)
      - [Generate Test Traffic for Verification:](#generate-test-traffic-for-verification)
  - [Common Use Cases](#common-use-cases)
    - [Troubleshooting Application Startup Issues](#troubleshooting-application-startup-issues)
    - [Monitoring API Health](#monitoring-api-health)
    - [Finding Authentication/Authorization Issues](#finding-authenticationauthorization-issues)
    - [Checking for Dependency Issues](#checking-for-dependency-issues)
  - [Best Practices](#best-practices)
  - [Tips for Effective Log Analysis](#tips-for-effective-log-analysis)
  - [How Container App Logging Works](#how-container-app-logging-works)
    - [Automatic Log Collection](#automatic-log-collection)
    - [Log Types Collected](#log-types-collected)
    - [Enabling/Disabling Logging](#enablingdisabling-logging)
    - [Log Retention and Performance](#log-retention-and-performance)
  - [Troubleshooting Logging Issues](#troubleshooting-logging-issues)
    - [No Logs Appearing in Log Analytics](#no-logs-appearing-in-log-analytics)
    - [Query Syntax Errors](#query-syntax-errors)
    - [No Results Found](#no-results-found)
    - [Performance Issues](#performance-issues)
    - [Common Configuration Problems](#common-configuration-problems)
      - [Missing Logs After Deployment](#missing-logs-after-deployment)
      - [Workspace Permission Issues](#workspace-permission-issues)
  - [Portal-Based Health Check Checklist](#portal-based-health-check-checklist)
    - [Quick Health Check (5-minute verification):](#quick-health-check-5-minute-verification)
    - [Detailed Health Check (15-minute verification):](#detailed-health-check-15-minute-verification)
    - [Monthly Health Check:](#monthly-health-check)

## Prerequisites

- Azure CLI installed and logged in

### Environment Variables
Set these variables for use throughout this guide:
```bash
export RESOURCE_GROUP="rg-ai-foundry-standard"
export CONTAINER_APP_NAME="intake-agent-api"
export CONTAINER_APP_ENV="agents-env"
export LOG_ANALYTICS_WORKSPACE="ai-foundry-std-log-analytics"
export WORKSPACE_ID="1111111-2222-3333-4444-555555555555"
export APP_INSIGHTS_NAME="app-insights"
```

## Verify Logging Configuration

### Check Container App Environment Logging Setup
```bash
# Verify that the Container App Environment is configured to send logs to Log Analytics
az containerapp env show --name "$CONTAINER_APP_ENV" --resource-group "$RESOURCE_GROUP" --query "properties.appLogsConfiguration" --output json
```

**Expected Output:**
```json
{
  "destination": "log-analytics",
  "logAnalyticsConfiguration": {
    "customerId": "1111111-2222-3333-4444-555555555555",
    "dynamicJsonColumns": false,
    "sharedKey": null
  }
}
```

### Verify Log Analytics Workspace Connection
```bash
# Confirm which Log Analytics workspace the Container App Environment is connected to
az containerapp env show --name "$CONTAINER_APP_ENV" --resource-group "$RESOURCE_GROUP" --query "{name:name, location:location, logsConfig:properties.appLogsConfiguration}" --output json

# Cross-reference with the actual Log Analytics workspace
az monitor log-analytics workspace show --resource-group "$RESOURCE_GROUP" --workspace-name "$LOG_ANALYTICS_WORKSPACE" --query "{name:name, customerId:customerId, location:location}" --output json
```

### Check Application Insights Integration
```bash
# Check if Application Insights is also connected to the same Log Analytics workspace
az monitor app-insights component show --app "$APP_INSIGHTS_NAME" --resource-group "$RESOURCE_GROUP" --query "{name:name, workspaceResourceId:workspaceResourceId}" --output json
```

**Key Points:**
- ✅ Container Apps **automatically** send logs to Log Analytics when the environment is properly configured
- ✅ The `customerId` in Container App Environment must match the Log Analytics workspace `customerId`
- ✅ Application Insights (if used) should point to the same Log Analytics workspace for unified logging

## Method 1: Real-time Container App Logs

### Get Recent Logs (Most Recent)
```bash
az containerapp logs show --name "$CONTAINER_APP_NAME" --resource-group "$RESOURCE_GROUP" --tail 100
```

### Get Maximum Recent Logs (300 lines max)
```bash
az containerapp logs show --name "$CONTAINER_APP_NAME" --resource-group "$RESOURCE_GROUP" --tail 300
```

### Stream Live Logs (Follow Mode)
```bash
az containerapp logs show --name "$CONTAINER_APP_NAME" --resource-group "$RESOURCE_GROUP" --follow --tail 50
```

### Search for Specific Error Patterns in Recent Logs

> Note: the following examples use `grep` for filtering and assume a Unix-like shell (bash/zsh/WSL/Git Bash). If you're running these commands without `grep`, omit the pipe and filter using your preferred tooling.

```bash
# Search for "server_error" (case-insensitive)
az containerapp logs show --name "$CONTAINER_APP_NAME" --resource-group "$RESOURCE_GROUP" --tail 300 | grep -i "server_error"

# Search for multiple error patterns
az containerapp logs show --name "$CONTAINER_APP_NAME" --resource-group "$RESOURCE_GROUP" --tail 300 | grep -iE "(server_error|error|exception|fail)"

# Search for specific status codes
az containerapp logs show --name "$CONTAINER_APP_NAME" --resource-group "$RESOURCE_GROUP" --tail 300 | grep -E "(404|500|502|503)"
```

## Method 2: Log Analytics Workspace Queries

### Get Workspace Information
```bash
# List all Log Analytics workspaces in resource group
az monitor log-analytics workspace list --resource-group "$RESOURCE_GROUP" --query "[].{Name:name, ResourceGroup:resourceGroup, Location:location}" --output table

# Get workspace details including customer ID
az monitor log-analytics workspace show --resource-group "$RESOURCE_GROUP" --workspace-name "$LOG_ANALYTICS_WORKSPACE" --query "{customerId:customerId, name:name}" --output table
```

### List Container Apps in Resource Group
```bash
az containerapp list --resource-group "$RESOURCE_GROUP" --query "[].{Name:name, Environment:properties.environmentId, FQDN:properties.configuration.ingress.fqdn}" --output table
```

### Discover Available Log Tables
```bash
az monitor log-analytics query \
  --workspace "$WORKSPACE_ID" \
  --analytics-query "search * | where TimeGenerated > ago(1h) | distinct Type | take 50" \
  --output table
```

## Method 3: KQL Queries for Structured Logging

### Application Traces (Structured Logs)
```bash
# Get recent application traces with essential information
az monitor log-analytics query \
  --workspace "$WORKSPACE_ID" \
  --analytics-query "AppTraces | where TimeGenerated > ago(2h) | project TimeGenerated, AppRoleInstance, Message, Properties | order by TimeGenerated desc" \
  --output table

# Get all traces from last 24 hours
az monitor log-analytics query \
  --workspace "$WORKSPACE_ID" \
  --analytics-query "AppTraces | where TimeGenerated > ago(24h) | order by TimeGenerated desc | take 50" \
  --output table
```

### HTTP Requests Analysis
```bash
# Get recent HTTP requests with response codes and duration
az monitor log-analytics query \
  --workspace "$WORKSPACE_ID" \
  --analytics-query "AppRequests | where TimeGenerated > ago(2h) | project TimeGenerated, Name, Url, ResultCode, DurationMs, AppRoleInstance | order by TimeGenerated desc" \
  --output table

# Get all requests from last 24 hours
az monitor log-analytics query \
  --workspace "$WORKSPACE_ID" \
  --analytics-query "AppRequests | where TimeGenerated > ago(24h) | order by TimeGenerated desc | take 50" \
  --output table
```

### Error Analysis Queries

#### HTTP Error Status Codes (4xx, 5xx)
```bash
# Find all HTTP errors (status codes >= 400) in last 24 hours
az monitor log-analytics query \
  --workspace "$WORKSPACE_ID" \
  --analytics-query "AppRequests | where TimeGenerated > ago(24h) | where toint(ResultCode) >= 400 | project TimeGenerated, Name, Url, ResultCode, DurationMs, AppRoleInstance | order by TimeGenerated desc" \
  --output table

# Count errors by status code
az monitor log-analytics query \
  --workspace "$WORKSPACE_ID" \
  --analytics-query "AppRequests | where TimeGenerated > ago(24h) | where toint(ResultCode) >= 400 | summarize Count = count() by ResultCode | order by Count desc" \
  --output table
```

#### Search for Specific Error Messages
```bash
# Search for "server_error" in traces (adjust time range as needed)
az monitor log-analytics query \
  --workspace "$WORKSPACE_ID" \
  --analytics-query "AppTraces | where TimeGenerated > ago(7d) | where Message contains 'server_error' or Properties contains 'server_error' | project TimeGenerated, AppRoleInstance, Message, Properties | order by TimeGenerated desc" \
  --output table

# Search for any error-related keywords
az monitor log-analytics query \
  --workspace "$WORKSPACE_ID" \
  --analytics-query "AppTraces | where TimeGenerated > ago(24h) | where Message has_any ('server_error', 'error', 'Error', 'exception', 'Exception', 'fail', 'Fail') | project TimeGenerated, AppRoleInstance, Message, Properties | order by TimeGenerated desc | take 50" \
  --output table
```

## Method 4: Performance and Monitoring Queries

### Request Performance Analysis
```bash
# Analyze request performance over time
az monitor log-analytics query \
  --workspace "$WORKSPACE_ID" \
  --analytics-query "AppRequests | where TimeGenerated > ago(24h) | summarize AvgDuration = avg(DurationMs), MaxDuration = max(DurationMs), RequestCount = count() by bin(TimeGenerated, 1h) | order by TimeGenerated desc" \
  --output table

# Find slow requests (> 1000ms)
az monitor log-analytics query \
  --workspace "$WORKSPACE_ID" \
  --analytics-query "AppRequests | where TimeGenerated > ago(24h) | where DurationMs > 1000 | project TimeGenerated, Name, Url, DurationMs, ResultCode | order by DurationMs desc" \
  --output table
```

### Application Health Summary
```bash
# Get error rate and performance summary
az monitor log-analytics query \
  --workspace "$WORKSPACE_ID" \
  --analytics-query "AppRequests | where TimeGenerated > ago(24h) | summarize TotalRequests = count(), ErrorRequests = countif(toint(ResultCode) >= 400), AvgDuration = avg(DurationMs) | extend ErrorRate = (ErrorRequests * 100.0) / TotalRequests" \
  --output table
```

## Method 5: Azure Portal Access and Verification

### Verify Logging Configuration via Azure Portal

#### 1. Check Container App Environment Logging Setup
**Steps:**
1. Navigate to: **Azure Portal** → **Resource Groups** → `$RESOURCE_GROUP`
2. Find and click: **Container Apps Environment** → `$CONTAINER_APP_ENV`
3. In the left menu, go to: **Monitoring** → **Logging options**
4. Look for the **Log Destination Configuration** section:
   - **Subscription**: Should show the Subscription where your Log Analytics Workspace is located
   - **Resource group**: should show the Resource Group where your Log Analytics Workspace is located
   - **Log Analytics Workspace**: Should show your Log Analytics Workspace
   - **Status**: Should be "Connected" or "Enabled"

#### 2. Verify Container App is Using the Environment
**Steps:**
1. Navigate to: **Azure Portal** → **Resource Groups** → `$RESOURCE_GROUP`
2. Find and click: **Container App** → `$CONTAINER_APP_NAME`
3. In the **Overview** tab, verify:
   - **Environment**: Should show your Container App Environment
   - **Status**: Should show "Running"
4. In the left menu, go to: **Monitoring** → **Log stream**
   - You should see live logs streaming (if app is active)
   - If no logs appear, check if the app is receiving traffic

#### 3. Verify Log Analytics Workspace Connection
**Steps:**
1. Navigate to: **Azure Portal** → **Resource Groups** → `$RESOURCE_GROUP`
2. Find and click: **Log Analytics Workspace** → `$LOG_ANALYTICS_WORKSPACE`
3. In the **Overview** tab, check:
   - **Status**: Should show "Active" or "Running"
   - **Data Retention**: Note the retention period (typically 30-90 days)
4. In the left menu, go to: **General** → **Logs**
   - This opens the KQL query interface
   - Try running: `search * | where TimeGenerated > ago(1h) | take 10`
   - You should see recent data if logs are flowing

#### 4. Test Log Flow with Live Data
**Steps:**
1. In **Log Analytics Workspace** → **Logs**, run these queries:

   **Check for recent Container App data:**
   ```kql
   search * 
   | where TimeGenerated > ago(1h) 
   | where * has "${CONTAINER_APP_NAME}"
   | take 20
   ```

   **Check AppTraces table:**
   ```kql
   AppTraces 
   | where TimeGenerated > ago(1h) 
   | where AppRoleInstance contains "${CONTAINER_APP_NAME}"
   | take 10
   ```

   **Check AppRequests table:**
   ```kql
   AppRequests 
   | where TimeGenerated > ago(1h) 
   | where AppRoleInstance contains "${CONTAINER_APP_NAME}"
   | take 10
   ```

#### 5. Verify Application Insights Integration (Optional)
**Steps:**
1. Navigate to: **Azure Portal** → **Resource Groups** → `$RESOURCE_GROUP`
2. Find and click: **Application Insights** → `$APP_INSIGHTS_NAME`
3. In the **Overview** tab, check:
   - **Connected Log Analytics Workspace**: Should show `$LOG_ANALYTICS_WORKSPACE`
   - **Live Metrics**: Should show real-time application data
4. In the left menu, go to: **Monitoring** → **Logs**
   - Should show the same data as Log Analytics workspace
   - Try the same KQL queries as above

### Direct Portal Links for Quick Access
1. **Container App Logs (Live Stream)**: 
   - Navigate to: Azure Portal → Resource Groups → `$RESOURCE_GROUP` → `$CONTAINER_APP_NAME` → Monitoring → **Log stream**

2. **Container App Environment Configuration**:
   - Navigate to: Azure Portal → Resource Groups → `$RESOURCE_GROUP` → `$CONTAINER_APP_ENV` → Monitoring → **Logs**

3. **Log Analytics Workspace Query Interface**:
   - Navigate to: Azure Portal → Resource Groups → `$RESOURCE_GROUP` → `$LOG_ANALYTICS_WORKSPACE` → General → **Logs**

4. **Application Insights** (if available):
   - Navigate to: Azure Portal → Resource Groups → `$RESOURCE_GROUP` → `$APP_INSIGHTS_NAME` → Monitoring → **Logs**

### Visual Indicators of Proper Log Flow

#### ✅ **Healthy Logging Setup - What to Look For:**
- **Container App Environment**: Shows "Log Analytics" as destination with workspace name
- **Live Log Stream**: Shows real-time application logs when app receives traffic
- **Log Analytics Queries**: Return recent data within 1-5 minutes of log generation
- **Application Insights**: Shows live metrics and traces (if configured)
- **No Error Messages**: No "disconnected" or "failed" status indicators

#### ❌ **Logging Issues - Warning Signs:**
- **No Live Logs**: Log stream shows "No logs available" even with active traffic
- **Empty Query Results**: KQL queries return no data for recent time periods
- **Configuration Errors**: Portal shows "Disconnected" or "Configuration Error"
- **Missing Tables**: Standard tables (AppTraces, AppRequests) don't exist in workspace
- **Old Data Only**: Only historical data available, no recent entries

### Troubleshooting via Portal

#### If No Logs Appear:
1. **Check Container App Status**: Ensure it's "Running" and receiving traffic
2. **Verify Environment Link**: Confirm Container App is using the correct environment
3. **Test Log Analytics**: Run basic queries to ensure workspace is functional
4. **Check Retention Settings**: Verify logs aren't being purged too quickly
5. **Generate Test Traffic**: Make API calls to your container app to generate logs
6. **Wait for Propagation**: Allow 5-10 minutes for initial log flow to establish

#### Generate Test Traffic for Verification:
```bash
# Make a test request to generate logs (replace with your actual FQDN)
curl -X GET "https://intake-agent-api.blackdesert-33f246e1.eastus2.azurecontainerapps.io/health"

# Then check the portal logs within 2-5 minutes
```

## Common Use Cases

### Troubleshooting Application Startup Issues
```bash
# Check application startup logs
az containerapp logs show --name "$CONTAINER_APP_NAME" --resource-group "$RESOURCE_GROUP" --tail 100 | grep -iE "(startup|init|error|exception)"
```

### Monitoring API Health
```bash
# Check recent API calls and their status
az monitor log-analytics query \
  --workspace "$WORKSPACE_ID" \
  --analytics-query "AppRequests | where TimeGenerated > ago(1h) | project TimeGenerated, Name, ResultCode, DurationMs | order by TimeGenerated desc" \
  --output table
```

### Finding Authentication/Authorization Issues
```bash
# Look for 401/403 errors
az monitor log-analytics query \
  --workspace "$WORKSPACE_ID" \
  --analytics-query "AppRequests | where TimeGenerated > ago(24h) | where ResultCode in ('401', '403') | project TimeGenerated, Name, Url, ResultCode, AppRoleInstance | order by TimeGenerated desc" \
  --output table
```

### Checking for Dependency Issues
```bash
# Look for dependency-related logs (if AppDependencies table exists)
az monitor log-analytics query \
  --workspace "$WORKSPACE_ID" \
  --analytics-query "AppDependencies | where TimeGenerated > ago(24h) | project TimeGenerated, Name, Data, Success, DurationMs | order by TimeGenerated desc | take 50" \
  --output table
```

## Best Practices

1. **Use appropriate time ranges**: Start with shorter ranges (1-2 hours) and extend if needed
2. **Limit result sets**: Use `take N` or `| limit N` to avoid overwhelming output
3. **Use structured queries**: KQL queries in Log Analytics provide more filtering options than simple log streaming
4. **Monitor regularly**: Set up alerts for critical errors rather than manually checking logs
5. **Save useful queries**: Document working queries for your specific use cases

## Tips for Effective Log Analysis

1. **Combine multiple methods**: Use real-time logs for immediate issues and Log Analytics for historical analysis
2. **Filter by time**: Always specify appropriate time ranges to improve query performance
3. **Use case-insensitive searches**: Use `has_any()` or `contains()` functions for flexible text matching
4. **Check multiple log types**: Don't rely only on AppTraces; also check AppRequests, AppDependencies, etc.
5. **Export results**: Use `--output json` or `--output csv` for further processing if needed

## How Container App Logging Works

### Automatic Log Collection
**Yes, Azure Container Apps automatically send logs to Log Analytics by default when:**
1. The Container App Environment is configured with a Log Analytics workspace
2. The workspace is properly connected (matching `customerId`)
3. The application produces logs to stdout/stderr (standard output streams)

### Log Types Collected
- **Application Logs**: Console output from your application (stdout/stderr)
- **System Logs**: Container runtime and infrastructure logs
- **HTTP Requests**: Incoming request logs (AppRequests table)
- **Application Traces**: Structured logging (AppTraces table)
- **Dependencies**: External service calls (AppDependencies table)
- **Performance Counters**: System metrics (AppMetrics table)

### Enabling/Disabling Logging
```bash
# Logging is enabled at the Container App Environment level
# To check current status:
az containerapp env show --name "$CONTAINER_APP_ENV" --resource-group "$RESOURCE_GROUP" --query "properties.appLogsConfiguration"

# To enable logging (if not already enabled):
az containerapp env update --name "$CONTAINER_APP_ENV" --resource-group "$RESOURCE_GROUP" --logs-workspace-id "$WORKSPACE_ID" --logs-workspace-key "<workspace-key>"
```

### Log Retention and Performance
- **Default Retention**: 90 days in Log Analytics workspace
- **Log Ingestion**: Near real-time (1-5 minutes delay)
- **Query Performance**: Optimized for recent data (last 24-48 hours)

## Troubleshooting Logging Issues

### No Logs Appearing in Log Analytics

1. **Check Environment Configuration:**
```bash
az containerapp env show --name "$CONTAINER_APP_ENV" --resource-group "$RESOURCE_GROUP" --query "properties.appLogsConfiguration"
```

2. **Verify Workspace Connection:**
```bash
# Ensure customerId matches
az monitor log-analytics workspace show --resource-group "$RESOURCE_GROUP" --workspace-name "$LOG_ANALYTICS_WORKSPACE" --query "customerId"
```

3. **Check Application Output:**
```bash
# Verify app is producing logs to stdout/stderr
az containerapp logs show --name "$CONTAINER_APP_NAME" --resource-group "$RESOURCE_GROUP" --tail 50
```

4. **Test Log Analytics Connection:**
```bash
# Check if any data is being received
az monitor log-analytics query --workspace "$WORKSPACE_ID" --analytics-query "search * | where TimeGenerated > ago(1h) | take 10"
```

### Query Syntax Errors
- Ensure proper KQL syntax (case-sensitive operators)
- Use `toint()`, `tostring()`, etc., for type conversions
- Escape special characters in search strings

### No Results Found
- **Check time ranges**: Extend if necessary (logs may have delay)
- **Verify table names**: Use the discovery query to confirm available tables
- **Application logging**: Ensure your application is writing to stdout/stderr
- **Log retention**: Check if logs are older than workspace retention period

### Performance Issues
- Limit query scope with appropriate time ranges
- Use `take` or `limit` to restrict result sets
- Consider using `summarize` for aggregated data instead of raw logs
- Use indexed columns (TimeGenerated, Type) for filtering

### Common Configuration Problems

#### Missing Logs After Deployment
```bash
# Check if new container instances are properly connected
az containerapp revision list --name "$CONTAINER_APP_NAME" --resource-group "$RESOURCE_GROUP" --query "[].{name:name, active:properties.active, createdTime:properties.createdTime}"

# Restart the container app if needed
az containerapp revision restart --name "$CONTAINER_APP_NAME" --resource-group "$RESOURCE_GROUP" --revision-name "<latest-revision>"
```

#### Workspace Permission Issues
```bash
# Check if the Container App Environment has proper permissions to write to Log Analytics
az role assignment list --scope "/subscriptions/$(az account show --query id -o tsv)/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.OperationalInsights/workspaces/$LOG_ANALYTICS_WORKSPACE" --query "[].{principalType:principalType, roleDefinitionName:roleDefinitionName}"
```

## Portal-Based Health Check Checklist

Use this checklist to verify logging is working correctly via the Azure Portal:

### Quick Health Check (5-minute verification):
- [ ] **Container App Environment** → Configuration shows "Log Analytics" destination
- [ ] **Container App** → Log stream shows recent activity (make a test request if needed)
- [ ] **Log Analytics Workspace** → Query `search * | where TimeGenerated > ago(15m) | take 5` returns data
- [ ] **Application Insights** → Live metrics shows active telemetry (if configured)

### Detailed Health Check (15-minute verification):
- [ ] **Environment Status**: Container Apps Environment shows "Running" status
- [ ] **Workspace Connectivity**: Log Analytics workspace shows "Active" status  
- [ ] **Data Tables Present**: Tables `AppTraces`, `AppRequests` exist and have recent data
- [ ] **Log Latency**: Logs appear in workspace within 5 minutes of generation
- [ ] **Error Monitoring**: No system errors in Container App or workspace logs
- [ ] **Data Retention**: Workspace retention policy matches requirements (check Settings → Usage and estimated costs)

### Monthly Health Check:
- [ ] **Data Ingestion Costs**: Monitor Log Analytics costs in Cost Management
- [ ] **Retention Cleanup**: Verify old logs are properly purged per retention policy
- [ ] **Performance**: Query response times remain acceptable for typical time ranges
- [ ] **Alerting**: Log-based alerts are firing correctly (if configured)
- [ ] **Backup Strategy**: Ensure critical logs are exported if needed for compliance