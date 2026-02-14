#!/usr/bin/env bash
# =============================================================================
# Nexus-Zero — Print Archestra Hub Registration Info
# =============================================================================
# After deploying agents, run this script to get the SSE endpoints
# you need to register in Archestra Hub.
# =============================================================================

set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:-nexus-zero-sre}"
REGION="${GCP_REGION:-us-central1}"

AGENTS=("sentinel" "detective" "historian" "mediator" "executor")

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  NEXUS-ZERO — Archestra Hub MCP Agent Registration"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

for agent in "${AGENTS[@]}"; do
    service_name="nexus-${agent}-agent"
    url=$(gcloud run services describe "$service_name" \
        --region "$REGION" \
        --format 'value(status.url)' 2>/dev/null || echo "NOT DEPLOYED")

    if [[ "$url" == "NOT DEPLOYED" ]]; then
        echo "  ❌ ${service_name}: NOT DEPLOYED"
    else
        echo "  ✅ ${service_name}"
        echo "     SSE Endpoint: ${url}/sse"
    fi
    echo ""
done

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  To register in Archestra Hub:"
echo "  1. Open the Archestra Hub UI"
echo "  2. Go to MCP Servers → Add Server"
echo "  3. Set transport to 'SSE'"
echo "  4. Paste the SSE endpoint URL for each agent"
echo "  5. Save and verify the connection"
echo ""
echo "  After registration, Archestra's Gemini 2.5 Flash can call"
echo "  any tool exposed by these agents via the MCP protocol."
echo ""
