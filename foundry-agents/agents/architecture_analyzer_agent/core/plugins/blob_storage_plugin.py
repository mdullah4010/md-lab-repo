"""
Semantic Kernel Plugin for Azure Blob Storage

This plugin provides Azure Blob Storage capabilities for Azure AI Agents, enabling them to
upload files, create markdown reports, and manage blob storage operations.
"""

import logging
import os
import json
from typing import Annotated, Optional
from datetime import datetime
from pathlib import Path
from semantic_kernel.functions import kernel_function

# Azure Blob Storage imports
from azure.storage.blob import BlobServiceClient, BlobClient
from azure.identity import DefaultAzureCredential, AzureCliCredential
from azure.identity.aio import DefaultAzureCredential as AsyncDefaultAzureCredential

# Semantic Kernel imports for Azure AI Agent
from semantic_kernel import Kernel
from semantic_kernel.agents import AzureAIAgent
from semantic_kernel.functions import KernelArguments
from semantic_kernel.contents import ChatMessageContent, AuthorRole

from .plugin_utils import load_plugin_environment, get_azure_storage_config, get_azure_openai_config
from ..agent_factory import AgentFactory

# Import tracing configuration
try:
    from tracing_config import (
        get_tracer,
        add_span_attributes,
        record_error_details
    )
    from opentelemetry.trace import Status, StatusCode
    TRACING_AVAILABLE = True
except ImportError:
    TRACING_AVAILABLE = False
    logger = logging.getLogger(__name__)
    logger.warning("Tracing not available - import failed")

logger = logging.getLogger(__name__)


