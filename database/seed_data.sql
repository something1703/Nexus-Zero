-- ============================================
-- NEXUS-ZERO SEED DATA (v2)
-- Realistic demo data for SRE incident management
-- Includes chaos service entries, rich playbooks,
-- and historical incidents for pattern matching.
-- ============================================

-- ============================================
-- Insert Demo Services (matches Cloud Run deployments)
-- ============================================

INSERT INTO services (name, type, environment, region, current_version, rollback_safety_score, status) VALUES
('order-api', 'api', 'production', 'us-central1', 'v1.8.1', 0.80, 'healthy'),
('payment-api', 'api', 'production', 'us-central1', 'v2.1.4', 0.85, 'healthy'),
('notification-api', 'api', 'production', 'us-central1', 'v1.2.0', 0.90, 'healthy'),
('users-api', 'api', 'production', 'us-central1', 'v1.3.2', 0.90, 'healthy'),
('analytics-pipeline', 'batch', 'production', 'us-central1', 'v0.9.3', 0.70, 'healthy')
ON CONFLICT (name) DO UPDATE SET
    current_version = EXCLUDED.current_version,
    rollback_safety_score = EXCLUDED.rollback_safety_score,
    status = EXCLUDED.status,
    updated_at = NOW();


-- ============================================
-- Insert Service Dependencies (Realistic Topology)
-- ============================================
-- Topology:
--   notification-api → order-api → payment-api
--   analytics-pipeline → order-api (data feed)
--   analytics-pipeline → payment-api (data feed)
--   order-api → users-api (auth check)

-- order-api → payment-api (critical: every order triggers payment)
INSERT INTO service_dependencies (service_id, depends_on_service_id, dependency_type, criticality)
SELECT
    (SELECT id FROM services WHERE name = 'order-api'),
    (SELECT id FROM services WHERE name = 'payment-api'),
    'api_call',
    'critical'
ON CONFLICT ON CONSTRAINT unique_dependency DO NOTHING;

-- order-api → users-api (high: auth and user lookup)
INSERT INTO service_dependencies (service_id, depends_on_service_id, dependency_type, criticality)
SELECT
    (SELECT id FROM services WHERE name = 'order-api'),
    (SELECT id FROM services WHERE name = 'users-api'),
    'api_call',
    'high'
ON CONFLICT ON CONSTRAINT unique_dependency DO NOTHING;

-- notification-api → order-api (critical: needs order data to send notifications)
INSERT INTO service_dependencies (service_id, depends_on_service_id, dependency_type, criticality)
SELECT
    (SELECT id FROM services WHERE name = 'notification-api'),
    (SELECT id FROM services WHERE name = 'order-api'),
    'event_stream',
    'critical'
ON CONFLICT ON CONSTRAINT unique_dependency DO NOTHING;

-- analytics-pipeline → order-api (medium: data ingestion)
INSERT INTO service_dependencies (service_id, depends_on_service_id, dependency_type, criticality)
SELECT
    (SELECT id FROM services WHERE name = 'analytics-pipeline'),
    (SELECT id FROM services WHERE name = 'order-api'),
    'database',
    'medium'
ON CONFLICT ON CONSTRAINT unique_dependency DO NOTHING;

-- analytics-pipeline → payment-api (low: revenue reports)
INSERT INTO service_dependencies (service_id, depends_on_service_id, dependency_type, criticality)
SELECT
    (SELECT id FROM services WHERE name = 'analytics-pipeline'),
    (SELECT id FROM services WHERE name = 'payment-api'),
    'database',
    'low'
ON CONFLICT ON CONSTRAINT unique_dependency DO NOTHING;


-- ============================================
-- Insert Playbooks (8 comprehensive playbooks)
-- ============================================

-- 1. DB Connection Pool Exhaustion
INSERT INTO playbooks (name, description, trigger_pattern, service_pattern, category, success_rate, avg_resolution_time_minutes, times_used, created_by)
VALUES
('Database Connection Pool Exhaustion',
 'Common issue when DB connection pool is misconfigured or traffic spikes. Usually caused by sudden traffic increase, pool config change, or connection leak. The most common root cause is a deployment that changed pool settings without updating max_connections.',
 'connection pool exhausted|too many clients|FATAL: remaining connection slots|DatabasePoolExhaustion|connection_pool',
 'order-api|payment-api|users-api',
 'database',
 95.00, 3, 12, 'sre-team')
ON CONFLICT DO NOTHING;

-- 2. Memory Leak / OOMKilled
INSERT INTO playbooks (name, description, trigger_pattern, service_pattern, category, success_rate, avg_resolution_time_minutes, times_used, created_by)
VALUES
('Memory Leak - Gradual OOM',
 'Service gradually consumes memory until OOMKilled by container runtime. Common in services with poor cache eviction, unbounded data structures, or circular references. Immediate action: rolling restart. Long-term: identify leak source in heap dump.',
 'out of memory|OOMKilled|memory limit exceeded|cannot allocate memory|OutOfMemoryError|exit code 137',
 'payment-api|order-api|users-api',
 'resource_exhaustion',
 88.00, 8, 7, 'sre-team')
ON CONFLICT DO NOTHING;

