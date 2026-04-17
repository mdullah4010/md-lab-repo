"""
Code Analyzer Agent - Pure Semantic Kernel Implementation

This module provides a pure Semantic Kernel based code analyzer that replaces
the previous AgentFactory pattern. It uses:
- AzureAIAgent for agent creation
- GroupChatOrchestration for multi-agent collaboration
- Kernel plugins for all tool functionality

The design follows Semantic Kernel best practices with proper plugin architecture.
Migrated from deprecated AgentGroupChat to new GroupChatOrchestration pattern.
"""

import os
import asyncio
import zipfile
import json
from datetime import timedelta, datetime
from typing import ClassVar, Dict, List, Optional, Any
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

# Semantic Kernel imports
from semantic_kernel import Kernel
from semantic_kernel.agents import AzureAIAgent, GroupChatOrchestration, RoundRobinGroupChatManager, BooleanResult, StringResult
from semantic_kernel.agents.runtime import InProcessRuntime
from semantic_kernel.contents import ChatMessageContent
from semantic_kernel.contents.utils.author_role import AuthorRole
from semantic_kernel.filters.functions.function_invocation_context import FunctionInvocationContext
from semantic_kernel.filters.filter_types import FilterTypes

# Azure imports
from azure.identity.aio import DefaultAzureCredential
from azure.ai.agents.models import FilePurpose, CodeInterpreterTool
from collections import deque
from datetime import datetime

# Tracing imports - Intake uses agents. prefix
try:
    from agents.tracing_config import (
        get_tracer,
        trace_async_function,
        add_span_attributes,
        record_agent_execution,
        record_tool_call,
        record_batch_operation,
        record_error_details
    )
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode
    TRACING_AVAILABLE = True
except ImportError:
    TRACING_AVAILABLE = False
    # Provide no-op fallbacks if tracing not available
    def get_tracer():
        return None
    def trace_async_function(name):
        def decorator(func):
            return func
        return decorator
    def add_span_attributes(span, attrs):
        pass
    def record_agent_execution(*args, **kwargs):
        pass
    def record_tool_call(*args, **kwargs):
        pass
    def record_batch_operation(*args, **kwargs):
        pass
    def record_error_details(*args, **kwargs):
        pass


# =============================================================================
# TOOL CALL LOGGER - KEEPS A TAIL OF RECENT TOOL CALLS
# =============================================================================

class ToolCallLogger:
    """
    Keeps a tail of tool calls for debugging and monitoring.
    Use get_tail() to see recent calls, or get_all() for full history.
    """
    
    def __init__(self, max_entries: int = 100):
        self._calls = deque(maxlen=max_entries)
        self._max_entries = max_entries
    
    def log_call(
        self, 
        function_name: str, 
        plugin_name: str = None,
        arguments: Dict[str, Any] = None,
        result: Any = None,
        duration_ms: float = None,
        agent_name: str = None,
        error: str = None
    ):
        """Log a tool/function call."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "function": function_name,
            "plugin": plugin_name,
            "agent": agent_name,
            "arguments": self._truncate_args(arguments) if arguments else None,
            "result_preview": self._truncate_result(result) if result else None,
            "duration_ms": duration_ms,
            "success": error is None,
            "error": error
        }
        self._calls.append(entry)
        
        # Also log to standard logger
        status = "✅" if error is None else "❌"
        duration_str = f" ({duration_ms:.1f}ms)" if duration_ms else ""
        agent_str = f"[{agent_name}]" if agent_name else ""
        logger.info(f"🔧 {status} {agent_str} {plugin_name}.{function_name}{duration_str}")
        if arguments:
            logger.debug(f"   Args: {self._truncate_args(arguments)}")
        if error:
            logger.error(f"   Error: {error}")
    
    def _truncate_args(self, args: Dict[str, Any], max_len: int = 200) -> Dict[str, Any]:
        """Truncate long argument values for readability."""
        result = {}
        for key, value in (args or {}).items():
            str_val = str(value)
            if len(str_val) > max_len:
                result[key] = str_val[:max_len] + "..."
            else:
                result[key] = value
        return result
    
    def _truncate_result(self, result: Any, max_len: int = 200) -> str:
        """Truncate result for preview."""
        str_result = str(result)
        if len(str_result) > max_len:
            return str_result[:max_len] + "..."
        return str_result
    
    def get_tail(self, n: int = 10) -> List[Dict[str, Any]]:
        """Get the last N tool calls."""
        return list(self._calls)[-n:]
    
    def get_all(self) -> List[Dict[str, Any]]:
        """Get all logged tool calls."""
        return list(self._calls)
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary statistics of tool calls."""
        all_calls = list(self._calls)
        if not all_calls:
            return {"total": 0, "by_function": {}, "by_agent": {}, "errors": 0}
        
        by_function = {}
        by_agent = {}
        errors = 0
        
        for call in all_calls:
            func = call["function"]
            agent = call.get("agent") or "unknown"
            
            by_function[func] = by_function.get(func, 0) + 1
            by_agent[agent] = by_agent.get(agent, 0) + 1
            if not call["success"]:
                errors += 1
        
        return {
            "total": len(all_calls),
            "by_function": by_function,
            "by_agent": by_agent,
            "errors": errors
        }
    
    def clear(self):
        """Clear all logged calls."""
        self._calls.clear()
    
    def print_tail(self, n: int = 10):
        """Print the last N tool calls to console."""
        print(f"\n{'='*60}")
        print(f"TOOL CALL LOG (last {n} calls)")
        print('='*60)
        
        for i, call in enumerate(self.get_tail(n), 1):
            status = "✅" if call["success"] else "❌"
            print(f"\n{i}. {status} {call['timestamp']}")
            print(f"   Function: {call['plugin']}.{call['function']}")
            if call["agent"]:
                print(f"   Agent: {call['agent']}")
            if call["arguments"]:
                print(f"   Args: {call['arguments']}")
            if call["result_preview"]:
                print(f"   Result: {call['result_preview']}")
            if call["error"]:
                print(f"   Error: {call['error']}")
            if call["duration_ms"]:
                print(f"   Duration: {call['duration_ms']:.1f}ms")
        
        print(f"\n{'='*60}\n")


# Global tool call logger instance
_tool_call_logger = ToolCallLogger(max_entries=100)


def get_tool_call_logger() -> ToolCallLogger:
    """Get the global tool call logger instance."""
    return _tool_call_logger

# Import our unified plugin - Intake uses agents. prefix
from agents.code_analyzer.src.plugins.code_analyzer_plugin import CodeAnalyzerPlugin

# Setup logging - use project's logging config for consistent output
try:
    from agents.logging_config import get_logger
    logger = get_logger(__name__)
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

