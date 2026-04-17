"""
Azure AI Search Index Validator

This module provides utility functions to validate Azure AI Search indexes
before proceeding with code analysis. It checks:
1. SCF index exists and has indexed documents
2. Code search index exists and is accessible from the configured endpoint

These validations ensure the code analyzer has all required dependencies
before starting the analysis workflow.
"""

import os
import logging
from typing import Tuple, Optional
from dataclasses import dataclass

from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import ResourceNotFoundError, HttpResponseError
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient

from dotenv import load_dotenv

load_dotenv()

# Try to import project logger, fallback to standard logging
try:
    from agents.logging_config import get_logger
    logger = get_logger(__name__)
except ImportError:
    logger = logging.getLogger(__name__)


@dataclass
class IndexValidationResult:
    """Result of an index validation check."""
    is_valid: bool
    index_name: str
    document_count: int
    error_message: Optional[str] = None


class IndexValidator:
    """
    Validates Azure AI Search indexes for the code analyzer.
    
    This class checks:
    1. SCF index existence and content (required for security controls)
    2. Code search index existence (required for code queries)
    """
    
    def __init__(
        self,
        search_endpoint: str = None,
        search_api_key: str = None
    ):
        """
        Initialize the index validator.
        
        Args:
            search_endpoint: Azure AI Search endpoint URL
            search_api_key: Optional API key for authentication
        """
        self.search_endpoint = search_endpoint or os.getenv("AZURE_SEARCH_ENDPOINT")
        self.search_api_key = search_api_key or os.getenv("AZURE_SEARCH_API_KEY")
        
        if not self.search_endpoint:
            raise ValueError("AZURE_SEARCH_ENDPOINT environment variable is required")
        
        logger.info(f"IndexValidator initialized with endpoint: {self.search_endpoint}")
    
    def _get_credential(self):
        """Get the appropriate credential based on configuration."""
        if self.search_api_key:
            return AzureKeyCredential(self.search_api_key)
        return DefaultAzureCredential(exclude_shared_token_cache_credential=True)
    
    def _get_index_client(self) -> SearchIndexClient:
        """Get a SearchIndexClient for index operations."""
        return SearchIndexClient(
            endpoint=self.search_endpoint,
            credential=self._get_credential()
        )
    
    def _get_search_client(self, index_name: str) -> SearchClient:
        """Get a SearchClient for a specific index."""
        return SearchClient(
            endpoint=self.search_endpoint,
            index_name=index_name,
            credential=self._get_credential()
        )
    
    def validate_scf_index(self) -> IndexValidationResult:
        """
        Validate that the SCF index exists and has indexed documents.
        
        The SCF index is required for security controls framework lookups
        during code analysis.
        
        Returns:
            IndexValidationResult with validation status and document count
        """
        scf_index_name = os.getenv("SCF_AZURE_SEARCH_INDEX") or os.getenv("SEARCH_INDEX_NAME")
        
        if not scf_index_name:
            logger.error("❌ SCF_AZURE_SEARCH_INDEX or SEARCH_INDEX_NAME environment variable is not set")
            return IndexValidationResult(
                is_valid=False,
                index_name="",
                document_count=0,
                error_message="SCF_AZURE_SEARCH_INDEX or SEARCH_INDEX_NAME environment variable is not set. Please configure the SCF index name."
            )
        
        logger.info(f"🔍 Validating SCF index: {scf_index_name}")
        
        try:
            # Check if index exists
            index_client = self._get_index_client()
            try:
                index = index_client.get_index(scf_index_name)
                logger.info(f"✅ SCF index '{scf_index_name}' found")
            except ResourceNotFoundError:
                logger.error(f"❌ SCF index '{scf_index_name}' does not exist")
                return IndexValidationResult(
                    is_valid=False,
                    index_name=scf_index_name,
                    document_count=0,
                    error_message=f"SCF index '{scf_index_name}' does not exist. Please create and populate the SCF index before running code analysis."
                )
            
            # Check if index has documents
            search_client = self._get_search_client(scf_index_name)
            results = search_client.search(
                search_text="*",
                top=1,
                include_total_count=True
            )
            
            # Get the total count
            doc_count = results.get_count()
            
            if doc_count is None or doc_count == 0:
                logger.error(f"❌ SCF index '{scf_index_name}' exists but has no documents")
                return IndexValidationResult(
                    is_valid=False,
                    index_name=scf_index_name,
                    document_count=0,
                    error_message=f"SCF index '{scf_index_name}' exists but contains no indexed documents. Please populate the SCF index before running code analysis."
                )
            
            logger.info(f"✅ SCF index '{scf_index_name}' validated with {doc_count} documents")
            return IndexValidationResult(
                is_valid=True,
                index_name=scf_index_name,
                document_count=doc_count
            )
            
        except HttpResponseError as e:
            logger.error(f"❌ Failed to validate SCF index: {e}")
            return IndexValidationResult(
                is_valid=False,
                index_name=scf_index_name,
                document_count=0,
                error_message=f"Failed to access SCF index '{scf_index_name}': {str(e)}"
            )
        except Exception as e:
            logger.error(f"❌ Unexpected error validating SCF index: {e}")
            return IndexValidationResult(
                is_valid=False,
                index_name=scf_index_name,
                document_count=0,
                error_message=f"Unexpected error validating SCF index: {str(e)}"
            )
    
    def validate_code_search_index(self, index_name: str = None) -> IndexValidationResult:
        """
        Validate that the code search index exists and is accessible.
        
        This validates that SCF_AZURE_SEARCH_INDEX is part of and accessible
        from the AZURE_SEARCH_ENDPOINT.
        
        Args:
            index_name: Optional index name override. If not provided, uses env var.
            
        Returns:
            IndexValidationResult with validation status
        """
        code_index_name = index_name or os.getenv("SCF_AZURE_SEARCH_INDEX", "scfindex")
        
        logger.info(f"🔍 Validating code search index: {code_index_name}")
        logger.info(f"   Endpoint: {self.search_endpoint}")
        
        try:
            # Check if index exists in the configured endpoint
            index_client = self._get_index_client()
            try:
                index = index_client.get_index(code_index_name)
                logger.info(f"✅ Code search index '{code_index_name}' found at endpoint")
            except ResourceNotFoundError:
                logger.error(f"❌ Code search index '{code_index_name}' not found at {self.search_endpoint}")
                return IndexValidationResult(
                    is_valid=False,
                    index_name=code_index_name,
                    document_count=0,
                    error_message=f"Code search index '{code_index_name}' is not part of the Azure Search endpoint '{self.search_endpoint}'. Please verify the index name and endpoint configuration."
                )
            
            # Get document count
            search_client = self._get_search_client(code_index_name)
            results = search_client.search(
                search_text="*",
                top=1,
                include_total_count=True
            )
            
            doc_count = results.get_count() or 0
            
            logger.info(f"✅ Code search index '{code_index_name}' validated with {doc_count} documents")
            return IndexValidationResult(
                is_valid=True,
                index_name=code_index_name,
                document_count=doc_count
            )
            
        except HttpResponseError as e:
            logger.error(f"❌ Failed to validate code search index: {e}")
            return IndexValidationResult(
                is_valid=False,
                index_name=code_index_name,
                document_count=0,
                error_message=f"Failed to access code search index '{code_index_name}' at endpoint '{self.search_endpoint}': {str(e)}"
            )
        except Exception as e:
            logger.error(f"❌ Unexpected error validating code search index: {e}")
            return IndexValidationResult(
                is_valid=False,
                index_name=code_index_name,
                document_count=0,
                error_message=f"Unexpected error validating code search index: {str(e)}"
            )
    
    def validate_all_indexes(self, require_scf: bool = True, require_code_index: bool = True) -> Tuple[bool, str]:
        """
        Validate all required indexes for code analysis.
        
        Args:
            require_scf: Whether SCF index is required (True for terraform analysis)
            require_code_index: Whether code search index is required
            
        Returns:
            Tuple of (all_valid: bool, message: str)
        """
        messages = []
        all_valid = True
        
        if require_scf:
            scf_result = self.validate_scf_index()
            if not scf_result.is_valid:
                all_valid = False
                messages.append(f"SCF Index Error: {scf_result.error_message}")
            else:
                messages.append(f"✅ SCF index validated ({scf_result.document_count} documents)")
        
        if require_code_index:
            code_result = self.validate_code_search_index()
            if not code_result.is_valid:
                all_valid = False
                messages.append(f"Code Search Index Error: {code_result.error_message}")
            else:
                messages.append(f"✅ Code search index validated ({code_result.document_count} documents)")
        
        return all_valid, "\n".join(messages)


