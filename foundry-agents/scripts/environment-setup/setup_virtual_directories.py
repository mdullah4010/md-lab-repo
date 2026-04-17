#!/usr/bin/env python3
# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

"""
Setup Virtual Directories in Azure Blob Storage.

This script creates the required virtual directory structure for the Insights Agent API.
In Azure Blob Storage, virtual directories are created by uploading placeholder files
with the appropriate path prefixes.

Endpoint to Virtual Directory Mapping:
    /generateDesign           → [app-id]/design/input/*, [app-id]/design/output/*
    /generateAssessmentReport → [app-id]/asr/input/*, [app-id]/asr/output/*
    /generateAppPlan          → [app-id]/app-planning/input/*, [app-id]/app-planning/output/*
    /analyzeArchitecture      → [app-id]/architecture-analyzer/input/*, [app-id]/architecture-analyzer/output/*
    /analyzeCode              → [app-id]/code-analyzer/input/*, [app-id]/code-analyzer/output/*
    /discoverKubernetes       → [app-id]/kubernetes-discovery/input/*, [app-id]/kubernetes-discovery/output/*
    /runAnalysis              → [app-id]/responder/input/*, [app-id]/responder/output/*

Usage:
    # Using environment variables
    export AZURE_STORAGE_ACCOUNT_NAME="mystorageaccount"
    python scripts/environment-setup/setup_virtual_directories.py --app-id my-application-001

    # Using command line arguments
    python scripts/environment-setup/setup_virtual_directories.py \\
        --app-id my-application-001 \\
        --storage-account mystorageaccount

    # Using connection string
    python scripts/environment-setup/setup_virtual_directories.py \\
        --app-id my-application-001 \\
        --connection-string "DefaultEndpointsProtocol=https;..."

    # Dry run (preview without creating)
    python scripts/environment-setup/setup_virtual_directories.py --app-id my-app --dry-run

Prerequisites:
    - Azure CLI logged in (az login) OR
    - Environment variables for service principal authentication OR
    - Connection string for storage account
    
    pip install azure-storage-blob azure-identity
"""

import os
import sys
import argparse
import logging
from datetime import datetime
from typing import Dict, List, Optional

try:
    from azure.storage.blob import BlobServiceClient, ContainerClient
    from azure.identity import DefaultAzureCredential
    from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
except ImportError as e:
    print(f"Error: Required Azure packages not installed.")
    print(f"Run: pip install azure-storage-blob azure-identity")
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# =============================================================================
# Virtual Directory Configuration
# =============================================================================

# Maps each API endpoint to its virtual directories
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


def get_blob_service_client(
    storage_account_name: Optional[str] = None,
    connection_string: Optional[str] = None
) -> BlobServiceClient:
    """
    Create a BlobServiceClient using available credentials.
    
    Args:
        storage_account_name: Name of the Azure Storage account
        connection_string: Full connection string (takes precedence)
        
    Returns:
        BlobServiceClient instance
        
    Raises:
        ValueError: If no valid credentials are provided
    """
    # Option 1: Use connection string if provided
    if connection_string:
        logger.info("Using connection string for authentication")
        return BlobServiceClient.from_connection_string(connection_string)
    
    # Option 2: Use storage account name with DefaultAzureCredential
    if storage_account_name:
        account_url = f"https://{storage_account_name}.blob.core.windows.net"
        logger.info(f"Using DefaultAzureCredential for {account_url}")
        credential = DefaultAzureCredential()
        return BlobServiceClient(account_url=account_url, credential=credential)
    
    # Option 3: Try environment variables
    env_account = os.environ.get("AZURE_STORAGE_ACCOUNT_NAME")
    env_connection = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    
    if env_connection:
        logger.info("Using connection string from environment")
        return BlobServiceClient.from_connection_string(env_connection)
    
    if env_account:
        account_url = f"https://{env_account}.blob.core.windows.net"
        logger.info(f"Using DefaultAzureCredential for {account_url}")
        credential = DefaultAzureCredential()
        return BlobServiceClient(account_url=account_url, credential=credential)
    
    raise ValueError(
        "No storage credentials provided. Use --storage-account, --connection-string, "
        "or set AZURE_STORAGE_ACCOUNT_NAME environment variable."
    )


def create_container_if_not_exists(
    blob_service_client: BlobServiceClient,
    container_name: str,
    dry_run: bool = False
) -> ContainerClient:
    """
    Create a container if it doesn't exist.
    
    Args:
        blob_service_client: BlobServiceClient instance
        container_name: Name of the container (app_id)
        dry_run: If True, only preview without creating
        
    Returns:
        ContainerClient for the container
    """
    container_client = blob_service_client.get_container_client(container_name)
    
    try:
        container_client.get_container_properties()
        logger.info(f"✅ Container '{container_name}' already exists")
    except ResourceNotFoundError:
        if dry_run:
            logger.info(f"🔍 [DRY RUN] Would create container: {container_name}")
        else:
            container_client.create_container()
            logger.info(f"✅ Created container: {container_name}")
    
    return container_client


