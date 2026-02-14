"""
Nexus-Zero Demo Service: Notification API
==========================================
Simulates a Notification service with controllable chaos modes.

Chaos Modes:
  - cascade_failure: Fails when upstream order-api is down (circuit breaker tripped)
  - rate_limit: Simulates hitting external provider rate limits (e.g., SendGrid, Twilio)
  - normal: No chaos, all requests succeed

Endpoints:
  POST /notify             - Send a notification (affected by chaos)
  GET  /notifications      - List sent notifications
  GET  /health             - Health check
  POST /chaos/enable       - Enable chaos mode
  POST /chaos/disable      - Disable chaos
  GET  /chaos/status       - Show current chaos state
"""

import os
import json
import time
import random
import logging
from datetime import datetime, timezone
from flask import Flask, request, jsonify

app = Flask(__name__)

# ---- Structured JSON Logging ----
class StructuredFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service": "notification-api",
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
logger = logging.getLogger("notification-api")

# ---- Chaos State ----
chaos_state = {
    "mode": "normal",
    "enabled_at": None,
    "request_count": 0,
    "error_count": 0,
    "failure_rate": 0.40,
    "circuit_breaker": "closed",   # closed (ok) | open (failing) | half-open (testing)
    "consecutive_failures": 0,
    "circuit_breaker_threshold": 5,
}

# ---- Simulated Data ----
notifications_db = []
notification_counter = 2000

ORDER_API_URL = os.environ.get(
    "ORDER_API_URL",
    "https://nexus-order-api-833613368271.us-central1.run.app"
)


def _log_error(message, error_type="ServiceError", status_code=500, **extra):
    """Log a structured error for Sentinel Agent detection."""
    record = logger.makeRecord(
        name="notification-api",
        level=logging.ERROR,
        fn="main.py",
        lno=0,
        msg=message,
        args=(),
        exc_info=None,
    )
    record.extra_fields = {
        "error_type": error_type,
        "httpRequest": {"status": status_code, "requestMethod": "POST", "requestUrl": "/notify"},
        **extra,
    }
    logger.handle(record)


# ============================================
# Core Endpoints
# ============================================

