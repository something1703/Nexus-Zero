#!/usr/bin/env bash
# =============================================================================
# Nexus-Zero — One-Command Deployment to Your GCP Project
# =============================================================================
# Usage:
#   chmod +x deploy.sh
#   ./deploy.sh                          # Interactive (prompts for everything)
#   ./deploy.sh --project my-gcp-project # Pass project ID directly
#
# This script deploys the ENTIRE Nexus-Zero platform to YOUR GCP project:
#   1. Enables required GCP APIs
#   2. Creates Cloud SQL instance + database + schema + seed data
#   3. Stores credentials in Secret Manager
#   4. Deploys 5 MCP agents (Sentinel, Detective, Historian, Mediator, Executor)
#   5. Deploys 1 demo service (order-api) with chaos injection
#   6. Deploys the Archestra MCP Bridge (auto-configures agent URLs)
#   7. Deploys the Dashboard (landing page + live demo)
#   8. Prints all URLs and next steps
#
# Prerequisites:
#   - Google Cloud SDK (gcloud) installed & authenticated
#   - Billing enabled on target project
#   - ~15 minutes for full deployment
#
# Cost: ~$0.50/day during active testing, $0 when idle (scale-to-zero)
# =============================================================================

set -euo pipefail

# ---- Colors & Helpers -------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; DIM='\033[2m'; NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}  ✅${NC}  $*"; }
fail()  { echo -e "${RED}  ❌${NC}  $*"; exit 1; }
warn()  { echo -e "${YELLOW}  ⚠️${NC}   $*"; }
step()  { echo -e "\n${BOLD}━━━ $* ━━━${NC}"; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ---- Banner ----------------------------------------------------------------
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║                                                      ║${NC}"
echo -e "${BOLD}║        🔮 NEXUS-ZERO DEPLOYMENT WIZARD 🔮            ║${NC}"
echo -e "${BOLD}║     Deploy Autonomous SRE to Your GCP Project        ║${NC}"
echo -e "${BOLD}║                                                      ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════╝${NC}"
echo ""

# ---- Parse Args -------------------------------------------------------------
PROJECT_ID=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --project) PROJECT_ID="$2"; shift 2 ;;
        --project=*) PROJECT_ID="${1#*=}"; shift ;;
        -h|--help)
            echo "Usage: ./deploy.sh [--project GCP_PROJECT_ID]"
            echo ""
            echo "If --project is not provided, you will be prompted."
            echo ""
            echo "Required credentials (will be prompted):"
            echo "  - GCP Project with billing enabled"
            echo "  - Gemini API Key (free: https://aistudio.google.com/apikey)"
            echo ""
            echo "Optional credentials:"
            echo "  - GitHub Token (for commit correlation in Detective Agent)"
            echo ""
            exit 0
            ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

# =============================================================================
# Step 1: Collect Configuration
# =============================================================================
step "Step 1/8: Configuration"

# GCP Project
if [[ -z "$PROJECT_ID" ]]; then
    CURRENT_PROJECT=$(gcloud config get-value project 2>/dev/null || echo "")
    if [[ -n "$CURRENT_PROJECT" ]]; then
        echo -e "  Current GCP project: ${CYAN}${CURRENT_PROJECT}${NC}"
        read -rp "  Use this project? (Y/n): " use_current
        if [[ "${use_current,,}" != "n" ]]; then
            PROJECT_ID="$CURRENT_PROJECT"
        fi
    fi
    if [[ -z "$PROJECT_ID" ]]; then
        read -rp "  Enter your GCP Project ID: " PROJECT_ID
    fi
fi

[[ -z "$PROJECT_ID" ]] && fail "Project ID is required"
gcloud config set project "$PROJECT_ID" --quiet 2>/dev/null
ok "GCP Project: $PROJECT_ID"

# Region
REGION="us-central1"
echo -e "  Region: ${CYAN}${REGION}${NC} (optimal for Cloud SQL + Cloud Run)"

