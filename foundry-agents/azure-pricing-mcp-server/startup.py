"""
Startup script for Azure Container Apps deployment.

This script serves as the entry point for the Azure MCP Pricing server
when deployed to Azure Container Apps.
"""

import os
import sys
import logging
import uvicorn


try:
    import azure_pricing_mcp_server
    get_application = azure_pricing_mcp_server.get_application
except Exception as e:
    logger = logging.getLogger(__name__)
    logger.error(f"❌ Failed to import azure_pricing_mcp_server: {e}")
    raise

# Import the same logging configuration as the main server
try:
    # Import directly from current directory (copied by Dockerfile)
    from logging_config import get_logger
    logger = get_logger(__name__)
    logger.info("🚀 Azure Pricing MCP Server startup.py using custom logging config")
except ImportError as e:
    # Fallback logging setup
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    logger = logging.getLogger(__name__)
    logger.warning(f"⚠️  Startup using fallback logging due to import error: {e}")
except Exception as e:
    # Fallback logging setup
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    logger = logging.getLogger(__name__)
    logger.warning(f"⚠️  Startup using fallback logging due to error: {e}")

def main():
    """Main entry point for Azure Container Apps."""
    logger.info("🚀 Starting Azure MCP Pricing Server from startup.py...")
    
    # Azure Container Apps environment variables
    port = int(os.environ.get('PORT', 8080))
    host = os.environ.get('HOST', '0.0.0.0')
    
    logger.info(f"🌐 Server will listen on {host}:{port}")
    
    # Get the application instance
    logger.info("🔧 About to call get_application()")
    
    try:
        app = get_application()
        logger.info("✅ get_application() succeeded")
    except Exception as e:
        logger.error(f"❌ get_application() failed: {e}")
        raise
    
    # Run uvicorn server
    uvicorn.run(app, host=host, port=port, log_level="info")

if __name__ == "__main__":
    main()