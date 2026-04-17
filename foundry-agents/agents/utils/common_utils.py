"""
Common Utility Functions for Agent Codebase

This module consolidates common utility functions used across multiple agents:
- File upload/download operations
- JSON to Markdown conversions
- Name sanitization (tables, indexes)
- Azure Storage client creation

These utilities reduce code duplication and improve maintainability.

Usage:
    from agents.utils.common_utils import (
        upload_file_to_container,
        responses_json_to_markdown,
        sanitize_table_name,
        sanitize_index_name,
        get_storage_account_url,
        get_table_service_client,
        get_blob_service_client,
        download_template_from_storage
    )
"""

import os
import re
import json
import hashlib
from typing import Optional, List, Tuple, Dict, Any
from datetime import datetime
from dataclasses import dataclass

from azure.identity import DefaultAzureCredential as SyncDefaultAzureCredential
from azure.core.exceptions import ResourceNotFoundError, HttpResponseError
from azure.storage.blob import BlobServiceClient, ContentSettings
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

# Import logging configuration
from agents.logging_config import get_logger

logger = get_logger(__name__)


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class IndexValidationResult:
    """Result of an Azure AI Search index validation check."""
    is_valid: bool
    index_name: str
    document_count: int
    error_message: Optional[str] = None


# =============================================================================
# INDEX VALIDATION UTILITIES
# =============================================================================

def validate_index(
    index_name: str,
    search_endpoint: str = None,
    require_documents: bool = False,
    index_display_name: str = "search"
) -> IndexValidationResult:
    """
    Validate that an Azure AI Search index exists and is accessible.
    
    This is a consolidated utility that can validate any Azure AI Search index,
    replacing the separate validate_scf_index and validate_code_search_index functions.
    
    Uses managed identity (DefaultAzureCredential) for authentication.
    
    Args:
        index_name: Name of the index to validate
        search_endpoint: Azure AI Search endpoint URL (uses AZURE_SEARCH_ENDPOINT env var if not provided)
        require_documents: If True, validation fails if index has no documents (default: False)
        index_display_name: Display name for error messages (e.g., "SCF", "code search")
    
    Returns:
        IndexValidationResult with validation status and document count
    
    Example:
        # Validate SCF index requiring documents
        result = validate_index(
            index_name="scf-index",
            require_documents=True,
            index_display_name="SCF"
        )
        
        # Validate any index
        result = validate_index(index_name="my-index")
    """
    endpoint = search_endpoint or os.getenv("AZURE_SEARCH_ENDPOINT")
    
    if not endpoint:
        logger.error("❌ AZURE_SEARCH_ENDPOINT environment variable is not set")
        return IndexValidationResult(
            is_valid=False,
            index_name=index_name or "",
            document_count=0,
            error_message="AZURE_SEARCH_ENDPOINT environment variable is required"
        )
    
    if not index_name:
        logger.error(f"❌ {index_display_name} index name is not provided")
        return IndexValidationResult(
            is_valid=False,
            index_name="",
            document_count=0,
            error_message=f"{index_display_name} index name is required"
        )
    
    logger.info(f"🔍 Validating {index_display_name} index: {index_name}")
    logger.info(f"   Endpoint: {endpoint}")
    
    try:
        # Use managed identity for authentication
        credential = SyncDefaultAzureCredential(exclude_shared_token_cache_credential=True)
        
        # Check if index exists
        index_client = SearchIndexClient(endpoint=endpoint, credential=credential)
        try:
            index_client.get_index(index_name)
            logger.info(f"✅ {index_display_name} index '{index_name}' found")
        except ResourceNotFoundError:
            logger.error(f"❌ {index_display_name} index '{index_name}' does not exist")
            return IndexValidationResult(
                is_valid=False,
                index_name=index_name,
                document_count=0,
                error_message=f"{index_display_name} index '{index_name}' does not exist at endpoint '{endpoint}'. Please create and configure the index."
            )
        
        # Get document count
        search_client = SearchClient(endpoint=endpoint, index_name=index_name, credential=credential)
        results = search_client.search(
            search_text="*",
            top=1,
            include_total_count=True
        )
        
        doc_count = results.get_count() or 0
        
        # Check if documents are required
        if require_documents and doc_count == 0:
            logger.error(f"❌ {index_display_name} index '{index_name}' exists but has no documents")
            return IndexValidationResult(
                is_valid=False,
                index_name=index_name,
                document_count=0,
                error_message=f"{index_display_name} index '{index_name}' exists but contains no indexed documents. Please populate the index before proceeding."
            )
        
        logger.info(f"✅ {index_display_name} index '{index_name}' validated with {doc_count} documents")
        return IndexValidationResult(
            is_valid=True,
            index_name=index_name,
            document_count=doc_count
        )
        
    except HttpResponseError as e:
        logger.error(f"❌ Failed to validate {index_display_name} index: {e}")
        return IndexValidationResult(
            is_valid=False,
            index_name=index_name,
            document_count=0,
            error_message=f"Failed to access {index_display_name} index '{index_name}' at endpoint '{endpoint}': {str(e)}"
        )
    except Exception as e:
        logger.error(f"❌ Unexpected error validating {index_display_name} index: {e}")
        return IndexValidationResult(
            is_valid=False,
            index_name=index_name,
            document_count=0,
            error_message=f"Unexpected error validating {index_display_name} index: {str(e)}"
        )


# =============================================================================
# STORAGE URL UTILITIES
# =============================================================================

