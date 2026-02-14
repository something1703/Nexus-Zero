#!/usr/bin/env bash
# =============================================================================
# Nexus-Zero — Deploy All MCP Agents to Google Cloud Run
# =============================================================================
# Usage:
#   chmod +x scripts/deploy_agents.sh
#   ./scripts/deploy_agents.sh              # deploy all agents
#   ./scripts/deploy_agents.sh sentinel     # deploy only sentinel-agent
#   ./scripts/deploy_agents.sh detective    # deploy only detective-agent
#
# Prerequisites:
#   - gcloud CLI authenticated with correct project
#   - Docker / Cloud Build enabled
#   - Cloud SQL instance running (nexus-zero-db)
#   - Environment variables set (or use defaults below)
# =============================================================================

set -euo pipefail

# ---- Configuration ----------------------------------------------------------
PROJECT_ID="${GCP_PROJECT_ID:-nexus-zero-sre}"
REGION="${GCP_REGION:-us-central1}"
DB_NAME="${DB_NAME:-nexus_zero}"
DB_USER="${DB_USER:-nexus_admin}"
DB_PASSWORD_SECRET="${DB_PASSWORD_SECRET:-db-password}"  # Secret Manager secret name
INSTANCE_CONNECTION_NAME="${INSTANCE_CONNECTION_NAME:-${PROJECT_ID}:${REGION}:nexus-zero-db}"

# NOTE: GEMINI_API_KEY, GITHUB_TOKEN, GITHUB_REPO, SLACK_BOT_TOKEN
# are NOT baked into env vars. They are injected at runtime via each
# agent's set_credentials MCP tool — zero trust, multi-tenant design.

# Cloud Run settings
MEMORY="512Mi"
CPU="1"
MIN_INSTANCES="0"          # scale-to-zero for cost savings
MAX_INSTANCES="3"
CONCURRENCY="80"
TIMEOUT="300"

# All agents
ALL_AGENTS=("sentinel" "detective" "historian" "mediator" "executor")

# ---- Helpers ----------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*"; }

# ---- Validation & Password Retrieval ----------------------------------------
# Make sure gcloud is configured
info "Active GCP project: $(gcloud config get-value project 2>/dev/null)"
gcloud config set project "$PROJECT_ID" --quiet

# Fetch DB password from Secret Manager (secure, no hardcoding)
info "Fetching database password from Secret Manager..."
DB_PASSWORD=$(gcloud secrets versions access latest \
    --secret="$DB_PASSWORD_SECRET" \
    --project="$PROJECT_ID" 2>/dev/null)

if [[ -z "$DB_PASSWORD" ]]; then
    err "Failed to retrieve password from Secret Manager."
    echo "  Make sure the secret exists: gcloud secrets describe $DB_PASSWORD_SECRET"
    echo "  Or create it: echo -n 'YOUR_PASSWORD' | gcloud secrets create $DB_PASSWORD_SECRET --data-file=-"
    exit 1
fi

info "✅ Password retrieved from Secret Manager (secret: $DB_PASSWORD_SECRET)"

# Enable required APIs (idempotent)
info "Enabling required GCP APIs..."
gcloud services enable \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    sqladmin.googleapis.com \
    logging.googleapis.com \
    artifactregistry.googleapis.com \
    --quiet

