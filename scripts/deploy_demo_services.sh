#!/usr/bin/env bash
# =============================================================================
# Nexus-Zero — Deploy Chaos Demo Services to Google Cloud Run
# =============================================================================
# Usage:
#   chmod +x scripts/deploy_demo_services.sh
#   ./scripts/deploy_demo_services.sh                 # deploy all 3 services
#   ./scripts/deploy_demo_services.sh order            # deploy only order-api
#   ./scripts/deploy_demo_services.sh payment order    # deploy specific services
#
# These are intentionally buggy Flask microservices with controllable chaos
# injection endpoints. They produce REAL errors in GCP Cloud Logging that
# Sentinel Agent detects and investigates.
# =============================================================================

set -euo pipefail

# ---- Configuration ----------------------------------------------------------
PROJECT_ID="${GCP_PROJECT_ID:-nexus-zero-sre}"
REGION="${GCP_REGION:-us-central1}"

# Cloud Run settings — small and cheap, realistic failure behavior
MEMORY="256Mi"
CPU="1"
MIN_INSTANCES="0"          # scale-to-zero when idle
MAX_INSTANCES="2"          # prevent runaway costs
CONCURRENCY="10"           # low concurrency = realistic failures under load
TIMEOUT="120"

ALL_SERVICES=("order" "payment" "notification")

# ---- Helpers ----------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*"; }

# ---- Validation -------------------------------------------------------------
info "Active GCP project: $(gcloud config get-value project 2>/dev/null)"
gcloud config set project "$PROJECT_ID" --quiet

# Enable required APIs
info "Enabling required GCP APIs..."
gcloud services enable \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    logging.googleapis.com \
    --quiet

# ---- Determine which services to deploy ------------------------------------
if [[ $# -gt 0 ]]; then
    SERVICES_TO_DEPLOY=("$@")
else
    SERVICES_TO_DEPLOY=("${ALL_SERVICES[@]}")
fi

info "Services to deploy: ${SERVICES_TO_DEPLOY[*]}"

# ---- Build & Deploy ---------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEMO_DIR="$REPO_ROOT/demo-services"

deploy_service() {
    local svc_name="$1"
    local cloud_run_name="nexus-${svc_name}-api"
    local svc_dir="${DEMO_DIR}/${svc_name}-api"

    if [[ ! -d "$svc_dir" ]]; then
        err "Service directory not found: $svc_dir"
        return 1
    fi

    info "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    info "Deploying: ${cloud_run_name}"
    info "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    local image="gcr.io/${PROJECT_ID}/${cloud_run_name}"

    info "Building Docker image: ${image}"
    gcloud builds submit "$svc_dir" \
        --tag "$image" \
        --quiet

    # Set cross-service URLs so they can call each other
    local order_url="https://nexus-order-api-$(gcloud run services describe nexus-order-api --region "$REGION" --format 'value(status.url)' 2>/dev/null | sed 's|https://nexus-order-api-||' || echo 'TBD')"

    info "Deploying to Cloud Run: ${cloud_run_name}"
    gcloud run deploy "$cloud_run_name" \
        --image "$image" \
        --region "$REGION" \
        --platform managed \
        --allow-unauthenticated \
        --memory "$MEMORY" \
        --cpu "$CPU" \
        --min-instances "$MIN_INSTANCES" \
        --max-instances "$MAX_INSTANCES" \
        --concurrency "$CONCURRENCY" \
        --timeout "$TIMEOUT" \
        --port 8080 \
        --quiet

    # Get the deployed URL
    local url
    url=$(gcloud run services describe "$cloud_run_name" \
        --region "$REGION" \
        --format 'value(status.url)' 2>/dev/null)

    ok "${cloud_run_name} deployed → ${url}"
    echo ""
    echo "  Endpoints:"
    echo "    Health:  curl ${url}/health"
    echo "    Chaos:   curl -X POST ${url}/chaos/enable -H 'Content-Type: application/json' -d '{\"mode\":\"...\"}"
    echo "    Disable: curl -X POST ${url}/chaos/disable"
    echo ""
}

# ---- Main loop --------------------------------------------------------------
FAILED=()
SUCCEEDED=()
URLS=()

for svc in "${SERVICES_TO_DEPLOY[@]}"; do
    if deploy_service "$svc"; then
        SUCCEEDED+=("$svc")
        url=$(gcloud run services describe "nexus-${svc}-api" \
            --region "$REGION" \
            --format 'value(status.url)' 2>/dev/null || echo "unknown")
        URLS+=("  ${svc}-api: ${url}")
    else
        FAILED+=("$svc")
    fi
done

# ---- Summary ----------------------------------------------------------------
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  DEMO SERVICES DEPLOYMENT SUMMARY"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [[ ${#SUCCEEDED[@]} -gt 0 ]]; then
    ok "Deployed: ${SUCCEEDED[*]}"
    echo ""
    echo "  Service URLs:"
    for u in "${URLS[@]}"; do
        echo "    $u"
    done
fi
if [[ ${#FAILED[@]} -gt 0 ]]; then
    err "Failed: ${FAILED[*]}"
fi

echo ""
echo "  Chaos Modes Available:"
echo "    order-api:        db_pool_exhaustion, cascading_timeout"
echo "    payment-api:      memory_leak, gateway_timeout"
echo "    notification-api: cascade_failure, rate_limit"
echo ""
echo "  Quick Test:"
echo "    ./scripts/inject_chaos.sh order db_pool_exhaustion"
echo ""

if [[ ${#FAILED[@]} -gt 0 ]]; then
    exit 1
fi