# Database password
echo ""
echo -e "  ${DIM}Choose a password for the Cloud SQL database user 'nexus_admin'.${NC}"
echo -e "  ${DIM}This password will be stored in GCP Secret Manager.${NC}"
while true; do
    read -srp "  Enter DB password (min 8 chars): " DB_PASSWORD
    echo ""
    if [[ ${#DB_PASSWORD} -ge 8 ]]; then
        break
    fi
    warn "Password must be at least 8 characters."
done
ok "Database password set"

# Gemini API Key
echo ""
echo -e "  ${DIM}Get a free Gemini API key at: ${CYAN}https://aistudio.google.com/apikey${NC}"
read -rp "  Enter Gemini API Key: " GEMINI_API_KEY
[[ -z "$GEMINI_API_KEY" ]] && fail "Gemini API Key is required for agents to reason"
ok "Gemini API Key provided"

# GitHub Token (optional)
echo ""
echo -e "  ${DIM}Optional: GitHub token for commit correlation in Detective Agent.${NC}"
echo -e "  ${DIM}Create one at: ${CYAN}https://github.com/settings/tokens${NC}  (scope: repo read)${NC}"
read -rp "  Enter GitHub Token (or press Enter to skip): " GITHUB_TOKEN
GITHUB_REPO=""
if [[ -n "$GITHUB_TOKEN" ]]; then
    read -rp "  Enter GitHub Repo (owner/repo): " GITHUB_REPO
    ok "GitHub credentials provided"
else
    info "Skipping GitHub integration (optional)"
fi

# Derived values
INSTANCE_NAME="nexus-zero-db"
DB_NAME="nexus_zero"
DB_USER="nexus_admin"
INSTANCE_CONNECTION_NAME="${PROJECT_ID}:${REGION}:${INSTANCE_NAME}"

echo ""
echo -e "  ${BOLD}Configuration Summary:${NC}"
echo -e "    Project:      ${CYAN}${PROJECT_ID}${NC}"
echo -e "    Region:       ${CYAN}${REGION}${NC}"
echo -e "    DB Instance:  ${CYAN}${INSTANCE_NAME}${NC}"
echo -e "    DB Name:      ${CYAN}${DB_NAME}${NC}"
echo -e "    Gemini Key:   ${CYAN}${GEMINI_API_KEY:0:8}...${NC}"
echo ""
read -rp "  Continue with deployment? (Y/n): " confirm
[[ "${confirm,,}" == "n" ]] && { echo "Aborted."; exit 0; }

# =============================================================================
# Step 2: Enable GCP APIs
# =============================================================================
step "Step 2/8: Enabling GCP APIs"

REQUIRED_APIS=(
    run.googleapis.com
    cloudbuild.googleapis.com
    sqladmin.googleapis.com
    logging.googleapis.com
    artifactregistry.googleapis.com
    secretmanager.googleapis.com
    cloudresourcemanager.googleapis.com
)

info "Enabling ${#REQUIRED_APIS[@]} APIs (this may take a minute)..."
gcloud services enable "${REQUIRED_APIS[@]}" --project="$PROJECT_ID" --quiet
ok "All APIs enabled"

# =============================================================================
# Step 3: Create Cloud SQL Instance + Database
# =============================================================================
step "Step 3/8: Setting Up Cloud SQL"

# Check if instance already exists
if gcloud sql instances describe "$INSTANCE_NAME" --project="$PROJECT_ID" &>/dev/null; then
    ok "Cloud SQL instance '${INSTANCE_NAME}' already exists"
else
    info "Creating Cloud SQL instance '${INSTANCE_NAME}' (this takes 3-5 minutes)..."
    gcloud sql instances create "$INSTANCE_NAME" \
        --project="$PROJECT_ID" \
        --region="$REGION" \
        --database-version=POSTGRES_15 \
        --tier=db-f1-micro \
        --storage-size=10GB \
        --storage-auto-increase \
        --availability-type=zonal \
        --root-password="$DB_PASSWORD" \
        --quiet
    ok "Cloud SQL instance created"
fi

# Set postgres password (in case instance pre-existed)
info "Setting postgres user password..."
gcloud sql users set-password postgres \
    --instance="$INSTANCE_NAME" \
    --project="$PROJECT_ID" \
    --password="$DB_PASSWORD" \
    --quiet 2>/dev/null || true

# Create nexus_admin user
info "Creating database user 'nexus_admin'..."
gcloud sql users create "$DB_USER" \
    --instance="$INSTANCE_NAME" \
    --project="$PROJECT_ID" \
    --password="$DB_PASSWORD" \
    --quiet 2>/dev/null || ok "User 'nexus_admin' already exists"

# Create database
info "Creating database '${DB_NAME}'..."
gcloud sql databases create "$DB_NAME" \
    --instance="$INSTANCE_NAME" \
    --project="$PROJECT_ID" \
    --quiet 2>/dev/null || ok "Database '${DB_NAME}' already exists"

# Get instance IP for schema loading
DB_IP=$(gcloud sql instances describe "$INSTANCE_NAME" \
    --project="$PROJECT_ID" \
    --format='value(ipAddresses[0].ipAddress)' 2>/dev/null || echo "")

# Whitelist current IP for schema loading
info "Whitelisting your IP for database setup..."
MY_IP=$(curl -s https://ifconfig.me 2>/dev/null || curl -s https://api.ipify.org 2>/dev/null || echo "")
if [[ -n "$MY_IP" ]]; then
    gcloud sql instances patch "$INSTANCE_NAME" \
        --project="$PROJECT_ID" \
        --authorized-networks="${MY_IP}/32" \
        --quiet 2>/dev/null || true
    ok "IP whitelisted: ${MY_IP}"
fi

# Apply schema and seed data
if [[ -n "$DB_IP" ]] && command -v psql &>/dev/null; then
    info "Applying database schema..."
    PGPASSWORD="$DB_PASSWORD" psql -h "$DB_IP" -U postgres -d "$DB_NAME" \
        -f "$SCRIPT_DIR/database/schema.sql" -q 2>/dev/null && ok "Schema applied" || warn "Schema may already exist"

    info "Loading seed data..."
    PGPASSWORD="$DB_PASSWORD" psql -h "$DB_IP" -U postgres -d "$DB_NAME" \
        -f "$SCRIPT_DIR/database/seed_data.sql" -q 2>/dev/null && ok "Seed data loaded" || warn "Seed data may already exist"

    # Grant permissions to nexus_admin
    info "Granting permissions to nexus_admin..."
    PGPASSWORD="$DB_PASSWORD" psql -h "$DB_IP" -U postgres -d "$DB_NAME" -q <<EOSQL 2>/dev/null
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO nexus_admin;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO nexus_admin;
GRANT USAGE ON SCHEMA public TO nexus_admin;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO nexus_admin;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO nexus_admin;
EOSQL
    ok "Permissions granted"
elif [[ -n "$DB_IP" ]]; then
    warn "psql not found — applying schema via python..."
    python3 -c "
import psycopg2
conn = psycopg2.connect(host='${DB_IP}', user='postgres', password='${DB_PASSWORD}', dbname='${DB_NAME}')
conn.autocommit = True
cur = conn.cursor()
with open('${SCRIPT_DIR}/database/schema.sql') as f:
    cur.execute(f.read())
with open('${SCRIPT_DIR}/database/seed_data.sql') as f:
    cur.execute(f.read())
cur.execute('GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO nexus_admin;')
cur.execute('GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO nexus_admin;')
conn.close()
print('Schema + seed data applied via Python')
" && ok "Schema + seed data applied" || warn "Schema application had warnings (may be OK if tables exist)"
else
    warn "Could not get DB IP. Run schema manually: psql -h <DB_IP> -U postgres -d ${DB_NAME} -f database/schema.sql"
fi

# =============================================================================
# Step 4: Store Secrets in Secret Manager
# =============================================================================
step "Step 4/8: Storing Secrets"

store_secret() {
    local name="$1" value="$2"
    if gcloud secrets describe "$name" --project="$PROJECT_ID" &>/dev/null; then
        echo -n "$value" | gcloud secrets versions add "$name" \
            --data-file=- --project="$PROJECT_ID" --quiet
        ok "Updated secret: $name"
    else
        echo -n "$value" | gcloud secrets create "$name" \
            --data-file=- --project="$PROJECT_ID" \
            --replication-policy=automatic --quiet
        ok "Created secret: $name"
    fi
}

store_secret "db-password" "$DB_PASSWORD"
store_secret "gemini-api-key" "$GEMINI_API_KEY"
[[ -n "$GITHUB_TOKEN" ]] && store_secret "github-token" "$GITHUB_TOKEN"

# =============================================================================
# Step 5: Deploy MCP Agents
# =============================================================================
step "Step 5/8: Deploying MCP Agents"

AGENTS=("sentinel" "detective" "historian" "mediator" "executor")
AGENT_URLS=()
AGENTS_DIR="$SCRIPT_DIR/mcp-agents"

for agent in "${AGENTS[@]}"; do
    service_name="nexus-${agent}-agent"
    agent_dir="${AGENTS_DIR}/${agent}-agent"

    if [[ ! -d "$agent_dir" ]]; then
        warn "Agent directory not found: $agent_dir — skipping"
        continue
    fi

    info "Deploying ${service_name}..."

    # Build image using Cloud Build
    image="gcr.io/${PROJECT_ID}/${service_name}"
    config_file=$(mktemp --suffix=.yaml)
    cat > "$config_file" <<BUILDEOF
steps:
  - name: 'gcr.io/cloud-builders/docker'
    args: ['build', '-f', '${agent}-agent/Dockerfile', '-t', '$image', '.']
images: ['$image']
BUILDEOF

    gcloud builds submit "$AGENTS_DIR" \
        --config="$config_file" \
        --project="$PROJECT_ID" \
        --quiet 2>/dev/null
    rm -f "$config_file"

    # Deploy to Cloud Run
    env_vars="GCP_PROJECT_ID=${PROJECT_ID}"
    env_vars+=",DB_NAME=${DB_NAME}"
    env_vars+=",DB_USER=${DB_USER}"
    env_vars+=",DB_PASSWORD=${DB_PASSWORD}"
    env_vars+=",INSTANCE_CONNECTION_NAME=${INSTANCE_CONNECTION_NAME}"
    env_vars+=",GEMINI_API_KEY=${GEMINI_API_KEY}"
    [[ -n "$GITHUB_TOKEN" ]] && env_vars+=",GITHUB_TOKEN=${GITHUB_TOKEN}"
    [[ -n "$GITHUB_REPO" ]] && env_vars+=",GITHUB_REPO=${GITHUB_REPO}"

    gcloud run deploy "$service_name" \
        --image "$image" \
        --region "$REGION" \
        --project "$PROJECT_ID" \
        --platform managed \
        --allow-unauthenticated \
        --add-cloudsql-instances "$INSTANCE_CONNECTION_NAME" \
        --set-env-vars "$env_vars" \
        --memory 512Mi \
        --cpu 1 \
        --min-instances 0 \
        --max-instances 3 \
        --timeout 600 \
        --port 8080 \
        --quiet 2>/dev/null

    url=$(gcloud run services describe "$service_name" \
        --region "$REGION" --project "$PROJECT_ID" \
        --format 'value(status.url)' 2>/dev/null || echo "")

    if [[ -n "$url" ]]; then
        AGENT_URLS+=("${agent}|${url}")
        ok "${service_name} → ${url}"
    else
        warn "${service_name} deployment may have failed"
    fi
done

# Extract agent URLs for bridge
get_agent_url() {
    local name="$1"
    for entry in "${AGENT_URLS[@]}"; do
        if [[ "$entry" == "${name}|"* ]]; then
            echo "${entry#*|}"
            return
        fi
    done
    echo ""
}

SENTINEL_URL=$(get_agent_url "sentinel")
DETECTIVE_URL=$(get_agent_url "detective")
HISTORIAN_URL=$(get_agent_url "historian")
MEDIATOR_URL=$(get_agent_url "mediator")
EXECUTOR_URL=$(get_agent_url "executor")

# =============================================================================
# Step 6: Deploy Demo Service (order-api)
# =============================================================================
step "Step 6/8: Deploying Demo Service"

info "Deploying nexus-order-api (chaos target)..."
gcloud builds submit "$SCRIPT_DIR/demo-services/order-api" \
    --tag "gcr.io/${PROJECT_ID}/nexus-order-api" \
    --project="$PROJECT_ID" \
    --quiet 2>/dev/null

gcloud run deploy nexus-order-api \
    --image "gcr.io/${PROJECT_ID}/nexus-order-api" \
    --region "$REGION" \
    --project "$PROJECT_ID" \
    --platform managed \
    --allow-unauthenticated \
    --memory 256Mi \
    --cpu 1 \
    --min-instances 0 \
    --max-instances 2 \
    --timeout 120 \
    --port 8080 \
    --quiet 2>/dev/null

ORDER_API_URL=$(gcloud run services describe nexus-order-api \
    --region "$REGION" --project "$PROJECT_ID" \
    --format 'value(status.url)' 2>/dev/null || echo "")
ok "nexus-order-api → ${ORDER_API_URL}"

# =============================================================================
# Step 7: Deploy Archestra Bridge
# =============================================================================
step "Step 7/8: Deploying Archestra Bridge"

info "Building and deploying bridge..."
gcloud builds submit "$SCRIPT_DIR" \
    --config /dev/stdin \
    --project="$PROJECT_ID" \
    --quiet 2>/dev/null <<BRIDGEOF
steps:
  - name: 'gcr.io/cloud-builders/docker'
    args:
      - 'build'
      - '-t'
      - 'gcr.io/${PROJECT_ID}/nexus-archestra-bridge:latest'
      - '-f'
      - 'mcp-agents/archestra-bridge/Dockerfile'
      - '.'
images:
  - 'gcr.io/${PROJECT_ID}/nexus-archestra-bridge:latest'
BRIDGEOF

# Bridge env vars include all agent URLs
bridge_env="SENTINEL_AGENT_URL=${SENTINEL_URL}"
bridge_env+=",DETECTIVE_AGENT_URL=${DETECTIVE_URL}"
bridge_env+=",HISTORIAN_AGENT_URL=${HISTORIAN_URL}"
bridge_env+=",MEDIATOR_AGENT_URL=${MEDIATOR_URL}"
bridge_env+=",EXECUTOR_AGENT_URL=${EXECUTOR_URL}"

gcloud run deploy nexus-archestra-bridge \
    --image "gcr.io/${PROJECT_ID}/nexus-archestra-bridge:latest" \
    --region "$REGION" \
    --project "$PROJECT_ID" \
    --platform managed \
    --allow-unauthenticated \
    --set-env-vars "$bridge_env" \
    --memory 512Mi \
    --cpu 1 \
    --min-instances 0 \
    --max-instances 3 \
    --timeout 600 \
    --port 8080 \
    --quiet 2>/dev/null

BRIDGE_URL=$(gcloud run services describe nexus-archestra-bridge \
    --region "$REGION" --project "$PROJECT_ID" \
    --format 'value(status.url)' 2>/dev/null || echo "")
ok "nexus-archestra-bridge → ${BRIDGE_URL}"

# =============================================================================
# Step 8: Deploy Dashboard
# =============================================================================
step "Step 8/8: Deploying Dashboard"

info "Deploying nexus-dashboard..."

# The Sentinel Agent ID will be retrieved after Archestra registration
# For now, use a placeholder — users will update after Archestra setup
gcloud run deploy nexus-dashboard \
    --source "$SCRIPT_DIR/web" \
    --region "$REGION" \
    --project "$PROJECT_ID" \
    --platform managed \
    --allow-unauthenticated \
    --add-cloudsql-instances "$INSTANCE_CONNECTION_NAME" \
    --set-env-vars "\
GCP_PROJECT_ID=${PROJECT_ID},\
GCP_REGION=${REGION},\
DB_NAME=${DB_NAME},\
DB_USER=${DB_USER},\
DB_PASSWORD=${DB_PASSWORD},\
INSTANCE_CONNECTION_NAME=${INSTANCE_CONNECTION_NAME},\
ORDER_API_URL=${ORDER_API_URL},\
BRIDGE_URL=${BRIDGE_URL}" \
    --memory 512Mi \
    --timeout 120 \
    --min-instances 0 \
    --max-instances 3 \
    --quiet 2>/dev/null

DASHBOARD_URL=$(gcloud run services describe nexus-dashboard \
    --region "$REGION" --project "$PROJECT_ID" \
    --format 'value(status.url)' 2>/dev/null || echo "")
ok "nexus-dashboard → ${DASHBOARD_URL}"

# =============================================================================
# FINAL SUMMARY
# =============================================================================
echo ""
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║                                                              ║${NC}"
echo -e "${BOLD}║          🎉 NEXUS-ZERO DEPLOYED SUCCESSFULLY! 🎉            ║${NC}"
echo -e "${BOLD}║                                                              ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${BOLD}🌐 Dashboard:${NC}        ${CYAN}${DASHBOARD_URL}${NC}"
echo -e "  ${BOLD}🔗 MCP Bridge:${NC}       ${CYAN}${BRIDGE_URL}/mcp${NC}"
echo -e "  ${BOLD}🎯 Order API:${NC}        ${CYAN}${ORDER_API_URL}${NC}"
echo ""
echo -e "  ${BOLD}🤖 MCP Agent SSE Endpoints:${NC}"
for entry in "${AGENT_URLS[@]}"; do
    name="${entry%%|*}"
    url="${entry#*|}"
    echo -e "     ${name}: ${CYAN}${url}/sse${NC}"
done
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}  NEXT STEPS — Archestra Hub Configuration${NC}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  1. Open Archestra Hub and create an account:"
echo -e "     ${CYAN}https://archestra.ai${NC}"
echo ""
echo -e "  2. Register your 5 MCP agents as remote MCP servers:"
echo -e "     • Go to ${BOLD}MCP Servers → Add Remote Server${NC}"
echo -e "     • For each agent, paste its ${BOLD}/sse${NC} endpoint URL"
echo -e "     • Name them: nexus-sentinel, nexus-detective, etc."
echo ""
echo -e "  3. Create an autonomous workflow (Agent):"
echo -e "     • Add all 5 MCP servers to the agent"
echo -e "     • Set system prompt: \"You are Nexus-Zero, an autonomous SRE agent.\""
echo -e "     • Use model: ${BOLD}gemini-2.5-flash${NC}"
echo ""
echo -e "  4. Get your Archestra credentials:"
echo -e "     • Copy the ${BOLD}Agent ID${NC} (UUID) of your Sentinel workflow"
echo -e "     • Copy your ${BOLD}API Bearer Token${NC} from Settings"
echo ""
echo -e "  5. Update dashboard with Archestra credentials:"
echo -e "     ${DIM}gcloud run services update nexus-dashboard \\${NC}"
echo -e "     ${DIM}  --region ${REGION} --project ${PROJECT_ID} \\${NC}"
echo -e "     ${DIM}  --update-env-vars \"ARCHESTRA_TOKEN=<your-token>,SENTINEL_AGENT_ID=<your-agent-id>\"${NC}"
echo ""
echo -e "  6. Test the deployment:"
echo -e "     • Open ${CYAN}${DASHBOARD_URL}${NC}"
echo -e "     • Click '💣 Break Service' to inject chaos"
echo -e "     • Click '🔍 Run Sentinel Detection'"
echo -e "     • Watch agents investigate and recommend a fix"
echo -e "     • Approve the remediation action"
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}  COST INFORMATION${NC}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  Cloud SQL (db-f1-micro):  ~\$8/month (always-on)"
echo -e "  Cloud Run (8 services):   ~\$0/month (scale-to-zero, pay per use)"
echo -e "  Cloud Build:              Free tier (120 min/day)"
echo -e "  Secret Manager:           Free tier (6 secret versions)"
echo -e "  ${BOLD}Total: ~\$8-12/month${NC}, or ~\$0.30/day"
echo ""
echo -e "  To minimize cost when not demoing:"
echo -e "    gcloud sql instances patch ${INSTANCE_NAME} --activation-policy=NEVER --project=${PROJECT_ID}"
echo -e "  To restart:"
echo -e "    gcloud sql instances patch ${INSTANCE_NAME} --activation-policy=ALWAYS --project=${PROJECT_ID}"
echo ""
echo -e "  ${BOLD}To tear down everything:${NC}"
echo -e "    ${CYAN}./teardown.sh --project ${PROJECT_ID}${NC}"
echo ""
echo -e "${DIM}  ─────────────────────────────────────────────────────────────${NC}"
echo -e "${DIM}  Built with ❤️  by Team Nexus-Zero${NC}"
echo -e "${DIM}  Hackathon: 2 Fast 2 MCP by Archestra (Feb 2026)${NC}"
echo ""
