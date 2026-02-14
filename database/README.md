# Nexus-Zero Database

This directory contains the complete database schema and setup scripts for the Nexus-Zero SRE incident management system.

## 📁 Files

- **schema.sql** - Complete database schema with all tables, indexes, and functions
- **seed_data.sql** - Demo data including services, playbooks, and historical incidents
- **README.md** - This file

## 🗄️ Database Structure

### Core Tables

1. **incidents** - Real-time incident tracking with error details, root cause analysis, and resolution
2. **playbooks** - Historical solutions catalog with success rates and patterns
3. **playbook_solutions** - Ranked remediation actions for each playbook
4. **services** - Service inventory with health status and deployment info
5. **service_dependencies** - Service dependency graph for blast radius calculation
6. **deployment_events** - All deployment history with incident correlation
7. **audit_logs** - Complete audit trail of all agent actions
8. **agent_states** - Persistent memory for agents
9. **config_changes** - Infrastructure configuration change tracking

### Key Features

- **pgvector extension** - Enables similarity matching for incident correlation
- **Blast radius calculation** - Recursive function to determine impact scope
- **Auto-updating timestamps** - Triggers maintain updated_at columns
- **Comprehensive indexing** - Optimized for agent query patterns

## 🚀 Setup Instructions

### Prerequisites

- Google Cloud Shell access
- Cloud SQL instance created (`nexus-zero-db`)
- postgres user password

### Quick Setup

Run from the project root in Google Cloud Shell:

```bash
# Make scripts executable
chmod +x scripts/setup_database.sh
chmod +x scripts/verify_database.sh

# Run setup
./scripts/setup_database.sh

# Verify setup
./scripts/verify_database.sh
```

### Manual Setup

If you prefer to run SQL commands manually:

```bash
# Connect to Cloud SQL
gcloud sql connect nexus-zero-db --user=postgres --database=postgres

# Apply schema
\i database/schema.sql

# Load seed data
\i database/seed_data.sql

# Verify
SELECT COUNT(*) FROM services;
SELECT COUNT(*) FROM playbooks;
```

## 📊 Seed Data Contents

The seed data includes:

- **5 demo services**: payment-api, users-api, orders-api, notification-service, analytics-pipeline
- **4 playbooks**: Database connection pool exhaustion, Memory leak, Rate limiting, Timeouts
- **2 historical incidents**: With full root cause analysis and resolution
- **Service dependency graph**: Shows critical paths between services
- **Agent initial states**: Ready for all 5 agents

## 🔍 Verification

After setup, you should see:

- 9 tables created
- 4 extensions enabled (uuid-ossp, vector, pg_trgm, btree_gist)
- 5 services
- 4 playbooks with multiple solutions
- 2 historical incidents
- 5 agent states

## 🛠️ Key Functions

### calculate_blast_radius(service_id)

Calculates which services would be affected if the target service goes down.

```sql
-- Example: Find blast radius for payment-api
SELECT * FROM calculate_blast_radius(
    (SELECT id FROM services WHERE name = 'payment-api')
);
```

### find_similar_incidents(embedding, threshold, max_results)

Finds historical incidents similar to current error using vector similarity.

```sql
-- Example: Find similar incidents (after generating embedding)
SELECT * FROM find_similar_incidents(
    '[embedding_vector]'::vector(768),
    0.7,
    5
);
```

## 📝 Schema Updates

To add new migrations:

1. Create numbered migration file in `migrations/` directory
2. Apply manually or update setup script
3. Document changes in this README

## 🔐 Security Notes

- Database uses Unix socket connection for security
- All agent actions logged in audit_logs table
- Human approval tracked for sensitive operations
- Service account needs `cloudsql.client` role

## 📖 Table Details

### incidents
Primary incident tracking table. Updated by Detective Agent with root cause, by Executor Agent with resolution.

### playbooks
Historical knowledge base. Historian Agent queries this to find similar past incidents.

### service_dependencies
Enables Mediator Agent to calculate blast radius before approving remediations.

### audit_logs
Complete traceability. Every agent action (detect, analyze, recommend, execute) is logged here.

## 🎯 Next Steps

After database setup:

1. Build Sentinel Agent (monitors for incidents)
2. Build Detective Agent (analyzes root cause)
3. Build Historian Agent (matches to playbooks)
4. Build Mediator Agent (assesses risk)
5. Build Executor Agent (performs remediation)

See `/mcp-agents/` directory for agent implementation.
