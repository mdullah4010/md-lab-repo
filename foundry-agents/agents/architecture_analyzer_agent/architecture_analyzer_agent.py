# Copyright (c) Microsoft. All rights reserved.

"""
Architecture Agent - Refactored Modular Version

Orchestrates architecture security analysis using modular components:
- AgentFactory: Creates and configures AI agents
- SecurityAnalyzer: Performs security analysis on components
- ReportGenerator: Generates consolidated reports
- FindingsExtractor: Extracts and formats findings

This refactored version separates concerns and improves maintainability.
"""

import os
import asyncio
import json
import time
import urllib.parse
from typing import Optional, Dict, Any, List, Callable
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Import logging configuration
from logging_config import get_logger

# Import tracing configuration
from tracing_config import (
    get_tracer,
    trace_async_function,
    add_span_attributes,
)
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

# Semantic Kernel imports
from semantic_kernel import Kernel

# Import plugins
from core.plugins import BlobStoragePlugin
from core.plugins.plugin_utils import load_agent_instructions
from core.plugins.foundry_image_analyzer import extract_and_analyze_architecture

# Import modular components
from core.agent_factory import AgentFactory
from core.security_analyzer import SecurityAnalyzer
from core.report_generator import ReportGenerator
from core.findings_extractor import FindingsExtractor

# Import cleanup functions as module-level aliases for convenience
cleanup_architecture_agent = AgentFactory.cleanup_architecture_agent
cleanup_security_agent = AgentFactory.cleanup_security_agent
cleanup_diagram_agent = AgentFactory.cleanup_diagram_agent

# Import operation tracking models
from operation_models import OperationRecord, OperationStatus

# Create logger for this module BEFORE loading env (so we can log env loading)
logger = get_logger(__name__)

# Load environment variables from multiple possible locations
env_paths = [
    Path(__file__).parent.parent.parent / ".env",  # AI-IntakeandAssessmentv1.0/.env
    ".env"  # Current directory
]

env_loaded = False
for env_path in env_paths:
    if load_dotenv(env_path):
        logger.info(f"Loaded .env from: {env_path}")
        env_loaded = True
        break

if not env_loaded:
    logger.warning("No .env file loaded, using system environment variables")

logger.info("Architecture Agent initialized (refactored modular version)")

# Debug-only console printing
_ARCH_AGENT_DEBUG = os.environ.get("ARCH_AGENT_DEBUG", os.environ.get("DEBUG", "false")).lower() in ("1", "true", "yes")


def _debug_print(*args, **kwargs):
    """Log to debug level when debug mode is enabled."""
    if not _ARCH_AGENT_DEBUG:
        return
    try:
        ts = datetime.utcnow().isoformat()
        msg = ' '.join(str(arg) for arg in args)
        logger.debug(f"[ARCH_AGENT DEBUG] {ts} - {msg}")
    except Exception:
        pass


_debug_print("Architecture Agent initialized (debug mode) - refactored version")


@trace_async_function("validate_scf_index")
async def _validate_scf_index() -> Dict[str, Any]:
    """
    Validate that SCF index exists and contains documents.
    
    Uses the consolidated validate_index utility from agents.utils.
    
    Returns:
        Dict with validation status, document count, and any error messages
    """
    from agents.utils import validate_index
    
    tracer = get_tracer()
    with tracer.start_as_current_span("validate_scf_index") as span:
        add_span_attributes(span, {
            "operation": "validate_scf_index"
        })
        
        try:
            # Get SCF index configuration
            scf_index_name = os.getenv("SCF_AZURE_SEARCH_INDEX", "scfindex")
            
            # Use consolidated validate_index utility
            result = validate_index(
                index_name=scf_index_name,
                require_documents=True,
                index_display_name="SCF"
            )
            
            add_span_attributes(span, {
                "scf_index_name": scf_index_name,
                "document_count": result.document_count,
                "validation_success": result.is_valid
            })
            
            if not result.is_valid:
                logger.warning(f"SCF index validation failed: {result.error_message}")
                return {
                    "status": "warning" if result.document_count == 0 else "error",
                    "valid": False,
                    "message": result.error_message or f"SCF index '{scf_index_name}' validation failed",
                    "document_count": result.document_count
                }
            
            logger.info(f"SCF index validation successful: {result.document_count} documents found")
            return {
                "status": "success",
                "valid": True,
                "message": f"SCF index '{scf_index_name}' validated successfully",
                "document_count": result.document_count
            }
            
        except Exception as ex:
            error_msg = f"SCF index validation failed: {str(ex)}"
            logger.error(error_msg)
            span.record_exception(ex)
            add_span_attributes(span, {
                "validation_error": str(ex),
                "validation_success": False
            })
            return {
                "status": "error",
                "valid": False,
                "message": error_msg,
                "document_count": 0
            }