def get_storage_account_url() -> Optional[str]:
    """
    Get the Azure Storage account URL from environment variables.
    
    Tries multiple environment variable names for compatibility.
    
    Returns:
        Storage account URL if found, None otherwise
    """
    account_url = (
        os.getenv("AZURE_BLOB_ACCOUNT_URL") or 
        os.getenv("AZURE_STORAGE_ACCOUNT_URL") or 
        os.getenv("AZURE_TABLES_ACCOUNT_URL") or 
        os.getenv("AZURE_TABLE_ACCOUNT_URL")
    )
    if account_url:
        account_url = account_url.strip()
    
    account_name = os.getenv("AZURE_STORAGE_ACCOUNT_NAME")
    if not account_url and account_name:
        account_url = f"https://{account_name}.blob.core.windows.net"
    
    return account_url


def get_tables_account_url() -> Optional[str]:
    """
    Get the Azure Tables account URL from environment variables.
    
    Returns:
        Tables account URL if found, None otherwise
    """
    tables_url = os.getenv("AZURE_TABLES_ACCOUNT_URL")
    if tables_url:
        return tables_url.strip()
    
    # Fallback: derive from storage account name
    account_name = os.getenv("AZURE_STORAGE_ACCOUNT_NAME")
    if account_name:
        return f"https://{account_name}.table.core.windows.net"
    
    return None


# =============================================================================
# CLIENT FACTORY FUNCTIONS
# =============================================================================

def get_table_service_client(tables_url: Optional[str] = None):
    """
    Create TableServiceClient with automatic fallback to storage account key if available.
    
    Args:
        tables_url: Azure Tables endpoint URL (uses env var if not provided)
        
    Returns:
        TableServiceClient instance
    """
    from azure.data.tables import TableServiceClient
    
    if not tables_url:
        tables_url = get_tables_account_url()
    
    if not tables_url:
        raise ValueError("Azure Tables URL not configured. Set AZURE_TABLES_ACCOUNT_URL")
    
    # Check for storage account key first
    storage_account_key = os.getenv("AZURE_STORAGE_ACCOUNT_KEY")
    if storage_account_key:
        logger.debug("🔑 Using storage account key for TableServiceClient authentication")
        # Extract account name from URL
        account_name = tables_url.split("//")[1].split(".")[0]
        connection_string = (
            f"DefaultEndpointsProtocol=https;"
            f"AccountName={account_name};"
            f"AccountKey={storage_account_key};"
            f"EndpointSuffix=core.windows.net"
        )
        return TableServiceClient.from_connection_string(connection_string)
    else:
        logger.debug("🔐 Using DefaultAzureCredential for TableServiceClient authentication")
        cred = SyncDefaultAzureCredential(exclude_shared_token_cache_credential=True)
        return TableServiceClient(endpoint=tables_url, credential=cred)


def get_blob_service_client(account_url: Optional[str] = None) -> BlobServiceClient:
    """
    Create BlobServiceClient with automatic fallback to storage account key if available.
    
    Args:
        account_url: Azure Blob Storage account URL (uses env var if not provided)
        
    Returns:
        BlobServiceClient instance
    """
    if not account_url:
        account_url = get_storage_account_url()
    
    if not account_url:
        raise ValueError("Azure Storage URL not configured. Set AZURE_BLOB_ACCOUNT_URL")
    
    # Check for storage account key first
    storage_account_key = os.getenv("AZURE_STORAGE_ACCOUNT_KEY")
    if storage_account_key:
        logger.debug("🔑 Using storage account key for BlobServiceClient authentication")
        # Extract account name from URL
        account_name = account_url.split("//")[1].split(".")[0]
        connection_string = (
            f"DefaultEndpointsProtocol=https;"
            f"AccountName={account_name};"
            f"AccountKey={storage_account_key};"
            f"EndpointSuffix=core.windows.net"
        )
        return BlobServiceClient.from_connection_string(connection_string)
    else:
        logger.debug("🔐 Using DefaultAzureCredential for BlobServiceClient authentication")
        cred = SyncDefaultAzureCredential(exclude_shared_token_cache_credential=True)
        return BlobServiceClient(account_url=account_url, credential=cred)


async def get_async_blob_service_client(account_url: Optional[str] = None):
    """
    Create async BlobServiceClient.
    
    Args:
        account_url: Azure Blob Storage account URL (uses env var if not provided)
        
    Returns:
        Async BlobServiceClient instance
    """
    from azure.storage.blob.aio import BlobServiceClient as AsyncBlobServiceClient
    from azure.identity.aio import DefaultAzureCredential as AsyncDefaultAzureCredential
    
    if not account_url:
        account_url = get_storage_account_url()
    
    if not account_url:
        raise ValueError("Azure Storage URL not configured")
    
    credential = AsyncDefaultAzureCredential(exclude_shared_token_cache_credential=True)
    return AsyncBlobServiceClient(account_url=account_url, credential=credential)


# =============================================================================
# NAME SANITIZATION UTILITIES
# =============================================================================

def sanitize_table_name(name: str) -> str:
    """
    Sanitize table name for Azure Table Storage compliance.
    
    Azure Table Storage naming rules:
    - Must be alphanumeric (letters and numbers only)
    - Must start with a letter
    - Between 3 and 63 characters
    - No hyphens, underscores, or special characters
    
    Args:
        name: Raw table name that may contain invalid characters
    
    Returns:
        Sanitized table name with invalid characters removed
    """
    # Remove all non-alphanumeric characters
    sanitized = re.sub(r'[^a-zA-Z0-9]', '', name)
    
    # Ensure it starts with a letter
    if sanitized and not sanitized[0].isalpha():
        sanitized = 'T' + sanitized
    
    # Ensure minimum length
    if len(sanitized) < 3:
        sanitized = sanitized + 'Table'
    
    # Ensure maximum length
    if len(sanitized) > 63:
        sanitized = sanitized[:63]
    
    return sanitized


