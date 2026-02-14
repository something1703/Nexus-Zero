-- ============================================
-- NEXUS-ZERO SEED DATA
-- Demo data for testing and demonstration
-- ============================================

-- ============================================
-- Insert Demo Services
-- ============================================

INSERT INTO services (name, type, environment, region, current_version, rollback_safety_score, status) VALUES
('payment-api', 'api', 'production', 'us-central1', 'v2.1.4', 0.85, 'healthy'),
('users-api', 'api', 'production', 'us-central1', 'v1.3.2', 0.90, 'healthy'),
('orders-api', 'api', 'production', 'us-central1', 'v1.8.1', 0.80, 'healthy'),
('notification-service', 'queue', 'production', 'us-central1', 'v1.2.0', 0.95, 'healthy'),
('analytics-pipeline', 'batch', 'production', 'us-central1', 'v0.9.3', 0.70, 'healthy');


-- ============================================
-- Insert Service Dependencies
-- ============================================

-- orders-api depends on payment-api (critical dependency)
INSERT INTO service_dependencies (service_id, depends_on_service_id, dependency_type, criticality)
SELECT 
    (SELECT id FROM services WHERE name = 'orders-api'),
    (SELECT id FROM services WHERE name = 'payment-api'),
    'api_call',
    'critical';

-- orders-api depends on users-api (high dependency)
INSERT INTO service_dependencies (service_id, depends_on_service_id, dependency_type, criticality)
SELECT 
    (SELECT id FROM services WHERE name = 'orders-api'),
    (SELECT id FROM services WHERE name = 'users-api'),
    'api_call',
    'high';

-- notification-service depends on orders-api (medium dependency)
INSERT INTO service_dependencies (service_id, depends_on_service_id, dependency_type, criticality)
SELECT 
    (SELECT id FROM services WHERE name = 'notification-service'),
    (SELECT id FROM services WHERE name = 'orders-api'),
    'event_stream',
    'medium';

-- analytics-pipeline depends on payment-api (low dependency)
INSERT INTO service_dependencies (service_id, depends_on_service_id, dependency_type, criticality)
SELECT 
    (SELECT id FROM services WHERE name = 'analytics-pipeline'),
    (SELECT id FROM services WHERE name = 'payment-api'),
    'database',
    'low';


-- ============================================
-- Insert Demo Playbooks
-- ============================================

INSERT INTO playbooks (name, description, trigger_pattern, service_pattern, category, success_rate, avg_resolution_time_minutes, times_used, created_by)
VALUES
('Database Connection Pool Exhaustion', 
 'Common issue when DB connection pool is misconfigured or traffic spikes. Usually caused by sudden traffic increase or config change.',
 'connection pool exhausted|too many clients|FATAL: remaining connection slots',
 'payment-api|users-api|orders-api',
 'database',
 95.00,
 3,
 12,
 'sre-team');

INSERT INTO playbooks (name, description, trigger_pattern, service_pattern, category, success_rate, avg_resolution_time_minutes, times_used, created_by)
VALUES
('Memory Leak - Gradual OOM',
 'Service gradually consumes memory until OOMKilled by container runtime. Common in services with poor memory management.',
 'out of memory|OOMKilled|memory limit exceeded|cannot allocate memory',
 '.*-api',
 'resource_exhaustion',
 88.00,
 8,
 7,
 'sre-team');

INSERT INTO playbooks (name, description, trigger_pattern, service_pattern, category, success_rate, avg_resolution_time_minutes, times_used, created_by)
VALUES
('Rate Limit Exceeded on Downstream Service',
 'Service hitting rate limits on external API or internal service. Requires backoff strategy or quota increase.',
 'rate limit|429|quota exceeded|too many requests',
 'notification-service|analytics-pipeline',
 'external_dependency',
 92.00,
 5,
 18,
 'sre-team');

INSERT INTO playbooks (name, description, trigger_pattern, service_pattern, category, success_rate, avg_resolution_time_minutes, times_used, created_by)
VALUES
('Timeout in Payment Processing',
 'Payment API timing out on external payment gateway. Usually caused by network issues or gateway degradation.',
 'timeout|gateway timeout|504|payment.*timeout',
 'payment-api',
 'timeout',
 85.00,
 10,
 5,
 'sre-team');


