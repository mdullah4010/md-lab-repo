# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

"""
LLM Response Quality Evaluation Tests.

This module tests the quality of agent responses using Microsoft Foundry SDK
with Azure AI Evaluation metrics:
- Relevance: How relevant is the response to the query?
- Coherence: Is the response coherent and well-structured?
- Fluency: Is the response grammatically correct and fluent?
- Groundedness: Is the response grounded in the provided context?
- Similarity: How similar is the response to baseline ground truth?

All tests use real Azure connections via Foundry SDK - no mocking is used.

Integration with agent_runner.py:
    1. Run agent_runner.py to collect real responses from the API:
       python tests/evaluation/agent_runner.py --batch --datasets-dir tests/evaluation/datasets
    
    2. Run these tests to evaluate the collected responses:
       pytest tests/evaluation/test_response_quality.py -v -m evaluation

Usage:
    pytest tests/evaluation/test_response_quality.py -v -m evaluation

Prerequisites:
    - Azure AI Foundry project configured
    - Azure OpenAI endpoint with gpt-4 or gpt-4o deployment
    - Azure credentials configured (DefaultAzureCredential)
    - Microsoft Foundry SDK installed (azure-ai-projects)
    - Real responses collected using agent_runner.py (optional, uses samples if not found)

Configuration:
    Test configuration is loaded from .env.test file in the project root.
    
    Required configuration in .env.test:
    - AZURE_EXISTING_AIPROJECT_ENDPOINT: Foundry project connection string
    - AZURE_OPENAI_ENDPOINT: Azure OpenAI endpoint URL
    - AZURE_AI_AGENT_DEPLOYMENT_NAME: Model deployment name
    - AZURE_OPENAI_API_VERSION: API version (default: 2024-02-15-preview)
"""

import os
import sys
import json
import logging
import pytest
import asyncio
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List
from dataclasses import asdict

# Add project root for imports
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "agents"))

# Import AgentResponse from agent_runner
from tests.evaluation.agent_runner import AgentResponse

logger = logging.getLogger(__name__)


# =============================================================================
# Test Markers
# =============================================================================

pytestmark = [
    pytest.mark.evaluation,
]


# =============================================================================
# Evaluation Helper Functions
# =============================================================================

# Maximum response length for LLM evaluators to avoid token limits and slow processing
MAX_RESPONSE_LENGTH_FOR_EVALUATION = 100000  # ~25k tokens


def _strip_image_data(text: str) -> str:
    """
    Remove base64 image data and image URLs from text to prevent OpenAI API errors.
    
    The OpenAI API can misinterpret base64 data as images, causing "Invalid image data" errors.
    The Azure AI Evaluation SDK also tries to parse markdown image syntax and load images.
    This function removes:
    - Markdown image syntax with data URLs: ![alt](data:image/...)
    - Markdown image syntax with any URL: ![alt](http...)
    - Base64 data URLs (data:image/...;base64,...)
    - Large base64-encoded blocks
    - SVG data URLs
    
    Args:
        text: The response text that may contain embedded image data
        
    Returns:
        Text with image data removed (replaced with descriptive text notes)
    """
    if not text:
        return text
    
    original_len = len(text)
    
    # FIRST: Remove complete markdown image syntax to prevent SDK from trying to load images
    # This handles: ![alt text](data:image/png;base64,...)
    text = re.sub(
        r'!\[[^\]]*\]\([^)]+\)',
        '',  # Remove completely - SDK tries to parse these as image paths
        text
    )
    
    # Remove HTML img tags with data URLs or any src
    text = re.sub(
        r'<img[^>]*>',
        '',
        text,
        flags=re.IGNORECASE
    )
    
    # Remove remaining data URLs (data:image/png;base64,... or data:image/svg+xml,...)
    text = re.sub(
        r'data:image/[^;]+;base64,[A-Za-z0-9+/=]+',
        '',
        text
    )
    
    # Remove SVG data URLs
    text = re.sub(
        r'data:image/svg\+xml[^"\'>\s]+',
        '',
        text
    )
    
    # Remove standalone base64 blocks (long sequences of base64 chars, typically > 100 chars)
    # This catches base64 that might not be in a data URL format
    text = re.sub(
        r'(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{100,}={0,2}(?![A-Za-z0-9+/])',
        '',
        text
    )
    
    # Clean up any resulting empty lines or excessive whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    new_len = len(text)
    if new_len < original_len:
        logger.info(f"Stripped image data: {original_len:,} -> {new_len:,} chars ({original_len - new_len:,} removed)")
    
    return text


