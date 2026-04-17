"""
Architecture Analyzer Module

This module provides tools and plugins for analyzing architecture from design documents
and other sources.

The module is now refactored into modular components:
- agent_factory: Creates and configures AI agents
- security_analyzer: Performs security analysis on components
- report_generator: Generates consolidated reports
- findings_extractor: Extracts and formats security findings
"""

# Lazy imports to avoid dependency issues during module import
# Import only when explicitly needed

__all__ = [
    'AgentFactory',
    'SecurityAnalyzer',
    'ReportGenerator',
    'FindingsExtractor'
]

def __getattr__(name):
    """Lazy load modules to avoid import errors."""
    if name == 'AgentFactory':
        from .agent_factory import AgentFactory
        return AgentFactory
    elif name == 'SecurityAnalyzer':
        from .security_analyzer import SecurityAnalyzer
        return SecurityAnalyzer
    elif name == 'ReportGenerator':
        from .report_generator import ReportGenerator
        return ReportGenerator
    elif name == 'FindingsExtractor':
        from .findings_extractor import FindingsExtractor
        return FindingsExtractor
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")