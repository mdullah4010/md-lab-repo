# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

"""
Evaluation test fixtures for LLM response quality testing.

This module provides fixtures for evaluating agent response quality
using Microsoft Foundry SDK (azure.ai.projects) with Azure AI Evaluation.

All tests use real Azure connections - no mocking is used.
"""

import os
import sys
import json
import logging
import pytest
from pathlib import Path
from typing import Dict, Any, List, Optional, Generator
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

# Add project root for imports
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "agents"))

logger = logging.getLogger(__name__)


# =============================================================================
# Evaluation Configuration
# =============================================================================

@pytest.fixture(scope="session")
def foundry_project_client(test_environment) -> AIProjectClient:
    """
    Create Azure AI Foundry project client for evaluations.
    
    Returns:
        AIProjectClient instance connected to the Foundry project.
    """
    connection_string = test_environment.get("ai_project_connection_string")
    
    if not connection_string:
        pytest.skip("AZURE_EXISTING_AIPROJECT_ENDPOINT not configured for Foundry evaluations")
    
    try:
        logger.info("Creating Foundry project client...")
        # AIProjectClient accepts connection string directly as first parameter
        client = AIProjectClient(
            credential=DefaultAzureCredential(),
            endpoint=connection_string
        )
        logger.info(f"✅ Connected to Foundry project: {connection_string}")
        return client
    except Exception as e:
        logger.error(f"Failed to create Foundry project client: {e}")
        pytest.skip(f"Could not connect to Foundry project: {e}")


@pytest.fixture(scope="session")
def evaluation_config(test_environment, foundry_project_client) -> Dict[str, Any]:
    """
    Provide evaluation-specific configuration using Foundry SDK.
    
    Returns:
        Dictionary containing evaluation configuration.
    """
    config = {
        "foundry_client": foundry_project_client,
        "azure_endpoint": test_environment["openai_endpoint"],
        "azure_deployment": test_environment["openai_deployment"],
        "api_version": test_environment["openai_api_version"],
        
        # Thresholds
        "relevance_threshold": test_environment["relevance_threshold"],
        "groundedness_threshold": test_environment["groundedness_threshold"],
        "coherence_threshold": 4.0,
        "fluency_threshold": 4.0,
        "content_safety_threshold": 3.0,  # Content safety: 1-5 scale, lower is safer
        
        # Paths
        "datasets_dir": Path(__file__).parent / "datasets",
    }
    
    logger.info("Evaluation configuration loaded (Foundry SDK)")
    logger.info(f"  Foundry Project: {test_environment.get('ai_project_connection_string', 'N/A')}")
    logger.info(f"  OpenAI Deployment: {config['azure_deployment']}")
    
    return config


@pytest.fixture(scope="session")
def model_config(evaluation_config) -> Dict[str, str]:
    """
    Provide model configuration for evaluators.
    
    Returns:
        Dictionary with Azure OpenAI model configuration.
    """
    return {
        "azure_endpoint": evaluation_config["azure_endpoint"],
        "azure_deployment": evaluation_config["azure_deployment"],
        "api_version": evaluation_config["api_version"],
    }


# =============================================================================
# Evaluator Fixtures
# =============================================================================

@pytest.fixture(scope="session")
def relevance_evaluator(evaluation_config):
    """
    Create RelevanceEvaluator using Foundry SDK.
    
    Returns:
        RelevanceEvaluator instance configured with Foundry project.
    """
    try:
        from azure.ai.evaluation import RelevanceEvaluator
        
        logger.info("Creating RelevanceEvaluator (Foundry SDK)...")
        
        # Use Foundry-compatible model config
        model_config = {
            "azure_endpoint": evaluation_config["azure_endpoint"],
            "azure_deployment": evaluation_config["azure_deployment"],
            "api_version": evaluation_config["api_version"],
        }
        
        evaluator = RelevanceEvaluator(model_config)
        logger.info("✅ RelevanceEvaluator created with Foundry integration")
        return evaluator
        
    except ImportError:
        pytest.skip("azure-ai-evaluation package not installed")


@pytest.fixture(scope="session")
def coherence_evaluator(evaluation_config):
    """
    Create CoherenceEvaluator using Foundry SDK.
    
    Returns:
        CoherenceEvaluator instance configured with Foundry project.
    """
    try:
        from azure.ai.evaluation import CoherenceEvaluator
        
        logger.info("Creating CoherenceEvaluator (Foundry SDK)...")
        
        model_config = {
            "azure_endpoint": evaluation_config["azure_endpoint"],
            "azure_deployment": evaluation_config["azure_deployment"],
            "api_version": evaluation_config["api_version"],
        }
        
        evaluator = CoherenceEvaluator(model_config)
        logger.info("✅ CoherenceEvaluator created with Foundry integration")
        return evaluator
        
    except ImportError:
        pytest.skip("azure-ai-evaluation package not installed")