# Import SCF Search Plugin for Azure AI Search integration
try:
    from agents.code_analyzer.src.plugins.scf_search_plugin import SCFSearchPlugin, create_scf_search_plugin
    SCF_SEARCH_AVAILABLE = True
    logger.info("✅ SCF Search Plugin import successful")
except ImportError as e:
    SCF_SEARCH_AVAILABLE = False
    logger.warning(f"⚠️ SCF Search Plugin not available - Azure AI Search integration disabled: {e}")

load_dotenv()

# Log tracing availability
if TRACING_AVAILABLE:
    logger.info("✅ OpenTelemetry tracing available for Code Analyzer")
else:
    logger.warning("⚠️ OpenTelemetry tracing not available - metrics will not be collected")


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class AgentConfig:
    """Configuration for creating an agent."""
    name: str
    instructions: str
    model: str
    description: str = ""
    use_code_interpreter: bool = True
    use_plugins: bool = False  # Whether to attach kernel plugins (code_tools)
    use_scf_search: bool = False  # Whether to attach SCF Search plugin for Azure AI Search


@dataclass 
class CodeAnalyzerConfig:
    """Configuration for the code analyzer workflow."""
    agents: List[AgentConfig] = field(default_factory=list)
    base_model: str = "gpt-4o"
    initial_message: str = ""
    enable_file_upload: bool = True
    perform_security_scan: bool = True
    max_iterations: int = 10
    polling_timeout_minutes: int = 30
    enable_scf_search: bool = False  # Whether to enable SCF (Secure Controls Framework) search via Azure AI Search


# =============================================================================
# CUSTOM GROUP CHAT MANAGER (Replaces deprecated AgentGroupChat strategies)
# =============================================================================

class CodeAnalyzerGroupChatManager(RoundRobinGroupChatManager):
    """
    Custom GroupChatManager that implements orchestrator-based agent selection
    and PERFECTUS-based termination for code analysis workflows.
    
    This replaces the deprecated OrchestratorSelectionStrategy and 
    CompletionTerminationStrategy from the old AgentGroupChat pattern.
    """
    
    TERMINATION_SIGNAL: ClassVar[str] = "PERFECTUS"
    
    def __init__(
        self, 
        orchestrator_name: str,
        agent_names: List[str],
        max_rounds: int = 10
    ):
        """
        Initialize the custom group chat manager.
        
        Args:
            orchestrator_name: Name of the orchestrator agent
            agent_names: List of all agent names (including orchestrator)
            max_rounds: Maximum number of rounds before forced termination
        """
        super().__init__(max_rounds=max_rounds)
        self._orchestrator_name = orchestrator_name
        self._agent_names = agent_names
        self._current_agent_index = 0
        self._iteration_count = 0
    
    async def should_terminate(self, chat_history) -> BooleanResult:
        """
        Determine if the group chat should terminate.
        
        Termination occurs when:
        1. PERFECTUS signal is received in the last message
        2. Maximum rounds exceeded
        """
        self._iteration_count += 1
        
        # Check max rounds
        if self._iteration_count >= self.max_rounds:
            logger.warning(f"🛑 Max rounds ({self.max_rounds}) reached, terminating")
            return BooleanResult(
                result=True, 
                reason=f"Maximum rounds ({self.max_rounds}) exceeded"
            )
        
        # Check for PERFECTUS signal in last message
        if chat_history:
            last_message = chat_history[-1]
            content = getattr(last_message, 'content', '') or ''
            if self.TERMINATION_SIGNAL.lower() in content.lower():
                logger.info("✅ PERFECTUS signal received, terminating conversation")
                return BooleanResult(
                    result=True, 
                    reason="PERFECTUS signal received - analysis complete"
                )
        
        return BooleanResult(result=False, reason="Analysis in progress")
    
    async def select_next_agent(self, chat_history, participant_descriptions: dict[str, str]) -> StringResult:
        """
        Select the next agent to take a turn in the group chat.
        
        The selection logic is:
        1. If no history or last message wasn't from orchestrator -> orchestrator
        2. Parse orchestrator's last message to determine next agent
        3. Handle PERFECTUS signal
        
        Args:
            chat_history: The conversation history
            participant_descriptions: Dict mapping agent name to description
            
        Returns:
            StringResult with the name of the next agent
        """
        # Get list of agent names from participant_descriptions
        agent_names = list(participant_descriptions.keys())
        
        # Find orchestrator agent name
        orchestrator_name = None
        for name in agent_names:
            if name == self._orchestrator_name or name.lower() == self._orchestrator_name.lower():
                orchestrator_name = name
                break
        
        if not orchestrator_name:
            logger.error(f"Orchestrator '{self._orchestrator_name}' not found in agents: {agent_names}")
            return StringResult(result=agent_names[0] if agent_names else "", reason="Orchestrator not found, selecting first agent")
        
        # If no history, start with orchestrator
        if not chat_history:
            return StringResult(result=orchestrator_name, reason="Starting with orchestrator")
        
        last_message = chat_history[-1]
        last_agent_name = getattr(last_message, 'name', None)
        
        # If last message wasn't from orchestrator, return orchestrator
        if last_agent_name != self._orchestrator_name:
            return StringResult(result=orchestrator_name, reason="Returning to orchestrator for next decision")
        
        # Parse orchestrator's selection
        raw_selection = getattr(last_message, 'content', '') or ''
        raw_selection = raw_selection.strip()
        
        logger.debug(f"Orchestrator raw output: {raw_selection[:200]}")
        
        # Extract agent name from response
        selection = self._extract_agent_name_from_response(raw_selection, agent_names)
        logger.debug(f"Extracted agent name: {selection}")
        
        # Check for termination signal
        if self.TERMINATION_SIGNAL.lower() in selection.lower():
            logger.info("🏁 Orchestrator signaled completion (PERFECTUS)")
            return StringResult(result=orchestrator_name, reason="Orchestrator signaled completion")
        
        # Find selected agent (case-insensitive match)
        selected_name = None
        for name in agent_names:
            if name == selection:
                selected_name = name
                break
            if name.lower() == selection.lower():
                selected_name = name
                break
        
        if selected_name is None:
            # Try partial match
            for name in agent_names:
                if name.lower() in selection.lower():
                    logger.info(f"Partial match found: '{name}' in '{selection}'")
                    return StringResult(result=name, reason=f"Partial match for '{selection}'")
            
            logger.warning(f"Unknown agent '{selection}', returning to orchestrator")
            # Check if it looks like a tool name (common mistake)
            tool_keywords = ['file_writer', 'code_interpreter', 'python', 'tool', 'create_file']
            if any(kw in selection.lower() for kw in tool_keywords):
                logger.warning(f"'{selection}' appears to be a tool name, not an agent!")
            return StringResult(result=orchestrator_name, reason=f"Unknown agent '{selection}', returning to orchestrator")
        
        return StringResult(result=selected_name, reason=f"Selected by orchestrator: {selection}")
    
    def _extract_agent_name_from_response(self, raw_response: str, agent_names: List[str]) -> str:
        """
        Extract agent name from potentially messy orchestrator response.
        
        Args:
            raw_response: The raw response from the orchestrator
            agent_names: List of valid agent names (strings)
            
        Returns:
            The extracted agent name
        """
        import re
        
        # If response is a single word or word-hyphen-word pattern, use it directly
        if len(raw_response.split()) == 1:
            return raw_response.strip()
        
        # Try to find agent name after common patterns
        patterns = [
            r'next (?:agent|step) is[:\s]+([\w-]+)',
            r'select[:\s]+([\w-]+)',
            r'choose[:\s]+([\w-]+)',
            r'^([\w-]+)$',
            r':\s*([\w-]+)\s*$',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, raw_response, re.IGNORECASE)
            if match:
                return match.group(1)
        
        # Check if PERFECTUS appears anywhere
        if 'perfectus' in raw_response.lower():
            return 'PERFECTUS'
        
        # Check if any known agent name appears in the response
        for name in agent_names:
            if name.lower() in raw_response.lower():
                return name
        
        # Fallback: return first word
        return raw_response.split()[0] if raw_response.split() else raw_response


