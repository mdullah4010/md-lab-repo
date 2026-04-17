"""
Kernel Plugin for Code Analyzer Operations - Pure Semantic Kernel Implementation

This plugin provides kernel functions to manage code analysis workflows:
- Analyze code from repositories (GitHub, GitLab, Azure DevOps, Bitbucket, Blob)
- Upload analysis reports to Azure Storage
- Extract and upload reports from analysis results

Uses the SemanticKernelCodeAnalyzer for all analysis operations.
"""

import os
import sys
import json
import hashlib
import uuid
from typing import Optional
from datetime import datetime
from dotenv import load_dotenv

# Setup logging configuration - Intake uses agents. prefix
from agents.logging_config import get_logger
logger = get_logger(__name__)

# Azure Storage imports
from azure.storage.blob.aio import BlobServiceClient
from azure.identity.aio import DefaultAzureCredential

# Import semantic kernel functions decorator
from semantic_kernel.functions import kernel_function

# Import tracing configuration - Intake uses agents. prefix
from agents.tracing_config import (
    get_tracer,
    add_span_attributes,
    record_agent_interaction,
    record_error_details
)
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

# Import the new pure Semantic Kernel code analyzer - Intake uses agents. prefix
from agents.code_analyzer.semantic_kernel_analyzer import (
    SemanticKernelCodeAnalyzer,
    CodeAnalyzerConfig,
    run_code_analysis
)

# Import the deterministic codebase analyzer (no LLM required)
from agents.code_analyzer.src.codebase_analyzer import (
    CodebaseAnalyzer,
    analyze_codebase,
    get_codebase_markdown_section
)

# Import repository handler - now in Agents folder directly
from agents.code_analyzer.repo_handler import GitHubRepoHandler, cleanup_repo

# Load environment variables
load_dotenv()