-- ============================================
-- Insert Solutions for Playbooks
-- ============================================

-- Solutions for Database Connection Pool Exhaustion
INSERT INTO playbook_solutions (playbook_id, rank, action_type, action_details, prerequisites, post_checks, expected_resolution_time_minutes)
SELECT 
    id,
    1,
    'rollback',
    '{"target_version": "previous", "reason": "revert connection pool config change", "safe": true}',
    '{"has_previous_version": true, "no_schema_changes": true}',
    '{"check_error_rate": true, "check_latency": true, "monitor_duration_minutes": 5}',
    3
FROM playbooks WHERE name = 'Database Connection Pool Exhaustion';

INSERT INTO playbook_solutions (playbook_id, rank, action_type, action_details, prerequisites, post_checks, expected_resolution_time_minutes)
SELECT 
    id,
    2,
    'scale_up',
    '{"resource": "database_connections", "action": "increase_pool_size", "from": 10, "to": 50, "requires_restart": false}',
    '{"database_has_capacity": true, "current_pool_size_known": true}',
    '{"verify_connection_count": true, "check_error_rate": true}',
    5
FROM playbooks WHERE name = 'Database Connection Pool Exhaustion';

INSERT INTO playbook_solutions (playbook_id, rank, action_type, action_details, prerequisites, post_checks, expected_resolution_time_minutes)
SELECT 
    id,
    3,
    'config_change',
    '{"parameter": "max_connections", "action": "increase", "from": 100, "to": 200, "apply_method": "hot_reload"}',
    '{"requires_manager_approval": true}',
    '{"verify_config_applied": true, "monitor_connection_usage": true}',
    7
FROM playbooks WHERE name = 'Database Connection Pool Exhaustion';


-- Solutions for Memory Leak - Gradual OOM
INSERT INTO playbook_solutions (playbook_id, rank, action_type, action_details, prerequisites, post_checks, expected_resolution_time_minutes)
SELECT 
    id,
    1,
    'restart',
    '{"action": "rolling_restart", "drain_time_seconds": 30, "wait_for_healthy": true}',
    '{"service_has_multiple_replicas": true, "can_drain_traffic": true}',
    '{"verify_memory_usage_stable": true, "monitor_duration_minutes": 15}',
    8
FROM playbooks WHERE name = 'Memory Leak - Gradual OOM';

INSERT INTO playbook_solutions (playbook_id, rank, action_type, action_details, prerequisites, post_checks, expected_resolution_time_minutes)
SELECT 
    id,
    2,
    'scale_up',
    '{"resource": "memory", "action": "increase_limit", "from": "512Mi", "to": "1Gi", "temporary": true}',
    '{"cluster_has_capacity": true}',
    '{"monitor_memory_growth": true, "alert_if_continues_growing": true}',
    10
FROM playbooks WHERE name = 'Memory Leak - Gradual OOM';

INSERT INTO playbook_solutions (playbook_id, rank, action_type, action_details, prerequisites, post_checks, expected_resolution_time_minutes)
SELECT 
    id,
    3,
    'rollback',
    '{"target_version": "last_known_good", "reason": "memory leak introduced in recent deploy"}',
    '{"has_previous_version": true, "memory_usage_was_stable": true}',
    '{"verify_memory_usage": true, "monitor_duration_minutes": 30}',
    5
FROM playbooks WHERE name = 'Memory Leak - Gradual OOM';


-- Solutions for Rate Limit Exceeded
INSERT INTO playbook_solutions (playbook_id, rank, action_type, action_details, prerequisites, post_checks, expected_resolution_time_minutes)
SELECT 
    id,
    1,
    'config_change',
    '{"parameter": "rate_limit_backoff", "action": "enable_exponential_backoff", "initial_delay_ms": 1000, "max_delay_ms": 30000}',
    '{"service_supports_backoff": true}',
    '{"verify_retry_behavior": true, "check_success_rate": true}',
    5
FROM playbooks WHERE name = 'Rate Limit Exceeded on Downstream Service';

