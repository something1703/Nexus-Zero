"""
Nexus-Zero — Interactive Demo Dashboard Backend
================================================
Flask API serving the judge-facing dashboard.

Endpoints:
  GET  /                              — Dashboard UI
  GET  /api/health                    — Backend health check
  GET  /api/incidents                 — List recent incidents
  GET  /api/incidents/<id>            — Incident detail + audit trail
  GET  /api/incidents/<id>/timeline   — Real-time agent activity
  GET  /api/approvals                 — Pending approvals
  POST /api/approvals/<id>/approve    — Approve an action
  POST /api/approvals/<id>/reject     — Reject an action
  POST /api/chaos/inject              — Inject chaos into a demo service
  POST /api/chaos/stop                — Stop chaos on all services
  GET  /api/chaos/status              — Get chaos status
  POST /api/detect                    — Trigger Sentinel detection
  GET  /api/services                  — List all registered services
  GET  /api/stats                     — Overall platform statistics
"""

import os
import json
import logging
import uuid
import time
import threading
from datetime import datetime, timezone, timedelta
from functools import wraps

import requests as http_requests
import psycopg2
import psycopg2.extras
from flask import Flask, jsonify, request, render_template, abort

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "nexus-zero-sre")
GCP_REGION = os.environ.get("GCP_REGION", "us-central1")

# Cloud SQL
DB_HOST = os.environ.get("DB_HOST", "")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_USER = os.environ.get("DB_USER", "nexus_admin")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_NAME = os.environ.get("DB_NAME", "nexus_zero")
INSTANCE_CONNECTION_NAME = os.environ.get(
    "INSTANCE_CONNECTION_NAME", "nexus-zero-sre:us-central1:nexus-zero-db"
)
CLOUD_SQL_SOCKET_DIR = os.environ.get("CLOUD_SQL_SOCKET_DIR", "/cloudsql")

# Archestra A2A
ARCHESTRA_TOKEN = os.environ.get("ARCHESTRA_TOKEN", "")
SENTINEL_AGENT_ID = os.environ.get(
    "SENTINEL_AGENT_ID", "9a77407c-7ab1-481a-8155-8159e4859d4d"
)
ARCHESTRA_HUB_URL = os.environ.get(
    "ARCHESTRA_HUB_URL",
    "https://archestra-hub-833613368271.us-central1.run.app"
)

# Demo Service URLs (auto-discovered or set via env)
ORDER_API_URL = os.environ.get("ORDER_API_URL", "")
PAYMENT_API_URL = os.environ.get("PAYMENT_API_URL", "")
NOTIFICATION_API_URL = os.environ.get("NOTIFICATION_API_URL", "")

# MCP Bridge URL
BRIDGE_URL = os.environ.get("BRIDGE_URL", "")

PORT = int(os.environ.get("PORT", 8080))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nexus-dashboard")

# ---------------------------------------------------------------------------
# Flask App
# ---------------------------------------------------------------------------
app = Flask(__name__, template_folder="templates", static_folder="static")

# ---------------------------------------------------------------------------
# Database Connection
# ---------------------------------------------------------------------------
psycopg2.extras.register_uuid()

def get_db_connection():
    """Get a database connection (Cloud SQL socket or TCP)."""
    params = {
        "user": DB_USER,
        "password": DB_PASSWORD,
        "dbname": DB_NAME,
    }
    if DB_HOST:
        params["host"] = DB_HOST
        params["port"] = DB_PORT
    else:
        socket_path = os.path.join(CLOUD_SQL_SOCKET_DIR, INSTANCE_CONNECTION_NAME)
        params["host"] = socket_path

    conn = psycopg2.connect(**params)
    conn.autocommit = True
    return conn


def query_db(sql, params=None, one=False):
    """Execute a read query and return results as list of dicts."""
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params or ())
        rows = cur.fetchall()
        cur.close()
        return dict(rows[0]) if one and rows else [dict(r) for r in rows]
    finally:
        conn.close()


def execute_db(sql, params=None):
    """Execute a write query and return affected row."""
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params or ())
        try:
            result = cur.fetchone()
            return dict(result) if result else None
        except psycopg2.ProgrammingError:
            return None
        finally:
            cur.close()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Service URL Discovery
