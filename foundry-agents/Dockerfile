# Use Python 3.11 slim image for smaller size and better performance
FROM python:3.11-slim

# Build arguments for version tracking
ARG BUILD_NUMBER=unknown
ARG GIT_COMMIT=unknown
ARG DEPLOYMENT_TIME=unknown

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    BUILD_NUMBER=${BUILD_NUMBER} \
    GIT_COMMIT=${GIT_COMMIT} \
    DEPLOYMENT_TIME=${DEPLOYMENT_TIME} \
    DEBIAN_FRONTEND=noninteractive \
    DEBCONF_NONINTERACTIVE_SEEN=true

# Set working directory
WORKDIR /app

# Install system dependencies and clean up in a single layer to save space
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    curl \
    && apt-get clean && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Copy requirements file first for better Docker layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY agents/ ./agents/

# Create necessary directories and set permissions
RUN mkdir -p /app/logs && \
    chmod -R 755 /app && \
    adduser --disabled-password --gecos '' --uid 1000 appuser && \
    chown -R appuser:appuser /app

# Switch to non-root user for security
USER appuser

# Expose port (Azure Container Apps will handle port mapping)
EXPOSE 8000

# Health check endpoint
HEALTHCHECK --interval=30s --timeout=30s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Start the FastAPI application using uvicorn
CMD ["uvicorn", "agents.api_main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]