-- 3. Rate Limit Exceeded
INSERT INTO playbooks (name, description, trigger_pattern, service_pattern, category, success_rate, avg_resolution_time_minutes, times_used, created_by)
VALUES
('Rate Limit Exceeded on External Provider',
 'Service hitting rate limits on external API (SendGrid, Stripe, Twilio) or internal service. Requires backoff strategy, request batching, or quota increase. Check if there is a retry storm amplifying the problem.',
 'rate limit|429|quota exceeded|too many requests|RateLimitExceeded|Retry-After',
 'notification-api|payment-api',
 'external_dependency',
 92.00, 5, 18, 'sre-team')
ON CONFLICT DO NOTHING;

-- 4. Payment Gateway Timeout
INSERT INTO playbooks (name, description, trigger_pattern, service_pattern, category, success_rate, avg_resolution_time_minutes, times_used, created_by)
VALUES
('Timeout in Payment Processing',
 'Payment API timing out on upstream payment gateway (Stripe, PayPal). Usually caused by network issues, gateway degradation, or misconfigured timeout values. Check gateway status page first.',
 'timeout|gateway timeout|504|payment.*timeout|GatewayTimeout|Stripe.*timeout|not responding',
 'payment-api',
 'timeout',
 85.00, 10, 5, 'sre-team')
ON CONFLICT DO NOTHING;

-- 5. Circuit Breaker Tripped
INSERT INTO playbooks (name, description, trigger_pattern, service_pattern, category, success_rate, avg_resolution_time_minutes, times_used, created_by)
VALUES
('Circuit Breaker Tripped on Upstream Service',
 'Circuit breaker opened due to consecutive failures calling upstream service. All requests are being rejected without attempting upstream call. Root cause is usually in the upstream service, not this one. Investigate upstream first, then reset circuit breaker.',
 'circuit breaker|CircuitBreakerOpen|upstream.*fail|connection refused|UpstreamConnectionRefused|consecutive failures',
 'notification-api|order-api',
 'cascade_failure',
 90.00, 6, 9, 'sre-team')
ON CONFLICT DO NOTHING;

-- 6. Cascading Timeout
INSERT INTO playbooks (name, description, trigger_pattern, service_pattern, category, success_rate, avg_resolution_time_minutes, times_used, created_by)
VALUES
('Cascading Timeout Across Service Chain',
 'Timeout propagating through service dependency chain. Service A times out waiting for B, which times out waiting for C. Clients see increased latency and eventual 504s. Fix the root service first, then retry upstream.',
 'DownstreamTimeout|cascading.*timeout|downstream.*service|timeout calling|502|Bad Gateway',
 'order-api|notification-api',
 'cascade_failure',
 82.00, 12, 4, 'sre-team')
ON CONFLICT DO NOTHING;

-- 7. Deployment Rollback Required
INSERT INTO playbooks (name, description, trigger_pattern, service_pattern, category, success_rate, avg_resolution_time_minutes, times_used, created_by)
VALUES
('Failed Deployment - Immediate Rollback',
 'Recent deployment caused errors. Rollback to previous known-good version. Check deployment events for the suspect commit. Verify that the rollback target version does not have schema incompatibilities.',
 'deploy.*fail|rollback|version.*mismatch|regression|new.*version.*error',
 '.*',
 'deployment',
 97.00, 2, 22, 'sre-team')
ON CONFLICT DO NOTHING;

-- 8. High Error Rate - Generic
INSERT INTO playbooks (name, description, trigger_pattern, service_pattern, category, success_rate, avg_resolution_time_minutes, times_used, created_by)
VALUES
('High Error Rate - Triage and Diagnose',
 'Generic high error rate detected. Start with log analysis to identify error pattern. Check recent deployments, config changes, and upstream dependencies. If error rate > 50%, consider traffic diversion or rollback.',
 'error rate|500|internal server error|ServiceError|high.*error|spike',
 '.*',
 'generic',
 75.00, 15, 31, 'sre-team')
ON CONFLICT DO NOTHING;


-- ============================================
-- Insert Playbook Solutions
-- ============================================

-- Solutions for: DB Connection Pool Exhaustion
INSERT INTO playbook_solutions (playbook_id, rank, action_type, action_details, prerequisites, post_checks, expected_resolution_time_minutes)
SELECT id, 1, 'rollback',
    '{"target_version": "previous", "reason": "revert connection pool config change", "safe": true}'::jsonb,
    '{"has_previous_version": true, "no_schema_changes": true}'::jsonb,
    '{"check_error_rate": true, "check_latency": true, "monitor_duration_minutes": 5}'::jsonb,
    3
FROM playbooks WHERE name = 'Database Connection Pool Exhaustion'
AND NOT EXISTS (SELECT 1 FROM playbook_solutions ps JOIN playbooks p ON ps.playbook_id = p.id WHERE p.name = 'Database Connection Pool Exhaustion' AND ps.rank = 1);

INSERT INTO playbook_solutions (playbook_id, rank, action_type, action_details, prerequisites, post_checks, expected_resolution_time_minutes)
SELECT id, 2, 'scale_up',
    '{"resource": "database_connections", "action": "increase_pool_size", "from": 10, "to": 50, "requires_restart": false}'::jsonb,
    '{"database_has_capacity": true}'::jsonb,
    '{"verify_connection_count": true, "check_error_rate": true}'::jsonb,
    5