INSERT INTO playbook_solutions (playbook_id, rank, action_type, action_details, prerequisites, post_checks, expected_resolution_time_minutes)
SELECT 
    id,
    2,
    'scale_down',
    '{"resource": "request_rate", "action": "reduce_concurrency", "from": 100, "to": 50}',
    '{"can_reduce_throughput": true, "has_rate_limit_config": true}',
    '{"verify_within_limits": true, "monitor_queue_depth": true}',
    3
FROM playbooks WHERE name = 'Rate Limit Exceeded on Downstream Service';


-- Solutions for Timeout in Payment Processing
INSERT INTO playbook_solutions (playbook_id, rank, action_type, action_details, prerequisites, post_checks, expected_resolution_time_minutes)
SELECT 
    id,
    1,
    'config_change',
    '{"parameter": "timeout", "action": "increase", "from_seconds": 5, "to_seconds": 15}',
    '{"acceptable_latency_increase": true}',
    '{"verify_timeout_reduced": true, "check_p95_latency": true}',
    3
FROM playbooks WHERE name = 'Timeout in Payment Processing';

INSERT INTO playbook_solutions (playbook_id, rank, action_type, action_details, prerequisites, post_checks, expected_resolution_time_minutes)
SELECT 
    id,
    2,
    'circuit_breaker',
    '{"action": "enable_circuit_breaker", "failure_threshold": 5, "timeout_seconds": 30, "half_open_requests": 3}',
    '{"service_supports_circuit_breaker": true}',
    '{"verify_circuit_breaker_active": true, "monitor_fallback_behavior": true}',
    10
FROM playbooks WHERE name = 'Timeout in Payment Processing';


-- ============================================
-- Insert Historical Incident (Demo)
-- ============================================

INSERT INTO incidents (
    service_name,
    severity,
    status,
    error_signature,
    error_message,
    stack_trace,
    region,
    environment,
    first_seen_at,
    last_seen_at,
    resolved_at,
    root_cause,
    suspect_commit_id,
    suspect_file_path,
    confidence_score,
    resolution_action,
    resolution_time_seconds
) VALUES (
    'payment-api',
    'critical',
    'closed',
    'db_pool_exhaustion_500',
    'FATAL: remaining connection slots are reserved for non-replication superuser connections',
    'Error: connection pool exhausted
  at Database.connect() [/app/database.js:45]
  at PaymentService.processPayment() [/app/services/payment.js:102]
  at PaymentController.handleRequest() [/app/controllers/payment.js:23]',
    'us-central1',
    'production',
    NOW() - INTERVAL '45 days',
    NOW() - INTERVAL '45 days' + INTERVAL '2 minutes',
    NOW() - INTERVAL '45 days' + INTERVAL '3 minutes',
    'Database connection pool size was increased from 10 to 100 in commit #402abc123, but max_connections on PostgreSQL was still set to 10, causing immediate exhaustion under load.',
    '402abc123def456',
    'config/database.js',
    0.92,
    'rollback_to_v2.1.3',
    180
);

-- Insert another historical incident (Memory leak example)
INSERT INTO incidents (
    service_name,
    severity,
    status,
    error_signature,
    error_message,
    stack_trace,
    region,
    environment,
    first_seen_at,
    last_seen_at,
    resolved_at,
    root_cause,
    suspect_commit_id,
    suspect_file_path,
    confidence_score,
    resolution_action,
    resolution_time_seconds
) VALUES (
    'users-api',
    'high',
    'closed',
    'oom_killed_137',
    'Container killed with exit code 137 (OOMKilled)',
    'Error: JavaScript heap out of memory
  at Array.map() [/app/services/user.js:156]
  at UserService.getAllUsers() [/app/services/user.js:145]
  at UserController.list() [/app/controllers/user.js:67]',
    'us-central1',
    'production',
    NOW() - INTERVAL '30 days',
    NOW() - INTERVAL '30 days' + INTERVAL '12 hours',
    NOW() - INTERVAL '30 days' + INTERVAL '12 hours' + INTERVAL '8 minutes',
    'Memory leak introduced in commit #789xyz where user cache was not properly cleared after processing large datasets.',
    '789xyz456abc123',
    'services/user.js',
    0.88,
    'rolling_restart',
    480
);


-- ============================================
-- Insert Deployment Events (Demo)
-- ============================================