@pytest.fixture(scope="session")
def fluency_evaluator(evaluation_config):
    """
    Create FluencyEvaluator using Foundry SDK.
    
    Returns:
        FluencyEvaluator instance configured with Foundry project.
    """
    try:
        from azure.ai.evaluation import FluencyEvaluator
        
        logger.info("Creating FluencyEvaluator (Foundry SDK)...")
        
        model_config = {
            "azure_endpoint": evaluation_config["azure_endpoint"],
            "azure_deployment": evaluation_config["azure_deployment"],
            "api_version": evaluation_config["api_version"],
        }
        
        evaluator = FluencyEvaluator(model_config)
        logger.info("✅ FluencyEvaluator created with Foundry integration")
        return evaluator
        
    except ImportError:
        pytest.skip("azure-ai-evaluation package not installed")


@pytest.fixture(scope="session")
def groundedness_evaluator(evaluation_config):
    """
    Create GroundednessEvaluator using Foundry SDK.
    
    Returns:
        GroundednessEvaluator instance configured with Foundry project.
    """
    try:
        from azure.ai.evaluation import GroundednessEvaluator
        
        logger.info("Creating GroundednessEvaluator (Foundry SDK)...")
        
        model_config = {
            "azure_endpoint": evaluation_config["azure_endpoint"],
            "azure_deployment": evaluation_config["azure_deployment"],
            "api_version": evaluation_config["api_version"],
        }
        
        evaluator = GroundednessEvaluator(model_config)
        logger.info("✅ GroundednessEvaluator created with Foundry integration")
        return evaluator
        
    except ImportError:
        pytest.skip("azure-ai-evaluation package not installed")


@pytest.fixture(scope="session")
def content_safety_evaluator(evaluation_config, test_environment):
    """
    Create ContentSafetyEvaluator using Foundry SDK to detect potentially harmful content.
    
    This composite evaluator checks for multiple safety categories including:
    - Violent content
    - Sexual content
    - Self-harm related content
    - Hateful and unfair content
    
    Note: Uses project endpoint URL format instead of dict format to work with
    AI Foundry Cognitive Services projects (not ML workspaces).
    
    Returns:
        ContentSafetyEvaluator: Configured evaluator ready for content safety assessment
    """
    try:
        from azure.ai.evaluation import ContentSafetyEvaluator
        from azure.identity import AzureCliCredential
        
        # Use the project endpoint URL directly - this works with AI Foundry projects!
        # Format: https://{resource_name}.services.ai.azure.com/api/projects/{project_name}
        # The dict format only works with ML workspaces, not Cognitive Services AI Foundry
        project_endpoint = test_environment.get("ai_project_connection_string")
        
        if not project_endpoint:
            logger.warning("ai_project_connection_string not found in environment")
            pytest.skip("AI Foundry project connection string required for ContentSafetyEvaluator")
        
        logger.info(f"Content Safety using project endpoint: {project_endpoint}")
        
        # Use AzureCliCredential to avoid managed identity timeout on local dev
        credential = AzureCliCredential()
        
        # Content safety evaluator with project endpoint URL (not dict!)
        # This approach works with AI Foundry Cognitive Services projects
        evaluator = ContentSafetyEvaluator(
            credential=credential,
            azure_ai_project=project_endpoint  # Pass URL string, not dict
        )
        logger.info("✅ ContentSafetyEvaluator created with AI Foundry project endpoint")
        return evaluator
        
    except ImportError:
        pytest.skip("azure-ai-evaluation package not installed")


@pytest.fixture(scope="session")
def similarity_evaluator(evaluation_config):
    """
    Create SimilarityEvaluator using Foundry SDK for comparing responses against ground truth baselines.
    
    This evaluator measures how similar the generated response is to a known-good
    baseline document, useful for evaluating migration document quality.
    
    Returns:
        SimilarityEvaluator instance configured with Foundry project.
    """
    try:
        from azure.ai.evaluation import SimilarityEvaluator
        
        logger.info("Creating SimilarityEvaluator (Foundry SDK)...")
        
        model_config = {
            "azure_endpoint": evaluation_config["azure_endpoint"],
            "azure_deployment": evaluation_config["azure_deployment"],
            "api_version": evaluation_config["api_version"],
        }
        
        evaluator = SimilarityEvaluator(model_config)
        logger.info("✅ SimilarityEvaluator created with Foundry integration")
        return evaluator
        
    except ImportError:
        pytest.skip("azure-ai-evaluation package not installed")


@pytest.fixture(scope="session")
def all_evaluators(
    relevance_evaluator,
    coherence_evaluator,
    fluency_evaluator,
    groundedness_evaluator
) -> Dict[str, Any]:
    """
    Provide all evaluators as a dictionary.
    
    Returns:
        Dictionary containing all evaluator instances.
    """
    return {
        "relevance": relevance_evaluator,
        "coherence": coherence_evaluator,
        "fluency": fluency_evaluator,
        "groundedness": groundedness_evaluator,
    }