FROM playbooks WHERE name = 'Database Connection Pool Exhaustion'
AND NOT EXISTS (SELECT 1 FROM playbook_solutions ps JOIN playbooks p ON ps.playbook_id = p.id WHERE p.name = 'Database Connection Pool Exhaustion' AND ps.rank = 2);

INSERT INTO playbook_solutions (playbook_id, rank, action_type, action_details, prerequisites, post_checks, expected_resolution_time_minutes)
SELECT id, 3, 'config_change',
    '{"parameter": "max_connections", "action": "increase", "from": 100, "to": 200, "apply_method": "hot_reload"}'::jsonb,
    '{"requires_manager_approval": true}'::jsonb,
    '{"verify_config_applied": true, "monitor_connection_usage": true}'::jsonb,
    7
FROM playbooks WHERE name = 'Database Connection Pool Exhaustion'
AND NOT EXISTS (SELECT 1 FROM playbook_solutions ps JOIN playbooks p ON ps.playbook_id = p.id WHERE p.name = 'Database Connection Pool Exhaustion' AND ps.rank = 3);

-- Solutions for: Memory Leak
INSERT INTO playbook_solutions (playbook_id, rank, action_type, action_details, prerequisites, post_checks, expected_resolution_time_minutes)
SELECT id, 1, 'restart',
    '{"action": "rolling_restart", "drain_time_seconds": 30, "wait_for_healthy": true}'::jsonb,
    '{"service_has_multiple_replicas": true}'::jsonb,
    '{"verify_memory_usage_stable": true, "monitor_duration_minutes": 15}'::jsonb,
    8
FROM playbooks WHERE name = 'Memory Leak - Gradual OOM'
AND NOT EXISTS (SELECT 1 FROM playbook_solutions ps JOIN playbooks p ON ps.playbook_id = p.id WHERE p.name = 'Memory Leak - Gradual OOM' AND ps.rank = 1);

INSERT INTO playbook_solutions (playbook_id, rank, action_type, action_details, prerequisites, post_checks, expected_resolution_time_minutes)
SELECT id, 2, 'scale_up',
    '{"resource": "memory", "action": "increase_limit", "from": "256Mi", "to": "512Mi", "temporary": true}'::jsonb,
    '{"cluster_has_capacity": true}'::jsonb,
    '{"monitor_memory_growth": true}'::jsonb,
    10
FROM playbooks WHERE name = 'Memory Leak - Gradual OOM'
AND NOT EXISTS (SELECT 1 FROM playbook_solutions ps JOIN playbooks p ON ps.playbook_id = p.id WHERE p.name = 'Memory Leak - Gradual OOM' AND ps.rank = 2);

INSERT INTO playbook_solutions (playbook_id, rank, action_type, action_details, prerequisites, post_checks, expected_resolution_time_minutes)
SELECT id, 3, 'rollback',
    '{"target_version": "last_known_good", "reason": "memory leak introduced in recent deploy"}'::jsonb,
    '{"has_previous_version": true}'::jsonb,
    '{"verify_memory_usage": true, "monitor_duration_minutes": 30}'::jsonb,
    5
FROM playbooks WHERE name = 'Memory Leak - Gradual OOM'
AND NOT EXISTS (SELECT 1 FROM playbook_solutions ps JOIN playbooks p ON ps.playbook_id = p.id WHERE p.name = 'Memory Leak - Gradual OOM' AND ps.rank = 3);

-- Solutions for: Rate Limit Exceeded
INSERT INTO playbook_solutions (playbook_id, rank, action_type, action_details, prerequisites, post_checks, expected_resolution_time_minutes)
SELECT id, 1, 'config_change',
    '{"parameter": "rate_limit_backoff", "action": "enable_exponential_backoff", "initial_delay_ms": 1000, "max_delay_ms": 30000}'::jsonb,
    '{"service_supports_backoff": true}'::jsonb,
    '{"verify_retry_behavior": true}'::jsonb,
    5
FROM playbooks WHERE name = 'Rate Limit Exceeded on External Provider'
AND NOT EXISTS (SELECT 1 FROM playbook_solutions ps JOIN playbooks p ON ps.playbook_id = p.id WHERE p.name = 'Rate Limit Exceeded on External Provider' AND ps.rank = 1);

INSERT INTO playbook_solutions (playbook_id, rank, action_type, action_details, prerequisites, post_checks, expected_resolution_time_minutes)
SELECT id, 2, 'scale_down',
    '{"resource": "request_rate", "action": "reduce_concurrency", "from": 100, "to": 50}'::jsonb,
    '{"can_reduce_throughput": true}'::jsonb,
    '{"verify_within_limits": true}'::jsonb,
    3
FROM playbooks WHERE name = 'Rate Limit Exceeded on External Provider'
AND NOT EXISTS (SELECT 1 FROM playbook_solutions ps JOIN playbooks p ON ps.playbook_id = p.id WHERE p.name = 'Rate Limit Exceeded on External Provider' AND ps.rank = 2);

-- Solutions for: Payment Gateway Timeout
INSERT INTO playbook_solutions (playbook_id, rank, action_type, action_details, prerequisites, post_checks, expected_resolution_time_minutes)
SELECT id, 1, 'config_change',
    '{"parameter": "timeout", "action": "increase", "from_seconds": 5, "to_seconds": 15}'::jsonb,
    '{"acceptable_latency_increase": true}'::jsonb,
    '{"verify_timeout_reduced": true}'::jsonb,
    3