def create_virtual_directory(
    container_client: ContainerClient,
    directory_path: str,
    dry_run: bool = False
) -> bool:
    """
    Create a virtual directory by uploading a placeholder file.
    
    In Azure Blob Storage, directories don't exist as separate entities.
    We create them by uploading a .gitkeep placeholder file.
    
    Args:
        container_client: ContainerClient for the container
        directory_path: Virtual directory path (e.g., "design/input")
        dry_run: If True, only preview without creating
        
    Returns:
        True if created or already exists, False on error
    """
    # Create a .gitkeep placeholder to establish the directory
    placeholder_blob = f"{directory_path}/.gitkeep"
    placeholder_content = f"# Placeholder for virtual directory: {directory_path}\n# Created: {datetime.utcnow().isoformat()}Z\n"
    
    try:
        # Check if directory already has content
        blobs = list(container_client.list_blobs(name_starts_with=f"{directory_path}/"))
        
        if blobs:
            logger.info(f"  ✅ Directory '{directory_path}' already exists ({len(blobs)} blob(s))")
            return True
        
        if dry_run:
            logger.info(f"  🔍 [DRY RUN] Would create: {placeholder_blob}")
            return True
        
        # Upload placeholder
        blob_client = container_client.get_blob_client(placeholder_blob)
        blob_client.upload_blob(placeholder_content, overwrite=True)
        logger.info(f"  ✅ Created: {directory_path}/")
        return True
        
    except Exception as e:
        logger.error(f"  ❌ Failed to create '{directory_path}': {e}")
        return False


def setup_virtual_directories(
    app_id: str,
    storage_account_name: Optional[str] = None,
    connection_string: Optional[str] = None,
    dry_run: bool = False,
    endpoints: Optional[List[str]] = None
) -> Dict[str, bool]:
    """
    Set up all virtual directories for an application.
    
    Args:
        app_id: Application ID (becomes the container name)
        storage_account_name: Azure Storage account name
        connection_string: Azure Storage connection string
        dry_run: If True, only preview without creating
        endpoints: Optional list of specific endpoints to set up
        
    Returns:
        Dictionary mapping directory paths to success status
    """
    logger.info("=" * 60)
    logger.info("Azure Blob Storage Virtual Directory Setup")
    logger.info("=" * 60)
    logger.info(f"App ID (Container): {app_id}")
    if dry_run:
        logger.info("Mode: DRY RUN (no changes will be made)")
    logger.info("")
    
    # Get blob service client
    blob_service_client = get_blob_service_client(
        storage_account_name=storage_account_name,
        connection_string=connection_string
    )
    
    # Create container (container name = app_id)
    container_client = create_container_if_not_exists(
        blob_service_client=blob_service_client,
        container_name=app_id,
        dry_run=dry_run
    )
    
    # Determine which directories to create
    if endpoints:
        # Filter to specific endpoints
        directories_to_create = []
        for endpoint in endpoints:
            normalized = endpoint if endpoint.startswith("/") else f"/{endpoint}"
            if normalized in ENDPOINT_VIRTUAL_DIRECTORIES:
                dirs = ENDPOINT_VIRTUAL_DIRECTORIES[normalized]
                directories_to_create.extend([dirs["input"], dirs["output"]])
            else:
                logger.warning(f"Unknown endpoint: {endpoint}")
    else:
        # Create all directories
        directories_to_create = get_all_virtual_directories()
    
    logger.info(f"\nCreating {len(directories_to_create)} virtual directories...")
    logger.info("-" * 40)
    
    # Create each directory
    results = {}
    for directory in directories_to_create:
        success = create_virtual_directory(
            container_client=container_client,
            directory_path=directory,
            dry_run=dry_run
        )
        results[directory] = success
    
    # Summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)
    
    successful = sum(1 for v in results.values() if v)
    failed = sum(1 for v in results.values() if not v)
    
    logger.info(f"Container: {app_id}")
    logger.info(f"Directories created/verified: {successful}")
    if failed > 0:
        logger.info(f"Directories failed: {failed}")
    
    if not dry_run:
        logger.info("")
        logger.info("Virtual directory structure:")
        logger.info(f"  {app_id}/")
        
        # Group by endpoint
        for endpoint, dirs in ENDPOINT_VIRTUAL_DIRECTORIES.items():
            base = dirs["input"].split("/")[0]
            logger.info(f"    ├── {base}/")
            logger.info(f"    │   ├── input/   ← {endpoint}")
            logger.info(f"    │   └── output/")
    
    return results


def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Create virtual directories in Azure Blob Storage for Insights Agent API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Create directories for a specific app
    python setup_virtual_directories.py --app-id my-app-001 --storage-account mystorageaccount

    # Dry run to preview changes
    python setup_virtual_directories.py --app-id my-app-001 --dry-run

    # Create directories for specific endpoints only
    python setup_virtual_directories.py --app-id my-app-001 --endpoints /generateDesign /analyzeCode

Virtual Directory Structure (per app):
    [app-id]/
    ├── design/input/           ← /generateDesign
    ├── design/output/
    ├── asr/input/              ← /generateAssessmentReport
    ├── asr/output/
    ├── app-planning/input/     ← /generateAppPlan
    ├── app-planning/output/
    ├── architecture-analyzer/  ← /analyzeArchitecture
    ├── code-analyzer/          ← /analyzeCode
    ├── kubernetes-discovery/   ← /discoverKubernetes
    └── responder/              ← /runAnalysis
        """
    )
    
    parser.add_argument(
        "--app-id",
        required=True,
        help="Application ID (becomes the container name)"
    )
    
    parser.add_argument(
        "--storage-account",
        help="Azure Storage account name (or set AZURE_STORAGE_ACCOUNT_NAME env var)"
    )
    
    parser.add_argument(
        "--connection-string",
        help="Azure Storage connection string (or set AZURE_STORAGE_CONNECTION_STRING env var)"
    )
    
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without creating directories"
    )
    
    parser.add_argument(
        "--endpoints",
        nargs="+",
        help="Specific endpoints to set up (e.g., /generateDesign /analyzeCode)"
    )
    
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    try:
        results = setup_virtual_directories(
            app_id=args.app_id,
            storage_account_name=args.storage_account,
            connection_string=args.connection_string,
            dry_run=args.dry_run,
            endpoints=args.endpoints
        )
        
        # Exit with error code if any failures
        if not all(results.values()):
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
