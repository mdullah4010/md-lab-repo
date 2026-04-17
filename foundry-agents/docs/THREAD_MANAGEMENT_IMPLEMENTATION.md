# Thread Management Implementation

## Overview
This document describes the implementation of deterministic thread ID management and cleanup for Azure AI Agents in the architecture analyzer.

## Problem Statement
Previously, when agents were invoked with `thread=None`, the Azure AI SDK would create threads internally without tracking them. This resulted in:
- Thread resource leaks in Azure AI Foundry
- No way to clean up threads when agents were deleted
- Orphaned threads consuming resources

## Solution
Implemented a centralized thread registry that:
1. **Tracks threads by app_id**: All threads for a given application are registered in a class-level registry
2. **Creates threads explicitly**: Instead of `thread=None`, threads are created explicitly before agent invocation
3. **Bulk cleanup**: When an agent is deleted, all registered threads for that app_id are deleted

## Implementation Details

### 1. Thread Registry (agent_factory.py)

Added class-level thread registry to `AgentFactory`:

```python
class AgentFactory:
    # Class-level thread registry to track threads by app_id
    _thread_registry: Dict[str, List[str]] = {}
    
    @classmethod
    def register_thread(cls, app_id: str, thread_id: str) -> None:
        """Register a thread ID for an app_id for later cleanup."""
        if app_id not in cls._thread_registry:
            cls._thread_registry[app_id] = []
        if thread_id not in cls._thread_registry[app_id]:
            cls._thread_registry[app_id].append(thread_id)
    
    @classmethod
    def get_threads_for_app(cls, app_id: str) -> List[str]:
        """Get all registered thread IDs for an app_id."""
        return cls._thread_registry.get(app_id, [])
    
    @classmethod
    def clear_threads_for_app(cls, app_id: str) -> None:
        """Clear thread registry for an app_id."""
        if app_id in cls._thread_registry:
            del cls._thread_registry[app_id]
```

### 2. Thread Creation Pattern

All locations where agents are invoked now follow this pattern:

```python
# Create a dedicated thread for this agent execution
thread = await agent.client.agents.threads.create()
AgentFactory.register_thread(app_id, thread.id)
logger.info(f"Created and registered thread {thread.id} for app {app_id}")

# Invoke agent with the dedicated thread
async for response_item in agent.invoke(
    messages=user_message,
    thread=thread.id,  # Explicit thread instead of thread=None
    temperature=0.1,
    max_completion_tokens=16384,
    max_prompt_tokens=100000
):
    # Process response...
```

### 3. Bulk Thread Cleanup

Updated cleanup methods to delete all registered threads:

```python
@staticmethod
async def cleanup_security_agent(
    app_id: str,
    agent: Optional[AzureAIAgent] = None,
    agent_id: Optional[str] = None,
    thread_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Cleanup security agent and all associated threads.
    Now deletes ALL registered threads for the app_id, not just the specified one.
    """
    # ... agent deletion logic ...
    
    # Delete all registered threads for this app
    threads_deleted = 0
    if client:
        # Get all threads registered for this app_id
        registered_threads = AgentFactory.get_threads_for_app(app_id)
        
        # If specific thread_id provided, also include it
        threads_to_delete = set(registered_threads)
        if thread_id:
            threads_to_delete.add(thread_id)
        
        if threads_to_delete:
            logger.info(f"Deleting {len(threads_to_delete)} thread(s) for security agent {agent_id}")
            for tid in threads_to_delete:
                try:
                    await client.agents.threads.delete(thread_id=tid)
                    threads_deleted += 1
                except Exception as threads_ex:
                    logger.warning(f"Error deleting thread {tid}: {str(threads_ex)}")
            
            # Clear the registry for this app
            AgentFactory.clear_threads_for_app(app_id)
    
    return {
        "status": "success",
        "threads_deleted": threads_deleted,
        # ... other fields ...
    }
```

## Modified Files