def _truncate_for_evaluation(text: str, max_length: int = MAX_RESPONSE_LENGTH_FOR_EVALUATION) -> str:
    """
    Truncate text to a maximum length for LLM evaluation.
    
    Very large responses can cause:
    - Token limit errors
    - Slow evaluation times
    - High costs
    
    Args:
        text: The response text
        max_length: Maximum character length (default 100k chars ~25k tokens)
        
    Returns:
        Truncated text with note if truncated
    """
    if not text or len(text) <= max_length:
        return text
    
    original_len = len(text)
    truncated = text[:max_length] + f"\n\n[RESPONSE TRUNCATED: Original length {original_len:,} chars, showing first {max_length:,}]"
    logger.warning(f"Response truncated for evaluation: {original_len:,} -> {max_length:,} chars")
    return truncated


def _preprocess_for_evaluation(text: str) -> str:
    """
    Preprocess response text for LLM-based evaluation.
    
    Applies:
    1. Image data stripping (to prevent "Invalid image data" errors)
    2. Length truncation (to prevent token limits and slow processing)
    
    Args:
        text: Raw response text
        
    Returns:
        Preprocessed text safe for LLM evaluation
    """
    text = _strip_image_data(text)
    text = _truncate_for_evaluation(text)
    return text


def _get_latest_response_file(responses_dir: Path, response_file: str, endpoint: str) -> Path:
    """
    Get the latest response file for an endpoint from the downloaded folder.
    
    If multiple files exist for the same endpoint (e.g., analyzeCode_50000_20260113_155048.md
    and analyzeCode_50000_20260113_155341.md), returns the one with the latest timestamp.
    
    Args:
        responses_dir: Base directory containing response files
        response_file: The response_file path from the JSONL entry (e.g., "downloaded/analyzeCode_50000_20260113_155048.md")
        endpoint: The endpoint name (e.g., "code", "asr", "design")
        
    Returns:
        Path to the latest response file, or the original path if not in downloaded folder
    """
    file_path = responses_dir / response_file
    
    # If the file is not in the downloaded folder, return the original path
    if "downloaded" not in response_file:
        return file_path
    
    # Get the downloaded folder
    downloaded_dir = responses_dir / "downloaded"
    if not downloaded_dir.exists():
        return file_path
    
    # Extract the endpoint prefix from the filename (e.g., "analyzeCode" from "analyzeCode_50000_20260113_155048.md")
    filename = Path(response_file).name
    parts = filename.split("_")
    if len(parts) < 2:
        return file_path
    
    endpoint_prefix = parts[0]  # e.g., "analyzeCode", "generateDesign"
    
    # Find all files matching this endpoint prefix
    matching_files = list(downloaded_dir.glob(f"{endpoint_prefix}_*.md"))
    
    if not matching_files:
        return file_path
    
    if len(matching_files) == 1:
        logger.debug(f"Found single file for endpoint {endpoint_prefix}: {matching_files[0].name}")
        return matching_files[0]
    
    # Multiple files found - sort by timestamp (filename format: {endpoint}_{app_id}_{YYYYMMDD}_{HHMMSS}.md)
    # The timestamp is in the last two parts before .md
    def extract_timestamp(f: Path) -> str:
        """Extract timestamp from filename for sorting."""
        name = f.stem  # Remove .md extension
        parts = name.split("_")
        if len(parts) >= 3:
            # Last two parts are date and time (YYYYMMDD_HHMMSS)
            return "_".join(parts[-2:])
        return name
    
    # Sort by timestamp descending (latest first)
    matching_files.sort(key=extract_timestamp, reverse=True)
    latest_file = matching_files[0]
    
    logger.info(f"Found {len(matching_files)} files for endpoint {endpoint_prefix}, using latest: {latest_file.name}")
    
    return latest_file


def load_collected_responses(endpoint: str, responses_dir: Path = None) -> List[AgentResponse]:
    """
    Load real collected responses from agent_runner.py output files.
    
    If responses have a response_file field, the content is loaded from that file.
    Otherwise, the inline response field is used (for backward compatibility).
    
    For downloaded files in the 'downloaded' subfolder, if multiple files exist
    for the same endpoint, only the latest one (by timestamp) is used.
    
    Args:
        endpoint: Endpoint name (e.g., "design", "asr", "architecture")
        responses_dir: Directory containing response JSONL files
        
    Returns:
        List of AgentResponse objects with response content loaded, or empty list if file not found
    """
    if responses_dir is None:
        responses_dir = Path(__file__).parent / "responses"
    
    # Try both *_requests_responses.jsonl and *_responses.jsonl patterns
    possible_files = [
        responses_dir / f"{endpoint}_requests_responses.jsonl",
        responses_dir / f"{endpoint}_responses.jsonl",
    ]
    
    for response_file in possible_files:
        if response_file.exists():
            logger.info(f"Loading responses from: {response_file}")
            responses = []
            
            with open(response_file) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            data = json.loads(line)
                            # Convert dict to AgentResponse object
                            response = AgentResponse(**data)
                            
                            # If response_file is specified, load content from file
                            if response.response_file:
                                # Check if it's in the downloaded folder and get latest file
                                file_path = _get_latest_response_file(responses_dir, response.response_file, endpoint)
                                if file_path and file_path.exists():
                                    with open(file_path, "r", encoding="utf-8") as rf:
                                        response.response = rf.read()
                                    logger.debug(f"Loaded {len(response.response)} chars from {file_path.name}")
                                else:
                                    logger.warning(f"Response file not found: {response.response_file}")
                            
                            responses.append(response)
                        except (json.JSONDecodeError, TypeError) as e:
                            logger.warning(f"Failed to parse response: {e}")
            
            logger.info(f"Loaded {len(responses)} real responses from {response_file.name}")
            return responses
    
    logger.warning(f"No collected responses found for endpoint: {endpoint}")
    logger.info(f"Searched in: {[str(f) for f in possible_files]}")
    logger.info(f"Run agent_runner.py first to collect responses, or tests will use hardcoded samples")
    return []


