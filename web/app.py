"""
Nexus-Zero — Interactive Demo Dashboard Backend
================================================
Flask API serving the judge-facing dashboard.

Endpoints:
  GET  /                              — Landing page
  GET  /dashboard                     — Interactive dashboard UI
  GET  /how-it-works                  — How We Work deep-dive page
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
  GET  /api/agent-activity            — Agent thinking/reasoning feed
  POST /api/cleanup                   — Clean up stale records
"""

import os
import json
import logging
import uuid
import time
import threading
from datetime import datetime, timezone, timedelta
from functools import wraps
from concurrent.futures import ThreadPoolExecutor

import requests as http_requests
import psycopg2
import psycopg2.extras
import psycopg2.pool
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

@app.errorhandler(Exception)
def handle_exception(e):
    """Log and return errors properly."""
    logger.error(f"Unhandled exception: {str(e)}", exc_info=True)
    return jsonify({"error": str(e), "type": type(e).__name__}), 500

# ---------------------------------------------------------------------------
# Database Connection Pool
# ---------------------------------------------------------------------------
psycopg2.extras.register_uuid()

_db_pool = None
_pool_lock = threading.Lock()

def _get_pool():
    """Lazily create a threadsafe connection pool."""
    global _db_pool
    if _db_pool is None:
        with _pool_lock:
            if _db_pool is None:
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
                _db_pool = psycopg2.pool.ThreadedConnectionPool(1, 10, **params)
    return _db_pool


def get_db_connection():
    """Get a pooled database connection."""
    conn = _get_pool().getconn()
    conn.autocommit = True
    return conn


def put_db_connection(conn):
    """Return connection to pool."""
    try:
        _get_pool().putconn(conn)
    except Exception:
        pass


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
        put_db_connection(conn)


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
        put_db_connection(conn)


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
# Page Routes
# ---------------------------------------------------------------------------
@app.route("/")
def landing():
    """Serve the landing page."""
    return render_template("index.html")


@app.route("/dashboard")
def dashboard():
    """Serve the interactive dashboard."""
    return render_template("dashboard.html")


@app.route("/how-it-works")
def how_it_works():
    """Serve the How We Work page."""
    return render_template("how-it-works.html")


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
# API Routes — Runtime Configuration (Live Demo Credentials)
# ---------------------------------------------------------------------------
# In-memory credential store for Detective Agent (Gemini + GitHub)
# Judges can paste their own keys from the dashboard Settings modal.
# These propagate to Detective via the Bridge on save.
_runtime_config = {}
_config_lock = threading.Lock()

# Bridge URL for propagating credentials to agents
BRIDGE_URL = os.environ.get("BRIDGE_URL", "https://nexus-bridge-833613368271.us-central1.run.app")


def _get_config(key, env_fallback):
    """Return runtime override if set, else fall back to env var."""
    with _config_lock:
        val = _runtime_config.get(key)
    return val if val else globals().get(env_fallback, os.environ.get(env_fallback, ""))


@app.route("/api/config", methods=["GET"])
def get_config():
    """Return current Detective credential status (redacted)."""
    gemini_key = _get_config("GEMINI_API_KEY", "GEMINI_API_KEY")
    github_token = _get_config("GITHUB_TOKEN", "GITHUB_TOKEN")
    github_repo = _get_config("GITHUB_REPO", "GITHUB_REPO")
    return jsonify({
        "gemini_api_key_set": bool(gemini_key),
        "gemini_api_key_preview": f"{gemini_key[:8]}..." if gemini_key and len(gemini_key) > 8 else ("set" if gemini_key else "not set"),
        "github_token_set": bool(github_token),
        "github_token_preview": f"{github_token[:8]}..." if github_token and len(github_token) > 8 else ("set" if github_token else "not set"),
        "github_repo": github_repo or "not set",
        "detective_enabled": bool(gemini_key),
    })