FROM playbooks WHERE name = 'Timeout in Payment Processing'
AND NOT EXISTS (SELECT 1 FROM playbook_solutions ps JOIN playbooks p ON ps.playbook_id = p.id WHERE p.name = 'Timeout in Payment Processing' AND ps.rank = 1);

INSERT INTO playbook_solutions (playbook_id, rank, action_type, action_details, prerequisites, post_checks, expected_resolution_time_minutes)
SELECT id, 2, 'circuit_breaker',
    '{"action": "enable_circuit_breaker", "failure_threshold": 5, "timeout_seconds": 30}'::jsonb,
    '{"service_supports_circuit_breaker": true}'::jsonb,
    '{"verify_circuit_breaker_active": true}'::jsonb,
    10
FROM playbooks WHERE name = 'Timeout in Payment Processing'
AND NOT EXISTS (SELECT 1 FROM playbook_solutions ps JOIN playbooks p ON ps.playbook_id = p.id WHERE p.name = 'Timeout in Payment Processing' AND ps.rank = 2);

-- Solutions for: Circuit Breaker Tripped
INSERT INTO playbook_solutions (playbook_id, rank, action_type, action_details, prerequisites, post_checks, expected_resolution_time_minutes)
SELECT id, 1, 'restart',
    '{"action": "restart_upstream_service", "target": "upstream", "verify_health_first": true}'::jsonb,
    '{"upstream_service_identified": true}'::jsonb,
    '{"verify_upstream_healthy": true, "verify_circuit_breaker_closed": true}'::jsonb,
    6
FROM playbooks WHERE name = 'Circuit Breaker Tripped on Upstream Service'
AND NOT EXISTS (SELECT 1 FROM playbook_solutions ps JOIN playbooks p ON ps.playbook_id = p.id WHERE p.name = 'Circuit Breaker Tripped on Upstream Service' AND ps.rank = 1);

INSERT INTO playbook_solutions (playbook_id, rank, action_type, action_details, prerequisites, post_checks, expected_resolution_time_minutes)
SELECT id, 2, 'config_change',
    '{"parameter": "circuit_breaker", "action": "reset_to_half_open", "allow_test_requests": 3}'::jsonb,
    '{"upstream_service_recovering": true}'::jsonb,
    '{"monitor_success_rate": true}'::jsonb,
    3
FROM playbooks WHERE name = 'Circuit Breaker Tripped on Upstream Service'
AND NOT EXISTS (SELECT 1 FROM playbook_solutions ps JOIN playbooks p ON ps.playbook_id = p.id WHERE p.name = 'Circuit Breaker Tripped on Upstream Service' AND ps.rank = 2);

-- Solutions for: Cascading Timeout
INSERT INTO playbook_solutions (playbook_id, rank, action_type, action_details, prerequisites, post_checks, expected_resolution_time_minutes)
SELECT id, 1, 'restart',
    '{"action": "restart_root_service", "strategy": "identify_deepest_dependency_first"}'::jsonb,
    '{"dependency_chain_identified": true}'::jsonb,
    '{"verify_all_services_healthy": true, "check_e2e_latency": true}'::jsonb,
    12
FROM playbooks WHERE name = 'Cascading Timeout Across Service Chain'
AND NOT EXISTS (SELECT 1 FROM playbook_solutions ps JOIN playbooks p ON ps.playbook_id = p.id WHERE p.name = 'Cascading Timeout Across Service Chain' AND ps.rank = 1);

INSERT INTO playbook_solutions (playbook_id, rank, action_type, action_details, prerequisites, post_checks, expected_resolution_time_minutes)
SELECT id, 2, 'config_change',
    '{"parameter": "timeout_budget", "action": "increase_all_timeouts_in_chain", "multiplier": 2}'::jsonb,
    '{"acceptable_latency_increase": true}'::jsonb,
    '{"verify_no_timeouts": true}'::jsonb,
    5
FROM playbooks WHERE name = 'Cascading Timeout Across Service Chain'
AND NOT EXISTS (SELECT 1 FROM playbook_solutions ps JOIN playbooks p ON ps.playbook_id = p.id WHERE p.name = 'Cascading Timeout Across Service Chain' AND ps.rank = 2);

-- Solutions for: Deployment Rollback
INSERT INTO playbook_solutions (playbook_id, rank, action_type, action_details, prerequisites, post_checks, expected_resolution_time_minutes)
SELECT id, 1, 'rollback',
    '{"target_version": "previous", "strategy": "immediate", "reason": "deployment caused errors"}'::jsonb,
    '{"has_previous_version": true, "no_breaking_schema_changes": true}'::jsonb,
    '{"check_error_rate": true, "check_latency": true, "verify_rollback_version": true}'::jsonb,
    2
FROM playbooks WHERE name = 'Failed Deployment - Immediate Rollback'
AND NOT EXISTS (SELECT 1 FROM playbook_solutions ps JOIN playbooks p ON ps.playbook_id = p.id WHERE p.name = 'Failed Deployment - Immediate Rollback' AND ps.rank = 1);

