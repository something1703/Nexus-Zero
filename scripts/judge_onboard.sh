#!/usr/bin/env bash
# =============================================================================
# Nexus-Zero — Judge Onboarding Script
# =============================================================================
# Usage:
#   ./scripts/judge_onboard.sh
#
# One-command setup for hackathon judges:
#   1. Verifies all infrastructure is running
#   2. Optionally sets API keys (Gemini, GitHub, Slack)
#   3. Deploys chaos services if not already deployed
#   4. Loads/refreshes seed data
#   5. Runs health checks
#   6. Prints access URLs and demo commands
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
DIM='\033[2m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}  ✅${NC}  $*"; }
fail()  { echo -e "${RED}  ❌${NC}  $*"; }
warn()  { echo -e "${YELLOW}  ⚠️${NC}   $*"; }
step()  { echo -e "\n${BOLD}━━━ $* ━━━${NC}"; }

# ---- Banner ----------------------------------------------------------------
clear 2>/dev/null || true
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║                                                  ║${NC}"
echo -e "${BOLD}║         🔮 NEXUS-ZERO SRE PLATFORM 🔮           ║${NC}"
echo -e "${BOLD}║     Autonomous AI Incident Response System       ║${NC}"
echo -e "${BOLD}║                                                  ║${NC}"
echo -e "${BOLD}║         Judge Onboarding & Setup                 ║${NC}"
echo -e "${BOLD}║                                                  ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${DIM}  Hackathon: 2 Fast 2 MCP by Archestra${NC}"
echo -e "${DIM}  Team: Nexus-Zero${NC}"
echo ""

# =============================================================================
# Step 1: Check GCP Infrastructure
# =============================================================================
step "Step 1/5: Checking Infrastructure"

# Check GCP project
info "GCP Project: $PROJECT_ID"
gcloud config set project "$PROJECT_ID" --quiet 2>/dev/null

# Check MCP Agents
AGENTS=("sentinel" "detective" "historian" "mediator" "executor")
AGENT_URLS=()
ALL_AGENTS_UP=true

for agent in "${AGENTS[@]}"; do
    svc_name="nexus-${agent}-agent"
    url=$(gcloud run services describe "$svc_name" \
        --region "$REGION" \
        --format 'value(status.url)' 2>/dev/null || echo "")
    
    if [[ -n "$url" ]]; then
        ok "${agent}-agent: ${url}"
        AGENT_URLS+=("$url")
    else
        fail "${agent}-agent: NOT DEPLOYED"
        ALL_AGENTS_UP=false
    fi
done

# Check Bridge
BRIDGE_URL=$(gcloud run services describe "nexus-archestra-bridge" \
    --region "$REGION" \
    --format 'value(status.url)' 2>/dev/null || echo "")
if [[ -n "$BRIDGE_URL" ]]; then
    ok "archestra-bridge: ${BRIDGE_URL}"
else
    fail "archestra-bridge: NOT DEPLOYED"
fi

# Check Cloud SQL
DB_STATUS=$(gcloud sql instances describe nexus-zero-db \
    --format 'value(state)' 2>/dev/null || echo "UNKNOWN")
if [[ "$DB_STATUS" == "RUNNABLE" ]]; then
    ok "Cloud SQL (nexus-zero-db): RUNNING"
else
    fail "Cloud SQL (nexus-zero-db): $DB_STATUS"
fi

# =============================================================================
# Step 2: Check Demo Services
# =============================================================================
step "Step 2/5: Checking Demo Services"

DEMO_SERVICES=("order" "payment" "notification")
DEMO_URLS=()
NEED_DEPLOY=false

for svc in "${DEMO_SERVICES[@]}"; do
    svc_name="nexus-${svc}-api"
    url=$(gcloud run services describe "$svc_name" \
        --region "$REGION" \
        --format 'value(status.url)' 2>/dev/null || echo "")
    
    if [[ -n "$url" ]]; then
        # Health check
        health=$(curl -s --max-time 10 "${url}/health" 2>/dev/null || echo '{"status":"unreachable"}')
        status=$(echo "$health" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null || echo "unknown")
        
        if [[ "$status" == "healthy" ]]; then
            ok "${svc}-api: ${url} (healthy)"
        else
            warn "${svc}-api: ${url} (status: ${status})"
        fi
        DEMO_URLS+=("${svc}|${url}")
    else
        fail "${svc}-api: NOT DEPLOYED"
        NEED_DEPLOY=true
    fi
done

if [[ "$NEED_DEPLOY" == true ]]; then
    echo ""
    read -rp "  Deploy missing demo services now? (y/N): " deploy_choice
    if [[ "${deploy_choice,,}" == "y" ]]; then
        info "Deploying demo services..."
        SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
        bash "$SCRIPT_DIR/deploy_demo_services.sh"
    else
        warn "Skipping deployment. Some demo features won't work."
    fi
fi

# =============================================================================
# Step 3: Optional Credential Setup
# =============================================================================
step "Step 3/5: Credential Setup (Optional)"

echo ""
echo -e "  ${DIM}These credentials enable advanced features but are NOT required.${NC}"
echo -e "  ${DIM}The core demo (chaos injection + incident response) works without them.${NC}"
echo ""
echo "  Available credential types:"
echo "    1) Gemini API Key   — Enables AI root cause analysis (Detective Agent)"
echo "    2) GitHub Token     — Enables commit correlation (Detective Agent)"
echo "    3) Skip             — Use demo without external credentials"
echo ""
read -rp "  Set up credentials? (1/2/3 or Enter to skip): " cred_choice

