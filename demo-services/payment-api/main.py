"""
Nexus-Zero Demo Service: Payment API
======================================
Simulates a Payment processing API with controllable chaos modes.

Chaos Modes:
  - memory_leak: Gradually allocates memory until OOMKilled
  - gateway_timeout: Simulates upstream payment gateway timeouts
  - normal: No chaos, all requests succeed

Endpoints:
  POST /process            - Process a payment (affected by chaos)
  GET  /transactions       - List recent transactions
  GET  /health             - Health check
  POST /chaos/enable       - Enable chaos mode
  POST /chaos/disable      - Disable chaos
  GET  /chaos/status       - Show current chaos state
"""

import os
import gc
import json
import time
import random
import logging
import threading
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
            "service": "payment-api",
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
logger = logging.getLogger("payment-api")

# ---- Chaos State ----
chaos_state = {
    "mode": "normal",
    "enabled_at": None,
    "request_count": 0,
    "error_count": 0,
    "failure_rate": 0.30,
}

# ---- Memory Leak Simulation ----
memory_leak_buffer = []
MEMORY_LEAK_CHUNK_MB = 5    # Each leaky request adds 5MB
MEMORY_LEAK_LIMIT_MB = 200  # OOMKill threshold (simulated)

# ---- Simulated Data ----
transactions_db = []
tx_counter = 5000


def _log_error(message, error_type="ServiceError", status_code=500, **extra):
    """Log a structured error for Cloud Logging / Sentinel detection."""
    record = logger.makeRecord(
        name="payment-api",
        level=logging.ERROR,
        fn="main.py",
        lno=0,
        msg=message,
        args=(),
        exc_info=None,
    )
    record.extra_fields = {
        "error_type": error_type,
        "httpRequest": {"status": status_code, "requestMethod": "POST", "requestUrl": "/process"},
        **extra,
    }
    logger.handle(record)


def _get_memory_usage_mb():
    """Get approximate memory used by leak buffer."""
    return len(memory_leak_buffer) * MEMORY_LEAK_CHUNK_MB


# ============================================
# Core Endpoints
# ============================================

@app.route("/process", methods=["POST"])
def process_payment():
    """Process a payment. Subject to chaos injection."""
    global tx_counter
    chaos_state["request_count"] += 1

    # ---- Chaos: Memory Leak ----
    if chaos_state["mode"] == "memory_leak":
        # Allocate memory that never gets freed
        leak_chunk = bytearray(MEMORY_LEAK_CHUNK_MB * 1024 * 1024)  # 5MB per request
        memory_leak_buffer.append(leak_chunk)
        current_mb = _get_memory_usage_mb()

        if current_mb >= MEMORY_LEAK_LIMIT_MB:
            chaos_state["error_count"] += 1
            _log_error(
                f"Container killed with exit code 137 (OOMKilled) - memory usage: {current_mb}MB",
                error_type="OutOfMemoryError",
                status_code=500,
                memory_usage_mb=current_mb,
                memory_limit_mb=MEMORY_LEAK_LIMIT_MB,
            )
            # Clear the buffer to simulate restart (but keep chaos mode on)
            memory_leak_buffer.clear()
            gc.collect()
            return jsonify({
                "error": "Out of Memory",
                "message": f"Container killed with exit code 137 (OOMKilled). Memory: {current_mb}MB / {MEMORY_LEAK_LIMIT_MB}MB",
                "service": "payment-api",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }), 500

        # Log warning if memory is getting high
        if current_mb > MEMORY_LEAK_LIMIT_MB * 0.7:
            logger.warning(f"⚠️ Memory usage high: {current_mb}MB / {MEMORY_LEAK_LIMIT_MB}MB")

    # ---- Chaos: Gateway Timeout ----
    if chaos_state["mode"] == "gateway_timeout":
        if random.random() < chaos_state["failure_rate"]:
            chaos_state["error_count"] += 1
            time.sleep(random.uniform(4.0, 10.0))
            _log_error(
                "Payment gateway timeout - Stripe API not responding after 5000ms",
                error_type="GatewayTimeout",
                status_code=504,
                gateway="stripe",
                timeout_ms=5000,
            )
            return jsonify({
                "error": "Gateway Timeout",
                "message": "Payment gateway (Stripe) not responding after 5000ms",
                "service": "payment-api",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }), 504

    # ---- Normal Operation ----
    tx_counter += 1
    data = request.get_json(silent=True) or {}
    amount = data.get("amount", round(random.uniform(10, 500), 2))
    tx = {
        "transaction_id": f"TXN-{tx_counter}",
        "order_id": data.get("order_id", f"ORD-{random.randint(1000, 9999)}"),
        "amount": amount,
        "currency": data.get("currency", "USD"),
        "status": "completed",
        "gateway": "stripe",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    transactions_db.append(tx)

    logger.info(f"Payment processed: {tx['transaction_id']} for ${amount}")
    return jsonify(tx), 201


@app.route("/transactions", methods=["GET"])
def list_transactions():
    """List recent transactions."""
    limit = request.args.get("limit", 20, type=int)
    return jsonify({
        "transactions": transactions_db[-limit:],
        "total": len(transactions_db),
    })


@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint."""
    mem_mb = _get_memory_usage_mb()
    is_healthy = chaos_state["mode"] == "normal" or (
        chaos_state["error_count"] < 10 and mem_mb < MEMORY_LEAK_LIMIT_MB * 0.8
    )
    status_code = 200 if is_healthy else 503

    health = {
        "service": "payment-api",
        "status": "healthy" if is_healthy else "degraded",
        "version": "v2.1.4",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "chaos_mode": chaos_state["mode"],
        "metrics": {
            "total_requests": chaos_state["request_count"],
            "total_errors": chaos_state["error_count"],
            "error_rate": round(
                chaos_state["error_count"] / max(chaos_state["request_count"], 1) * 100, 2
            ),
            "memory_usage_mb": mem_mb,
            "memory_limit_mb": MEMORY_LEAK_LIMIT_MB,
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
    mode = data.get("mode", "memory_leak")

    if mode not in ("memory_leak", "gateway_timeout"):
        return jsonify({"error": f"Unknown mode: {mode}. Use: memory_leak, gateway_timeout"}), 400

    chaos_state["mode"] = mode
    chaos_state["enabled_at"] = datetime.now(timezone.utc).isoformat()
    chaos_state["request_count"] = 0
    chaos_state["error_count"] = 0
    chaos_state["failure_rate"] = data.get("failure_rate", 0.30)

    # Clear memory leak buffer on fresh enable
    memory_leak_buffer.clear()
    gc.collect()

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
    memory_leak_buffer.clear()
    gc.collect()

    logger.info(f"🟢 CHAOS DISABLED: previous mode was {prev}")
    return jsonify({
        "status": "chaos_disabled",
        "previous_mode": prev,
        "total_errors_during_chaos": chaos_state["error_count"],
        "memory_freed_mb": _get_memory_usage_mb(),
    })


@app.route("/chaos/status", methods=["GET"])
def get_chaos_status():
    """Current chaos status."""
    return jsonify({**chaos_state, "memory_usage_mb": _get_memory_usage_mb()})


# ============================================
# Startup
# ============================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"🚀 Payment API starting on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