INSERT INTO deployment_events (
    service_id,
    commit_id,
    version,
    deployed_by,
    deployment_type,
    status,
    caused_incident,
    related_incident_id,
    started_at,
    completed_at
)
SELECT 
    (SELECT id FROM services WHERE name = 'payment-api'),
    '402abc123def456',
    'v2.1.4',
    'alice@example.com',
    'deploy',
    'rolled_back',
    TRUE,
    (SELECT id FROM incidents WHERE error_signature = 'db_pool_exhaustion_500'),
    NOW() - INTERVAL '45 days',
    NOW() - INTERVAL '45 days' + INTERVAL '5 minutes';

INSERT INTO deployment_events (
    service_id,
    commit_id,
    version,
    deployed_by,
    deployment_type,
    status,
    caused_incident,
    started_at,
    completed_at
)
SELECT 
    (SELECT id FROM services WHERE name = 'users-api'),
    '789xyz456abc123',
    'v1.3.2',
    'bob@example.com',
    'deploy',
    'success',
    TRUE,
    NOW() - INTERVAL '30 days',
    NOW() - INTERVAL '30 days' + INTERVAL '3 minutes';


-- ============================================
-- Insert Agent Initial States
-- ============================================

INSERT INTO agent_states (agent_name, state) VALUES
('sentinel', '{"last_scan_time": null, "baseline_metrics": {}, "active_monitors": []}'),
('detective', '{"active_investigations": [], "correlation_cache": {}}'),
('historian', '{"cache": {}, "last_playbook_update": null}'),
('mediator', '{"pending_approvals": [], "risk_thresholds": {"critical": 0.8, "high": 0.6}}'),
('executor', '{"active_remediations": [], "last_execution_time": null}');


-- ============================================
-- Insert Sample Audit Logs
-- ============================================

INSERT INTO audit_logs (
    agent_name,
    action_type,
    action_details,
    incident_id,
    service_id,
    human_approved,
    approved_by,
    approved_at,
    status,
    result,
    created_at,
    completed_at
)
SELECT 
    'executor',
    'rollback',
    '{"from_version": "v2.1.4", "to_version": "v2.1.3", "reason": "database pool exhaustion"}',
    (SELECT id FROM incidents WHERE error_signature = 'db_pool_exhaustion_500'),
    (SELECT id FROM services WHERE name = 'payment-api'),
    TRUE,
    'sre-oncall@example.com',
    NOW() - INTERVAL '45 days' + INTERVAL '2.5 minutes',
    'success',
    '{"rollback_duration_seconds": 45, "error_rate_after": 0, "verification": "passed"}',
    NOW() - INTERVAL '45 days' + INTERVAL '2 minutes',
    NOW() - INTERVAL '45 days' + INTERVAL '3 minutes';

INSERT INTO audit_logs (
    agent_name,
    action_type,
    action_details,
    incident_id,
    human_approved,
    status,
    result,
    created_at,
    completed_at
)
SELECT 
    'detective',
    'analyze_root_cause',
    '{"method": "commit_correlation", "time_window_minutes": 30}',
    (SELECT id FROM incidents WHERE error_signature = 'db_pool_exhaustion_500'),
    FALSE,
    'success',
    '{"root_cause_found": true, "confidence": 0.92, "suspect_commit": "402abc123def456"}',
    NOW() - INTERVAL '45 days' + INTERVAL '30 seconds',
    NOW() - INTERVAL '45 days' + INTERVAL '1 minute';


-- ============================================
-- Verification Queries
-- ============================================

-- Count all created objects
DO $$
DECLARE
    service_count INTEGER;
    playbook_count INTEGER;
    incident_count INTEGER;
    dependency_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO service_count FROM services;
    SELECT COUNT(*) INTO playbook_count FROM playbooks;
    SELECT COUNT(*) INTO incident_count FROM incidents;
    SELECT COUNT(*) INTO dependency_count FROM service_dependencies;
    
    RAISE NOTICE 'Seed data loaded successfully:';
    RAISE NOTICE '  Services: %', service_count;
    RAISE NOTICE '  Playbooks: %', playbook_count;
    RAISE NOTICE '  Incidents: %', incident_count;
    RAISE NOTICE '  Dependencies: %', dependency_count;
END $$;