def evaluate_response(
    evaluator: Any,
    query: str,
    response: str,
    context: str = ""
) -> float:
    """
    Evaluate a response using the given evaluator.
    
    Args:
        evaluator: Azure AI evaluator instance
        query: The input query
        response: The agent response
        context: Optional context for groundedness evaluation
        
    Returns:
        Score from the evaluator (1-5 scale)
    """
    try:
        # Preprocess response to remove image data and truncate if needed
        processed_response = _preprocess_for_evaluation(response)
        processed_context = _preprocess_for_evaluation(context) if context else ""
        
        if processed_context:
            result = evaluator(
                query=query,
                response=processed_response,
                context=processed_context
            )
        else:
            result = evaluator(
                query=query,
                response=processed_response
            )
        
        # Extract score from result
        if isinstance(result, dict):
            for key in ["score", "relevance", "coherence", "fluency", "groundedness"]:
                if key in result:
                    return float(result[key])
        elif isinstance(result, (int, float)):
            return float(result)
        
        logger.warning(f"Unexpected evaluator result format: {result}")
        return 0.0
        
    except Exception as e:
        logger.error(f"Evaluation failed: {e}")
        raise


# =============================================================================
# Generic Agent Response Quality Tests
# =============================================================================

# Endpoint configuration mapping
ENDPOINT_CONFIG = {
    "/runAnalysis": {
        "name": "analysis",
        "file_pattern": "analysis",
        "agent_name": "responder"
    },
    "/generateAssessmentReport": {
        "name": "asr",
        "file_pattern": "asr",
        "agent_name": "asr"
    },
    "/generateDesign": {
        "name": "design",
        "file_pattern": "design",
        "agent_name": "design"
    },
    "/generateAppPlan": {
        "name": "app_plan",
        "file_pattern": "app_plan",
        "agent_name": "app_planning"
    },
    "/discoverKubernetes": {
        "name": "kubernetes",
        "file_pattern": "kubernetes",
        "agent_name": "kubernetes_discovery"
    },
    "/analyzeCode": {
        "name": "code",
        "file_pattern": "code_analysis",
        "agent_name": "code_analyser"
    },
    "/analyzeArchitecture": {
        "name": "architecture",
        "file_pattern": "architecture",
        "agent_name": "architecture_analyser"
    }
}