# =============================================================================
# MAIN CODE ANALYZER CLASS
# =============================================================================

class SemanticKernelCodeAnalyzer:
    """
    Pure Semantic Kernel implementation of the code analyzer.
    
    This class manages the complete code analysis workflow:
    1. Configuration loading
    2. File processing and security scanning
    3. Agent creation with proper plugins
    4. Multi-agent group chat execution
    5. Result collection and cleanup
    """
    
    def __init__(self, config: CodeAnalyzerConfig, output_directory: str = None, app_id: str = None):
        """
        Initialize the code analyzer.
        
        Args:
            config: Configuration for the analysis workflow
            output_directory: Directory for output files (if None, plugin creates temp dir)
            app_id: Application ID to append to agent names for multi-tenant isolation
        """
        self.config = config
        self.output_directory = output_directory
        self.app_id = app_id
        
        # Will be set during execution
        self._client = None
        self._agents: Dict[str, AzureAIAgent] = {}
        self._uploaded_files: List[Any] = []
        self._plugin = CodeAnalyzerPlugin(output_directory=self.output_directory)
        
        # Initialize SCF Search Plugin if enabled and available
        self._scf_plugin = None
        logger.info(f"🔍 SCF Config: enable_scf_search={config.enable_scf_search}, SCF_SEARCH_AVAILABLE={SCF_SEARCH_AVAILABLE}")
        if config.enable_scf_search and SCF_SEARCH_AVAILABLE:
            try:
                self._scf_plugin = create_scf_search_plugin()
                logger.info("✅ SCF Search Plugin initialized for Azure AI Search integration")
            except Exception as e:
                logger.warning(f"❌ Failed to initialize SCF Search Plugin: {e}")
        elif config.enable_scf_search and not SCF_SEARCH_AVAILABLE:
            logger.warning("⚠️ SCF Search requested but plugin not available - check dependencies")
        elif not config.enable_scf_search:
            logger.info("ℹ️ SCF Search not enabled in config")
        
        logger.info(f"SemanticKernelCodeAnalyzer initialized with {len(config.agents)} agent configs")
    
    def cleanup_temp_files(self):
        """Cleanup temporary files created by the plugin. Call this after reading report files."""
        if self._plugin:
            self._plugin.cleanup()
    
    @classmethod
    def from_config_file(cls, config_path: str, output_directory: str = None, app_id: str = None) -> "SemanticKernelCodeAnalyzer":
        """
        Create a code analyzer from a JSON configuration file.
        
        Args:
            config_path: Path to the JSON config file
            output_directory: Optional output directory override
            app_id: Application ID to append to agent names for multi-tenant isolation
            
        Returns:
            Configured SemanticKernelCodeAnalyzer instance
        """
        with open(config_path, 'r', encoding='utf-8') as f:
            config_data = json.load(f)
        
        config_dir = Path(config_path).parent
        
        # Parse agent configurations
        agents = []
        for agent_data in config_data.get('agents', []):
            # Load instructions from file if specified
            instructions = agent_data.get('instructions', '')
            if 'instructions_file' in agent_data:
                instructions_path = config_dir / agent_data['instructions_file']
                if instructions_path.exists():
                    instructions = instructions_path.read_text(encoding='utf-8')
            
            # Inject current date into instructions (replace placeholders or prepend date context)
            current_date = datetime.now().strftime("%Y-%m-%d")
            date_context = f"\n\n**IMPORTANT - TODAY'S DATE IS: {current_date}**\nYou MUST use this exact date ({current_date}) in ALL date fields in your reports. Do NOT use any other date.\n\n"
            
            # Prepend date context to ensure the AI knows today's date
            instructions = date_context + instructions
            
            agents.append(AgentConfig(
                name=agent_data['name'],
                instructions=instructions,
                model=agent_data.get('model', 'gpt-4o'),
                description=agent_data.get('description', ''),
                use_code_interpreter=True,
                use_plugins=agent_data.get('file_writer', False),
                use_scf_search=agent_data.get('scf_search', False)
            ))
        
        config = CodeAnalyzerConfig(
            agents=agents,
            base_model=config_data.get('base_model', 'gpt-4o'),
            initial_message=config_data.get('initial_message', ''),
            enable_file_upload=config_data.get('uploads', True),
            perform_security_scan=True,
            enable_scf_search=config_data.get('scf_search_enabled', False)
        )
        
        return cls(config, output_directory, app_id)
    
    async def analyze(
        self, 
        files_path: str,
        task_message: str = None,
        perform_security_scan: bool = None,
        progress_callback: callable = None
    ) -> Dict[str, Any]:
        """
        Execute the code analysis workflow.
        
        Args:
            files_path: Path to the directory containing files to analyze
            task_message: Optional override for the initial task message
            perform_security_scan: Optional override for security scan setting
            progress_callback: Optional async callback function(step_name: str, progress: int) for progress updates
            
        Returns:
            Dictionary containing analysis results and metadata
        """
        task = task_message or self.config.initial_message
        do_security_scan = perform_security_scan if perform_security_scan is not None else self.config.perform_security_scan
        
        # Helper to call progress callback if available
        async def update_progress(step_name: str, progress: int):
            if progress_callback:
                try:
                    await progress_callback(step_name, progress)
                except Exception as ex:
                    logger.warning(f"Progress callback failed: {ex}")
        
        # Clear tool call logger for this run
        _tool_call_logger.clear()
        logger.info("🔧 Tool call logging enabled - use get_tool_call_logger() to inspect")
        
        result = {
            "status": "in_progress",
            "messages": [],
            "agents_used": [],
            "security_scan": None,
            "files_processed": 0
        }
        
        endpoint = os.getenv("AZURE_EXISTING_AIPROJECT_ENDPOINT") or os.getenv("FOUNDRY_PROJECT_ENDPOINT")
        if not endpoint:
            raise ValueError("AZURE_EXISTING_AIPROJECT_ENDPOINT or FOUNDRY_PROJECT_ENDPOINT not set")
        
        # Get tracer for distributed tracing
        tracer = get_tracer()
        analysis_start_time = datetime.now()
        
        async with (
            DefaultAzureCredential(exclude_shared_token_cache_credential=True) as creds,
            AzureAIAgent.create_client(credential=creds, endpoint=endpoint) as client,
        ):
            self._client = client
            
            # Start main analysis span
            analysis_span = None
            if TRACING_AVAILABLE and tracer:
                analysis_span = tracer.start_span("code_analyzer.analyze")
                add_span_attributes(analysis_span, {
                    "code_analyzer.files_path": files_path,
                    "code_analyzer.security_scan_enabled": do_security_scan,
                    "code_analyzer.agent_count": len(self.config.agents),
                    "code_analyzer.scf_search_enabled": self.config.enable_scf_search
                })
            
            try:
                # Step 1: Security scan (if enabled) - 45%
                if do_security_scan:
                    logger.info("Performing security scan...")
                    await update_progress("Scanning for secrets", 45)
                    
                    # Trace security scan
                    if TRACING_AVAILABLE and tracer:
                        with tracer.start_as_current_span("code_analyzer.security_scan") as scan_span:
                            scan_result_json = self._plugin.scan_for_secrets(files_path)
                            scan_result = json.loads(scan_result_json)
                            add_span_attributes(scan_span, {
                                "security_scan.total_files": scan_result.get("total_files_scanned", 0),
                                "security_scan.secrets_found": scan_result.get("total_secrets_found", 0),
                                "security_scan.files_with_secrets": len(scan_result.get("files_with_secrets", []))
                            })
                            if scan_result.get("total_secrets_found", 0) > 0:
                                scan_span.add_event("secrets_detected", {
                                    "count": scan_result.get("total_secrets_found", 0)
                                })
                    else:
                        scan_result_json = self._plugin.scan_for_secrets(files_path)
                        scan_result = json.loads(scan_result_json)
                    
                    result["security_scan"] = scan_result
                    files_with_secrets = scan_result.get("files_with_secrets", [])
                else:
                    files_with_secrets = []
                    result["security_scan"] = {"performed": False}
                
                # Step 2: Create ZIP and upload files - 50%
                if self.config.enable_file_upload:
                    await update_progress("Preparing files for analysis", 50)
                    
                    # Trace ZIP creation
                    if TRACING_AVAILABLE and tracer:
                        with tracer.start_as_current_span("code_analyzer.create_zip") as zip_span:
                            zip_result_json = self._plugin.create_zip_archive(
                                source_directory=files_path,
                                exclude_files=json.dumps(files_with_secrets),
                                output_name="analysis_files"
                            )
                            zip_result = json.loads(zip_result_json)
                            add_span_attributes(zip_span, {
                                "zip.files_added": zip_result.get("files_added", 0),
                                "zip.success": zip_result.get("success", False)
                            })
                    else:
                        zip_result_json = self._plugin.create_zip_archive(
                            source_directory=files_path,
                            exclude_files=json.dumps(files_with_secrets),
                            output_name="analysis_files"
                        )
                        zip_result = json.loads(zip_result_json)
                    
                    logger.info(f"📦 ZIP creation result: {zip_result}")
                    
                    if zip_result.get("success"):
                        zip_path = zip_result["zip_path"]
                        
                        # Verify ZIP file exists and has content
                        if os.path.exists(zip_path):
                            zip_size = os.path.getsize(zip_path)
                            logger.info(f"📦 ZIP file size: {zip_size} bytes at {zip_path}")
                            
                            # Verify ZIP contents
                            with zipfile.ZipFile(zip_path, 'r') as zf:
                                zip_contents = zf.namelist()
                                logger.info(f"📦 ZIP contains {len(zip_contents)} files")
                                if len(zip_contents) <= 5:
                                    logger.info(f"📦 ZIP files: {zip_contents}")
                                else:
                                    logger.info(f"📦 ZIP files (first 5): {zip_contents[:5]}")
                        else:
                            logger.error(f"❌ ZIP file does not exist: {zip_path}")
                        
                        logger.info(f"Uploading ZIP file: {zip_path}")
                        await update_progress("Uploading files to AI agents", 55)
                        
                        # Trace file upload
                        upload_start = datetime.now()
                        if TRACING_AVAILABLE and tracer:
                            with tracer.start_as_current_span("code_analyzer.upload_file") as upload_span:
                                uploaded_file = await client.agents.files.upload_and_poll(
                                    file_path=zip_path,
                                    purpose=FilePurpose.AGENTS
                                )
                                upload_duration = (datetime.now() - upload_start).total_seconds() * 1000
                                add_span_attributes(upload_span, {
                                    "upload.file_id": uploaded_file.id,
                                    "upload.filename": getattr(uploaded_file, 'filename', 'unknown'),
                                    "upload.size_bytes": zip_size,
                                    "upload.duration_ms": upload_duration
                                })
                        else:
                            uploaded_file = await client.agents.files.upload_and_poll(
                                file_path=zip_path,
                                purpose=FilePurpose.AGENTS
                            )
                        
                        logger.info(f"📤 Uploaded file ID: {uploaded_file.id}, filename: {getattr(uploaded_file, 'filename', 'unknown')}")
                        self._uploaded_files.append(uploaded_file)
                        result["files_processed"] = zip_result.get("files_added", 0)
                    else:
                        logger.error(f"❌ ZIP creation failed: {zip_result}")
                
                # Step 3: Create code interpreter tool - 60%
                await update_progress("Initializing code interpreter", 60)
                code_interpreter = CodeInterpreterTool(
                    file_ids=[f.id for f in self._uploaded_files]
                ) if self._uploaded_files else None
                
                # Step 4: Create agents - 65%
                await update_progress("Creating AI analysis agents", 65)
                
                # Trace agent creation
                if TRACING_AVAILABLE and tracer:
                    with tracer.start_as_current_span("code_analyzer.create_agents") as agents_span:
                        await self._create_agents(code_interpreter)
                        add_span_attributes(agents_span, {
                            "agents.count": len(self._agents),
                            "agents.names": ",".join(self._agents.keys())
                        })
                else:
                    await self._create_agents(code_interpreter)
                    
                result["agents_used"] = list(self._agents.keys())
                
                if not self._agents:
                    raise ValueError("No agents were created")
                
                # Step 5: Create orchestrator agent - 70%
                await update_progress("Starting multi-agent analysis", 70)
                
                # Trace orchestrator creation
                if TRACING_AVAILABLE and tracer:
                    with tracer.start_as_current_span("code_analyzer.create_orchestrator") as orch_span:
                        orchestrator = await self._create_orchestrator_agent(code_interpreter)
                        add_span_attributes(orch_span, {
                            "orchestrator.id": orchestrator.id if hasattr(orchestrator, 'id') else "unknown"
                        })
                else:
                    orchestrator = await self._create_orchestrator_agent(code_interpreter)
                
                # Step 6: Run group chat - 75%
                await update_progress("Running AI agent group chat", 75)
                
                # Trace group chat execution
                group_chat_start = datetime.now()
                if TRACING_AVAILABLE and tracer:
                    with tracer.start_as_current_span("code_analyzer.group_chat") as chat_span:
                        messages = await self._run_group_chat(orchestrator, task)
                        chat_duration = (datetime.now() - group_chat_start).total_seconds() * 1000
                        add_span_attributes(chat_span, {
                            "group_chat.message_count": len(messages),
                            "group_chat.duration_ms": chat_duration,
                            "group_chat.max_iterations": self.config.max_iterations
                        })
                else:
                    messages = await self._run_group_chat(orchestrator, task)
                    
                result["messages"] = messages
                result["status"] = "completed"
                
                # Track created files for multi-tenant isolation - 80%
                await update_progress("Processing analysis results", 80)
                created_files = self._plugin.get_created_files()
                report_file = self._plugin.get_created_report_file()
                result["created_files"] = created_files
                result["report_file"] = report_file
                
                # Add tool call log to result
                result["tool_calls"] = _tool_call_logger.get_all()
                result["tool_call_summary"] = _tool_call_logger.get_summary()
                
                logger.info(f"Analysis completed with {len(messages)} messages")
                logger.info(f"📁 Created files ({len(created_files)}): {created_files}")
                logger.info(f"📄 Report file: {report_file}")
                
                # Print tool call tail
                tool_summary = _tool_call_logger.get_summary()
                logger.info(f"🔧 Tool calls: {tool_summary['total']} total, {tool_summary['errors']} errors")
                logger.info(f"   By function: {tool_summary['by_function']}")
                
                if not report_file:
                    logger.warning("⚠️ No report file was created during analysis - agent may have failed to generate output")
                    # Print last few tool calls to help debug
                    _tool_call_logger.print_tail(5)
                
                # Record successful completion in span
                if TRACING_AVAILABLE and analysis_span:
                    analysis_duration = (datetime.now() - analysis_start_time).total_seconds() * 1000
                    add_span_attributes(analysis_span, {
                        "code_analyzer.status": "completed",
                        "code_analyzer.duration_ms": analysis_duration,
                        "code_analyzer.messages_count": len(messages),
                        "code_analyzer.files_created": len(created_files),
                        "code_analyzer.report_generated": report_file is not None,
                        "code_analyzer.tool_calls_total": tool_summary['total'],
                        "code_analyzer.tool_calls_errors": tool_summary['errors']
                    })
                    analysis_span.set_status(Status(StatusCode.OK))
                
            except Exception as e:
                logger.error(f"Analysis failed: {str(e)}")
                result["status"] = "failed"
                result["error"] = str(e)
                
                # Record error in span
                if TRACING_AVAILABLE and analysis_span:
                    analysis_span.record_exception(e)
                    analysis_span.set_status(Status(StatusCode.ERROR, str(e)))
                    add_span_attributes(analysis_span, {
                        "code_analyzer.status": "failed",
                        "code_analyzer.error_type": type(e).__name__,
                        "code_analyzer.error_message": str(e)[:500]
                    })
                
                raise
            
            finally:
                # Cleanup agents INSIDE the async with block while client is still open
                await self._cleanup()
                
                # End the analysis span
                if TRACING_AVAILABLE and analysis_span:
                    analysis_span.end()
                
                # NOTE: Do NOT cleanup plugin temp directory here!
                # The orchestrator needs to read the report file after this function returns.
                # The orchestrator is responsible for calling cleanup_temp_files() after upload.
        
        return result
    
    async def _create_agents(self, code_interpreter: CodeInterpreterTool = None) -> None:
        """Create all configured agents."""
        tracer = get_tracer()
        
        for agent_config in self.config.agents:
            try:
                # Build tools list
                tools = code_interpreter.definitions if code_interpreter else []
                tool_resources = code_interpreter.resources if code_interpreter else None
                
                # Trace individual agent creation
                agent_start = datetime.now()
                if TRACING_AVAILABLE and tracer:
                    with tracer.start_as_current_span(f"code_analyzer.create_agent.{agent_config.name}") as agent_span:
                        # Append app_id to agent name for multi-tenant isolation
                        agent_name = f"{agent_config.name}-{self.app_id}" if self.app_id else agent_config.name
                        add_span_attributes(agent_span, {
                            "agent.name": agent_name,
                            "agent.model": agent_config.model,
                            "agent.use_plugins": agent_config.use_plugins,
                            "agent.use_scf_search": agent_config.use_scf_search
                        })
                        
                        # Create agent definition
                        agent_def = await self._client.agents.create_agent(
                            name=agent_name,
                            model=agent_config.model,
                            instructions=agent_config.instructions,
                            description=agent_config.description,
                            tools=tools,
                            tool_resources=tool_resources
                        )
                        
                        agent_duration = (datetime.now() - agent_start).total_seconds() * 1000
                        add_span_attributes(agent_span, {
                            "agent.id": agent_def.id if hasattr(agent_def, 'id') else "unknown",
                            "agent.creation_duration_ms": agent_duration
                        })
                else:
                    # Create agent definition without tracing
                    # Append app_id to agent name for multi-tenant isolation
                    agent_name = f"{agent_config.name}-{self.app_id}" if self.app_id else agent_config.name
                    agent_def = await self._client.agents.create_agent(
                        name=agent_name,
                        model=agent_config.model,
                        instructions=agent_config.instructions,
                        description=agent_config.description,
                        tools=tools,
                        tool_resources=tool_resources
                    )
                
                # Create kernel with plugins if needed
                kernel = None
                logger.info(f"🔧 Agent {agent_config.name}: use_plugins={agent_config.use_plugins}, use_scf_search={agent_config.use_scf_search}, scf_plugin_available={self._scf_plugin is not None}")
                if agent_config.use_plugins or agent_config.use_scf_search:
                    kernel = Kernel()
                    
                    # Add code tools plugin if enabled
                    if agent_config.use_plugins:
                        kernel.add_plugin(self._plugin, plugin_name="code_tools")
                        logger.info(f"✅ Added code_tools plugin to agent: {agent_config.name}")
                    
                    # Add SCF Search plugin if enabled and available
                    if agent_config.use_scf_search and self._scf_plugin:
                        kernel.add_plugin(self._scf_plugin, plugin_name="scf_search")
                        logger.info(f"✅ Added SCF Search Plugin to agent: {agent_config.name}")
                    elif agent_config.use_scf_search and not self._scf_plugin:
                        logger.warning(f"⚠️ Agent {agent_config.name} wants SCF search but plugin not initialized")
                    
                    # Add function invocation filter for tool call logging with tracing
                    @kernel.filter(FilterTypes.FUNCTION_INVOCATION)
                    async def log_function_calls(context: FunctionInvocationContext, next):
                        """Log all function/tool calls with OpenTelemetry tracing."""
                        import time
                        start_time = time.time()
                        error_msg = None
                        tool_tracer = get_tracer()
                        
                        # Start tool call span if tracing available
                        tool_span = None
                        if TRACING_AVAILABLE and tool_tracer:
                            tool_span = tool_tracer.start_span(f"tool_call.{context.function.plugin_name}.{context.function.name}")
                            add_span_attributes(tool_span, {
                                "tool.name": context.function.name,
                                "tool.plugin": context.function.plugin_name,
                                "tool.agent": agent_config.name
                            })
                        
                        try:
                            await next(context)
                        except Exception as e:
                            error_msg = str(e)
                            if TRACING_AVAILABLE and tool_span:
                                tool_span.record_exception(e)
                                tool_span.set_status(Status(StatusCode.ERROR, str(e)))
                            raise
                        finally:
                            duration_ms = (time.time() - start_time) * 1000
                            
                            # Log to tool call logger
                            _tool_call_logger.log_call(
                                function_name=context.function.name,
                                plugin_name=context.function.plugin_name,
                                arguments=dict(context.arguments) if context.arguments else None,
                                result=context.result.value if context.result else None,
                                duration_ms=duration_ms,
                                agent_name=agent_config.name,
                                error=error_msg
                            )
                            
                            # Record in OpenTelemetry span
                            if TRACING_AVAILABLE and tool_span:
                                add_span_attributes(tool_span, {
                                    "tool.duration_ms": duration_ms,
                                    "tool.success": error_msg is None
                                })
                                if error_msg is None:
                                    tool_span.set_status(Status(StatusCode.OK))
                                tool_span.end()
                
                # Create Semantic Kernel agent
                agent = AzureAIAgent(
                    client=self._client,
                    definition=agent_def,
                    kernel=kernel
                )
                agent.polling_options.run_polling_timeout = timedelta(
                    minutes=self.config.polling_timeout_minutes
                )
                
                # Store agent with the actual name (including app_id suffix if present)
                actual_agent_name = agent_def.name
                self._agents[actual_agent_name] = agent
                logger.info(f"Created agent: {actual_agent_name}")
                
            except Exception as e:
                logger.error(f"Failed to create agent {agent_config.name}: {e}")
    
    async def _create_orchestrator_agent(self, code_interpreter: CodeInterpreterTool = None) -> AzureAIAgent:
        """Create the orchestrator agent that coordinates the group chat."""
        
        # Build orchestrator instructions dynamically
        agent_names = list(self._agents.keys())
        agent_descriptions = "\n".join([
            f"- {name}: {agent.definition.description or 'No description'}"
            for name, agent in self._agents.items()
        ])
        
        orchestrator_instructions = f"""You are a routing agent. Your ONLY job is to output the name of the next agent.

CRITICAL: You must respond with EXACTLY ONE WORD - an agent name or PERFECTUS.

VALID RESPONSES (choose ONE):
{chr(10).join([f'- {name}' for name in agent_names])}
- PERFECTUS (when all work is complete)

INVALID RESPONSES (NEVER do these):
- Sentences or explanations
- Apologies or commentary  
- Tool names like "create_file" or "code_interpreter"
- Multiple words

WORKFLOW:
1. If no analysis done yet → {agent_names[0] if agent_names else 'Analyst'}
2. If analysis complete and report written → PERFECTUS
3. If stuck or error → {agent_names[0] if agent_names else 'Analyst'}

Available agents:
{agent_descriptions}

YOUR RESPONSE MUST BE A SINGLE WORD FROM THE VALID RESPONSES LIST."""

        # Create orchestrator
        tools = code_interpreter.definitions if code_interpreter else []
        tool_resources = code_interpreter.resources if code_interpreter else None
        
        # Append app_id to orchestrator name for multi-tenant isolation
        orchestrator_name = f"Orchestrator-{self.app_id}" if self.app_id else "Orchestrator"
        orchestrator_def = await self._client.agents.create_agent(
            name=orchestrator_name,
            model=self.config.base_model,
            instructions=orchestrator_instructions,
            description="Orchestrates the multi-agent workflow",
            tools=tools,
            tool_resources=tool_resources
        )
        
        orchestrator = AzureAIAgent(
            client=self._client,
            definition=orchestrator_def
        )
        orchestrator.polling_options.run_polling_timeout = timedelta(
            minutes=self.config.polling_timeout_minutes
        )
        
        # Store orchestrator with the actual name (including app_id suffix if present)
        self._agents[orchestrator_name] = orchestrator
        logger.info(f"Created orchestrator agent: {orchestrator_name}")
        
        return orchestrator
    
    async def _run_group_chat(self, orchestrator: AzureAIAgent, task: str) -> List[Dict[str, str]]:
        """Run the multi-agent group chat using GroupChatOrchestration."""
        tracer = get_tracer()
        
        all_agents = list(self._agents.values())
        agent_names = [a.name for a in all_agents]
        
        # Create custom GroupChatManager with orchestrator logic
        manager = CodeAnalyzerGroupChatManager(
            orchestrator_name=orchestrator.name,
            agent_names=agent_names,
            max_rounds=self.config.max_iterations
        )
        
        # Create GroupChatOrchestration (replaces deprecated AgentGroupChat)
        orchestration = GroupChatOrchestration(
            members=all_agents,
            manager=manager
        )
        
        messages = []
        iteration_count = 0
        
        # Create and start the runtime
        runtime = InProcessRuntime()
        runtime.start()
        
        try:
            # Invoke the orchestration with the task
            logger.info(f"🚀 Starting GroupChatOrchestration with task: {task[:100]}...")
            
            orchestration_result = await orchestration.invoke(
                task=task,
                runtime=runtime
            )
            
            # Get the result from the orchestration
            result_value = await orchestration_result.get()
            
            # Process the result - StreamingChatMessageContent is a SINGLE message, not a list
            # When iterated, it yields attribute tuples, not messages
            if result_value:
                result_type = type(result_value).__name__
                logger.info(f"🔍 Result type: {result_type}")
                
                # Check if it's a ChatMessageContent-like object (single message)
                if 'ChatMessageContent' in result_type or 'MessageContent' in result_type:
                    # It's a single message - extract content directly
                    content = None
                    agent_name = getattr(result_value, 'name', 'final_result') or 'final_result'
                    
                    # Try items first (StreamingChatMessageContent)
                    if hasattr(result_value, 'items') and result_value.items:
                        texts = [item.text for item in result_value.items if hasattr(item, 'text') and item.text]
                        if texts:
                            content = ''.join(texts)
                    
                    # Fallback to content attribute
                    if not content and hasattr(result_value, 'content'):
                        raw_content = result_value.content
                        if raw_content and isinstance(raw_content, str):
                            content = raw_content
                    
                    if content and content.strip():
                        messages.append({
                            "agent": agent_name,
                            "content": content
                        })
                        logger.info(f"💬 {agent_name}: {content[:200]}...")
                
                # Check if it's actually a list of messages
                elif isinstance(result_value, list):
                    for message in result_value:
                        iteration_count += 1
                        msg_type = type(message).__name__
                        
                        # Skip tuples (attribute iterations)
                        if isinstance(message, tuple):
                            continue
                        
                        content = None
                        agent_name = getattr(message, 'name', 'unknown') or 'unknown'
                        
                        if hasattr(message, 'items') and message.items:
                            texts = [item.text for item in message.items if hasattr(item, 'text') and item.text]
                            if texts:
                                content = ''.join(texts)
                        
                        if not content and hasattr(message, 'content'):
                            raw_content = message.content
                            if raw_content and isinstance(raw_content, str):
                                content = raw_content
                        
                        if content and content.strip():
                            messages.append({
                                "agent": agent_name,
                                "content": content
                            })
                            logger.info(f"💬 {agent_name}: {content[:200]}...")
                
                # String result
                elif isinstance(result_value, str) and result_value.strip():
                    messages.append({
                        "agent": "final_result",
                        "content": result_value
                    })
                    logger.info(f"💬 Final result: {result_value[:200]}...")
            
            logger.info(f"✅ GroupChatOrchestration completed with {len(messages)} messages")
                
        except Exception as e:
            logger.error(f"Error during group chat: {e}")
            # Record group chat error in trace
            if TRACING_AVAILABLE:
                current_span = trace.get_current_span()
                if current_span and current_span.is_recording():
                    current_span.record_exception(e)
                    current_span.add_event("group_chat_error", {
                        "error_type": type(e).__name__,
                        "error_message": str(e),
                        "iteration_count": iteration_count
                    })
            raise
        finally:
            # Stop the runtime when done
            await runtime.stop_when_idle()
        
        return messages
    
    async def _cleanup(self) -> None:
        """Clean up all created agents, uploaded files, and resources."""
        logger.info("Cleaning up resources...")
        tracer = get_tracer()
        
        cleanup_start = datetime.now()
        agents_deleted = 0
        agents_failed = 0
        files_deleted = 0
        files_failed = 0
        
        # Step 1: Delete all uploaded files from Azure AI Agent service
        # This prevents file accumulation in the AI agent service
        for uploaded_file in self._uploaded_files:
            try:
                file_id = uploaded_file.id if hasattr(uploaded_file, 'id') else str(uploaded_file)
                if TRACING_AVAILABLE and tracer:
                    with tracer.start_as_current_span(f"code_analyzer.cleanup_file.{file_id}") as file_span:
                        await self._client.agents.files.delete(file_id)
                        add_span_attributes(file_span, {
                            "cleanup.file_id": file_id,
                            "cleanup.success": True
                        })
                else:
                    await self._client.agents.files.delete(file_id)
                logger.debug(f"Deleted uploaded file: {file_id}")
                files_deleted += 1
            except Exception as e:
                logger.warning(f"Failed to delete uploaded file {file_id}: {e}")
                files_failed += 1
        
        # Step 2: Delete all agents
        for agent_name, agent in self._agents.items():
            try:
                if TRACING_AVAILABLE and tracer:
                    with tracer.start_as_current_span(f"code_analyzer.cleanup_agent.{agent_name}") as cleanup_span:
                        await self._client.agents.delete_agent(agent.id)
                        add_span_attributes(cleanup_span, {
                            "cleanup.agent_name": agent_name,
                            "cleanup.agent_id": agent.id,
                            "cleanup.success": True
                        })
                else:
                    await self._client.agents.delete_agent(agent.id)
                logger.debug(f"Deleted agent: {agent_name}")
                agents_deleted += 1
            except Exception as e:
                logger.warning(f"Failed to delete agent {agent_name}: {e}")
                agents_failed += 1
        
        # Step 3: Clear internal state
        self._agents.clear()
        self._uploaded_files.clear()
        
        # Log cleanup summary with tracing
        cleanup_duration = (datetime.now() - cleanup_start).total_seconds() * 1000
        total_deleted = agents_deleted + files_deleted
        total_failed = agents_failed + files_failed
        
        if TRACING_AVAILABLE and tracer:
            # Record batch cleanup operation
            current_span = trace.get_current_span()
            if current_span and current_span.is_recording():
                record_batch_operation(
                    current_span,
                    operation_name="agent_cleanup",
                    batch_size=total_deleted + total_failed,
                    processed_count=total_deleted,
                    failed_count=total_failed,
                    success_rate=(total_deleted / (total_deleted + total_failed) * 100) if (total_deleted + total_failed) > 0 else 100,
                    total_duration_ms=cleanup_duration
                )
                # Add more detailed attributes
                add_span_attributes(current_span, {
                    "cleanup.agents_deleted": agents_deleted,
                    "cleanup.agents_failed": agents_failed,
                    "cleanup.files_deleted": files_deleted,
                    "cleanup.files_failed": files_failed
                })
        
        logger.info(f"Cleanup completed: {agents_deleted} agents deleted ({agents_failed} failed), "
                   f"{files_deleted} files deleted ({files_failed} failed) ({cleanup_duration:.1f}ms)")