@pytest.fixture(scope="session")
def all_evaluators_with_similarity(
    relevance_evaluator,
    coherence_evaluator,
    fluency_evaluator,
    groundedness_evaluator,
    similarity_evaluator
) -> Dict[str, Any]:
    """
    Provide all evaluators including SimilarityEvaluator.
    
    Returns:
        Dictionary containing all evaluator instances including similarity.
    """
    return {
        "relevance": relevance_evaluator,
        "coherence": coherence_evaluator,
        "fluency": fluency_evaluator,
        "groundedness": groundedness_evaluator,
        "similarity": similarity_evaluator,
    }


# =============================================================================
# Baseline Fixtures for Ground Truth Comparison
# =============================================================================

@pytest.fixture(scope="session")
def baselines_directory() -> Path:
    """
    Provide path to the baselines directory containing ground truth documents.
    
    Returns:
        Path to the baselines directory.
    """
    baselines_dir = Path(__file__).parent / "baselines"
    
    if not baselines_dir.exists():
        logger.warning(f"Baselines directory not found: {baselines_dir}")
        baselines_dir.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"Baselines directory: {baselines_dir}")
    return baselines_dir


@pytest.fixture(scope="session")
def load_baseline(baselines_directory):
    """
    Factory fixture to load baseline documents by relative path.
    
    Returns:
        Function that loads baseline content given a relative path.
    """
    def _load_baseline(relative_path: str) -> Optional[str]:
        """
        Load baseline document content.
        
        Args:
            relative_path: Path relative to baselines directory (e.g., "design/java-spring-001.md")
                           Can also include "baselines/" prefix which will be stripped.
            
        Returns:
            Content of the baseline document, or None if not found.
        """
        # Strip "baselines/" prefix if present (dataset files may include it)
        if relative_path.startswith("baselines/"):
            relative_path = relative_path[len("baselines/"):]
        
        baseline_path = baselines_directory / relative_path
        
        if not baseline_path.exists():
            logger.warning(f"Baseline not found: {baseline_path}")
            return None
        
        try:
            content = baseline_path.read_text(encoding="utf-8")
            logger.debug(f"Loaded baseline: {relative_path} ({len(content)} chars)")
            return content
        except Exception as e:
            logger.error(f"Error loading baseline {relative_path}: {e}")
            return None
    
    return _load_baseline


@pytest.fixture(scope="session")
def load_baseline_for_request(load_baseline):
    """
    Factory fixture to load baseline for a dataset request entry.
    
    Extracts ground_truth_file from request data and loads the baseline.
    
    Returns:
        Function that loads baseline content given a request dictionary.
    """
    def _load_for_request(request_data: Dict[str, Any]) -> Optional[str]:
        """
        Load baseline for a request entry.
        
        Args:
            request_data: Dataset entry containing ground_truth_file field.
            
        Returns:
            Baseline content, or None if not available.
        """
        ground_truth_file = request_data.get("ground_truth_file")
        
        if not ground_truth_file:
            logger.debug(f"No ground_truth_file in request: {request_data.get('app_id', 'unknown')}")
            return None
        
        return load_baseline(ground_truth_file)
    
    return _load_for_request


# =============================================================================
# Dataset Fixtures
# =============================================================================

@pytest.fixture(scope="session")
def datasets_directory(evaluation_config) -> Path:
    """
    Create and return the datasets directory.
    
    Returns:
        Path to the datasets directory.
    """
    datasets_dir = evaluation_config["datasets_dir"]
    datasets_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Datasets directory: {datasets_dir}")
    return datasets_dir


@pytest.fixture(scope="session")
def responses_directory() -> Path:
    """
    Create and return the responses directory for evaluation results.
    
    Returns:
        Path to the responses directory.
    """
    responses_dir = Path(__file__).parent / "responses"
    responses_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Responses directory: {responses_dir}")
    return responses_dir


# =============================================================================
# New Endpoint-Specific Dataset Fixtures
# =============================================================================

@pytest.fixture(scope="session")
def design_requests_dataset(datasets_directory) -> Path:
    """
    Provide path to design requests dataset.
    
    Uses structured API request parameters (app_id, storage_account_name, etc.)
    instead of free-form queries.
    
    Returns:
        Path to the design_requests.jsonl file.
    """
    dataset_path = datasets_directory / "design_requests.jsonl"
    
    if not dataset_path.exists():
        pytest.skip("design_requests.jsonl dataset not found. Create it in tests/evaluation/datasets/")
    
    logger.info(f"Using design requests dataset: {dataset_path}")
    return dataset_path


@pytest.fixture(scope="session")
def asr_requests_dataset(datasets_directory) -> Path:
    """
    Provide path to ASR (Assessment Report) requests dataset.
    
    Uses structured API request parameters.
    
    Returns:
        Path to the asr_requests.jsonl file.
    """
    dataset_path = datasets_directory / "asr_requests.jsonl"
    
    if not dataset_path.exists():
        pytest.skip("asr_requests.jsonl dataset not found. Create it in tests/evaluation/datasets/")
    
    logger.info(f"Using ASR requests dataset: {dataset_path}")
    return dataset_path