@trace_async_function("analyze_single_architecture")
async def analyze_single_architecture(
    design_doc_url: str,
    analysis_instructions: str = "Analyze this architecture for security compliance and generate recommendations",
    operation: Optional[OperationRecord] = None,
    is_async: bool = False
) -> Dict[str, Any]:
    """
    Analyze a single architecture design document for security compliance.
    
    Args:
        design_doc_url: Blob storage path to design document to analyze
        analysis_instructions: Custom instructions for the architecture analysis agent
        operation: Optional operation record for tracking (async mode)
        is_async: Whether this is running in async mode
    
    Returns:
        Dict: Result containing status, analysis results, and report details
    """
    logger.info(f"Starting architecture analysis for design document: {design_doc_url}")
    _debug_print(f"Starting architecture analysis for design document: {design_doc_url}")
    
    tracer = get_tracer()
    function_start_time = time.time()
    
    operation_service = None
    if operation:
        from operation_service import get_operation_service
        operation_service = get_operation_service()
    
    log_prefix = "[ASYNC]" if is_async else "[SYNC]"
    span_name = "background_architecture_analysis" if is_async else "architecture_analysis"
    
    with tracer.start_as_current_span(span_name) as main_span:
        add_span_attributes(main_span, {
            "architecture.design_doc_url": design_doc_url[:200],
            "architecture.instructions": analysis_instructions[:100],
            "architecture.is_async": is_async,
            "architecture.has_operation_tracking": operation is not None
        })
        
        execution_log = []
        
        try:
            # Update operation progress - initialization
            if operation and operation_service:
                operation.update_progress("Initializing architecture analysis", 10, OperationStatus.IN_PROGRESS)
                await operation_service.update_operation(operation)
            
            main_span.add_event("analysis_initialization", {
                "progress": 10,
                "is_async": is_async,
                "has_operation": operation is not None
            })
            
            execution_log.append(f"{log_prefix} Starting architecture analysis workflow")
            execution_log.append(f"[CONFIG] Design Document URL: {design_doc_url}")
            
            # Initialize modular components
            agent_factory = AgentFactory()
            
            # Update operation progress - setting up agent
            if operation and operation_service:
                operation.update_progress("Setting up analysis environment", 20, OperationStatus.IN_PROGRESS)
                await operation_service.update_operation(operation)
            
            main_span.add_event("agent_setup_started", {"progress": 20})
            
            # Create kernel and add custom plugins
            execution_log.append("[SETUP] Initializing kernel and plugins...")
            kernel = Kernel()
            
            # Register extract_and_analyze_architecture function as a plugin
            from core.plugins.foundry_image_analyzer import extract_and_analyze_architecture
            kernel.add_function(
                plugin_name="DesignDocExtractor",
                function=extract_and_analyze_architecture
            )
            blob_plugin = BlobStoragePlugin()
            kernel.add_plugin(blob_plugin, "BlobStorage")
            
            execution_log.append("[SUCCESS] Plugins initialized: DesignDocExtractor, BlobStorage")
            main_span.add_event("plugins_initialized", {"plugin_count": 2})
            
            # Update operation progress - configuring agent
            if operation and operation_service:
                operation.update_progress("Configuring AI agent", 40, OperationStatus.IN_PROGRESS)
                await operation_service.update_operation(operation)
            
            main_span.add_event("agent_configuration_started", {"progress": 40})
            
            # Load agent instructions
            base_instructions = load_agent_instructions("architecture-analyzer-agent")
            
            if base_instructions:
                agent_instructions = f"""{base_instructions}
Design Document URL: {design_doc_url}
Analysis Focus: {analysis_instructions}"""
                execution_log.append(f"[SUCCESS] Loaded agent instructions from file")
                main_span.add_event("instructions_loaded", {"source": "file"})
            else:
                agent_instructions = _build_fallback_instructions(design_doc_url, analysis_instructions)
                execution_log.append("[FALLBACK] Using enhanced fallback instructions")
                main_span.add_event("instructions_loaded", {"source": "fallback"})
            
            # Create architecture agent
            execution_log.append("[SETUP] Creating architecture analyzer agent...")
            agent = await agent_factory.create_architecture_agent(
                instructions=agent_instructions,
                kernel=kernel,
                agent_name="ArchitectureAnalyzer"
            )
            
            execution_log.append(f"[SUCCESS] Architecture Analyzer Agent created")
            main_span.add_event("agent_created", {"agent_name": "ArchitectureAnalyzer"})
            
            # Update operation progress - starting agent execution
            if operation and operation_service:
                operation.update_progress("Starting agent analysis", 60, OperationStatus.IN_PROGRESS)
                await operation_service.update_operation(operation)
            
            main_span.add_event("agent_execution_started", {"progress": 60})
            
            # Execute the agent analysis
            execution_log.append("[START] Starting agent analysis workflow...")
            
            user_message = _build_workflow_message(design_doc_url)
            execution_log.append(f"[MESSAGE] Sending workflow message to agent")
            _debug_print("Sending workflow message to agent")
            
            main_span.add_event("agent_invocation", {"design_doc_url": design_doc_url[:200]})
            
            # Execute agent and collect responses
            agent_responses = []
            tool_calls_made = []
            required_functions = ["extract_and_analyze_architecture", "create_architecture_report"]
            completed_functions = []
            
            # Update operation progress - agent processing
            if operation and operation_service:
                operation.update_progress("Agent processing request", 80, OperationStatus.IN_PROGRESS)
                await operation_service.update_operation(operation)
            
            # Create a dedicated thread for this agent execution
            thread = await agent.client.agents.threads.create()
            # Derive app_id from operation or use a default
            thread_app_id = operation.app_id if operation and hasattr(operation, 'app_id') else "unknown"
            AgentFactory.register_thread(thread_app_id, thread.id)
            logger.info(f"Created and registered thread {thread.id} for app {thread_app_id}")
            execution_log.append(f"[THREAD] Created thread {thread.id}")
            
            # Invoke agent with the dedicated thread
            async for response_item in agent.invoke(
                messages=user_message,
                thread=thread.id,
                temperature=0.1,
                max_completion_tokens=16384,
                max_prompt_tokens=100000
            ):
                response = response_item.message if hasattr(response_item, 'message') else response_item
                agent_responses.append(response)
                
                # Track function calls
                if hasattr(response, 'content') and isinstance(response.content, list):
                    for item in response.content:
                        if hasattr(item, 'name'):
                            func_name = item.name
                            tool_calls_made.append(func_name)
                            if func_name in required_functions and func_name not in completed_functions:
                                completed_functions.append(func_name)
                                execution_log.append(f"[PROGRESS] Completed: {func_name}")
                                main_span.add_event("tool_call_completed", {"tool_name": func_name})
            
            # Combine responses
            final_response_parts = []
            for resp in agent_responses:
                if hasattr(resp, 'content'):
                    if isinstance(resp.content, str):
                        final_response_parts.append(resp.content)
                    elif isinstance(resp.content, list):
                        for item in resp.content:
                            if hasattr(item, 'text') and item.text:
                                final_response_parts.append(item.text)
            
            final_response = "\n".join(final_response_parts) if final_response_parts else "No response content"
            
            # Update operation progress - completing
            if operation and operation_service:
                operation.update_progress("Analysis completed successfully", 100, OperationStatus.COMPLETED)
                await operation_service.update_operation(operation)
            
            main_span.add_event("response_processing_completed", {
                "response_count": len(agent_responses),
                "tool_calls": len(tool_calls_made)
            })
            
            # Calculate execution time
            execution_time = time.time() - function_start_time
            
            # Build analysis summary
            analysis_summary = {
                "total_tools": len(tool_calls_made),
                "tools_executed": tool_calls_made,
                "required_functions": required_functions,
                "completed_functions": completed_functions,
                "execution_time_seconds": round(execution_time, 2),
                "response_count": len(agent_responses),
                "workflow_complete": len(completed_functions) >= len(required_functions)
            }
            
            main_span.add_event("analysis_summary_created", {
                "execution_time": execution_time,
                "tool_calls": len(tool_calls_made),
                "workflow_complete": analysis_summary["workflow_complete"]
            })
            
            # Build result
            result = {
                "status": "success",
                "design_doc_url": design_doc_url,
                "agent_response": final_response,
                "execution_log": execution_log,
                "analysis_summary": analysis_summary,
                "generated_report_url": None,
                "timestamp": datetime.utcnow().isoformat()
            }
            
            logger.info(f"Architecture analysis completed successfully in {execution_time:.2f}s")
            add_span_attributes(main_span, {
                "architecture.status": "success",
                "architecture.execution_time": execution_time,
                "architecture.tool_calls": len(tool_calls_made)
            })
            
            return result
            
        except Exception as ex:
            error_type = type(ex).__name__
            error_message = str(ex)
            
            logger.error(f"Architecture analysis failed: {error_type} - {error_message}")
            execution_log.append(f"[ERROR] {error_type}: {error_message}")
            
            if operation and operation_service:
                operation.update_progress(f"Analysis failed: {error_message[:100]}", 0, OperationStatus.FAILED)
                operation.error_message = error_message
                await operation_service.update_operation(operation)
            
            main_span.set_status(Status(StatusCode.ERROR, error_message))
            main_span.record_exception(ex)
            
            return {
                "status": "error",
                "error": error_message,
                "error_type": error_type,
                "execution_log": execution_log,
                "timestamp": datetime.utcnow().isoformat()
            }