def sanitize_index_name(raw: str) -> str:
    """
    Sanitize arbitrary application/agent name into a valid Azure AI Search index name.

    Rules enforced:
    - Lowercase only
    - Allowed chars: a-z, 0-9, -
    - Collapse multiple dashes
    - Trim leading/trailing dashes
    - Must start with alphanumeric (fallback to hash-based prefix if not)
    - Length 2..128 (truncate or pad as needed)
    
    Args:
        raw: Raw name to sanitize
    
    Returns:
        Sanitized index name
    """
    try:
        s = (raw or "").lower().strip()
        s = re.sub(r"[^a-z0-9-]", "-", s)
        s = re.sub(r"-+", "-", s).strip('-')
        if not s or not s[0].isalnum():
            s = f"app-{hashlib.sha1((raw or 'x').encode()).hexdigest()[:8]}"
        if len(s) < 2:
            s = (s + "ix")[:2]
        if len(s) > 128:
            s = s[:128]
        return s
    except Exception as ex:
        # Record exception in span if available
        current_span = trace.get_current_span()
        if current_span and current_span.is_recording():
            current_span.record_exception(ex)
            current_span.set_status(Status(StatusCode.ERROR, str(ex)))
            current_span.add_event("index_name_sanitization_failed", {"raw_name": raw})
        logger.error(f"Failed to sanitize index name '{raw}': {ex}")
        # Fallback to a safe default
        return f"app-{hashlib.sha1((raw or 'fallback').encode()).hexdigest()[:8]}"


# =============================================================================
# FILE UPLOAD/DOWNLOAD UTILITIES
# =============================================================================

async def list_files_in_folder_async(
    app_id: str,
    folder_prefix: str,
    file_extensions: Optional[List[str]] = None,
    filename_prefix: Optional[str] = None,
    exclude_placeholder: bool = True
) -> List[Dict[str, Any]]:
    """
    Async version: List files in a blob storage folder for a given application.
    
    Args:
        app_id: Application ID (container name)
        folder_prefix: Folder prefix to list (e.g., 'architecture-analyzer/input')
        file_extensions: Optional list of file extensions to filter (e.g., ['.md', '.docx', '.pdf'])
        filename_prefix: Optional filename prefix filter (e.g., 'design-' to match 'design-doc.md')
        exclude_placeholder: Whether to exclude .gitkeep placeholder files
    
    Returns:
        List of dictionaries with file info: {'name': str, 'path': str, 'size': int, 'url': str}
    """
    try:
        from azure.storage.blob.aio import BlobServiceClient as AsyncBlobServiceClient
        from azure.identity.aio import DefaultAzureCredential as AsyncDefaultAzureCredential
        
        storage_account_url = os.getenv("AZURE_STORAGE_ACCOUNT_URL")
        if not storage_account_url:
            logger.error("AZURE_STORAGE_ACCOUNT_URL not set")
            return []
        
        # Normalize folder prefix
        folder_prefix = folder_prefix.rstrip('/') + '/'
        
        async with AsyncDefaultAzureCredential() as credential:
            async with AsyncBlobServiceClient(storage_account_url, credential=credential) as blob_service_client:
                container_client = blob_service_client.get_container_client(app_id)
                
                files = []
                async for blob in container_client.list_blobs(name_starts_with=folder_prefix):
                    blob_name = blob.name
                    filename = blob_name.split('/')[-1]
                    
                    # Skip placeholder files
                    if exclude_placeholder and blob_name.endswith('.gitkeep'):
                        continue
                    
                    # Apply filename prefix filter if specified
                    if filename_prefix:
                        if not filename.lower().startswith(filename_prefix.lower()):
                            continue
                    
                    # Apply file extension filter if specified
                    if file_extensions:
                        blob_lower = blob_name.lower()
                        if not any(blob_lower.endswith(ext.lower()) for ext in file_extensions):
                            continue
                    
                    # Build full URL
                    blob_url = f"{storage_account_url.rstrip('/')}/{app_id}/{blob_name}"
                    
                    files.append({
                        'name': filename,
                        'path': blob_name,
                        'size': blob.size,
                        'url': blob_url,
                        'last_modified': blob.last_modified.isoformat() if blob.last_modified else None
                    })
        
        logger.info(f"Found {len(files)} file(s) in {app_id}/{folder_prefix}" + (f" with prefix '{filename_prefix}'" if filename_prefix else ""))
        return files
        
    except Exception as ex:
        logger.error(f"Failed to list files in {app_id}/{folder_prefix}: {ex}")
        return []