class CodeAnalyzerValidationError(Exception):
    """Exception raised when code analyzer validation fails."""
    
    def __init__(self, message: str, validation_type: str = "general"):
        self.message = message
        self.validation_type = validation_type
        super().__init__(self.message)


def validate_scf_index_ready() -> bool:
    """
    Quick check if SCF index is ready for code analysis.
    
    Returns:
        True if SCF index exists and has documents, False otherwise
        
    Raises:
        CodeAnalyzerValidationError: If validation fails with details
    """
    try:
        validator = IndexValidator()
        result = validator.validate_scf_index()
        
        if not result.is_valid:
            raise CodeAnalyzerValidationError(
                message=result.error_message or "SCF index is not ready",
                validation_type="scf_index"
            )
        
        return True
        
    except ValueError as e:
        raise CodeAnalyzerValidationError(
            message=str(e),
            validation_type="configuration"
        )


def validate_code_search_index_ready(index_name: str = None) -> bool:
    """
    Quick check if code search index is accessible from the configured endpoint.
    
    Args:
        index_name: Optional index name override
        
    Returns:
        True if index exists and is accessible, False otherwise
        
    Raises:
        CodeAnalyzerValidationError: If validation fails with details
    """
    try:
        validator = IndexValidator()
        result = validator.validate_code_search_index(index_name)
        
        if not result.is_valid:
            raise CodeAnalyzerValidationError(
                message=result.error_message or "Code search index is not accessible",
                validation_type="code_search_index"
            )
        
        return True
        
    except ValueError as e:
        raise CodeAnalyzerValidationError(
            message=str(e),
            validation_type="configuration"
        )
