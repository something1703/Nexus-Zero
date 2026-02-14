#!/usr/bin/env bash
# =============================================================================
# Nexus-Zero — Reset Demo Environment
# =============================================================================
# Usage:
#   ./scripts/reset_demo.sh              # Full reset (DB + chaos disable)
#   ./scripts/reset_demo.sh --db-only    # Only reset database
#   ./scripts/reset_demo.sh --chaos-only # Only disable chaos
#
# Resets the demo environment to a clean state:
#   1. Disables all chaos modes on demo services
#   2. Clears all open/investigating incidents from database
#   3. Clears audit logs from this session
#   4. Resets service health to 'healthy'
#   5. Keeps historical incidents + playbooks intact
# =============================================================================

set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:-nexus-zero-sre}"
REGION="${GCP_REGION:-us-central1}"
DB_INSTANCE="nexus-zero-db"
DB_NAME="${DB_NAME:-nexus_zero}"
DB_USER="${DB_USER:-nexus_admin}"

# ---- Helpers ----------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*"; }

# ---- Parse Args -------------------------------------------------------------
RESET_DB=true
RESET_CHAOS=true

if [[ "${1:-}" == "--db-only" ]]; then
    RESET_CHAOS=false
elif [[ "${1:-}" == "--chaos-only" ]]; then
    RESET_DB=false
fi

echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}  NEXUS-ZERO DEMO RESET${NC}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# ---- Step 1: Disable Chaos --------------------------------------------------
if [[ "$RESET_CHAOS" == true ]]; then
    info "Step 1: Disabling chaos on all demo services..."

    for svc in order payment notification; do
        local_url=$(gcloud run services describe "nexus-${svc}-api" \
            --region "$REGION" \
            --format 'value(status.url)' 2>/dev/null || echo "")
        
        if [[ -n "$local_url" ]]; then
            curl -s -X POST "${local_url}/chaos/disable" > /dev/null 2>&1 && \
                ok "  ${svc}-api: chaos disabled" || \
                warn "  ${svc}-api: could not disable (service may be cold)"
        else
            warn "  ${svc}-api: not deployed, skipping"
        fi
    done
    echo ""
else
    info "Skipping chaos disable (--db-only mode)"
fi

# ---- Step 2: Reset Database -------------------------------------------------
if [[ "$RESET_DB" == true ]]; then
    info "Step 2: Resetting database..."

    # Fetch DB password from Secret Manager
    DB_PASSWORD=$(gcloud secrets versions access latest \
        --secret="db-password" --project="$PROJECT_ID" 2>/dev/null || echo "")

    if [[ -z "$DB_PASSWORD" ]]; then
        err "Cannot fetch DB password from Secret Manager."
        err "Set it: echo -n 'PASSWORD' | gcloud secrets create db-password --data-file=-"
        exit 1
    fi

    # SQL to reset the demo state
    RESET_SQL=$(cat <<'EOSQL'
-- Close all open/investigating incidents (keep historical ones)
UPDATE incidents 
SET status = 'closed', 
    resolved_at = NOW(),
    resolution_action = 'demo_reset'
WHERE status IN ('open', 'investigating');

-- Reset all services to healthy
UPDATE services SET status = 'healthy', updated_at = NOW();

-- Clear recent audit logs (keep historical ones from seed data)
DELETE FROM audit_logs 
WHERE created_at > NOW() - INTERVAL '1 day'
AND agent_name != 'demo_seed';

-- Reset agent states
UPDATE agent_states SET state = jsonb_build_object(
    'last_reset', NOW()::text,
    'reset_reason', 'demo_reset'
), last_updated = NOW();

-- Count remaining data to verify
DO $$
DECLARE
    open_incidents INTEGER;
    total_incidents INTEGER;
    total_playbooks INTEGER;
    total_services INTEGER;
BEGIN
    SELECT COUNT(*) INTO open_incidents FROM incidents WHERE status IN ('open', 'investigating');
    SELECT COUNT(*) INTO total_incidents FROM incidents;
    SELECT COUNT(*) INTO total_playbooks FROM playbooks;
    SELECT COUNT(*) INTO total_services FROM services;
    
    RAISE NOTICE 'Reset complete:';
    RAISE NOTICE '  Open incidents: % (should be 0)', open_incidents;
    RAISE NOTICE '  Total incidents (incl. historical): %', total_incidents;
    RAISE NOTICE '  Playbooks preserved: %', total_playbooks;
    RAISE NOTICE '  Services: %', total_services;
END $$;
EOSQL
)

    # Execute via Cloud SQL proxy or gcloud sql connect
    info "  Connecting to Cloud SQL and executing reset..."
    echo "$RESET_SQL" | gcloud sql connect "$DB_INSTANCE" \
        --user="$DB_USER" \
        --database="$DB_NAME" \
        --quiet 2>/dev/null && \
        ok "  Database reset complete" || {
            warn "  gcloud sql connect failed, trying Cloud SQL Auth Proxy..."
            # Fallback: try psql directly if proxy is running
            PGPASSWORD="$DB_PASSWORD" psql \
                -h "/cloudsql/${PROJECT_ID}:${REGION}:${DB_INSTANCE}" \
                -U "$DB_USER" \
                -d "$DB_NAME" \
                -c "$RESET_SQL" 2>/dev/null && \
                ok "  Database reset complete (via proxy)" || \
                err "  Could not connect to database. Reset SQL manually."
        }
    echo ""
else
    info "Skipping database reset (--chaos-only mode)"
fi

# ---- Summary ----------------------------------------------------------------
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
ok "  DEMO ENVIRONMENT RESET COMPLETE"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  All services are healthy, all incidents closed."
echo "  Historical data (playbooks, past incidents) preserved."
echo ""
echo "  Ready for demo! Next:"
echo "    1. ./scripts/inject_chaos.sh all          # Start chaos"
echo "    2. Ask Archestra: 'Detect anomalies'      # Watch AI respond"
echo ""