class CodeAnalyzerPlugin:
    """
    Kernel plugin for managing code analyzer operations.
    
    This plugin encapsulates all code analysis lifecycle management using
    the pure Semantic Kernel implementation (SemanticKernelCodeAnalyzer).
    """
    
    def __init__(self, operation_id: str = None, app_id: str = None):
        self.logger = get_logger(f"{__name__}.{self.__class__.__name__}")
        self.operation_id = operation_id
        self.app_id = app_id
        self.blob_url = None
        # Store last analysis result for reliable access (not relying on AI to pass JSON correctly)
        self._last_analysis_result = None
        self._last_content_type = None
        self._last_repo_url = None
        self._analyzer = None  # Store analyzer instance for deferred cleanup
        self._codebase_analysis = None  # Store deterministic codebase analysis result
        self.logger.info(f"Code Analyzer Plugin initialized with operation_id: {operation_id}, app_id: {app_id}")
    
    async def _update_operation_progress(self, step_name: str, progress: int, status=None):
        """
        Helper method to update operation progress in the tracking table.
        
        Args:
            step_name: Description of the current step
            progress: Progress percentage (0-100)
            status: Optional OperationStatus to set
        """
        if not self.operation_id:
            self.logger.debug(f"No operation_id set, skipping progress update: {step_name} ({progress}%)")
            return
        
        if not self.app_id:
            self.logger.debug(f"No app_id set, skipping progress update: {step_name} ({progress}%)")
            return
        
        try:
            from operation_service import get_operation_service
            from operation_models import OperationStatus
            
            operation_service = get_operation_service()
            operation = await operation_service.get_operation(self.operation_id, self.app_id)
            
            if operation:
                operation.update_progress(step_name, progress, status or operation.status)
                await operation_service.update_operation(operation)
                self.logger.info(f"📊 Progress update: {step_name} ({progress}%)")
            else:
                self.logger.warning(f"⚠️ Operation {self.operation_id} not found for progress update")
        except Exception as ex:
            self.logger.error(f"❌ Failed to update progress for {self.operation_id}: {ex}")
    
    @kernel_function(
        name="analyze_code_from_repo",
        description="Analyze code from a repository (GitHub, GitLab, Azure DevOps, Bitbucket) or Azure Blob Storage URL using AI agents with automatic content detection and security scanning."
    )
    async def analyze_code_from_repo(
        self, 
        repo_url: str,
        perform_security_scan: bool = True
    ) -> str:
        """
        Execute code analysis from a repository or blob storage with automatic content type detection.
        
        This function supports multiple source types:
        - GitHub repositories (https://github.com/user/repo)
        - GitLab repositories (https://gitlab.com/user/repo)
        - Azure DevOps repositories (https://dev.azure.com/org/project/_git/repo)
        - Bitbucket repositories (https://bitbucket.org/user/repo)
        - Azure Blob Storage URLs (https://account.blob.core.windows.net/container/path)
        
        This function:
        1. Clones/downloads the repository or downloads from blob storage
        2. Detects content type (Terraform vs. other languages)
        3. Selects appropriate configuration folder (terrasec or kinfosec)
        4. Runs analysis using SemanticKernelCodeAnalyzer
        5. Cleans up temporary files
        
        Args:
            repo_url: Repository URL (GitHub, GitLab, etc.) or Azure Blob Storage URL
            perform_security_scan: Whether to scan for secrets before upload
            
        Returns:
            JSON string containing analysis results and agent conversation
        """
        tracer = get_tracer()
        with tracer.start_as_current_span("analyze_code_from_repo") as span:
            self.logger.info(f"Starting code analysis for repo: {repo_url}")
            
            add_span_attributes(span, {
                "code_analysis.repo_url": repo_url[:200],
                "code_analysis.security_scan": perform_security_scan,
                "code_analysis.operation": "analyze_repo"
            })
            
            repo_handler = GitHubRepoHandler()
            repo_path = None
            
            try:
                # Step 1: Clone or download the repository (25%)
                span.add_event("cloning_repository")
                self.logger.info("Cloning/downloading repository...")
                await self._update_operation_progress("Cloning repository", 25)
                repo_path = await repo_handler.clone_or_download_repo(repo_url)
                
                add_span_attributes(span, {
                    "code_analysis.repo_path": repo_path[:200]
                })
                
                # Step 2: Detect content type and determine config folder (30%)
                span.add_event("detecting_content_type")
                self.logger.info("Detecting repository content type...")
                await self._update_operation_progress("Detecting content type", 30)
                content_type, config_folder = repo_handler.detect_repo_content_type(repo_path)
                
                add_span_attributes(span, {
                    "code_analysis.content_type": content_type,
                    "code_analysis.config_folder": config_folder
                })
                
                self.logger.info(f"Detected content type: {content_type}, using config: {config_folder}")
                
                # Step 3: Get repository metadata (32%)
                await self._update_operation_progress(f"Analyzing {content_type} repository", 32)
                repo_metadata = repo_handler.get_repo_metadata(repo_path)
                self.logger.info(f"Repository metadata: {repo_metadata['total_files']} files, "
                               f"{repo_metadata['total_size_bytes']} bytes")
                
                # Step 3.5: Run deterministic codebase analysis (no LLM) (35%)
                span.add_event("running_codebase_analysis")
                self.logger.info("Running deterministic codebase analysis...")
                await self._update_operation_progress("Analyzing codebase structure", 35)
                
                try:
                    codebase_analyzer = CodebaseAnalyzer(repo_path)
                    self._codebase_analysis = codebase_analyzer.analyze()
                    
                    self.logger.info(f"📊 Codebase analysis complete: "
                                   f"{self._codebase_analysis.total_files} files, "
                                   f"{self._codebase_analysis.total_lines} LOC, "
                                   f"{len(self._codebase_analysis.frameworks)} frameworks, "
                                   f"{len(self._codebase_analysis.classes)} classes")
                    
                    add_span_attributes(span, {
                        "codebase_analysis.total_files": self._codebase_analysis.total_files,
                        "codebase_analysis.total_lines": self._codebase_analysis.total_lines,
                        "codebase_analysis.frameworks_count": len(self._codebase_analysis.frameworks),
                        "codebase_analysis.dependencies_count": len(self._codebase_analysis.dependencies),
                        "codebase_analysis.classes_count": len(self._codebase_analysis.classes)
                    })
                except Exception as cba_ex:
                    self.logger.warning(f"⚠️ Codebase analysis failed (non-fatal): {cba_ex}")
                    self._codebase_analysis = None
                
                # Step 4: Resolve config path (38%)
                await self._update_operation_progress("Resolving configuration", 38)
                # Path: agents/code_analyzer_plugin.py -> agents/code_analyzer/{config_folder}/config.json
                agents_path = os.path.dirname(__file__)
                code_analyzer_path = os.path.join(agents_path, 'code_analyzer')
                config_path = os.path.join(code_analyzer_path, config_folder, 'config.json')
                
                if not os.path.exists(config_path):
                    raise FileNotFoundError(f"Config file not found: {config_path}")
                
                # Step 5: Run analysis using SemanticKernelCodeAnalyzer (40%)
                # Note: No need to clear old reports - analyzer now uses temp directories
                span.add_event("starting_analysis", {
                    "config_folder": config_folder,
                    "content_type": content_type
                })
                
                await self._update_operation_progress("Starting AI agent analysis", 40)
                self.logger.info(f"Running analysis with config: {config_path}")
                
                result, analyzer = await run_code_analysis(
                    files_path=repo_path,
                    config_path=config_path,
                    perform_security_scan=perform_security_scan,
                    progress_callback=self._update_operation_progress,  # Pass progress callback
                    app_id=self.app_id  # Pass app_id for dynamic agent naming
                )
                
                # Update progress: Analysis complete, preparing results (85%)
                await self._update_operation_progress("Analysis complete, preparing report", 85)
                
                # Store analyzer instance for cleanup AFTER report upload
                self._analyzer = analyzer
                
                # Add repo-specific metadata to result
                result["repo_url"] = repo_url
                result["content_type"] = content_type
                result["config_folder"] = config_folder
                result["repo_metadata"] = repo_metadata
                
                # Store content type and repo url for quick access
                self._last_content_type = content_type
                self._last_repo_url = repo_url
                
                # Log what was created
                report_file = result.get('report_file')
                created_files = result.get('created_files', [])
                tool_calls = result.get('tool_calls', [])
                tool_summary = result.get('tool_call_summary', {})
                
                self.logger.info(f"📁 Created files ({len(created_files)}): {created_files}")
                self.logger.info(f"📄 Report file: {report_file}")
                self.logger.info(f"🔧 Tool calls: {tool_summary.get('total', 0)} total, list has {len(tool_calls)} entries")
                
                if not report_file:
                    self.logger.warning("⚠️ No report file was created - inner agent may have failed to generate output")
                
                span.set_status(Status(StatusCode.OK))
                self.logger.info("Repository analysis completed successfully")
                
                # Build clean result - remove empty/None values for cleaner response
                clean_result = {
                    "result": "success",
                    "repo_url": repo_url,
                    "content_type": content_type,
                    "config_folder": config_folder,
                    "message": "Code analysis completed successfully"
                }
                
                # Add repo metadata (remove empty fields)
                if repo_metadata:
                    clean_result["repo_metadata"] = {k: v for k, v in repo_metadata.items() if v}
                
                # Add analysis summary (cleaned)
                analysis_summary = {
                    "status": result.get("status"),
                    "agents_used": result.get("agents_used", []),
                    "files_processed": result.get("files_processed", 0),
                    "report_file": report_file,
                    "created_files": created_files,
                }
                
                # Add security scan results if performed
                security_scan = result.get("security_scan")
                if security_scan and security_scan.get("performed", True):
                    analysis_summary["security_scan"] = security_scan
                
                # Add tool calls for transparency
                # Check both the list AND the summary (in case list is empty but summary has counts)
                if tool_calls or tool_summary.get("total", 0) > 0:
                    # Summarize tool calls (don't include full args/results to keep response clean)
                    tool_call_list = []
                    for tc in tool_calls:
                        tool_call_list.append({
                            "function": f"{tc.get('plugin', '')}.{tc.get('function', '')}",
                            "agent": tc.get("agent"),
                            "success": tc.get("success", True),
                            "duration_ms": tc.get("duration_ms")
                        })
                    analysis_summary["tool_calls"] = tool_call_list
                    analysis_summary["tool_call_summary"] = tool_summary
                
                clean_result["analysis_summary"] = analysis_summary
                
                # Add codebase analysis (deterministic - no LLM)
                if self._codebase_analysis:
                    clean_result["codebase_analysis"] = {
                        "total_files": self._codebase_analysis.total_files,
                        "total_lines": self._codebase_analysis.total_lines,
                        "total_bytes": self._codebase_analysis.total_bytes,
                        "language_breakdown": self._codebase_analysis.language_breakdown,
                        "frameworks": [
                            {"name": f.name, "category": f.category, "package": f.package, "version": f.version}
                            for f in self._codebase_analysis.frameworks
                        ],
                        "dependencies_count": len(self._codebase_analysis.dependencies),
                        "classes_count": len(self._codebase_analysis.classes),
                        "mvc_classification": {
                            layer: len(files) 
                            for layer, files in self._codebase_analysis.mvc_classification.items() 
                            if files
                        },
                        "mermaid_class_diagram": self._codebase_analysis.mermaid_class_diagram,
                        "mermaid_dependency_diagram": self._codebase_analysis.mermaid_dependency_diagram,
                    }
                    # Store markdown section for report injection
                    clean_result["codebase_analysis_markdown"] = self._codebase_analysis.to_markdown_section()
                
                # Include messages for fallback report extraction (store ALL messages, not just last 3)
                messages = result.get("messages", [])
                if messages:
                    clean_result["message_count"] = len(messages)
                    # Store ALL messages for reliable report extraction fallback
                    # This is needed when agent generates report inline instead of using create_file
                    clean_result["messages"] = messages
                
                # Store the clean result in plugin instance for reliable access by orchestrator
                # This avoids relying on AI to pass JSON correctly between function calls
                self._last_analysis_result = clean_result
                self.logger.info(f"📦 Stored clean analysis result in plugin instance ({len(messages)} messages)")
                
                return json.dumps(clean_result)
                    
            except Exception as ex:
                self.logger.error(f"Error in repository analysis: {str(ex)}", exc_info=True)
                span.record_exception(ex)
                span.set_status(Status(StatusCode.ERROR, str(ex)))
                
                record_error_details(
                    error_type=type(ex).__name__,
                    error_message=str(ex),
                    error_code=None,
                    is_retryable=True
                )
                
                return json.dumps({
                    "result": "error",
                    "repo_url": repo_url,
                    "message": str(ex)
                })
            
            finally:
                # Cleanup temporary repository directory
                if repo_path:
                    try:
                        self.logger.info(f"Cleaning up temporary repository at {repo_path}")
                        cleanup_repo(repo_path)
                        span.add_event("cleanup_completed")
                    except Exception as cleanup_ex:
                        self.logger.warning(f"Failed to cleanup repository: {cleanup_ex}")
                        span.add_event("cleanup_failed", {"error": str(cleanup_ex)})
    
    @kernel_function(
        name="upload_code_report_to_storage",
        description="Upload a code analysis Markdown report to Azure Storage blob container."
    )
    async def upload_code_report_to_storage(
        self, 
        markdown_content: str, 
        repo_url: str,
        content_type: str = "code",
        filename: Optional[str] = None,
        container_name: Optional[str] = None
    ) -> str:
        """
        Upload generated code analysis Markdown report to Azure Storage.
        
        Args:
            markdown_content: The Markdown content to upload
            repo_url: Repository URL for generating unique filename
            content_type: Type of content analyzed (terraform, java, python, etc.)
            filename: Optional custom filename (default: auto-generated)
            container_name: Optional container name (default: from env or 'code-analysis-reports')
            
        Returns:
            JSON string with upload result including blob URL
        """
        tracer = get_tracer()
        with tracer.start_as_current_span("upload_code_report_to_storage") as span:
            try:
                # Inject codebase analysis section into the report (file stats, mermaid diagrams, etc.)
                if self._codebase_analysis:
                    self.logger.info("📊 Injecting codebase analysis section into report...")
                    try:
                        codebase_section = self._codebase_analysis.to_markdown_section()
                        
                        # Try to insert after the report header and before Security Findings
                        insert_markers = [
                            "## Security Findings",
                            "## Findings",
                            "## Vulnerability",
                            "## Analysis",
                            "| Deficiency ID"  # Fallback: insert before first table
                        ]
                        
                        inserted = False
                        for marker in insert_markers:
                            if marker in markdown_content:
                                # Insert codebase analysis before this marker
                                markdown_content = markdown_content.replace(
                                    marker,
                                    f"{codebase_section}\n{marker}"
                                )
                                self.logger.info(f"✅ Injected codebase analysis before '{marker}'")
                                inserted = True
                                break
                        
                        if not inserted:
                            # Fallback: append after first horizontal rule or after header section
                            if "\n---\n" in markdown_content:
                                parts = markdown_content.split("\n---\n", 1)
                                markdown_content = f"{parts[0]}\n---\n\n{codebase_section}\n---\n{parts[1]}"
                                self.logger.info("✅ Injected codebase analysis after first section divider")
                            else:
                                # Last resort: prepend after first paragraph (header)
                                lines = markdown_content.split('\n')
                                header_end = 0
                                for i, line in enumerate(lines):
                                    if line.startswith('**Analysis') or line.startswith('**Code') or line.strip() == '':
                                        if i > 3:  # After at least a few header lines
                                            header_end = i
                                            break
                                
                                if header_end > 0:
                                    markdown_content = '\n'.join(lines[:header_end+1]) + f"\n\n{codebase_section}\n" + '\n'.join(lines[header_end+1:])
                                    self.logger.info("✅ Injected codebase analysis after header section")
                                else:
                                    # Very last resort: append at end
                                    markdown_content = f"{markdown_content}\n\n{codebase_section}"
                                    self.logger.info("✅ Appended codebase analysis at end of report")
                        
                        span.add_event("codebase_analysis_injected", {
                            "section_length": len(codebase_section),
                            "total_files": self._codebase_analysis.total_files,
                            "frameworks_count": len(self._codebase_analysis.frameworks)
                        })
                        
                    except Exception as inject_ex:
                        self.logger.warning(f"⚠️ Failed to inject codebase analysis: {inject_ex}")
                        span.add_event("codebase_analysis_injection_failed", {"error": str(inject_ex)})
                
                # Get storage account configuration from environment
                storage_account_name = os.getenv("AZURE_STORAGE_ACCOUNT_NAME")
                # Use provided container_name, or fall back to env, or default
                if not container_name:
                    container_name = os.getenv("AZURE_STORAGE_CONTAINER_NAME", "code-analysis-reports")
                
                if not storage_account_name:
                    error_msg = "AZURE_STORAGE_ACCOUNT_NAME not configured in environment"
                    span.set_status(Status(StatusCode.ERROR, error_msg))
                    return json.dumps({
                        "result": "error",
                        "message": error_msg
                    })
                
                # Generate filename in format: codeanalyzer-report-{app_id}.md
                # Use app_id if available, otherwise fall back to repo-based naming
                if not filename:
                    if self.app_id:
                        filename = f"codeanalyzer-report-{self.app_id}.md"
                    else:
                        # Fallback to repo-based naming if no app_id
                        repo_name = repo_url.rstrip('/').split('/')[-1].replace('.git', '')
                        filename = f"codeanalyzer-report-{repo_name}.md"
                
                # Store files in code-analyzer/output/ folder with versioning
                folder_prefix = "code-analyzer/output/"
                file_root, file_ext = os.path.splitext(filename)
                
                add_span_attributes(span, {
                    "storage_account": storage_account_name,
                    "container_name": container_name,
                    "filename": filename,
                    "content_size": len(markdown_content),
                    "content_type": content_type,
                    "repo_url": repo_url[:200]
                })
                
                # Create blob service client with managed identity
                account_url = f"https://{storage_account_name}.blob.core.windows.net"
                
                async with DefaultAzureCredential(exclude_shared_token_cache_credential=True) as credential:
                    async with BlobServiceClient(account_url=account_url, credential=credential) as blob_service_client:
                        
                        # Get container client
                        container_client = blob_service_client.get_container_client(container_name)
                        
                        # Create container if it doesn't exist
                        try:
                            await container_client.create_container()
                            span.add_event("container_created", {"container": container_name})
                        except Exception:
                            # Container might already exist
                            span.add_event("container_exists", {"container": container_name})
                        
                        # Handle versioning if file exists (like kubernetes agent)
                        version = 1
                        container_files = []
                        try:
                            # List blobs in the codeanalyzer/ folder (async iteration)
                            async for blob in container_client.list_blobs(name_starts_with=folder_prefix):
                                container_files.append(blob.name)
                        except Exception as list_ex:
                            self.logger.debug(f"Could not list existing blobs: {list_ex}")
                        
                        # Check for existing file in code-analyzer/output/ folder
                        blob_path = f"{folder_prefix}{filename}"
                        final_filename = filename
                        while blob_path in container_files:
                            version += 1
                            final_filename = f"{file_root}_v{version}{file_ext}"
                            blob_path = f"{folder_prefix}{final_filename}"
                        
                        if final_filename != filename:
                            self.logger.info(f"File version conflict detected, creating versioned file: {final_filename}")
                            span.add_event("file_versioned", {"original": filename, "versioned": final_filename, "version": version})
                        
                        filename = final_filename
                        
                        # Get blob client with folder prefix
                        blob_client = blob_service_client.get_blob_client(
                            container=container_name,
                            blob=f"{folder_prefix}{filename}"
                        )
                        
                        # Upload the markdown content
                        from azure.storage.blob import ContentSettings
                        
                        await blob_client.upload_blob(
                            markdown_content,
                            overwrite=True,
                            content_settings=ContentSettings(content_type="text/markdown")
                        )
                        
                        # Get the blob URL
                        blob_url = blob_client.url
                        
                        # Store blob_url in plugin instance for retrieval
                        self.blob_url = blob_url
                        self.logger.info(f"📦 Stored blob_url in plugin instance: {blob_url}")
                        
                        span.add_event("blob_uploaded_successfully", {
                            "blob_url": blob_url,
                            "filename": filename
                        })
                        
                        # Update operation record with blob_url if operation_id and app_id are available
                        if self.operation_id and self.app_id:
                            try:
                                from operation_service import get_operation_service
                                operation_service = get_operation_service()
                                operation = await operation_service.get_operation(self.operation_id, self.app_id)
                                
                                if operation:
                                    operation.blob_url = blob_url
                                    operation.update_progress("Report uploaded to Azure Storage", 90, operation.status)
                                    await operation_service.update_operation(operation)
                                    self.logger.info(f"✅ Updated operation {self.operation_id} with blob_url: {blob_url}")
                                else:
                                    self.logger.warning(f"⚠️ Operation {self.operation_id} not found for blob_url update")
                            except Exception as update_ex:
                                self.logger.error(f"❌ Failed to update operation {self.operation_id} with blob_url: {update_ex}")
                        else:
                            self.logger.warning(f"⚠️ No operation_id or app_id set on plugin, cannot update operation record")
                        
                        span.set_status(Status(StatusCode.OK))
                        
                        return json.dumps({
                            "result": "success",
                            "blob_url": blob_url,
                            "filename": filename,
                            "blob_path": f"{folder_prefix}{filename}",
                            "container_name": container_name,
                            "storage_account": storage_account_name,
                            "content_type": content_type,
                            "version": version if version > 1 else None,
                            "message": "Code analysis report uploaded successfully to Azure Storage"
                        })
                
            except Exception as ex:
                self.logger.error(f"Error uploading code report to storage: {str(ex)}")
                span.record_exception(ex)
                span.set_status(Status(StatusCode.ERROR, str(ex)))
                
                record_error_details(
                    error_type=type(ex).__name__,
                    error_message=str(ex),
                    error_code=None,
                    is_retryable=True
                )
                
                return json.dumps({
                    "result": "error",
                    "message": f"Failed to upload code report to storage: {str(ex)}"
                })
    
    @kernel_function(
        name="extract_and_upload_report",
        description="Extract generated Markdown report from agent messages and upload to Azure Storage."
    )
    async def extract_and_upload_report(
        self,
        analysis_result: str,
        repo_url: str,
        content_type: str = "code"
    ) -> str:
        """
        Extract Markdown report from analysis result and upload to Azure Storage.
        
        This function uses the EXPLICIT report_file path from the analysis result
        to ensure multi-tenant isolation - each request only reads files it created.
        
        Args:
            analysis_result: JSON string containing analysis results with report_file path
            repo_url: Repository URL (supports GitHub, GitLab, Azure DevOps, Bitbucket, Blob)
            content_type: Type of content analyzed
            
        Returns:
            JSON string with upload result including blob URL
        """
        self.logger.info(f"🔵 EXTRACT_AND_UPLOAD_REPORT CALLED - Repo URL: {repo_url}, Content Type: {content_type}")
        tracer = get_tracer()
        with tracer.start_as_current_span("extract_and_upload_report") as span:
            try:
                markdown_content = None
                report_filename = None
                analysis_data = None
                
                # PRIORITY 0: Use stored result from plugin instance (most reliable)
                # This avoids issues with AI passing malformed JSON between function calls
                if self._last_analysis_result:
                    self.logger.info(f"✅ Using stored analysis result from plugin instance")
                    analysis_data = self._last_analysis_result
                    
                    # Use stored content_type if provided param is generic
                    if content_type == "code" and self._last_content_type:
                        content_type = self._last_content_type
                        self.logger.info(f"📋 Using stored content_type: {content_type}")
                else:
                    # PRIORITY 1: Try to parse analysis_result JSON from AI
                    self.logger.info(f"⚠️ No stored result, attempting to parse analysis_result from AI...")
                    try:
                        result_dict = json.loads(analysis_result)
                        analysis_data = result_dict.get("analysis_result", {})
                    except json.JSONDecodeError as json_err:
                        self.logger.warning(f"⚠️ JSON parsing failed: {str(json_err)[:200]}")
                    except Exception as parse_ex:
                        self.logger.warning(f"⚠️ Error parsing analysis result: {str(parse_ex)[:200]}")
                
                # Extract report file from analysis data
                # Support both new structure (in analysis_summary) and legacy (at top level)
                if analysis_data:
                    # Try new structure first (analysis_summary.report_file)
                    analysis_summary = analysis_data.get("analysis_summary", {})
                    explicit_report_file = analysis_summary.get("report_file") or analysis_data.get("report_file")
                    created_files = analysis_summary.get("created_files") or analysis_data.get("created_files", [])
                    
                    self.logger.info(f"📋 Analysis created {len(created_files)} files: {created_files}")
                    self.logger.info(f"📋 Explicit report file: {explicit_report_file}")
                    
                    if explicit_report_file and os.path.exists(explicit_report_file):
                        report_filename = os.path.basename(explicit_report_file)
                        self.logger.info(f"✅ Reading EXPLICIT report file: {explicit_report_file}")
                        
                        with open(explicit_report_file, 'r', encoding='utf-8') as f:
                            markdown_content = f.read()
                        
                        self.logger.info(f"📄 Successfully read {len(markdown_content)} characters from {report_filename}")
                        
                        span.add_event("report_read_from_explicit_path", {
                            "file_path": explicit_report_file,
                            "content_length": len(markdown_content)
                        })
                    elif explicit_report_file:
                        self.logger.error(f"❌ Explicit report file does not exist: {explicit_report_file}")
                    else:
                        self.logger.warning(f"⚠️ No explicit report_file in analysis data")
                else:
                    self.logger.warning(f"⚠️ No analysis_data available")
                
                # PRIORITY 2 (FALLBACK): Search agent messages for inline report content
                if not markdown_content and analysis_data:
                    self.logger.info("📋 Explicit file not found, searching agent messages for inline report...")
                    # Check both 'messages' (full list) and 'last_messages' (truncated list in clean_result)
                    messages = analysis_data.get("messages", []) or analysis_data.get("last_messages", [])
                    self.logger.info(f"Found {len(messages)} messages to search (keys in analysis_data: {list(analysis_data.keys())})")
                    
                    for idx, message in enumerate(messages):
                        content = message.get("content", "")
                        content_preview = content[:100] if content else "(empty)"
                        self.logger.debug(f"Message #{idx + 1} from {message.get('agent', 'unknown')}: {content_preview}...")
                        
                        # Look for markdown report indicators
                        if "# Infrastructure Security Assessment Report" in content or \
                           "# Code Security Assessment Report" in content or \
                           "Deficiency ID" in content:
                            self.logger.info(f"✅ Found Markdown report in message #{idx + 1} from agent {message.get('agent', 'unknown')}")
                            markdown_content = content
                            report_filename = "inline_report.md"
                            break
                
                if not markdown_content:
                    error_msg = "No markdown report found - neither explicit report_file nor inline content in messages"
                    self.logger.error(f"❌ {error_msg}")
                    span.set_status(Status(StatusCode.ERROR, error_msg))
                    return json.dumps({
                        "result": "error",
                        "message": error_msg
                    })
                
                # Validate that the report content matches expected content_type
                # This helps detect if stale reports are being read
                import datetime
                today_str = datetime.datetime.now().strftime("%Y-%m-%d")
                content_lower = markdown_content.lower()
                
                # Check for content type mismatch warnings
                content_type_lower = content_type.lower()
                if content_type_lower in ["javascript", "typescript", "node", "nodejs"]:
                    if "c++" in content_lower or ".cpp" in content_lower:
                        self.logger.warning(f"⚠️ CONTENT MISMATCH: Expected JavaScript analysis but found C++ content in report!")
                        span.add_event("content_type_mismatch", {
                            "expected": content_type,
                            "found_indicator": "C++"
                        })
                elif content_type_lower == "terraform":
                    if "javascript" in content_lower or ".js" in content_lower:
                        self.logger.warning(f"⚠️ CONTENT MISMATCH: Expected Terraform analysis but found JavaScript content in report!")
                        span.add_event("content_type_mismatch", {
                            "expected": content_type,
                            "found_indicator": "JavaScript"
                        })
                
                # Check for stale date in report (older than today)
                if "Analysis Date:" in markdown_content:
                    import re
                    date_match = re.search(r'Analysis Date:\*?\*?\s*(\d{4}-\d{2}-\d{2})', markdown_content)
                    if date_match:
                        report_date = date_match.group(1)
                        if report_date != today_str:
                            self.logger.warning(f"⚠️ STALE REPORT: Report date {report_date} does not match today {today_str}")
                            span.add_event("stale_report_detected", {
                                "report_date": report_date,
                                "today": today_str
                            })
                
                self.logger.info(f"📄 Markdown report extracted ({len(markdown_content)} chars), preparing to upload...")
                add_span_attributes(span, {
                    "markdown_found": True,
                    "markdown_length": len(markdown_content),
                    "report_filename": report_filename or "extracted_from_messages"
                })
                
                # Inject codebase analysis section into the report (before Security Findings)
                if self._codebase_analysis:
                    self.logger.info("📊 Injecting codebase analysis section into report...")
                    try:
                        codebase_section = self._codebase_analysis.to_markdown_section()
                        
                        # Try to insert after the report header and before Security Findings
                        insert_markers = [
                            "## Security Findings",
                            "## Findings",
                            "## Vulnerability",
                            "## Analysis",
                            "| Deficiency ID"  # Fallback: insert before first table
                        ]
                        
                        inserted = False
                        for marker in insert_markers:
                            if marker in markdown_content:
                                # Insert codebase analysis before this marker
                                markdown_content = markdown_content.replace(
                                    marker,
                                    f"{codebase_section}\n{marker}"
                                )
                                self.logger.info(f"✅ Injected codebase analysis before '{marker}'")
                                inserted = True
                                break
                        
                        if not inserted:
                            # Fallback: append after first horizontal rule or after header section
                            if "\n---\n" in markdown_content:
                                parts = markdown_content.split("\n---\n", 1)
                                markdown_content = f"{parts[0]}\n---\n\n{codebase_section}\n---\n{parts[1]}"
                                self.logger.info("✅ Injected codebase analysis after first section divider")
                            else:
                                # Last resort: prepend after first paragraph (header)
                                lines = markdown_content.split('\n')
                                header_end = 0
                                for i, line in enumerate(lines):
                                    if line.startswith('**Analysis') or line.startswith('**Code') or line.strip() == '':
                                        if i > 3:  # After at least a few header lines
                                            header_end = i
                                            break
                                
                                if header_end > 0:
                                    markdown_content = '\n'.join(lines[:header_end+1]) + f"\n\n{codebase_section}\n" + '\n'.join(lines[header_end+1:])
                                    self.logger.info("✅ Injected codebase analysis after header section")
                                else:
                                    # Very last resort: append at end
                                    markdown_content = f"{markdown_content}\n\n{codebase_section}"
                                    self.logger.info("✅ Appended codebase analysis at end of report")
                        
                        span.add_event("codebase_analysis_injected", {
                            "section_length": len(codebase_section),
                            "total_files": self._codebase_analysis.total_files,
                            "frameworks_count": len(self._codebase_analysis.frameworks)
                        })
                        
                    except Exception as inject_ex:
                        self.logger.warning(f"⚠️ Failed to inject codebase analysis: {inject_ex}")
                        span.add_event("codebase_analysis_injection_failed", {"error": str(inject_ex)})
                
                # Upload the extracted report
                self.logger.info("Uploading report to Azure Storage...")
                upload_result = await self.upload_code_report_to_storage(
                    markdown_content=markdown_content,
                    repo_url=repo_url,
                    content_type=content_type,
                    filename=None
                )
                
                self.logger.info(f"✅ Report upload completed: {upload_result}")
                
                # Cleanup temp files now that report has been uploaded
                if self._analyzer:
                    self.logger.info("🧹 Cleaning up temporary analysis files...")
                    self._analyzer.cleanup_temp_files()
                    self._analyzer = None
                
                span.set_status(Status(StatusCode.OK))
                
                return upload_result
                
            except Exception as ex:
                self.logger.error(f"Error extracting and uploading report: {str(ex)}")
                span.record_exception(ex)
                span.set_status(Status(StatusCode.ERROR, str(ex)))
                
                # Cleanup temp files even on error
                if self._analyzer:
                    self.logger.info("🧹 Cleaning up temporary analysis files (error path)...")
                    try:
                        self._analyzer.cleanup_temp_files()
                    except Exception as cleanup_ex:
                        self.logger.warning(f"Cleanup failed: {cleanup_ex}")
                    self._analyzer = None
                
                return json.dumps({
                    "result": "error",
                    "message": f"Failed to extract and upload report: {str(ex)}"
                })


# Create plugin instance for use by orchestrator
code_analyzer_plugin = CodeAnalyzerPlugin()
