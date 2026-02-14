import os
import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

from common.config import (
    DB_USER, DB_PASSWORD, DB_NAME, DB_HOST, DB_PORT,
    INSTANCE_CONNECTION_NAME, CLOUD_SQL_SOCKET_DIR
)

logger = logging.getLogger(__name__)

# Register UUID adapter for psycopg2
psycopg2.extras.register_uuid()

# Connection pool (initialized lazily)
_pool = None


def _get_connection_params():
    """Build connection parameters for Cloud SQL or local PostgreSQL."""
    params = {
        "user": DB_USER,
        "password": DB_PASSWORD,
        "dbname": DB_NAME,
    }

    # If DB_HOST is set, connect via TCP (local dev or Cloud SQL proxy)
    if DB_HOST:
        params["host"] = DB_HOST
        params["port"] = DB_PORT
    else:
        # Connect via Unix socket (Cloud Run to Cloud SQL)
        socket_path = os.path.join(CLOUD_SQL_SOCKET_DIR, INSTANCE_CONNECTION_NAME)
        params["host"] = socket_path

    return params


def get_pool():
    """Get or create the connection pool."""
    global _pool
    if _pool is None:
        try:
            conn_params = _get_connection_params()
            _pool = ThreadedConnectionPool(
                minconn=1,
                maxconn=5,
                **conn_params
            )
            logger.info("Database connection pool created successfully")
        except Exception as e:
            logger.error(f"Failed to create database pool: {e}")
            raise
    return _pool


@contextmanager
def get_db_connection():
    """Context manager for database connections from the pool."""
    pool = get_pool()
    conn = pool.getconn()
    try:
        conn.autocommit = False
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


@contextmanager
def get_db_cursor(dict_cursor=True):
    """Context manager that provides a database cursor."""
    with get_db_connection() as conn:
        cursor_factory = psycopg2.extras.RealDictCursor if dict_cursor else None
        cursor = conn.cursor(cursor_factory=cursor_factory)
        try:
            yield cursor
        finally:
            cursor.close()


# ============================================
# Incident Operations
# ============================================

def create_incident(service_name, severity, error_signature, error_message,
                    stack_trace=None, region="us-central1", environment="production"):
    """Create a new incident record."""
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO incidents (
                service_name, severity, error_signature, error_message,
                stack_trace, region, environment
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id, service_name, severity, status, error_signature, created_at
            """,
            (service_name, severity, error_signature, error_message,
             stack_trace, region, environment)
        )
        result = cursor.fetchone()
        return dict(result)


def update_incident(incident_id, **kwargs):
    """Update an incident with new information."""
    allowed_fields = [
        "status", "root_cause", "suspect_commit_id", "suspect_file_path",
        "confidence_score", "resolution_action", "resolution_time_seconds",
        "resolved_at", "error_count", "last_seen_at"
    ]

    updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
    if not updates:
        return None

    set_clause = ", ".join(f"{k} = %s" for k in updates.keys())
    values = list(updates.values()) + [incident_id]

    with get_db_cursor() as cursor:
        cursor.execute(
            f"""
            UPDATE incidents SET {set_clause}
            WHERE id = %s
            RETURNING *
            """,
            values
        )
        result = cursor.fetchone()
        return dict(result) if result else None


def get_incident(incident_id):
    """Get a single incident by ID."""
    with get_db_cursor() as cursor:
        cursor.execute("SELECT * FROM incidents WHERE id = %s", (incident_id,))
        result = cursor.fetchone()
        return dict(result) if result else None


def get_open_incidents():
    """Get all open or investigating incidents."""
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT * FROM incidents
            WHERE status IN ('open', 'investigating')
            ORDER BY
                CASE severity
                    WHEN 'critical' THEN 1
                    WHEN 'high' THEN 2
                    WHEN 'medium' THEN 3
                    WHEN 'low' THEN 4
                END,
                created_at DESC
            """
        )
        return [dict(row) for row in cursor.fetchall()]


# ============================================
# Playbook Operations
# ============================================

def search_playbooks_by_pattern(error_message):
    """Search playbooks by matching error message against trigger patterns."""
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT p.*, 
                   json_agg(json_build_object(
                       'rank', ps.rank,
                       'action_type', ps.action_type,
                       'action_details', ps.action_details,
                       'prerequisites', ps.prerequisites,
                       'post_checks', ps.post_checks,
                       'expected_resolution_time_minutes', ps.expected_resolution_time_minutes
                   ) ORDER BY ps.rank) as solutions
            FROM playbooks p
            LEFT JOIN playbook_solutions ps ON p.id = ps.playbook_id
            WHERE %s ~* p.trigger_pattern
               OR p.trigger_pattern ~* %s
            GROUP BY p.id
            ORDER BY p.success_rate DESC
            """,
            (error_message, error_message)
        )
        return [dict(row) for row in cursor.fetchall()]


def get_playbook_with_solutions(playbook_id):
    """Get a playbook and its ranked solutions."""
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT p.*,
                   json_agg(json_build_object(
                       'rank', ps.rank,
                       'action_type', ps.action_type,
                       'action_details', ps.action_details,
                       'prerequisites', ps.prerequisites,
                       'post_checks', ps.post_checks
                   ) ORDER BY ps.rank) as solutions
            FROM playbooks p
            LEFT JOIN playbook_solutions ps ON p.id = ps.playbook_id
            WHERE p.id = %s
            GROUP BY p.id
            """,
            (playbook_id,)
        )
        result = cursor.fetchone()
        return dict(result) if result else None