# =============================================================================
# STANDALONE CLEANUP FUNCTION
# =============================================================================

@trace_async_function("cleanup_code_analyzer_agents")
async def cleanup_code_analyzer_agents(
    agent_name_prefix: str = "CodeAnalyzer",
    client=None
) -> dict:
    """
    Clean up orphaned code analyzer agents by name prefix.
    
    This function can be used to clean up agents that were not properly deleted
    due to errors or crashes. It searches for agents matching the given prefix
    and deletes them.
    
    Args:
        agent_name_prefix: Prefix to match agent names (default: "CodeAnalyzer")
        client: Optional AIProjectClient. If None, a new client will be created.
    
    Returns:
        dict: Result containing status and cleanup details
    """
    tracer = get_tracer()
    
    with tracer.start_as_current_span("code_analyzer_cleanup") as cleanup_span:
        add_span_attributes(cleanup_span, {
            "cleanup.agent_name_prefix": agent_name_prefix,
            "cleanup.client_provided": client is not None
        })
        
        # Create client if not provided
        client_created = False
        endpoint = os.getenv("AZURE_EXISTING_AIPROJECT_ENDPOINT") or os.getenv("FOUNDRY_PROJECT_ENDPOINT")
        
        if not endpoint:
            return {"status": "error", "message": "AZURE_EXISTING_AIPROJECT_ENDPOINT not configured"}
        
        try:
            if client is None:
                creds = DefaultAzureCredential(exclude_shared_token_cache_credential=True)
                client = await AzureAIAgent.create_client(credential=creds, endpoint=endpoint).__aenter__()
                client_created = True
            
            agents_deleted = 0
            agents_failed = 0
            deleted_agents = []
            
            # List all agents and find ones matching the prefix
            try:
                agents = client.agents.list_agents()
                async for agent in agents:
                    agent_name = getattr(agent, 'name', '') or ''
                    if agent_name.startswith(agent_name_prefix) or agent_name in ["Orchestrator", "Analyst", "Reviewer"]:
                        try:
                            await client.agents.delete_agent(agent.id)
                            agents_deleted += 1
                            deleted_agents.append({"id": agent.id, "name": agent_name})
                            logger.info(f"Deleted orphaned agent: {agent_name} ({agent.id})")
                        except Exception as e:
                            agents_failed += 1
                            logger.warning(f"Failed to delete agent {agent_name}: {e}")
            except Exception as list_ex:
                cleanup_span.record_exception(list_ex)
                logger.error(f"Error listing agents: {list_ex}")
                return {"status": "error", "message": f"Failed to list agents: {str(list_ex)}"}
            
            add_span_attributes(cleanup_span, {
                "cleanup.agents_deleted": agents_deleted,
                "cleanup.agents_failed": agents_failed
            })
            
            cleanup_span.set_status(Status(StatusCode.OK))
            return {
                "status": "success",
                "message": f"Cleanup completed: {agents_deleted} agents deleted, {agents_failed} failed",
                "agents_deleted": agents_deleted,
                "agents_failed": agents_failed,
                "deleted_agents": deleted_agents
            }
            
        except Exception as ex:
            cleanup_span.record_exception(ex)
            cleanup_span.set_status(Status(StatusCode.ERROR, str(ex)))
            logger.error(f"Error during cleanup: {str(ex)}")
            return {"status": "error", "message": str(ex)}
        finally:
            # Close client if we created it
            if client_created and client:
                try:
                    await client.__aexit__(None, None, None)
                except Exception as close_ex:
                    logger.debug(f"Error closing client: {close_ex}")


