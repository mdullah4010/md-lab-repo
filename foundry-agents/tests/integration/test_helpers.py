# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

"""
Helper utilities for integration tests.

This module provides common utilities used across integration tests,
particularly for polling asynchronous operations until completion.
"""

import logging
import asyncio
import time
import pytest
from typing import Dict, Any

logger = logging.getLogger(__name__)


async def poll_operation_until_complete(
    http_client,
    integration_config: Dict[str, Any],
    operation_id: str,
    status_endpoint: str,
    result_endpoint: str,
    max_wait_time: int = 3600,
    poll_interval: int = 10,
    allow_failure: bool = False
) -> Dict[str, Any]:
    """
    Poll operation status until completion and retrieve results.
    
    Args:
        http_client: HTTP client for API requests
        integration_config: Test configuration with credentials
        operation_id: Operation ID to track
        status_endpoint: Endpoint to check operation status
        result_endpoint: Endpoint to retrieve operation results
        max_wait_time: Maximum time to wait in seconds (default: 3600 = 60 minutes)
        poll_interval: Time between status checks in seconds (default: 10)
        allow_failure: If True, return failed operations without raising exception (default: False)
        
    Returns:
        Dictionary containing operation result data
        
    Raises:
        AssertionError: If operation fails or times out (unless allow_failure=True)
    """
    # Prepare headers for operation access
    operation_headers = {
        "X-User-Object-Id": integration_config["user_object_id"],
        "X-Storage-Account": integration_config["storage_account_name"]
    }
    
    logger.info(f"Polling operation {operation_id} until completion...")
    logger.info(f"Status endpoint: {status_endpoint}")
    logger.info(f"Result endpoint: {result_endpoint}")
    
    # Build query parameters (app_id is required for status endpoint)
    query_params = {"app_id": integration_config["app_id"]}
    
    # Poll operation status until completion
    start_time = time.time()
    operation_status = "in_progress"
    last_progress = -1
    last_progress_change_time = start_time
    stuck_threshold = 1800  # 30 minutes without progress change (increased from 10 min for long-running operations)
    
    while operation_status == "in_progress":
        if time.time() - start_time > max_wait_time:
            logger.error(f"Operation {operation_id} timed out after {max_wait_time} seconds")
            raise TimeoutError(f"Operation timed out after {max_wait_time} seconds")
        
        await asyncio.sleep(poll_interval)
        
        # Retry logic for connection errors
        max_retries = 3
        retry_delay = 5
        
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"Checking status at endpoint: {status_endpoint} (attempt {attempt}/{max_retries})")
                status_response = await http_client.get(status_endpoint, headers=operation_headers, params=query_params, timeout=300.0)
                logger.info(f"Status response code: {status_response.status_code}")
                break  # Success - exit retry loop
                
            except Exception as e:
                if attempt < max_retries:
                    logger.warning(
                        f"Status check failed (attempt {attempt}/{max_retries}): {type(e).__name__}: {e}. "
                        f"Retrying in {retry_delay} seconds..."
                    )
                    await asyncio.sleep(retry_delay)
                else:
                    logger.error(f"Status check failed after {max_retries} attempts: {type(e).__name__}: {e}")
                    raise
        
        # Handle Azure throttling errors with retry logic
        if status_response.status_code in [429, 500, 503]:
            response_text = status_response.text
            # Check for throttling indicators
            if any(keyword in response_text for keyword in ["Throttled", "TooManyRequests", "too many requests", "ResourceCollectionRequestsThrottled"]):
                # Extract retry-after time if available (default to 90 seconds)
                import re
                retry_after_match = re.search(r"try after '(\d+)' seconds", response_text)
                retry_after = int(retry_after_match.group(1)) if retry_after_match else 90
                
                logger.warning(f"Azure throttling detected. Waiting {retry_after} seconds before retry...")
                await asyncio.sleep(retry_after)
                continue  # Retry the status check
        
        if status_response.status_code != 200:
            logger.error(f"Status check failed with code {status_response.status_code}")
            logger.error(f"Response text: {status_response.text}")
            raise RuntimeError(f"Status check failed: {status_response.status_code} - {status_response.text}")
        
        status_data = status_response.json()
        operation_status = status_data.get("status", "unknown")
        progress_percentage = status_data.get("progress_percentage", 0)
        current_step = status_data.get("current_step", "Processing")
        elapsed_time = int(time.time() - start_time)
        logger.info(f"[{elapsed_time}s] Operation status: {operation_status} - Progress: {progress_percentage}% - Step: {current_step}")
        
        # Log full status response for debugging
        if elapsed_time % 60 == 0:  # Every minute
            logger.info(f"Full status response: {status_data}")
        
        # Detect stuck operations (no progress change)
        if progress_percentage != last_progress:
            last_progress = progress_percentage
            last_progress_change_time = time.time()
        elif time.time() - last_progress_change_time > stuck_threshold:
            error_msg = (f"Operation {operation_id} appears stuck - "
                        f"no progress change for {stuck_threshold}s at {progress_percentage}%")
            logger.error(error_msg)
            logger.error(f"Last status: {status_data}")
            raise RuntimeError(error_msg)
    
    # Verify operation completed successfully
    if operation_status == "failed":
        # Try to get error message from multiple possible fields
        error_msg = status_data.get("error_message", "")
        if not error_msg:
            # Check error_details
            error_details = status_data.get("error_details", {})
            if error_details and isinstance(error_details, dict):
                error_msg = error_details.get("error_message", "")
        if not error_msg:
            # Check current_step for error info (often contains "Failed: <message>")
            current_step = status_data.get("current_step", "")
            if current_step.startswith("Failed:"):
                error_msg = current_step
        if not error_msg:
            error_msg = "Unknown error (no error details available)"
        
        if allow_failure:
            logger.info(f"Operation {operation_id} failed as expected: {error_msg[:100]}...")
            # For allowed failures, continue to retrieve result with error details
        else:
            logger.error(f"Operation {operation_id} failed: {error_msg}")
            raise RuntimeError(f"Operation failed: {error_msg}")
    elif operation_status != "completed":
        logger.error(f"Operation {operation_id} ended with unexpected status: {operation_status}")
        raise RuntimeError(f"Operation ended with unexpected status: {operation_status}")
    
    # Retrieve operation result (works for both completed and failed operations)
    logger.info(f"Retrieving operation result: {result_endpoint}")
    result_response = await http_client.get(result_endpoint, headers=operation_headers, params=query_params, timeout=300.0)
    if result_response.status_code != 200:
        raise RuntimeError(f"Result retrieval failed: {result_response.status_code} - {result_response.text}")
    
    result_data = result_response.json()
    logger.info(f"Operation result retrieved successfully")
    
    return result_data