@trace_async_function("run_dynamic_architecture_analysis")
async def run_dynamic_architecture_analysis(
    app_id: str,
    design_doc_url: str,
    analysis_instructions: str = "Analyze this architecture for security compliance and generate recommendations",
    operation: Optional[OperationRecord] = None,
    is_async: bool = False,
    progress_callback: Optional[Callable] = None
) -> Dict[str, Any]:
    """
    Run architecture analysis that dynamically discovers architecture diagrams from blob storage.
    
    Args:
        app_id: Application ID (used for agent naming and blob container)
        design_doc_url: Blob storage path to design document to analyze
        analysis_instructions: Custom instructions for the architecture analysis
        operation: Optional operation record for tracking
        is_async: Whether this is running in async mode
        progress_callback: Optional callback function for progress updates
    
    Returns:
        Dict: Result containing consolidated findings and report URL
    """
    # Validate required parameters
    if not design_doc_url or not isinstance(design_doc_url, str) or not design_doc_url.strip():
        error_msg = "design_doc_url parameter is required and must be a non-empty string"
        logger.error(f"Architecture analysis validation failed: {error_msg}")
        return {
            "status": "error",
            "error": error_msg,
            "error_type": "ValidationError",
            "suggestion": "Please provide a valid design_doc_url parameter (blob storage path to design document)",
            "timestamp": datetime.utcnow().isoformat()
        }
    
    if not app_id or not isinstance(app_id, str) or not app_id.strip():
        error_msg = "app_id parameter is required and must be a non-empty string"
        logger.error(f"Architecture analysis validation failed: {error_msg}")
        return {
            "status": "error",
            "error": error_msg,
            "error_type": "ValidationError",
            "suggestion": "Please provide a valid app_id parameter",
            "timestamp": datetime.utcnow().isoformat()
        }
    
    logger.info(f"Starting dynamic architecture analysis for: {design_doc_url}")
    
    tracer = get_tracer()
    function_start_time = time.time()
    
    operation_service = None
    if operation:
        from operation_service import get_operation_service
        operation_service = get_operation_service()
    
    # Track agent/thread for cleanup
    security_agent = None
    security_agent_id = None
    
    with tracer.start_as_current_span("dynamic_architecture_analysis") as main_span:
        add_span_attributes(main_span, {
            "architecture.app_id": app_id,
            "architecture.design_doc_url": design_doc_url[:200],
            "architecture.is_async": is_async
        })
        
        try:
            # Initialize components
            report_generator = ReportGenerator()
            findings_extractor = FindingsExtractor()
            agent_factory = AgentFactory()
            
            # STEP 1: Validate SCF index before proceeding
            if operation and operation_service:
                operation.update_progress("Validating SCF index", 5, OperationStatus.IN_PROGRESS)
                await operation_service.update_operation(operation)
            
            main_span.add_event("scf_validation_started", {"progress": 5})
            logger.info("Step 1: Validating SCF index...")
            
            scf_validation = await _validate_scf_index()
            
            if not scf_validation.get("valid"):
                error_msg = scf_validation.get("message", "SCF index validation failed")
                logger.error(f"SCF index validation failed: {error_msg}")
                main_span.add_event("scf_validation_failed", {
                    "message": error_msg,
                    "document_count": scf_validation.get("document_count", 0)
                })
                main_span.set_status(Status(StatusCode.ERROR, "SCF index validation failed"))
                
                if operation and operation_service:
                    operation.update_progress(f"Validation failed: {error_msg[:100]}", 0, OperationStatus.FAILED)
                    await operation_service.update_operation(operation)
                
                return {
                    "status": "validation_failed",
                    "error": error_msg,
                    "validation_details": scf_validation,
                    "suggestion": "Please ensure SCF data is properly indexed in Azure AI Search before running architecture analysis.",
                    "timestamp": datetime.utcnow().isoformat()
                }
            
            logger.info(f"SCF index validated: {scf_validation.get('document_count')} documents found")
            main_span.add_event("scf_validation_success", {
                "document_count": scf_validation.get("document_count")
            })
            
            # STEP 2: Extract Design Document and discover architectures
            if operation and operation_service:
                operation.update_progress("Discovering architecture diagrams", 10, OperationStatus.IN_PROGRESS)
                await operation_service.update_operation(operation)
            
            main_span.add_event("design_doc_extraction_started", {"progress": 10})
            
            logger.info("Step 2: Extracting design document and discovering architecture diagrams")
            
            # Use the direct function for extraction
            from core.plugins.foundry_image_analyzer import extract_and_analyze_architecture
            extraction_result_str = await extract_and_analyze_architecture(design_doc_url=design_doc_url, app_id=app_id)
            
            extraction_result = json.loads(extraction_result_str) if isinstance(extraction_result_str, str) else extraction_result_str
            
            main_span.add_event("Design-doc_extraction_completed", {
                "status": extraction_result.get("status")
            })
            
            if extraction_result.get("status") != "success":
                # Extract detailed error information
                error_details = extraction_result.get('error_details', {})
                if error_details:
                    error_type = error_details.get('error_type', 'Unknown')
                    error_message = error_details.get('error_message', 'Unknown error')
                    suggestion = error_details.get('suggestion', '')
                    error_msg = f"{error_type}: {error_message}"
                    if suggestion:
                        error_msg += f" - {suggestion}"
                else:
                    error_msg = extraction_result.get('error', 'Unknown error')
                
                logger.error(f"Design-doc extraction failed: {error_msg}")
                main_span.add_event("Design-doc_extraction_failed", {"error": error_msg[:500]})
                raise Exception(f"Design-doc extraction failed: {error_msg}")
            
            architecture_analyses = extraction_result.get("architecture_analyses", [])
            
            if not architecture_analyses:
                logger.warning("No architecture diagrams found in Design-doc")
                main_span.add_event("no_architectures_found")
                return {
                    "status": "success",
                    "total_architectures": 0,
                    "total_findings": 0,
                    "message": "No architecture diagrams found in the Design-doc",
                    "timestamp": datetime.utcnow().isoformat()
                }
            
            logger.info(f"Discovered {len(architecture_analyses)} architecture diagrams")
            main_span.add_event("architectures_discovered", {
                "count": len(architecture_analyses)
            })
            
            # STEP 3: Find or create SINGLE security agent for all architectures
            if operation and operation_service:
                operation.update_progress("Setting up security analysis agent", 15, OperationStatus.IN_PROGRESS)
                await operation_service.update_operation(operation)
            
            main_span.add_event("security_agent_setup_started", {"app_id": app_id})
            
            # Check if agent already exists
            logger.info(f"Looking for existing Security-Agent-{app_id}")
            from azure.identity.aio import DefaultAzureCredential
            from azure.ai.projects.aio import AIProjectClient
            from semantic_kernel.agents import AzureAIAgent
            from semantic_kernel import Kernel
            
            async with DefaultAzureCredential() as credential:
                project_client = AIProjectClient(
                    credential=credential,
                    endpoint=agent_factory.ai_endpoint
                )
                
                existing_security_agent = await AgentFactory.find_existing_security_agent(project_client, app_id)
                
                if existing_security_agent:
                    logger.info(f"Reusing existing Security-Agent-{app_id} with ID: {existing_security_agent.id}")
                    
                    agent_client = AzureAIAgent.create_client(
                        credential=credential,
                        endpoint=agent_factory.ai_endpoint
                    )
                    
                    security_agent = AzureAIAgent(
                        client=agent_client,
                        definition=existing_security_agent,
                        kernel=Kernel()
                    )
                    security_agent_id = existing_security_agent.id
                    
                    main_span.add_event("security_agent_reused", {
                        "agent_name": f"Security-Agent-{app_id}",
                        "agent_id": security_agent_id
                    })
                else:
                    logger.info(f"Creating new Security-Agent-{app_id}")
                    security_agent = await agent_factory.create_security_analysis_agent(app_id=app_id)
                    security_agent_id = security_agent.definition.id if hasattr(security_agent, 'definition') else None
                    
                    main_span.add_event("security_agent_created", {
                        "agent_name": f"Security-Agent-{app_id}",
                        "agent_id": security_agent_id
                    })
            
            logger.info(f"Security agent ready with ID: {security_agent_id}")
            
            # STEP 4: Analyze each architecture using the SAME agent
            architecture_results = {}
            all_findings = []
            deficiency_counter = 1
            total_architectures = len(architecture_analyses)
            
            for idx, arch_analysis in enumerate(architecture_analyses, 1):
                arch_name = _sanitize_architecture_name(arch_analysis, idx)
                
                logger.info(f"Processing architecture {idx}/{total_architectures}: {arch_name}")
                
                main_span.add_event("architecture_processing_started", {
                    "index": idx,
                    "total": total_architectures,
                    "name": arch_name
                })
                
                # Calculate progress percentage within 20-85% range (65% span) during architecture processing
                progress_pct = 20 + int((idx / total_architectures) * 65)
                
                # Report progress via callback if provided
                if progress_callback:
                    await progress_callback(
                        f"Analyzing architecture {idx}/{total_architectures}: {arch_name}",
                        progress_pct
                    )
                
                if operation and operation_service:
                    operation.update_progress(f"Analyzing architecture {idx}/{total_architectures}: {arch_name}", 
                                            progress_pct, OperationStatus.IN_PROGRESS)
                    await operation_service.update_operation(operation)
                
                # Run security analysis with shared agent
                result = await run_single_architecture_security_analysis(
                    architecture_analysis=arch_analysis,
                    design_doc_content=extraction_result.get("design_doc_content", ""),
                    design_doc_url=design_doc_url,
                    analysis_instructions=analysis_instructions,
                    security_agent=security_agent,
                    app_id=app_id
                )
                
                architecture_results[arch_name] = result
                
                # Extract findings
                arch_findings = findings_extractor.extract_findings(result, arch_name, deficiency_counter)
                all_findings.extend(arch_findings)
                deficiency_counter += len(arch_findings)
            
            # STEP 5: Generate consolidated report
            if operation and operation_service:
                operation.update_progress("Generating consolidated report", 85, OperationStatus.IN_PROGRESS)
                await operation_service.update_operation(operation)
            
            main_span.add_event("report_generation_started", {
                "total_findings": len(all_findings),
                "architectures": total_architectures
            })
            
            consolidated_report = report_generator.generate_consolidated_report(all_findings, architecture_results)
            
            main_span.add_event("report_generated", {"report_size": len(consolidated_report)})
            
            # Upload report to architecture-analyzer/output/ folder
            blob_plugin = BlobStoragePlugin()
            report_timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%S")
            consolidated_blob_name = f"architecture-analyzer/output/consolidated_security_findings_report_{report_timestamp}.md"
            
            upload_result_json = await blob_plugin.upload_text_content(
                content=consolidated_report,
                blob_name=consolidated_blob_name,
                container_name=app_id,
                content_type="text/markdown"
            )
            
            upload_result = json.loads(upload_result_json) if isinstance(upload_result_json, str) else upload_result_json
            consolidated_report_url = upload_result.get("blob_url") if upload_result.get("status") == "success" else None
            
            # STEP 6: Cleanup security agent (cleanup function handles thread deletion)
            if operation and operation_service:
                operation.update_progress("Cleaning up resources", 95, OperationStatus.IN_PROGRESS)
                await operation_service.update_operation(operation)
            
            main_span.add_event("cleanup_started", {"agent_id": security_agent_id})
            
            # Use cleanup function which properly handles thread deletion for this specific agent
            cleanup_result = await cleanup_security_agent(
                app_id=app_id,
                agent=security_agent,
                agent_id=security_agent_id
            )
            
            main_span.add_event("cleanup_completed", {
                "status": cleanup_result.get("status"),
                "agent_deleted": cleanup_result.get("agent_deleted", False)
            })
            
            if operation and operation_service:
                operation.update_progress("Analysis completed", 100, OperationStatus.COMPLETED)
                await operation_service.update_operation(operation)
            
            execution_time = time.time() - function_start_time
            
            main_span.add_event("dynamic_analysis_completed", {
                "total_architectures": total_architectures,
                "total_findings": len(all_findings),
                "execution_time": execution_time,
                "has_report_url": consolidated_report_url is not None
            })
            add_span_attributes(main_span, {
                "analysis.total_architectures": total_architectures,
                "analysis.total_findings": len(all_findings),
                "analysis.execution_time": execution_time
            })
            main_span.set_status(Status(StatusCode.OK))
            
            return {
                "status": "success",
                "scf_validation": scf_validation,
                "total_architectures": total_architectures,
                "architecture_results": architecture_results,
                "all_findings": all_findings,
                "total_findings": len(all_findings),
                "consolidated_report_url": consolidated_report_url,
                "execution_time_seconds": round(execution_time, 2),
                "cleanup_performed": True,
                "timestamp": datetime.utcnow().isoformat()
            }
            
        except Exception as ex:
            error_message = str(ex)
            logger.error(f"Dynamic architecture analysis failed: {error_message}")
            
            # Cleanup security agent on error (cleanup function handles thread deletion)
            if security_agent:
                logger.info("Cleaning up security agent after error")
                try:
                    # Use cleanup function which properly handles thread deletion
                    cleanup_result = await cleanup_security_agent(
                        app_id=app_id,
                        agent=security_agent,
                        agent_id=security_agent_id
                    )
                    logger.info(f"Cleanup after error: {cleanup_result}")
                except Exception as cleanup_ex:
                    logger.warning(f"Cleanup after error failed: {str(cleanup_ex)}")
            
            if operation and operation_service:
                operation.update_progress(f"Failed: {error_message[:100]}", 0, OperationStatus.FAILED)
                await operation_service.update_operation(operation)
            
            main_span.record_exception(ex)
            main_span.add_event("dynamic_analysis_failed", {
                "error_type": type(ex).__name__,
                "error_message": error_message[:500]
            })
            main_span.set_status(Status(StatusCode.ERROR, error_message[:256]))
            
            return {
                "status": "error",
                "error": error_message,
                "cleanup_performed": security_agent_id is not None,
                "timestamp": datetime.utcnow().isoformat()
            }


