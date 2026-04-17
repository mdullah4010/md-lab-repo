# Copyright (c) Microsoft. All rights reserved.

import os
from typing import List, Tuple, Dict, Any, Optional
from azure.ai.agents.models import (
    McpTool,
    SubmitToolApprovalAction,
    RequiredMcpToolCall,
    ToolApproval,
)

# Import logging configuration
from agents.logging_config import get_logger

# Create logger for this module
logger = get_logger(__name__)

logger.info("MCP Tools module initialized")

def build_mcp_tool_definitions(allowed_labels: List[str]) -> Tuple[List[dict], List[str]]:
    """Return (tool_definitions, labels_added) based on allowed labels and env URLs.

    Each label is matched to an environment variable containing the MCP server URL.
    Mapping rules:
      - azure-pricing or azure-pricing-calculator -> AZURE_PRICING_MCP_URL
      - github -> GITHUB_MCP_URL
      - fallback -> <UPPER_LABEL>_MCP_URL (dashes -> underscores)
    """
    logger.info(f"Building MCP tool definitions for {len(allowed_labels)} labels: {allowed_labels}")
    tool_defs: List[dict] = []
    labels_added: List[str] = []

    def _maybe(label: str, env_var: str):
        url_val = os.getenv(env_var, "").strip()
        if not url_val:
            logger.debug(f"No URL configured for MCP tool '{label}' (env var: {env_var})")
            return
        try:
            logger.debug(f"Creating MCP tool '{label}' with URL: {url_val}")
            tool = McpTool(server_label=label, server_url=url_val, allowed_tools=[])
            tool.set_approval_mode("never")
            tool_defs.extend(tool.definitions)
            labels_added.append(label)
            logger.info(f"Successfully created MCP tool '{label}' with {len(tool.definitions)} tool definitions")
        except Exception as ex:
            logger.error(f"Failed to build MCP tool '{label}' ({env_var}): {ex}")
            logger.error(f"[MCP] Failed to build tool '{label}' ({env_var}): {ex}")

    for label in allowed_labels:
        norm = label.lower()
        if norm in ("azurepricing", "azure-pricing-calculator"):
            _maybe(label, "AZURE_PRICING_MCP_URL")
        elif norm == "azuredevops":
            _maybe(label, "AZURE_DEVOPS_MCP_URL")
        elif norm == "microsoft_learn":
            _maybe(label, "MICROSOFT_LEARN_MCP_URL")
        else:
            env_guess = f"{norm.upper().replace('-', '_')}_MCP_URL"
            _maybe(label, env_guess)

    logger.info(f"MCP tool definitions built: {len(labels_added)} tools added ({labels_added}), {len(tool_defs)} total definitions")
    logger.debug(f"Tool definitions created: {len(tool_defs)} total definitions")
    
    return tool_defs, labels_added

async def approve_mcp_required_actions(agents_client, thread_id: str, run, headers: Optional[Dict[str, str]] = None, auto_approve: bool = True) -> bool:
    """If run.status == 'requires_action', approve all RequiredMcpToolCall tool calls.

    Returns True if approvals were submitted, False otherwise.
    """
    if run.status != "requires_action" or not isinstance(run.required_action, SubmitToolApprovalAction):
        return False
    tool_calls = run.required_action.submit_tool_approval.tool_calls
    if not tool_calls:
        logger.warning("Run requires action but no tool calls present; cancelling run")
        await agents_client.runs.cancel(thread_id=thread_id, run_id=run.id)
        return False
    approvals = []
    for call in tool_calls:
        if isinstance(call, RequiredMcpToolCall):
            approvals.append(
                ToolApproval(
                    tool_call_id=call.id,
                    approve=auto_approve,
                    headers=headers or {},
                )
            )
            logger.info(f"Approving MCP tool call {call.id}")
    if approvals:
        await agents_client.runs.submit_tool_outputs(
            thread_id=thread_id,
            run_id=run.id,
            tool_approvals=approvals,
        )
        return True
    return False


def update_mcp_tool_headers(mcp_tool: McpTool, headers: Dict[str, str]):
    """Utility to bulk-update headers on an McpTool instance."""
    for k, v in headers.items():
        mcp_tool.update_headers(k, v)
    logger.debug(f"Updated MCP tool headers: {list(headers.keys())}")


__all__ = [
    "build_mcp_tool_definitions",
    "approve_mcp_required_actions",
    "update_mcp_tool_headers",
]