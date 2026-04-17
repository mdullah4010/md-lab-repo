"""
Code Analyzer Package - Pure Semantic Kernel Implementation

This package provides code analysis using Semantic Kernel agents.

Main components:
- SemanticKernelCodeAnalyzer: Main class for running code analysis
- CodeAnalyzerPlugin: Unified plugin with all kernel functions
- CodebaseAnalyzer: Deterministic codebase analysis (no LLM required)
- run_code_analysis: Convenience function for quick analysis
- cleanup_code_analyzer_agents: Utility function to clean up orphaned agents

Example usage:
    from code_analyzer import run_code_analysis, SemanticKernelCodeAnalyzer
    
    # Quick analysis
    result = await run_code_analysis(
        files_path="/path/to/code",
        config_path="/path/to/config.json"
    )
    
    # Or with more control
    analyzer = SemanticKernelCodeAnalyzer.from_config_file("config.json")
    result = await analyzer.analyze(files_path="/path/to/code")
    
    # Deterministic codebase analysis (no LLM)
    from code_analyzer import CodebaseAnalyzer, analyze_codebase
    result = analyze_codebase("/path/to/repo")
    markdown = result.to_markdown_section()
    
    # Clean up orphaned agents (if needed)
    cleanup_result = await cleanup_code_analyzer_agents()
"""

from agents.code_analyzer.semantic_kernel_analyzer import (
    SemanticKernelCodeAnalyzer,
    CodeAnalyzerConfig,
    AgentConfig,
    run_code_analysis,
    cleanup_code_analyzer_agents,
    ToolCallLogger,
    get_tool_call_logger,
)

from agents.code_analyzer.src.plugins.code_analyzer_plugin import CodeAnalyzerPlugin

# Import deterministic codebase analyzer (no LLM required)
from agents.code_analyzer.src.codebase_analyzer import (
    CodebaseAnalyzer,
    CodebaseAnalysisResult,
    analyze_codebase,
    get_codebase_markdown_section,
    FileStats,
    DependencyInfo,
    FrameworkInfo,
    ClassInfo
)

__all__ = [
    # Semantic Kernel based analyzer
    "SemanticKernelCodeAnalyzer",
    "CodeAnalyzerConfig", 
    "AgentConfig",
    "run_code_analysis",
    "cleanup_code_analyzer_agents",
    "CodeAnalyzerPlugin",
    "ToolCallLogger",
    "get_tool_call_logger",
    # Deterministic codebase analyzer
    "CodebaseAnalyzer",
    "CodebaseAnalysisResult",
    "analyze_codebase",
    "get_codebase_markdown_section",
    "FileStats",
    "DependencyInfo",
    "FrameworkInfo",
    "ClassInfo",
]