# ---------------------------------------------------------------------------
def discover_service_url(service_name):
    """Build Cloud Run URL for a service."""
    # Try environment variable first
    env_map = {
        "nexus-order-api": ORDER_API_URL,
        "nexus-payment-api": PAYMENT_API_URL,
        "nexus-notification-api": NOTIFICATION_API_URL,
    }
    url = env_map.get(service_name, "")
    if url:
        return url

    # Auto-discover from GCP project naming convention
    return f"https://{service_name}-833613368271.{GCP_REGION}.run.app"


# ---------------------------------------------------------------------------
# JSON Serializer
# ---------------------------------------------------------------------------
def serialize(obj):
    """JSON serializer for datetime and UUID objects."""
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, timedelta):
        return obj.total_seconds()
    return str(obj)


# ---------------------------------------------------------------------------
# API Routes — Dashboard
# ---------------------------------------------------------------------------
@app.route("/")
def dashboard():
    """Serve the main dashboard page."""
    return render_template("dashboard.html")


@app.route("/api/health")
def health_check():
    """Backend health check."""
    try:
        query_db("SELECT 1")
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"

    return jsonify({
        "status": "healthy",
        "database": db_status,
        "project": GCP_PROJECT_ID,
        "region": GCP_REGION,
        "timestamp": datetime.now(timezone.utc).isoformat()
    })


# ---------------------------------------------------------------------------
# API Routes — Incidents
# ---------------------------------------------------------------------------
@app.route("/api/incidents")
def list_incidents():
    """List recent incidents with summary stats."""
    limit = request.args.get("limit", 20, type=int)
    status_filter = request.args.get("status", None)

    sql = """
        SELECT id, service_name, severity, status, error_count,
               error_signature, error_message, created_at, updated_at,
               resolution_action, resolution_time_seconds
        FROM incidents
    """
    params = []
    if status_filter:
        sql += " WHERE status = %s"
        params.append(status_filter)
    sql += " ORDER BY created_at DESC LIMIT %s"
    params.append(limit)

    incidents = query_db(sql, params)
    return jsonify({
        "count": len(incidents),
        "incidents": incidents
    }), 200, {"Content-Type": "application/json"}


@app.route("/api/incidents/<incident_id>")
def get_incident(incident_id):
    """Get incident details with full audit trail."""
    incident = query_db(
        "SELECT * FROM incidents WHERE id = %s", (incident_id,), one=True
    )
    if not incident:
        return jsonify({"error": "Incident not found"}), 404

    audit_logs = query_db(
        """
        SELECT id, agent_name, action_type, status, action_details,
               result, error_message, human_approved, approved_by,
               created_at, completed_at
        FROM audit_logs
        WHERE incident_id = %s
        ORDER BY created_at ASC
        """,
        (incident_id,)
    )

    return jsonify({
        "incident": incident,
        "audit_logs": audit_logs,
        "audit_count": len(audit_logs)
    }), 200, {"Content-Type": "application/json"}


@app.route("/api/incidents/<incident_id>/timeline")
def get_incident_timeline(incident_id):
    """Get real-time timeline of agent actions for an incident."""
    audit_logs = query_db(
        """
        SELECT agent_name, action_type, status, created_at, completed_at,
               result, error_message
        FROM audit_logs
        WHERE incident_id = %s
        ORDER BY created_at ASC
        """,
        (incident_id,)
    )

    timeline = []
    for log in audit_logs:
        entry = {
            "agent": log["agent_name"],
            "action": log["action_type"],
            "status": log["status"],
            "timestamp": log["created_at"],
            "completed": log["completed_at"],
        }
        if log["result"]:
            try:
                result = json.loads(log["result"]) if isinstance(log["result"], str) else log["result"]
                entry["summary"] = _summarize_result(log["agent_name"], log["action_type"], result)
            except Exception:
                entry["summary"] = str(log["result"])[:200]
        if log["error_message"]:
            entry["error"] = log["error_message"]
        timeline.append(entry)

    return jsonify({"timeline": timeline}), 200, {"Content-Type": "application/json"}


def _summarize_result(agent, action, result):
    """Create human-readable summary from agent result."""
    if agent == "sentinel" and action == "detect_anomalies":
        return f"Detected {result.get('total_errors', '?')} errors across {result.get('services_affected', '?')} services"
    if agent == "historian" and "recommended_solutions" in str(result):
        return f"Found {result.get('total_solutions', '?')} solutions. Top: {result.get('top_recommendation', {}).get('action_type', '?')}"
    if agent == "mediator" and "verdict" in str(result):
        return f"Verdict: {result.get('verdict', '?')} (safety: {result.get('safety_score', '?')})"
    if agent == "executor":
        return f"Action: {result.get('action', '?')} → {result.get('status', '?')}"
    return json.dumps(result, default=serialize)[:200]