class TestAgentResponseQuality:
    """Generic tests for all agent endpoint response quality."""
    
    @pytest.fixture(params=[
        pytest.param("/runAnalysis", id="analysis"),
        pytest.param("/generateAssessmentReport", id="asr"),
        pytest.param("/generateDesign", id="design"),
        pytest.param("/generateAppPlan", id="app_plan"),
        pytest.param("/discoverKubernetes", id="kubernetes"),
        pytest.param("/analyzeCode", id="code"),
        pytest.param("/analyzeArchitecture", id="architecture")
    ])
    def endpoint(self, request):
        """Parameterized fixture providing each endpoint."""
        return request.param
    
    @pytest.fixture
    def sample_response(self, endpoint) -> AgentResponse:
        """
        Load real agent responses for any endpoint.
        
        This fixture requires real responses collected by agent_runner.py.
        If no responses are found, the test will fail with instructions.
        """
        config = ENDPOINT_CONFIG[endpoint]
        file_pattern = config["file_pattern"]
        endpoint_name = config["name"]
        
        real_responses = load_collected_responses(file_pattern)
        
        if not real_responses:
            pytest.fail(
                f"\n\n"
                f"❌ No real {endpoint_name} agent responses found!\n\n"
                f"To collect responses, run:\n\n"
                f"    python tests/evaluation/agent_runner.py \\\n"
                f"        --endpoint {endpoint_name} \\\n"
                f"        --dataset tests/evaluation/datasets/{file_pattern}_requests.jsonl \\\n"
                f"        --output tests/evaluation/responses/{file_pattern}_responses.jsonl \\\n"
                f"        --api-url https://your-api.azurecontainerapps.io\n\n"
                f"This will generate: tests/evaluation/responses/{file_pattern}_responses.jsonl\n"
            )
        
        logger.info(f"✅ Using {len(real_responses)} real API responses for {endpoint_name} evaluation")
        return real_responses[0]
    
    def test_response_relevance(
        self,
        endpoint,
        relevance_evaluator,
        datasets_directory,
        evaluation_config,
        evaluation_results_store
    ):
        """Test that agent responses are relevant to queries.
        
        Tests ALL responses in the dataset, not just the first one.
        """
        config = ENDPOINT_CONFIG[endpoint]
        agent_name = config["agent_name"]
        endpoint_name = config["name"]
        file_pattern = config["file_pattern"]
        threshold = evaluation_config["relevance_threshold"]
        
        # Load ALL real responses
        real_responses = load_collected_responses(file_pattern)
        
        if not real_responses:
            pytest.skip(f"No collected responses found for {endpoint_name}")
        
        # Load request dataset to get test scenarios
        import json
        request_file = datasets_directory / f"{file_pattern}_requests.jsonl"
        requests = []
        if request_file.exists():
            with open(request_file) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        requests.append(json.loads(line))
        
        failures = []
        
        # Test each response
        for idx, response_obj in enumerate(real_responses):
            # Get test scenario name if available
            test_scenario = f"Entry {idx + 1}"
            if idx < len(requests) and requests[idx].get("test_scenario"):
                test_scenario = requests[idx]["test_scenario"]
            
            query = response_obj.query
            response = response_obj.response
            
            logger.info(f"\n{'='*80}")
            logger.info(f"SCENARIO #{idx + 1}: {test_scenario}")
            logger.info(f"{'='*80}")
            logger.info(f"Endpoint: {endpoint_name}")
            logger.info(f"Agent: {agent_name}")
            logger.info(f"Query: {query[:100]}...")
            logger.info(f"Response Length: {len(response):,} chars")
            logger.info(f"{'-'*80}")
            logger.info(f"Evaluating relevance...")
            
            score = evaluate_response(
                evaluator=relevance_evaluator,
                query=query,
                response=response
            )
            
            evaluation_results_store.add_result(
                agent_name=agent_name,
                query=query,
                response=response,
                scores={"relevance": score}
            )
            
            logger.info(f"📊 Relevance Score: {score:.2f}")
            
            if score >= threshold:
                logger.info(f"✅ PASSED: Score {score:.2f} ≥ threshold {threshold}")
            else:
                logger.info(f"❌ FAILED: Score {score:.2f} < threshold {threshold}")
                failures.append((idx + 1, test_scenario, score))
            logger.info(f"{'='*80}\n")
        
        # Assert all passed
        if failures:
            failure_msg = "\n".join(
                f"  Scenario #{num}: {name} - Score {score:.2f} < {threshold}"
                for num, name, score in failures
            )
            pytest.fail(
                f"\n{len(failures)} of {len(real_responses)} scenarios failed relevance check:\n{failure_msg}"
            )
        
        logger.info(f"\n✅ All {len(real_responses)} {endpoint_name} responses passed relevance check (threshold: {threshold})")
    
    def test_response_coherence(
        self,
        endpoint,
        coherence_evaluator,
        datasets_directory,
        evaluation_config,
        evaluation_results_store
    ):
        """Test that agent responses are coherent and well-structured.
        
        Tests ALL responses in the dataset, not just the first one.
        """
        config = ENDPOINT_CONFIG[endpoint]
        agent_name = config["agent_name"]
        endpoint_name = config["name"]
        file_pattern = config["file_pattern"]
        threshold = evaluation_config["coherence_threshold"]
        
        # Load ALL real responses
        real_responses = load_collected_responses(file_pattern)
        
        if not real_responses:
            pytest.skip(f"No collected responses found for {endpoint_name}")
        
        # Load request dataset to get test scenarios
        import json
        request_file = datasets_directory / f"{file_pattern}_requests.jsonl"
        requests = []
        if request_file.exists():
            with open(request_file) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        requests.append(json.loads(line))
        
        failures = []
        
        # Test each response
        for idx, response_obj in enumerate(real_responses):
            # Get test scenario name if available
            test_scenario = f"Entry {idx + 1}"
            if idx < len(requests) and requests[idx].get("test_scenario"):
                test_scenario = requests[idx]["test_scenario"]
            
            query = response_obj.query
            response = response_obj.response
            
            logger.info(f"\n{'='*80}")
            logger.info(f"SCENARIO #{idx + 1}: {test_scenario}")
            logger.info(f"{'='*80}")
            logger.info(f"Endpoint: {endpoint_name}")
            logger.info(f"Agent: {agent_name}")
            logger.info(f"Response Length: {len(response):,} chars")
            logger.info(f"{'-'*80}")
            logger.info(f"Evaluating coherence...")
            
            score = evaluate_response(
                evaluator=coherence_evaluator,
                query=query,
                response=response
            )
            
            evaluation_results_store.add_result(
                agent_name=agent_name,
                query=query,
                response=response,
                scores={"coherence": score}
            )
            
            logger.info(f"📊 Coherence Score: {score:.2f}")
            
            if score >= threshold:
                logger.info(f"✅ PASSED: Score {score:.2f} ≥ threshold {threshold}")
            else:
                logger.info(f"❌ FAILED: Score {score:.2f} < threshold {threshold}")
                failures.append((idx + 1, test_scenario, score))
            logger.info(f"{'='*80}\n")
        
        # Assert all passed
        if failures:
            failure_msg = "\n".join(
                f"  Scenario #{num}: {name} - Score {score:.2f} < {threshold}"
                for num, name, score in failures
            )
            pytest.fail(
                f"\n{len(failures)} of {len(real_responses)} scenarios failed coherence check:\n{failure_msg}"
            )
        
        logger.info(f"\n✅ All {len(real_responses)} {endpoint_name} responses passed coherence check (threshold: {threshold})")
    
    def test_response_fluency(
        self,
        endpoint,
        fluency_evaluator,
        datasets_directory,
        evaluation_config,
        evaluation_results_store
    ):
        """Test that agent responses are grammatically correct and fluent.
        
        Tests ALL responses in the dataset, not just the first one.
        """
        config = ENDPOINT_CONFIG[endpoint]
        agent_name = config["agent_name"]
        endpoint_name = config["name"]
        file_pattern = config["file_pattern"]
        threshold = evaluation_config["fluency_threshold"]
        
        # Load ALL real responses
        real_responses = load_collected_responses(file_pattern)
        
        if not real_responses:
            pytest.skip(f"No collected responses found for {endpoint_name}")
        
        # Load request dataset to get test scenarios
        import json
        request_file = datasets_directory / f"{file_pattern}_requests.jsonl"
        requests = []
        if request_file.exists():
            with open(request_file) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        requests.append(json.loads(line))
        
        failures = []
        
        # Test each response
        for idx, response_obj in enumerate(real_responses):
            # Get test scenario name if available
            test_scenario = f"Entry {idx + 1}"
            if idx < len(requests) and requests[idx].get("test_scenario"):
                test_scenario = requests[idx]["test_scenario"]
            
            query = response_obj.query
            response = response_obj.response
            
            logger.info(f"\n{'='*80}")
            logger.info(f"SCENARIO #{idx + 1}: {test_scenario}")
            logger.info(f"{'='*80}")
            logger.info(f"Endpoint: {endpoint_name}")
            logger.info(f"Agent: {agent_name}")
            logger.info(f"Response Length: {len(response):,} chars")
            logger.info(f"{'-'*80}")
            logger.info(f"Evaluating fluency...")
            
            score = evaluate_response(
                evaluator=fluency_evaluator,
                query=query,
                response=response
            )
            
            evaluation_results_store.add_result(
                agent_name=agent_name,
                query=query,
                response=response,
                scores={"fluency": score}
            )
            
            logger.info(f"📊 Fluency Score: {score:.2f}")
            
            if score >= threshold:
                logger.info(f"✅ PASSED: Score {score:.2f} ≥ threshold {threshold}")
            else:
                logger.info(f"❌ FAILED: Score {score:.2f} < threshold {threshold}")
                failures.append((idx + 1, test_scenario, score))
            logger.info(f"{'='*80}\n")
        
        # Assert all passed
        if failures:
            failure_msg = "\n".join(
                f"  Scenario #{num}: {name} - Score {score:.2f} < {threshold}"
                for num, name, score in failures
            )
            pytest.fail(
                f"\n{len(failures)} of {len(real_responses)} scenarios failed fluency check:\n{failure_msg}"
            )
        
        logger.info(f"\n✅ All {len(real_responses)} {endpoint_name} responses passed fluency check (threshold: {threshold})")
    
    def test_response_similarity(
        self,
        endpoint,
        similarity_evaluator,
        load_baseline_for_request,
        datasets_directory,
        evaluation_results_store
    ):
        """
        Test agent response similarity against ground truth baseline.
        
        Tests ALL entries in the dataset that have a ground_truth_file,
        not just the first one.
        """
        config = ENDPOINT_CONFIG[endpoint]
        agent_name = config["agent_name"]
        endpoint_name = config["name"]
        file_pattern = config["file_pattern"]
        
        similarity_threshold = 3.5  # 70% similarity on 1-5 scale
        
        # Load request dataset to get ground_truth_file entries
        request_file = datasets_directory / f"{file_pattern}_requests.jsonl"
        
        if not request_file.exists():
            pytest.skip(f"{endpoint_name} request dataset not found: {request_file}")
        
        # Load ALL real responses
        real_responses = load_collected_responses(file_pattern)
        
        if not real_responses:
            pytest.skip(f"No collected responses found for {endpoint_name}")
        
        # Find ALL request entries with ground_truth_file
        import json
        requests_with_baselines = []
        with open(request_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    data = json.loads(line)
                    if data.get("ground_truth_file"):
                        requests_with_baselines.append(data)
        
        if not requests_with_baselines:
            pytest.skip(f"{endpoint_name} request dataset has no ground_truth_file references")
        
        # Ensure we have matching number of responses and baseline requests
        if len(real_responses) < len(requests_with_baselines):
            pytest.fail(
                f"\n\n"
                f"❌ Mismatch: Found {len(requests_with_baselines)} baseline entries "
                f"but only {len(real_responses)} responses!\n\n"
                f"Run agent_runner.py to collect all responses.\n"
            )
        
        failures = []
        
        # Test each request-response pair
        for idx, request_data in enumerate(requests_with_baselines):
            if idx >= len(real_responses):
                break
            
            response_obj = real_responses[idx]
            
            # Load baseline from ground_truth_file field
            baseline_content = load_baseline_for_request(request_data)
            
            if baseline_content is None:
                logger.warning(f"Baseline not found: {request_data.get('ground_truth_file')}, skipping")
                continue
            
            test_scenario = request_data.get("test_scenario", f"Entry {idx + 1}")
            
            logger.info(f"\n{'='*80}")
            logger.info(f"SCENARIO #{idx + 1}: {test_scenario}")
            logger.info(f"{'='*80}")
            logger.info(f"Endpoint: {endpoint_name}")
            logger.info(f"Agent: {agent_name}")
            logger.info(f"Baseline File: {request_data['ground_truth_file']}")
            logger.info(f"Response Length: {len(response_obj.response):,} chars")
            logger.info(f"Baseline Length: {len(baseline_content):,} chars")
            logger.info(f"{'-'*80}")
            logger.info(f"Evaluating similarity...")
            
            score = evaluate_similarity(
                evaluator=similarity_evaluator,
                response=response_obj.response,
                ground_truth=baseline_content,
                query=response_obj.query
            )
            
            evaluation_results_store.add_result(
                agent_name=agent_name,
                query=response_obj.query,
                response=response_obj.response,
                scores={"similarity": score}
            )
            
            logger.info(f"📊 Similarity Score: {score:.2f}")
            
            # Check threshold
            if score < similarity_threshold:
                logger.error(f"❌ FAILED: Score {score:.2f} < threshold {similarity_threshold}")
                failures.append({
                    "scenario": test_scenario,
                    "score": score,
                    "baseline": request_data.get('ground_truth_file'),
                    "index": idx + 1
                })
            else:
                logger.info(f"✅ PASSED: Score {score:.2f} ≥ threshold {similarity_threshold}")
            
            logger.info(f"{'='*80}\n")
        
        # Report all failures at once
        if failures:
            failure_msg = f"\n\n❌ {len(failures)} of {len(requests_with_baselines)} {endpoint_name} responses failed similarity check:\n\n"
            for fail in failures:
                failure_msg += (
                    f"  #{fail['index']} - {fail['scenario']}\n"
                    f"    Score: {fail['score']:.2f} (threshold: {similarity_threshold})\n"
                    f"    Baseline: {fail['baseline']}\n\n"
                )
            failure_msg += "Review the baseline documents and generated responses for quality issues.\n"
            pytest.fail(failure_msg)
        
        logger.info(f"\n✅ All {len(requests_with_baselines)} {endpoint_name} responses passed similarity check (threshold: {similarity_threshold})")
    
    def test_response_content_safety(
        self,
        endpoint,
        content_safety_evaluator,
        datasets_directory,
        evaluation_config,
        evaluation_results_store
    ):
        """
        Test agent response content safety.
        
        Evaluates ALL responses for potentially harmful content including:
        - Violent content
        - Sexual content
        - Self-harm related content
        - Hateful and unfair content
        
        Uses 0-7 severity scale where lower is safer:
        - 0: Very low (safe)
        - 2: Low
        - 4: Medium
        - 6: High (harmful)
        
        The evaluator uses the AI Foundry hub's built-in Content Safety endpoint.
        No separate Content Safety resource is required.
        """
        config = ENDPOINT_CONFIG[endpoint]
        agent_name = config["agent_name"]
        endpoint_name = config["name"]
        file_pattern = config["file_pattern"]
        
        # Get safety threshold from config (0-7 scale, lower is safer)
        safety_threshold = evaluation_config.get("content_safety_threshold", 3.0)
        
        # Load ALL real responses
        real_responses = load_collected_responses(file_pattern)
        
        if not real_responses:
            pytest.skip(f"No collected responses found for {endpoint_name}")
        
        # Load request dataset to get test scenarios
        import json
        request_file = datasets_directory / f"{file_pattern}_requests.jsonl"
        requests = []
        if request_file.exists():
            with open(request_file) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        requests.append(json.loads(line))
        
        failures = []
        
        # Test each response
        for idx, response_obj in enumerate(real_responses):
            # Get test scenario name if available
            test_scenario = f"Entry {idx + 1}"
            if idx < len(requests) and requests[idx].get("test_scenario"):
                test_scenario = requests[idx]["test_scenario"]
            
            query = response_obj.query
            response_text = response_obj.response
            
            logger.info(f"\n{'='*80}")
            logger.info(f"SCENARIO #{idx + 1}: {test_scenario}")
            logger.info(f"{'='*80}")
            logger.info(f"Endpoint: {endpoint_name}")
            logger.info(f"Agent: {agent_name}")
            logger.info(f"Query: {query[:100]}..." if len(query) > 100 else f"Query: {query}")
            logger.info(f"Response Length: {len(response_text):,} chars")
            logger.info(f"{'-'*80}")
            logger.info(f"Evaluating content safety...")
            
            # Evaluate content safety
            safety_result = evaluate_content_safety(
                content_safety_evaluator,
                response_text,
                query
            )
            
            # ContentSafetyEvaluator returns multiple scores and severity labels
            # We check the highest score (worst category) against threshold
            max_score = safety_result.get("max_score", 0)
            max_severity = safety_result.get("max_severity", "N/A")
            categories = safety_result.get("categories", {})
            labels = safety_result.get("labels", {})
            
            # Store results
            evaluation_results_store.add_result(
                agent_name=agent_name,
                query=query,
                response=response_text,
                scores={
                    "content_safety_max": max_score,
                    **{f"content_safety_{k}": v for k, v in categories.items()}
                }
            )
            
            # Get chunks count for logging
            chunks_evaluated = safety_result.get("chunks_evaluated", 1)
            
            logger.info(f"📊 Content Safety Scores (evaluated {chunks_evaluated} chunk(s)):")
            logger.info(f"   Violence: {categories.get('violence', 'N/A'):.2f} ({labels.get('violence', 'N/A')})")
            logger.info(f"   Sexual: {categories.get('sexual', 'N/A'):.2f} ({labels.get('sexual', 'N/A')})")
            logger.info(f"   Self-harm: {categories.get('self_harm', 'N/A'):.2f} ({labels.get('self_harm', 'N/A')})")
            logger.info(f"   Hate: {categories.get('hate', 'N/A'):.2f} ({labels.get('hate', 'N/A')})")
            logger.info(f"   Max Score: {max_score:.2f} ({max_severity})")
            
            # Check against threshold (lower is safer)
            if max_score > safety_threshold:
                logger.error(f"❌ FAILED: Max score {max_score:.2f} > threshold {safety_threshold}")
                failures.append({
                    "scenario": test_scenario,
                    "max_score": max_score,
                    "categories": categories,
                    "index": idx + 1
                })
            else:
                logger.info(f"✅ PASSED: Max score {max_score:.2f} ≤ threshold {safety_threshold}")
            
            logger.info(f"{'='*80}\n")
        
        # Report all failures at once
        if failures:
            failure_msg = f"\n\n❌ {len(failures)} of {len(real_responses)} {endpoint_name} responses failed content safety check:\n\n"
            for fail in failures:
                failure_msg += (
                    f"  #{fail['index']} - {fail['scenario']}\n"
                    f"    Max Score: {fail['max_score']:.2f} (threshold: {safety_threshold})\n"
                    f"    Categories: {fail['categories']}\n\n"
                )
            failure_msg += "Review the agent responses for potentially harmful content.\n"
            pytest.fail(failure_msg)
        
        logger.info(f"\n✅ All {len(real_responses)} {endpoint_name} responses passed content safety check (threshold: {safety_threshold})")


# =============================================================================
# Similarity Evaluation Helper
# =============================================================================

def evaluate_similarity(
    evaluator: Any,
    response: str,
    ground_truth: str,
    query: str = ""
) -> float:
    """
    Evaluate response similarity against ground truth baseline.
    
    Args:
        evaluator: SimilarityEvaluator instance
        response: The generated agent response
        ground_truth: The baseline ground truth document
        query: Optional query for context
        
    Returns:
        Similarity score from the evaluator (1-5 scale)
    """
    try:
        # Preprocess both response and ground_truth to remove image data and truncate
        processed_response = _preprocess_for_evaluation(response)
        processed_ground_truth = _preprocess_for_evaluation(ground_truth)
        
        result = evaluator(
            response=processed_response,
            ground_truth=processed_ground_truth,
            query=query
        )
        
        # Extract score from result
        if isinstance(result, dict):
            for key in ["score", "similarity", "gpt_similarity"]:
                if key in result:
                    return float(result[key])
        elif isinstance(result, (int, float)):
            return float(result)
        
        logger.warning(f"Unexpected similarity evaluator result format: {result}")
        return 0.0
        
    except Exception as e:
        logger.error(f"Similarity evaluation failed: {e}")
        raise


# =============================================================================
# Content Safety Evaluation Helper
# =============================================================================

def _chunk_text(text: str, chunk_size: int, overlap: int = 500) -> list:
    """
    Split text into overlapping chunks for comprehensive evaluation.
    
    Args:
        text: The text to split
        chunk_size: Maximum size of each chunk
        overlap: Number of characters to overlap between chunks
        
    Returns:
        List of text chunks
    """
    if len(text) <= chunk_size:
        return [text]
    
    chunks = []
    start = 0
    prev_start = -1  # Track previous start to prevent infinite loop
    
    while start < len(text):
        # Prevent infinite loop if overlap >= chunk_size
        if start <= prev_start:
            break
        prev_start = start
        
        end = start + chunk_size
        chunk = text[start:end]
        chunks.append(chunk)
        start = end - overlap  # Overlap to avoid missing content at boundaries
            
    return chunks


def _aggregate_safety_results(results: list) -> dict:
    """
    Aggregate multiple chunk safety results, taking the worst score for each category.
    
    Args:
        results: List of safety result dictionaries from each chunk
        
    Returns:
        Aggregated result with worst scores across all chunks
    """
    if not results:
        return {
            "categories": {},
            "labels": {},
            "max_score": 0.0,
            "max_severity": "N/A",
            "chunks_evaluated": 0
        }
    
    # Initialize with first result structure
    aggregated_categories = {}
    aggregated_labels = {}
    
    # Track which chunk had the worst score for each category
    for result in results:
        for category, score in result.get("categories", {}).items():
            if category not in aggregated_categories or score > aggregated_categories[category]:
                aggregated_categories[category] = score
                # Also update the label when we find a worse score
                if category in result.get("labels", {}):
                    aggregated_labels[category] = result["labels"][category]
    
    # Calculate overall max score and severity
    max_score = max(aggregated_categories.values()) if aggregated_categories else 0.0
    max_category = max(aggregated_categories, key=aggregated_categories.get) if aggregated_categories else None
    max_severity = aggregated_labels.get(max_category, "N/A") if max_category else "N/A"
    
    return {
        "categories": aggregated_categories,
        "labels": aggregated_labels,
        "max_score": max_score,
        "max_severity": max_severity,
        "chunks_evaluated": len(results)
    }


def evaluate_content_safety(
    evaluator: Any,
    response: str,
    query: str = "",
    max_chars: int = 32000  # ~8000 tokens at 4 chars/token, under 10k limit
) -> dict:
    """
    Evaluate response for content safety across multiple categories.
    For large documents, splits into chunks and evaluates each, then aggregates
    results by taking the worst (highest) score for each category.
    
    Args:
        evaluator: ContentSafetyEvaluator instance
        response: The generated agent response
        query: Optional query for context
        max_chars: Maximum characters per chunk (Content Safety has 10k token limit)
        
    Returns:
        Dictionary with safety scores and severity labels by category:
        {
            "categories": {category: score, ...},
            "labels": {category: severity_label, ...},
            "max_score": highest_score,
            "max_severity": worst_severity_label,
            "chunks_evaluated": number of chunks evaluated
        }
    """
    try:
        # Strip image data first to avoid sending base64 to API
        processed_response = _strip_image_data(response)
        
        # Split response into chunks if it exceeds the limit
        chunks = _chunk_text(processed_response, max_chars)
        
        if len(chunks) > 1:
            logger.info(
                f"Response of {len(processed_response)} chars (after image stripping) split into {len(chunks)} chunks for content safety evaluation"
            )
        
        # Evaluate each chunk
        chunk_results = []
        for i, chunk in enumerate(chunks):
            logger.debug(f"Evaluating chunk {i+1}/{len(chunks)} ({len(chunk)} chars)")
            
            result = evaluator(
                response=chunk,
                query=query
            )
            
            # Extract scores and labels from result
            categories = {}
            labels = {}
            if isinstance(result, dict):
                # Map our category names to SDK key names
                category_mapping = {
                    "violence": ("violence_score", "violence"),
                    "sexual": ("sexual_score", "sexual"),
                    "self_harm": ("self_harm_score", "self_harm"),
                    "hate": ("hate_unfairness_score", "hate_unfairness")
                }
                
                for category, (score_key, label_key) in category_mapping.items():
                    if score_key in result:
                        score_value = result[score_key]
                        categories[category] = float(score_value) if score_value is not None else 0.0
                    if label_key in result:
                        labels[category] = result[label_key]
            
            chunk_results.append({
                "categories": categories,
                "labels": labels
            })
        
        # Aggregate results from all chunks (take worst scores)
        aggregated = _aggregate_safety_results(chunk_results)
        
        if len(chunks) > 1:
            logger.info(
                f"Content safety evaluation complete: {len(chunks)} chunks, "
                f"max_score={aggregated['max_score']}, max_severity={aggregated['max_severity']}"
            )
        
        return aggregated
        
    except Exception as e:
        logger.error(f"Content safety evaluation failed: {e}")
        raise
