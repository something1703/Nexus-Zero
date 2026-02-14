-- ============================================
-- NEXUS-ZERO DATABASE SCHEMA
-- Complete schema for SRE incident management system
-- ============================================

-- ============================================
-- Step 1: Enable Required Extensions
-- ============================================

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Enable pgvector for similarity matching (Historian Agent)
CREATE EXTENSION IF NOT EXISTS vector;

-- Enable pg_trgm for fuzzy text matching
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Enable timestamp functions
CREATE EXTENSION IF NOT EXISTS btree_gist;


-- ============================================
-- Step 2: Core Tables
-- ============================================

-- Incidents Table (Real-time incident tracking)
CREATE TABLE incidents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    -- Incident Metadata
    service_name VARCHAR(255) NOT NULL,
    severity VARCHAR(20) NOT NULL CHECK (severity IN ('critical', 'high', 'medium', 'low')),
    status VARCHAR(20) NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'investigating', 'resolved', 'closed')),
    
    -- Error Details
    error_signature VARCHAR(255) NOT NULL,
    error_message TEXT,
    stack_trace TEXT,
    error_count INTEGER DEFAULT 1,
    
    -- Timestamps
    first_seen_at TIMESTAMP NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMP NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMP,
    
    -- Location
    region VARCHAR(50),
    environment VARCHAR(50) DEFAULT 'production',
    
    -- Root Cause Analysis (filled by Detective Agent)
    root_cause TEXT,
    suspect_commit_id VARCHAR(255),
    suspect_file_path VARCHAR(500),
    confidence_score DECIMAL(3,2),
    
    -- Resolution (filled by Executor Agent)
    resolution_action VARCHAR(100),
    resolution_time_seconds INTEGER,
    
    -- Embeddings for similarity matching
    error_embedding vector(768),
    
    -- Metadata
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Indexes for fast queries
CREATE INDEX idx_incidents_service ON incidents(service_name);
CREATE INDEX idx_incidents_severity ON incidents(severity);
CREATE INDEX idx_incidents_status ON incidents(status);
CREATE INDEX idx_incidents_signature ON incidents(error_signature);
CREATE INDEX idx_incidents_created_at ON incidents(created_at DESC);
CREATE INDEX idx_incidents_embedding ON incidents USING ivfflat (error_embedding vector_cosine_ops) WITH (lists = 100);


-- Playbooks Table (Historical Solutions)
CREATE TABLE playbooks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    -- Matching Pattern
    name VARCHAR(255) NOT NULL,
    description TEXT,
    trigger_pattern VARCHAR(255) NOT NULL,
    service_pattern VARCHAR(255),
    
    -- Solution metadata
    category VARCHAR(100),
    success_rate DECIMAL(5,2) DEFAULT 0.00,
    avg_resolution_time_minutes INTEGER,
    times_used INTEGER DEFAULT 0,
    
    -- Embeddings for similarity matching
    pattern_embedding vector(768),
    
    -- Metadata
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    created_by VARCHAR(255)
);

CREATE INDEX idx_playbooks_pattern ON playbooks(trigger_pattern);
CREATE INDEX idx_playbooks_category ON playbooks(category);
CREATE INDEX idx_playbooks_embedding ON playbooks USING ivfflat (pattern_embedding vector_cosine_ops) WITH (lists = 100);


-- Playbook Solutions Table (Ranked solutions per playbook)
CREATE TABLE playbook_solutions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    playbook_id UUID NOT NULL REFERENCES playbooks(id) ON DELETE CASCADE,
    
    -- Solution Details
    rank INTEGER NOT NULL,
    action_type VARCHAR(100) NOT NULL,
    action_details JSONB NOT NULL,
    
    -- Prerequisites
    prerequisites JSONB,
    
    -- Validation
    post_checks JSONB,
    expected_resolution_time_minutes INTEGER,
    
    -- Metadata
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_playbook_solutions_playbook ON playbook_solutions(playbook_id, rank);


-- Service Topology Table (Dependency Graph)
CREATE TABLE services (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    -- Service Info
    name VARCHAR(255) NOT NULL UNIQUE,
    type VARCHAR(100),
    environment VARCHAR(50) DEFAULT 'production',
    region VARCHAR(50),
    
    -- Health
    status VARCHAR(20) DEFAULT 'healthy' CHECK (status IN ('healthy', 'degraded', 'down')),
    last_health_check TIMESTAMP,
    
    -- Deployment Info
    current_version VARCHAR(50),
    last_deployment_at TIMESTAMP,
    last_deployment_commit VARCHAR(255),
    
    -- Risk Scoring
    rollback_safety_score DECIMAL(3,2),
    incident_count_30d INTEGER DEFAULT 0,
    
    -- Metadata
    metadata JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_services_name ON services(name);
CREATE INDEX idx_services_status ON services(status);


-- Service Dependencies Table (Who depends on whom)
CREATE TABLE service_dependencies (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    -- Dependency Relationship
    service_id UUID NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    depends_on_service_id UUID NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    
    -- Dependency Type
    dependency_type VARCHAR(50) NOT NULL,
    criticality VARCHAR(20) NOT NULL CHECK (criticality IN ('critical', 'high', 'medium', 'low')),
    
    -- Metadata
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    
    -- Constraints
    CONSTRAINT no_self_dependency CHECK (service_id != depends_on_service_id),
    CONSTRAINT unique_dependency UNIQUE (service_id, depends_on_service_id)
);

CREATE INDEX idx_service_deps_service ON service_dependencies(service_id);
CREATE INDEX idx_service_deps_depends_on ON service_dependencies(depends_on_service_id);


-- Deployment Events Table (Track all deployments)
CREATE TABLE deployment_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    -- Deployment Info
    service_id UUID NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    commit_id VARCHAR(255) NOT NULL,
    version VARCHAR(50),
    
    -- Deployment Details
    deployed_by VARCHAR(255),
    deployment_type VARCHAR(50),
    status VARCHAR(20) NOT NULL CHECK (status IN ('pending', 'in_progress', 'success', 'failed', 'rolled_back')),
    
    -- Correlation to Incidents
    caused_incident BOOLEAN DEFAULT FALSE,
    related_incident_id UUID REFERENCES incidents(id),
    
    -- Timestamps
    started_at TIMESTAMP NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMP,
    
    -- Metadata
    deployment_metadata JSONB
);