async def run_single_architecture_security_analysis(
    architecture_analysis: Dict[str, Any],
    design_doc_content: str,
    design_doc_url: str,
    analysis_instructions: str,
    security_agent=None,
    app_id: str = None
) -> Dict[str, Any]:
    """
    Run security analysis for a single architecture diagram.
    
    Args:
        architecture_analysis: The architecture analysis data from extraction
        design_doc_content: The complete design document content
        design_doc_url: The design document URL
        analysis_instructions: Analysis instructions
        security_agent: Pre-created security agent to reuse (recommended)
        app_id: Application ID for agent naming (used if security_agent is None)
    
    Returns:
        Dict: Analysis result with security findings
    """
    tracer = get_tracer()
    with tracer.start_as_current_span("single_architecture_security_analysis") as span:
        try:
            components = architecture_analysis.get("components", [])
            sanitized_name = _sanitize_architecture_name(architecture_analysis)
            
            logger.info(f"Analyzing architecture '{sanitized_name}' with {len(components)} components")
            
            add_span_attributes(span, {
                "architecture.name": sanitized_name,
                "architecture.components_count": len(components),
                "architecture.agent_provided": security_agent is not None
            })
            span.add_event("security_analysis_started", {
                "architecture": sanitized_name,
                "components": len(components),
                "agent_reused": security_agent is not None
            })
            
            # Use SecurityAnalyzer with pre-created agent
            analyzer = SecurityAnalyzer(agent=security_agent)
            security_findings = await analyzer.analyze_components(
                components=components,
                architecture_name=sanitized_name,
                analysis_instructions=analysis_instructions,
                app_id=app_id
            )
            
            span.add_event("security_findings_obtained", {
                "findings_count": len(security_findings.get("identified_risks", []))
            })
            
            # Create individual architecture report (non-critical - don't fail if this errors)
            report_url = None
            try:
                blob_plugin = BlobStoragePlugin()
                report_result = await blob_plugin.create_architecture_report(
                    design_doc_url=design_doc_url,
                    design_doc_content=design_doc_content,
                    architecture_analysis=json.dumps(architecture_analysis),
                    security_findings=json.dumps(security_findings),
                    report_title=f"Security Analysis - {sanitized_name}"
                )
                
                if isinstance(report_result, str):
                    try:
                        report_result = json.loads(report_result)
                    except json.JSONDecodeError:
                        logger.warning(f"Could not parse report_result as JSON")
                        report_result = {"status": "error", "error": "Invalid JSON response"}
                
                report_url = report_result.get("blob_url") if isinstance(report_result, dict) and report_result.get("status") == "success" else None
                
                span.add_event("architecture_report_created", {
                    "has_report_url": report_url is not None
                })
            except Exception as report_ex:
                logger.warning(f"Failed to generate individual report (non-critical): {str(report_ex)}")
                span.add_event("report_generation_failed", {"error": str(report_ex)[:200]})
                # Continue with analysis - report generation failure shouldn't fail the security analysis
            
            add_span_attributes(span, {
                "analysis.status": "success",
                "report.url": report_url[:200] if report_url else "none"
            })
            span.set_status(Status(StatusCode.OK))
            
            return {
                "status": "success",
                "architecture_name": sanitized_name,
                "components_analyzed": len(components),
                "security_findings": security_findings,
                "report_url": report_url,
                "agent_response": security_findings
            }
                
        except Exception as ex:
            logger.error(f"Security analysis failed for architecture: {str(ex)}")
            error_arch_name = _sanitize_architecture_name(architecture_analysis)
            
            span.record_exception(ex)
            span.add_event("security_analysis_failed", {
                "error": str(ex)[:500],
                "architecture": error_arch_name
            })
            span.set_status(Status(StatusCode.ERROR, str(ex)[:256]))
            
            return {
                "status": "error",
                "error": str(ex),
                "architecture_name": error_arch_name
            }