# ---------------------------------------------------------------------------
# API Routes — Approvals
# ---------------------------------------------------------------------------
@app.route("/api/approvals")
def list_approvals():
    """List pending approvals."""
    actions = query_db(
        """
        SELECT al.id, al.incident_id, al.agent_name, al.action_type,
               al.action_details, al.created_at,
               i.service_name, i.severity, i.error_signature
        FROM audit_logs al
        JOIN incidents i ON i.id = al.incident_id
        WHERE al.status = 'pending'
          AND al.action_type != 'produce_recommendation'
          AND al.action_type != 'detect_anomalies'
          AND al.action_type != 'acknowledge_incident'
        ORDER BY al.created_at DESC
        """
    )

    for action in actions:
        if isinstance(action.get("action_details"), str):
            try:
                action["action_details"] = json.loads(action["action_details"])
            except Exception:
                pass

    return jsonify({
        "count": len(actions),
        "actions": actions
    }), 200, {"Content-Type": "application/json"}


@app.route("/api/approvals/<approval_id>/approve", methods=["POST"])
def approve_action(approval_id):
    """Approve a pending action and execute it."""
    approved_by = request.json.get("approved_by", "dashboard_judge") if request.is_json else "dashboard_judge"

    # Get the pending action
    action = query_db(
        """
        SELECT al.id, al.incident_id, al.action_type, al.action_details, al.status,
               i.service_name
        FROM audit_logs al
        JOIN incidents i ON i.id = al.incident_id
        WHERE al.id = %s
        """,
        (approval_id,),
        one=True
    )

    if not action:
        return jsonify({"error": "Action not found"}), 404
    if action["status"] != "pending":
        return jsonify({"error": f"Action has status '{action['status']}', only 'pending' can be approved"}), 400

    # Parse action details
    details = action["action_details"]
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except Exception:
            details = {}

    service_name = action["service_name"]
    action_type = action["action_type"]

    # Simulate execution based on action type
    result = _execute_action(action_type, service_name, details)
    result["approved_by"] = approved_by
    result["approved_at"] = datetime.now(timezone.utc).isoformat()

    # Update audit log
    execute_db(
        """
        UPDATE audit_logs SET
            status = 'success',
            result = %s,
            human_approved = TRUE,
            approved_by = %s,
            approved_at = NOW(),
            completed_at = NOW()
        WHERE id = %s
        """,
        (json.dumps(result, default=serialize), approved_by, approval_id)
    )

    # Resolve the incident
    execute_db(
        """
        UPDATE incidents SET
            status = 'resolved',
            resolution_action = %s,
            resolution_time_seconds = EXTRACT(EPOCH FROM (NOW() - created_at)),
            updated_at = NOW()
        WHERE id = %s
        """,
        (action_type, action["incident_id"])
    )

    return jsonify({
        "status": "approved_and_executed",
        "audit_log_id": str(approval_id),
        "incident_id": str(action["incident_id"]),
        "action_type": action_type,
        "service": service_name,
        "result": result
    }), 200, {"Content-Type": "application/json"}


@app.route("/api/approvals/<approval_id>/reject", methods=["POST"])
def reject_action(approval_id):
    """Reject a pending action."""
    reason = request.json.get("reason", "Rejected by judge") if request.is_json else "Rejected by judge"

    execute_db(
        """
        UPDATE audit_logs SET
            status = 'rejected',
            error_message = %s,
            completed_at = NOW()
        WHERE id = %s AND status = 'pending'
        """,
        (reason, approval_id)
    )

    return jsonify({"status": "rejected", "audit_log_id": str(approval_id), "reason": reason})


def _execute_action(action_type, service_name, details):
    """Simulate executing a remediation action."""
    if action_type == "rollback":
        return {
            "action": "rollback",
            "service": service_name,
            "target_revision": details.get("target_version", "previous"),
            "status": "completed",
            "message": f"Traffic for {service_name} shifted to revision 'previous'. New instances are healthy.",
            "simulated": True
        }
    elif action_type == "scale_up":
        return {
            "action": "scale_up",
            "service": service_name,
            "from_instances": details.get("from", 1),
            "to_instances": details.get("to", 5),
            "status": "completed",
            "message": f"Scaled {service_name} from {details.get('from', 1)} to {details.get('to', 5)} instances.",
            "simulated": True
        }
    elif action_type == "restart":
        return {
            "action": "restart",
            "service": service_name,
            "status": "completed",
            "message": f"Restarted all instances of {service_name}. Health checks passing.",
            "simulated": True
        }
    else:
        return {
            "action": action_type,
            "service": service_name,
            "status": "completed",
            "message": f"Executed {action_type} on {service_name}.",
            "simulated": True
        }


