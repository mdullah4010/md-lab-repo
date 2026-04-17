# Use Python 3.11 slim image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements first (for better caching)
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY *.py ./
COPY config.py ./

# Set environment variables
ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=8080
ENV MCP_DEBUG=false
ENV MCP_RELOAD=false
ENV CORS_ORIGINS=*
ENV LOG_LEVEL=INFO

# Expose port
EXPOSE 8080

# Run the simple test application
CMD ["python", "startup.py"]