### 1. agent_factory.py
- **Added**: Class-level `_thread_registry` dictionary
- **Added**: `register_thread()` class method
- **Added**: `get_threads_for_app()` class method
- **Added**: `clear_threads_for_app()` class method
- **Updated**: `cleanup_security_agent()` to delete all registered threads
- **Updated**: `cleanup_diagram_agent()` to delete all registered threads

### 2. architecture_analyzer_agent.py
- **Updated**: `run_architecture_analysis()` to create and register threads explicitly
- Thread creation uses `operation.app_id` if available, otherwise "unknown"

### 3. security_analyzer.py
- **Updated**: `analyze_components()` signature to accept optional `app_id` parameter
- **Updated**: Thread creation to register with app_id

### 4. foundry_image_analyzer.py
- **Updated**: `_analyze_architecture_diagram_with_foundry_async()` signature to accept optional `app_id` parameter
- **Updated**: Thread creation to register with app_id

### 5. foundry_image_analyzer.py (Design Document Extractor)
- **Updated**: Passes `app_id` to diagram analysis function

## Thread Lifecycle

```
1. Agent Created
   ↓
2. Thread Created Explicitly
   └─> thread = await agent.client.agents.threads.create()
   ↓
3. Thread Registered
   └─> AgentFactory.register_thread(app_id, thread.id)
   ↓
4. Agent Invoked with Thread
   └─> agent.invoke(messages=msg, thread=thread.id)
   ↓
5. Agent Analysis Completes
   ↓
6. Cleanup Triggered
   └─> cleanup_security_agent(app_id, agent)
   ↓
7. All Threads Retrieved
   └─> threads = AgentFactory.get_threads_for_app(app_id)
   ↓
8. All Threads Deleted
   └─> for tid in threads: client.agents.threads.delete(thread_id=tid)
   ↓
9. Registry Cleared
   └─> AgentFactory.clear_threads_for_app(app_id)
```

## Benefits

1. **No Thread Leaks**: All threads are tracked and deleted when agents are cleaned up
2. **Deterministic Cleanup**: Threads are explicitly managed rather than left to SDK internals
3. **Better Observability**: Thread creation and deletion are logged
4. **Resource Efficiency**: Prevents accumulation of orphaned threads in Azure AI Foundry
5. **Centralized Management**: Thread registry provides single source of truth

## Usage Example

```python
# Create agent
agent_factory = AgentFactory()
security_agent = await agent_factory.create_security_analysis_agent(app_id="myapp-001")

# Use analyzer (threads automatically registered)
analyzer = SecurityAnalyzer(agent=security_agent)
findings = await analyzer.analyze_components(
    components=["Azure App Service", "Azure SQL"],
    architecture_name="MyApp",
    app_id="myapp-001"  # Thread registered with this app_id
)

# Cleanup (deletes all threads for myapp-001)
cleanup_result = await AgentFactory.cleanup_security_agent(
    app_id="myapp-001",
    agent=security_agent
)

print(f"Deleted {cleanup_result['threads_deleted']} threads")
```

## Testing Considerations

When testing, verify:
1. Threads are created before agent invocation
2. Thread IDs are registered in the registry
3. Multiple threads for the same app_id are all registered
4. Cleanup deletes all threads for an app_id
5. Registry is cleared after cleanup
6. Cleanup handles missing threads gracefully (logs warning but continues)

## Migration Notes

### Before (Old Pattern)
```python
async for response_item in agent.invoke(
    messages=user_message,
    thread=None,  # SDK creates internal thread
    temperature=0.1
):
    # Process...
```

### After (New Pattern)
```python
thread = await agent.client.agents.threads.create()
AgentFactory.register_thread(app_id, thread.id)

async for response_item in agent.invoke(
    messages=user_message,
    thread=thread.id,  # Explicit thread
    temperature=0.1
):
    # Process...
```

## Future Enhancements

Possible improvements:
1. **Thread Metadata**: Store thread creation timestamp and purpose
2. **Auto-Expiration**: Automatically delete threads older than X hours
3. **Thread Pooling**: Reuse threads across multiple agent invocations
4. **Metrics**: Track thread creation/deletion rates
5. **Health Checks**: Periodic validation that threads still exist in Azure
