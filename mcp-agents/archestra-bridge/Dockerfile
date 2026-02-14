FROM python:3.11-slim

WORKDIR /app

# Copy requirements and install
COPY mcp-agents/archestra-bridge/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy bridge code
COPY mcp-agents/archestra-bridge/main.py /app/main.py

# Expose port
EXPOSE 8080

# Run the bridge
CMD ["python", "main.py"]