@pytest.fixture(scope="session")
def architecture_analysis_requests_dataset(datasets_directory) -> Path:
    """
    Provide path to architecture analysis requests dataset.
    
    Uses structured API request parameters including design_doc_url.
    
    Returns:
        Path to the architecture_analysis_requests.jsonl file.
    """
    dataset_path = datasets_directory / "architecture_analysis_requests.jsonl"
    
    if not dataset_path.exists():
        pytest.skip("architecture_analysis_requests.jsonl dataset not found. Create it in tests/evaluation/datasets/")
    
    logger.info(f"Using architecture analysis requests dataset: {dataset_path}")
    return dataset_path


@pytest.fixture(scope="session")
def code_analysis_requests_dataset(datasets_directory) -> Path:
    """
    Provide path to code analysis requests dataset.
    
    Uses structured API request parameters including repo_url.
    
    Returns:
        Path to the code_analysis_requests.jsonl file.
    """
    dataset_path = datasets_directory / "code_analysis_requests.jsonl"
    
    if not dataset_path.exists():
        pytest.skip("code_analysis_requests.jsonl dataset not found. Create it in tests/evaluation/datasets/")
    
    logger.info(f"Using code analysis requests dataset: {dataset_path}")
    return dataset_path


@pytest.fixture(scope="session")
def kubernetes_discovery_requests_dataset(datasets_directory) -> Path:
    """
    Provide path to Kubernetes discovery requests dataset.
    
    Uses structured API request parameters.
    
    Returns:
        Path to the kubernetes_discovery_requests.jsonl file.
    """
    dataset_path = datasets_directory / "kubernetes_discovery_requests.jsonl"
    
    if not dataset_path.exists():
        pytest.skip("kubernetes_discovery_requests.jsonl dataset not found. Create it in tests/evaluation/datasets/")
    
    logger.info(f"Using Kubernetes discovery requests dataset: {dataset_path}")
    return dataset_path


# =============================================================================
# Legacy Query-Based Dataset Fixtures (Deprecated)
# =============================================================================

@pytest.fixture(scope="session")
def design_queries_dataset(datasets_directory) -> Path:
    """
    [DEPRECATED] Provide path to legacy design queries dataset.
    
    Use design_requests_dataset instead for proper API parameter structure.
    
    Returns:
        Path to the design_queries.jsonl file.
    """
    logger.warning("design_queries_dataset is deprecated. Use design_requests_dataset instead.")
    dataset_path = datasets_directory / "design_queries.jsonl"
    
    if not dataset_path.exists():
        logger.info("Creating sample design queries dataset...")
        _create_sample_design_dataset(dataset_path)
    
    return dataset_path


@pytest.fixture(scope="session")
def asr_queries_dataset(datasets_directory) -> Path:
    """
    [DEPRECATED] Provide path to legacy ASR queries dataset.
    
    Use asr_requests_dataset instead for proper API parameter structure.
    
    Returns:
        Path to the asr_queries.jsonl file.
    """
    logger.warning("asr_queries_dataset is deprecated. Use asr_requests_dataset instead.")
    dataset_path = datasets_directory / "asr_queries.jsonl"
    
    if not dataset_path.exists():
        logger.info("Creating sample ASR queries dataset...")
        _create_sample_asr_dataset(dataset_path)
    
    return dataset_path


@pytest.fixture(scope="session")
def orchestrator_queries_dataset(datasets_directory) -> Path:
    """
    [DEPRECATED] Provide path to legacy orchestrator queries dataset.
    
    Use appropriate endpoint-specific dataset instead.
    
    Returns:
        Path to the orchestrator_queries.jsonl file.
    """
    logger.warning("orchestrator_queries_dataset is deprecated. Use endpoint-specific datasets instead.")
    dataset_path = datasets_directory / "orchestrator_queries.jsonl"
    
    if not dataset_path.exists():
        logger.info("Creating sample orchestrator queries dataset...")
        _create_sample_orchestrator_dataset(dataset_path)
    
    return dataset_path


# =============================================================================
# Helper Functions for Dataset Creation
# =============================================================================

def _create_sample_design_dataset(path: Path):
    """Create sample design queries dataset."""
    samples = [
        {
            "query": "Generate Azure migration design for a Java Spring Boot application with PostgreSQL database",
            "context": "Enterprise application with 50K daily users",
            "app_id": "JAVA001",
            "expected_sections": ["architecture", "database", "networking", "security"]
        },
        {
            "query": "Create migration plan for .NET Framework 4.8 monolith to Azure",
            "context": "Legacy WCF services, SQL Server backend",
            "app_id": "NET001",
            "expected_sections": ["modernization", "containerization", "database"]
        },
        {
            "query": "Design cloud-native architecture for microservices migration",
            "context": "Currently on-premises Kubernetes",
            "app_id": "K8S001",
            "expected_sections": ["aks", "networking", "monitoring", "security"]
        },
        {
            "query": "Plan migration for Oracle-based application to Azure",
            "context": "Mission-critical financial application",
            "app_id": "ORA001",
            "expected_sections": ["database", "high-availability", "disaster-recovery"]
        },
    ]
    
    with open(path, "w") as f:
        for sample in samples:
            f.write(json.dumps(sample) + "\n")
    
    logger.info(f"Created design dataset with {len(samples)} samples")