@trace_async_function("analyze_multiple_architectures")
async def analyze_multiple_architectures(
    architecture_urls: Dict[str, str],
    analysis_instructions: str = "Analyze this architecture for security compliance and generate recommendations",
    operation: Optional[OperationRecord] = None,
    is_async: bool = False
) -> Dict[str, Any]:
    """
    Analyze multiple architecture design documents in batch mode.
    
    Args:
        architecture_urls: Dict mapping architecture_name to design_doc_url
        analysis_instructions: Custom instructions for the architecture analysis
        operation: Optional operation record for tracking
        is_async: Whether this is running in async mode
    
    Returns:
        Dict: Result containing consolidated findings and report URL
    """
    logger.info(f"Starting batch architecture analysis for {len(architecture_urls)} architectures")
    
    tracer = get_tracer()
    function_start_time = time.time()
    
    operation_service = None
    if operation:
        from operation_service import get_operation_service
        operation_service = get_operation_service()
    
    with tracer.start_as_current_span("batch_architecture_analysis") as main_span:
        add_span_attributes(main_span, {
            "architecture.count": len(architecture_urls),
            "architecture.is_async": is_async
        })
        
        try:
            # Initialize components
            report_generator = ReportGenerator()
            findings_extractor = FindingsExtractor()
            
            architecture_results = {}
            all_findings = []
            deficiency_counter = 1
            total_architectures = len(architecture_urls)
            
            for idx, (arch_name, design_doc_url) in enumerate(architecture_urls.items(), 1):
                logger.info(f"Processing architecture {idx}/{total_architectures}: {arch_name}")
                
                main_span.add_event("batch_architecture_started", {
                    "index": idx,
                    "total": total_architectures,
                    "name": arch_name
                })
                
                if operation and operation_service:
                    progress = (idx / total_architectures * 80)
                    operation.update_progress(f"Analyzing {arch_name}", progress, OperationStatus.IN_PROGRESS)
                    await operation_service.update_operation(operation)
                
                # Run single analysis
                result = await analyze_single_architecture(design_doc_url, analysis_instructions)
                architecture_results[arch_name] = result
                
                # Extract findings
                arch_findings = findings_extractor.extract_findings(result, arch_name, deficiency_counter)
                all_findings.extend(arch_findings)
                deficiency_counter += len(arch_findings)
            
            # Generate consolidated report
            if operation and operation_service:
                operation.update_progress("Generating report", 90, OperationStatus.IN_PROGRESS)
                await operation_service.update_operation(operation)
            
            main_span.add_event("batch_report_generation_started", {
                "findings_count": len(all_findings)
            })
            
            consolidated_report = report_generator.generate_consolidated_report(all_findings, architecture_results)
            
            # Upload report to architecture-analyzer/output/ folder
            blob_plugin = BlobStoragePlugin()
            report_timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%S")
            consolidated_blob_name = f"architecture-analyzer/output/consolidated_security_findings_report_{report_timestamp}.md"
            
            main_span.add_event("batch_report_upload_started", {
                "blob_name": consolidated_blob_name
            })
            
            upload_result_json = await blob_plugin.upload_text_content(
                content=consolidated_report,
                blob_name=consolidated_blob_name,
                content_type="text/markdown"
            )
            
            upload_result = json.loads(upload_result_json) if isinstance(upload_result_json, str) else upload_result_json
            consolidated_report_url = upload_result.get("blob_url") if upload_result.get("status") == "success" else None
            
            if operation and operation_service:
                operation.update_progress("Completed", 100, OperationStatus.COMPLETED)
                await operation_service.update_operation(operation)
            
            execution_time = time.time() - function_start_time
            
            main_span.add_event("batch_analysis_completed", {
                "total_architectures": total_architectures,
                "total_findings": len(all_findings),
                "execution_time": execution_time
            })
            add_span_attributes(main_span, {
                "batch.total_architectures": total_architectures,
                "batch.total_findings": len(all_findings),
                "batch.execution_time": execution_time
            })
            main_span.set_status(Status(StatusCode.OK))
            
            return {
                "status": "success",
                "total_architectures": total_architectures,
                "architecture_results": architecture_results,
                "total_findings": len(all_findings),
                "consolidated_report_url": consolidated_report_url,
                "execution_time_seconds": round(execution_time, 2),
                "timestamp": datetime.utcnow().isoformat()
            }
            
        except Exception as ex:
            error_message = str(ex)
            logger.error(f"Batch architecture analysis failed: {error_message}")
            
            if operation and operation_service:
                operation.update_progress(f"Failed: {error_message[:100]}", 0, OperationStatus.FAILED)
                await operation_service.update_operation(operation)
            
            main_span.record_exception(ex)
            main_span.add_event("batch_analysis_failed", {
                "error_type": type(ex).__name__,
                "error_message": error_message[:500]
            })
            main_span.set_status(Status(StatusCode.ERROR, error_message[:256]))
            
            return {
                "status": "error",
                "error": error_message,
                "timestamp": datetime.utcnow().isoformat()
            }