# ============================================
# Service Topology Operations
# ============================================

def get_service(service_name):
    """Get a service by name."""
    with get_db_cursor() as cursor:
        cursor.execute("SELECT * FROM services WHERE name = %s", (service_name,))
        result = cursor.fetchone()
        return dict(result) if result else None


def get_all_services():
    """Get all services."""
    with get_db_cursor() as cursor:
        cursor.execute("SELECT * FROM services ORDER BY name")
        return [dict(row) for row in cursor.fetchall()]


def get_service_dependencies(service_name):
    """Get all services that depend on the given service."""
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT s.name as dependent_service, sd.dependency_type, sd.criticality
            FROM service_dependencies sd
            JOIN services s ON sd.service_id = s.id
            JOIN services target ON sd.depends_on_service_id = target.id
            WHERE target.name = %s
            ORDER BY
                CASE sd.criticality
                    WHEN 'critical' THEN 1
                    WHEN 'high' THEN 2
                    WHEN 'medium' THEN 3
                    WHEN 'low' THEN 4
                END
            """,
            (service_name,)
        )
        return [dict(row) for row in cursor.fetchall()]


def calculate_blast_radius(service_name):
    """Calculate the blast radius for a service using the DB function."""
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT service_name, hop_count
            FROM calculate_blast_radius(
                (SELECT id FROM services WHERE name = %s)
            )
            ORDER BY hop_count, service_name
            """,
            (service_name,)
        )
        return [dict(row) for row in cursor.fetchall()]


# ============================================
# Deployment Events
# ============================================

def get_recent_deployments(service_name, hours=1):
    """Get recent deployments for a service within a time window."""
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT de.*, s.name as service_name
            FROM deployment_events de
            JOIN services s ON de.service_id = s.id
            WHERE s.name = %s
              AND de.started_at >= NOW() - INTERVAL '%s hours'
            ORDER BY de.started_at DESC
            """,
            (service_name, hours)
        )
        return [dict(row) for row in cursor.fetchall()]


# ============================================
# Audit Log Operations
# ============================================

def create_audit_log(agent_name, action_type, action_details,
                     incident_id=None, service_id=None, status="pending"):
    """Create an audit log entry."""
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO audit_logs (
                agent_name, action_type, action_details,
                incident_id, service_id, status
            ) VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, agent_name, action_type, status, created_at
            """,
            (agent_name, action_type, json.dumps(action_details),
             incident_id, service_id, status)
        )
        result = cursor.fetchone()
        return dict(result)


def update_audit_log(audit_id, status, result=None, error_message=None,
                     human_approved=False, approved_by=None):
    """Update an audit log entry with results."""
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            UPDATE audit_logs SET
                status = %s,
                result = %s,
                error_message = %s,
                human_approved = %s,
                approved_by = %s,
                approved_at = CASE WHEN %s THEN NOW() ELSE NULL END,
                completed_at = NOW()
            WHERE id = %s
            RETURNING *
            """,
            (status, json.dumps(result) if result else None, error_message,
             human_approved, approved_by, human_approved, audit_id)
        )
        result = cursor.fetchone()
        return dict(result) if result else None


def get_audit_trail(incident_id):
    """Get the full audit trail for an incident."""
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT * FROM audit_logs
            WHERE incident_id = %s
            ORDER BY created_at ASC
            """,
            (incident_id,)
        )
        return [dict(row) for row in cursor.fetchall()]


# ============================================
# Historical Incident Search
# ============================================

def find_similar_past_incidents(error_signature, service_name=None, limit=5):
    """Find similar past incidents using pattern matching."""
    with get_db_cursor() as cursor:
        query = """
            SELECT id, service_name, severity, error_signature, error_message,
                   root_cause, resolution_action, resolution_time_seconds,
                   confidence_score, first_seen_at, resolved_at
            FROM incidents
            WHERE status = 'closed'
              AND (
                  error_signature = %s
                  OR error_message ILIKE %s
              )
        """
        params = [error_signature, f"%{error_signature}%"]

        if service_name:
            query += " AND service_name = %s"
            params.append(service_name)

        query += " ORDER BY resolved_at DESC LIMIT %s"
        params.append(limit)

        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]


# ============================================
# Config Change Operations
# ============================================

def record_config_change(service_name, change_type, old_value, new_value,
                         changed_by=None, change_reason=None, related_incident_id=None):
    """Record a configuration change."""
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO config_changes (
                service_id, change_type, old_value, new_value,
                changed_by, change_reason, related_incident_id
            ) VALUES (
                (SELECT id FROM services WHERE name = %s),
                %s, %s, %s, %s, %s, %s
            )
            RETURNING id, change_type, applied_at
            """,
            (service_name, change_type,
             json.dumps(old_value), json.dumps(new_value),
             changed_by, change_reason, related_incident_id)
        )
        result = cursor.fetchone()
        return dict(result)