@app.route("/api/config", methods=["POST"])
def update_config():
    """Update Detective Agent credentials and propagate to the agent via Bridge.
    Accepts: gemini_api_key, github_token, github_repo
    """
    data = request.get_json(force=True)
    updated = []
    creds_for_agent = {}

    with _config_lock:
        if data.get("gemini_api_key"):
            _runtime_config["GEMINI_API_KEY"] = data["gemini_api_key"].strip()
            creds_for_agent["gemini_api_key"] = data["gemini_api_key"].strip()
            updated.append("GEMINI_API_KEY")
        if data.get("github_token"):
            _runtime_config["GITHUB_TOKEN"] = data["github_token"].strip()
            creds_for_agent["github_token"] = data["github_token"].strip()
            updated.append("GITHUB_TOKEN")
        if data.get("github_repo"):
            _runtime_config["GITHUB_REPO"] = data["github_repo"].strip()
            creds_for_agent["github_repo"] = data["github_repo"].strip()
            updated.append("GITHUB_REPO")

    # Propagate credentials to Detective Agent via Bridge (fire-and-forget)
    if creds_for_agent and BRIDGE_URL:
        def _propagate():
            try:
                resp = http_requests.post(
                    f"{BRIDGE_URL}/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": 999,
                        "method": "tools/call",
                        "params": {
                            "name": "detective_set_credentials",
                            "arguments": creds_for_agent
                        }
                    },
                    timeout=10
                )
                if resp.status_code == 200:
                    logger.info(f"✅ Propagated credentials to Detective: {list(creds_for_agent.keys())}")
                else:
                    logger.warning(f"Bridge returned {resp.status_code}: {resp.text[:200]}")
            except Exception as e:
                logger.warning(f"Failed to propagate credentials to Detective: {e}")
        threading.Thread(target=_propagate, daemon=True).start()
    elif creds_for_agent and not BRIDGE_URL:
        logger.warning("BRIDGE_URL not configured — credentials saved locally but not propagated to Detective")

    return jsonify({"status": "updated", "keys": updated, "detective_enabled": "GEMINI_API_KEY" in updated or bool(_get_config("GEMINI_API_KEY", "GEMINI_API_KEY"))})


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
          AND al.incident_id IS NOT NULL
          AND al.action_type IN ('rollback', 'scale_up', 'restart', 'config_change')
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

    # Atomic transaction: Update audit log AND resolve incident in one query
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        # Update audit log + resolve incident in one round-trip
        cur.execute(
            """
            WITH updated_log AS (
                UPDATE audit_logs SET
                    status = 'success',
                    result = %s,
                    human_approved = TRUE,
                    approved_by = %s,
                    approved_at = NOW(),
                    completed_at = NOW()
                WHERE id = %s
            )
            UPDATE incidents SET
                status = 'resolved',
                resolution_action = %s,
                resolution_time_seconds = EXTRACT(EPOCH FROM (NOW() - created_at)),
                updated_at = NOW()
            WHERE id = %s
            """,
            (json.dumps(result, default=serialize), approved_by, approval_id,
             action_type, action["incident_id"])
        )
        cur.close()
    finally:
        put_db_connection(conn)

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

        # Send burst of requests in parallel to generate errors quickly
        def send_burst():
            def _hit(_i):
                try:
                    http_requests.post(
                        f"{service_url}{service['endpoint']}",
                        json={"customer": "chaos-demo", "amount": 99.99},
                        timeout=5
                    )
                except Exception:
                    pass
            with ThreadPoolExecutor(max_workers=10) as pool:
                pool.map(_hit, range(30))

        # Run burst in background so response is fast
        threading.Thread(target=send_burst, daemon=True).start()

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

    token = _get_config("ARCHESTRA_TOKEN", "ARCHESTRA_TOKEN")
    agent_id = _get_config("SENTINEL_AGENT_ID", "SENTINEL_AGENT_ID")
    hub_url = _get_config("ARCHESTRA_HUB_URL", "ARCHESTRA_HUB_URL")

    if not token:
        return jsonify({
            "error": "Archestra token not configured",
            "hint": "Open ⚙️ Settings on the dashboard to set your Archestra credentials, or set ARCHESTRA_TOKEN env var."
        }), 500

    if not agent_id:
        return jsonify({
            "error": "Sentinel Agent ID not configured",
            "hint": "Open ⚙️ Settings on the dashboard to set your Sentinel Agent ID."
        }), 500

    # Fire detection in background so the dashboard responds instantly
    def _run_detection(tw, _token, _agent_id, _hub_url):
        try:
            http_requests.post(
                f"{_hub_url}/v1/a2a/{_agent_id}",
                headers={
                    "Authorization": f"Bearer {_token}",
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
                                "text": f"Detect anomalies in the last {tw} minutes and process all incidents through the full pipeline"
                            }]
                        }
                    }
                },
                timeout=120
            )
            logger.info("Sentinel detection completed")
        except Exception as e:
            logger.error(f"Sentinel detection failed: {e}")

    threading.Thread(target=_run_detection, args=(time_window, token, agent_id, hub_url), daemon=True).start()

    return jsonify({
        "status": "detection_triggered",
        "message": "Sentinel agent dispatched. Watch the Agent Thinking panel for real-time updates."
    })


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
    """Get overall platform statistics — single query."""
    # One round-trip for all counters instead of 7 separate queries
    row = query_db(
        """
        SELECT
            (SELECT COUNT(*) FROM incidents) AS total_incidents,
            (SELECT COUNT(*) FROM incidents WHERE status IN ('open', 'investigating')) AS active_incidents,
            (SELECT COUNT(*) FROM incidents WHERE status = 'resolved') AS resolved_incidents,
            (SELECT COALESCE(ROUND(AVG(resolution_time_seconds)::numeric, 1), 0) FROM incidents WHERE resolution_time_seconds IS NOT NULL) AS avg_resolution_time_seconds,
            (SELECT COUNT(*) FROM audit_logs WHERE status = 'pending' AND incident_id IS NOT NULL AND action_type IN ('rollback', 'scale_up', 'restart', 'config_change')) AS pending_approvals,
            (SELECT COUNT(*) FROM audit_logs) AS total_agent_actions
        """,
        one=True
    )

    stats = {
        "total_incidents": row["total_incidents"],
        "active_incidents": row["active_incidents"],
        "resolved_incidents": row["resolved_incidents"],
        "avg_resolution_time_seconds": float(row["avg_resolution_time_seconds"]),
        "pending_approvals": row["pending_approvals"],
        "total_agent_actions": row["total_agent_actions"],
    }

    # Agent breakdown (small table, very fast)
    stats["agent_breakdown"] = query_db(
        """
        SELECT agent_name, COUNT(*) as actions,
               COUNT(*) FILTER (WHERE status = 'success') as successes,
               COUNT(*) FILTER (WHERE status = 'failed') as failures
        FROM audit_logs
        GROUP BY agent_name
        ORDER BY actions DESC
        """
    )

    # Recent activity (last 10 actions)
    stats["recent_activity"] = query_db(
        """
        SELECT al.agent_name, al.action_type, al.status, al.created_at,
               i.service_name, i.severity
        FROM audit_logs al
        LEFT JOIN incidents i ON i.id = al.incident_id
        ORDER BY al.created_at DESC
        LIMIT 10
        """
    )

    return jsonify(stats), 200, {"Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# API Routes — Agent Activity Feed (Explainability)
# ---------------------------------------------------------------------------
@app.route("/api/agent-activity")
def agent_activity():
    """Get recent agent activity with thinking/reasoning details for explainability."""
    limit = request.args.get("limit", 20, type=int)
    incident_id = request.args.get("incident_id", None)

    sql = """
        SELECT al.id, al.agent_name, al.action_type, al.status,
               al.action_details, al.result, al.error_message,
               al.created_at, al.completed_at, al.human_approved, al.approved_by,
               i.service_name, i.severity, i.error_signature, i.id as incident_id
        FROM audit_logs al
        LEFT JOIN incidents i ON i.id = al.incident_id
    """
    params = []
    if incident_id:
        sql += " WHERE al.incident_id = %s"
        params.append(incident_id)
    sql += " ORDER BY al.created_at DESC LIMIT %s"
    params.append(limit)

    logs = query_db(sql, params)

    activities = []
    for log in logs:
        agent = log["agent_name"]
        action = log["action_type"]
        result = log.get("result")
        details = log.get("action_details")

        # Parse JSON fields
        if isinstance(result, str):
            try: result = json.loads(result)
            except: pass
        if isinstance(details, str):
            try: details = json.loads(details)
            except: pass

        # Generate human-readable thinking summary
        thinking = _generate_agent_thinking(agent, action, log["status"], result, details, log.get("error_message"))

        activities.append({
            "id": log["id"],
            "agent": agent,
            "action": action,
            "status": log["status"],
            "service": log.get("service_name"),
            "severity": log.get("severity"),
            "incident_id": log.get("incident_id"),
            "thinking": thinking,
            "timestamp": log["created_at"],
            "completed": log.get("completed_at"),
            "human_approved": log.get("human_approved"),
        })

    return jsonify({"activities": activities, "count": len(activities)})


def _generate_agent_thinking(agent, action, status, result, details, error):
    """Generate human-readable reasoning for what the agent is doing.
    
    Extracts rich details from agent results to provide transparency into
    the autonomous decision-making process. This is the 'glass box' view
    of the agent parliament for judges and operators.
    """
    r = result or {}
    d = details or {}

    # ── Sentinel Agent ─────────────────────────────────────────────────────
    if agent == "sentinel":
        if action == "detect_anomalies":
            if status == "failed":
                return f"❌ GCP Cloud Logging scan failed: {error or 'unable to query logs'}. Check service account permissions."
            
            total = r.get("total_errors", 0)
            svcs = r.get("services_affected", 0)
            incidents = r.get("incidents_created", 0)
            window = d.get("time_window_minutes", 5)
            
            if total == 0:
                return f"✅ Scanned last {window} minutes of GCP Cloud Logging. All clear — no anomalies detected."
            
            # Extract incident details for richer display
            inc_list = r.get("incidents", [])
            if inc_list and len(inc_list) > 0:
                top_inc = inc_list[0]
                svc = top_inc.get("service_name", "unknown")
                sev = top_inc.get("severity", "?")
                err_count = top_inc.get("error_count", "?")
                return f"🚨 Scanned {window}m of logs → Found {total} errors across {svcs} services. Created {incidents} incidents. Top: {svc} ({sev}, {err_count} errors). Dispatching to Detective + Historian."
            
            return f"🔍 Scanned {window} minutes of GCP logs. Found {total} errors across {svcs} services. Created {incidents} incident(s) for investigation."
        
        if action == "acknowledge_incident":
            inc_id = d.get("incident_id", "")[:8] if d.get("incident_id") else "?"
            return f"📋 Acknowledged incident {inc_id}… → Status changed to 'investigating'. Dispatching to Detective (root cause) + Historian (solutions) in parallel."
        
        if action == "get_service_health":
            healthy = r.get("healthy", 0)
            degraded = r.get("degraded", 0)
            down = r.get("down", 0)
            total = r.get("total_services", 0)
            return f"🏥 Health check: {healthy}/{total} services healthy, {degraded} degraded, {down} down."

    # ── Detective Agent ────────────────────────────────────────────────────
    if agent == "detective":
        if action == "investigate_root_cause":
            if status == "failed":
                return f"❌ Root cause investigation failed: {error or 'unknown error'}. Falling back to Historian pattern-based recommendations."
            
            root_cause = r.get("root_cause", "")
            confidence = r.get("confidence_score", 0)
            suspect = r.get("suspect_commit", "")
            suspect_file = r.get("suspect_file", "")
            logs_analyzed = r.get("evidence", {}).get("log_entries_analyzed", 0) if isinstance(r.get("evidence"), dict) else 0
            commits_analyzed = r.get("evidence", {}).get("commits_analyzed", 0) if isinstance(r.get("evidence"), dict) else 0
            
            parts = [f"🔬 Investigation complete (confidence: {int(confidence*100)}%)"]
            if logs_analyzed:
                parts.append(f"Analyzed {logs_analyzed} log entries")
            if commits_analyzed:
                parts.append(f"correlated with {commits_analyzed} recent commits")
            if suspect:
                parts.append(f"Suspect commit: {suspect[:8]}")
            if suspect_file:
                parts.append(f"in {suspect_file}")
            if root_cause:
                # Truncate root cause to fit
                rc_short = root_cause[:150] + "…" if len(root_cause) > 150 else root_cause
                parts.append(f"→ {rc_short}")
            
            return ". ".join(parts[:3]) + (f". {parts[-1]}" if len(parts) > 3 else "")
        
        if action == "analyze_logs":
            if status == "failed":
                return f"❌ Log analysis failed: {error or 'Cloud Logging query error'}. Check GCP permissions or try wider time window."
            
            total_logs = r.get("total_log_entries", 0)
            patterns = r.get("unique_error_patterns", 0)
            window = d.get("time_window", 30)
            service = r.get("service_name", "")
            
            return f"📊 Pulled {total_logs} log entries from {service or 'target service'} (last {window}m). Identified {patterns} unique error patterns. Correlating with deployment history…"
        
        if action == "correlate_with_commits":
            if status == "failed":
                return f"❌ GitHub correlation failed: {error or 'check GITHUB_TOKEN'}. Enable via ⚙️ Settings for commit-based root cause analysis."
            
            commits = r.get("total_commits_analyzed", 0)
            top = r.get("top_suspect", {})
            
            if not commits:
                return "📝 No recent commits found in the analysis window. Root cause likely in infrastructure or external dependencies."
            
            if top:
                sha = top.get("sha", "")[:8]
                author = top.get("author", "")
                msg = top.get("message", "")[:50]
                score = top.get("suspicion_score", 0)
                return f"🔗 Analyzed {commits} commits. Top suspect: {sha} by {author} — \"{msg}\" (suspicion score: {score})"
            
            return f"🔗 Analyzed {commits} recent commits. No high-confidence suspects found."
        
        if action == "set_credentials":
            keys = r.get("keys_set", [])
            return f"🔐 Credentials updated: {', '.join(keys)}. Detective agent now has {'full' if 'GEMINI_API_KEY' in keys else 'partial'} analysis capabilities."

    # ── Historian Agent ────────────────────────────────────────────────────
    if agent == "historian":
        if action == "get_recommended_solutions":
            if status == "failed":
                return f"❌ Solution lookup failed: {error or 'playbook database error'}."
            
            sols = r.get("total_solutions", 0)
            top = r.get("top_recommendation", {})
            
            if not sols:
                return "📚 No matching solutions found in playbook database. This may be a novel incident type — consider manual investigation."
            
            top_action = top.get("action_type", "unknown")
            confidence = top.get("confidence", 0)
            source = top.get("source_name", "playbook")
            success_rate = top.get("historical_success_rate")
            
            parts = [f"📚 Found {sols} solutions in playbook database"]
            parts.append(f"Top recommendation: **{top_action}** (confidence: {int(confidence*100) if confidence < 1 else confidence}%)")
            parts.append(f"Source: '{source}'")
            if success_rate:
                parts.append(f"Historical success rate: {int(success_rate*100)}%")
            
            return ". ".join(parts) + ". Forwarding to Mediator for risk assessment."
        
        if action == "record_solution":
            return "💾 Recording this incident + resolution in history for future pattern matching."
        
        if action == "search_similar_incidents":
            found = r.get("similar_incidents_found", 0)
            return f"🔎 Searched incident history. Found {found} similar past incidents to analyze for patterns."

    # ── Mediator Agent ─────────────────────────────────────────────────────
    if agent == "mediator":
        if action == "produce_recommendation":
            if status == "failed":
                return f"❌ Risk assessment failed: {error or 'unknown'}."
            
            verdict = r.get("verdict", "unknown")
            safety = r.get("safety_score", 0)
            blast = r.get("blast_radius", {})
            affected = blast.get("total_affected", 0) if isinstance(blast, dict) else (blast if isinstance(blast, int) else 0)
            proposed = r.get("proposed_action", "unknown")
            risk_level = r.get("risk_level", "")
            
            emoji = "✅" if verdict in ("approve", "approved") else "⚠️" if verdict == "conditional" else "❌"
            
            parts = [f"{emoji} Risk assessment: {verdict.upper()}"]
            parts.append(f"Safety score: {int(safety*100) if safety < 1 else safety}%")
            if affected:
                parts.append(f"Blast radius: {affected} downstream services")
            if risk_level:
                parts.append(f"Risk level: {risk_level}")
            parts.append(f"Proposed action: {proposed}")
            
            if verdict in ("approve", "approved"):
                parts.append("→ Queued for human approval")
            
            return ". ".join(parts)
        
        if action == "check_guardrails":
            if status == "failed":
                return f"🛑 Guardrail check failed: {error or 'blocked by safety policy'}."
            
            checks = r.get("guardrail_results", [])
            passed = sum(1 for c in checks if c.get("status") == "passed") if checks else 0
            total = len(checks) if checks else 0
            
            return f"🛡️ Guardrail check: {passed}/{total} passed. Checking blast radius, change freeze windows, rollback safety, deployment velocity…"
        
        if action == "analyze_blast_radius":
            total = r.get("total_blast_radius", 0)
            risk = r.get("risk_level", "unknown")
            service = r.get("service_name", "")
            
            return f"💥 Blast radius for {service or 'target'}: {total} downstream services affected. Risk level: {risk}."

    # ── Executor Agent ─────────────────────────────────────────────────────
    if agent == "executor":
        if action == "get_pending_approvals":
            count = r.get("pending_count", 0)
            return f"📋 {count} actions awaiting human approval."
        
        if action == "approve_action":
            if status == "failed":
                return f"❌ Execution failed: {error or 'unknown error'}."
            
            exec_result = r.get("execution_result", {})
            action_type = exec_result.get("action", d.get("action_type", "unknown"))
            service = exec_result.get("service", d.get("service_name", ""))
            msg = exec_result.get("message", "")
            
            return f"✅ Executed **{action_type}** on {service or 'target'}. {msg}"
        
        if action == "reject_action":
            reason = r.get("reason", d.get("reason", ""))
            return f"🚫 Action rejected. Reason: {reason or 'operator decision'}"
        
        if action in ("rollback", "scale", "restart", "config_change"):
            if status == "pending":
                return f"⏳ **{action.replace('_', ' ').title()}** action pending human approval. Awaiting operator confirmation in Approvals panel."
            if status == "success":
                msg = r.get("message", "Action completed successfully")
                return f"✅ {msg}"
            if status == "failed":
                return f"❌ {action.replace('_', ' ').title()} failed: {error or 'execution error'}"
            return f"⚙️ Preparing {action.replace('_', ' ')} action…"

    # ── Fallback ───────────────────────────────────────────────────────────
    if status == "pending":
        return f"⏳ {action.replace('_', ' ').title()} action pending approval…"
    if status == "failed" and error:
        return f"❌ {action} failed: {error}"
    if status == "success":
        return f"✅ Completed {action.replace('_', ' ')} successfully."
    return f"⚙️ Processing {action.replace('_', ' ')}…"


@app.route("/api/cleanup", methods=["POST"])
def cleanup_stale_data():
    """Clean up stale/orphaned records."""
    # Remove orphaned pending records with no incident
    execute_db(
        "DELETE FROM audit_logs WHERE status = 'pending' AND incident_id IS NULL"
    )
    # Mark very old investigating incidents as closed
    execute_db(
        """
        UPDATE incidents SET status = 'closed', updated_at = NOW()
        WHERE status IN ('open', 'investigating')
        AND created_at < NOW() - INTERVAL '2 hours'
        """
    )
    return jsonify({"status": "cleaned", "message": "Stale records removed"})


# ---------------------------------------------------------------------------
# API Routes — Combined Dashboard Data (single request)
# ---------------------------------------------------------------------------
@app.route("/api/dashboard-data")
def dashboard_data():
    """Single endpoint returning all dashboard data in one shot.
    Replaces 4 parallel API calls with 1 request + 1 DB connection.
    """
    limit_incidents = request.args.get("limit", 30, type=int)
    status_filter = request.args.get("status", None)
    limit_activity = request.args.get("activity_limit", 15, type=int)

    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # 1) Stats — single query with sub-selects
        cur.execute("""
            SELECT
                (SELECT COUNT(*) FROM incidents) AS total_incidents,
                (SELECT COUNT(*) FROM incidents WHERE status IN ('open', 'investigating')) AS active_incidents,
                (SELECT COUNT(*) FROM incidents WHERE status = 'resolved') AS resolved_incidents,
                (SELECT COALESCE(ROUND(AVG(resolution_time_seconds)::numeric, 1), 0)
                 FROM incidents WHERE resolution_time_seconds IS NOT NULL) AS avg_resolution_time_seconds,
                (SELECT COUNT(*) FROM audit_logs
                 WHERE status = 'pending' AND incident_id IS NOT NULL
                 AND action_type IN ('rollback','scale_up','restart','config_change')) AS pending_approvals,
                (SELECT COUNT(*) FROM audit_logs) AS total_agent_actions
        """)
        stats_row = dict(cur.fetchone())
        stats_row["avg_resolution_time_seconds"] = float(stats_row["avg_resolution_time_seconds"])

        # 2) Incidents
        if status_filter:
            cur.execute("""
                SELECT id, service_name, severity, status, error_count,
                       error_signature, error_message, created_at, updated_at,
                       resolution_action, resolution_time_seconds
                FROM incidents WHERE status = %s
                ORDER BY created_at DESC LIMIT %s
            """, (status_filter, limit_incidents))
        else:
            cur.execute("""
                SELECT id, service_name, severity, status, error_count,
                       error_signature, error_message, created_at, updated_at,
                       resolution_action, resolution_time_seconds
                FROM incidents
                ORDER BY created_at DESC LIMIT %s
            """, (limit_incidents,))
        incidents = [dict(r) for r in cur.fetchall()]

        # 3) Pending approvals
        cur.execute("""
            SELECT al.id, al.incident_id, al.agent_name, al.action_type,
                   al.action_details, al.created_at,
                   i.service_name, i.severity, i.error_signature
            FROM audit_logs al
            JOIN incidents i ON i.id = al.incident_id
            WHERE al.status = 'pending'
              AND al.incident_id IS NOT NULL
              AND al.action_type IN ('rollback', 'scale_up', 'restart', 'config_change')
            ORDER BY al.created_at DESC
        """)
        approvals = [dict(r) for r in cur.fetchall()]
        for a in approvals:
            if isinstance(a.get("action_details"), str):
                try:
                    a["action_details"] = json.loads(a["action_details"])
                except Exception:
                    pass

        # 4) Agent activity
        cur.execute("""
            SELECT al.id, al.agent_name, al.action_type, al.status,
                   al.action_details, al.result, al.error_message,
                   al.created_at, al.completed_at, al.human_approved, al.approved_by,
                   i.service_name, i.severity, i.error_signature, i.id as incident_id
            FROM audit_logs al
            LEFT JOIN incidents i ON i.id = al.incident_id
            ORDER BY al.created_at DESC LIMIT %s
        """, (limit_activity,))
        raw_logs = [dict(r) for r in cur.fetchall()]

        cur.close()
    finally:
        put_db_connection(conn)

    # Process agent activity (in Python, no extra DB calls)
    activities = []
    for log in raw_logs:
        agent = log["agent_name"]
        action = log["action_type"]
        result = log.get("result")
        details = log.get("action_details")
        if isinstance(result, str):
            try: result = json.loads(result)
            except: pass
        if isinstance(details, str):
            try: details = json.loads(details)
            except: pass
        thinking = _generate_agent_thinking(agent, action, log["status"], result, details, log.get("error_message"))
        activities.append({
            "id": log["id"],
            "agent": agent,
            "action": action,
            "status": log["status"],
            "service": log.get("service_name"),
            "severity": log.get("severity"),
            "incident_id": log.get("incident_id"),
            "thinking": thinking,
            "timestamp": log["created_at"],
            "completed": log.get("completed_at"),
            "human_approved": log.get("human_approved"),
        })

    return jsonify({
        "stats": stats_row,
        "incidents": incidents,
        "approvals": {"count": len(approvals), "actions": approvals},
        "activities": {"count": len(activities), "activities": activities},
    }), 200, {"Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info(f"Starting Nexus-Zero Dashboard on port {PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=os.environ.get("FLASK_DEBUG", "false").lower() == "true")