-- Solutions for: High Error Rate
INSERT INTO playbook_solutions (playbook_id, rank, action_type, action_details, prerequisites, post_checks, expected_resolution_time_minutes)
SELECT id, 1, 'scale_up',
    '{"resource": "replicas", "action": "increase_instances", "from": 1, "to": 3, "reason": "distribute load"}'::jsonb,
    '{"has_autoscaling": true}'::jsonb,
    '{"check_error_rate_after_5min": true}'::jsonb,
    5
FROM playbooks WHERE name = 'High Error Rate - Triage and Diagnose'
AND NOT EXISTS (SELECT 1 FROM playbook_solutions ps JOIN playbooks p ON ps.playbook_id = p.id WHERE p.name = 'High Error Rate - Triage and Diagnose' AND ps.rank = 1);

INSERT INTO playbook_solutions (playbook_id, rank, action_type, action_details, prerequisites, post_checks, expected_resolution_time_minutes)
SELECT id, 2, 'rollback',
    '{"target_version": "previous", "reason": "high error rate after recent deployment"}'::jsonb,
    '{"recent_deployment_found": true}'::jsonb,
    '{"check_error_rate": true}'::jsonb,
    3
FROM playbooks WHERE name = 'High Error Rate - Triage and Diagnose'
AND NOT EXISTS (SELECT 1 FROM playbook_solutions ps JOIN playbooks p ON ps.playbook_id = p.id WHERE p.name = 'High Error Rate - Triage and Diagnose' AND ps.rank = 2);


-- ============================================
-- Insert Historical Incidents (10 realistic incidents)
-- ============================================

-- 1. order-api: DB pool exhaustion (45 days ago)
INSERT INTO incidents (
    service_name, severity, status, error_signature, error_message, stack_trace,
    region, environment, first_seen_at, last_seen_at, resolved_at,
    root_cause, suspect_commit_id, suspect_file_path, confidence_score,
    resolution_action, resolution_time_seconds
) VALUES (
    'order-api', 'critical', 'closed',
    'db_pool_exhaustion_500',
    'FATAL: remaining connection slots are reserved for non-replication superuser connections',
    'Error: connection pool exhausted
  at Database.connect() [/app/database.py:45]
  at OrderService.create_order() [/app/services/order.py:102]
  at handle_request() [/app/main.py:23]',
    'us-central1', 'production',
    NOW() - INTERVAL '45 days',
    NOW() - INTERVAL '45 days' + INTERVAL '2 minutes',
    NOW() - INTERVAL '45 days' + INTERVAL '3 minutes',
    'Database connection pool size was changed from 10 to 100 in commit #402abc, but PostgreSQL max_connections was still 10.',
    '402abc123def456', 'config/database.py', 0.92,
    'rollback_to_v1.8.0', 180
);

-- 2. payment-api: Memory leak (30 days ago)
INSERT INTO incidents (
    service_name, severity, status, error_signature, error_message, stack_trace,
    region, environment, first_seen_at, last_seen_at, resolved_at,
    root_cause, suspect_commit_id, suspect_file_path, confidence_score,
    resolution_action, resolution_time_seconds
) VALUES (
    'payment-api', 'high', 'closed',
    'oom_killed_137',
    'Container killed with exit code 137 (OOMKilled) - memory usage: 512MB',
    'Error: Cannot allocate memory
  at PaymentProcessor.cache_transaction() [/app/services/payment.py:156]
  at process_payment() [/app/main.py:45]
  RuntimeError: memory allocation failed',
    'us-central1', 'production',
    NOW() - INTERVAL '30 days',
    NOW() - INTERVAL '30 days' + INTERVAL '12 hours',
    NOW() - INTERVAL '30 days' + INTERVAL '12 hours' + INTERVAL '8 minutes',
    'Memory leak in transaction cache - entries never evicted. Commit #789xyz added caching without TTL.',
    '789xyz456abc123', 'services/payment.py', 0.88,
    'rolling_restart', 480
);

-- 3. notification-api: Rate limit (20 days ago)
INSERT INTO incidents (
    service_name, severity, status, error_signature, error_message, stack_trace,
    region, environment, first_seen_at, last_seen_at, resolved_at,
    root_cause, suspect_commit_id, suspect_file_path, confidence_score,
    resolution_action, resolution_time_seconds
) VALUES (
    'notification-api', 'medium', 'closed',
    'rate_limit_429',
    'SendGrid rate limit exceeded: 429 Too Many Requests. Daily limit: 1000/1000.',
    'Error: RateLimitExceeded
  at SendGridClient.send() [/app/providers/sendgrid.py:89]
  at NotificationService.send_email() [/app/services/notification.py:34]',
    'us-central1', 'production',
    NOW() - INTERVAL '20 days',
    NOW() - INTERVAL '20 days' + INTERVAL '6 hours',
    NOW() - INTERVAL '20 days' + INTERVAL '6 hours' + INTERVAL '5 minutes',
    'Marketing campaign triggered 5000 notifications in 1 hour, exceeding SendGrid daily limit.',
    'abc123marketing', 'services/notification.py', 0.95,
    'enable_exponential_backoff', 300
);

