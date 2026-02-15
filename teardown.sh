#!/usr/bin/env bash
# =============================================================================
# Nexus-Zero — Tear Down All Resources from Your GCP Project
# =============================================================================
# Usage:
#   ./teardown.sh                          # Interactive (prompts)
#   ./teardown.sh --project my-gcp-project # Direct
#   ./teardown.sh --project my-project --yes  # Skip confirmation
#
# Removes:
#   - All Cloud Run services (agents, bridge, dashboard, demo services)
#   - Cloud SQL instance and database
#   - Secret Manager secrets
#   - Container images from gcr.io
# =============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}  ✅${NC}  $*"; }
warn()  { echo -e "${YELLOW}  ⚠️${NC}   $*"; }

# ---- Parse Args -------------------------------------------------------------
PROJECT_ID=""
AUTO_YES=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --project) PROJECT_ID="$2"; shift 2 ;;
        --project=*) PROJECT_ID="${1#*=}"; shift ;;
        --yes|-y) AUTO_YES=true; shift ;;
        *) shift ;;
    esac
done

if [[ -z "$PROJECT_ID" ]]; then
    PROJECT_ID=$(gcloud config get-value project 2>/dev/null || echo "")
    if [[ -z "$PROJECT_ID" ]]; then
        read -rp "Enter GCP Project ID to tear down: " PROJECT_ID
    fi
fi

REGION="us-central1"

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║                                                      ║${NC}"
echo -e "${BOLD}║     ⚠️  NEXUS-ZERO TEARDOWN — DESTRUCTIVE ⚠️         ║${NC}"
echo -e "${BOLD}║                                                      ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Project: ${RED}${PROJECT_ID}${NC}"
echo -e "  Region:  ${RED}${REGION}${NC}"
echo ""
echo -e "  This will ${RED}PERMANENTLY DELETE${NC}:"
echo -e "    • 8 Cloud Run services"
echo -e "    • Cloud SQL instance (nexus-zero-db) and ALL data"
echo -e "    • Secret Manager secrets"
echo -e "    • Container images"
echo ""

if [[ "$AUTO_YES" != true ]]; then
    read -rp "  Type 'DELETE' to confirm: " confirm
    if [[ "$confirm" != "DELETE" ]]; then
        echo "Aborted."
        exit 0
    fi
fi

gcloud config set project "$PROJECT_ID" --quiet 2>/dev/null

# ---- Delete Cloud Run Services ----------------------------------------------
echo ""
info "Deleting Cloud Run services..."

SERVICES=(
    "nexus-sentinel-agent"
    "nexus-detective-agent"
    "nexus-historian-agent"
    "nexus-mediator-agent"
    "nexus-executor-agent"
    "nexus-archestra-bridge"
    "nexus-order-api"
    "nexus-payment-api"
    "nexus-notification-api"
    "nexus-dashboard"
)

for svc in "${SERVICES[@]}"; do
    if gcloud run services describe "$svc" --region="$REGION" --project="$PROJECT_ID" &>/dev/null; then
        gcloud run services delete "$svc" \
            --region="$REGION" --project="$PROJECT_ID" --quiet 2>/dev/null && \
            ok "Deleted: $svc" || warn "Could not delete: $svc"
    else
        info "Not found (skipping): $svc"
    fi
done

# ---- Delete Cloud SQL Instance -----------------------------------------------
echo ""
info "Deleting Cloud SQL instance..."

INSTANCE_NAME="nexus-zero-db"
if gcloud sql instances describe "$INSTANCE_NAME" --project="$PROJECT_ID" &>/dev/null; then
    gcloud sql instances delete "$INSTANCE_NAME" \
        --project="$PROJECT_ID" --quiet 2>/dev/null && \
        ok "Deleted Cloud SQL instance: $INSTANCE_NAME" || \
        warn "Could not delete Cloud SQL instance (may still be deleting)"
else
    info "Cloud SQL instance not found (skipping)"
fi

# ---- Delete Secrets ----------------------------------------------------------
echo ""
info "Deleting Secret Manager secrets..."

SECRETS=("db-password" "gemini-api-key" "github-token")
for secret in "${SECRETS[@]}"; do
    if gcloud secrets describe "$secret" --project="$PROJECT_ID" &>/dev/null; then
        gcloud secrets delete "$secret" \
            --project="$PROJECT_ID" --quiet 2>/dev/null && \
            ok "Deleted secret: $secret" || warn "Could not delete: $secret"
    else
        info "Secret not found (skipping): $secret"
    fi
done

# ---- Delete Container Images -------------------------------------------------
echo ""
info "Deleting container images from gcr.io..."

IMAGES=(
    "nexus-sentinel-agent"
    "nexus-detective-agent"
    "nexus-historian-agent"
    "nexus-mediator-agent"
    "nexus-executor-agent"
    "nexus-archestra-bridge"
    "nexus-order-api"
    "nexus-payment-api"
    "nexus-notification-api"
)

for img in "${IMAGES[@]}"; do
    gcloud container images delete "gcr.io/${PROJECT_ID}/${img}" \
        --force-delete-tags --quiet 2>/dev/null && \
        ok "Deleted image: $img" || true
done

# ---- Done --------------------------------------------------------------------
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║          ✅ TEARDOWN COMPLETE                        ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  All Nexus-Zero resources have been removed from ${CYAN}${PROJECT_ID}${NC}."
echo ""
echo -e "  ${YELLOW}Note:${NC} Cloud SQL deletion takes a few minutes to complete."
echo -e "  The instance name '${INSTANCE_NAME}' will be reserved for ~1 week."
echo -e "  If you need to redeploy immediately, use a different instance name."
echo ""
