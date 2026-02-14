FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Copy common utilities first (for layer caching)
COPY common/ /app/common/

# Copy agent code
COPY sentinel-agent/requirements.txt /app/requirements.txt
COPY sentinel-agent/main.py /app/main.py

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose port for SSE transport
EXPOSE 8080

ENV PORT=8080
ENV HOST=0.0.0.0

# Run the MCP server
CMD ["python", "main.py"]
