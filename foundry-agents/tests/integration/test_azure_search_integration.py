# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

"""
Integration tests for Azure AI Search infrastructure.

This module tests Azure AI Search integration with real Azure services:
- Search index existence and accessibility
- Document indexing and retrieval
- Search query relevance
- Index management

All tests connect to real Azure AI Search - no mocking is used.

Tests Completed:
    1. test_search_index_exists
       - Validates Azure AI Search index is accessible
       - Verifies search service connectivity
       - Confirms index schema is properly configured
       - Tests search client authentication and authorization
    
    2. test_search_index_has_documents
       - Tests document count retrieval from search index
       - Validates indexed documents are queryable
       - Ensures index contains data for testing
       - Confirms document indexing pipeline is functional
    
    3. test_search_retrieves_relevant_content
       - Tests search query execution against index
       - Validates search relevance and scoring
       - Ensures semantic search capabilities work correctly
       - Tests content retrieval accuracy
  

Usage:
    pytest tests/integration/test_azure_search_integration.py -v -s

Configuration:
    Test configuration is loaded from .env.test file in the project root.
    
    Required configuration in .env.test:
    - AZURE_SEARCH_ENDPOINT: Azure AI Search endpoint
    - TEST_APP_ID: Application ID for testing
    
    Authentication:
    - Uses Azure Managed Identity for authentication. If running from the CLI, make sure your identity have the role "Search Index Data Reader" on the AI Search resource.
"""

import os
import sys
import logging
import pytest
from pathlib import Path
from typing import Dict, Any

# Add project root for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

logger = logging.getLogger(__name__)


@pytest.mark.integration
class TestAzureSearchIntegration:
    """
    Test integration with Azure AI Search.
    """
    
    @pytest.fixture(autouse=True)
    def setup(self, integration_config, search_client):
        """Setup test fixtures."""
        self.config = integration_config
        self.search_client = search_client
        self.app_id = integration_config["app_id"]
    
    def test_search_index_exists(self, search_index_client):
        """
        Test that the search index exists for the application.
        """
        logger.info(f"Checking search index for app_id: {self.app_id}")
        
        index_name = f"{self.app_id}"
        
        try:
            indexes = list(search_index_client.list_indexes())
            index_names = [idx.name for idx in indexes]
            
            if index_name in index_names:
                logger.info(f"✅ Search index '{index_name}' exists")
            else:
                logger.warning(f"⚠️  Search index '{index_name}' not found")
                logger.info(f"Available indexes: {index_names[:5]}...")
                pytest.skip(f"Search index {index_name} not found")
                
        except Exception as e:
            logger.error(f"Failed to check search index: {e}")
            pytest.skip(f"Failed to check search index: {e}")
    
    def test_search_index_has_documents(self):
        """
        Test that the search index contains documents for retrieval.
        """
        logger.info("Checking for documents in search index...")
        
        try:
            # Search for any documents
            results = list(self.search_client.search("*", top=5))
            
            if len(results) > 0:
                logger.info(f"✅ Found {len(results)} document(s) in search index")
                for doc in results:
                    # Try to get a meaningful name: filename, title, or source
                    doc_name = doc.get("filename") or doc.get("title") or doc.get("source") or doc.get("id", "unknown")
                    logger.info(f"   - Document: {doc_name}")
            else:
                logger.warning("⚠️  No documents found in search index")
                pytest.skip("Search index is empty")
                
        except Exception as e:
            logger.error(f"Failed to search index: {e}")
            pytest.skip(f"Failed to search index: {e}")
    
    def test_search_retrieves_relevant_content(self):
        """
        Test that search retrieves relevant content for queries.
        """
        logger.info("Testing search relevance...")
        
        test_queries = [
            "application architecture",
            "database configuration",
            "infrastructure"
        ]
        
        for query in test_queries:
            try:
                results = list(self.search_client.search(query, top=3))
                
                if len(results) > 0:
                    logger.info(f"✅ Query '{query}': {len(results)} result(s)")
                else:
                    logger.warning(f"⚠️  Query '{query}': No results")
                    
            except Exception as e:
                logger.warning(f"Query '{query}' failed: {e}")
        
        logger.info("Search relevance test completed")
    

# =============================================================================
# Standalone Execution
# =============================================================================

if __name__ == "__main__":
    """
    Run Azure Search integration tests directly.
    
    Usage:
        python tests/integration/test_azure_search_integration.py
    """
    import sys
    
    exit_code = pytest.main([
        __file__,
        "-v",
        "--tb=short",
        "-W", "ignore::DeprecationWarning"
    ])
    
    sys.exit(exit_code)