# Helper functions

def _sanitize_architecture_name(architecture_analysis: Dict[str, Any], index: int = 0) -> str:
    """Sanitize architecture name for use in file paths."""
    image_name = architecture_analysis.get("image_name", "Unknown")
    
    # Clean URL parameters
    if "?" in image_name:
        image_name = image_name.split("?")[0]
    if "&" in image_name:
        image_name = image_name.split("&")[0]
    
    # URL decode
    image_name = urllib.parse.unquote(image_name)
    
    # Extract filename
    image_name = Path(image_name).name
    
    # Remove file extension
    if image_name.endswith(".png"):
        image_name = image_name[:-4]
    
    # Replace special characters
    import re
    sanitized_name = re.sub(r'[^\w\s-]', '_', image_name)
    sanitized_name = re.sub(r'[-\s]+', '_', sanitized_name)
    sanitized_name = sanitized_name.strip('_')
    
    # Fallback
    if not sanitized_name or sanitized_name == '_':
        if index > 0:
            sanitized_name = f"Architecture_{index}"
        else:
            sanitized_name = f"Architecture_{abs(hash(architecture_analysis.get('image_url', 'unknown')))}"
    
    return sanitized_name


def _build_fallback_instructions(design_doc_url: str, analysis_instructions: str) -> str:
    """Build fallback agent instructions when file-based instructions are not available."""
    return f"""You are an Architecture Analyzer Agent that performs comprehensive security compliance analysis.

WORKFLOW - Execute these steps sequentially:

STEP 1: Extract Architecture from Blob Storage
- Call: extract_and_analyze_architecture(design_doc_url="{design_doc_url}")
- This extracts the architecture from blob storage and identifies all components, patterns, and services
- Store the COMPLETE result including: Design-doc_content, summary, architecture_analyses

STEP 2: Security Compliance Research (Using AzureAISearch)
- For EACH component/service identified in STEP 1, use the built-in AzureAISearch tool to find:
  * Security best practices for that component
  * Compliance requirements (SCF controls, NIST frameworks)
  * Common security gaps and risks
  * Recommended security controls
  
- Aggregate all search results into a comprehensive security findings structure with:
  * identified_risks: List of security risks found
  * missing_controls: List of missing security controls
  * compliance_gaps: List of compliance gaps
  * recommendations: List of remediation recommendations
  * scf_control_mapping: Mapping of components to SCF controls

STEP 3: Generate Comprehensive Report
- Call: create_architecture_report with these parameters:
  * design_doc_url: "{design_doc_url}"
  * design_doc_content: <STEP 1 design_doc_content - pass verbatim>
  * architecture_analysis: <STEP 1 COMPLETE result as JSON string>
  * security_findings: <STEP 2 aggregated findings as JSON string>
  * report_title: "Architecture Security Compliance Analysis"

CRITICAL RULES:
1. Execute steps in order: Extract → Research → Report
2. Use AzureAISearch to query the knowledge base for security information
3. DO NOT truncate JSON strings - pass COMPLETE data structures
4. DO NOT add comments or markers like [...] in JSON
5. Aggregate multiple search results into a cohesive security analysis
6. Map findings to specific architecture components from STEP 1

Design Document URL: {design_doc_url}
Analysis Focus: {analysis_instructions}

Begin with STEP 1."""