class BlobStoragePlugin:
    """
    A Semantic Kernel plugin that provides Azure Blob Storage capabilities.
    
    This plugin allows Azure AI Agents to:
    1. Upload text content and files to Azure Blob Storage
    2. Create and upload markdown reports
    3. Generate structured analysis reports
    4. Manage blob storage operations and metadata
    """
    
    def __init__(self):
        """Initialize the Blob Storage plugin with credentials and configuration."""
        # Load environment configuration
        load_plugin_environment()
        
        # Get storage configuration
        storage_config = get_azure_storage_config()
        self.storage_account_name = storage_config.get("account_name")
        
        # Try AzureCliCredential first (more reliable when user is logged in via az login)
        # Fall back to DefaultAzureCredential if needed
        try:
            self.credential = AzureCliCredential()
            logger.info("[SUCCESS] Using AzureCliCredential for authentication")
        except Exception as e:
            logger.warning(f"[WARNING] AzureCliCredential failed: {str(e)}, falling back to DefaultAzureCredential")
            self.credential = DefaultAzureCredential()
        
        if not self.storage_account_name or self.storage_account_name == "your_storage_account_name_here":
            logger.warning("AZURE_STORAGE_ACCOUNT_NAME not configured - blob storage functions will not work")
            self.blob_service_client = None
        else:
            account_url = f"https://{self.storage_account_name}.blob.core.windows.net"
            self.blob_service_client = BlobServiceClient(
                account_url=account_url,
                credential=self.credential
            )
        
        # Get Azure AI Foundry configuration (uses DefaultAzureCredential for authentication)
        openai_config = get_azure_openai_config()
        self.foundry_endpoint = openai_config.get("foundry_endpoint")
        self.foundry_model = openai_config.get("foundry_model", "gpt-4o")
        
        # Validate Foundry endpoint
        if not self.foundry_endpoint:
            logger.warning("AZURE_EXISTING_AIPROJECT_ENDPOINT not configured - AI report generation will not work")
        
        # Azure AI Agent will be created on-demand when needed for report generation
        self.agent_client = None
        self.report_agent = None
    
    @kernel_function(
        description="Upload text content to Azure Blob Storage as a file with specified name and container",
        name="upload_text_content"
    )
    async def upload_text_content(
        self,
        content: Annotated[str, "The text content to upload to blob storage"],
        blob_name: Annotated[str, "Name for the blob file (e.g., 'report.txt', 'analysis.md')"],
        container_name: Annotated[str, "Container name to upload to (default: 'architecture-reports')"] = "architecture-reports",
        content_type: Annotated[str, "MIME type of the content (default: 'text/plain')"] = "text/plain"
    ) -> Annotated[str, "JSON string containing upload result with blob URL and metadata"]:
        """
        Upload text content to Azure Blob Storage.
        
        Args:
            content: The text content to upload
            blob_name: Name for the blob file
            container_name: Container to upload to
            content_type: MIME type of the content
            
        Returns:
            JSON string with upload result including blob URL and metadata
        """
        tracer = get_tracer() if TRACING_AVAILABLE else None
        span_context = tracer.start_as_current_span("blob_storage.upload_text_content") if tracer else None
        
        try:
            if span_context:
                span = span_context.__enter__()
                add_span_attributes(span, {
                    "blob_storage.blob_name": blob_name,
                    "blob_storage.container_name": container_name,
                    "blob_storage.content_length": len(content),
                    "blob_storage.content_type": content_type
                })
            if not self.blob_service_client:
                return json.dumps({
                    "status": "error",
                    "error": "Azure Blob Storage not configured",
                    "blob_url": None
                })
            
            logger.info(f"[OUTPUT] Uploading content to blob: {blob_name}")
            
            # Create container if it doesn't exist
            try:
                self.blob_service_client.create_container(container_name)
                logger.info(f"[SUCCESS] Created container: {container_name}")
            except Exception:
                logger.debug(f"Container {container_name} already exists")
            
            # Upload the content
            blob_client = self.blob_service_client.get_blob_client(
                container=container_name,
                blob=blob_name
            )
            
            blob_client.upload_blob(
                content,
                overwrite=True,
                content_type=content_type
            )
            
            # Get blob properties for metadata
            blob_properties = blob_client.get_blob_properties()
            
            result = {
                "status": "success",
                "container_name": container_name,
                "blob_name": blob_name,
                "blob_url": blob_client.url,
                "upload_timestamp": datetime.utcnow().isoformat(),
                "file_size_bytes": blob_properties.size,
                "content_type": content_type,
                "content_length": len(content)
            }
            
            logger.info(f"[SUCCESS] Successfully uploaded {len(content)} chars to {blob_client.url}")
            
            if span_context:
                add_span_attributes(span, {
                    "blob_storage.blob_url": blob_client.url[:200],
                    "blob_storage.file_size_bytes": blob_properties.size
                })
                span.set_status(Status(StatusCode.OK))
            
            return json.dumps(result)
            
        except Exception as e:
            logger.error(f"[ERROR] Blob upload failed: {str(e)}")
            
            if span_context:
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)[:256]))
            
            if TRACING_AVAILABLE and span_context:
                record_error_details(
                    span=span,
                    error_type=type(e).__name__,
                    error_message=str(e),
                    error_code=None,
                    is_retryable=True
                )
            
            return json.dumps({
                "status": "error",
                "error": str(e),
                "blob_url": None
            })
        finally:
            if span_context:
                span_context.__exit__(None, None, None)
    
    @kernel_function(
        description="Create and upload a comprehensive markdown report from architecture analysis data using AI-generated content based on security analyzer template",
        name="create_architecture_report"
    )
    async def create_architecture_report(
        self,
        design_doc_url: Annotated[str, "Blob storage path to design document being analyzed"],
        design_doc_content: Annotated[str, "Extracted design document text content"],
        architecture_analysis: Annotated[str, "JSON string containing architecture analysis results"],
        security_findings: Annotated[str, "JSON string containing security analysis and compliance findings"],
        report_title: Annotated[str, "Title for the report (default: 'Architecture Analysis Report')"] = "Architecture Analysis Report"
    ) -> Annotated[str, "JSON string containing the generated markdown report and upload result"]:
        """
        Create a comprehensive markdown report from analysis data and upload to blob storage.
        The report is generated by Azure OpenAI using the security analyzer template.
        
        Args:
            design_doc_url: Blob storage path to design document being analyzed
            design_doc_content: Extracted design document text content
            architecture_analysis: JSON string with architecture analysis results
            security_findings: JSON string with security findings
            report_title: Title for the report
            
        Returns:
            JSON string with generated report content and upload result
        """
        tracer = get_tracer() if TRACING_AVAILABLE else None
        span_context = tracer.start_as_current_span("blob_storage.create_architecture_report") if tracer else None
        
        try:
            if span_context:
                span = span_context.__enter__()
                add_span_attributes(span, {
                    "blob_storage.design_doc_url": design_doc_url[:200],
                    "blob_storage.report_title": report_title,
                    "blob_storage.architecture_analysis_length": len(architecture_analysis),
                    "blob_storage.security_findings_length": len(security_findings),
                    "blob_storage.design_doc_content_length": len(design_doc_content)
                })
            
            logger.info(f"[LOG] Creating architecture report for: {design_doc_url}")
            
            # CRITICAL FIX: Auto-sanitize JSON FIRST to remove common LLM truncation artifacts
            architecture_analysis = self._sanitize_json_string(architecture_analysis, "architecture_analysis")
            security_findings = self._sanitize_json_string(security_findings, "security_findings")
            
            # POST-SANITIZATION VALIDATION: Check for remaining issues
            logger.debug(f"[BLOB_STORAGE] ===== POST-SANITIZATION VALIDATION =====")
            validation_errors = []
            
            # Check for remaining comments after sanitization
            if "//" in architecture_analysis or "/*" in architecture_analysis:
                validation_errors.append("❌ Architecture JSON still contains comments after sanitization")
                logger.debug(f"[BLOB_STORAGE] ❌ DETECTED: Comments remain in architecture_analysis")
            if "//" in security_findings or "/*" in security_findings:
                validation_errors.append("❌ Security JSON still contains comments after sanitization")
                logger.debug(f"[BLOB_STORAGE] ❌ DETECTED: Comments remain in security_findings")
            
            # Check for obvious truncation markers
            truncation_markers = ["...", "<truncated>", "(truncated)"]
            for marker in truncation_markers:
                if marker in architecture_analysis:
                    validation_errors.append(f"❌ Architecture JSON appears truncated (contains '{marker}')")
                    logger.debug(f"[BLOB_STORAGE] ❌ DETECTED: Truncation marker '{marker}' in architecture_analysis")
                if marker in security_findings:
                    validation_errors.append(f"❌ Security JSON appears truncated (contains '{marker}')")
                    logger.debug(f"[BLOB_STORAGE] ❌ DETECTED: Truncation marker '{marker}' in security_findings")
            
            # Check if JSON starts/ends correctly
            arch_stripped = architecture_analysis.strip()
            if arch_stripped and not (arch_stripped.startswith('{') or arch_stripped.startswith('[')):
                validation_errors.append("❌ Architecture JSON doesn't start with { or [")
                logger.debug(f"[BLOB_STORAGE] ❌ DETECTED: Invalid start character in architecture_analysis: '{arch_stripped[0]}'")
            
            sec_stripped = security_findings.strip()
            if sec_stripped and not (sec_stripped.startswith('{') or sec_stripped.startswith('[')):
                validation_errors.append("❌ Security JSON doesn't start with { or [")
                logger.debug(f"[BLOB_STORAGE] ❌ DETECTED: Invalid start character in security_findings: '{sec_stripped[0]}'")
            
            if validation_errors:
                logger.debug(f"[BLOB_STORAGE] ⚠️  VALIDATION WARNINGS - {len(validation_errors)} issues after sanitization:")
                for error in validation_errors:
                    logger.debug(f"[BLOB_STORAGE]    {error}")
                logger.debug(f"[BLOB_STORAGE] " + "=" * 50)
                logger.debug(f"[BLOB_STORAGE] Note: Sanitization attempted to fix common issues.")
                logger.debug(f"[BLOB_STORAGE] If errors persist, check agent response format.")
                logger.debug(f"[BLOB_STORAGE] " + "=" * 50)
            else:
                logger.debug(f"[BLOB_STORAGE] ✅ Validation passed - JSON appears well-formed")
            
            logger.debug(f"[BLOB_STORAGE] ===================================")
            
            # Enhanced JSON validation logging
            logger.info(f"[INFO] Parsing architecture_analysis (length: {len(architecture_analysis)} chars)")
            logger.info(f"[INFO] Parsing security_findings (length: {len(security_findings)} chars)")
            
            # Log JSON details
            logger.debug(f"[BLOB_STORAGE] ===== JSON DETAILS =====")
            logger.debug(f"[BLOB_STORAGE] Architecture Analysis:")
            print(f"  - Length: {len(architecture_analysis)} chars")
            print(f"  - Type: {type(architecture_analysis)}")
            print(f"  - Starts with: {architecture_analysis[:100] if len(architecture_analysis) > 0 else 'EMPTY'}")
            print(f"  - Ends with: {architecture_analysis[-100:] if len(architecture_analysis) > 100 else architecture_analysis}")
            
            logger.debug(f"[BLOB_STORAGE] Security Findings:")
            print(f"  - Length: {len(security_findings)} chars")
            print(f"  - Type: {type(security_findings)}")
            print(f"  - Starts with: {security_findings[:100] if len(security_findings) > 0 else 'EMPTY'}")
            print(f"  - Ends with: {security_findings[-100:] if len(security_findings) > 100 else security_findings}")
            logger.debug(f"[BLOB_STORAGE] =============================")
            
            # Handle empty or placeholder strings
            if not architecture_analysis or architecture_analysis.strip() in ['', '{}', 'null']:
                logger.warning("[WARNING] Empty architecture_analysis data, using fallback")
                arch_data = {"summary": {}, "architecture_analyses": []}
            else:
                try:
                    arch_data = json.loads(architecture_analysis)
                    logger.info(f"[SUCCESS] Parsed architecture_analysis: {list(arch_data.keys())}")
                    logger.debug(f"[BLOB_STORAGE] ✅ Architecture JSON parsed successfully - keys: {list(arch_data.keys())}")
                except json.JSONDecodeError as e:
                    logger.error(f"[ERROR] Architecture analysis JSON parsing failed: {str(e)}")
                    logger.error(f"[ERROR] JSON error at position {e.pos}: {e.msg}")
                    logger.debug(f"[BLOB_STORAGE] ❌ Architecture JSON PARSE ERROR:")
                    logger.debug(f"  - Error: {str(e)}")
                    logger.debug(f"  - Position: {e.pos}")
                    logger.debug(f"  - Context: {architecture_analysis[max(0, e.pos-50):min(len(architecture_analysis), e.pos+50)]}")
                    logger.debug(f"[BLOB_STORAGE] 🔧 TROUBLESHOOTING:")
                    logger.debug(f"  1. The LLM may have truncated the JSON in its function call")
                    logger.debug(f"  2. Current max_tokens setting: check architecture_analyzer_agent.py (should be ~16384)")
                    logger.debug(f"  3. The JSON string length is {len(architecture_analysis)} chars")
                    logger.debug(f"  4. This error occurs in the agent's function call, not in blob storage")
                    logger.debug(f"  5. If JSON > 50KB, consider implementing data summarization in the agent")
                    arch_data = {"summary": {}, "architecture_analyses": [], "parse_error": str(e)}
                
            if not security_findings or security_findings.strip() in ['', '{}', 'null']:
                logger.warning("[WARNING] Empty security_findings data, using fallback")
                security_data = {"status": "fallback", "recommendations": ["No security analysis data available"]}
            else:
                try:
                    security_data = json.loads(security_findings)
                    logger.info(f"[SUCCESS] Parsed security_findings: {list(security_data.keys())}")
                    logger.debug(f"[BLOB_STORAGE] ✅ Security JSON parsed successfully - keys: {list(security_data.keys())}")
                    # Log the structure to see what we have
                    if "gap_analysis" in security_data:
                        gap = security_data["gap_analysis"]
                        logger.info(f"[DEBUG] gap_analysis keys: {list(gap.keys())}")
                        if "findings" in gap:
                            findings_list = gap['findings']
                            logger.info(f"[DEBUG] findings count: {len(findings_list)}")
                            # CRITICAL DEBUG: Show actual component values from parsed JSON
                            if len(findings_list) > 0:
                                logger.debug(f"[BLOB_STORAGE] 🔍 First 3 findings from parsed JSON:")
                                for i, finding in enumerate(findings_list[:3], 1):
                                    comp = finding.get('component', 'KEY_MISSING')
                                    scf = finding.get('scf_id', 'KEY_MISSING')
                                    logger.debug(f"[BLOB_STORAGE]   Finding {i}: component='{comp}', scf_id='{scf}'")
                except json.JSONDecodeError as e:
                    logger.error(f"[ERROR] Security findings JSON parsing failed: {str(e)}")
                    logger.error(f"[ERROR] JSON error at position {e.pos}: {e.msg}")
                    logger.debug(f"[BLOB_STORAGE] ❌ Security JSON PARSE ERROR:")
                    logger.debug(f"  - Error: {str(e)}")
                    logger.debug(f"  - Position: {e.pos}")
                    logger.debug(f"  - Context: {security_findings[max(0, e.pos-50):min(len(security_findings), e.pos+50)]}")
                    logger.debug(f"[BLOB_STORAGE] 🔧 TROUBLESHOOTING:")
                    logger.debug(f"  1. The LLM may have truncated the JSON in its function call")
                    logger.debug(f"  2. Current max_tokens setting: check architecture_analyzer_agent.py (should be ~16384)")
                    logger.debug(f"  3. The JSON string length is {len(security_findings)} chars")
                    logger.debug(f"  4. This error occurs in the agent's function call, not in blob storage")
                    logger.debug(f"  5. If JSON > 50KB, consider implementing data summarization in the agent")
                    security_data = {"status": "fallback", "recommendations": ["JSON parsing failed"], "parse_error": str(e)}
            
            # Generate comprehensive markdown report using LLM or fallback to code-based generation
            # FIXED: Use Windows-safe timestamp format (no colons) to avoid [Errno 22] Invalid argument
            timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%S")
            
            # Debug: Log parsed data structure
            logger.debug(f"[BLOB_DEBUG] arch_data keys: {list(arch_data.keys()) if isinstance(arch_data, dict) else 'NOT A DICT'}")
            logger.debug(f"[BLOB_DEBUG] security_data keys: {list(security_data.keys()) if isinstance(security_data, dict) else 'NOT A DICT'}")
            
            # Check if security findings exist
            if isinstance(security_data, dict):
                if "gap_analysis" in security_data:
                    gap = security_data["gap_analysis"]
                    if "findings" in gap:
                        logger.debug(f"[BLOB_DEBUG] Found {len(gap['findings'])} findings in gap_analysis")
                        if len(gap['findings']) > 0:
                            logger.debug(f"[BLOB_DEBUG] First finding: {gap['findings'][0]}")
                    else:
                        logger.debug(f"[BLOB_DEBUG] No 'findings' in gap_analysis. Keys: {list(gap.keys())}")
                else:
                    logger.debug(f"[BLOB_DEBUG] No 'gap_analysis' in security_data")
            
            # Try Azure AI Agent-based generation first if endpoint is configured
            if self.foundry_endpoint:
                logger.info("[INFO] Using Azure AI Foundry Agent-based report generation with template")
                print("[BLOB_DEBUG] Azure AI Foundry endpoint available - using Agent generation")
                markdown_content = await self._generate_ai_report(
                    report_title,
                    design_doc_url,
                    timestamp,
                    design_doc_content,
                    arch_data,
                    security_data
                )
            else:
                logger.warning("[WARNING] Azure AI Foundry endpoint not available, falling back to code-based generation")
                print("[BLOB_DEBUG] Azure AI Foundry endpoint NOT available - using code-based fallback")
                markdown_content = self._generate_markdown_report(
                    report_title,
                    design_doc_url,
                    timestamp,
                    design_doc_content,
                    arch_data,
                    security_data
                )
            
            # SKIP individual report upload - only return report content
            # The consolidated report will be uploaded by the architecture_agent
            logger.info(f"[SUCCESS] Generated report (upload skipped for individual reports): {len(markdown_content)} chars")
            
            if span_context:
                add_span_attributes(span, {
                    "blob_storage.report_length": len(markdown_content),
                    "blob_storage.generation_mode": "ai" if self.foundry_endpoint else "code_based"
                })
                span.set_status(Status(StatusCode.OK))
            
            result = {
                "status": "success",
                "report_title": report_title,
                "report_content": markdown_content,
                "report_length": len(markdown_content),
                "blob_url": None,  # Individual reports are not uploaded
                "generation_timestamp": timestamp,
                "message": "Report generated successfully (not uploaded - will be included in consolidated report)"
            }
            
            return json.dumps(result)
            
        except Exception as e:
            logger.error(f"[ERROR] Report creation failed: {str(e)}")
            
            if span_context:
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)[:256]))
            
            if TRACING_AVAILABLE and span_context:
                record_error_details(
                    span=span,
                    error_type=type(e).__name__,
                    error_message=str(e),
                    error_code=None,
                    is_retryable=True
                )
            
            return json.dumps({
                "status": "error",
                "error": str(e),
                "report_content": ""
            })
        finally:
            if span_context:
                span_context.__exit__(None, None, None)
    
    def _load_report_template(self) -> str:
        """Load report generation template from security_report_generator.txt"""
        try:
            # Get the path to the agent instructions file
            current_dir = Path(__file__).parent.parent
            template_path = current_dir / "agent-instructions" / "security_report_generator.txt"
            
            if template_path.exists():
                with open(template_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    logger.info(f"[SUCCESS] Loaded report template from {template_path}")
                    return content
            else:
                logger.warning(f"[WARNING] Template file not found: {template_path}")
                return ""
        except Exception as e:
            logger.error(f"[ERROR] Failed to load report template: {str(e)}")
            return ""
    
    def _summarize_architecture_data(self, arch_data: dict, max_size_kb: int = 50) -> dict:
        """Summarize architecture data to fit within size constraints.
        
        Args:
            arch_data: Full architecture analysis data
            max_size_kb: Maximum size in KB for the summarized data
            
        Returns:
            Summarized architecture data
        """
        try:
            # Start with essential summary information
            summarized = {
                "status": arch_data.get("status"),
                "design_doc_url": arch_data.get("design_doc_url"),
                "summary": arch_data.get("summary", {}),
                "text_content_length": arch_data.get("text_content_length", 0),
                "total_images": arch_data.get("total_images", 0)
            }
            
            # Add condensed architecture analyses (without full architecture_data)
            analyses = arch_data.get("architecture_analyses", [])
            if analyses:
                summarized["architecture_analyses"] = []
                for analysis in analyses:
                    condensed = {
                        "image_index": analysis.get("image_index"),
                        "image_filename": analysis.get("image_filename"),
                        "nodes_count": analysis.get("nodes_count", 0),
                        "edges_count": analysis.get("edges_count", 0),
                        "boundaries_count": analysis.get("boundaries_count", 0)
                    }
                    
                    # Include limited node/edge data if available
                    if "architecture_data" in analysis:
                        arch = analysis["architecture_data"]
                        # Only include first 10 nodes and edges
                        condensed["nodes_preview"] = arch.get("nodes", [])[:10]
                        condensed["edges_preview"] = arch.get("edges", [])[:10]
                    
                    summarized["architecture_analyses"].append(condensed)
            
            logger.info(f"[SUMMARIZE] Reduced architecture data from {len(json.dumps(arch_data))} to {len(json.dumps(summarized))} bytes")
            return summarized
        except Exception as e:
            logger.error(f"[ERROR] Architecture data summarization failed: {str(e)}")
            return {"summary": arch_data.get("summary", {}), "error": "Summarization failed"}
    
    def _summarize_security_data(self, security_data: dict, max_size_kb: int = 50) -> dict:
        """Summarize security findings to fit within size constraints.
        
        Args:
            security_data: Full security findings data
            max_size_kb: Maximum size in KB for the summarized data
            
        Returns:
            Summarized security data
        """
        try:
            summarized = {
                "status": security_data.get("status")
            }
            
            # Handle gap_analysis structure
            if "gap_analysis" in security_data:
                gap = security_data["gap_analysis"]
                summarized["gap_analysis"] = {
                    "component_to_scf": gap.get("component_to_scf", {}),
                    "summary": gap.get("summary", {})
                }
                
                # Include findings but limit size
                findings = gap.get("findings", [])
                if findings:
                    # Calculate max findings based on size
                    max_findings = min(len(findings), 100)  # Cap at 100 findings
                    summarized["gap_analysis"]["findings"] = findings[:max_findings]
                    
                    if len(findings) > max_findings:
                        summarized["gap_analysis"]["findings_truncated"] = True
                        summarized["gap_analysis"]["total_findings"] = len(findings)
                        logger.warning(f"[TRUNCATE] Truncated findings from {len(findings)} to {max_findings}")
            
            # Include recommendations if present
            if "recommendations" in security_data:
                summarized["recommendations"] = security_data["recommendations"][:10]  # Max 10 recommendations
            
            logger.info(f"[SUMMARIZE] Reduced security data from {len(json.dumps(security_data))} to {len(json.dumps(summarized))} bytes")
            return summarized
        except Exception as e:
            logger.error(f"[ERROR] Security data summarization failed: {str(e)}")
            return {"status": "error", "error": "Summarization failed"}
    
    def _sanitize_json_string(self, json_str: str, field_name: str) -> str:
        """Sanitize JSON string to remove common LLM truncation artifacts.
        
        Args:
            json_str: JSON string to sanitize
            field_name: Name of the field (for logging)
            
        Returns:
            Sanitized JSON string
        """
        try:
            import re
            original_length = len(json_str)
            
            # Remove block comments first (/* ... */)
            if "/*" in json_str:
                logger.warning(f"[SANITIZE] Removing /* */ comments from {field_name}")
                json_str = re.sub(r'/\*.*?\*/', '', json_str, flags=re.DOTALL)
            
            # Remove inline // comments (but be careful not to remove URLs like https://)
            if "//" in json_str:
                logger.warning(f"[SANITIZE] Removing // comments from {field_name}")
                lines = json_str.split('\n')
                sanitized_lines = []
                for line in lines:
                    # Check if line starts with // (full line comment)
                    if line.strip().startswith('//'):
                        continue
                    # Check for inline comments: "key": "value" // comment
                    # Use regex to find // that's not part of a URL (not preceded by :)
                    # Match // that's not part of http:// or https://
                    line = re.sub(r'(?<!:)//.*$', '', line)
                    sanitized_lines.append(line)
                json_str = '\n'.join(sanitized_lines)
            
            # Check for truncation at end (incomplete JSON)
            json_str = json_str.strip()
            if json_str and not json_str.endswith(('}', ']', '"')):
                logger.warning(f"[SANITIZE] {field_name} appears truncated (doesn't end with }}, ], or \")")
                # Try to find the last complete object/array
                if '{' in json_str:
                    # Count braces to find where valid JSON ends
                    open_count = 0
                    last_valid_pos = -1
                    for i, char in enumerate(json_str):
                        if char == '{':
                            open_count += 1
                        elif char == '}':
                            open_count -= 1
                            if open_count == 0:
                                last_valid_pos = i + 1
                    
                    if last_valid_pos > 0:
                        json_str = json_str[:last_valid_pos]
                        logger.info(f"[SANITIZE] Truncated {field_name} to last complete object (position {last_valid_pos})")
            
            if len(json_str) != original_length:
                logger.info(f"[SANITIZE] {field_name} sanitized: {original_length} -> {len(json_str)} chars")
            
            return json_str
        except Exception as e:
            logger.error(f"[ERROR] JSON sanitization failed for {field_name}: {str(e)}")
            return json_str
    
    def _check_payload_size(self, data: dict) -> tuple[int, bool]:
        """Check if payload size is acceptable for LLM API call.
        
        Args:
            data: Data dictionary to check
            
        Returns:
            Tuple of (size_in_kb, is_too_large)
        """
        try:
            json_str = json.dumps(data)
            size_kb = len(json_str.encode('utf-8')) / 1024
            # Conservative limit: 100KB for JSON payload (leaves room for template and other context)
            is_too_large = size_kb > 100
            return (int(size_kb), is_too_large)
        except Exception as e:
            logger.error(f"[ERROR] Payload size check failed: {str(e)}")
            return (0, False)
    
    async def _generate_ai_report(
        self,
        title: str,
        design_doc_url: str,
        timestamp: str,
        design_doc_content: str,
        arch_data: dict,
        security_data: dict
    ) -> str:
        """
        Generate a comprehensive markdown report using Azure AI Agent and the security analyzer template.
        
        This method uses Azure AI Agent to generate the entire report based on the template and data provided,
        ensuring consistent formatting and structure while leveraging AI for intelligent analysis.
        
        Args:
            title: Report title
            design_doc_url: Blob storage path to design document
            timestamp: Report generation timestamp
            design_doc_content: Design document text content
            arch_data: Architecture analysis data
            security_data: Security findings data
            
        Returns:
            AI-generated markdown content
        """
        try:
            # Load the report template
            template = self._load_report_template()
            
            if not template:
                logger.warning("[WARNING] Template not loaded, falling back to code-based generation")
                return self._generate_markdown_report(title, design_doc_url, timestamp, design_doc_content, arch_data, security_data)
            
            # Check payload sizes and summarize if needed
            arch_size_kb, arch_too_large = self._check_payload_size(arch_data)
            security_size_kb, security_too_large = self._check_payload_size(security_data)
            
            logger.info(f"[SIZE_CHECK] Architecture data: {arch_size_kb}KB, Security data: {security_size_kb}KB")
            logger.debug(f"[BLOB_DEBUG] Payload sizes - Arch: {arch_size_kb}KB, Security: {security_size_kb}KB")
            
            # Automatically summarize if data is too large
            processed_arch_data = arch_data
            processed_security_data = security_data
            
            if arch_too_large:
                logger.warning(f"[AUTO_SUMMARIZE] Architecture data ({arch_size_kb}KB) exceeds limit, summarizing...")
                logger.debug(f"[BLOB_DEBUG] Auto-summarizing architecture data (was {arch_size_kb}KB)")
                processed_arch_data = self._summarize_architecture_data(arch_data)
                new_size_kb, _ = self._check_payload_size(processed_arch_data)
                logger.info(f"[SUMMARIZED] Architecture data reduced to {new_size_kb}KB")
            
            if security_too_large:
                logger.warning(f"[AUTO_SUMMARIZE] Security data ({security_size_kb}KB) exceeds limit, summarizing...")
                logger.debug(f"[BLOB_DEBUG] Auto-summarizing security data (was {security_size_kb}KB)")
                processed_security_data = self._summarize_security_data(security_data)
                new_size_kb, _ = self._check_payload_size(processed_security_data)
                logger.info(f"[SUMMARIZED] Security data reduced to {new_size_kb}KB")
            
            # Build summarization note
            summarization_note = ""
            if arch_too_large or security_too_large:
                summarization_note = f"\n\nNOTE: Data was automatically summarized due to size constraints. Original sizes - Arch: {arch_size_kb}KB, Security: {security_size_kb}KB"
            
            user_message = f"""Generate a security compliance report with the following data:

REPORT METADATA:
- title: {title}
- design_doc_url: {design_doc_url}
- timestamp: {timestamp}{summarization_note}

ARCHITECTURE DATA:
{json.dumps(processed_arch_data, indent=2)}

SECURITY FINDINGS:
{json.dumps(processed_security_data, indent=2)}

DESIGN DOCUMENT CONTENT:
{design_doc_content[:2000]}{'...' if len(design_doc_content) > 2000 else ''}

Generate a complete markdown report following the structure in your instructions. Extract findings from security_data.gap_analysis.findings and create both the "All SCF Control Findings" table and "Security Deficiencies" table."""
            
            # Create Azure AI Agent for report generation
            logger.info(f"[AGENT] Creating Azure AI Foundry Agent with model: {self.foundry_model}")
            logger.debug(f"[BLOB_DEBUG] About to create Azure AI Agent")
            logger.debug(f"[BLOB_DEBUG] Template length: {len(template)} chars")
            logger.debug(f"[BLOB_DEBUG] User message length: {len(user_message)} chars")
            
            # Create agent using factory
            agent_factory = AgentFactory()
            agent = await agent_factory.create_report_generator_agent(
                template=template
            )
            
            async with AsyncDefaultAzureCredential() as credential:
                logger.info("[AGENT] Azure AI Agent created successfully")
                print("[BLOB_DEBUG] Agent created, invoking with user message")
                
                try:
                    # Invoke agent with the user message
                    agent_responses = []
                    async for response_item in agent.invoke(
                        messages=user_message,
                        temperature=0.3,
                        max_completion_tokens=16384,
                        max_prompt_tokens=100000
                    ):
                        response = response_item.message if hasattr(response_item, 'message') else response_item
                        agent_responses.append(response)
                    
                    # Combine all agent responses
                    final_response_parts = []
                    for resp in agent_responses:
                        if hasattr(resp, 'content'):
                            if isinstance(resp.content, str):
                                final_response_parts.append(resp.content)
                            elif isinstance(resp.content, list):
                                for item in resp.content:
                                    if hasattr(item, 'text'):
                                        final_response_parts.append(str(item.text))
                                    elif hasattr(item, 'content'):
                                        final_response_parts.append(str(item.content))
                        else:
                            final_response_parts.append(str(resp))
                    
                    generated_content = "\n".join(final_response_parts) if final_response_parts else ""
                    
                    if generated_content:
                        logger.info(f"[SUCCESS] Azure AI Agent generated report: {len(generated_content)} characters")
                        logger.debug(f"[BLOB_DEBUG] Agent response received: {len(generated_content)} chars")
                        return generated_content
                    else:
                        logger.error("[ERROR] No content generated by Azure AI Agent")
                        print("[BLOB_DEBUG] Empty agent response - falling back")
                        return self._generate_markdown_report(title, design_doc_url, timestamp, design_doc_content, arch_data, security_data)
                        
                except Exception as agent_error:
                    logger.error(f"[ERROR] Azure AI Agent invocation failed: {str(agent_error)}")
                    logger.debug(f"[BLOB_DEBUG] Agent exception: {type(agent_error).__name__}: {str(agent_error)}")
                    import traceback
                    traceback.print_exc()
                    return self._generate_markdown_report(title, design_doc_url, timestamp, design_doc_content, arch_data, security_data)
                
        except Exception as e:
            logger.error(f"[ERROR] Azure AI Agent report generation failed: {str(e)}")
            logger.info("[FALLBACK] Using code-based report generation")
            import traceback
            traceback.print_exc()
            return self._generate_markdown_report(title, design_doc_url, timestamp, design_doc_content, arch_data, security_data)
    
    def _generate_markdown_report(
        self, 
        title: str, 
        design_doc_url: str, 
        timestamp: str, 
        design_doc_content: str, 
        arch_data: dict, 
        security_data: dict
    ) -> str:
        """
        Generate a comprehensive markdown report from analysis data.
        Uses the security_report_generator.txt template for structure.
        
        Args:
            title: Report title
            design_doc_url: Blob storage path to design document
            timestamp: Report generation timestamp
            design_doc_content: Design document text content
            arch_data: Architecture analysis data
            security_data: Security findings data
            
        Returns:
            Formatted markdown content
        """
        # Load template instructions for reference
        template = self._load_report_template()
        
        md_content = f"""# {title}

**Design Document:** {design_doc_url}  
**Analysis Timestamp:** {timestamp}  
**Report Generated by:** Architecture Analyzer API with Semantic Kernel Agent

## Executive Summary

"""
        
        # Extract components from security data or architecture data
        components_list = []
        if isinstance(security_data, dict):
            current_arch = security_data.get("current_architecture", {})
            if isinstance(current_arch, dict):
                components_list = current_arch.get("services", [])
        
        # Add architecture summary
        summary = arch_data.get("summary", {})
        if components_list:
            md_content += f"""- **Architecture Components Analyzed:** {len(components_list)}
- **Azure Services Identified:** {', '.join(components_list)}
- **Analysis Type:** Security Compliance Analysis using SCF Controls

"""
        elif summary and summary.get('total_architectures_analyzed', 0) > 0:
            md_content += f"""- **Total Architectures Analyzed:** {summary.get('total_architectures_analyzed', 0)}
- **Total Images Processed:** {summary.get('total_images_processed', 0)}
- **Failed Analyses:** {summary.get('failed_analyses', 0)}
- **Azure Services Identified:** {len(summary.get('unique_azure_services', []))}
- **Total Components:** {summary.get('total_components_extracted', 0)}
- **Total Relationships:** {summary.get('total_relationships_extracted', 0)}

### Azure Services Inventory

{chr(10).join(f"- {service}" for service in summary.get('unique_azure_services', []))}

"""
        else:
            md_content += """- **Analysis Status:** Architecture extraction completed from design document content
- **Security Analysis:** SCF control compliance check performed
- **Note:** Detailed component analysis available in security section below

"""
        
        # Add security analysis summary
        md_content += "## Security & Compliance Analysis\n\n"
        
        # Extract component-to-SCF mapping from security_data
        component_to_scf = {}
        if isinstance(security_data, dict):
            gap_analysis = security_data.get("gap_analysis", {})
            if isinstance(gap_analysis, dict):
                component_to_scf = gap_analysis.get("component_to_scf", {})
        
        # Add Component to SCF Mapping Table
        if component_to_scf and isinstance(component_to_scf, dict):
            md_content += "### Component to SCF Control Mapping\n\n"
            md_content += "| Component | Applicable SCF Controls | Control Count |\n"
            md_content += "|-----------|------------------------|---------------|\n"
            
            for component, scf_list in component_to_scf.items():
                if isinstance(scf_list, list):
                    scf_controls = ", ".join(scf_list) if scf_list else "None"
                    control_count = len(scf_list)
                else:
                    scf_controls = "None"
                    control_count = 0
                
                # Sanitize pipe characters
                component_safe = str(component).replace("|", "/")
                scf_controls_safe = scf_controls.replace("|", "/")
                
                md_content += f"| {component_safe} | {scf_controls_safe} | {control_count} |\n"
            
            md_content += "\n"

        # Build Security Findings list from available security_data
        findings = []
        if isinstance(security_data, dict):
            if isinstance(security_data.get("findings"), list):
                findings = security_data.get("findings")
            elif isinstance(security_data.get("gap_analysis"), dict) and isinstance(security_data.get("gap_analysis").get("findings"), list):
                findings = security_data.get("gap_analysis").get("findings")
            elif isinstance(security_data.get("security_findings"), list):
                findings = security_data.get("security_findings")

        # Extract summary statistics
        gap_summary = {}
        if isinstance(security_data, dict):
            gap_analysis = security_data.get("gap_analysis", {})
            if isinstance(gap_analysis, dict):
                gap_summary = gap_analysis.get("summary", {})
        
        # Add summary statistics
        if gap_summary:
            md_content += f"""### Security Compliance Summary

- **Total SCF Controls Found:** {gap_summary.get('total_findings', 0)}
- **Applicable Controls:** {gap_summary.get('applicable_findings', 0)}
- **Non-Compliant/Not Applicable:** {gap_summary.get('not_applicable_findings', 0)}
- **Compliance Coverage:** {gap_summary.get('coverage_percentage', 0):.1f}%
- **Components Analyzed:** {gap_summary.get('components_analyzed', 0)}

"""
        
        # Helper function to sanitize markdown table content
        def _safe(s):
            try:
                return str(s).replace("|", "/").replace("\n", " ")[:200]
            except Exception:
                return ""
        
        # Add ALL FINDINGS table - shows all analyzed controls (compliant and non-compliant)
        if findings:
            # Debug: Check if findings have component field
            logger.debug(f"[BLOB_DEBUG] ===== FINDINGS ANALYSIS =====")
            logger.debug(f"[BLOB_DEBUG] Processing {len(findings)} findings for report")
            
            # Show ALL component values from first 5 findings
            for i, f in enumerate(findings[:5], 1):
                comp_value = f.get('component')
                logger.debug(f"[BLOB_DEBUG] Finding {i}: component='{comp_value}' (type={type(comp_value)}, truthy={bool(comp_value)})")
                logger.debug(f"[BLOB_DEBUG]   Full finding keys: {list(f.keys())}")
                # Print the actual component value that will be used
                final_comp = f.get("component", "Unknown")
                logger.debug(f"[BLOB_DEBUG]   Final component used: '{final_comp}'")
            
            logger.debug(f"[BLOB_DEBUG] ================================")
            
            md_content += "### All SCF Control Findings (Complete Analysis)\n\n"
            md_content += "| Component | SCF Control ID | Control Title | Description | Status | Applies |\n"
            md_content += "|-----------|----------------|---------------|-------------|--------|----------|\n"

            for f in findings:
                # Extract component field with proper fallback logic
                component = f.get("component", "Unknown")
                
                # Debug: Log ONLY THE FIRST "Unknown" component for detailed analysis
                if component == "Unknown":
                    logger.debug(f"[BLOB_DEBUG] ⚠️  CRITICAL: Finding with Unknown component detected!")
                    logger.debug(f"[BLOB_DEBUG]     This should NOT happen if findings were parsed correctly")
                    logger.debug(f"[BLOB_DEBUG]     Finding type: {type(f)}")
                    logger.debug(f"[BLOB_DEBUG]     Finding keys: {list(f.keys())}")
                    logger.debug(f"[BLOB_DEBUG]     'component' in keys: {'component' in f}")
                    logger.debug(f"[BLOB_DEBUG]     f['component'] direct access: {f['component'] if 'component' in f else 'KEY NOT FOUND'}")
                    logger.debug(f"[BLOB_DEBUG]     f.get('component'): {f.get('component')}")
                    # Only log the first one to avoid spam
                    component = "Unknown"  # Keep Unknown to identify the bug in output
                
                scf_id = f.get("scf_id") or f.get("reference") or "Unknown"
                title = f.get("title") or f.get("deficiency_title") or ""
                description = f.get("description") or f.get("threat_description") or ""
                status = f.get("status") or ("Applicable" if f.get("applies") else "Not Applicable")
                applies = "✓ Yes" if f.get("applies") else "✗ No"

                md_content += f"| {_safe(component)} | {_safe(scf_id)} | {_safe(title)} | {_safe(description)} | {_safe(status)} | {applies} |\n"
            
            md_content += f"\n**Total Controls Analyzed:** {len(findings)}\n\n"
        
        # Add DEFICIENCIES table - only non-compliant items (where applies=False or status indicates non-compliance)
        if findings:
            # Filter for deficiencies only - controls that APPLY but are NON-COMPLIANT
            # Do NOT include controls that simply don't apply (applies=False) - those aren't deficiencies
            deficiencies = [f for f in findings if f.get("applies", False) and f.get("status") not in ["Applicable", "Compliant"]]
            
            if deficiencies:
                md_content += "### Security Deficiencies (Non-Compliant Controls Only)\n\n"
                md_content += "| Component | SCF Control ID | Control Title | Description | Reason for Non-Compliance |\n"
                md_content += "|-----------|----------------|---------------|-------------|---------------------------|\n"

                for f in deficiencies:
                    component = f.get("component") or f.get("affected_assets", ["Unknown"])[0] if isinstance(f.get("affected_assets"), list) else "Unknown"
                    scf_id = f.get("scf_id") or f.get("reference") or "Unknown"
                    title = f.get("title") or f.get("deficiency_title") or ""
                    description = f.get("description") or f.get("threat_description") or ""
                    status = f.get("status", "Unknown")
                    reason = f"Control status: {status}" if status != "Unknown" else "Non-compliant with SCF requirements"

                    md_content += f"| {_safe(component)} | {_safe(scf_id)} | {_safe(title)} | {_safe(description)} | {_safe(reason)} |\n"
                
                md_content += f"\n**Total Deficiencies:** {len(deficiencies)}\n\n"
            else:
                md_content += "### Security Deficiencies (Non-Compliant Controls Only)\n\n"
                md_content += "**No deficiencies found.** All applicable SCF controls are compliant with the architecture.\n\n"

        else:
            # No findings available
            md_content += "### All SCF Control Findings\n\n"
            md_content += "**No security analysis data available.**\n\n"

        # Add recommendations based on deficiencies
        if isinstance(security_data, dict):
            recommendations = security_data.get("recommendations", [])
            if recommendations:
                md_content += "### Recommendations\n\n"
                for i, rec in enumerate(recommendations, 1):
                    md_content += f"{i}. {rec}\n"
                md_content += "\n"
        
        # Add Architecture Components and Relationships (structured) when available
        md_content += "## Architecture Components & Relationships\n\n"
        architecture_analyses = arch_data.get("architecture_analyses", []) if isinstance(arch_data, dict) else []

        if architecture_analyses:
            for ai, analysis in enumerate(architecture_analyses, start=1):
                name = analysis.get("name") or analysis.get("diagram_name") or f"Architecture {ai}"
                md_content += f"### Analysis {ai}: {name}\n\n"

                arch_payload = analysis.get("architecture_data") or analysis.get("arch") or analysis

                components = arch_payload.get("components") if isinstance(arch_payload, dict) else []
                if components:
                    md_content += "#### Components\n\n"
                    md_content += "| Component ID | Type | Name | Azure Service | Region | Notes |\n"
                    md_content += "|--------------|------|------|---------------|--------|-------|\n"
                    for ci, comp in enumerate(components, start=1):
                        comp_id = comp.get("id") or comp.get("component_id") or f"CMP_{ci:03d}"
                        comp_type = comp.get("type") or comp.get("component_type") or comp.get("role") or "N/A"
                        comp_name = comp.get("name") or comp.get("title") or ""
                        azure_service = comp.get("azure_service") or comp.get("service") or ""
                        region = comp.get("region") or comp.get("location") or ""
                        notes = comp.get("notes") or comp.get("description") or ""
                        md_content += f"| {comp_id} | {comp_type} | {comp_name} | {azure_service} | {region} | {notes} |\n"
                    md_content += "\n"

                relationships = arch_payload.get("relationships") if isinstance(arch_payload, dict) else []
                if relationships:
                    md_content += "#### Relationships\n\n"
                    md_content += "| Source Component | Target Component | Relationship Type | Notes |\n"
                    md_content += "|------------------|------------------|-------------------|-------|\n"
                    for rel in relationships:
                        src = rel.get("source") or rel.get("from") or rel.get("source_id") or ""
                        tgt = rel.get("target") or rel.get("to") or rel.get("target_id") or ""
                        rtype = rel.get("type") or rel.get("relationship") or ""
                        rnotes = rel.get("notes") or rel.get("description") or ""
                        md_content += f"| {src} | {tgt} | {rtype} | {rnotes} |\n"
                    md_content += "\n"
        else:
            md_content += "No architecture component or relationship data available in the analysis.\n\n"
        
        # Add consolidated security findings for ALL images
        md_content += "\n## Consolidated Security Findings for All Images\n\n"
        
        architecture_analyses = arch_data.get("architecture_analyses", [])
        
        if architecture_analyses:
            # Generate consolidated findings table with Image column
            md_content += "### Security Findings\n\n"
            md_content += "| Image | Deficiency ID | Severity | Status | Current Date | Deficiency Type | ControlObjective Identifier | Owner | Affected Assets | Deficiency Title | Threat Description | Proposed Mitigation |\n"
            md_content += "|-------|---------------|----------|--------|--------------|-----------------|----------------------------|-------|-----------------|------------------|--------------------|---------------------|\n"
            
            # Counter for deficiency IDs
            deficiency_counter = 1
            current_date = datetime.utcnow().strftime("%Y-%m-%d")
            
            # Iterate through all analyzed images
            for i, analysis in enumerate(architecture_analyses, 1):
                if "architecture_data" in analysis:
                    image_name = analysis.get('image_filename', f'Image {i}')
                    arch_detail = analysis["architecture_data"]
                    
                    # Extract security observations and generate findings
                    security_obs = arch_detail.get('security_observations', {})
                    nodes = arch_detail.get('nodes', [])
                    
                    # Generate findings from security observations
                    if security_obs and isinstance(security_obs, dict):
                        # Check for missing controls
                        missing_controls = security_obs.get('missing_controls', [])
                        if isinstance(missing_controls, list):
                            for control in missing_controls:
                                deficiency_id = f"ARCH-{deficiency_counter:03d}"
                                deficiency_counter += 1
                                
                                md_content += f"| {image_name} | {deficiency_id} | Medium | Open | {current_date} | Security Control | /SCF-SEC-01-01 | Security Team | {control} | Missing security control | Implement {control} | Enable {control} with appropriate configuration |\n"
                        
                        # Check for network security issues
                        network_issues = security_obs.get('network_security_issues', [])
                        if isinstance(network_issues, list):
                            for issue in network_issues:
                                deficiency_id = f"NET-{deficiency_counter:03d}"
                                deficiency_counter += 1
                                
                                md_content += f"| {image_name} | {deficiency_id} | High | Open | {current_date} | Network Security | /SCF-NETW-03-01 | Network Team | Network Components | {issue} | Network security vulnerability detected | Implement network security controls |\n"
                    
                    # Generate findings from components (nodes) - check for missing security controls
                    for node in nodes:
                        node_label = node.get('label', 'Unknown')
                        node_type = node.get('type', 'Unknown')
                        
                        # Example: Check if databases have encryption
                        if 'database' in node_label.lower() or 'sql' in node_label.lower():
                            deficiency_id = f"DATA-{deficiency_counter:03d}"
                            deficiency_counter += 1
                            
                            md_content += f"| {image_name} | {deficiency_id} | High | Open | {current_date} | Data Protection | /SCF-DATA-02-01 | Data Team | {node_label} | Verify encryption at rest and in transit | - SCF THREAT 3 (Primary): Obtain information from Cloud Environment<br>- SCF THREAT 8 (Complementary): Data exfiltration from CSP private networks | Enable encryption at rest using customer-managed keys and enforce TLS 1.2+ for all connections |\n"
                        
                        # Example: Check if network components have proper controls
                        if any(keyword in node_label.lower() for keyword in ['firewall', 'nsg', 'network', 'gateway']):
                            deficiency_id = f"NET-{deficiency_counter:03d}"
                            deficiency_counter += 1
                            
                            md_content += f"| {image_name} | {deficiency_id} | Medium | Open | {current_date} | Network Security | /SCF-NETW-03-01 | Network Team | {node_label} | Verify network security controls configuration | - SCF THREAT 4 (Complementary): Target Cloud Environment with Malware<br>- SCF THREAT 12 (Primary): Exploit misconfigured cloud infrastructure | Review and harden network security group rules and firewall policies |\n"
                
                else:
                    # Handle failed analysis
                    image_name = analysis.get('image_filename', f'Image {i}')
                    error = analysis.get('error', 'Unknown error')
                    
                    md_content += f"| {image_name} | ERR-{i:03d} | Low | Open | {current_date} | Analysis Error | N/A | Analysis Team | {image_name} | Failed to analyze image | {error} | Re-run analysis with correct configuration |\n"
            
            md_content += f"\n**Total Findings:** {deficiency_counter - 1}\n\n"
        
        # Add detailed architecture analysis per image
        md_content += "\n## Detailed Architecture Analysis Per Image\n\n"
        
        for i, analysis in enumerate(architecture_analyses, 1):
            if "architecture_data" in analysis:
                arch_detail = analysis["architecture_data"]
                image_name = analysis.get('image_filename', f'Image {i}')
                
                md_content += f"""### {image_name}

**Nodes:** {analysis.get('nodes_count', 0)}  
**Relationships:** {analysis.get('edges_count', 0)}  
**Security Boundaries:** {analysis.get('boundaries_count', 0)}  
**Pattern:** {arch_detail.get('architecture_pattern', 'Unknown')}

#### Components

| Component | Type | Category | Security Zone |
|-----------|------|----------|---------------|
"""
                
                for node in arch_detail.get('nodes', []):
                    md_content += f"| {node.get('label', 'N/A')} | {node.get('type', 'N/A')} | {node.get('category', 'N/A')} | {node.get('security_zone', 'N/A')} |\n"
                
                md_content += "\n#### Relationships\n\n| Source | Target | Protocol | Encryption |\n|--------|--------|----------|------------|\n"
                
                for edge in arch_detail.get('edges', []):
                    md_content += f"| {edge.get('source', 'N/A')} | {edge.get('target', 'N/A')} | {edge.get('protocol', 'N/A')} | {edge.get('encryption', 'N/A')} |\n"
                
                # Add security observations
                security_obs = arch_detail.get('security_observations', {})
                if security_obs:
                    md_content += "\n#### Security Observations\n\n"
                    if isinstance(security_obs, dict):
                        for key, value in security_obs.items():
                            md_content += f"- **{key.replace('_', ' ').title()}:** {value}\n"
                
                md_content += "\n---\n\n"
            else:
                # Handle failed analysis
                image_name = analysis.get('image_filename', f'Image {i}')
                md_content += f"""### {image_name} (Analysis Failed)

**Error:** {analysis.get('error', 'Unknown error')}  
**Error Type:** {analysis.get('error_type', 'Unknown')}

---

"""
        
        # Add original design document content as appendix
        md_content += f"""## Appendix: Original Design Document Content

```markdown
{design_doc_content[:5000]}{'...' if len(design_doc_content) > 5000 else ''}
```

---

**Report Generated by:** Architecture Analyzer API with Semantic Kernel Agent  
**Generation Time:** {timestamp}  
**Analysis Tools:** Azure AI Foundry, Azure AI Search, Semantic Kernel
"""
        
        return md_content
    
    @kernel_function(
        description="Read blob content from Azure Blob Storage by container and blob name",
        name="read_blob_content"
    )
    async def read_blob_content(
        self,
        container_name: Annotated[str, "Container name to read from"],
        blob_name: Annotated[str, "Name of the blob file to read"]
    ) -> Annotated[str, "JSON string containing blob content and metadata"]:
        """
        Read blob content from Azure Blob Storage.
        
        Args:
            container_name: Container to read from
            blob_name: Name of the blob to read
            
        Returns:
            JSON string with blob content and metadata
        """
        try:
            if not self.blob_service_client:
                return json.dumps({
                    "status": "error",
                    "error": "Azure Blob Storage not configured",
                    "content": None
                })
            
            logger.info(f"[READ] Reading blob: {blob_name} from container: {container_name}")
            
            # Get blob client
            blob_client = self.blob_service_client.get_blob_client(
                container=container_name,
                blob=blob_name
            )
            
            # Download blob content
            blob_data = blob_client.download_blob()
            content = blob_data.readall()
            
            # Decode if text content
            try:
                content_str = content.decode('utf-8')
            except UnicodeDecodeError:
                # Binary content - encode as base64
                import base64
                content_str = base64.b64encode(content).decode('utf-8')
            
            # Get blob properties
            blob_properties = blob_client.get_blob_properties()
            
            result = {
                "status": "success",
                "container_name": container_name,
                "blob_name": blob_name,
                "content": content_str,
                "content_length": len(content),
                "content_type": blob_properties.content_settings.content_type,
                "last_modified": blob_properties.last_modified.isoformat() if blob_properties.last_modified else None
            }
            
            logger.info(f"[SUCCESS] Successfully read blob: {len(content)} bytes")
            return json.dumps(result)
            
        except Exception as e:
            logger.error(f"[ERROR] Blob read failed: {str(e)}")
            return json.dumps({
                "status": "error",
                "error": str(e),
                "content": None
            })
    
    @kernel_function(
        description="List all blobs in a container with optional prefix filter",
        name="list_blobs_in_container"
    )
    async def list_blobs_in_container(
        self,
        container_name: Annotated[str, "Container name to list blobs from"],
        blob_prefix: Annotated[str, "Optional prefix filter for blob names (e.g., 'Design-docs/project1/')"] = ""
    ) -> Annotated[str, "JSON string containing list of blob names and metadata"]:
        """
        List all blobs in a container with optional prefix filter.
        
        Args:
            container_name: Container to list blobs from
            blob_prefix: Optional prefix filter
            
        Returns:
            JSON string with list of blobs and metadata
        """
        try:
            if not self.blob_service_client:
                return json.dumps({
                    "status": "error",
                    "error": "Azure Blob Storage not configured",
                    "blobs": []
                })
            
            logger.info(f"[LIST] Listing blobs in container: {container_name} with prefix: {blob_prefix}")
            
            # Get container client
            container_client = self.blob_service_client.get_container_client(container_name)
            
            # List blobs with prefix filter
            blobs = []
            blob_list = container_client.list_blobs(name_starts_with=blob_prefix if blob_prefix else None)
            
            for blob in blob_list:
                blobs.append({
                    "name": blob.name,
                    "size": blob.size,
                    "content_type": blob.content_settings.content_type if blob.content_settings else None,
                    "last_modified": blob.last_modified.isoformat() if blob.last_modified else None
                })
            
            result = {
                "status": "success",
                "container_name": container_name,
                "blob_prefix": blob_prefix,
                "blob_count": len(blobs),
                "blobs": blobs
            }
            
            logger.info(f"[SUCCESS] Found {len(blobs)} blobs in container")
            return json.dumps(result)
            
        except Exception as e:
            logger.error(f"[ERROR] Blob list failed: {str(e)}")
            return json.dumps({
                "status": "error",
                "error": str(e),
                "blobs": []
            })
    
    @kernel_function(
        description="Upload a file from local path to Azure Blob Storage with metadata",
        name="upload_file"
    )
    async def upload_file(
        self,
        file_path: Annotated[str, "Local file path to upload"],
        blob_name: Annotated[str, "Name for the blob file in storage"],
        container_name: Annotated[str, "Container name (default: 'files')"] = "files",
        content_type: Annotated[str, "MIME type (auto-detected if empty)"] = ""
    ) -> Annotated[str, "JSON string containing upload result with blob URL and file metadata"]:
        """
        Upload a file from local path to Azure Blob Storage.
        
        Args:
            file_path: Local file path to upload
            blob_name: Name for the blob in storage
            container_name: Container to upload to
            content_type: MIME type (auto-detected if not provided)
            
        Returns:
            JSON string with upload result and file metadata
        """
        try:
            if not self.blob_service_client:
                return json.dumps({
                    "status": "error",
                    "error": "Azure Blob Storage not configured",
                    "blob_url": None
                })
            
            logger.info(f"[OUTPUT] Uploading file: {file_path}")
            
            # Check if file exists
            if not os.path.exists(file_path):
                return json.dumps({
                    "status": "error",
                    "error": f"File not found: {file_path}",
                    "blob_url": None
                })
            
            # Auto-detect content type if not provided
            if not content_type:
                if file_path.lower().endswith('.md'):
                    content_type = "text/markdown"
                elif file_path.lower().endswith('.txt'):
                    content_type = "text/plain"
                elif file_path.lower().endswith('.json'):
                    content_type = "application/json"
                elif file_path.lower().endswith('.pdf'):
                    content_type = "application/pdf"
                else:
                    content_type = "application/octet-stream"
            
            # Create container if it doesn't exist
            try:
                self.blob_service_client.create_container(container_name)
            except Exception:
                pass  # Container might already exist
            
            # Upload the file
            blob_client = self.blob_service_client.get_blob_client(
                container=container_name,
                blob=blob_name
            )
            
            with open(file_path, 'rb') as data:
                blob_client.upload_blob(
                    data,
                    overwrite=True,
                    content_type=content_type
                )
            
            # Get file and blob metadata
            file_size = os.path.getsize(file_path)
            blob_properties = blob_client.get_blob_properties()
            
            result = {
                "status": "success",
                "local_file_path": file_path,
                "container_name": container_name,
                "blob_name": blob_name,
                "blob_url": blob_client.url,
                "upload_timestamp": datetime.utcnow().isoformat(),
                "file_size_bytes": file_size,
                "content_type": content_type,
                "blob_size_bytes": blob_properties.size
            }
            
            logger.info(f"[SUCCESS] Successfully uploaded file: {file_size} bytes to {blob_client.url}")
            return json.dumps(result)
            
        except Exception as e:
            logger.error(f"[ERROR] File upload failed: {str(e)}")
            return json.dumps({
                "status": "error",
                "error": str(e),
                "blob_url": None
            })