# =============================================================================
# CONVENIENCE FUNCTION
# =============================================================================

@trace_async_function("run_code_analysis")
async def run_code_analysis(
    files_path: str,
    config_path: str = None,
    config: CodeAnalyzerConfig = None,
    task_message: str = None,
    output_directory: str = None,
    perform_security_scan: bool = True,
    progress_callback: callable = None,
    app_id: str = None
) -> tuple[Dict[str, Any], 'SemanticKernelCodeAnalyzer']:
    """
    Convenience function to run code analysis.
    
    Args:
        files_path: Path to the directory containing files to analyze
        config_path: Path to JSON config file (mutually exclusive with config)
        config: CodeAnalyzerConfig object (mutually exclusive with config_path)
        task_message: Optional override for the analysis task
        output_directory: Directory for output files
        perform_security_scan: Whether to scan for secrets
        progress_callback: Optional async callback function(step_name: str, progress: int) for progress updates
        app_id: Application ID to append to agent names for multi-tenant isolation
        
    Returns:
        Tuple of (analysis results dictionary, analyzer instance)
        IMPORTANT: Caller must call analyzer.cleanup_temp_files() after reading report files
    """
    if config_path:
        analyzer = SemanticKernelCodeAnalyzer.from_config_file(config_path, output_directory, app_id)
    elif config:
        analyzer = SemanticKernelCodeAnalyzer(config, output_directory, app_id)
    else:
        raise ValueError("Either config_path or config must be provided")
    
    result = await analyzer.analyze(
        files_path=files_path,
        task_message=task_message,
        perform_security_scan=perform_security_scan,
        progress_callback=progress_callback
    )
    
    return result, analyzer


