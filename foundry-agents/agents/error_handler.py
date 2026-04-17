"""
Shared error handling utilities for AI agents.
This module provides common error handling and retry mechanisms that can be used across different agents.
"""

import asyncio
import logging
from typing import Tuple, Dict, Any, Optional, List

# Import logging configuration to use consistent logging setup
from agents.logging_config import get_logger

logger = get_logger(__name__)


def get_detailed_run_error(run, context_description: str = "Agent run") -> Tuple[str, Dict[str, Any]]:
    """
    Extract detailed error information from a failed run object.
    
    Args:
        run: The agent run object that failed
        context_description: Description for logging context
    
    Returns:
        Tuple of (error_message, error_details_dict)
    """
    error_details = {
        "status": run.status,
        "run_id": getattr(run, 'id', 'unknown'),
        "last_error": getattr(run, 'last_error', None),
        "error": getattr(run, 'error', None),
        "incomplete_details": getattr(run, 'incomplete_details', None)
    }

    # Create comprehensive error message
    error_msg = f"{context_description} failed: {run.status}"
    if hasattr(run, 'last_error') and run.last_error:
        if hasattr(run.last_error, 'message'):
            error_msg += f" - {run.last_error.message}"
        elif hasattr(run.last_error, 'code'):
            error_msg += f" - Error code: {run.last_error.code}"
        else:
            error_msg += f" - {str(run.last_error)}"

    return error_msg, error_details


def should_retry_server_error(error_msg: str, error_details: Dict[str, Any]) -> bool:
    """
    Check if the error code indicates a retryable server error.
    
    Args:
        error_msg: The error message string
        error_details: Dictionary containing error details
    
    Returns:
        True if the error should be retried, False otherwise
    """
    last_error = error_details.get('last_error')
    if last_error:
        # Check if last_error is a dictionary with 'code' key
        if isinstance(last_error, dict) and 'code' in last_error:
            error_code = str(last_error['code']).lower()
            return error_code in ['server_error', 'rate_limit_exceeded']
        # Check if last_error is an object with 'code' attribute
        elif hasattr(last_error, 'code'):
            error_code = str(last_error.code).lower()
            return error_code in ['server_error', 'rate_limit_exceeded']
    
    return False


async def retry_agent_run_on_server_error(
    client, 
    agent_id: str, 
    thread_id: str, 
    context_description: str, 
    max_retries: int = 3,
    base_delay: float = 2.0,
    metadata: Optional[Dict[str, Any]] = None,
    is_async_client: bool = True,
    tools: Optional[List] = None
) -> Tuple[object, bool]:
    """
    Retry an agent run if it fails with server_error or rate_limit_exceeded.
    Uses exponential backoff: 2s, 4s, 8s for the 3 retry attempts.
    
    Args:
        client: AI client for making requests (sync or async)
        agent_id: Agent ID to retry
        thread_id: Thread ID for the run
        context_description: Description for logging
        max_retries: Maximum number of retry attempts (default 3)
        base_delay: Base delay in seconds for exponential backoff (default 2.0)
        metadata: Optional metadata for the run
        is_async_client: True if client uses async calls, False for sync
        tools: Optional list of tools to pass to the run
    
    Returns:
        Tuple: (run_object, retry_succeeded)
    """
    
    for retry_attempt in range(max_retries):
        try:
            # Create a new run
            logger.debug(f"Creating new agent run for attempt {retry_attempt + 1}/{max_retries}")
            
            # Prepare run creation arguments
            run_kwargs = {
                "thread_id": thread_id,
                "agent_id": agent_id
            }
            if metadata:
                run_kwargs["metadata"] = metadata
            if tools:
                run_kwargs["tools"] = tools
            
            if is_async_client:
                run = await client.agents.runs.create(**run_kwargs)
            else:
                run = client.agents.runs.create(**run_kwargs)
            
            # Wait for completion with timeout
            max_wait = 60
            wait_time = 0
            poll_interval = 1
            terminal_statuses = {"completed", "failed", "cancelled", "succeeded"}
            
            while run.status not in terminal_statuses and wait_time < max_wait:
                await asyncio.sleep(poll_interval)
                wait_time += poll_interval
                
                if is_async_client:
                    run = await client.agents.runs.get(
                        thread_id=thread_id,
                        run_id=run.id
                    )
                else:
                    run = client.agents.runs.get(
                        thread_id=thread_id,
                        run_id=run.id
                    )
            
            # If run completed successfully, return it
            if run.status in {"completed", "succeeded"}:
                if retry_attempt > 0:
                    logger.info(f"✅ Retry succeeded on attempt {retry_attempt + 1} for {context_description}")
                return run, retry_attempt > 0
            
            # If run failed, check if it's a retryable error
            if run.status == "failed":
                error_msg, error_details = get_detailed_run_error(run, context_description)
                
                # Only retry on retryable errors (server_error or rate_limit_exceeded)
                if should_retry_server_error(error_msg, error_details):
                    if retry_attempt < max_retries - 1:  # Don't retry on the last attempt
                        # Calculate exponential backoff delay: 2s, 4s, 8s
                        exponential_delay = base_delay * (2 ** retry_attempt)
                        
                        # Determine error type for better logging
                        error_type = "server_error"  # default
                        last_error = error_details.get('last_error')
                        if last_error:
                            if isinstance(last_error, dict) and 'code' in last_error:
                                error_type = str(last_error['code']).lower()
                            elif hasattr(last_error, 'code'):
                                error_type = str(last_error.code).lower()
                            elif "rate_limit_exceeded" in error_msg.lower():
                                error_type = "rate_limit_exceeded"
                        
                        logger.warning(f"⚠️ {error_type} detected in {context_description}, retrying in {exponential_delay}s (attempt {retry_attempt + 1}/{max_retries})")
                        logger.debug(f"Error details: {error_msg}")
                        
                        await asyncio.sleep(exponential_delay)
                        continue
                    else:
                        # Determine error type for better logging
                        error_type = "server_error"  # default
                        last_error = error_details.get('last_error')
                        if last_error:
                            if isinstance(last_error, dict) and 'code' in last_error:
                                error_type = str(last_error['code']).lower()
                            elif hasattr(last_error, 'code'):
                                error_type = str(last_error.code).lower()
                            elif "rate_limit_exceeded" in error_msg.lower():
                                error_type = "rate_limit_exceeded"
                        
                        logger.error(f"❌ Max retries ({max_retries}) reached for {context_description} with {error_type}")
                        return run, False
                else:
                    # Not a retryable error, don't retry
                    logger.info(f"Non-retryable error in {context_description}, not retrying: {error_msg}")
                    return run, False
            
            # For other non-completed statuses, don't retry
            logger.warning(f"Run ended with status '{run.status}' for {context_description}, not retrying")
            return run, False
            
        except Exception as ex:
            logger.error(f"Exception during retry attempt {retry_attempt + 1} for {context_description}: {ex}")
            if retry_attempt == max_retries - 1:
                logger.error(f"❌ All retry attempts failed for {context_description}")
                raise
            
            # Wait before next retry even on exceptions (exponential backoff)
            exponential_delay = base_delay * (2 ** retry_attempt)
            logger.warning(f"Exception occurred, waiting {exponential_delay}s before next retry")
            await asyncio.sleep(exponential_delay)
    
    # Should not reach here, but just in case
    return None, False