#!/usr/bin/env bash
# =============================================================================
# Nexus-Zero — Deploy Dashboard to Cloud Run
# =============================================================================
set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:-nexus-zero-sre}"
REGION="${GCP_REGION:-us-central1}"
SERVICE_NAME="nexus-dashboard"
INSTANCE_CONNECTION_NAME="${PROJECT_ID}:${REGION}:nexus-zero-db"

# ---- Required env vars (prompt if missing) ----------------------------------
if [[ -z "${ARCHESTRA_TOKEN:-}" ]]; then
    echo -n "Enter Archestra Bearer Token: "
    read -s ARCHESTRA_TOKEN
    echo ""
fi

if [[ -z "${DB_PASSWORD:-}" ]]; then
    echo -n "Enter DB Password: "
    read -s DB_PASSWORD
    echo ""
fi

# ---- Deploy -----------------------------------------------------------------
echo "🚀 Deploying ${SERVICE_NAME} to Cloud Run..."

gcloud run deploy "${SERVICE_NAME}" \
    --source web \
    --region "${REGION}" \
    --platform managed \
    --allow-unauthenticated \
    --add-cloudsql-instances "${INSTANCE_CONNECTION_NAME}" \
    --set-env-vars "\
GCP_PROJECT_ID=${PROJECT_ID},\
GCP_REGION=${REGION},\
DB_NAME=nexus_zero,\
DB_USER=nexus_admin,\
DB_PASSWORD=${DB_PASSWORD},\
INSTANCE_CONNECTION_NAME=${INSTANCE_CONNECTION_NAME},\
ARCHESTRA_TOKEN=${ARCHESTRA_TOKEN},\
SENTINEL_AGENT_ID=9a77407c-7ab1-481a-8155-8159e4859d4d" \
    --memory 512Mi \
    --timeout 120 \
    --min-instances 0 \
    --max-instances 3 \
    --quiet

DASHBOARD_URL=$(gcloud run services describe "${SERVICE_NAME}" \
    --region "${REGION}" \
    --format 'value(status.url)')

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✅ DASHBOARD DEPLOYED"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  🌐 Dashboard URL: ${DASHBOARD_URL}"
echo ""
echo "  Share this URL with judges. They can:"
echo "    💣 Click 'Break Service' to inject chaos"
echo "    🔍 Click 'Run Sentinel Detection' to trigger agents"
echo "    ✅ Click 'Approve' to execute remediation"
echo "    📊 Watch real-time incident resolution"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