-- 4. order-api: Cascading timeout (15 days ago)
INSERT INTO incidents (
    service_name, severity, status, error_signature, error_message, stack_trace,
    region, environment, first_seen_at, last_seen_at, resolved_at,
    root_cause, suspect_commit_id, suspect_file_path, confidence_score,
    resolution_action, resolution_time_seconds
) VALUES (
    'order-api', 'critical', 'closed',
    'downstream_timeout_504',
    'Timeout calling downstream service: payment-api - not responding after 5000ms',
    'Error: DownstreamTimeout
  at HTTPClient.post() [/app/clients/http.py:45]
  at PaymentClient.process() [/app/clients/payment.py:23]
  TimeoutError: Connection timed out after 5000ms',
    'us-central1', 'production',
    NOW() - INTERVAL '15 days',
    NOW() - INTERVAL '15 days' + INTERVAL '30 minutes',
    NOW() - INTERVAL '15 days' + INTERVAL '42 minutes',
    'payment-api memory pressure caused slow responses. Timeouts cascaded to order-api.',
    'timeout_config_change', 'config/timeouts.yaml', 0.78,
    'increase_timeout_and_restart_payment', 720
);

-- 5. notification-api: Circuit breaker (10 days ago)
INSERT INTO incidents (
    service_name, severity, status, error_signature, error_message, stack_trace,
    region, environment, first_seen_at, last_seen_at, resolved_at,
    root_cause, suspect_commit_id, suspect_file_path, confidence_score,
    resolution_action, resolution_time_seconds
) VALUES (
    'notification-api', 'high', 'closed',
    'circuit_breaker_open',
    'Circuit breaker OPEN for upstream order-api. Consecutive failures: 8. Requests rejected.',
    'Error: CircuitBreakerOpen
  at CircuitBreaker.call() [/app/middleware/circuit_breaker.py:67]
  at OrderClient.get_order() [/app/clients/order.py:15]',
    'us-central1', 'production',
    NOW() - INTERVAL '10 days',
    NOW() - INTERVAL '10 days' + INTERVAL '15 minutes',
    NOW() - INTERVAL '10 days' + INTERVAL '21 minutes',
    'order-api deployment introduced a bug causing 100% 500 errors. Circuit breaker correctly tripped.',
    'buggy_deploy_order', 'services/order.py', 0.90,
    'rollback_order_api_and_reset_cb', 360
);

-- 6. payment-api: Stripe gateway timeout (7 days ago)
INSERT INTO incidents (
    service_name, severity, status, error_signature, error_message, stack_trace,
    region, environment, first_seen_at, last_seen_at, resolved_at,
    root_cause, suspect_commit_id, suspect_file_path, confidence_score,
    resolution_action, resolution_time_seconds
) VALUES (
    'payment-api', 'high', 'closed',
    'gateway_timeout_504',
    'Payment gateway timeout - Stripe API not responding after 5000ms',
    'Error: GatewayTimeout
  at StripeClient.charge() [/app/gateways/stripe.py:112]
  requests.exceptions.ReadTimeout: HTTPSConnectionPool: Read timed out. (read timeout=5)',
    'us-central1', 'production',
    NOW() - INTERVAL '7 days',
    NOW() - INTERVAL '7 days' + INTERVAL '45 minutes',
    NOW() - INTERVAL '7 days' + INTERVAL '55 minutes',
    'Stripe partial outage. Our 5s timeout was too aggressive during degraded conditions.',
    NULL, NULL, 0.65,
    'increase_timeout_to_15s', 600
);

-- 7. order-api: Deploy regression (5 days ago)
INSERT INTO incidents (
    service_name, severity, status, error_signature, error_message, stack_trace,
    region, environment, first_seen_at, last_seen_at, resolved_at,
    root_cause, suspect_commit_id, suspect_file_path, confidence_score,
    resolution_action, resolution_time_seconds
) VALUES (
    'order-api', 'critical', 'closed',
    'deploy_regression_500',
    'Internal Server Error: KeyError: customer_id - Missing required field after API schema change',
    'Error: KeyError
  at OrderValidator.validate() [/app/validators/order.py:34]
  KeyError: customer_id',
    'us-central1', 'production',
    NOW() - INTERVAL '5 days',
    NOW() - INTERVAL '5 days' + INTERVAL '8 minutes',
    NOW() - INTERVAL '5 days' + INTERVAL '10 minutes',
    'API schema renamed customer_id to user_id but not all callers updated.',
    'schema_change_456', 'validators/order.py', 0.98,
    'rollback_to_v1.7.9', 120
);

-- 8. users-api: Connection refused (3 days ago)
INSERT INTO incidents (
    service_name, severity, status, error_signature, error_message, stack_trace,
    region, environment, first_seen_at, last_seen_at, resolved_at,
    root_cause, suspect_commit_id, suspect_file_path, confidence_score,
    resolution_action, resolution_time_seconds
) VALUES (
    'users-api', 'medium', 'closed',
    'connection_refused_econnrefused',
    'ECONNREFUSED: Connection refused to database at 10.0.0.5:5432',
    'Error: ConnectionRefused
  at Database.connect() [/app/database.py:23]
  psycopg2.OperationalError: could not connect to server: Connection refused',
    'us-central1', 'production',
    NOW() - INTERVAL '3 days',
    NOW() - INTERVAL '3 days' + INTERVAL '5 minutes',
    NOW() - INTERVAL '3 days' + INTERVAL '9 minutes',
    'Cloud SQL automatic maintenance restart. Service reconnected after pool refresh.',
    NULL, NULL, 0.70,
    'wait_for_db_restart', 240
);