def _build_workflow_message(design_doc_url: str) -> str:
    """Build the workflow message for the agent."""
    return f"""
You are an Architecture Analyzer Agent. You MUST execute the following workflow exactly ONCE, in order, with NO steps skipped or repeated. Follow these rules:

1. Call DesignDocExtractor.extract_and_analyze_architecture(design_doc_url="{design_doc_url}") and WAIT for the complete result.
2. For EACH component in summary.all_components from STEP 1, use the built-in AzureAISearch tool to research security best practices, compliance requirements, risks, controls, and SCF mappings. Aggregate all findings into a valid, complete security_findings JSON object as described in your instructions.
3. Call BlobStorage.create_architecture_report with:
    - design_doc_url="{design_doc_url}"
    - design_doc_content=<verbatim design document content from STEP 1>
    - architecture_analysis=json.dumps(<COMPLETE STEP 1 result>)
    - security_findings=json.dumps(<COMPLETE STEP 2 result>)
    - report_title="Architecture Security Compliance Analysis"

STRICT RULES:
- Execute each function ONCE, in order, and WAIT for results before proceeding.
- DO NOT summarize, truncate, or modify JSON results. Pass COMPLETE, valid JSON objects.
- DO NOT add comments, ellipsis, or markers to JSON.
- The workflow runs ONCE and does not repeat.

Begin with STEP 1 now. Do not describe the workflow, just execute it.
"""


