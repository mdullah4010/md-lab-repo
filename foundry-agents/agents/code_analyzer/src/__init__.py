"""
Code Analyzer Source Module

This module contains:
- codebase_analyzer: Deterministic codebase analysis (no LLM required)
- plugins: Semantic Kernel plugins for code analysis
"""

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
    "CodebaseAnalyzer",
    "CodebaseAnalysisResult", 
    "analyze_codebase",
    "get_codebase_markdown_section",
    "FileStats",
    "DependencyInfo",
    "FrameworkInfo",
    "ClassInfo"
]