case "${cred_choice:-3}" in
    1)
        read -rp "  Enter Gemini API Key: " gemini_key
        if [[ -n "$gemini_key" ]]; then
            # Call set_credentials on detective agent via bridge
            if [[ -n "$BRIDGE_URL" ]]; then
                curl -s -X POST "${BRIDGE_URL}/mcp" \
                    -H "Content-Type: application/json" \
                    -d "{\"jsonrpc\":\"2.0\",\"method\":\"tools/call\",\"params\":{\"name\":\"detective_set_credentials\",\"arguments\":{\"gemini_api_key\":\"${gemini_key}\"}},\"id\":1}" > /dev/null 2>&1
                ok "Gemini API key stored in Detective Agent"
            fi
        fi
        ;;
    2)
        read -rp "  Enter GitHub Token: " github_token
        read -rp "  Enter GitHub Repo (owner/repo): " github_repo
        if [[ -n "$github_token" ]]; then
            if [[ -n "$BRIDGE_URL" ]]; then
                curl -s -X POST "${BRIDGE_URL}/mcp" \
                    -H "Content-Type: application/json" \
                    -d "{\"jsonrpc\":\"2.0\",\"method\":\"tools/call\",\"params\":{\"name\":\"detective_set_credentials\",\"arguments\":{\"github_token\":\"${github_token}\",\"github_repo\":\"${github_repo:-something1703/Nexus-Zero}\"}},\"id\":1}" > /dev/null 2>&1
                ok "GitHub credentials stored in Detective Agent"
            fi
        fi
        ;;
    *)
        info "Skipping credential setup."
        ;;
esac

# =============================================================================
# Step 4: Verify System End-to-End
# =============================================================================
step "Step 4/5: End-to-End Verification"

# Test bridge connectivity
if [[ -n "$BRIDGE_URL" ]]; then
    info "Testing Archestra Bridge..."
    tools_response=$(curl -s --max-time 15 -X POST "${BRIDGE_URL}/mcp" \
        -H "Content-Type: application/json" \
        -d '{"jsonrpc":"2.0","method":"tools/list","params":{},"id":1}' 2>/dev/null || echo "")
    
    if echo "$tools_response" | grep -q "tools" 2>/dev/null; then
        tool_count=$(echo "$tools_response" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('result',{}).get('tools',[])))" 2>/dev/null || echo "?")
        ok "Bridge connected: ${tool_count} tools available"
    else
        warn "Bridge responded but tools not loaded (agents may be cold-starting)"
    fi
fi

# Test sentinel service health
if [[ -n "$BRIDGE_URL" ]]; then
    info "Testing Sentinel Agent (get_service_health)..."
    health_response=$(curl -s --max-time 30 -X POST "${BRIDGE_URL}/mcp" \
        -H "Content-Type: application/json" \
        -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"sentinel_get_service_health","arguments":{}},"id":2}' 2>/dev/null || echo "")
    
    if echo "$health_response" | grep -q "healthy\|services" 2>/dev/null; then
        ok "Sentinel Agent responding (service health check passed)"
    else
        warn "Sentinel Agent slow to respond (may be cold-starting, try again in 30s)"
    fi
fi

# =============================================================================
# Step 5: Print Summary & Demo Commands
# =============================================================================
step "Step 5/5: Setup Complete!"

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║           🎉 NEXUS-ZERO IS READY! 🎉            ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${BOLD}  Access Points:${NC}"
echo ""

# Archestra Hub
echo "  🌐 Archestra Hub:"
echo "     https://archestra-hub-833613368271.us-central1.run.app"
echo ""

# Bridge
if [[ -n "$BRIDGE_URL" ]]; then
    echo "  🔗 MCP Bridge:"
    echo "     ${BRIDGE_URL}/mcp"
    echo ""
fi

# Demo Services
echo "  🎯 Demo Services (Chaos Targets):"
for entry in "${DEMO_URLS[@]:-}"; do
    if [[ -n "$entry" ]]; then
        svc=$(echo "$entry" | cut -d'|' -f1)
        url=$(echo "$entry" | cut -d'|' -f2)
        echo "     ${svc}-api: ${url}"
    fi
done
echo ""

echo -e "${BOLD}  Quick Demo Commands:${NC}"
echo ""
echo "  1️⃣  Inject chaos (creates REAL errors):"
echo "     ./scripts/inject_chaos.sh order db_pool_exhaustion"
echo ""
echo "  2️⃣  Ask Archestra to detect & investigate:"
echo "     \"Detect anomalies in the last 5 minutes\""
echo "     \"Investigate the root cause of the latest incident\""
echo "     \"Search for playbooks about connection pool\""
echo "     \"Analyze the blast radius for order-api\""
echo ""
echo "  3️⃣  Enable ALL chaos (full demo):"
echo "     ./scripts/inject_chaos.sh all"
echo ""
echo "  4️⃣  Reset everything:"
echo "     ./scripts/reset_demo.sh"
echo ""
echo -e "${BOLD}  Chaos Modes Available:${NC}"
echo "     order-api:        db_pool_exhaustion, cascading_timeout"
echo "     payment-api:      memory_leak, gateway_timeout"
echo "     notification-api: cascade_failure, rate_limit"
echo ""
echo -e "${DIM}  ─────────────────────────────────────────────────${NC}"
echo -e "${DIM}  Built with ❤️  by Team Nexus-Zero${NC}"
echo -e "${DIM}  Hackathon: 2 Fast 2 MCP by Archestra (Feb 2026)${NC}"
echo ""