def _get_versioned_blob_name(
    container_client,
    blob_name: str,
    folder_prefix: Optional[str] = None
) -> str:
    """
    Get a versioned blob name if the file already exists.
    
    Checks for existing blobs and increments version number (_v2, _v3, etc.)
    
    Args:
        container_client: Azure container client
        blob_name: Original blob name
        folder_prefix: Optional folder prefix for listing
    
    Returns:
        Versioned blob name (original or with _vN suffix)
    """
    import re
    
    try:
        # List existing blobs in the folder
        prefix_for_list = folder_prefix.rstrip('/') + '/' if folder_prefix else None
        existing_blobs = set()
        try:
            blobs = container_client.list_blobs(name_starts_with=prefix_for_list) if prefix_for_list else container_client.list_blobs()
            existing_blobs = {b.name for b in blobs}
        except Exception:
            pass
        
        # Check if blob exists
        if blob_name not in existing_blobs:
            return blob_name
        
        # Extract file parts
        # Handle folder prefix in blob_name
        if '/' in blob_name:
            folder_part = blob_name.rsplit('/', 1)[0] + '/'
            file_part = blob_name.rsplit('/', 1)[1]
        else:
            folder_part = ''
            file_part = blob_name
        
        # Split filename and extension
        if '.' in file_part:
            name_without_ext, ext = file_part.rsplit('.', 1)
            ext = '.' + ext
        else:
            name_without_ext = file_part
            ext = ''
        
        # Check if already versioned, extract base name
        version_match = re.match(r'^(.+?)_v(\d+)$', name_without_ext)
        if version_match:
            base_name = version_match.group(1)
            current_version = int(version_match.group(2))
        else:
            base_name = name_without_ext
            current_version = 1
        
        # Find next available version
        version = current_version + 1
        while True:
            new_name = f"{folder_part}{base_name}_v{version}{ext}"
            if new_name not in existing_blobs:
                logger.info(f"File version conflict detected, using versioned name: {new_name}")
                return new_name
            version += 1
            if version > 1000:  # Safety limit
                raise ValueError(f"Too many versions exist for blob: {blob_name}")
                
    except Exception as ex:
        logger.warning(f"Could not check for existing versions: {ex}, using original name")
        return blob_name


def upload_file_to_container(
    file_path: str, 
    app_id: str, 
    blob_name: Optional[str] = None,
    folder_prefix: Optional[str] = None,
    content_type: Optional[str] = None,
    enable_versioning: bool = True
) -> str:
    """
    Upload a file to the application's storage container with automatic versioning.
    
    Args:
        file_path: Path to the file to upload
        app_id: Application ID (used as container name)
        blob_name: Optional blob name (uses filename if not provided)
        folder_prefix: Optional folder prefix (e.g., 'asr/', 'design/', 'kubernetes/')
        content_type: Optional content type override
        enable_versioning: If True, auto-version files that already exist (default: True)
    
    Returns:
        Blob URL of the uploaded file
    """
    try:
        account_url = get_storage_account_url()
        if not account_url:
            raise ValueError("Azure Storage URL not configured")
        
        blob_service = get_blob_service_client(account_url)
        container_name = str(app_id).lower()
        
        if not blob_name:
            blob_name = os.path.basename(file_path)
        
        # Add folder prefix if specified and not already present
        if folder_prefix and not blob_name.startswith(folder_prefix):
            blob_name = f"{folder_prefix.rstrip('/')}/{blob_name}"
        
        # Create container if not exists
        container_client = blob_service.get_container_client(container_name)
        try:
            container_client.create_container()
        except Exception:
            pass  # Already exists
        
        # Apply versioning for markdown files (or all files if enabled)
        if enable_versioning and file_path.endswith('.md'):
            blob_name = _get_versioned_blob_name(container_client, blob_name, folder_prefix)
        
        # Auto-detect content type if not specified
        if not content_type:
            if file_path.endswith(".md"):
                content_type = "text/markdown"
            elif file_path.endswith(".json"):
                content_type = "application/json"
            elif file_path.endswith(".txt"):
                content_type = "text/plain"
        
        # Upload file
        with open(file_path, "rb") as data:
            container_client.upload_blob(
                name=blob_name,
                data=data,
                overwrite=True,
                content_settings=ContentSettings(content_type=content_type) if content_type else None
            )
        
        blob_url = f"{account_url.rstrip('/')}/{container_name}/{blob_name}"
        logger.info(f"Uploaded file to: {blob_url}")
        return blob_url
        
    except Exception as ex:
        current_span = trace.get_current_span()
        if current_span and current_span.is_recording():
            current_span.record_exception(ex)
            current_span.set_status(Status(StatusCode.ERROR, str(ex)))
            current_span.add_event("upload_file_to_container_failed", {
                "file_path": file_path,
                "app_id": app_id,
                "blob_name": blob_name or "auto"
            })
        logger.error(f"Failed to upload file to container: {ex}")
        raise


def upload_content_to_container(
    content: bytes,
    app_id: str,
    blob_name: str,
    folder_prefix: Optional[str] = None,
    content_type: Optional[str] = None,
    metadata: Optional[Dict[str, str]] = None
) -> str:
    """
    Upload in-memory content to the application's storage container.
    
    This is useful when you have content in memory (e.g., from StringIO/BytesIO)
    and don't want to write it to a temp file first.
    
    Args:
        content: Bytes content to upload
        app_id: Application ID (used as container name)
        blob_name: Name for the blob
        folder_prefix: Optional folder prefix (e.g., 'asr/input/', 'design/output/')
        content_type: Optional content type (auto-detected from blob_name if not provided)
        metadata: Optional dictionary of metadata key-value pairs to attach to the blob
    
    Returns:
        Blob URL of the uploaded content
    """
    try:
        account_url = get_storage_account_url()
        if not account_url:
            raise ValueError("Azure Storage URL not configured")
        
        blob_service = get_blob_service_client(account_url)
        container_name = str(app_id).lower()
        # Sanitize container name
        container_name = re.sub(r"[^a-z0-9-]", "-", container_name)
        
        # Add folder prefix if specified and not already present
        if folder_prefix and not blob_name.startswith(folder_prefix):
            blob_name = f"{folder_prefix.rstrip('/')}/{blob_name}"
        
        # Create container if not exists
        container_client = blob_service.get_container_client(container_name)
        try:
            container_client.create_container()
        except Exception:
            pass  # Already exists
        
        # Auto-detect content type if not specified
        if not content_type:
            if blob_name.endswith(".jsonl"):
                content_type = "application/x-ndjson"
            elif blob_name.endswith(".json"):
                content_type = "application/json"
            elif blob_name.endswith(".md"):
                content_type = "text/markdown"
            elif blob_name.endswith(".txt"):
                content_type = "text/plain"
        
        # Upload content with optional metadata
        container_client.upload_blob(
            name=blob_name,
            data=content,
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type) if content_type else None,
            metadata=metadata
        )
        
        blob_url = f"{account_url.rstrip('/')}/{container_name}/{blob_name}"
        logger.info(f"Uploaded content to: {blob_url}" + (f" with metadata: {metadata}" if metadata else ""))
        return blob_url
        
    except Exception as ex:
        current_span = trace.get_current_span()
        if current_span and current_span.is_recording():
            current_span.record_exception(ex)
            current_span.set_status(Status(StatusCode.ERROR, str(ex)))
            current_span.add_event("upload_content_to_container_failed", {
                "app_id": app_id,
                "blob_name": blob_name
            })
        logger.error(f"Failed to upload content to container: {ex}")
        raise


