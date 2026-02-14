"""
Nexus-Zero Demo Service: Order API
====================================
Simulates a real-world Order API with controllable chaos modes.

Chaos Modes:
  - db_pool_exhaustion: Simulates database connection pool exhaustion (30% 500s)
  - cascading_timeout: Calls payment-api, cascading failures
  - normal: No chaos, all requests succeed

Endpoints:
  POST /order              - Create a new order (affected by chaos)
  GET  /orders             - List recent orders
  GET  /health             - Health check (structured JSON for Cloud Logging)
  POST /chaos/enable       - Enable chaos mode (db_pool_exhaustion | cascading_timeout)
  POST /chaos/disable      - Disable chaos, return to normal
  GET  /chaos/status       - Show current chaos state
"""

import os
import json
import time
import random
import logging
import traceback
from datetime import datetime, timezone
from flask import Flask, request, jsonify

app = Flask(__name__)

# ---- Structured JSON Logging for Cloud Logging ----
class StructuredFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service": "order-api",
            "logger": record.name,
        }
        if record.exc_info and record.exc_info[0]:
            log_entry["stack_trace"] = self.formatException(record.exc_info)
            log_entry["error_type"] = record.exc_info[0].__name__
        if hasattr(record, "extra_fields"):
            log_entry.update(record.extra_fields)
        return json.dumps(log_entry)

handler = logging.StreamHandler()
handler.setFormatter(StructuredFormatter())
logging.root.handlers = [handler]
logging.root.setLevel(logging.INFO)
logger = logging.getLogger("order-api")

# ---- Chaos State ----
chaos_state = {
    "mode": "normal",           # normal | db_pool_exhaustion | cascading_timeout
    "enabled_at": None,
    "request_count": 0,
    "error_count": 0,
    "failure_rate": 0.30,       # 30% failure rate when chaos enabled
}

# ---- Simulated Data ----
orders_db = []
order_counter = 1000

# Payment API URL (for cascading timeout chaos)
PAYMENT_API_URL = os.environ.get(
    "PAYMENT_API_URL",
    "https://nexus-payment-api-833613368271.us-central1.run.app"
)


def _log_error(message, error_type="ServiceError", status_code=500, **extra):
    """Log a structured error that Sentinel Agent will detect."""
    record = logger.makeRecord(
        name="order-api",
        level=logging.ERROR,
        fn="main.py",
        lno=0,
        msg=message,
        args=(),
        exc_info=None,
    )
    record.extra_fields = {
        "error_type": error_type,
        "httpRequest": {"status": status_code, "requestMethod": "POST", "requestUrl": "/order"},
        **extra,
    }
    logger.handle(record)


# ============================================
# Core Endpoints
# ============================================

@app.route("/order", methods=["POST"])
def create_order():
    """Create a new order. Subject to chaos injection."""
    global order_counter
    chaos_state["request_count"] += 1

    # ---- Chaos: DB Pool Exhaustion ----
    if chaos_state["mode"] == "db_pool_exhaustion":
        if random.random() < chaos_state["failure_rate"]:
            chaos_state["error_count"] += 1
            # Simulate connection wait time
            time.sleep(random.uniform(0.5, 2.0))
            _log_error(
                "FATAL: remaining connection slots are reserved for non-replication superuser connections",
                error_type="DatabasePoolExhaustion",
                status_code=500,
                db_pool_active=47,
                db_pool_max=50,
                db_pool_waiting=23,
            )
            return jsonify({
                "error": "Database connection pool exhausted",
                "message": "FATAL: remaining connection slots are reserved for non-replication superuser connections",
                "service": "order-api",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }), 500

    # ---- Chaos: Cascading Timeout ----
    if chaos_state["mode"] == "cascading_timeout":
        if random.random() < chaos_state["failure_rate"]:
            chaos_state["error_count"] += 1
            # Simulate waiting for payment-api that's timing out
            time.sleep(random.uniform(3.0, 8.0))
            _log_error(
                f"Timeout calling downstream service: payment-api at {PAYMENT_API_URL}/process",
                error_type="DownstreamTimeout",
                status_code=504,
                downstream_service="payment-api",
                timeout_seconds=5,
            )
            return jsonify({
                "error": "Gateway Timeout",
                "message": f"Timeout waiting for payment-api to process payment",
                "service": "order-api",
                "downstream": "payment-api",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }), 504

    # ---- Normal Operation ----
    order_counter += 1
    data = request.get_json(silent=True) or {}
    order = {
        "order_id": f"ORD-{order_counter}",
        "customer": data.get("customer", "demo-customer"),
        "amount": data.get("amount", round(random.uniform(10, 500), 2)),
        "status": "created",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    orders_db.append(order)

    logger.info(f"Order created: {order['order_id']} for ${order['amount']}")
    return jsonify(order), 201


@app.route("/orders", methods=["GET"])
def list_orders():
    """List recent orders."""
    limit = request.args.get("limit", 20, type=int)
    return jsonify({
        "orders": orders_db[-limit:],
        "total": len(orders_db),
    })


@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint for monitoring."""
    is_healthy = chaos_state["mode"] == "normal" or chaos_state["error_count"] < 10
    status_code = 200 if is_healthy else 503

    health = {
        "service": "order-api",
        "status": "healthy" if is_healthy else "degraded",
        "version": "v1.8.1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "chaos_mode": chaos_state["mode"],
        "metrics": {
            "total_requests": chaos_state["request_count"],
            "total_errors": chaos_state["error_count"],
            "error_rate": round(
                chaos_state["error_count"] / max(chaos_state["request_count"], 1) * 100, 2
            ),
        },
    }
    return jsonify(health), status_code


# ============================================
# Chaos Control Endpoints
# ============================================

@app.route("/chaos/enable", methods=["POST"])
def enable_chaos():
    """Enable chaos mode. Body: {"mode": "db_pool_exhaustion"} or {"mode": "cascading_timeout"}"""
    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "db_pool_exhaustion")

    if mode not in ("db_pool_exhaustion", "cascading_timeout"):
        return jsonify({"error": f"Unknown chaos mode: {mode}. Use: db_pool_exhaustion, cascading_timeout"}), 400

    chaos_state["mode"] = mode
    chaos_state["enabled_at"] = datetime.now(timezone.utc).isoformat()
    chaos_state["request_count"] = 0
    chaos_state["error_count"] = 0
    chaos_state["failure_rate"] = data.get("failure_rate", 0.30)

    logger.warning(f"🔴 CHAOS ENABLED: mode={mode}, failure_rate={chaos_state['failure_rate']}")
    return jsonify({
        "status": "chaos_enabled",
        "mode": mode,
        "failure_rate": chaos_state["failure_rate"],
        "message": f"Chaos mode '{mode}' is now active. {int(chaos_state['failure_rate']*100)}% of requests will fail.",
    })


@app.route("/chaos/disable", methods=["POST"])
def disable_chaos():
    """Disable chaos, return to normal operation."""
    prev_mode = chaos_state["mode"]
    chaos_state["mode"] = "normal"
    chaos_state["enabled_at"] = None

    logger.info(f"🟢 CHAOS DISABLED: previous mode was {prev_mode}")
    return jsonify({
        "status": "chaos_disabled",
        "previous_mode": prev_mode,
        "total_errors_during_chaos": chaos_state["error_count"],
        "message": "System returned to normal operation.",
    })


@app.route("/chaos/status", methods=["GET"])
def chaos_status():
    """Get current chaos state."""
    return jsonify(chaos_state)


# ============================================
# Startup
# ============================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"🚀 Order API starting on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
