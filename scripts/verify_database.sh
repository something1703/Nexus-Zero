#!/bin/bash

# ============================================
# Nexus-Zero Database Verification Script
# Checks that all tables and data are correct
# ============================================

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Configuration
PROJECT_ID="nexus-zero-sre"
INSTANCE_NAME="nexus-zero-db"
DATABASE_NAME="postgres"

echo -e "${GREEN}======================================${NC}"
echo -e "${GREEN}Database Verification${NC}"
echo -e "${GREEN}======================================${NC}"
echo ""

echo -e "${YELLOW}Enter postgres password:${NC}"
read -s DB_PASSWORD
echo ""

echo -e "${YELLOW}Running verification checks...${NC}"
echo ""

# Run verification queries
gcloud sql connect $INSTANCE_NAME --user=postgres --database=$DATABASE_NAME --project=$PROJECT_ID --quiet <<EOF
$DB_PASSWORD

-- Check extensions
\echo '=== Extensions ==='
SELECT extname, extversion FROM pg_extension WHERE extname IN ('uuid-ossp', 'vector', 'pg_trgm', 'btree_gist');

-- Check tables
\echo ''
\echo '=== Tables ==='
SELECT table_name FROM information_schema.tables 
WHERE table_schema = 'public' 
ORDER BY table_name;

-- Check data counts
\echo ''
\echo '=== Data Counts ==='
SELECT 'Services' as table_name, COUNT(*) as count FROM services
UNION ALL
SELECT 'Playbooks', COUNT(*) FROM playbooks
UNION ALL
SELECT 'Playbook Solutions', COUNT(*) FROM playbook_solutions
UNION ALL
SELECT 'Service Dependencies', COUNT(*) FROM service_dependencies
UNION ALL
SELECT 'Incidents', COUNT(*) FROM incidents
UNION ALL
SELECT 'Deployment Events', COUNT(*) FROM deployment_events
UNION ALL
SELECT 'Audit Logs', COUNT(*) FROM audit_logs
UNION ALL
SELECT 'Agent States', COUNT(*) FROM agent_states;

-- Check service details
\echo ''
\echo '=== Services Overview ==='
SELECT name, type, current_version, status FROM services ORDER BY name;

-- Check playbook details
\echo ''
\echo '=== Playbooks Overview ==='
SELECT name, category, success_rate, times_used FROM playbooks ORDER BY success_rate DESC;

-- Test blast radius function
\echo ''
\echo '=== Blast Radius Test (payment-api) ==='
SELECT service_name, hop_count 
FROM calculate_blast_radius((SELECT id FROM services WHERE name = 'payment-api'))
ORDER BY hop_count, service_name;

-- Check indexes
\echo ''
\echo '=== Key Indexes ==='
SELECT tablename, indexname 
FROM pg_indexes 
WHERE schemaname = 'public' AND indexname LIKE 'idx_%'
ORDER BY tablename, indexname;

\q
EOF

echo ""
echo -e "${GREEN}======================================${NC}"
echo -e "${GREEN}Verification Complete${NC}"
echo -e "${GREEN}======================================${NC}"
echo ""
