"""
Repository Handler Utilities

This module provides utilities for:
- Cloning/downloading repositories from multiple sources (GitHub, GitLab, Azure DevOps, Bitbucket)
- Downloading code from Azure Blob Storage URLs
- Detecting repository content types (Terraform, Java, Python, etc.)
- Determining appropriate analysis configuration folders
"""

import os
import shutil
import tempfile
import subprocess
from typing import Dict, Tuple, Optional
from pathlib import Path
import logging
import re
import zipfile
import urllib.request
from enum import Enum

from agents.logging_config import get_logger

logger = get_logger(__name__)


class SourceType(str, Enum):
    """Enum for supported source types."""
    GITHUB = "github"
    GITLAB = "gitlab"
    AZURE_DEVOPS = "azure_devops"
    BITBUCKET = "bitbucket"
    BLOB = "blob"
    UNKNOWN = "unknown"


class GitHubRepoHandler:
    """
    Handles repository operations including cloning and content detection.
    Supports GitHub, GitLab, Azure DevOps, Bitbucket, and Azure Blob Storage.
    
    Note: Class name kept as GitHubRepoHandler for backward compatibility,
    but it now supports multiple source types.
    """
    
    def __init__(self):
        self.logger = get_logger(f"{__name__}.{self.__class__.__name__}")
        self.temp_dirs = []  # Track temporary directories for cleanup
    
    def _detect_source_type(self, url: str) -> SourceType:
        """
        Detect the source type from the URL.
        
        Args:
            url: Repository or blob URL
            
        Returns:
            SourceType enum value
        """
        url_lower = url.lower()
        if 'github.com' in url_lower:
            return SourceType.GITHUB
        elif 'gitlab.com' in url_lower:
            return SourceType.GITLAB
        elif 'dev.azure.com' in url_lower or 'visualstudio.com' in url_lower:
            return SourceType.AZURE_DEVOPS
        elif 'bitbucket.org' in url_lower:
            return SourceType.BITBUCKET
        elif '.blob.core.windows.net' in url_lower:
            return SourceType.BLOB
        else:
            return SourceType.UNKNOWN
    
    async def clone_or_download_repo(self, repo_url: str) -> str:
        """
        Clone or download a repository to a temporary directory.
        Supports multiple source types including Azure Blob Storage.
        
        Args:
            repo_url: Repository URL (GitHub, GitLab, Azure DevOps, Bitbucket) or Azure Blob Storage URL
            
        Returns:
            Path to the cloned/downloaded repository directory
            
        Raises:
            Exception: If cloning/downloading fails
        """
        source_type = self._detect_source_type(repo_url)
        self.logger.info(f"Detected source type: {source_type.value} for URL: {repo_url}")
        
        if source_type == SourceType.BLOB:
            return await self._download_from_blob(repo_url)
        else:
            return await self._clone_git_repo(repo_url, source_type)
    
    async def _clone_git_repo(self, repo_url: str, source_type: SourceType) -> str:
        """
        Clone a git repository to a temporary directory.
        
        Args:
            repo_url: Git repository URL
            source_type: Type of git host
            
        Returns:
            Path to the cloned repository directory
        """
        temp_dir = None
        try:
            # Create a temporary directory
            temp_dir = tempfile.mkdtemp(prefix="repo_")
            self.temp_dirs.append(temp_dir)
            
            self.logger.info(f"Cloning repository from {repo_url} to {temp_dir}")
            
            # Normalize URL for git clone
            git_url = repo_url
            if not git_url.endswith('.git'):
                git_url = f"{git_url}.git"
            
            # Try git clone first
            try:
                result = subprocess.run(
                    ['git', 'clone', '--depth', '1', git_url, temp_dir],
                    capture_output=True,
                    text=True,
                    timeout=300  # 5 minutes timeout
                )
                
                if result.returncode == 0:
                    self.logger.info(f"Successfully cloned repository to {temp_dir}")
                    return temp_dir
                else:
                    self.logger.error(f"Git clone failed: {result.stderr}")
                    raise Exception(f"Git clone failed: {result.stderr}")
                    
            except FileNotFoundError:
                # Git not available, try alternative method
                self.logger.warning("Git command not found, attempting alternative download method")
                return await self._download_repo_zip(repo_url, temp_dir, source_type)
                
        except Exception as ex:
            self.logger.error(f"Failed to clone/download repository: {ex}")
            # Cleanup on failure
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
            raise
    
    async def _download_from_blob(self, blob_url: str) -> str:
        """
        Download code from Azure Blob Storage URL.
        
        IMPORTANT: Code must be uploaded as a .zip file since Azure Blob Storage
        doesn't support folder structures directly. The zip file will be extracted
        for analysis.
        
        Supported formats:
        - .zip files (recommended)
        - .tar.gz files
        
        Args:
            blob_url: Azure Blob Storage URL pointing to a zip/tar.gz file
            
        Returns:
            Path to the extracted directory
            
        Raises:
            ValueError: If the blob URL doesn't point to a supported archive format
        """
        temp_dir = None
        try:
            temp_dir = tempfile.mkdtemp(prefix="blob_code_")
            self.temp_dirs.append(temp_dir)
            
            self.logger.info(f"Downloading code from blob storage: {blob_url}")
            
            # Parse URL to get filename (remove query params like SAS tokens)
            url_path = blob_url.split('?')[0].lower()
            
            # Determine archive type
            is_zip = url_path.endswith('.zip')
            is_targz = url_path.endswith('.tar.gz') or url_path.endswith('.tgz')
            
            if not is_zip and not is_targz:
                error_msg = (
                    "Blob URL must point to a .zip or .tar.gz file. "
                    "Azure Blob Storage doesn't support folder uploads directly. "
                    "Please zip your code folder and upload the zip file. "
                    f"Received URL: {blob_url}"
                )
                self.logger.error(error_msg)
                raise ValueError(error_msg)
            
            # Determine local filename
            archive_ext = '.zip' if is_zip else ('.tar.gz' if url_path.endswith('.tar.gz') else '.tgz')
            archive_path = os.path.join(temp_dir, f'code{archive_ext}')
            
            # Download the archive
            try:
                from azure.storage.blob.aio import BlobClient
                from azure.identity.aio import DefaultAzureCredential
                
                async with DefaultAzureCredential(exclude_shared_token_cache_credential=True) as credential:
                    blob_client = BlobClient.from_blob_url(blob_url, credential=credential)
                    
                    async with blob_client:
                        blob_data = await blob_client.download_blob()
                        with open(archive_path, 'wb') as f:
                            async for chunk in blob_data.chunks():
                                f.write(chunk)
                
                self.logger.info(f"Downloaded blob archive using Azure SDK")
            except Exception as sdk_ex:
                self.logger.warning(f"Azure SDK download failed, trying direct URL: {sdk_ex}")
                # Fall back to direct URL download (for public blobs or blobs with SAS tokens)
                urllib.request.urlretrieve(blob_url, archive_path)
                self.logger.info(f"Downloaded blob archive using direct URL")
            
            # Extract the archive
            if is_zip:
                self.logger.info("Extracting zip archive...")
                with zipfile.ZipFile(archive_path, 'r') as zip_ref:
                    zip_ref.extractall(temp_dir)
            else:
                # Handle tar.gz
                self.logger.info("Extracting tar.gz archive...")
                import tarfile
                with tarfile.open(archive_path, 'r:gz') as tar_ref:
                    tar_ref.extractall(temp_dir)
            
            # Remove the archive file after extraction
            os.remove(archive_path)
            
            # Check if there's a single root folder and move contents up
            # This handles the common case where zip contains: myproject/src/... -> we want src/...
            extracted_items = [d for d in os.listdir(temp_dir) if os.path.isdir(os.path.join(temp_dir, d))]
            if len(extracted_items) == 1:
                extracted_path = os.path.join(temp_dir, extracted_items[0])
                # Check if this single folder contains the actual code
                inner_items = os.listdir(extracted_path)
                if inner_items:  # Only move if the folder isn't empty
                    self.logger.info(f"Moving contents from single root folder: {extracted_items[0]}")
                    for item in inner_items:
                        src = os.path.join(extracted_path, item)
                        dst = os.path.join(temp_dir, item)
                        shutil.move(src, dst)
                    os.rmdir(extracted_path)
            
            # Log what we extracted
            total_files = sum(len(files) for _, _, files in os.walk(temp_dir))
            self.logger.info(f"Successfully extracted {total_files} files from blob archive to {temp_dir}")
            
            return temp_dir
                
        except ValueError:
            # Re-raise validation errors
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
            raise
        except Exception as ex:
            self.logger.error(f"Failed to download from blob storage: {ex}")
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
            raise
    
    async def _download_repo_zip(self, repo_url: str, target_dir: str, source_type: SourceType = SourceType.GITHUB) -> str:
        """
        Download repository as ZIP (fallback method when git is not available).
        Supports GitHub, GitLab, and other git hosts.
        
        Args:
            repo_url: Repository URL
            target_dir: Target directory to extract files
            source_type: Type of git host
            
        Returns:
            Path to the extracted repository directory
        """
        # Convert git URL to ZIP download URL based on source type
        clean_url = repo_url.rstrip('/').replace('.git', '')
        
        if source_type == SourceType.GITHUB:
            zip_url = f"{clean_url}/archive/refs/heads/main.zip"
        elif source_type == SourceType.GITLAB:
            zip_url = f"{clean_url}/-/archive/main/{clean_url.split('/')[-1]}-main.zip"
        elif source_type == SourceType.BITBUCKET:
            zip_url = f"{clean_url}/get/main.zip"
        else:
            # Default to GitHub-style
            zip_url = f"{clean_url}/archive/refs/heads/main.zip"
        
        zip_path = os.path.join(target_dir, 'repo.zip')
        
        try:
            self.logger.info(f"Downloading repository as ZIP from {zip_url}")
            urllib.request.urlretrieve(zip_url, zip_path)
            
            # Extract ZIP
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(target_dir)
            
            # Remove ZIP file
            os.remove(zip_path)
            
            # Find the extracted folder (usually repo-name-main or repo-name-master)
            extracted_dirs = [d for d in os.listdir(target_dir) if os.path.isdir(os.path.join(target_dir, d))]
            if extracted_dirs:
                # Move contents up one level
                extracted_path = os.path.join(target_dir, extracted_dirs[0])
                for item in os.listdir(extracted_path):
                    shutil.move(os.path.join(extracted_path, item), target_dir)
                os.rmdir(extracted_path)
            
            self.logger.info(f"Successfully downloaded and extracted repository to {target_dir}")
            return target_dir
            
        except Exception as ex:
            self.logger.error(f"Failed to download repository as ZIP: {ex}")
            raise
    
    def detect_repo_content_type(self, repo_path: str) -> Tuple[str, str]:
        """
        Detect the type of content in the repository and return appropriate config folder.
        
        Args:
            repo_path: Path to the cloned repository
            
        Returns:
            Tuple of (content_type, config_folder)
            - content_type: 'terraform', 'java', 'python', 'javascript', 'general'
            - config_folder: 'terrasec' or 'kinfosec'
        """
        self.logger.info(f"Detecting content type for repository at {repo_path}")
        
        # Track file types found
        file_extensions = {}
        terraform_files = []
        
        # Walk through repository
        for root, dirs, files in os.walk(repo_path):
            # Skip hidden directories and common ignore patterns
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['node_modules', 'venv', '__pycache__']]
            
            for file in files:
                if file.startswith('.'):
                    continue
                
                file_path = os.path.join(root, file)
                _, ext = os.path.splitext(file)
                
                # Count extensions
                if ext:
                    file_extensions[ext] = file_extensions.get(ext, 0) + 1
                
                # Check for Terraform files
                if ext in ['.tf', '.tfvars'] or file.endswith('.tf.json'):
                    terraform_files.append(file_path)
                
                # Check for terraform JSON files
                if file.endswith('.json') and 'terraform' in file.lower():
                    terraform_files.append(file_path)
        
        self.logger.info(f"File extensions found: {file_extensions}")
        self.logger.info(f"Terraform files found: {len(terraform_files)}")
        
        # Decision logic: Terraform takes precedence
        if len(terraform_files) > 0 or '.tf' in file_extensions or '.tfvars' in file_extensions:
            self.logger.info("Detected Terraform content - using terrasec configuration")
            return 'terraform', 'terrasec'
        
        # Check for other common languages
        if '.java' in file_extensions:
            self.logger.info("Detected Java content - using kinfosec configuration")
            return 'java', 'kinfosec'
        elif '.py' in file_extensions:
            self.logger.info("Detected Python content - using kinfosec configuration")
            return 'python', 'kinfosec'
        elif '.js' in file_extensions or '.ts' in file_extensions:
            self.logger.info("Detected JavaScript/TypeScript content - using kinfosec configuration")
            return 'javascript', 'kinfosec'
        elif '.cs' in file_extensions:
            self.logger.info("Detected C# content - using kinfosec configuration")
            return 'csharp', 'kinfosec'
        elif '.go' in file_extensions:
            self.logger.info("Detected Go content - using kinfosec configuration")
            return 'go', 'kinfosec'
        else:
            # Default to general code analysis
            self.logger.info("Using general code analysis - kinfosec configuration")
            return 'general', 'kinfosec'
    
    def get_repo_metadata(self, repo_path: str) -> Dict[str, any]:
        """
        Extract metadata about the repository.
        
        Args:
            repo_path: Path to the cloned repository
            
        Returns:
            Dictionary containing repository metadata
        """
        metadata = {
            'path': repo_path,
            'total_files': 0,
            'file_types': {},
            'total_size_bytes': 0,
            'languages_detected': []
        }
        
        for root, dirs, files in os.walk(repo_path):
            # Skip hidden directories
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            
            for file in files:
                if file.startswith('.'):
                    continue
                
                metadata['total_files'] += 1
                file_path = os.path.join(root, file)
                
                # Get file size
                try:
                    metadata['total_size_bytes'] += os.path.getsize(file_path)
                except:
                    pass
                
                # Track file types
                _, ext = os.path.splitext(file)
                if ext:
                    metadata['file_types'][ext] = metadata['file_types'].get(ext, 0) + 1
        
        return metadata
    
    def cleanup(self):
        """
        Clean up all temporary directories created during repository operations.
        """
        for temp_dir in self.temp_dirs:
            try:
                if os.path.exists(temp_dir):
                    self.logger.info(f"Cleaning up temporary directory: {temp_dir}")
                    shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception as ex:
                self.logger.warning(f"Failed to cleanup directory {temp_dir}: {ex}")
        
        self.temp_dirs.clear()


# Module-level convenience functions

async def clone_and_analyze_repo(github_url: str) -> Tuple[str, str, str]:
    """
    Clone a GitHub repository and detect its content type.
    
    Args:
        github_url: GitHub repository URL
        
    Returns:
        Tuple of (repo_path, content_type, config_folder)
    """
    handler = GitHubRepoHandler()
    
    try:
        repo_path = await handler.clone_or_download_repo(github_url)
        content_type, config_folder = handler.detect_repo_content_type(repo_path)
        
        return repo_path, content_type, config_folder
    except Exception as ex:
        handler.cleanup()
        raise


def cleanup_repo(repo_path: str):
    """
    Clean up a cloned repository directory.
    
    Args:
        repo_path: Path to repository to clean up
    """
    try:
        if os.path.exists(repo_path):
            shutil.rmtree(repo_path, ignore_errors=True)
            logger.info(f"Cleaned up repository at {repo_path}")
    except Exception as ex:
        logger.warning(f"Failed to cleanup repository at {repo_path}: {ex}")