CREATE INDEX idx_deployment_events_service ON deployment_events(service_id);
CREATE INDEX idx_deployment_events_commit ON deployment_events(commit_id);
CREATE INDEX idx_deployment_events_started_at ON deployment_events(started_at DESC);


-- Audit Logs Table (Every agent action)
CREATE TABLE audit_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    -- Action Details
    agent_name VARCHAR(100) NOT NULL,
    action_type VARCHAR(100) NOT NULL,
    action_details JSONB NOT NULL,
    
    -- Related Entities
    incident_id UUID REFERENCES incidents(id),
    service_id UUID REFERENCES services(id),
    
    -- Authorization
    human_approved BOOLEAN DEFAULT FALSE,
    approved_by VARCHAR(255),
    approved_at TIMESTAMP,
    
    -- Outcome
    status VARCHAR(20) NOT NULL CHECK (status IN ('pending', 'success', 'failed', 'rejected')),
    result JSONB,
    error_message TEXT,
    
    -- Timestamps
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMP
);

CREATE INDEX idx_audit_logs_agent ON audit_logs(agent_name);
CREATE INDEX idx_audit_logs_incident ON audit_logs(incident_id);
CREATE INDEX idx_audit_logs_created_at ON audit_logs(created_at DESC);


-- Agent State Table (Persistent memory for agents)
CREATE TABLE agent_states (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    -- Agent Info
    agent_name VARCHAR(100) NOT NULL UNIQUE,
    
    -- State
    state JSONB NOT NULL,
    
    -- Metadata
    last_updated TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_agent_states_agent ON agent_states(agent_name);


-- Config Changes Table (Track infrastructure changes)
CREATE TABLE config_changes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    -- Change Details
    service_id UUID REFERENCES services(id),
    change_type VARCHAR(100) NOT NULL,
    old_value JSONB,
    new_value JSONB,
    
    -- Who made the change
    changed_by VARCHAR(255),
    change_reason TEXT,
    
    -- Correlation
    related_incident_id UUID REFERENCES incidents(id),
    
    -- Timestamps
    applied_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_config_changes_service ON config_changes(service_id);
CREATE INDEX idx_config_changes_applied_at ON config_changes(applied_at DESC);


-- ============================================
-- Step 3: Helper Functions
-- ============================================

-- Auto-update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply to relevant tables
CREATE TRIGGER update_incidents_updated_at BEFORE UPDATE ON incidents
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_services_updated_at BEFORE UPDATE ON services
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_playbooks_updated_at BEFORE UPDATE ON playbooks
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();


-- Function to calculate blast radius
CREATE OR REPLACE FUNCTION calculate_blast_radius(target_service_id UUID)
RETURNS TABLE(affected_service_id UUID, service_name VARCHAR, hop_count INTEGER) AS $$
BEGIN
    RETURN QUERY
    WITH RECURSIVE dependency_tree AS (
        -- Base case: direct dependencies
        SELECT 
            sd.service_id,
            s.name,
            1 as depth
        FROM service_dependencies sd
        JOIN services s ON sd.service_id = s.id
        WHERE sd.depends_on_service_id = target_service_id
        
        UNION
        
        -- Recursive case: transitive dependencies
        SELECT 
            sd.service_id,
            s.name,
            dt.depth + 1
        FROM service_dependencies sd
        JOIN services s ON sd.service_id = s.id
        JOIN dependency_tree dt ON sd.depends_on_service_id = dt.service_id
        WHERE dt.depth < 5
    )
    SELECT DISTINCT service_id, name, depth
    FROM dependency_tree
    ORDER BY depth, name;
END;
$$ LANGUAGE plpgsql;


-- Function to find similar incidents using pgvector
CREATE OR REPLACE FUNCTION find_similar_incidents(
    query_embedding vector(768),
    similarity_threshold FLOAT DEFAULT 0.7,
    max_results INTEGER DEFAULT 5
)
RETURNS TABLE(
    incident_id UUID,
    service_name VARCHAR,
    error_signature VARCHAR,
    similarity_score FLOAT
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        i.id,
        i.service_name,
        i.error_signature,
        1 - (i.error_embedding <=> query_embedding) as similarity
    FROM incidents i
    WHERE i.error_embedding IS NOT NULL
        AND i.status = 'closed'
        AND (1 - (i.error_embedding <=> query_embedding)) >= similarity_threshold
    ORDER BY i.error_embedding <=> query_embedding
    LIMIT max_results;
END;
$$ LANGUAGE plpgsql;
