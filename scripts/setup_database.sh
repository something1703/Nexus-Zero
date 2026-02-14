#!/bin/bash

# ============================================
# Nexus-Zero Database Setup Script
# Run this in Google Cloud Shell
# ============================================

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
PROJECT_ID="nexus-zero-sre"
REGION="us-central1"
INSTANCE_NAME="nexus-zero-db"
DATABASE_NAME="postgres"

echo -e "${GREEN}======================================${NC}"
echo -e "${GREEN}Nexus-Zero Database Setup${NC}"
echo -e "${GREEN}======================================${NC}"
echo ""

# Step 1: Verify we're in the right project
echo -e "${YELLOW}Step 1: Verifying GCP project...${NC}"
CURRENT_PROJECT=$(gcloud config get-value project 2>/dev/null)
if [ "$CURRENT_PROJECT" != "$PROJECT_ID" ]; then
    echo -e "${YELLOW}Switching to project: $PROJECT_ID${NC}"
    gcloud config set project $PROJECT_ID
fi
echo -e "${GREEN}✓ Using project: $PROJECT_ID${NC}"
echo ""

# Step 2: Check if Cloud SQL instance exists
echo -e "${YELLOW}Step 2: Checking Cloud SQL instance...${NC}"
if gcloud sql instances describe $INSTANCE_NAME --project=$PROJECT_ID &>/dev/null; then
    echo -e "${GREEN}✓ Cloud SQL instance '$INSTANCE_NAME' found${NC}"
else
    echo -e "${RED}✗ Cloud SQL instance '$INSTANCE_NAME' not found${NC}"
    echo -e "${RED}Please create the instance first${NC}"
    exit 1
fi
echo ""

# Step 3: Get database credentials
echo -e "${YELLOW}Step 3: Database connection setup${NC}"
echo -e "Enter the postgres user password for instance '$INSTANCE_NAME':"
read -s DB_PASSWORD
echo ""

# Step 4: Test connection
echo -e "${YELLOW}Step 4: Testing database connection...${NC}"
if gcloud sql connect $INSTANCE_NAME --user=postgres --database=$DATABASE_NAME --project=$PROJECT_ID --quiet <<EOF &>/dev/null
$DB_PASSWORD
\q
EOF
then
    echo -e "${GREEN}✓ Database connection successful${NC}"
else
    echo -e "${RED}✗ Database connection failed${NC}"
    echo -e "${RED}Please check your password and try again${NC}"
    exit 1
fi
echo ""

# Step 5: Apply schema
echo -e "${YELLOW}Step 5: Applying database schema...${NC}"
if [ ! -f "database/schema.sql" ]; then
    echo -e "${RED}✗ schema.sql not found in database/ directory${NC}"
    exit 1
fi

gcloud sql connect $INSTANCE_NAME --user=postgres --database=$DATABASE_NAME --project=$PROJECT_ID --quiet <<EOF
$DB_PASSWORD
\i database/schema.sql
EOF

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Schema applied successfully${NC}"
else
    echo -e "${RED}✗ Failed to apply schema${NC}"
    exit 1
fi
echo ""

# Step 6: Load seed data
echo -e "${YELLOW}Step 6: Loading seed data...${NC}"
if [ ! -f "database/seed_data.sql" ]; then
    echo -e "${RED}✗ seed_data.sql not found in database/ directory${NC}"
    exit 1
fi

gcloud sql connect $INSTANCE_NAME --user=postgres --database=$DATABASE_NAME --project=$PROJECT_ID --quiet <<EOF
$DB_PASSWORD
\i database/seed_data.sql
EOF

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Seed data loaded successfully${NC}"
else
    echo -e "${RED}✗ Failed to load seed data${NC}"
    exit 1
fi
echo ""

# Step 7: Verify setup
echo -e "${YELLOW}Step 7: Verifying database setup...${NC}"
gcloud sql connect $INSTANCE_NAME --user=postgres --database=$DATABASE_NAME --project=$PROJECT_ID --quiet <<EOF
$DB_PASSWORD
SELECT 'Services:', COUNT(*) FROM services;
SELECT 'Playbooks:', COUNT(*) FROM playbooks;
SELECT 'Incidents:', COUNT(*) FROM incidents;
SELECT 'Dependencies:', COUNT(*) FROM service_dependencies;
\q
EOF

echo ""
echo -e "${GREEN}======================================${NC}"
echo -e "${GREEN}Database Setup Complete!${NC}"
echo -e "${GREEN}======================================${NC}"
echo ""
echo -e "Next steps:"
echo -e "  1. Verify tables: ${YELLOW}./scripts/verify_database.sh${NC}"
echo -e "  2. Build MCP agents: ${YELLOW}cd mcp-agents/sentinel-agent${NC}"
echo -e "  3. Deploy to Cloud Run"
echo ""