-- 9. payment-api: DB pool exhaustion (60 days ago, similar pattern)
INSERT INTO incidents (
    service_name, severity, status, error_signature, error_message, stack_trace,
    region, environment, first_seen_at, last_seen_at, resolved_at,
    root_cause, suspect_commit_id, suspect_file_path, confidence_score,
    resolution_action, resolution_time_seconds
) VALUES (
    'payment-api', 'critical', 'closed',
    'db_pool_exhaustion_payment',
    'FATAL: too many clients already - connection pool exhausted on payment-api',
    'Error: connection pool exhausted
  at Database.get_connection() [/app/database.py:89]
  psycopg2.pool.PoolError: connection pool exhausted',
    'us-central1', 'production',
    NOW() - INTERVAL '60 days',
    NOW() - INTERVAL '60 days' + INTERVAL '10 minutes',
    NOW() - INTERVAL '60 days' + INTERVAL '15 minutes',
    'Black Friday traffic spike exceeded connection pool capacity. Pool configured for 10 but needed 50.',
    'pool_size_fix_001', 'config/database.py', 0.85,
    'scale_up_pool_size', 300
);

-- 10. analytics-pipeline: OOM during batch (2 days ago)
INSERT INTO incidents (
    service_name, severity, status, error_signature, error_message, stack_trace,
    region, environment, first_seen_at, last_seen_at, resolved_at,
    root_cause, suspect_commit_id, suspect_file_path, confidence_score,
    resolution_action, resolution_time_seconds
) VALUES (
    'analytics-pipeline', 'medium', 'closed',
    'batch_oom_killed',
    'Analytics batch job OOMKilled processing 2M records - memory usage: 4GB',
    'Error: OutOfMemoryError
  at BatchProcessor.aggregate() [/app/jobs/aggregate.py:234]
  MemoryError: Unable to allocate 2.1 GiB',
    'us-central1', 'production',
    NOW() - INTERVAL '2 days',
    NOW() - INTERVAL '2 days' + INTERVAL '2 hours',
    NOW() - INTERVAL '2 days' + INTERVAL '2 hours' + INTERVAL '15 minutes',
    'Batch job loaded all 2M records into memory. Fixed by chunked processing.',
    'streaming_fix_789', 'jobs/aggregate.py', 0.92,
    'increase_memory_and_chunk_processing', 900
);


-- ============================================
-- Insert Deployment Events
-- ============================================

INSERT INTO deployment_events (
    service_id, commit_id, version, deployed_by, deployment_type, status,
    caused_incident, related_incident_id, started_at, completed_at
)
SELECT
    (SELECT id FROM services WHERE name = 'order-api'),
    '402abc123def456', 'v1.8.1', 'alice@example.com', 'deploy', 'rolled_back',
    TRUE,
    (SELECT id FROM incidents WHERE error_signature = 'db_pool_exhaustion_500' LIMIT 1),
    NOW() - INTERVAL '45 days',
    NOW() - INTERVAL '45 days' + INTERVAL '5 minutes';

INSERT INTO deployment_events (
    service_id, commit_id, version, deployed_by, deployment_type, status,
    caused_incident, started_at, completed_at
)
SELECT
    (SELECT id FROM services WHERE name = 'payment-api'),
    '789xyz456abc123', 'v2.1.4', 'bob@example.com', 'deploy', 'success',
    TRUE,
    NOW() - INTERVAL '30 days',
    NOW() - INTERVAL '30 days' + INTERVAL '3 minutes';

INSERT INTO deployment_events (
    service_id, commit_id, version, deployed_by, deployment_type, status,
    caused_incident, started_at, completed_at
)
SELECT
    (SELECT id FROM services WHERE name = 'order-api'),
    'fix_pool_config_789', 'v1.8.1', 'alice@example.com', 'deploy', 'success',
    FALSE,
    NOW() - INTERVAL '44 days',
    NOW() - INTERVAL '44 days' + INTERVAL '4 minutes';

INSERT INTO deployment_events (
    service_id, commit_id, version, deployed_by, deployment_type, status,
    caused_incident, started_at, completed_at
)
SELECT
    (SELECT id FROM services WHERE name = 'notification-api'),
    'backoff_impl_abc', 'v1.2.0', 'charlie@example.com', 'deploy', 'success',
    FALSE,
    NOW() - INTERVAL '19 days',
    NOW() - INTERVAL '19 days' + INTERVAL '3 minutes';


-- ============================================
-- Insert Agent Initial States
-- ============================================

INSERT INTO agent_states (agent_name, state) VALUES
('sentinel', '{"last_scan_time": null, "baseline_metrics": {}, "active_monitors": ["order-api", "payment-api", "notification-api"]}'),
('detective', '{"active_investigations": [], "correlation_cache": {}}'),
('historian', '{"cache": {}, "last_playbook_update": null, "playbook_count": 8}'),
('mediator', '{"pending_approvals": [], "risk_thresholds": {"critical": 0.8, "high": 0.6}}'),
('executor', '{"active_remediations": [], "last_execution_time": null}')
ON CONFLICT (agent_name) DO UPDATE SET
    state = EXCLUDED.state,
    last_updated = NOW();