# ---- Determine which agents to deploy --------------------------------------
if [[ $# -gt 0 ]]; then
    AGENTS_TO_DEPLOY=("$@")
else
    AGENTS_TO_DEPLOY=("${ALL_AGENTS[@]}")
fi

info "Agents to deploy: ${AGENTS_TO_DEPLOY[*]}"

# ---- Build & Deploy ---------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
AGENTS_DIR="$REPO_ROOT/mcp-agents"

deploy_agent() {
    local agent_name="$1"
    local service_name="nexus-${agent_name}-agent"
    local agent_dir="${AGENTS_DIR}/${agent_name}-agent"

    if [[ ! -d "$agent_dir" ]]; then
        err "Agent directory not found: $agent_dir"
        return 1
    fi

    info "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    info "Deploying: ${service_name}"
    info "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    # Build with Cloud Build (uses Dockerfile from agent directory)
    # Docker build context = mcp-agents/ so Dockerfile can COPY common/
    local image="gcr.io/${PROJECT_ID}/${service_name}"

    info "Building Docker image: ${image}"
    # Build from mcp-agents/ directory so Dockerfile can COPY common/ and {agent}/ dirs
    # Create a cloudbuild.yaml that points to the right Dockerfile
    local config_file=$(mktemp --suffix=.yaml)
    cat > "$config_file" <<BUILDEOF
steps:
  - name: 'gcr.io/cloud-builders/docker'
    args: ['build', '-f', '${agent_name}-agent/Dockerfile', '-t', '$image', '.']
images: ['$image']
BUILDEOF
    
    gcloud builds submit "$AGENTS_DIR" \
        --config="$config_file" \
        --quiet
    
    rm -f "$config_file"

    # All agents get the same infrastructure env vars.
    # User-specific credentials (Gemini, GitHub, Slack) are injected
    # at runtime via each agent's set_credentials MCP tool.
    # Properly escape special characters in password for YAML format
    local escaped_password=$(printf '%s\n' "$DB_PASSWORD" | sed 's/\\/\\\\/g; s/"/\\"/g')
    
    local env_file=$(mktemp --suffix=.yaml)
    cat > "$env_file" <<EOF
GCP_PROJECT_ID: "${PROJECT_ID}"
DB_NAME: "${DB_NAME}"
DB_USER: "${DB_USER}"
DB_PASSWORD: "${escaped_password}"
INSTANCE_CONNECTION_NAME: "${INSTANCE_CONNECTION_NAME}"
EOF

    info "Deploying to Cloud Run: ${service_name}"
    gcloud run deploy "$service_name" \
        --image "$image" \
        --region "$REGION" \
        --platform managed \
        --allow-unauthenticated \
        --add-cloudsql-instances "$INSTANCE_CONNECTION_NAME" \
        --env-vars-file "$env_file" \
        --memory "$MEMORY" \
        --cpu "$CPU" \
        --min-instances "$MIN_INSTANCES" \
        --max-instances "$MAX_INSTANCES" \
        --concurrency "$CONCURRENCY" \
        --timeout 600 \
        --port 8080 \
        --quiet
    
    rm -f "$env_file"

    # Get the deployed URL
    local url
    url=$(gcloud run services describe "$service_name" \
        --region "$REGION" \
        --format 'value(status.url)' 2>/dev/null)

    ok "${service_name} deployed → ${url}"
    echo ""
    echo "  MCP SSE endpoint: ${url}/sse"
    echo "  Health check:     curl ${url}/sse"
    echo ""
}

# ---- Main loop --------------------------------------------------------------
FAILED=()
SUCCEEDED=()

for agent in "${AGENTS_TO_DEPLOY[@]}"; do
    if deploy_agent "$agent"; then
        SUCCEEDED+=("$agent")
    else
        FAILED+=("$agent")
    fi
done

# ---- Summary ----------------------------------------------------------------
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  DEPLOYMENT SUMMARY"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [[ ${#SUCCEEDED[@]} -gt 0 ]]; then
    ok "Succeeded: ${SUCCEEDED[*]}"
fi
if [[ ${#FAILED[@]} -gt 0 ]]; then
    err "Failed:    ${FAILED[*]}"
fi

echo ""
info "Next steps:"
echo "  1. Go to Archestra Hub and add each agent's SSE endpoint"
echo "  2. SSE endpoints follow the pattern:"
echo "     https://nexus-<agent>-agent-<hash>-uc.a.run.app/sse"
echo "  3. Test with: gcloud run services list --region ${REGION}"
echo ""

if [[ ${#FAILED[@]} -gt 0 ]]; then
    exit 1
fi
