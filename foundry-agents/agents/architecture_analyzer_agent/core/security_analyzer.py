"""
Security Analysis Module

Handles security analysis for architecture components using AI agents.
Provides SCF control mapping and security findings generation.
"""

import re
import json
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional
# Add parent directory to path for logging_config and tracing_config
arch_agent_root = Path(__file__).parent.parent
if str(arch_agent_root) not in sys.path:
    sys.path.insert(0, str(arch_agent_root))
from logging_config import get_logger
from tracing_config import get_tracer, add_span_attributes
from opentelemetry.trace import Status, StatusCode
from semantic_kernel.agents import AzureAIAgent

logger = get_logger(__name__)


class SecurityAnalyzer:
    """Analyzes architecture components for security compliance."""
    
    def __init__(self, agent: Optional[AzureAIAgent] = None):
        """
        Initialize the security analyzer.
        
        Args:
            agent: Optional pre-created security agent to reuse across analyses.
                   If not provided, a new agent will be created per analysis.
        """
        self._agent = agent
    
    async def analyze_components(
        self,
        components: List[str],
        architecture_name: str,
        analysis_instructions: str = "",
        app_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Run AI agent to perform security analysis for architecture components.
        
        Args:
            components: List of component names from the architecture
            architecture_name: Name of the architecture being analyzed
            analysis_instructions: Custom analysis instructions
        
        Returns:
            Dict: Security findings structure populated by AI agent
        """
        logger.info(f"Running AI security analysis for {len(components)} components in {architecture_name}")
        
        tracer = get_tracer()
        with tracer.start_as_current_span("security_analysis") as span:
            try:
                add_span_attributes(span, {
                    "architecture.name": architecture_name,
                    "components.count": len(components),
                    "components.list": ", ".join(components[:10]),  # First 10 components
                    "agent.reused": self._agent is not None
                })
                
                span.add_event("security_analysis_started", {
                    "architecture": architecture_name,
                    "component_count": len(components),
                    "agent_reused": self._agent is not None
                })
                
                # Use provided agent or create new one (fallback for backward compatibility)
                if self._agent:
                    agent = self._agent
                    span.add_event("using_existing_agent")
                else:
                    # Legacy path - create agent per analysis (not recommended)
                    logger.warning("No pre-created agent provided, creating new agent per analysis (not recommended)")
                    from .agent_factory import AgentFactory
                    agent_factory = AgentFactory()
                    agent = await agent_factory.create_security_analysis_agent(
                        app_id=f"temp_{architecture_name}",
                    )
                    span.add_event("security_agent_created_inline")
                
                # Execute the agent with component-specific message
                user_message = self._build_analysis_message(components, architecture_name)
                
                span.add_event("agent_invocation_started", {"component_count": len(components)})
                
                agent_responses = []
                async for response_item in agent.invoke(
                    messages=user_message,
                    thread=None,
                    temperature=0.1,
                    max_completion_tokens=16384,
                    max_prompt_tokens=50000
                ):
                    response = response_item.message if hasattr(response_item, 'message') else response_item
                    agent_responses.append(response)
                
                span.add_event("agent_invocation_completed", {"response_count": len(agent_responses)})
                
                # Extract security findings from response
                security_findings = self._extract_findings_from_response(agent_responses)
                
                if security_findings:
                    risks_count = len(security_findings.get('identified_risks', []))
                    controls_count = len(security_findings.get('missing_controls', []))
                    
                    span.add_event("findings_extracted", {
                        "risks_count": risks_count,
                        "missing_controls_count": controls_count
                    })
                    add_span_attributes(span, {
                        "findings.risks_count": risks_count,
                        "findings.controls_count": controls_count
                    })
                    span.set_status(Status(StatusCode.OK))
                    
                    logger.info(f"Successfully extracted security findings with {risks_count} risks")
                    return security_findings
                else:
                    logger.warning("Could not extract JSON from agent response, using fallback")
                    span.add_event("findings_extraction_failed", {"using_fallback": True})
                    return self.create_placeholder_findings(components)
                    
            except Exception as ex:
                logger.error(f"AI security analysis failed: {str(ex)}", exc_info=True)
                span.record_exception(ex)
                span.add_event("security_analysis_failed", {
                    "error": str(ex)[:500],
                    "using_fallback": True
                })
                span.set_status(Status(StatusCode.ERROR, str(ex)[:256]))
                return self.create_placeholder_findings(components)
    
    def _build_analysis_message(self, components: List[str], architecture_name: str) -> str:
        """Build the analysis message for the agent."""
        components_list = "\n".join([f"- {comp}" for comp in components])
        return f"""Analyze the following {len(components)} components from architecture "{architecture_name}":

COMPONENTS TO ANALYZE:
{components_list}

MANDATORY REQUIREMENTS:
1. Use AzureAISearch to research EACH component before creating findings
2. Read the search results carefully and extract SCF control IDs from the "title" field
3. DO NOT invent or hallucinate control IDs - only use IDs from actual search results
4. Format extracted IDs as SCF-[CATEGORY]-[NUMBER] (e.g., "Scf Data 07..." becomes "SCF-DATA-07")
5. Only include findings with high semantic relevance (search score > 0.8)

Return the complete security findings JSON with only real SCF control IDs from the search index."""
    
    def _extract_findings_from_response(self, agent_responses: List) -> Dict[str, Any]:
        """Extract JSON security findings from agent responses."""
        final_response = ""
        for resp in agent_responses:
            if hasattr(resp, 'content'):
                if isinstance(resp.content, str):
                    final_response += resp.content
                elif isinstance(resp.content, list):
                    for item in resp.content:
                        if hasattr(item, 'text'):
                            final_response += item.text
        
        # Parse JSON from response
        json_match = re.search(r'\{[\s\S]*"identified_risks"[\s\S]*\}', final_response)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse security findings JSON: {str(e)}")
        
        return None
    
    def create_placeholder_findings(self, components: List[str]) -> Dict[str, Any]:
        """
        Create placeholder security findings structure.
        
        Args:
            components: List of component names
        
        Returns:
            Dict: Placeholder security findings structure
        """
        logger.warning("Using placeholder security findings (AI analysis not available)")
        
        security_findings = {
            "identified_risks": [],
            "missing_controls": [],
            "compliance_gaps": [],
            "recommendations": [],
            "scf_control_mapping": []
        }
        
        # For each component, add placeholder SCF control mapping
        for component in components:
            security_findings["scf_control_mapping"].append({
                "component": component,
                "scf_controls": [],  # To be populated by AI agent
                "coverage": "partial"
            })
        
        return security_findings
