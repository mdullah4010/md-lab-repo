"""
Code Analyzer Plugin - Pure Semantic Kernel Implementation

This plugin consolidates all code analysis functionality into proper kernel functions:
- File operations (create files, create zips)
- Security scanning (detect secrets)
- Code analysis utilities

All functionality is exposed as @kernel_function decorated methods for proper
Semantic Kernel integration.
"""

import os
import sys
import json
import zipfile
import subprocess
import tempfile
from typing import Annotated, List, Dict, Any, Optional
from pathlib import Path
from semantic_kernel.functions import kernel_function

# Setup logging
import logging
logger = logging.getLogger(__name__)


class CodeAnalyzerPlugin:
    """
    Unified plugin for code analysis operations.
    
    This plugin provides all the tools needed for code analysis workflows:
    - File writing and management
    - Security scanning for secrets
    - ZIP file creation (safe/unsafe)
    - Code analysis utilities
    """
    
    def __init__(self, output_directory: str = None):
        """
        Initialize the CodeAnalyzerPlugin.
        
        Args:
            output_directory: Directory for output files. If None, creates a temporary directory.
        """
        if output_directory:
            self._output_directory = Path(output_directory)
            self._is_temp_dir = False
        else:
            # Create temporary directory for this analysis session
            temp_dir = tempfile.mkdtemp(prefix="code_analysis_")
            self._output_directory = Path(temp_dir)
            self._is_temp_dir = True
        
        # Ensure output directory exists
        self._output_directory.mkdir(parents=True, exist_ok=True)
        
        # Track files created during this analysis session (for multi-tenant isolation)
        self._created_files: list[str] = []
        
        logger.info(f"CodeAnalyzerPlugin initialized with output directory: {self._output_directory} (temp={self._is_temp_dir})")

    # =========================================================================
    # FILE OPERATIONS
    # =========================================================================
    
    @kernel_function(
        name="create_file",
        description="Create a file with the specified name and content. Use this to save analysis reports, code files, or any text content."
    )
    def create_file(
        self,
        file_name: Annotated[str, "The name of the file to create (can include subdirectories like 'reports/analysis.md')"],
        content: Annotated[str, "The content to write into the file"]
    ) -> Annotated[str, "The full path to the created file"]:
        """
        Create a file with the specified name and content in the output directory.
        
        Args:
            file_name: The name of the file to create (can include subdirectories)
            content: The content to write into the file
            
        Returns:
            The full path to the created file
        """
        try:
            file_path = self._output_directory / file_name
            
            # Create parent directories if needed
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            # Track this file for multi-tenant isolation
            self._created_files.append(str(file_path))
            
            logger.info(f"Created file: {file_path} (total created this session: {len(self._created_files)})")
            return str(file_path)
            
        except Exception as e:
            error_msg = f"Failed to create file '{file_name}': {str(e)}"
            logger.error(error_msg)
            return f"ERROR: {error_msg}"
    
    def get_created_files(self) -> list[str]:
        """Return list of files created during this session. Used for multi-tenant isolation."""
        return self._created_files.copy()
    
    def get_created_report_file(self) -> str | None:
        """Return the most recent .md report file created during this session."""
        md_files = [f for f in self._created_files if f.endswith('.md')]
        return md_files[-1] if md_files else None
    
    def get_output_directory(self) -> str:
        """Return the output directory path."""
        return str(self._output_directory)
    
    def cleanup(self):
        """Clean up temporary directory if one was created."""
        if self._is_temp_dir and self._output_directory.exists():
            import shutil
            try:
                shutil.rmtree(self._output_directory, ignore_errors=True)
                logger.info(f"Cleaned up temporary directory: {self._output_directory}")
            except Exception as e:
                logger.warning(f"Failed to clean up temporary directory {self._output_directory}: {e}")

    # NOTE: read_file and list_files were REMOVED because they caused confusion.
    # The agent was trying to use them to access /mnt/data/ (Azure code interpreter sandbox)
    # but these functions operate on LOCAL paths. The code interpreter tool already
    # handles file reading from uploaded archives. Only create_file is needed.

    # =========================================================================
    # SECURITY SCANNING
    # =========================================================================
    
    @kernel_function(
        name="scan_for_secrets",
        description="Scan a directory for potential secrets (API keys, passwords, tokens). Returns list of files containing secrets."
    )
    def scan_for_secrets(
        self,
        path_to_scan: Annotated[str, "The directory path to scan for secrets"]
    ) -> Annotated[str, "JSON result with files containing secrets and details"]:
        """
        Scan a directory for potential secrets using detect-secrets.
        
        Args:
            path_to_scan: The directory path to scan for secrets
            
        Returns:
            JSON result containing:
            - files_with_secrets: List of file paths containing secrets
            - total_secrets_found: Total count of secrets detected
            - details: Per-file breakdown of secrets found
        """
        try:
            if not os.path.exists(path_to_scan):
                return json.dumps({
                    "error": f"Path '{path_to_scan}' does not exist",
                    "files_with_secrets": [],
                    "total_secrets_found": 0
                })
            
            # Run detect-secrets scan
            result = subprocess.run(
                [sys.executable, "-m", "detect_secrets", "scan", "--all-files", path_to_scan],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            if result.returncode == 0:
                scan_output = json.loads(result.stdout)
                results = scan_output.get("results", {})
                
                files_with_secrets = []
                details = {}
                total_secrets = 0
                
                for file_path, secrets in results.items():
                    if secrets and len(secrets) > 0:
                        files_with_secrets.append(file_path)
                        details[file_path] = {
                            "secret_count": len(secrets),
                            "secret_types": [s.get("type", "unknown") for s in secrets]
                        }
                        total_secrets += len(secrets)
                
                scan_result = {
                    "scan_completed": True,
                    "files_with_secrets": files_with_secrets,
                    "total_secrets_found": total_secrets,
                    "details": details,
                    "recommendation": "Remove or rotate any detected secrets before proceeding" if total_secrets > 0 else "No secrets detected - safe to proceed"
                }
                
                logger.info(f"Security scan completed: {total_secrets} secrets found in {len(files_with_secrets)} files")
                return json.dumps(scan_result, indent=2)
                
            else:
                return json.dumps({
                    "scan_completed": False,
                    "error": f"detect-secrets scan failed: {result.stderr}",
                    "files_with_secrets": [],
                    "total_secrets_found": 0
                })
                
        except json.JSONDecodeError as e:
            return json.dumps({
                "scan_completed": False,
                "error": f"Failed to parse scan results: {str(e)}",
                "files_with_secrets": [],
                "total_secrets_found": 0
            })
        except Exception as e:
            error_msg = f"Security scan failed: {str(e)}"
            logger.error(error_msg)
            return json.dumps({
                "scan_completed": False,
                "error": error_msg,
                "files_with_secrets": [],
                "total_secrets_found": 0
            })

    # =========================================================================
    # ZIP FILE OPERATIONS
    # =========================================================================
    
    @kernel_function(
        name="create_zip_archive",
        description="Create a ZIP archive of files in a directory. Can optionally exclude files containing secrets."
    )
    def create_zip_archive(
        self,
        source_directory: Annotated[str, "The directory containing files to archive"],
        exclude_files: Annotated[str, "JSON array of file paths to exclude (e.g., files with secrets)"] = "[]",
        output_name: Annotated[str, "Name for the output ZIP file (without .zip extension)"] = "archive"
    ) -> Annotated[str, "JSON result with the path to created ZIP and statistics"]:
        """
        Create a ZIP archive of files in a directory.
        
        Args:
            source_directory: The directory containing files to archive
            exclude_files: JSON array of file paths to exclude
            output_name: Name for the output ZIP file
            
        Returns:
            JSON result with path to ZIP and file statistics
        """
        try:
            source_path = Path(source_directory)
            if not source_path.exists():
                return json.dumps({"error": f"Source directory '{source_directory}' does not exist"})
            
            # Parse excluded files
            try:
                excluded = json.loads(exclude_files) if exclude_files else []
                normalized_excluded = [os.path.normpath(p) for p in excluded]
            except json.JSONDecodeError:
                normalized_excluded = []
            
            # Create ZIP in source directory
            zip_path = source_path / f"{output_name}.zip"
            
            files_added = 0
            files_excluded = 0
            
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(source_directory):
                    for file in files:
                        file_path = os.path.join(root, file)
                        normalized_path = os.path.normpath(file_path)
                        
                        # Skip the output zip itself
                        if normalized_path == os.path.normpath(str(zip_path)):
                            continue
                        
                        # Check if file should be excluded
                        should_exclude = any(
                            normalized_path == excluded_path or
                            normalized_path.endswith(excluded_path)
                            for excluded_path in normalized_excluded
                        )
                        
                        if should_exclude:
                            files_excluded += 1
                            logger.debug(f"Excluding file: {file_path}")
                        else:
                            archive_name = os.path.relpath(file_path, source_directory)
                            zipf.write(file_path, archive_name)
                            files_added += 1
            
            result = {
                "success": True,
                "zip_path": str(zip_path),
                "files_added": files_added,
                "files_excluded": files_excluded,
                "total_files_processed": files_added + files_excluded
            }
            
            logger.info(f"Created ZIP archive: {zip_path} ({files_added} files, {files_excluded} excluded)")
            return json.dumps(result, indent=2)
            
        except Exception as e:
            error_msg = f"Failed to create ZIP archive: {str(e)}"
            logger.error(error_msg)
            return json.dumps({"error": error_msg, "success": False})

    # =========================================================================
    # CODE ANALYSIS UTILITIES
    # =========================================================================
    
    @kernel_function(
        name="analyze_file_structure",
        description="Analyze the structure of a codebase - file types, counts, and organization."
    )
    def analyze_file_structure(
        self,
        directory_path: Annotated[str, "The directory path to analyze"]
    ) -> Annotated[str, "JSON analysis of the file structure"]:
        """
        Analyze the structure of a codebase.
        
        Args:
            directory_path: The directory path to analyze
            
        Returns:
            JSON analysis including file types, counts, and structure
        """
        try:
            path = Path(directory_path)
            if not path.exists():
                return json.dumps({"error": f"Directory '{directory_path}' does not exist"})
            
            analysis = {
                "total_files": 0,
                "total_directories": 0,
                "file_types": {},
                "largest_files": [],
                "directory_structure": {}
            }
            
            all_files = []
            
            for root, dirs, files in os.walk(directory_path):
                analysis["total_directories"] += len(dirs)
                
                rel_root = os.path.relpath(root, directory_path)
                if rel_root == ".":
                    rel_root = "root"
                
                analysis["directory_structure"][rel_root] = len(files)
                
                for file in files:
                    analysis["total_files"] += 1
                    file_path = Path(root) / file
                    ext = file_path.suffix.lower() or "no_extension"
                    
                    # Count by extension
                    analysis["file_types"][ext] = analysis["file_types"].get(ext, 0) + 1
                    
                    # Track for largest files
                    try:
                        size = file_path.stat().st_size
                        all_files.append({"path": str(file_path), "size": size})
                    except:
                        pass
            
            # Get top 10 largest files
            all_files.sort(key=lambda x: x["size"], reverse=True)
            analysis["largest_files"] = all_files[:10]
            
            # Determine project type based on files
            analysis["detected_project_types"] = self._detect_project_types(analysis["file_types"])
            
            return json.dumps(analysis, indent=2)
            
        except Exception as e:
            error_msg = f"Failed to analyze file structure: {str(e)}"
            logger.error(error_msg)
            return json.dumps({"error": error_msg})

    def _detect_project_types(self, file_types: Dict[str, int]) -> List[str]:
        """Detect project types based on file extensions."""
        project_types = []
        
        type_indicators = {
            "python": [".py", ".pyx", ".pyi"],
            "javascript": [".js", ".jsx", ".mjs"],
            "typescript": [".ts", ".tsx"],
            "terraform": [".tf", ".tfvars"],
            "java": [".java"],
            "csharp": [".cs"],
            "go": [".go"],
            "rust": [".rs"],
            "kubernetes": [".yaml", ".yml"],  # Could be k8s manifests
            "docker": [],  # Detected by dockerfile
            "web": [".html", ".css", ".scss"]
        }
        
        for project_type, extensions in type_indicators.items():
            if any(ext in file_types for ext in extensions):
                project_types.append(project_type)
        
        return project_types

    @kernel_function(
        name="generate_security_report_template",
        description="Generate a markdown template for a security assessment report with proper structure and tables."
    )
    def generate_security_report_template(
        self,
        project_name: Annotated[str, "Name of the project being assessed"],
        analysis_type: Annotated[str, "Type of analysis (e.g., 'code_review', 'terraform', 'infrastructure')"] = "code_review"
    ) -> Annotated[str, "Markdown template for the security report"]:
        """
        Generate a security report template.
        
        Args:
            project_name: Name of the project being assessed
            analysis_type: Type of analysis being performed
            
        Returns:
            Markdown template for the security report
        """
        from datetime import datetime
        current_date = datetime.now().strftime("%Y-%m-%d")
        
        template = f"""# Security Assessment Report

## Project Information
- **Project Name:** {project_name}
- **Assessment Type:** {analysis_type}
- **Assessment Date:** {current_date}
- **Report Version:** 1.0

## Executive Summary
[Provide a high-level summary of the security assessment findings]

## Scope
[Describe what was analyzed and any limitations]

## Findings Summary

| Severity | Count |
|----------|-------|
| Critical | 0     |
| High     | 0     |
| Medium   | 0     |
| Low      | 0     |
| Info     | 0     |

## Detailed Findings

| Deficiency ID | Severity | Status | Date | Deficiency Type | Reference | Owner | Affected Assets | Deficiency Title | Threat Description | Proposed Mitigation |
|---------------|----------|--------|------|-----------------|-----------|-------|-----------------|------------------|-------------------|---------------------|
| DEF-001 | [Critical/High/Medium/Low] | Open | {current_date} | [Type] | [Ref] | [Owner] | [Assets] | [Title] | [Description] | [Mitigation] |

## Recommendations
[Provide prioritized recommendations for remediation]

## Appendix
[Include any additional technical details, code snippets, or references]

---
*This report was generated as part of an automated security assessment.*
"""
        return template