async def _get_versioned_blob_name_async(
    container_client,
    blob_name: str,
    folder_prefix: Optional[str] = None
) -> str:
    """
    Async version: Get a versioned blob name if the file already exists.
    
    Checks for existing blobs and increments version number (_v2, _v3, etc.)
    
    Args:
        container_client: Azure async container client
        blob_name: Original blob name
        folder_prefix: Optional folder prefix for listing
    
    Returns:
        Versioned blob name (original or with _vN suffix)
    """
    import re
    
    try:
        # List existing blobs in the folder
        prefix_for_list = folder_prefix.rstrip('/') + '/' if folder_prefix else None
        existing_blobs = set()
        try:
            async for b in container_client.list_blobs(name_starts_with=prefix_for_list) if prefix_for_list else container_client.list_blobs():
                existing_blobs.add(b.name)
        except Exception:
            pass
        
        # Check if blob exists
        if blob_name not in existing_blobs:
            return blob_name
        
        # Extract file parts
        if '/' in blob_name:
            folder_part = blob_name.rsplit('/', 1)[0] + '/'
            file_part = blob_name.rsplit('/', 1)[1]
        else:
            folder_part = ''
            file_part = blob_name
        
        # Split filename and extension
        if '.' in file_part:
            name_without_ext, ext = file_part.rsplit('.', 1)
            ext = '.' + ext
        else:
            name_without_ext = file_part
            ext = ''
        
        # Check if already versioned, extract base name
        version_match = re.match(r'^(.+?)_v(\d+)$', name_without_ext)
        if version_match:
            base_name = version_match.group(1)
            current_version = int(version_match.group(2))
        else:
            base_name = name_without_ext
            current_version = 1
        
        # Find next available version
        version = current_version + 1
        while True:
            new_name = f"{folder_part}{base_name}_v{version}{ext}"
            if new_name not in existing_blobs:
                logger.info(f"File version conflict detected, using versioned name: {new_name}")
                return new_name
            version += 1
            if version > 1000:  # Safety limit
                raise ValueError(f"Too many versions exist for blob: {blob_name}")
                
    except Exception as ex:
        logger.warning(f"Could not check for existing versions (async): {ex}, using original name")
        return blob_name


async def upload_file_to_container_async(
    file_path: str, 
    app_id: str, 
    blob_name: Optional[str] = None,
    folder_prefix: Optional[str] = None,
    content_type: Optional[str] = None,
    enable_versioning: bool = True
) -> str:
    """
    Async version: Upload a file to the application's storage container with automatic versioning.
    
    Args:
        file_path: Path to the file to upload
        app_id: Application ID (used as container name)
        blob_name: Optional blob name (uses filename if not provided)
        folder_prefix: Optional folder prefix
        content_type: Optional content type override
        enable_versioning: If True, auto-version files that already exist (default: True)
    
    Returns:
        Blob URL of the uploaded file
    """
    try:
        account_url = get_storage_account_url()
        if not account_url:
            raise ValueError("Azure Storage URL not configured")
        
        blob_service = await get_async_blob_service_client(account_url)
        container_name = str(app_id).lower()
        
        if not blob_name:
            blob_name = os.path.basename(file_path)
        
        # Add folder prefix if specified and not already present
        if folder_prefix and not blob_name.startswith(folder_prefix):
            blob_name = f"{folder_prefix.rstrip('/')}/{blob_name}"
        
        # Create container if not exists
        container_client = blob_service.get_container_client(container_name)
        try:
            await container_client.create_container()
        except Exception:
            pass  # Already exists
        
        # Apply versioning for supported file types (md, xlsx, json) or when explicitly enabled
        versioned_extensions = ('.md', '.xlsx', '.json', '.csv')
        if enable_versioning and file_path.endswith(versioned_extensions):
            blob_name = await _get_versioned_blob_name_async(container_client, blob_name, folder_prefix)
        
        # Auto-detect content type
        if not content_type:
            if file_path.endswith(".md"):
                content_type = "text/markdown"
            elif file_path.endswith(".json"):
                content_type = "application/json"
        
        # Upload file
        with open(file_path, "rb") as data:
            await container_client.upload_blob(
                name=blob_name,
                data=data,
                overwrite=True,
                content_settings=ContentSettings(content_type=content_type) if content_type else None
            )
        
        blob_url = f"{account_url.rstrip('/')}/{container_name}/{blob_name}"
        logger.info(f"Uploaded file to: {blob_url}")
        return blob_url
        
    except Exception as ex:
        current_span = trace.get_current_span()
        if current_span and current_span.is_recording():
            current_span.record_exception(ex)
            current_span.set_status(Status(StatusCode.ERROR, str(ex)))
        logger.error(f"Failed to upload file to container (async): {ex}")
        raise


