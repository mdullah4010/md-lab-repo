"""
Report Generation Module

Generates consolidated security findings reports in markdown format.
Handles report formatting, findings aggregation, and component-wise summaries.
"""

import sys
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime
# Add parent directory to path for logging_config and tracing_config
arch_agent_root = Path(__file__).parent.parent
if str(arch_agent_root) not in sys.path:
    sys.path.insert(0, str(arch_agent_root))
from logging_config import get_logger
from tracing_config import get_tracer, add_span_attributes
from opentelemetry.trace import Status, StatusCode

logger = get_logger(__name__)


class ReportGenerator:
    """Generates security findings reports."""
    
    def generate_consolidated_report(
        self,
        findings: List[Dict[str, Any]],
        architecture_results: Dict[str, Any]
    ) -> str:
        """
        Generate consolidated security findings report in markdown format.
        
        Args:
            findings: List of all findings from all architectures
            architecture_results: Dictionary of individual architecture analysis results
        
        Returns:
            Markdown formatted consolidated report
        """
        tracer = get_tracer()
        with tracer.start_as_current_span("generate_consolidated_report") as span:
            try:
                add_span_attributes(span, {
                    "report.findings_count": len(findings),
                    "report.architectures_count": len(architecture_results)
                })
                
                span.add_event("report_generation_started", {
                    "findings_count": len(findings),
                    "architectures_count": len(architecture_results)
                })
                
                current_date = datetime.utcnow().strftime("%Y-%m-%d")
                
                report_parts = [
                    self._generate_header(findings, architecture_results),
                    self._generate_executive_summary(architecture_results),
                    self._generate_findings_table(findings, current_date),
                    self._generate_component_summary(findings),
                    self._generate_recommendations()
                ]
                
                report = "\n".join(report_parts)
                
                add_span_attributes(span, {
                    "report.size_bytes": len(report),
                    "report.sections_count": len(report_parts)
                })
                
                span.add_event("report_generation_completed", {
                    "report_size": len(report),
                    "sections": len(report_parts)
                })
                span.set_status(Status(StatusCode.OK))
                
                return report
                
            except Exception as ex:
                span.record_exception(ex)
                span.add_event("report_generation_failed", {"error": str(ex)[:500]})
                span.set_status(Status(StatusCode.ERROR, str(ex)[:256]))
                raise
    
    def _generate_header(self, findings: List[Dict[str, Any]], architecture_results: Dict[str, Any]) -> str:
        """Generate report header."""
        return f"""# Consolidated Security Findings Report

**Generated:** {datetime.utcnow().isoformat()}
**Total Architectures Analyzed:** {len(architecture_results)}
**Total Findings:** {len(findings)}

---
"""
    
    def _generate_executive_summary(self, architecture_results: Dict[str, Any]) -> str:
        """Generate executive summary section."""
        summary = f"""## Executive Summary

This consolidated report aggregates security findings from {len(architecture_results)} architecture analyses. Each finding is mapped to specific components and SCF control objectives.

### Analyzed Architectures

"""
        for arch_name, result in architecture_results.items():
            status = result.get("status", "unknown")
            summary += f"- **{arch_name}**: {status.upper()}\n"
        
        summary += "\n---\n\n"
        return summary
    
    def _generate_findings_table(self, findings: List[Dict[str, Any]], current_date: str) -> str:
        """Generate findings table section."""
        table = """## Security Findings

| Deficiency ID | Severity | Status | Current Date | Deficiency Type | ControlObjective Identifier | Owner | Affected Assets | Deficiency Title | Threat Description | Proposed Mitigation |
|---------------|----------|--------|--------------|-----------------|-----------------------------|-------|-----------------|------------------|--------------------|--------------------|\n"""
        
        if not findings:
            table += "| N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | No deficiencies identified | N/A | N/A |\n"
        else:
            for finding in findings:
                deficiency_id = finding.get("deficiency_id", "UNK-000")
                severity = finding.get("severity", "Medium")
                status = finding.get("status", "Open")
                deficiency_type = finding.get("deficiency_type", "Security")
                control_id = finding.get("control_objective_id", "")
                owner = finding.get("owner", "Security Team")
                affected_assets = finding.get("affected_assets", "")
                title = finding.get("title", "")
                threat_desc = finding.get("threat_description", "")
                mitigation = finding.get("proposed_mitigation", "")
                
                table += f"| {deficiency_id} | {severity} | {status} | {current_date} | {deficiency_type} | {control_id} | {owner} | {affected_assets} | {title} | {threat_desc} | {mitigation} |\n"
        
        table += "\n---\n\n"
        return table
    
    def _generate_component_summary(self, findings: List[Dict[str, Any]]) -> str:
        """Generate component-wise findings summary."""
        summary = """## Component-Wise Findings Summary

"""
        
        # Group findings by component
        component_findings = {}
        for finding in findings:
            assets = finding.get("affected_assets", "Unknown")
            for asset in assets.split(", "):
                if asset not in component_findings:
                    component_findings[asset] = []
                component_findings[asset].append(finding)
        
        for component, comp_findings in component_findings.items():
            summary += f"""### {component}

**Total Findings:** {len(comp_findings)}

"""
            for finding in comp_findings:
                summary += f"- **{finding.get('deficiency_id')}** ({finding.get('severity')}): {finding.get('title')}\n"
            summary += "\n"
        
        summary += "---\n\n"
        return summary
    
    def _generate_recommendations(self) -> str:
        """Generate recommendations section."""
        return """## Recommendations

1. Prioritize remediation of HIGH severity findings
2. Implement comprehensive monitoring and SIEM integration
3. Enforce encryption at rest and in transit for all components
4. Deploy WAF and network security controls
5. Establish secure CI/CD pipelines with security scanning
6. Regular security audits and compliance validation

---

**Report End**
"""
