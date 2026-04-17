"""
SCF (Secure Controls Framework) Search Plugin

This plugin provides Azure AI Search integration for querying the SCF knowledge base.
It allows code analyzer agents to look up security controls, threats, and recommendations
from the indexed SCF documentation.

Used primarily for:
- Terraform/Infrastructure analysis (terrasec) - High applicability
- Application code analysis (kinfosec) - Lower applicability, optional
"""

import os
import json
import logging
from typing import Optional, List, Dict, Any
from semantic_kernel.functions import kernel_function

# Azure imports
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient

logger = logging.getLogger(__name__)


class SCFSearchPlugin:
    """
    Semantic Kernel plugin for searching SCF (Secure Controls Framework) knowledge base.
    
    This plugin queries Azure AI Search to retrieve relevant security controls,
    threats, and remediation guidance from the indexed SCF documentation.
    """
    
    def __init__(
        self,
        search_endpoint: str = None,
        search_index: str = None
    ):
        """
        Initialize the SCF Search Plugin.
        
        Args:
            search_endpoint: Azure AI Search endpoint URL
            search_index: Name of the search index (default: from env)
        """
        self.search_endpoint = search_endpoint or os.getenv("AZURE_SEARCH_ENDPOINT") or os.getenv("SEARCH_ENDPOINT")
        self.search_index = search_index or os.getenv("SCF_AZURE_SEARCH_INDEX") or os.getenv("SEARCH_INDEX_NAME") or os.getenv("AZURE_SEARCH_INDEX", "")
        
        self._client: Optional[SearchClient] = None
        
        logger.info(f"SCFSearchPlugin initialized with index: {self.search_index}")
        logger.info(f"  Endpoint: {self.search_endpoint}")
        logger.info(f"  Using Managed Identity: True")
    
    def _get_client(self) -> SearchClient:
        """Get or create the search client."""
        if self._client is None:
            if not self.search_endpoint:
                raise ValueError("AZURE_SEARCH_ENDPOINT or SEARCH_ENDPOINT not configured")
            if not self.search_index:
                raise ValueError("SCF_AZURE_SEARCH_INDEX or SEARCH_INDEX_NAME not configured")
            
            # Use managed identity for authentication
            credential = DefaultAzureCredential(exclude_shared_token_cache_credential=True)
            logger.debug("Using managed identity for Azure AI Search authentication")
            
            self._client = SearchClient(
                endpoint=self.search_endpoint,
                index_name=self.search_index,
                credential=credential
            )
        
        return self._client
    
    @kernel_function(
        name="search_scf_controls",
        description="Search the SCF (Secure Controls Framework) knowledge base for security controls related to a specific Azure service or security topic. Returns SCF control IDs, descriptions, and linked threats."
    )
    def search_scf_controls(
        self,
        query: str,
        top_k: int = 5
    ) -> str:
        """
        Search for SCF security controls matching the query.
        
        Use this function to find relevant security controls from the SCF knowledge base.
        Good queries include:
        - "Azure Key Vault security baseline"
        - "Storage Account encryption requirements"
        - "Network Security Group controls"
        - "Identity and access management"
        
        Args:
            query: Search query for finding relevant SCF controls
            top_k: Maximum number of results to return (default: 5)
            
        Returns:
            JSON string containing search results with SCF controls and linked threats
        """
        try:
            client = self._get_client()
            
            logger.info(f"🔍 SCF Search: '{query}' (top_k={top_k})")
            
            # Perform the search
            results = client.search(
                search_text=query,
                top=top_k,
                include_total_count=True
            )
            
            # Process results
            scf_controls = []
            for result in results:
                control = {
                    "score": result.get("@search.score", 0),
                    "design_doc_path": result.get("design_doc_path") or result.get("path") or result.get("source", ""),
                    "title": result.get("title", ""),
                    "content": self._extract_content_preview(result.get("content", ""), max_len=500),
                    "scf_control_id": self._extract_scf_control_id(result),
                    "linked_threats": self._extract_linked_threats(result.get("content", ""))
                }
                scf_controls.append(control)
            
            response = {
                "success": True,
                "query": query,
                "total_results": len(scf_controls),
                "controls": scf_controls
            }
            
            logger.info(f"✅ SCF Search returned {len(scf_controls)} results")
            
            return json.dumps(response, indent=2)
            
        except Exception as ex:
            logger.error(f"❌ SCF Search failed: {str(ex)}")
            return json.dumps({
                "success": False,
                "query": query,
                "error": str(ex),
                "controls": []
            })
    
    @kernel_function(
        name="get_scf_control_for_resource",
        description="Get SCF security controls for a specific Azure resource type. Use this when analyzing Terraform resources to find applicable security controls."
    )
    def get_scf_control_for_resource(
        self,
        resource_type: str,
        security_concern: str = ""
    ) -> str:
        """
        Get SCF controls applicable to a specific Azure resource type.
        
        This function searches for security controls that apply to the given
        Azure resource type and optional security concern.
        
        Args:
            resource_type: Azure resource type (e.g., "azurerm_key_vault", "azurerm_storage_account")
            security_concern: Optional specific security concern (e.g., "encryption", "access control")
            
        Returns:
            JSON string with applicable SCF controls
        """
        # Map common Terraform resource types to search queries
        resource_mappings = {
            "azurerm_key_vault": "Azure Key Vault security secrets management",
            "azurerm_key_vault_secret": "secrets management SCF-IAM-04",
            "azurerm_storage_account": "Azure Storage Account encryption security",
            "azurerm_managed_disk": "Azure disk encryption data protection",
            "azurerm_network_security_group": "Network Security Group inbound outbound connectivity",
            "azurerm_subnet": "Azure subnet network segmentation",
            "azurerm_virtual_network": "Azure Virtual Network security segmentation",
            "azurerm_sql_server": "Azure SQL Database security authentication",
            "azurerm_sql_database": "Azure SQL Database encryption audit",
            "azurerm_app_service": "Azure App Service security authentication",
            "azurerm_function_app": "Azure Function security identity",
            "azurerm_container_registry": "Azure Container Registry security",
            "azurerm_kubernetes_cluster": "Azure Kubernetes AKS security",
            "azurerm_log_analytics_workspace": "Azure monitoring logging audit",
            "azurerm_role_assignment": "Azure RBAC identity access management",
            "azurerm_private_endpoint": "Azure private endpoint network security",
            "azurerm_firewall": "Azure Firewall network security",
            "azurerm_application_gateway": "Azure Application Gateway WAF security",
        }
        
        # Build search query
        base_query = resource_mappings.get(resource_type.lower(), f"Azure {resource_type} security")
        if security_concern:
            query = f"{base_query} {security_concern}"
        else:
            query = base_query
        
        logger.info(f"🔍 SCF Resource Search: {resource_type} -> '{query}'")
        
        return self.search_scf_controls(query, top_k=3)
    
    @kernel_function(
        name="get_scf_threats_for_finding",
        description="Get SCF threat catalog entries for a security finding. Use this to enrich findings with threat intelligence."
    )
    def get_scf_threats_for_finding(
        self,
        finding_type: str,
        severity: str = ""
    ) -> str:
        """
        Get SCF threat catalog entries applicable to a security finding.
        
        Args:
            finding_type: Type of security finding (e.g., "missing encryption", "hardcoded credentials")
            severity: Optional severity level to filter by
            
        Returns:
            JSON string with relevant SCF threats
        """
        # Map common finding types to threat-focused queries
        finding_mappings = {
            "hardcoded credentials": "secrets code repositories SCF THREAT 5",
            "missing encryption": "data encryption SCF THREAT",
            "public access": "public exposure unauthorized access SCF THREAT",
            "missing authentication": "authentication brute force SCF THREAT 2",
            "network exposure": "network security lateral movement SCF THREAT 15",
            "missing audit logging": "audit logging compliance SCF THREAT",
            "privilege escalation": "privilege escalation SCF THREAT 6",
            "misconfiguration": "misconfigured cloud infrastructure SCF THREAT 12",
        }
        
        # Find best matching query
        query = finding_mappings.get(finding_type.lower())
        if not query:
            # Default search
            query = f"{finding_type} security threat SCF"
        
        logger.info(f"🔍 SCF Threat Search: {finding_type} -> '{query}'")
        
        return self.search_scf_controls(query, top_k=3)
    
    def _extract_content_preview(self, content: str, max_len: int = 500) -> str:
        """Extract a preview of the content, truncating if needed."""
        if not content:
            return ""
        
        # Clean up whitespace
        content = " ".join(content.split())
        
        if len(content) <= max_len:
            return content
        
        return content[:max_len] + "..."
    
    def _extract_scf_control_id(self, result: Dict[str, Any]) -> str:
        """Extract SCF control ID from search result."""
        # Try to extract from design_doc_path or title
        design_doc_path = result.get("design_doc_path") or result.get("path") or ""
        title = result.get("title", "")
        
        import re
        
        # Look for SCF control patterns like SCF-IAM-01, SCF-DATA-02, etc.
        patterns = [
            r'SCF[-\s]?([A-Z]{2,5})[-\s]?(\d{1,2})',  # SCF-IAM-01, SCF DATA 02
            r'(IAM|DATA|NETW|AUD|SEC|TVM|RES|SBX)[-\s]?(\d{1,2})',  # IAM-01, DATA-02
        ]
        
        for text in [design_doc_path, title]:
            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    domain = match.group(1).upper()
                    number = match.group(2).zfill(2)
                    return f"SCF-{domain}-{number}"
        
        return ""
    
    def _extract_linked_threats(self, content: str) -> List[Dict[str, str]]:
        """Extract linked threats from SCF content."""
        if not content:
            return []
        
        import re
        
        threats = []
        
        # Look for SCF THREAT patterns
        # Pattern: SCF THREAT X: description or SCF THREAT X (Primary/Complementary)
        pattern = r'SCF\s+THREAT\s+(\d+)[:\s]+([^|<\n]+?)(?:\s*\((Primary|Complementary)\))?'
        
        matches = re.findall(pattern, content, re.IGNORECASE)
        
        for match in matches[:5]:  # Limit to first 5 threats
            threat_num = match[0]
            description = match[1].strip()[:200]  # Limit description length
            designation = match[2] if len(match) > 2 else "Primary"
            
            threats.append({
                "threat_id": f"SCF THREAT {threat_num}",
                "description": description,
                "designation": designation or "Primary"
            })
        
        return threats


# Factory function for creating the plugin
def create_scf_search_plugin(
    search_endpoint: str = None,
    search_index: str = None
) -> SCFSearchPlugin:
    """
    Factory function to create an SCF Search Plugin instance.
    
    Args:
        search_endpoint: Optional Azure AI Search endpoint
        search_index: Optional index name
        
    Returns:
        Configured SCFSearchPlugin instance
    """
    return SCFSearchPlugin(
        search_endpoint=search_endpoint,
        search_index=search_index
    )