def download_template_from_storage(
    account_url: str, 
    blob_name: str, 
    local_path: str,
    container_name: str = "templates"
) -> bool:
    """
    Downloads a template file from Azure Storage.
    
    Args:
        account_url: Storage account URL
        blob_name: Name of the blob to download
        local_path: Local path to save the file
        container_name: Container name (default: 'templates')
    
    Returns:
        True if download successful, False otherwise
    """
    from azure.core.exceptions import ResourceNotFoundError
    
    try:
        credential = SyncDefaultAzureCredential(exclude_shared_token_cache_credential=True)
        blob_service_client = BlobServiceClient(account_url=account_url, credential=credential)
        blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_name)
        
        blob_data = blob_client.download_blob().readall()
        if not blob_data:
            logger.warning(f"Downloaded blob {blob_name} is empty!")
            return False
            
        with open(local_path, "wb") as download_file:
            download_file.write(blob_data)
        logger.debug(f"Downloaded {blob_name} to {local_path}")
        return True
        
    except ResourceNotFoundError:
        logger.warning(f"Blob '{blob_name}' not found in container '{container_name}'.")
        return False
    except Exception as e:
        logger.error(f"Error downloading blob {blob_name}: {e}")
        return False


# =============================================================================
# JSON TO MARKDOWN CONVERSION
# =============================================================================

def responses_json_to_markdown(
    json_path: str, 
    app_id: str, 
    md_path: Optional[str] = None,
    title: Optional[str] = None,
    report_type: str = "report"
) -> str:
    """
    Convert responses JSON to markdown format.
    
    Supports both ASR and Design report formats with automatic section
    heading detection based on numbering.
    
    Args:
        json_path: Path to the responses JSON file
        app_id: Application ID
        md_path: Optional output markdown path (auto-generated if not provided)
        title: Optional title override
        report_type: Report type for default filename (e.g., 'asr', 'design')
    
    Returns:
        Path to the generated markdown file
    """
    try:
        if md_path is None:
            md_path = f"{report_type}-report-{app_id}.md"
        
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        doc_title = title or data.get("title", "Application Report")
        sections = data.get("sections_array", [])
        
        lines = [f"# {doc_title}\n"]
        lines.append(f"**Application ID:** {app_id}\n")
        lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        lines.append("---\n")
        
        for section in sections:
            sec_id = section.get("id", "Section")
            response = section.get("response", "")
            
            # Parse section numbering for proper heading level
            match = re.match(r"(\d+(?:\.\d+)*)(?:\s+)(.*)", sec_id)
            if match:
                numbering = match.group(1)
                level = numbering.count('.') + 1
                # Cap at h4
                level = min(level + 1, 4)
                heading = f"{'#' * level} {sec_id}"
            else:
                heading = f"## {sec_id}"
            
            lines.append(f"{heading}\n")
            if response:
                lines.append(f"{response}\n")
        
        md_content = "\n".join(lines)
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(md_content)
        
        logger.debug(f"Generated markdown report: {md_path}")
        return md_path
        
    except Exception as ex:
        # Record exception in span if available
        current_span = trace.get_current_span()
        if current_span and current_span.is_recording():
            current_span.record_exception(ex)
            current_span.set_status(Status(StatusCode.ERROR, str(ex)))
            current_span.add_event("responses_json_to_markdown_failed", {
                "json_path": json_path,
                "app_id": app_id,
                "md_path": md_path
            })
        logger.error(f"Failed to convert JSON to markdown: {ex}")
        raise