# Wrapper functions for backward compatibility

async def _analyze_with_operation_tracking(request, operation: Optional[OperationRecord] = None, is_async: bool = False):
    """Core architecture analysis logic (backward compatibility wrapper)."""
    try:
        result = await analyze_single_architecture(
            design_doc_url=request.design_doc_url,
            analysis_instructions=request.analysis_instructions,
            operation=operation,
            is_async=is_async
        )
        return result
    except Exception as e:
        logger.error(f"Architecture analysis core failed: {str(e)}")
        raise


async def _background_analysis_task(operation: OperationRecord, request):
    """Background task wrapper for async architecture analysis."""
    try:
        await _analyze_with_operation_tracking(request, operation, is_async=True)
    except asyncio.CancelledError:
        logger.info("Background architecture analysis task was cancelled")
        raise
    except Exception as e:
        logger.error(f"Background task failed: {str(e)}", exc_info=True)


# Main execution function
async def main() -> None:
    """Main function for standalone execution"""
    try:
        design_doc_url = "design-documents/example/architecture.md"
        result = await analyze_single_architecture(design_doc_url)
        logger.info(f"Architecture Agent execution result: {result}")
        logger.info("Processing done")
    except Exception as ex:
        logger.error(f"Error in architecture agent main execution: {str(ex)}")
        current_span = trace.get_current_span()
        if current_span and current_span.is_recording():
            current_span.record_exception(ex)
            current_span.set_status(Status(StatusCode.ERROR, str(ex)))


# Export functions
__all__ = [
    'analyze_single_architecture',
    'run_dynamic_architecture_analysis',
    'analyze_multiple_architectures',
    'cleanup_architecture_agent',
    'cleanup_security_agent',
    '_analyze_with_operation_tracking',
    '_background_analysis_task'
]


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Architecture agent interrupted by user (KeyboardInterrupt)")
    except asyncio.CancelledError:
        logger.info("Architecture agent run cancelled")
    except Exception as ex:
        logger.error(f"Unhandled exception in architecture agent main: {str(ex)}", exc_info=True)
