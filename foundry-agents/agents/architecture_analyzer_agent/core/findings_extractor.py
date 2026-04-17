"""
Findings Extraction Module

Extracts and formats security findings from analysis results.
Handles JSON parsing and finding structure normalization.
"""

import re
import json
import sys
from pathlib import Path
from typing import List, Dict, Any
# Add parent directory to path for logging_config and tracing_config
arch_agent_root = Path(__file__).parent.parent
if str(arch_agent_root) not in sys.path:
    sys.path.insert(0, str(arch_agent_root))
from logging_config import get_logger
from tracing_config import get_tracer, add_span_attributes
from opentelemetry.trace import Status, StatusCode

logger = get_logger(__name__)


class FindingsExtractor:
    """Extracts security findings from analysis results."""
    
    def extract_findings(
        self,
        result: Dict[str, Any],
        architecture_name: str,
        start_id: int = 1
    ) -> List[Dict[str, Any]]:
        """
        Extract security findings from analysis result and format them with component-wise tracking.
        
        Args:
            result: The analysis result dictionary
            architecture_name: Name of the architecture being analyzed
            start_id: Starting deficiency ID number
        
        Returns:
            List of finding dictionaries formatted for deficiency table
        """
        tracer = get_tracer()
        with tracer.start_as_current_span("extract_findings") as span:
            findings = []
            
            try:
                add_span_attributes(span, {
                    "architecture.name": architecture_name,
                    "extraction.start_id": start_id
                })
                
                span.add_event("extraction_started", {"architecture": architecture_name})
                
                # Extract security findings from result
                security_findings = self._get_security_findings(result, architecture_name)
                
                if security_findings and isinstance(security_findings, dict):
                    logger.info(f"Processing {len(security_findings.get('identified_risks', []))} identified_risks for {architecture_name}")
                    
                    span.add_event("security_findings_parsed", {
                        "risks_count": len(security_findings.get('identified_risks', [])),
                        "controls_count": len(security_findings.get('missing_controls', [])),
                        "gaps_count": len(security_findings.get('compliance_gaps', []))
                    })
                    
                    # Process different types of findings
                    findings.extend(self._process_identified_risks(security_findings, architecture_name, start_id))
                    start_id += len(findings)
                    
                    findings.extend(self._process_missing_controls(security_findings, architecture_name, start_id))
                    start_id += len(findings)
                    
                    findings.extend(self._process_compliance_gaps(security_findings, architecture_name, start_id))
                else:
                    logger.warning(f"No valid security_findings dict found for {architecture_name}")
                    logger.warning(f"Result keys: {list(result.keys())}")
                    span.add_event("no_findings_found", {"result_keys": str(list(result.keys())[:10])})
            
            except Exception as ex:
                logger.error(f"Error extracting findings from result for {architecture_name}: {str(ex)}", exc_info=True)
                span.record_exception(ex)
                span.add_event("extraction_failed", {"error": str(ex)[:500]})
                span.set_status(Status(StatusCode.ERROR, str(ex)[:256]))
            
            add_span_attributes(span, {"findings.total_count": len(findings)})
            span.add_event("extraction_completed", {"findings_count": len(findings)})
            span.set_status(Status(StatusCode.OK))
            
            logger.info(f"Extracted {len(findings)} findings for {architecture_name}")
            return findings
    
    def _get_security_findings(self, result: Dict[str, Any], architecture_name: str) -> Dict[str, Any]:
        """Extract security_findings from the result."""
        # Check if result indicates an error
        if result.get("status") == "error":
            logger.warning(f"Result has error status for {architecture_name}: {result.get('error')}")
            return None
        
        agent_response = result.get("agent_response", {})
        
        logger.info(f"Processing {architecture_name}, agent_response type: {type(agent_response)}")
        
        # If agent_response is already a dict, use it directly
        if isinstance(agent_response, dict):
            logger.info(f"Using dict directly for {architecture_name}")
            return agent_response
        
        # If it's a string, try to parse it
        if isinstance(agent_response, str):
            logger.info(f"agent_response is string for {architecture_name}, attempting to parse")
            json_match = re.search(r'\{[\s\S]*"identified_risks"[\s\S]*\}', agent_response)
            
            if json_match:
                try:
                    return json.loads(json_match.group(0))
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse JSON: {str(e)}")
        
        # Try to get from result directly
        if "security_findings" in result:
            security_findings = result.get("security_findings")
            if isinstance(security_findings, dict):
                logger.info(f"Using security_findings from result for {architecture_name}")
                return security_findings
        
        return None
    
    def _process_identified_risks(
        self,
        security_findings: Dict[str, Any],
        architecture_name: str,
        start_id: int
    ) -> List[Dict[str, Any]]:
        """Process identified risks into findings."""
        findings = []
        
        for risk in security_findings.get("identified_risks", []):
            finding_id = f"{risk.get('deficiency_type', 'GEN')[:3].upper()}-{start_id:03d}"
            start_id += 1
            
            component = risk.get("component", "Unknown")
            affected_assets = f"{component} ({architecture_name})"
            
            finding = {
                "deficiency_id": finding_id,
                "severity": risk.get("severity", "Medium"),
                "status": "Open",
                "deficiency_type": risk.get("deficiency_type", "Security"),
                "control_objective_id": risk.get("scf_control_id", ""),
                "owner": risk.get("owner", "Security Team"),
                "affected_assets": affected_assets,
                "title": risk.get("title", risk.get("risk", "Security risk identified")),
                "threat_description": risk.get("threat_description", ""),
                "proposed_mitigation": risk.get("proposed_mitigation", ""),
                "architecture": architecture_name,
                "component": component
            }
            findings.append(finding)
        
        return findings
    
    def _process_missing_controls(
        self,
        security_findings: Dict[str, Any],
        architecture_name: str,
        start_id: int
    ) -> List[Dict[str, Any]]:
        """Process missing controls into findings."""
        findings = []
        
        for control in security_findings.get("missing_controls", []):
            finding_id = f"CTL-{start_id:03d}"
            start_id += 1
            
            component = control.get("component", "Unknown")
            affected_assets = f"{component} ({architecture_name})"
            
            finding = {
                "deficiency_id": finding_id,
                "severity": control.get("severity", "Medium"),
                "status": "Open",
                "deficiency_type": "Compliance",
                "control_objective_id": control.get("scf_control_id", ""),
                "owner": control.get("owner", "Security Team"),
                "affected_assets": affected_assets,
                "title": f"Missing control: {control.get('control', 'Unknown')}",
                "threat_description": "Missing security control increases risk exposure",
                "proposed_mitigation": f"Implement {control.get('control', 'required control')}",
                "architecture": architecture_name,
                "component": component
            }
            findings.append(finding)
        
        return findings
    
    def _process_compliance_gaps(
        self,
        security_findings: Dict[str, Any],
        architecture_name: str,
        start_id: int
    ) -> List[Dict[str, Any]]:
        """Process compliance gaps into findings."""
        findings = []
        
        for gap in security_findings.get("compliance_gaps", []):
            finding_id = f"CMP-{start_id:03d}"
            start_id += 1
            
            component = gap.get("component", "Unknown")
            affected_assets = f"{component} ({architecture_name})"
            
            finding = {
                "deficiency_id": finding_id,
                "severity": "Medium",
                "status": "Open",
                "deficiency_type": "Compliance",
                "control_objective_id": gap.get("scf_control_id", ""),
                "owner": "Compliance Team",
                "affected_assets": affected_assets,
                "title": f"Compliance gap: {gap.get('framework', 'Unknown')}",
                "threat_description": gap.get("gap", "Compliance gap identified"),
                "proposed_mitigation": f"Address {gap.get('framework', 'compliance')} requirements",
                "architecture": architecture_name,
                "component": component
            }
            findings.append(finding)
        
        return findings