# ---------------------------------------------------------------------------
# API Routes — Chaos Injection
# ---------------------------------------------------------------------------
CHAOS_MODES = {
    "db_pool_exhaustion": "Simulates database connection pool exhaustion",
    "memory_leak": "Simulates gradual memory leak",
    "cascade_failure": "Triggers cascading failures across services",
    "latency_spike": "Adds 5-10s random latency to requests",
}

DEMO_SERVICES = {
    "order": {"name": "nexus-order-api", "endpoint": "/order"},
    "payment": {"name": "nexus-payment-api", "endpoint": "/process"},
    "notification": {"name": "nexus-notification-api", "endpoint": "/notify"},
}


@app.route("/api/chaos/inject", methods=["POST"])
def inject_chaos():
    """Inject chaos into a demo service."""
    data = request.get_json() or {}
    service_key = data.get("service", "order")
    mode = data.get("mode", "db_pool_exhaustion")
    failure_rate = data.get("failure_rate", 0.40)

    if service_key not in DEMO_SERVICES:
        return jsonify({"error": f"Unknown service: {service_key}. Valid: {list(DEMO_SERVICES.keys())}"}), 400
    if mode not in CHAOS_MODES:
        return jsonify({"error": f"Unknown mode: {mode}. Valid: {list(CHAOS_MODES.keys())}"}), 400

    service = DEMO_SERVICES[service_key]
    service_url = discover_service_url(service["name"])

    try:
        # Enable chaos
        resp = http_requests.post(
            f"{service_url}/chaos/enable",
            json={"mode": mode, "failure_rate": failure_rate},
            timeout=10
        )

        # Send burst of requests to generate errors
        burst_results = {"success": 0, "errors": 0}

        def send_burst():
            for _ in range(30):
                try:
                    r = http_requests.post(
                        f"{service_url}{service['endpoint']}",
                        json={"customer": "chaos-demo", "amount": 99.99},
                        timeout=15
                    )
                    if r.status_code < 300:
                        burst_results["success"] += 1
                    else:
                        burst_results["errors"] += 1
                except Exception:
                    burst_results["errors"] += 1

        # Run burst in background so response is fast
        burst_thread = threading.Thread(target=send_burst, daemon=True)
        burst_thread.start()

        return jsonify({
            "status": "chaos_injected",
            "service": service_key,
            "service_name": service["name"],
            "mode": mode,
            "mode_description": CHAOS_MODES[mode],
            "failure_rate": failure_rate,
            "chaos_response": resp.json() if resp.ok else resp.text,
            "burst_started": True,
            "message": f"Chaos '{mode}' enabled on {service['name']}. 30 requests being sent to generate errors."
        })

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Failed to inject chaos: {str(e)}",
            "hint": "Ensure demo services are deployed. Run: ./scripts/deploy_demo_services.sh"
        }), 500


@app.route("/api/chaos/stop", methods=["POST"])
def stop_chaos():
    """Stop chaos on all services."""
    results = {}
    for key, service in DEMO_SERVICES.items():
        service_url = discover_service_url(service["name"])
        try:
            resp = http_requests.post(f"{service_url}/chaos/disable", timeout=10)
            results[key] = resp.json() if resp.ok else "failed"
        except Exception as e:
            results[key] = f"error: {str(e)}"

    return jsonify({"status": "chaos_stopped", "results": results})


@app.route("/api/chaos/status")
def chaos_status():
    """Get chaos status on all services."""
    results = {}
    for key, service in DEMO_SERVICES.items():
        service_url = discover_service_url(service["name"])
        try:
            resp = http_requests.get(f"{service_url}/chaos/status", timeout=5)
            results[key] = resp.json() if resp.ok else {"status": "unreachable"}
        except Exception:
            results[key] = {"status": "unreachable"}

    return jsonify(results)


