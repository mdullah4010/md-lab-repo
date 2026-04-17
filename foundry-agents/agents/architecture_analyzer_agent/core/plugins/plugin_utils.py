"""
Common utilities for Semantic Kernel plugins

This module provides shared functionality for all plugins including environment 
variable loading, authentication, and configuration management.
"""

import logging
import os
from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


def load_plugin_environment():
    """
    Load environment variables from .env files in multiple possible locations.
    
    Searches for .env files in the following order:
    1. Current directory
    2. Parent directory  
    3. Two levels up
    4. App directory (relative to this file)
    5. Project root (relative to this file)
    
    Returns:
        bool: True if any .env file was loaded, False otherwise
    """
    try:
        # Get the current file's directory
        current_file_dir = Path(__file__).parent
        
        # Try to load .env from multiple possible locations
        env_paths = [
            ".env",  # Current working directory
            "../.env",  # Parent directory
            "../../.env",  # Two levels up
            current_file_dir.parent / ".env",  # App directory
            current_file_dir.parent.parent / ".env",  # Project root
            current_file_dir.parent.parent.parent / ".env",  # One more level up
            current_file_dir.parent.parent.parent.parent / ".env",  # AI-IntakeandAssessmentv1.0 directory
        ]
        
        env_loaded = False
        for env_path in env_paths:
            try:
                if load_dotenv(env_path):
                    logger.info(f"[SUCCESS] Loaded .env file from: {env_path}")
                    env_loaded = True
                    break
            except Exception as e:
                logger.debug(f"Failed to load .env from {env_path}: {str(e)}")
                continue
        
        if not env_loaded:
            logger.warning("[WARNING] No .env file found, will rely on system environment variables")
        
        return env_loaded
        
    except Exception as e:
        logger.error(f"[ERROR] Error loading environment: {str(e)}")
        return False


def get_ado_credentials():
    """
    Get Azure DevOps authentication credentials.
    
    Returns:
        str or None: PAT token if available, None if not found
    """
    pat_token = os.environ.get("ADO_PAT_TOKEN")
    
    if pat_token:
        logger.info("[SUCCESS] ADO_PAT_TOKEN found and will be used for authentication")
    else:
        logger.info("ADO_PAT_TOKEN not provided, attempting to use DefaultAzureCredential")
    
    return pat_token


def get_azure_openai_config():
    """
    Get Azure OpenAI configuration from environment variables.
    
    Returns:
        dict: Configuration dictionary with endpoint, deployment, and model info
    """
    config = {
        "endpoint": os.environ.get("AZURE_OPENAI_ENDPOINT"),
        "deployment": os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4.1"),
        "foundry_endpoint": os.environ.get("AZURE_EXISTING_AIPROJECT_ENDPOINT"),
        "foundry_model": os.environ.get("AZURE_AI_AGENT_DEPLOYMENT_NAME", "gpt-4o")
    }
    
    # Use foundry endpoint as fallback if OpenAI endpoint not configured
    if not config["endpoint"] and config["foundry_endpoint"]:
        if "openai" in config["foundry_endpoint"].lower():
            config["endpoint"] = config["foundry_endpoint"]
            config["deployment"] = config["foundry_model"]
            logger.info("Using FOUNDRY_PROJECT_ENDPOINT for Azure OpenAI connection")
    
    return config


def get_azure_storage_config():
    """
    Get Azure Storage configuration from environment variables.
    
    Returns:
        dict: Configuration dictionary with storage account info
    """
    return {
        "account_name": os.environ.get("AZURE_STORAGE_ACCOUNT_NAME"),
        "account_key": os.environ.get("AZURE_STORAGE_ACCOUNT_KEY"),
        "connection_string": os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    }


def get_azure_search_config():
    """
    Get Azure AI Search configuration from environment variables.
    Uses managed identity for authentication.
    
    Uses managed identity for authentication (no API key needed).
    
    Returns:
        dict: Configuration dictionary with search service info
    """
    return {
        "service_name": os.environ.get("AZURE_SEARCH_INDEX"),
        "index_name": os.environ.get("SCF_AZURE_SEARCH_INDEX", "scfindex"),
        "endpoint": os.environ.get("AZURE_SEARCH_ENDPOINT")
    }


def validate_required_config(config_dict, required_keys, service_name="service"):
    """
    Validate that required configuration keys are present and not empty.
    
    Args:
        config_dict (dict): Configuration dictionary to validate
        required_keys (list): List of required keys
        service_name (str): Name of the service for error messages
    
    Returns:
        bool: True if all required keys are present, False otherwise
    
    Raises:
        ValueError: If required configuration is missing
    """
    missing_keys = []
    
    for key in required_keys:
        if not config_dict.get(key):
            missing_keys.append(key)
    
    if missing_keys:
        error_msg = f"Missing required {service_name} configuration: {', '.join(missing_keys)}"
        logger.error(f"[ERROR] {error_msg}")
        raise ValueError(error_msg)
    
    logger.info(f"[SUCCESS] {service_name} configuration validated successfully")
    return True


def load_agent_instructions(agent_name: str) -> str:
    """
    Load agent instructions from the agent-instructions directory.
    
    This is a common utility function that can be used across all modules
    (main.py, plugins, tools) to load agent instruction files consistently.
    
    Args:
        agent_name: Name of the agent instruction file without .txt extension
                   (e.g., 'architecture-analyzer-agent', 'foundry_image_analyzer_agent')
    
    Returns:
        str: The agent instructions as a string, or None if file not found or error occurs
    """
    try:
        # Get the Agents directory (4 levels up from plugin_utils.py)
        # Path: Agents/architecture_agent/arch_analyzer/plugins/plugin_utils.py
        agents_dir = Path(__file__).parent.parent.parent.parent
        
        # Candidate locations to look for the agent-instructions directory
        # Looking in Agents/agent-instructions/architecture-agents-instructions/
        candidates = [
            agents_dir / "agent-instructions" / "architecture-agents-instructions",  # Agents/agent-instructions/architecture-agents-instructions/
            agents_dir / "agent-instructions",                                        # Agents/agent-instructions/
            agents_dir.parent / "agent-instructions",                                # Parent of Agents/
            Path.cwd() / "agent-instructions"                                        # current working directory
        ]

        instructions_path = None
        for cand in candidates:
            path = cand / f"{agent_name}.txt"
            if path.exists():
                instructions_path = path
                break

        if instructions_path is None:
            logger.warning(f"[WARNING] Agent instructions file not found in candidates for: {agent_name}.txt")
            return None

        with open(instructions_path, 'r', encoding='utf-8') as f:
            instructions = f.read().strip()

        logger.info(f"[SUCCESS] Loaded agent instructions for {agent_name} from {instructions_path} ({len(instructions)} characters)")
        return instructions

    except Exception as e:
        logger.error(f"[ERROR] Error loading agent instructions for {agent_name}: {str(e)}")
        return None


def load_agent_instructions_with_fallback(agent_name: str, fallback_instructions: str = None) -> str:
    """
    Load agent instructions with a fallback option.
    
    Args:
        agent_name: Name of the agent instruction file without .txt extension
        fallback_instructions: Fallback instructions to use if file loading fails
    
    Returns:
        str: The agent instructions or fallback instructions
    """
    instructions = load_agent_instructions(agent_name)
    
    if instructions is None and fallback_instructions is not None:
        logger.info(f"[FALLBACK] Using fallback instructions for {agent_name}")
        return fallback_instructions
    
    return instructions


# Initialize environment when module is imported
_env_loaded = load_plugin_environment()