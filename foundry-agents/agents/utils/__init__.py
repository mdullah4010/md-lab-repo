"""
Agent Utilities Package

This package provides centralized utility functions to reduce code duplication
across the agent codebase. It is organized into two main modules:

1. common_utils - Common-purpose utilities:
   - File upload/download operations
   - JSON to Markdown conversions
   - Name sanitization (tables, indexes)
   - Azure Storage client creation
   - Instructions file loading

2. agent_utils - Agent lifecycle utilities:
   - Agent discovery (find existing agents)
   - Agent cleanup and deletion
   - AI Project client management
   - Search tool configuration
   - Run processing with retry

"""

# =============================================================================
# COMMON UTILITIES
# =============================================================================

from agents.utils.common_utils import (
    # Data classes
    IndexValidationResult,
    
    # Storage URL utilities
    get_storage_account_url,
    
    # Index validation utilities
    validate_index,
    
    # Client factory functions
    get_table_service_client,
    get_blob_service_client,
    get_async_blob_service_client,
    
    # Name sanitization
    sanitize_table_name,
    sanitize_index_name,
    
    # File upload/download
    upload_file_to_container,
    upload_file_to_container_async,
    upload_content_to_container,
    download_template_from_storage,
    
    # JSON/Markdown conversion
    responses_json_to_markdown,
    create_response_file,
    
    # Prompt processing
    process_prompts_from_json,
    
    # Instructions utilities
    load_instructions_from_file,
    
    # Virtual directory utilities
    ENDPOINT_VIRTUAL_DIRECTORIES,
    get_all_virtual_directories,
    setup_virtual_directories_for_app,
)

# =============================================================================
# AGENT UTILITIES
# =============================================================================

from agents.utils.agent_utils import (
    # Configuration dataclasses
    AgentConfig,
    SearchToolConfig,
    AgentCreationResult,
    
    # Client management
    AgentClientManager,
    
    # Agent discovery
    find_existing_agent,
    build_agent_name,
    
    # Agent cleanup
    cleanup_agent,
    
    # Search configuration
    get_search_connection,
    create_project_index,
    configure_search_tool,
    
    # Agent creation utilities
    create_or_update_agent,
    create_agent_with_search_tool,
    
    # Search index utilities
    check_index_exists,
    check_semantic_config,
    check_vector_fields,
    determine_optimal_query_type,
    
    # Enhanced run execution with retry
    RunResult,
    extract_json_from_text,
    execute_run_with_retry,
)

# =============================================================================
# VERSION AND METADATA
# =============================================================================

__version__ = "1.0.0"
__all__ = [
    # General utilities - Data classes
    "IndexValidationResult",
    
    # General utilities - Index validation
    "validate_index",
    
    # General utilities - Storage
    "get_storage_account_url",
    "get_table_service_client",
    "get_blob_service_client",
    "get_async_blob_service_client",
    "sanitize_table_name",
    "sanitize_index_name",
    "upload_file_to_container",
    "upload_file_to_container_async",
    "upload_content_to_container",
    "download_template_from_storage",
    "responses_json_to_markdown",
    "create_response_file",
    "process_prompts_from_json",
    "load_instructions_from_file",
    "get_instructions_file_path",
    
    # Agent utilities - Configuration
    "AgentConfig",
    "SearchToolConfig",
    "AgentCreationResult",
    
    # Agent utilities - Client management
    "AgentClientManager",
    
    # Agent utilities - Discovery and cleanup
    "find_existing_agent",
    "build_agent_name",
    "cleanup_agent",
    
    # Agent utilities - Search configuration
    "get_search_connection",
    "create_project_index",
    "configure_search_tool",
    
    # Agent utilities - Agent creation
    "create_or_update_agent",
    "create_agent_with_search_tool",
    
    # Agent utilities - Search index utilities
    "check_index_exists",
    "check_semantic_config",
    "check_vector_fields",
    "determine_optimal_query_type",
    
    # Agent utilities - Run execution
    "RunResult",
    "extract_json_from_text",
    "execute_run_with_retry",
]