# ---------------------------------------------------------------------------
# API Routes — Sentinel Trigger
# ---------------------------------------------------------------------------
@app.route("/api/detect", methods=["POST"])
def trigger_detection():
    """Trigger Sentinel agent to detect anomalies via Archestra A2A API."""
    time_window = request.json.get("time_window_minutes", 5) if request.is_json else 5

    if not ARCHESTRA_TOKEN:
        return jsonify({
            "error": "Archestra token not configured",
            "hint": "Set ARCHESTRA_TOKEN environment variable"
        }), 500

    try:
        # Call Archestra A2A endpoint to trigger Sentinel
        resp = http_requests.post(
            f"{ARCHESTRA_HUB_URL}/v1/a2a/{SENTINEL_AGENT_ID}",
            headers={
                "Authorization": f"Bearer {ARCHESTRA_TOKEN}",
                "Content-Type": "application/json"
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "message/send",
                "params": {
                    "message": {
                        "parts": [{
                            "kind": "text",
                            "text": f"Detect anomalies in the last {time_window} minutes and process all incidents through the full pipeline"
                        }]
                    }
                }
            },
            timeout=120
        )

        return jsonify({
            "status": "detection_triggered",
            "sentinel_response": resp.json() if resp.ok else resp.text,
            "message": "Sentinel agent is scanning for anomalies. Check incidents list for results."
        })

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Failed to trigger Sentinel: {str(e)}"
        }), 500


# ---------------------------------------------------------------------------
# API Routes — Services & Stats
# ---------------------------------------------------------------------------
@app.route("/api/services")
def list_services():
    """List all registered services."""
    services = query_db(
        """
        SELECT s.name, s.type, s.status, s.current_version, s.region,
               s.rollback_safety_score,
               COUNT(DISTINCT i.id) FILTER (WHERE i.status IN ('open', 'investigating')) as active_incidents
        FROM services s
        LEFT JOIN incidents i ON i.service_name = s.name
        GROUP BY s.id, s.name, s.type, s.status, s.current_version, s.region, s.rollback_safety_score
        ORDER BY s.name
        """
    )
    return jsonify({"services": services})


@app.route("/api/stats")
def platform_stats():
    """Get overall platform statistics."""
    stats = {}

    # Total incidents
    row = query_db("SELECT COUNT(*) as total FROM incidents", one=True)
    stats["total_incidents"] = row["total"] if row else 0

    # Active incidents
    row = query_db("SELECT COUNT(*) as total FROM incidents WHERE status IN ('open', 'investigating')", one=True)
    stats["active_incidents"] = row["total"] if row else 0

    # Resolved incidents
    row = query_db("SELECT COUNT(*) as total FROM incidents WHERE status = 'resolved'", one=True)
    stats["resolved_incidents"] = row["total"] if row else 0

    # Average resolution time
    row = query_db(
        "SELECT AVG(resolution_time_seconds) as avg_time FROM incidents WHERE resolution_time_seconds IS NOT NULL",
        one=True
    )
    stats["avg_resolution_time_seconds"] = round(row["avg_time"] or 0, 1) if row else 0

    # Pending approvals
    row = query_db(
        """SELECT COUNT(*) as total FROM audit_logs
        WHERE status = 'pending'
        AND action_type NOT IN ('produce_recommendation', 'detect_anomalies', 'acknowledge_incident')""",
        one=True
    )
    stats["pending_approvals"] = row["total"] if row else 0

    # Total agent actions
    row = query_db("SELECT COUNT(*) as total FROM audit_logs", one=True)
    stats["total_agent_actions"] = row["total"] if row else 0

    # Actions by agent
    agent_stats = query_db(
        """
        SELECT agent_name, COUNT(*) as actions,
               COUNT(*) FILTER (WHERE status = 'success') as successes,
               COUNT(*) FILTER (WHERE status = 'failed') as failures
        FROM audit_logs
        GROUP BY agent_name
        ORDER BY actions DESC
        """
    )
    stats["agent_breakdown"] = agent_stats

    # Recent activity (last 10 actions)
    recent = query_db(
        """
        SELECT al.agent_name, al.action_type, al.status, al.created_at,
               i.service_name, i.severity
        FROM audit_logs al
        LEFT JOIN incidents i ON i.id = al.incident_id
        ORDER BY al.created_at DESC
        LIMIT 10
        """
    )
    stats["recent_activity"] = recent

    return jsonify(stats), 200, {"Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info(f"Starting Nexus-Zero Dashboard on port {PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=os.environ.get("FLASK_DEBUG", "false").lower() == "true")
