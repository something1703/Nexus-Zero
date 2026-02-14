FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy common utilities first (for Docker layer caching)
# Build context is mcp-agents/, so common/ is at ./common/
COPY common/ /app/common/

# Copy agent code
# Build context is mcp-agents/, so executor-agent/ is at ./executor-agent/
COPY executor-agent/requirements.txt /app/requirements.txt
COPY executor-agent/main.py /app/main.py

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Cloud Run expects PORT env var
ENV PORT=8080
EXPOSE 8080

CMD ["python", "main.py"]