@app.route("/notify", methods=["POST"])
def send_notification():
    """Send a notification. Subject to chaos injection."""
    global notification_counter
    chaos_state["request_count"] += 1

    # ---- Chaos: Circuit Breaker / Cascade Failure ----
    if chaos_state["mode"] == "cascade_failure":
        # Simulate circuit breaker logic
        if chaos_state["circuit_breaker"] == "open":
            chaos_state["error_count"] += 1
            _log_error(
                f"Circuit breaker OPEN for upstream order-api ({ORDER_API_URL}). "
                f"Consecutive failures: {chaos_state['consecutive_failures']}. "
                f"Requests are being rejected without attempting upstream call.",
                error_type="CircuitBreakerOpen",
                status_code=503,
                upstream_service="order-api",
                circuit_breaker_state="open",
                consecutive_failures=chaos_state["consecutive_failures"],
            )
            return jsonify({
                "error": "Service Unavailable",
                "message": "Circuit breaker is OPEN. Upstream order-api is unreachable.",
                "circuit_breaker": "open",
                "service": "notification-api",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }), 503

        # Simulate upstream failure
        if random.random() < chaos_state["failure_rate"]:
            chaos_state["consecutive_failures"] += 1
            chaos_state["error_count"] += 1

            # Trip the circuit breaker after N consecutive failures
            if chaos_state["consecutive_failures"] >= chaos_state["circuit_breaker_threshold"]:
                chaos_state["circuit_breaker"] = "open"
                logger.error(f"🔴 Circuit breaker TRIPPED after {chaos_state['consecutive_failures']} failures")

            time.sleep(random.uniform(1.0, 3.0))
            _log_error(
                f"Failed to fetch order details from order-api: Connection refused",
                error_type="UpstreamConnectionRefused",
                status_code=502,
                upstream_service="order-api",
                upstream_url=ORDER_API_URL,
            )
            return jsonify({
                "error": "Bad Gateway",
                "message": "Cannot reach upstream order-api to fetch order details",
                "service": "notification-api",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }), 502
        else:
            # Success resets consecutive failure count
            chaos_state["consecutive_failures"] = 0
            if chaos_state["circuit_breaker"] == "half-open":
                chaos_state["circuit_breaker"] = "closed"

    # ---- Chaos: Rate Limit ----
    if chaos_state["mode"] == "rate_limit":
        if random.random() < chaos_state["failure_rate"]:
            chaos_state["error_count"] += 1
            _log_error(
                "Rate limit exceeded on notification provider (SendGrid). "
                "429 Too Many Requests. Retry-After: 60 seconds.",
                error_type="RateLimitExceeded",
                status_code=429,
                provider="sendgrid",
                retry_after_seconds=60,
                daily_limit=1000,
                daily_used=1000,
            )
            return jsonify({
                "error": "Too Many Requests",
                "message": "SendGrid rate limit exceeded (1000/1000 daily). Retry after 60s.",
                "service": "notification-api",
                "retry_after": 60,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }), 429

    # ---- Normal Operation ----
    notification_counter += 1
    data = request.get_json(silent=True) or {}
    notification = {
        "notification_id": f"NTF-{notification_counter}",
        "order_id": data.get("order_id", f"ORD-{random.randint(1000, 9999)}"),
        "type": data.get("type", "order_confirmation"),
        "channel": data.get("channel", random.choice(["email", "sms", "push"])),
        "recipient": data.get("recipient", "customer@example.com"),
        "status": "sent",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    notifications_db.append(notification)

    logger.info(f"Notification sent: {notification['notification_id']} via {notification['channel']}")
    return jsonify(notification), 201


@app.route("/notifications", methods=["GET"])
def list_notifications():
    """List recent notifications."""
    limit = request.args.get("limit", 20, type=int)
    return jsonify({
        "notifications": notifications_db[-limit:],
        "total": len(notifications_db),
    })


@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint."""
    is_healthy = chaos_state["mode"] == "normal" or (
        chaos_state["error_count"] < 10 and chaos_state["circuit_breaker"] != "open"
    )
    status_code = 200 if is_healthy else 503

    health = {
        "service": "notification-api",
        "status": "healthy" if is_healthy else "degraded",
        "version": "v1.2.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "chaos_mode": chaos_state["mode"],
        "circuit_breaker": chaos_state["circuit_breaker"],
        "metrics": {
            "total_requests": chaos_state["request_count"],
            "total_errors": chaos_state["error_count"],
            "error_rate": round(
                chaos_state["error_count"] / max(chaos_state["request_count"], 1) * 100, 2
            ),
            "consecutive_failures": chaos_state["consecutive_failures"],
        },
    }
    return jsonify(health), status_code


# ============================================
# Chaos Control Endpoints
# ============================================

@app.route("/chaos/enable", methods=["POST"])
def enable_chaos():
    """Enable chaos mode."""
    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "cascade_failure")

    if mode not in ("cascade_failure", "rate_limit"):
        return jsonify({"error": f"Unknown mode: {mode}. Use: cascade_failure, rate_limit"}), 400

    chaos_state["mode"] = mode
    chaos_state["enabled_at"] = datetime.now(timezone.utc).isoformat()
    chaos_state["request_count"] = 0
    chaos_state["error_count"] = 0
    chaos_state["failure_rate"] = data.get("failure_rate", 0.40)
    chaos_state["circuit_breaker"] = "closed"
    chaos_state["consecutive_failures"] = 0

    logger.warning(f"🔴 CHAOS ENABLED: mode={mode}")
    return jsonify({
        "status": "chaos_enabled",
        "mode": mode,
        "failure_rate": chaos_state["failure_rate"],
        "message": f"Chaos mode '{mode}' active.",
    })


@app.route("/chaos/disable", methods=["POST"])
def disable_chaos():
    """Disable chaos."""
    prev = chaos_state["mode"]
    chaos_state["mode"] = "normal"
    chaos_state["enabled_at"] = None
    chaos_state["circuit_breaker"] = "closed"
    chaos_state["consecutive_failures"] = 0

    logger.info(f"🟢 CHAOS DISABLED: previous mode was {prev}")
    return jsonify({
        "status": "chaos_disabled",
        "previous_mode": prev,
        "total_errors_during_chaos": chaos_state["error_count"],
    })


@app.route("/chaos/status", methods=["GET"])
def get_chaos_status():
    """Current chaos status."""
    return jsonify(chaos_state)


# ============================================
# Startup
# ============================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"🚀 Notification API starting on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