def create_response_file(
    responses: List[str], 
    prompt_file: str, 
    app_id: str,
    output_file: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create a response file with agent responses.
    
    Merges responses back into the prompt file structure.
    
    Args:
        responses: List of responses for each section
        prompt_file: Path to the original prompt file
        app_id: Application ID
        output_file: Optional output filename
    
    Returns:
        dict: Result containing status and output file path
    """
    try:
        if output_file is None:
            output_file = f"responses-{app_id}.json"
        
        with open(prompt_file, 'r', encoding='utf-8') as infile:
            data = json.load(infile)
        
        sections = data.get("sections_array", [])
        for section, response in zip(sections, responses):
            section["response"] = response
        
        with open(output_file, 'w', encoding='utf-8') as outfile:
            json.dump(data, outfile, indent=4)
        
        return {"status": "success", "output_file": output_file}
        
    except Exception as ex:
        # Record exception in span if available
        current_span = trace.get_current_span()
        if current_span and current_span.is_recording():
            current_span.record_exception(ex)
            current_span.set_status(Status(StatusCode.ERROR, str(ex)))
        logger.error(f"Failed to create response file: {ex}")
        return {"status": "error", "message": str(ex)}


# =============================================================================
# INSTRUCTION FILE UTILITIES
# =============================================================================

def load_instructions_from_file(
    instructions_file: str,
    placeholder_replacements: Optional[Dict[str, str]] = None,
    default_instructions: Optional[str] = None
) -> str:
    """
    Load agent instructions from a file with optional placeholder replacement.
    
    Args:
        instructions_file: Path to the instructions file
        placeholder_replacements: Dict of {placeholder: value} for replacements
        default_instructions: Fallback instructions if file loading fails
    
    Returns:
        Loaded and processed instructions string
    """
    try:
        with open(instructions_file, 'r', encoding='utf-8') as f:
            instructions_template = f.read().strip()
        logger.debug(f"Loaded instructions from file: {instructions_file}")
    except Exception as file_ex:
        logger.warning(f"Failed to load instructions from {instructions_file}: {file_ex}")
        if default_instructions:
            return default_instructions
        raise
    
    # Replace placeholders
    if placeholder_replacements:
        for placeholder, value in placeholder_replacements.items():
            # Support both {{placeholder}} and {placeholder} formats
            instructions_template = instructions_template.replace(f"{{{{{placeholder}}}}}", value)
            instructions_template = instructions_template.replace(f"{{{placeholder}}}", value)
    
    return instructions_template



# =============================================================================
# VIRTUAL DIRECTORY SETUP UTILITIES
# =============================================================================

# Maps each API endpoint to its virtual directories (input/output folders)
# The container name IS the app_id
ENDPOINT_VIRTUAL_DIRECTORIES: Dict[str, Dict[str, str]] = {
    "/generateDesign": {
        "input": "design/input",
        "output": "design/output",
    },
    "/generateAssessmentReport": {
        "input": "asr/input",
        "output": "asr/output",
    },
    "/generateAppPlan": {
        "input": "app-planning/input",
        "output": "app-planning/output",
    },
    "/analyzeArchitecture": {
        "input": "architecture-analyzer/input",
        "output": "architecture-analyzer/output",
    },
    "/analyzeCode": {
        "input": "code-analyzer/input",
        "output": "code-analyzer/output",
    },
    "/discoverKubernetes": {
        "input": "kubernetes-discovery/input",
        "output": "kubernetes-discovery/output",
    },
    "/runAnalysis": {
        "input": "responder/input",
        "output": "responder/output",
    },
}


def get_all_virtual_directories() -> List[str]:
    """
    Get a flat list of all virtual directory paths.
    
    Returns:
        List of virtual directory paths (14 total: 7 endpoints × 2 dirs each)
    """
    directories = []
    for endpoint, dirs in ENDPOINT_VIRTUAL_DIRECTORIES.items():
        directories.append(dirs["input"])
        directories.append(dirs["output"])
    return directories


def setup_virtual_directories_for_app(
    app_id: str,
    storage_account_url: Optional[str] = None,
    endpoints: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Set up all virtual directories for an application in Azure Blob Storage.
    
    Creates the required folder structure for all agent endpoints within the
    application's container. Virtual directories are created by uploading
    placeholder .gitkeep files.
    
    Endpoint to Virtual Directory Mapping:
        /generateDesign           → [app-id]/design/input/*, [app-id]/design/output/*
        /generateAssessmentReport → [app-id]/asr/input/*, [app-id]/asr/output/*
        /generateAppPlan          → [app-id]/app-planning/input/*, [app-id]/app-planning/output/*
        /analyzeArchitecture      → [app-id]/architecture-analyzer/input/*, [app-id]/architecture-analyzer/output/*
        /analyzeCode              → [app-id]/code-analyzer/input/*, [app-id]/code-analyzer/output/*
        /discoverKubernetes       → [app-id]/kubernetes-discovery/input/*, [app-id]/kubernetes-discovery/output/*
        /runAnalysis              → [app-id]/responder/input/*, [app-id]/responder/output/*
    
    Args:
        app_id: Application ID (container name)
        storage_account_url: Azure Blob Storage account URL (uses env var if not provided)
        endpoints: Optional list of specific endpoints to set up (defaults to all)
        
    Returns:
        Dictionary with setup results:
        {
            "status": "success" | "partial" | "error",
            "container": app_id,
            "directories_created": [...],
            "directories_existed": [...],
            "directories_failed": [...],
            "total": int,
            "successful": int,
            "failed": int
        }
    """
    logger.info(f"Setting up virtual directories for app: {app_id}")
    
    try:
        # Get blob service client
        blob_service_client = get_blob_service_client(storage_account_url)
        container_client = blob_service_client.get_container_client(app_id)
        
        # Verify container exists
        try:
            container_client.get_container_properties()
            logger.debug(f"Container '{app_id}' exists")
        except ResourceNotFoundError:
            logger.error(f"Container '{app_id}' does not exist")
            return {
                "status": "error",
                "container": app_id,
                "message": f"Container '{app_id}' does not exist. Create container first.",
                "directories_created": [],
                "directories_existed": [],
                "directories_failed": [],
                "total": 0,
                "successful": 0,
                "failed": 0
            }
        
        # Determine which directories to create
        if endpoints:
            directories_to_create = []
            for endpoint in endpoints:
                normalized = endpoint if endpoint.startswith("/") else f"/{endpoint}"
                if normalized in ENDPOINT_VIRTUAL_DIRECTORIES:
                    dirs = ENDPOINT_VIRTUAL_DIRECTORIES[normalized]
                    directories_to_create.extend([dirs["input"], dirs["output"]])
                else:
                    logger.warning(f"Unknown endpoint: {endpoint}")
        else:
            directories_to_create = get_all_virtual_directories()
        
        # Track results
        created = []
        existed = []
        failed = []
        
        # Create each virtual directory
        for directory_path in directories_to_create:
            result = _create_virtual_directory(container_client, directory_path)
            if result["status"] == "created":
                created.append(directory_path)
            elif result["status"] == "exists":
                existed.append(directory_path)
            else:
                failed.append({"path": directory_path, "error": result.get("error", "Unknown error")})
        
        # Determine overall status
        total = len(directories_to_create)
        successful = len(created) + len(existed)
        failed_count = len(failed)
        
        if failed_count == 0:
            status = "success"
            message = f"All {total} virtual directories set up successfully"
        elif successful > 0:
            status = "partial"
            message = f"{successful}/{total} directories set up, {failed_count} failed"
        else:
            status = "error"
            message = f"Failed to set up virtual directories"
        
        result = {
            "status": status,
            "container": app_id,
            "message": message,
            "directories_created": created,
            "directories_existed": existed,
            "directories_failed": failed,
            "total": total,
            "successful": successful,
            "failed": failed_count
        }
        
        logger.info(f"Virtual directory setup for '{app_id}': {status} - {successful}/{total} successful")
        return result
        
    except Exception as ex:
        logger.error(f"Error setting up virtual directories for '{app_id}': {str(ex)}")
        current_span = trace.get_current_span()
        if current_span and current_span.is_recording():
            current_span.record_exception(ex)
        return {
            "status": "error",
            "container": app_id,
            "message": str(ex),
            "directories_created": [],
            "directories_existed": [],
            "directories_failed": [],
            "total": 0,
            "successful": 0,
            "failed": 0
        }


def _create_virtual_directory(
    container_client,
    directory_path: str
) -> Dict[str, str]:
    """
    Create a virtual directory by uploading a placeholder file.
    
    In Azure Blob Storage, directories don't exist as separate entities.
    We create them by uploading a .gitkeep placeholder file.
    
    Args:
        container_client: ContainerClient for the container
        directory_path: Virtual directory path (e.g., "design/input")
        
    Returns:
        Dict with status: "created", "exists", or "error"
    """
    placeholder_blob = f"{directory_path}/.gitkeep"
    placeholder_content = (
        f"# Placeholder for virtual directory: {directory_path}\n"
        f"# Created: {datetime.utcnow().isoformat()}Z\n"
        f"# This file establishes the directory structure for agent operations.\n"
    )
    
    try:
        # Check if directory already has content
        blobs = list(container_client.list_blobs(name_starts_with=f"{directory_path}/"))
        
        if blobs:
            logger.debug(f"Directory '{directory_path}' already exists ({len(blobs)} blob(s))")
            return {"status": "exists", "path": directory_path, "blob_count": len(blobs)}
        
        # Upload placeholder to create directory
        blob_client = container_client.get_blob_client(placeholder_blob)
        blob_client.upload_blob(
            placeholder_content,
            overwrite=True,
            content_settings=ContentSettings(content_type="text/plain")
        )
        logger.debug(f"Created virtual directory: {directory_path}/")
        return {"status": "created", "path": directory_path}
        
    except Exception as e:
        logger.error(f"Failed to create virtual directory '{directory_path}': {e}")
        return {"status": "error", "path": directory_path, "error": str(e)}


# =============================================================================
# PROMPTS PROCESSING UTILITIES
# =============================================================================

def process_prompts_from_json(json_file: str) -> List[Dict[str, Any]]:
    """
    Process prompts from a JSON file.
    
    Extracts sections and their prompts for agent processing.
    
    Args:
        json_file: Path to the prompts JSON file
    
    Returns:
        List of prompt dictionaries with section info
    """
    logger.debug(f"Processing prompts from file: {json_file}")
    try:
        with open(json_file, 'r', encoding='utf-8') as file:
            data = json.load(file)
        
        prompts = []
        for section in data.get("sections_array", []):
            section_id = section.get("id", "")
            prompt_text = section.get("prompt", "")
            
            if prompt_text:
                prompts.append({
                    "section_id": section_id,
                    "prompt": prompt_text,
                    "knowledge": section.get("knowledge", {}),
                    "mcp": section.get("knowledge", {}).get("mcp", []) if isinstance(section.get("knowledge"), dict) else []
                })
        
        logger.debug(f"Processed {len(prompts)} prompts from {json_file}")
        return prompts
        
    except Exception as ex:
        logger.error(f"Error processing prompts from {json_file}: {str(ex)}")
        current_span = trace.get_current_span()
        if current_span and current_span.is_recording():
            current_span.record_exception(ex)
        raise


def get_unique_blob_metadata(container_name: str, storage_account_name: str, expand_csv: bool = True):
    """
    Query all blob files' metadata in the given container and return unique metadata key-value pairs.
    Args:
        container_name (str): The name of the container to look into.
        storage_account_name (str): The name of the Azure Storage account.
        expand_csv (bool): If True, expands comma-separated values into individual (key, value) pairs.
                          If False, returns the raw comma-separated string as-is.
    Returns:
        Set of unique (key, value) metadata pairs found across all blobs.
    """
    credential = SyncDefaultAzureCredential(exclude_shared_token_cache_credential=True)
    blob_service_client = BlobServiceClient(
        f"https://{storage_account_name}.blob.core.windows.net/",
        credential=credential
    )
    unique_metadata = set()
    container_client = blob_service_client.get_container_client(container_name)
    # Include metadata in the listing to retrieve it in a single API call
    blobs = container_client.list_blobs(include=['metadata'])
    for blob in blobs:
        metadata = blob.metadata or {}
        for k, v in metadata.items():
            if expand_csv and ',' in v:
                # Split comma-separated values and add each as a separate pair
                for individual_value in v.split(','):
                    cleaned_value = individual_value.strip()
                    if cleaned_value:  # Skip empty values
                        unique_metadata.add((k, cleaned_value))
            else:
                unique_metadata.add((k, v))
    return unique_metadata