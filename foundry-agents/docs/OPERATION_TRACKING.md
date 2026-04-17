# Operation Status Tracking

This document describes the operation tracking system implemented in the Insights API.

## Table of Contents

1. [Overview](#overview)
2. [Tracked Operations](#tracked-operations)
3. [Operation Status Values](#operation-status-values)
4. [Implementation Details](#implementation-details)
5. [API Endpoints](#api-endpoints)
6. [Operation States](#operation-states)
7. [RBAC Requirements](#rbac-requirements)
8. [Error Handling](#error-handling)
9. [Storage Requirements and Setup](#storage-requirements-and-setup)
10. [Implementation Guide](#implementation-guide)
11. [Health Monitoring and Alerting](#health-monitoring-and-alerting)
12. [Monitoring and Observability](#monitoring-and-observability)
13. [Best Practices](#best-practices)
14. [Data Retention](#data-retention)
15. [Integration Examples](#integration-examples)
16. [AI Agent Development Best Practices](#ai-agent-development-best-practices)
17. [Advanced Monitoring Scenarios](#advanced-monitoring-scenarios)
18. [Troubleshooting Guide](#troubleshooting-guide)
19. [Performance Optimization](#performance-optimization)

## Overview

The operation tracking system provides comprehensive monitoring and status reporting for all long-running operations in the API. Each operation is tracked with detailed progress information, timing metrics, and status updates.

## Tracked Operations

The following operation types are automatically tracked:

1. **CREATE_APPLICATION** (`/createApplicationId`) - RBAC validation and application table cloning
2. **INDEX_DOCUMENTS** (`/indexDocuments`) - Document indexing trigger
3. **RUN_ANALYSIS** (`/runAnalysis`) - Full analysis pipeline execution
4. **GENERATE_REPORT** (`/generateAssessmentReport`) - Assessment report generation
5. **GENERATE_DESIGN** (`/generateDesign`) - Design generation pipeline
6. **KUBERNETES_DISCOVERY** (`/discoverKubernetes`) - Kubernetes discovery workflow
7. **ARCHITECTURE_ANALYSIS** (`/analyzeArchitecture`) - Architecture/security analysis workflow
8. **CODE_ANALYSIS** (`/analyzeCode`) - Code analysis workflow
9. **DELETE_APP_DATA** (`/deleteAppData`) - Cleanup operations

## Operation Status Values

- `pending` - Operation queued but not started
- `in_progress` - Operation currently executing
- `completed` - Operation finished successfully
- `failed` - Operation encountered an error
- `cancelled` - Operation was cancelled by user or system

## Implementation Details

### Automatic Tracking

Operations are automatically tracked using the `@track_operation` decorator:

```python
@track_operation(OperationType.CREATE_APPLICATION, total_steps=4)
async def create_application_id(request: CreateApplicationRequest):
    # Operation implementation
```

### Progress Updates

Operations update progress throughout execution:

```python
if hasattr(create_application_id, '_current_operation'):
    operation = create_application_id._current_operation
    operation.update_progress("Starting RBAC validation", 20, OperationStatus.IN_PROGRESS)
    operation_service = get_operation_service()
    await operation_service.update_operation(operation)
```

## API Endpoints

### Get Operation Status

**GET** `/operations/status`

Retrieve operations with flexible filtering options.

**Query Parameters:**
- `operation_id` (optional) - Specific operation ID
- `app_id` (optional) - Filter by application ID
- `user_object_id` (optional) - Filter by user object ID
- `operation_type` (optional) - Filter by type (create_application, index_documents, run_analysis, generate_report, generate_design, kubernetes_discovery, architecture_analysis, code_analysis, delete_app_data)
- `status` (optional) - Filter by status (pending, in_progress, completed, failed, cancelled)
- `limit` (optional) - Results limit (1-100, default: 10)
- `offset` (optional) - Pagination offset (default: 0)

**Headers (required when app_id provided):**
- `X-User-Object-Id` - User object ID for RBAC validation
- `X-Storage-Account` - Storage account name for RBAC validation
- `X-Resource-Group` - Resource group name (optional)

**Example Request:**
```bash
curl -X GET "https://api.example.com/operations/status?app_id=51151&status=in_progress&limit=10" \
  -H "X-User-Object-Id: 12345678-1234-1234-1234-123456789012" \
  -H "X-Storage-Account: mystorageaccount" \
  -H "Content-Type: application/json"
```

**Example Response:**
```json
{
  "operations": [
    {
      "operation_id": "op_12345",
      "app_id": "51151",
      "operation_type": "run_analysis",
      "status": "in_progress",
      "current_step": "Processing dependencies",
      "progress_percentage": 75,
      "total_steps": 5,
      "created_at": "2024-01-15T10:30:00Z",
      "updated_at": "2024-01-15T10:35:00Z",
      "estimated_completion": "2024-01-15T10:40:00Z"
    }
  ],
  "total_count": 1,
  "limit": 10,
  "offset": 0,
  "has_more": false
}
```

### Get Operation Summary

**GET** `/operations/summary`

Get statistical summary of operations.

**Query Parameters:**
- `app_id` (optional) - Filter by application ID
- `days` (optional) - Days to include (default: 7, max: 365)

**Headers (required when app_id provided):**
- `X-User-Object-Id` - User object ID for RBAC validation
- `X-Storage-Account` - Storage account name for RBAC validation
- `X-Resource-Group` - Resource group name (optional)

**Example Request:**
```bash
curl -X GET "https://api.example.com/operations/summary?app_id=51151&days=30" \
  -H "X-User-Object-Id: 12345678-1234-1234-1234-123456789012" \
  -H "X-Storage-Account: mystorageaccount" \
  -H "Content-Type: application/json"
```

**Example Response:**
```json
{
  "summary": {
    "total_operations": 25,
    "completed": 20,
    "failed": 2,
    "in_progress": 2,
    "pending": 1,
    "cancelled": 0,
    "success_rate": 80.0,
    "average_duration_seconds": 145.5
  },
  "by_type": {
    "create_application": {"count": 5, "success_rate": 100.0},
    "index_documents": {"count": 5, "success_rate": 80.0},
    "run_analysis": {"count": 5, "success_rate": 80.0},
    "generate_report": {"count": 5, "success_rate": 80.0},
    "generate_design": {"count": 5, "success_rate": 80.0}
  },
  "recent_operations": [
    {
      "operation_id": "op_12345",
      "operation_type": "run_analysis",
      "status": "completed",
      "created_at": "2024-01-15T10:30:00Z"
    }
  ],
  "period_days": 30,
  "app_id": "51151"
}
```

### Get Specific Operation

**GET** `/operations/{operation_id}/status`

Get detailed status of a specific operation.

**Path Parameters:**
- `operation_id` - Unique operation identifier

**Query Parameters:**
- `app_id` - Application ID (required for efficient lookup)

**Headers (always required):**
- `X-User-Object-Id` - User object ID for RBAC validation
- `X-Storage-Account` - Storage account name for RBAC validation
- `X-Resource-Group` - Resource group name (optional)

**Example Request:**
```bash
curl -X GET "https://api.example.com/operations/op_12345/status?app_id=51151" \
  -H "X-User-Object-Id: 12345678-1234-1234-1234-123456789012" \
  -H "X-Storage-Account: mystorageaccount" \
  -H "Content-Type: application/json"
```

### Cleanup Operations

**DELETE** `/operations/cleanup`

Clean up operations with flexible options.

**Query Parameters:**
- `confirm` - Must be true to perform deletion (required)
- `app_id` (optional) - Filter by application ID
- `all_for_app` (optional) - Delete ALL operations for app (default: false)
- `days` (optional) - Delete operations older than X days (default: 30, max: 365)

**Headers (required when app_id provided):**
- `X-User-Object-Id` - User object ID for RBAC validation
- `X-Storage-Account` - Storage account name for RBAC validation
- `X-Resource-Group` - Resource group name (optional)

**Examples:**

Delete ALL operations for a specific app:
```bash
curl -X DELETE "https://api.example.com/operations/cleanup?confirm=true&app_id=51151&all_for_app=true" \
  -H "X-User-Object-Id: 12345678-1234-1234-1234-123456789012" \
  -H "X-Storage-Account: mystorageaccount" \
  -H "Content-Type: application/json"
```

Delete old operations for a specific app:
```bash
curl -X DELETE "https://api.example.com/operations/cleanup?confirm=true&app_id=51151&days=30" \
  -H "X-User-Object-Id: 12345678-1234-1234-1234-123456789012" \
  -H "X-Storage-Account: mystorageaccount" \
  -H "Content-Type: application/json"
```

Delete old operations globally (no app filter):
```bash
curl -X DELETE "https://api.example.com/operations/cleanup?confirm=true&days=60" \
  -H "Content-Type: application/json"
```

## Operation States

### Status Values
- `pending`: Operation has been created but not started
- `in_progress`: Operation is currently running
- `completed`: Operation finished successfully
- `failed`: Operation encountered an error
- `cancelled`: Operation was cancelled (not currently implemented)

### Operation Types
- `create_application`: Application creation and RBAC setup
- `index_documents`: Document indexing operations
- `run_analysis`: Analysis pipeline execution
- `generate_report`: Assessment report generation
- `generate_design`: Design generation pipeline
- `kubernetes_discovery`: Kubernetes discovery workflow
- `architecture_analysis`: Architecture/security analysis workflow
- `code_analysis`: Code analysis workflow
- `delete_app_data`: Application data deletion and cleanup

## RBAC Requirements

### When RBAC Headers are Required

- **Always Required**: `/operations/{operation_id}/status`
- **Required when app_id provided**: `/operations/status`, `/operations/summary`, `/operations/cleanup`
- **Not required**: Global queries without app_id filter

### RBAC Validation Process

When RBAC headers are provided, the system:

1. Validates user has access to the specified storage account
2. Verifies container exists and user has permissions
3. Checks table permissions if applicable
4. Only returns operations the user has access to

### Error Responses

Missing required headers:
```json
{
  "detail": "X-User-Object-Id header is required when filtering by app_id"
}
```

Access denied:
```json
{
  "detail": "User does not have access to application 51151"
}
```

## Error Handling

All operation tracking endpoints include comprehensive error handling:

- **400 Bad Request**: Invalid parameters, missing required headers
- **404 Not Found**: Operation or application not found
- **500 Internal Server Error**: System errors during operation tracking

## Monitoring and Observability

### Tracing Integration

All operation tracking endpoints are integrated with OpenTelemetry tracing:

- Automatic span creation for each endpoint
- Request/response size tracking
- Latency measurements
- Error tracking and categorization

### Logging

Comprehensive logging includes:

- Operation lifecycle events
- RBAC validation results
- Performance metrics
- Error details with context

### Example Log Entries

```
INFO - API Call: GET /operations/status | Status: 200 | Latency: 45.23ms | App: 51151
INFO - Retrieved 5 operations, total: 25
INFO - User validated for operation status query: exist_with_permissions
```

## Best Practices

### For API Consumers

1. **Always include RBAC headers** when querying app-specific operations
2. **Use pagination** for large result sets (limit parameter)
3. **Poll operation status** periodically for long-running operations
4. **Handle errors gracefully** with appropriate retry logic
5. **Clean up old operations** regularly to maintain performance

### For System Administrators

1. **Monitor operation success rates** using the summary endpoint
2. **Set up alerts** for failed operations
3. **Regular cleanup** of old operations (30+ days)
4. **Review RBAC logs** for access patterns
5. **Monitor API latency** and operation durations

## Data Retention

- Operations are retained indefinitely by default
- Use cleanup endpoints to manage retention
- Recommended cleanup: 30-90 days for most use cases
- Critical operations may need longer retention

## Integration Examples

### curl Script for Monitoring

```bash
#!/bin/bash

# Monitor operations for a specific app
USER_ID="12345678-1234-1234-1234-123456789012"
STORAGE_ACCOUNT="mystorageaccount"
API_BASE="https://api.example.com"

# Get in-progress operations
echo "Checking in-progress operations..."
curl -X GET "${API_BASE}/operations/status?app_id=51151&status=in_progress" \
  -H "X-User-Object-Id: ${USER_ID}" \
  -H "X-Storage-Account: ${STORAGE_ACCOUNT}" \
  -H "Content-Type: application/json" \
  | jq '.operations | length' \
  | xargs -I {} echo "Found {} in-progress operations"

# Get operation summary
echo "Getting operation summary..."
curl -X GET "${API_BASE}/operations/summary?app_id=51151&days=7" \
  -H "X-User-Object-Id: ${USER_ID}" \
  -H "X-Storage-Account: ${STORAGE_ACCOUNT}" \
  -H "Content-Type: application/json" \
  | jq '.summary.success_rate' \
  | xargs -I {} echo "Success rate: {}%"
```

### curl Script for Cleanup

```bash
#!/bin/bash

# Cleanup old operations
API_BASE="https://api.example.com"

echo "Cleaning up operations older than 60 days..."
response=$(curl -X DELETE "${API_BASE}/operations/cleanup?confirm=true&days=60" \
  -H "Content-Type: application/json" \
  -s)

deleted_count=$(echo "$response" | jq -r '.deleted_count')
echo "Deleted $deleted_count operations"

# Verify cleanup
echo "Verifying cleanup..."
curl -X GET "${API_BASE}/operations/summary?days=60" \
  -H "Content-Type: application/json" \
  | jq '.summary.total_operations' \
  | xargs -I {} echo "Remaining operations: {}"
```

## AI Agent Development Best Practices

### Operation Tracking for AI Agents

The operation tracking system is designed following AI agent development best practices:

#### 1. Stateful Operation Management
- Each AI agent operation maintains state throughout its lifecycle
- Progress is tracked at granular steps for complex multi-agent workflows
- Operations can be resumed or retried at specific checkpoints

#### 2. Multi-Agent Coordination
- Operations track coordination between multiple agents (orchestrator, responder, etc.)
- Cross-agent dependencies are monitored and logged
- Agent cleanup is tracked to prevent resource leaks

#### 3. Error Recovery and Resilience
- Operations include retry mechanisms for transient failures
- Agent-specific error categorization (model errors, infrastructure errors, etc.)
- Graceful degradation when individual agents fail

### Tracing Integration for AI Operations

Following AI application tracing best practices:

#### 1. Semantic Tracing
```python
# Each operation creates semantic spans
with tracer.start_as_current_span(f"AI_Operation_{operation_type}") as span:
    span.set_attribute("ai.operation.type", operation_type.value)
    span.set_attribute("ai.application.id", app_id)
    span.set_attribute("ai.agent.count", agent_count)
```

#### 2. Model Performance Tracking
- Token usage tracking across all AI model calls
- Model response quality metrics (confidence scores)
- Latency tracking for each model interaction

#### 3. Agent Lifecycle Events
```python
# Track agent creation, execution, and cleanup
span.add_event("agent_created", {
    "agent_type": "orchestrator",
    "agent_id": agent_id
})
span.add_event("agent_completed", {
    "success": True,
    "tokens_used": token_count
})
```

## Storage Requirements and Setup

### Azure Table Storage Configuration

The operation tracking system uses Azure Table Storage for persistent operation tracking:

- **Table name**: `OperationStatus`
- **Partition key**: `app_id` (for efficient querying by application)
- **Row key**: `operation_id` (unique operation identifier)

**Required Environment Variables**:
```bash
AZURE_TABLES_ACCOUNT_URL=https://yourstorageaccount.table.core.windows.net/
```

### Authentication Requirements

The system uses Azure Managed Identity for authentication to Table Storage. Ensure the application has:
- `Storage Table Data Contributor` role on the storage account

### Complete State Data Structure

Each operation record contains the following fields:

```python
{
    "operation_id": "uuid",           # Unique identifier
    "app_id": "string",              # Application ID
    "operation_type": "enum",        # Type of operation
    "status": "enum",                # Current status
    "user_object_id": "uuid",        # User who initiated
    "storage_account_name": "string", # Storage account
    "resource_group_name": "string", # Resource group (optional)
    
    # Timing information
    "timestamp_started": "datetime",
    "timestamp_updated": "datetime", 
    "timestamp_completed": "datetime",
    
    # Progress tracking
    "progress_percentage": "int",    # 0-100
    "current_step": "string",        # Human-readable step
    "total_steps": "int",           # Total number of steps
    "completed_steps": "int",       # Completed steps
    "steps": [                      # Detailed step information
        {
            "step_name": "string",
            "status": "enum",
            "started_at": "datetime",
            "completed_at": "datetime",
            "details": {},
            "error_message": "string"
        }
    ],
    
    # Results and metadata
    "result_data": {},              # Operation output
    "error_details": {},            # Error information
    "metadata": {},                 # Additional context
    "duration_seconds": "float"     # Total duration
}
```

## Implementation Guide

### Adding Operation Tracking to New Endpoints

There are three methods to add operation tracking to new endpoints:

#### Method 1: Decorator (Automatic - Recommended)

```python
from agents.operation_tracker import track_operation
from agents.operation_models import OperationType

@app.post("/myEndpoint")
@track_operation(OperationType.MY_OPERATION, total_steps=3)
async def my_endpoint(request: ApplicationOperationRequest):
    # Your endpoint implementation
    # Operation tracking is automatic
    return response
```

#### Method 2: Manual Context Manager

```python
from agents.operation_tracker import OperationTracker
from agents.operation_models import OperationType

@app.post("/myEndpoint")
async def my_endpoint(request: ApplicationOperationRequest):
    async with OperationTracker(request, OperationType.MY_OPERATION, 5) as tracker:
        await tracker.update_progress("Step 1: Validation", 20)
        # Do validation work
        
        await tracker.update_progress("Step 2: Processing", 60)
        # Do processing work
        
        await tracker.update_progress("Step 3: Finalization", 90)
        # Do finalization work
        
        return response
```

#### Method 3: Manual Progress Updates

```python
from agents.operation_tracker import update_operation_progress

async def my_endpoint(request: ApplicationOperationRequest):
    # Create operation manually or get operation_id from decorator
    operation_id = "some-operation-id"
    
    await update_operation_progress(
        operation_id, 
        request.app_id, 
        "Processing data", 
        50, 
        OperationStatus.IN_PROGRESS
    )
    
    return response
```

## Health Monitoring and Alerting

### Health Monitoring Requirements

- Monitor the `/health` endpoint to ensure the service is running
- Check Table Storage connectivity and permissions
- Monitor operation failure rates and durations

### Recommended Alert Thresholds

1. **High failure rate**: Alert if failure rate > 10% over 1 hour
2. **Long-running operations**: Alert if operations run > 30 minutes
3. **Storage errors**: Alert on Table Storage connectivity issues
4. **Stuck operations**: Alert if operations remain in `in_progress` > 1 hour

### Performance Considerations

- Operations older than 30 days are automatically eligible for cleanup
- Use pagination for large result sets
- Filter by `app_id` for better query performance
- Consider indexing patterns for frequently queried fields

## Advanced Monitoring Scenarios

### Long-Running AI Workflows

For complex AI operations that may run for extended periods:

```bash
# Monitor a long-running analysis operation
curl -X GET "https://api.example.com/operations/status?operation_type=run_analysis&status=in_progress&limit=5" \
  -H "Content-Type: application/json"
```

Response includes estimated completion times based on historical data:
```json
{
  "operations": [
    {
      "operation_id": "op_analysis_12345",
      "estimated_completion": "2024-01-15T11:45:00Z",
      "ai_metrics": {
        "tokens_consumed": 15000,
        "model_calls": 45,
        "confidence_score": 0.87
      }
    }
  ]
}
```

### AI Model Performance Monitoring

Track AI model performance across operations:

```bash
# Get summary with AI-specific metrics
curl -X GET "https://api.example.com/operations/summary?days=7" \
  -H "Content-Type: application/json"
```

Enhanced response with AI metrics:
```json
{
  "summary": {
    "total_operations": 25,
    "ai_metrics": {
      "average_tokens_per_operation": 12500,
      "average_confidence_score": 0.85,
      "model_error_rate": 0.02
    }
  },
  "by_type": {
    "run_analysis": {
      "count": 10,
      "avg_tokens": 18000,
      "avg_confidence": 0.88,
      "avg_duration_seconds": 240
    }
  }
}
```

### Agent Resource Management

Monitor agent resource usage and cleanup:

```bash
#!/bin/bash

# Check for stuck or orphaned agents
API_BASE="https://api.example.com"
USER_ID="12345678-1234-1234-1234-123456789012"
STORAGE_ACCOUNT="mystorageaccount"

# Get long-running operations
response=$(curl -X GET "${API_BASE}/operations/status?status=in_progress&operation_type=run_analysis" \
  -H "X-User-Object-Id: ${USER_ID}" \
  -H "X-Storage-Account: ${STORAGE_ACCOUNT}" \
  -H "Content-Type: application/json" \
  -s)

# Check for operations running longer than 2 hours
echo "$response" | jq -r '.operations[] | select(.created_at < (now - 7200 | todate)) | .operation_id' | \
while read -r op_id; do
  echo "Alert: Operation $op_id has been running for more than 2 hours"
  # Add alerting logic here
done
```

## Troubleshooting Guide

### Common System Issues

#### 1. Configuration Issues

**"AZURE_TABLES_ACCOUNT_URL environment variable not set"**
- **Solution**: Ensure the environment variable is properly configured
- **Check**: Verify that the storage account URL is valid

**"Permission denied" errors**
- **Solution**: Verify the application has `Storage Table Data Contributor` role
- **Check**: Ensure that Managed Identity is properly configured

#### 2. Operation Tracking Issues

**Operations not appearing**
- **Solution**: Verify the decorator is applied correctly
- **Check**: Ensure that the operation service is initialized
- **Action**: Look for errors in application logs

**Slow query performance**
- **Solution**: Use `app_id` filter when possible
- **Optimization**: Limit result sets with `limit` parameter
- **Consider**: The age of operations being queried

#### 3. Debug Logging

Enable debug logging to see detailed operation tracking:

```python
import logging
logging.getLogger("agents.operation_service").setLevel(logging.DEBUG)
logging.getLogger("agents.operation_tracker").setLevel(logging.DEBUG)
```

### Common AI Operation Issues

#### 1. Model API Failures
- **Symptom**: Operations fail with "model error" in logs
- **Check**: 
```bash
curl -X GET "https://api.example.com/operations/status?status=failed&operation_type=run_analysis" \
  -H "Content-Type: application/json"
```
- **Action**: Review model endpoint health, check quota limits

#### 2. Agent Coordination Issues
- **Symptom**: Operations stuck at "Starting analysis" step
- **Check**: Look for agent communication failures in traces
- **Action**: Restart stuck operations, check agent service health

#### 3. Resource Exhaustion
- **Symptom**: Operations fail with timeout errors
- **Check**: Monitor token usage and operation duration
- **Action**: Implement backoff strategies, optimize prompts

### Diagnostic Commands

```bash
# Check for failed operations in last 24 hours
curl -X GET "https://api.example.com/operations/status?status=failed&days=1" \
  -H "Content-Type: application/json"

# Monitor current resource usage
curl -X GET "https://api.example.com/operations/summary?days=1" \
  -H "Content-Type: application/json"

# Clean up failed operations older than 7 days
curl -X DELETE "https://api.example.com/operations/cleanup?confirm=true&status=failed&days=7" \
  -H "Content-Type: application/json"
```

## Performance Optimization

### Operation Batching

For high-throughput scenarios, consider batching operations:

```bash
#!/bin/bash

# Monitor operation queue depth
queue_depth=$(curl -X GET "https://api.example.com/operations/status?status=pending" \
  -H "Content-Type: application/json" \
  -s | jq '.total_count')

if [ "$queue_depth" -gt 10 ]; then
  echo "Queue depth ($queue_depth) exceeds threshold - implementing backpressure"
  # Add backpressure logic here
fi
```

### Predictive Scaling

Use operation history to predict resource needs:

```bash
#!/bin/bash

# Analyze historical patterns for predictive scaling
API_BASE="https://api.example.com"

summary=$(curl -X GET "${API_BASE}/operations/summary?days=30" \
  -H "Content-Type: application/json" \
  -s)

avg_duration=$(echo "$summary" | jq '.summary.average_duration_seconds')
total_ops=$(echo "$summary" | jq '.summary.total_operations')

echo "Average operation duration: ${avg_duration}s"
echo "Monthly operation volume: ${total_ops}"
# Use data for capacity planning
```