# =============================================================================
# CLI ENTRY POINT
# =============================================================================

async def main():
    """CLI entry point for running code analysis."""
    print("=== Semantic Kernel Code Analyzer ===\n")
    
    # Get configuration
    cwd = os.path.dirname(__file__)
    folder_name = input("Enter the config folder name (default: 'kinfosec'): ").strip() or 'kinfosec'
    config_path = os.path.join(cwd, folder_name, 'config.json')
    
    if not os.path.exists(config_path):
        print(f"Error: Config file not found: {config_path}")
        return
    
    files_path = os.path.join(cwd, folder_name, 'files')
    if not os.path.exists(files_path):
        print(f"Error: Files directory not found: {files_path}")
        return
    
    # Security scan option
    do_security_scan = input("Perform security scan for secrets? (y/n, default: y): ").strip().lower() != 'n'
    
    print(f"\nStarting analysis...")
    print(f"  Config: {config_path}")
    print(f"  Files: {files_path}")
    print(f"  Security Scan: {do_security_scan}\n")
    
    try:
        result, analyzer = await run_code_analysis(
            files_path=files_path,
            config_path=config_path,
            perform_security_scan=do_security_scan
        )
        
        print(f"\n=== Analysis Complete ===")
        print(f"Status: {result['status']}")
        print(f"Agents Used: {', '.join(result['agents_used'])}")
        print(f"Files Processed: {result['files_processed']}")
        print(f"Messages Exchanged: {len(result['messages'])}")
        
        if result.get('security_scan', {}).get('total_secrets_found', 0) > 0:
            print(f"⚠️  Secrets Found: {result['security_scan']['total_secrets_found']}")
        
        # Cleanup temp files after displaying results
        if analyzer:
            analyzer.cleanup_temp_files()
        
    except Exception as e:
        print(f"\n❌ Analysis failed: {str(e)}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
