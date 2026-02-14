#!/usr/bin/env bash
# =============================================================================
# Nexus-Zero — Chaos Injection Script
# =============================================================================
# Usage:
#   ./scripts/inject_chaos.sh                                # Interactive menu
#   ./scripts/inject_chaos.sh order db_pool_exhaustion       # Direct inject
#   ./scripts/inject_chaos.sh payment memory_leak            # Direct inject
#   ./scripts/inject_chaos.sh notification cascade_failure   # Direct inject
#   ./scripts/inject_chaos.sh all                            # Enable chaos on ALL services
#   ./scripts/inject_chaos.sh stop                           # Disable chaos on ALL services
#
# After enabling chaos, this script sends a burst of requests to trigger
# real errors in GCP Cloud Logging that Sentinel Agent will detect.
# =============================================================================

set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:-nexus-zero-sre}"
REGION="${GCP_REGION:-us-central1}"

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

# ---- Get Service URLs -------------------------------------------------------
get_url() {
    local svc="$1"
    gcloud run services describe "nexus-${svc}-api" \
        --region "$REGION" \
        --format 'value(status.url)' 2>/dev/null || echo ""
}

ORDER_URL=$(get_url "order")
PAYMENT_URL=$(get_url "payment")
NOTIFICATION_URL=$(get_url "notification")

# ---- Functions --------------------------------------------------------------
enable_chaos() {
    local service="$1"
    local mode="$2"
    local url=""
    local endpoint=""

    case "$service" in
        order)        url="$ORDER_URL"; endpoint="/order" ;;
        payment)      url="$PAYMENT_URL"; endpoint="/process" ;;
        notification) url="$NOTIFICATION_URL"; endpoint="/notify" ;;
        *)            err "Unknown service: $service"; return 1 ;;
    esac

    if [[ -z "$url" ]]; then
        err "Service nexus-${service}-api not found. Deploy it first."
        return 1
    fi

    info "Enabling chaos on ${service}-api: mode=${mode}"
    curl -s -X POST "${url}/chaos/enable" \
        -H "Content-Type: application/json" \
        -d "{\"mode\": \"${mode}\", \"failure_rate\": 0.40}" | python3 -m json.tool 2>/dev/null || true

    echo ""
    info "Sending burst of 50 requests to trigger errors..."
    local success=0
    local errors=0
    
    for i in $(seq 1 50); do
        local status
        status=$(curl -s -o /dev/null -w "%{http_code}" -X POST "${url}${endpoint}" \
            -H "Content-Type: application/json" \
            -d '{"customer":"chaos-test","amount":99.99}' \
            --max-time 15 2>/dev/null || echo "000")
        
        if [[ "$status" -ge 200 && "$status" -lt 300 ]]; then
            success=$((success + 1))
            printf "."
        else
            errors=$((errors + 1))
            printf "${RED}x${NC}"
        fi
    done

    echo ""
    echo ""
    ok "Burst complete: ${GREEN}${success} success${NC}, ${RED}${errors} errors${NC}"
    echo ""

    info "Checking chaos status..."
    curl -s "${url}/chaos/status" | python3 -m json.tool 2>/dev/null || true
    echo ""
}

disable_chaos() {
    local service="$1"
    local url=""

    case "$service" in
        order)        url="$ORDER_URL" ;;
        payment)      url="$PAYMENT_URL" ;;
        notification) url="$NOTIFICATION_URL" ;;
        *)            err "Unknown service: $service"; return 1 ;;
    esac

    if [[ -z "$url" ]]; then
        warn "Service nexus-${service}-api not found, skipping."
        return 0
    fi

    info "Disabling chaos on ${service}-api..."
    curl -s -X POST "${url}/chaos/disable" | python3 -m json.tool 2>/dev/null || true
    echo ""
}

disable_all() {
    info "Disabling chaos on ALL services..."
    for svc in order payment notification; do
        disable_chaos "$svc"
    done
    ok "All services returned to normal."
}

enable_all() {
    info "Enabling chaos on ALL services..."
    enable_chaos "order" "db_pool_exhaustion"
    enable_chaos "payment" "memory_leak"
    enable_chaos "notification" "cascade_failure"
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    ok "  ALL CHAOS MODES ACTIVE"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    echo "  Now ask Archestra: 'Detect anomalies in the last 5 minutes'"
    echo "  Or wait for the automated workflow to pick them up."
    echo ""
    echo "  To stop:  ./scripts/inject_chaos.sh stop"
    echo ""
}

show_menu() {
    echo ""
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD}  NEXUS-ZERO CHAOS INJECTOR${NC}"
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "  Service URLs:"
    [[ -n "$ORDER_URL" ]]        && echo "    order-api:        $ORDER_URL"        || echo "    order-api:        NOT DEPLOYED"
    [[ -n "$PAYMENT_URL" ]]      && echo "    payment-api:      $PAYMENT_URL"      || echo "    payment-api:      NOT DEPLOYED"
    [[ -n "$NOTIFICATION_URL" ]] && echo "    notification-api: $NOTIFICATION_URL" || echo "    notification-api: NOT DEPLOYED"
    echo ""
    echo "  Available chaos modes:"
    echo "    1) order        → db_pool_exhaustion     (30% of requests get 500s)"
    echo "    2) order        → cascading_timeout      (order-api times out calling payment-api)"
    echo "    3) payment      → memory_leak            (gradual OOM until crash)"
    echo "    4) payment      → gateway_timeout        (Stripe gateway timeouts)"
    echo "    5) notification → cascade_failure        (circuit breaker trips on order-api)"
    echo "    6) notification → rate_limit             (SendGrid 429s)"
    echo "    7) ALL services → enable all chaos"
    echo "    8) STOP all chaos"
    echo ""
    read -rp "  Select (1-8): " choice
    echo ""

    case "$choice" in
        1) enable_chaos "order" "db_pool_exhaustion" ;;
        2) enable_chaos "order" "cascading_timeout" ;;
        3) enable_chaos "payment" "memory_leak" ;;
        4) enable_chaos "payment" "gateway_timeout" ;;
        5) enable_chaos "notification" "cascade_failure" ;;
        6) enable_chaos "notification" "rate_limit" ;;
        7) enable_all ;;
        8) disable_all ;;
        *) err "Invalid choice: $choice" ;;
    esac
}

# ---- Main -------------------------------------------------------------------
if [[ $# -eq 0 ]]; then
    show_menu
elif [[ "$1" == "stop" || "$1" == "disable" ]]; then
    disable_all
elif [[ "$1" == "all" ]]; then
    enable_all
elif [[ $# -ge 2 ]]; then
    enable_chaos "$1" "$2"
else
    err "Usage: $0 <service> <chaos_mode>  OR  $0 all  OR  $0 stop"
    echo ""
    echo "  Services: order, payment, notification"
    echo "  Modes:"
    echo "    order:        db_pool_exhaustion, cascading_timeout"
    echo "    payment:      memory_leak, gateway_timeout"
    echo "    notification: cascade_failure, rate_limit"
    exit 1
fi
