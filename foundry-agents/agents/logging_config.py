# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

import logging
import sys
from typing import Optional
import os
from contextvars import ContextVar

# Context variable to store the current AI Foundry thread ID
_ai_thread_id: ContextVar[Optional[str]] = ContextVar('ai_thread_id', default=None)

# Global flag to track if logging has been configured
_logging_configured = False

def set_ai_thread_id(thread_id: Optional[str]) -> None:
    """Set the current AI Foundry thread ID for logging context."""
    _ai_thread_id.set(thread_id)

def get_ai_thread_id() -> Optional[str]:
    """Get the current AI Foundry thread ID from logging context."""
    return _ai_thread_id.get()

def _configure_global_logging(level: int) -> None:
    """Configure global logging settings and suppress Azure SDK verbose logging."""
    global _logging_configured
    
    if _logging_configured:
        return  # Already configured
    
    # Set root logger level but don't add handlers here to avoid duplication
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    # Suppress verbose Azure SDK logging
    logging.getLogger('azure.core.pipeline.policies.http_logging_policy').setLevel(logging.WARNING)
    logging.getLogger('azure.identity').setLevel(logging.WARNING)
    logging.getLogger('azure.ai.projects').setLevel(logging.WARNING)
    logging.getLogger('azure.ai.agents').setLevel(logging.WARNING)
    logging.getLogger('azure.storage').setLevel(logging.WARNING)
    logging.getLogger('azure.data.tables').setLevel(logging.WARNING)
    logging.getLogger('azure.monitor.opentelemetry.exporter.export._base').setLevel(logging.WARNING)
    logging.getLogger('azure.monitor.opentelemetry').setLevel(logging.WARNING)
    logging.getLogger('azure.monitor.opentelemetry._configure').setLevel(logging.ERROR)  # Suppress psycopg2 instrumentation warnings
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)
    logging.getLogger('uvicorn.access').setLevel(logging.WARNING)  # Suppress uvicorn access logs
    logging.getLogger('fastapi').setLevel(logging.INFO)
    
    # Suppress verbose Semantic Kernel debug logging
    logging.getLogger('semantic_kernel').setLevel(logging.INFO)
    
    _logging_configured = True

def configure_logging(log_file_name: Optional[str] = None, logger_name: str = "azureaiapp") -> logging.Logger:
    """
    Configure and return a logger that outputs to BOTH stdout AND file simultaneously.
    All log entries will appear on the console AND be written to the log file.
    Includes Azure SDK logging suppression.

    :param log_file_name: The path to the log file. Logs will be written to this file AND stdout.
    :type log_file_name: Optional[str]
    :param logger_name: The name of the logger to configure.
    :type logger_name: str
    :return: The configured logger instance that logs to both console and file.
    :rtype: logging.Logger
    """
    logger = logging.getLogger(logger_name)
    
    # Clear existing handlers to prevent duplication
    logger.handlers.clear()
    
    # Disable propagation to avoid duplicate logging with root logger
    logger.propagate = False
    
    # Determine log level from LOG_LEVEL environment variable
    log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    if log_level in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        level = getattr(logging, log_level)
    else:
        # Default to INFO if an invalid log level is provided
        level = logging.INFO
    
    logger.setLevel(level)

    # Configure global logging settings and suppress verbose Azure SDK logging
    _configure_global_logging(level)

    # Custom formatter that handles Unicode and includes AI Foundry thread ID
    class AIThreadFormatter(logging.Formatter):
        def format(self, record):
            try:
                # Get AI Foundry thread ID from context
                ai_thread_id = get_ai_thread_id()
                if ai_thread_id:
                    record.ai_thread = f"[{ai_thread_id}]"
                else:
                    record.ai_thread = ""
                return super().format(record)
            except UnicodeEncodeError:
                # Replace problematic characters with safe alternatives
                record.msg = str(record.msg).encode('ascii', 'replace').decode('ascii')
                return super().format(record)

    # ALWAYS add console handler (stdout) - this ensures all logs appear on console
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)  # Use the same level as the logger
    console_formatter = AIThreadFormatter("%(asctime)s %(ai_thread)s[%(levelname)s] %(name)s: %(message)s")
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    # ALWAYS add file handler - this ensures all logs are written to file
    log_file = log_file_name if log_file_name else os.getenv("LOG_FILE", "insights-agent.log")
    file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
    file_handler.setLevel(level)  # Use the same level as the logger
    file_formatter = AIThreadFormatter("%(asctime)s %(ai_thread)s[%(levelname)s] %(name)s: %(message)s")
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    return logger

def get_logger(module_name: str) -> logging.Logger:
    """
    Get a configured logger for a module that outputs to BOTH stdout AND file.
    All log entries will appear on the console AND be written to the log file.
    Uses the shared log file from LOG_FILE environment variable.
    
    :param module_name: The name of the module (typically __name__)
    :type module_name: str
    :return: Configured logger instance that logs to both console and file
    :rtype: logging.Logger
    """
    # Read log file name from environment variable, fallback to insights-agent.log
    log_file = os.getenv("LOG_FILE", "insights-agent.log")
    return configure_logging(log_file, module_name)