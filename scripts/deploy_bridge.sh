#!/bin/bash
set -e

PROJECT_ID="nexus-zero-sre"
REGION="us-central1"
SERVICE_NAME="nexus-archestra-bridge"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  DEPLOYING ARCHESTRA BRIDGE"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Get active project
ACTIVE_PROJECT=$(gcloud config get-value project 2>/dev/null || echo "")
echo "[INFO]  Active GCP project: $ACTIVE_PROJECT"

if [ "$ACTIVE_PROJECT" != "$PROJECT_ID" ]; then
    echo "[WARN]  Setting project to $PROJECT_ID"
    gcloud config set project $PROJECT_ID
fi

# Build Docker image with Cloud Build
echo ""
echo "[INFO]  Building Docker image with Cloud Build..."
gcloud builds submit \
    --config /dev/stdin \
    --timeout=600s \
    . << 'EOF'
steps:
  - name: 'gcr.io/cloud-builders/docker'
    args:
      - 'build'
      - '-t'
      - 'gcr.io/$PROJECT_ID/nexus-archestra-bridge:latest'
      - '-f'
      - 'mcp-agents/archestra-bridge/Dockerfile'
      - '.'
images:
  - 'gcr.io/$PROJECT_ID/nexus-archestra-bridge:latest'
EOF

echo ""
echo "[INFO]  Deploying to Cloud Run..."

# Deploy to Cloud Run
gcloud run deploy $SERVICE_NAME \
    --image gcr.io/$PROJECT_ID/nexus-archestra-bridge:latest \
    --region $REGION \
    --platform managed \
    --allow-unauthenticated \
    --memory 512Mi \
    --cpu 1 \
    --timeout 600 \
    --max-instances 3 \
    --min-instances 0

# Get the service URL
SERVICE_URL=$(gcloud run services describe $SERVICE_NAME \
    --region $REGION \
    --format 'value(status.url)')

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  DEPLOYMENT COMPLETE"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "[OK]    Bridge deployed → $SERVICE_URL"
echo ""
echo "  MCP endpoint:     $SERVICE_URL/mcp"
echo "  Health check:     curl $SERVICE_URL"
echo "  List tools:       curl $SERVICE_URL/tools/sentinel"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ARCHESTRA CONFIGURATION"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  In Archestra MCP Registry → Remote:"
echo "  Name:       Nexus-Zero Bridge"
echo "  URL:        $SERVICE_URL/mcp"
echo "  Auth:       No authorization"
echo ""