def _create_sample_asr_dataset(path: Path):
    """Create sample ASR queries dataset."""
    samples = [
        {
            "query": "Perform security review for healthcare application migrating to Azure",
            "context": "HIPAA compliance required, handles PHI data",
            "app_id": "HEALTH001",
            "expected_sections": ["hipaa", "encryption", "access-control", "audit-logging"]
        },
        {
            "query": "Security assessment for financial services application",
            "context": "PCI-DSS compliance, payment processing",
            "app_id": "FIN001",
            "expected_sections": ["pci-dss", "encryption", "network-security"]
        },
        {
            "query": "Review authentication and authorization for multi-tenant SaaS",
            "context": "Azure AD B2C integration",
            "app_id": "SAAS001",
            "expected_sections": ["authentication", "authorization", "tenant-isolation"]
        },
    ]
    
    with open(path, "w") as f:
        for sample in samples:
            f.write(json.dumps(sample) + "\n")
    
    logger.info(f"Created ASR dataset with {len(samples)} samples")


def _create_sample_orchestrator_dataset(path: Path):
    """Create sample orchestrator queries dataset."""
    samples = [
        {
            "query": "What is the recommended Azure architecture for this application?",
            "context": "Migration assessment",
            "expected_agent": "design",
            "app_id": "TEST001"
        },
        {
            "query": "Analyze the Kubernetes deployment configuration",
            "context": "Infrastructure discovery",
            "expected_agent": "kubernetes_discovery",
            "app_id": "TEST001"
        },
        {
            "query": "Generate migration assessment report",
            "context": "Security assessment",
            "expected_agent": "asr",
            "app_id": "TEST001"
        },
        {
            "query": "What are the current infrastructure requirements?",
            "context": "General inquiry",
            "expected_agent": "responder",
            "app_id": "TEST001"
        },
    ]
    
    with open(path, "w") as f:
        for sample in samples:
            f.write(json.dumps(sample) + "\n")
    
    logger.info(f"Created orchestrator dataset with {len(samples)} samples")


# =============================================================================
# Evaluation Results Storage
# =============================================================================

