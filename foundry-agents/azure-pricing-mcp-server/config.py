"""
Azure Pricing MCP Server Configuration.

This file can be overridden via environment variables with MCP_ prefix
"""
from typing import List, Optional, Union
from pydantic import Field, field_validator, HttpUrl, validator
from pydantic_settings import BaseSettings, SettingsConfigDict
import os

# Default values
DEFAULT_HOST = '0.0.0.0'
DEFAULT_PORT = 8080
DEFAULT_LOG_LEVEL = 'INFO'

class Settings(BaseSettings):
    # Server configuration
    MCP_HOST: str = Field(
        default=DEFAULT_HOST,
        description='IP address to listen on (e.g., 0.0.0.0 for all interfaces)'
    )
    
    MCP_PORT: int = Field(
        default_factory=lambda: int(os.environ.get('PORT', DEFAULT_PORT)),
        description='Server port',
        gt=0,
        lt=65536
    )
    
    MCP_DEBUG: bool = Field(
        default=True,
        description='Enable debug mode (do not use in production)'
    )
    
    MCP_RELOAD: bool = Field(
        default=False,
        description='Enable automatic reload in development'
    )
    
    # CORS configuration
    CORS_ORIGINS: Union[str, List[str]] = Field(
        default='*',
        description='Allowed origins for CORS (comma-separated or list)'
    )
    
    # Logging configuration
    LOG_LEVEL: str = Field(
        default=DEFAULT_LOG_LEVEL,
        description='Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)'
    )
    
    # Azure Retail Prices API configuration
    AZURE_RETAIL_PRICES_URL: str = Field(
        default='https://prices.azure.com/api/retail/prices',
        description='Azure pricing API base URL'
    )
    
    AZURE_API_VERSION: str = Field(
        default='2023-01-01-preview',
        description='Azure pricing API version'
    )
    
    # Calculation configuration
    HOURS_IN_MONTH: int = Field(
        default=730,  # 24 hours * 365 days / 12 months ≈ 730
        description='Hours in a month (approx. 24/7)',
        gt=0
    )
    
    # Alternatives configuration
    MAX_ALTERNATIVES_TO_SHOW: int = Field(
        default=3,
        description='Maximum number of alternatives to show',
        gt=0
    )
    
    # Price configuration
    PRICE_TYPE: str = Field(
        default='Consumption',
        description='Price type (e.g., Consumption)'
    )
    
    # Model configuration
    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        env_prefix='MCP_',
        case_sensitive=False,
        extra='ignore',
        validate_default=True,
        env_nested_delimiter='__'
    )
    
    # Validadores
    @field_validator('CORS_ORIGINS', mode='before')
    def parse_cors_origins(cls, v):
        if not v:
            return ['*']
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(',')]
        if isinstance(v, (list, set)):
            return list(v)
        return str(v).split(',')
    
    @field_validator('LOG_LEVEL')
    def validate_log_level(cls, v):
        if not v:
            return DEFAULT_LOG_LEVEL
        v = v.upper()
        valid_levels = {'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'}
        if v not in valid_levels:
            raise ValueError(f'Nivel de log inválido: {v}. Debe ser uno de: {", ".join(valid_levels)}')
        return v
    
    @field_validator('AZURE_RETAIL_PRICES_URL')
    def validate_azure_url(cls, v):
        if not v:
            v = 'https://prices.azure.com/api/retail/prices'
        v = str(v).strip()
        if not v.startswith(('http://', 'https://')):
            v = f'https://{v}'
        return v.rstrip('/')
    
    @field_validator('AZURE_API_VERSION')
    def validate_api_version(cls, v):
        if not v:
            return '2023-01-01-preview'
        v = str(v).strip()
        # Validate version format (e.g., 2023-01-01-preview)
        try:
            parts = v.split('-')
            if not all(part.isdigit() for part in parts[0].split('.')):
                raise ValueError()
        except (ValueError, AttributeError):
            # If format is invalid, use default value
            return '2023-01-01-preview'
        return v
    
    @field_validator('PRICE_TYPE')
    def validate_price_type(cls, v):
        if not v:
            return 'Consumption'
        return str(v).strip()
    
    @field_validator('MCP_DEBUG', 'MCP_RELOAD', mode='before')
    def validate_bool(cls, v):
        if isinstance(v, str):
            v = v.lower()
            if v in ('true', '1', 'yes'):
                return True
            if v in ('false', '0', 'no', ''):
                return False
        return bool(v)

# Load configuration
try:
    settings = Settings()
except Exception as e:
    print(f"Error loading configuration: {e}")
    raise

# Logging configuration
import logging
from logging.config import dictConfig

# Create logging configuration after settings are loaded
logging_config = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'default': {
            'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            'datefmt': '%Y-%m-%d %H:%M:%S'
        },
        'simple': {
            'format': '%(levelname)s: %(message)s'
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'default' if settings.MCP_DEBUG else 'simple',
            'stream': 'ext://sys.stdout',
        },
    },
    'loggers': {
        '': {  # root logger
            'handlers': ['console'],
            'level': settings.LOG_LEVEL,
            'propagate': True
        },
        'azure': {
            'level': 'WARNING',  # Reduce noise from Azure libraries
            'propagate': False
        },
        'urllib3': {
            'level': 'WARNING',  # Reduce noise from HTTP requests
            'propagate': False
        },
    }
}

# Apply logging configuration
dictConfig(logging_config)

# Configure logger for this module
logger = logging.getLogger(__name__)
logger.debug("Configuration loaded successfully")
