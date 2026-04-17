"""
Semantic Kernel Plugins Package

This package contains Semantic Kernel plugins for Azure AI Agents to interact with:
- Design Document Extraction and Analysis (via foundry_image_analyzer)
- Azure Blob Storage for report generation and file uploads

These plugins enable Azure AI Agents to perform comprehensive architecture analysis workflows.
"""

from .blob_storage_plugin import BlobStoragePlugin
from . import plugin_utils
# Design document extraction function is in foundry_image_analyzer module
from .foundry_image_analyzer import extract_and_analyze_architecture

__all__ = [
    "BlobStoragePlugin",
    "plugin_utils",
    "extract_and_analyze_architecture"
]