class EvaluationResultsStore:
    """
    Stores and manages evaluation results across tests.
    
    Supports both local file storage and publishing to Azure AI Foundry portal.
    """
    
    def __init__(
        self, 
        output_dir: Path,
        foundry_client: Optional[AIProjectClient] = None,
        enable_foundry_tracking: bool = False
    ):
        self.output_dir = output_dir
        self.results: List[Dict[str, Any]] = []
        self.foundry_client = foundry_client
        self.enable_foundry_tracking = enable_foundry_tracking
        self.foundry_run_id: Optional[str] = None
        
    def add_result(
        self,
        agent_name: str,
        query: str,
        response: str,
        scores: Dict[str, float],
        metadata: Optional[Dict[str, Any]] = None
    ):
        """
        Add an evaluation result.
        
        Args:
            agent_name: Name of the agent/endpoint being evaluated
            query: Input query or request description
            response: Generated response
            scores: Dictionary of metric scores (e.g., {"relevance": 5.0})
            metadata: Optional metadata (test_scenario, timestamp, etc.)
        """
        # Store each metric as a separate result for proper tracking
        for metric_name, score in scores.items():
            result = {
                "agent_name": agent_name,
                "metric_name": metric_name,  # Add explicit metric_name field
                "score": score,  # Add explicit score field
                "query": query,
                "response_length": len(response),
                "scores": {metric_name: score},  # Keep for backward compatibility
                "timestamp": metadata.get("timestamp") if metadata else None,
                "metadata": metadata or {}
            }
            self.results.append(result)
        
    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all evaluation results."""
        if not self.results:
            return {"total": 0, "by_agent": {}}
        
        by_agent = {}
        for result in self.results:
            agent = result["agent_name"]
            if agent not in by_agent:
                by_agent[agent] = {"count": 0, "scores": {}}
            
            by_agent[agent]["count"] += 1
            for metric, score in result["scores"].items():
                if metric not in by_agent[agent]["scores"]:
                    by_agent[agent]["scores"][metric] = []
                by_agent[agent]["scores"][metric].append(score)
        
        # Calculate averages
        for agent, data in by_agent.items():
            for metric, values in data["scores"].items():
                data["scores"][metric] = sum(values) / len(values) if values else 0
        
        return {
            "total": len(self.results),
            "by_agent": by_agent
        }
    
    def save_results(self, filename: str = "evaluation_results.json"):
        """Save results to local file."""
        output_path = self.output_dir / filename
        
        with open(output_path, "w") as f:
            json.dump({
                "results": self.results,
                "summary": self.get_summary()
            }, f, indent=2)
        
        logger.info(f"Evaluation results saved to: {output_path}")
    
    def publish_to_foundry(self, run_name: str = "Agent Quality Evaluation"):
        """
        Publish evaluation results to Azure AI Foundry portal.
        
        This creates an evaluation run in Foundry with full lineage tracking,
        enabling dashboards, comparison views, and automated reporting.
        
        Args:
            run_name: Display name for the evaluation run in Foundry portal
        """
        if not self.enable_foundry_tracking or not self.foundry_client:
            logger.info("Foundry tracking disabled - skipping portal publish")
            return
        
        if not self.results:
            logger.warning("No evaluation results to publish")
            return
        
        try:
            from datetime import datetime
            from azure.ai.projects.models import Evaluation, InputData, EvaluatorConfiguration
            
            # Prepare evaluation data for Foundry
            summary = self.get_summary()
            
            # Create temporary JSONL file with results for Foundry
            results_file = self.output_dir / f"foundry_eval_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.jsonl"
            with open(results_file, 'w') as f:
                for result in self.results:
                    f.write(json.dumps(result) + '\n')
            
            logger.info(f"📊 Publishing {len(self.results)} evaluation results to Foundry portal...")
            logger.info(f"   Run Name: {run_name}")
            logger.info(f"   Agents Evaluated: {', '.join(summary['by_agent'].keys())}")
            
            evaluation_name = f"agent_quality_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
            
            # Publish metrics to Application Insights for automatic portal visibility
            try:
                from applicationinsights import TelemetryClient
                
                # Get Application Insights connection string from Foundry
                app_insights_conn_str = self.foundry_client.telemetry.get_application_insights_connection_string()
                
                # Extract instrumentation key from connection string
                # Format: InstrumentationKey=xxx;IngestionEndpoint=xxx;...
                instrumentation_key = None
                for part in app_insights_conn_str.split(';'):
                    if part.startswith('InstrumentationKey='):
                        instrumentation_key = part.split('=')[1]
                        break
                
                if instrumentation_key:
                    # Create telemetry client
                    tc = TelemetryClient(instrumentation_key)
                    logger.info(f"📡 TelemetryClient created with key: {instrumentation_key[:8]}...")
                    
                    # Prepare agent and metric names as strings
                    agent_names = ', '.join(list(summary.get('by_agent', {}).keys())) if 'by_agent' in summary else 'unknown'
                    metric_names = ', '.join(list(summary.get('by_metric', {}).keys())) if 'by_metric' in summary else 'unknown'
                    num_agents = len(summary.get('by_agent', {}))
                    
                    # Track evaluation run as custom event
                    tc.track_event(
                        'EvaluationRunCompleted',
                        properties={
                            'run_name': run_name,
                            'evaluation_name': evaluation_name,
                            'num_results': str(len(self.results)),
                            'agents': agent_names,
                            'metrics': metric_names
                        },
                        measurements={
                            'total_evaluations': len(self.results),
                            'num_agents': num_agents
                        }
                    )
                    logger.info(f"📊 Tracked event: EvaluationRunCompleted")
                    
                    # Track individual evaluation metrics
                    # Group results by scenario for cleaner logging
                    results_by_query = {}
                    for result in self.results:
                        query = result.get('query', 'unknown')[:50]  # Truncate for display
                        if query not in results_by_query:
                            results_by_query[query] = []
                        results_by_query[query].append(result)
                    
                    metrics_tracked = 0
                    for query, results in results_by_query.items():
                        for result in results:
                            for metric_name, score in result.get('scores', {}).items():
                                full_metric_name = f"Evaluation_{metric_name}"
                                
                                # Ensure score is numeric
                                try:
                                    score_value = float(score) if score is not None else 0.0
                                except (TypeError, ValueError):
                                    logger.warning(f"Non-numeric score for {full_metric_name}: {score}, using 0.0")
                                    score_value = 0.0
                                
                                tc.track_metric(
                                    full_metric_name,
                                    score_value,
                                    properties={
                                        'agent': str(result.get('agent_name', 'unknown')),
                                        'endpoint': str(result.get('endpoint_name', 'unknown')),
                                        'query': query,
                                        'evaluation_run': str(evaluation_name)
                                    }
                                )
                                metrics_tracked += 1
                    
                    # Track aggregated metrics by agent (silently - detailed logging removed)
                    agent_metrics_tracked = 0
                    for agent_name, agent_summary in summary.get('by_agent', {}).items():
                        for metric_name, score in agent_summary.items():
                            # Skip non-metric keys (count, scores dict, etc.)
                            if metric_name in ('count', 'scores') or not isinstance(score, (int, float)):
                                continue
                            
                            full_metric_name = f"Agent_{metric_name}"
                            
                            # Ensure score is numeric
                            try:
                                score_value = float(score) if score is not None else 0.0
                            except (TypeError, ValueError):
                                score_value = 0.0
                            
                            tc.track_metric(
                                full_metric_name,
                                score_value,
                                properties={
                                    'agent': str(agent_name),
                                    'evaluation_run': str(evaluation_name),
                                    'aggregation': 'mean'
                                }
                            )
                            agent_metrics_tracked += 1
                    
                    # Flush telemetry to ensure immediate upload
                    logger.info(f"🔄 Flushing telemetry client...")
                    tc.flush()
                    logger.info(f"✅ Flush completed")
                    
                    logger.info(f"✅ Evaluation metrics published to Application Insights")
                    logger.info(f"   Published {metrics_tracked} individual metrics")
                    logger.info(f"   Published {agent_metrics_tracked} agent summary metrics")
                    logger.info(f"")
                    logger.info(f"📊 View in Azure Portal:")
                    logger.info(f"   1. Go to https://portal.azure.com/")
                    logger.info(f"   2. Navigate to Application Insights resource")
                    logger.info(f"   3. Go to Logs → Query: customEvents | where name == 'EvaluationRunCompleted'")
                    logger.info(f"   4. Or Metrics → Custom metrics → Evaluation_* metrics")
                    
                else:
                    logger.warning("Could not extract instrumentation key from connection string")
                    logger.info(f"ℹ️  Results saved locally in: {results_file}")
                    
            except ImportError:
                logger.warning("applicationinsights package not installed")
                logger.info(f"ℹ️  Install with: pip install applicationinsights")
                logger.info(f"ℹ️  Results saved locally in: {results_file}")
                
            except Exception as publish_error:
                logger.warning(f"Could not publish to Application Insights: {publish_error}")
                logger.info(f"ℹ️  Results saved locally in: {results_file}")
            
            self.foundry_run_id = evaluation_name
            
            # Keep JSONL file as backup
            logger.info(f"📁 Results also saved to: {results_file.name}")
            
        except Exception as e:
            logger.error(f"Failed to export results for Foundry: {e}")
            logger.warning("Evaluation results saved locally")
            logger.debug(f"Error details: {str(e)}", exc_info=True)


@pytest.fixture(scope="session")
def evaluation_results_store(
    responses_directory,
    foundry_project_client,
    test_environment
) -> Generator[EvaluationResultsStore, None, None]:
    """
    Provide evaluation results store with optional Foundry portal tracking.
    
    Set ENABLE_FOUNDRY_TRACKING=true in .env.test to publish results to portal.
    
    Yields:
        EvaluationResultsStore instance.
    """
    enable_tracking = test_environment.get("enable_foundry_tracking", "false").lower() == "true"
    
    store = EvaluationResultsStore(
        output_dir=responses_directory,
        foundry_client=foundry_project_client if enable_tracking else None,
        enable_foundry_tracking=enable_tracking
    )
    
    if enable_tracking:
        logger.info("🚀 Foundry portal tracking ENABLED")
        logger.info("   Evaluation results will be published to Azure AI Foundry")
    else:
        logger.info("📁 Foundry portal tracking disabled (local storage only)")
        logger.info("   Set ENABLE_FOUNDRY_TRACKING=true in .env.test to enable")
    
    yield store
    
    # Save results at end of session
    if store.results:
        # Save to local file
        store.save_results()
        
        # Publish to Foundry portal if enabled
        if enable_tracking:
            from datetime import datetime
            run_name = f"Agent Quality Evaluation - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            store.publish_to_foundry(run_name)
        
        # Print summary grouped by scenario
        logger.info("=" * 50)
        logger.info("EVALUATION SUMMARY")
        logger.info(f"Total evaluations: {len(store.results)}")
        logger.info("")
        
        # Group results by agent and scenario
        by_agent_scenario = {}
        for result in store.results:
            agent = result["agent_name"]
            query = result.get("query", "Unknown scenario")
            scenario = query[:80] + "..." if len(query) > 80 else query
            
            if agent not in by_agent_scenario:
                by_agent_scenario[agent] = {}
            if scenario not in by_agent_scenario[agent]:
                by_agent_scenario[agent][scenario] = {}
            
            metric = result["metric_name"]
            score = result["score"]
            by_agent_scenario[agent][scenario][metric] = score
        
        # Print results by agent and scenario
        for agent, scenarios in by_agent_scenario.items():
            logger.info(f"{agent}:")
            for scenario, metrics in scenarios.items():
                logger.info(f"  Scenario: {scenario}")
                for metric, score in metrics.items():
                    logger.info(f"    - {metric}: {score:.2f}")
                logger.info("")
        
        logger.info("=" * 50)


# =============================================================================
# Test Data Loaders
# =============================================================================

def load_jsonl_dataset(path: Path) -> List[Dict[str, Any]]:
    """
    Load a JSONL dataset file.
    
    Args:
        path: Path to the JSONL file
        
    Returns:
        List of parsed JSON objects
    """
    data = []
    
    if not path.exists():
        logger.warning(f"Dataset file not found: {path}")
        return data
    
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    data.append(json.loads(line))
                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse line: {e}")
    
    logger.info(f"Loaded {len(data)} samples from {path.name}")
    return data


@pytest.fixture
def load_design_queries(design_queries_dataset) -> List[Dict[str, Any]]:
    """[DEPRECATED] Load legacy design queries for testing."""
    logger.warning("load_design_queries is deprecated. Use load_design_requests instead.")
    return load_jsonl_dataset(design_queries_dataset)


@pytest.fixture
def load_asr_queries(asr_queries_dataset) -> List[Dict[str, Any]]:
    """[DEPRECATED] Load legacy ASR queries for testing."""
    logger.warning("load_asr_queries is deprecated. Use load_asr_requests instead.")
    return load_jsonl_dataset(asr_queries_dataset)


@pytest.fixture
def load_orchestrator_queries(orchestrator_queries_dataset) -> List[Dict[str, Any]]:
    """[DEPRECATED] Load legacy orchestrator queries for testing."""
    logger.warning("load_orchestrator_queries is deprecated. Use endpoint-specific loaders instead.")
    return load_jsonl_dataset(orchestrator_queries_dataset)


# =============================================================================
# New Request-Based Dataset Loaders
# =============================================================================

@pytest.fixture
def load_design_requests(design_requests_dataset) -> List[Dict[str, Any]]:
    """
    Load design endpoint request parameters for testing.
    
    Each request contains the full API parameters for /generateDesign endpoint:
    - app_id: Application identifier
    - storage_account_name: Azure storage account
    - user_object_id: Azure AD user GUID
    - resource_group_name: Optional resource group
    - test_scenario: Description of the test scenario
    - expected_status: Expected API response status
    
    Returns:
        List of request parameter dictionaries.
    """
    return load_jsonl_dataset(design_requests_dataset)


@pytest.fixture
def load_asr_requests(asr_requests_dataset) -> List[Dict[str, Any]]:
    """
    Load assessment report request parameters for testing.
    
    Each request contains the full API parameters for /generateAssessmentReport endpoint.
    
    Returns:
        List of request parameter dictionaries.
    """
    return load_jsonl_dataset(asr_requests_dataset)


@pytest.fixture
def load_architecture_analysis_requests(architecture_analysis_requests_dataset) -> List[Dict[str, Any]]:
    """
    Load architecture analysis request parameters for testing.
    
    Each request contains the full API parameters for /analyzeArchitecture endpoint,
    including design_doc_url for the design document to analyze.
    
    Returns:
        List of request parameter dictionaries.
    """
    return load_jsonl_dataset(architecture_analysis_requests_dataset)


@pytest.fixture
def load_code_analysis_requests(code_analysis_requests_dataset) -> List[Dict[str, Any]]:
    """
    Load code analysis request parameters for testing.
    
    Each request contains the full API parameters for /analyzeCode endpoint,
    including repo_url and analysis options.
    
    Returns:
        List of request parameter dictionaries.
    """
    return load_jsonl_dataset(code_analysis_requests_dataset)


@pytest.fixture
def load_kubernetes_discovery_requests(kubernetes_discovery_requests_dataset) -> List[Dict[str, Any]]:
    """
    Load Kubernetes discovery request parameters for testing.
    
    Each request contains the full API parameters for /kubernetesDiscovery endpoint.
    
    Returns:
        List of request parameter dictionaries.
    """
    return load_jsonl_dataset(kubernetes_discovery_requests_dataset)


# =============================================================================
# Request Builder Utility
# =============================================================================

class APIRequestBuilder:
    """
    Utility class to build API request payloads from dataset entries.
    
    Transforms dataset entries into properly formatted API request bodies,
    handling optional fields and validation.
    """
    
    # Fields that are API parameters (not test metadata)
    API_FIELDS = {
        "app_id", "storage_account_name", "user_object_id", "group_object_id",
        "resource_group_name", "azure_region", "design_doc_url", "repo_url",
        "perform_security_scan", "analysis_options"
    }
    
    # Metadata fields (not sent to API)
    METADATA_FIELDS = {"test_scenario", "expected_status", "metadata"}
    
    @classmethod
    def build_request_payload(cls, dataset_entry: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build an API request payload from a dataset entry.
        
        Args:
            dataset_entry: Dictionary from dataset JSONL file
            
        Returns:
            Dictionary containing only API request parameters
        """
        payload = {}
        for key, value in dataset_entry.items():
            if key in cls.API_FIELDS and value is not None:
                payload[key] = value
        return payload
    
    @classmethod
    def extract_metadata(cls, dataset_entry: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract test metadata from a dataset entry.
        
        Args:
            dataset_entry: Dictionary from dataset JSONL file
            
        Returns:
            Dictionary containing test metadata (scenario, expected status, etc.)
        """
        metadata = {}
        for key in cls.METADATA_FIELDS:
            if key in dataset_entry:
                metadata[key] = dataset_entry[key]
        return metadata


@pytest.fixture
def api_request_builder() -> APIRequestBuilder:
    """Provide APIRequestBuilder utility."""
    return APIRequestBuilder()