-- ============================================
-- Insert Sample Audit Logs
-- ============================================

INSERT INTO audit_logs (
    agent_name, action_type, action_details, incident_id, service_id,
    human_approved, status, result, created_at, completed_at
)
SELECT
    'sentinel', 'detect_anomalies',
    '{"time_window_minutes": 5, "errors_found": 47, "severity": "critical"}'::jsonb,
    (SELECT id FROM incidents WHERE error_signature = 'db_pool_exhaustion_500' LIMIT 1),
    (SELECT id FROM services WHERE name = 'order-api'),
    FALSE, 'success',
    '{"incidents_created": 1, "services_affected": 1}'::jsonb,
    NOW() - INTERVAL '45 days',
    NOW() - INTERVAL '45 days' + INTERVAL '10 seconds';

INSERT INTO audit_logs (
    agent_name, action_type, action_details, incident_id,
    human_approved, status, result, created_at, completed_at
)
SELECT
    'detective', 'investigate_root_cause',
    '{"method": "commit_correlation", "time_window_minutes": 30}'::jsonb,
    (SELECT id FROM incidents WHERE error_signature = 'db_pool_exhaustion_500' LIMIT 1),
    FALSE, 'success',
    '{"root_cause_found": true, "confidence": 0.92, "suspect_commit": "402abc123def456"}'::jsonb,
    NOW() - INTERVAL '45 days' + INTERVAL '30 seconds',
    NOW() - INTERVAL '45 days' + INTERVAL '1 minute';

INSERT INTO audit_logs (
    agent_name, action_type, action_details, incident_id, service_id,
    human_approved, approved_by, approved_at, status, result, created_at, completed_at
)
SELECT
    'executor', 'rollback',
    '{"from_version": "v1.8.1", "to_version": "v1.8.0", "reason": "database pool exhaustion"}'::jsonb,
    (SELECT id FROM incidents WHERE error_signature = 'db_pool_exhaustion_500' LIMIT 1),
    (SELECT id FROM services WHERE name = 'order-api'),
    TRUE, 'sre-oncall@example.com',
    NOW() - INTERVAL '45 days' + INTERVAL '2.5 minutes',
    'success',
    '{"rollback_duration_seconds": 45, "error_rate_after": 0, "verification": "passed"}'::jsonb,
    NOW() - INTERVAL '45 days' + INTERVAL '2 minutes',
    NOW() - INTERVAL '45 days' + INTERVAL '3 minutes';

INSERT INTO audit_logs (
    agent_name, action_type, action_details, incident_id,
    human_approved, status, result, created_at, completed_at
)
SELECT
    'historian', 'search_playbooks',
    '{"query": "connection pool exhausted", "results_found": 1}'::jsonb,
    (SELECT id FROM incidents WHERE error_signature = 'db_pool_exhaustion_500' LIMIT 1),
    FALSE, 'success',
    '{"playbook": "Database Connection Pool Exhaustion", "success_rate": 95, "recommended_action": "rollback"}'::jsonb,
    NOW() - INTERVAL '45 days' + INTERVAL '25 seconds',
    NOW() - INTERVAL '45 days' + INTERVAL '35 seconds';

INSERT INTO audit_logs (
    agent_name, action_type, action_details, incident_id, service_id,
    human_approved, status, result, created_at, completed_at
)
SELECT
    'mediator', 'analyze_blast_radius',
    '{"service": "order-api", "action": "rollback"}'::jsonb,
    (SELECT id FROM incidents WHERE error_signature = 'db_pool_exhaustion_500' LIMIT 1),
    (SELECT id FROM services WHERE name = 'order-api'),
    FALSE, 'success',
    '{"blast_radius": 2, "affected_services": ["notification-api", "analytics-pipeline"], "risk_level": "medium"}'::jsonb,
    NOW() - INTERVAL '45 days' + INTERVAL '40 seconds',
    NOW() - INTERVAL '45 days' + INTERVAL '50 seconds';


-- ============================================
-- Verification
-- ============================================
DO $$
DECLARE
    service_count INTEGER;
    playbook_count INTEGER;
    solution_count INTEGER;
    incident_count INTEGER;
    dependency_count INTEGER;
    deployment_count INTEGER;
    audit_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO service_count FROM services;
    SELECT COUNT(*) INTO playbook_count FROM playbooks;
    SELECT COUNT(*) INTO solution_count FROM playbook_solutions;
    SELECT COUNT(*) INTO incident_count FROM incidents;
    SELECT COUNT(*) INTO dependency_count FROM service_dependencies;
    SELECT COUNT(*) INTO deployment_count FROM deployment_events;
    SELECT COUNT(*) INTO audit_count FROM audit_logs;

    RAISE NOTICE '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━';
    RAISE NOTICE 'Seed data loaded successfully:';
    RAISE NOTICE '  Services:      %', service_count;
    RAISE NOTICE '  Dependencies:  %', dependency_count;
    RAISE NOTICE '  Playbooks:     %', playbook_count;
    RAISE NOTICE '  Solutions:     %', solution_count;
    RAISE NOTICE '  Incidents:     %', incident_count;
    RAISE NOTICE '  Deployments:   %', deployment_count;
    RAISE NOTICE '  Audit logs:    %', audit_count;
    RAISE NOTICE '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━';
END $$;
