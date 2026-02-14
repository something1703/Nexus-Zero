"""
Nexus-Zero Sentinel Agent
=========================
Monitors GCP Cloud Logging for anomalies and creates incidents.
This is the first agent in the investigation chain.

MCP Tools:
  - detect_anomalies: Scan GCP logs for errors and anomalies
  - get_service_health: Check current health of all monitored services
  - acknowledge_incident: Mark an incident as acknowledged
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone, timedelta

# Add directory containing main.py to path so 'common' package is found
# In Docker: main.py is at /app/main.py, common is at /app/common/
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP
from google.cloud import logging as gcp_logging

from common.config import GCP_PROJECT_ID, GCP_REGION, PORT, HOST
from common.credential_store import get_credential, set_credential, get_all_credentials
from common.db_utils import (
    create_incident, update_incident, get_open_incidents,
    get_all_services, create_audit_log, update_audit_log
)

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("sentinel-agent")

# Initialize MCP Server
mcp = FastMCP(
    "Nexus-Zero Sentinel Agent",
    host=HOST,
    port=PORT
)


def _classify_severity(error_count, error_rate_percent):
    """Classify incident severity based on error metrics."""
    if error_rate_percent >= 50 or error_count >= 100:
        return "critical"
    elif error_rate_percent >= 25 or error_count >= 50:
        return "high"
    elif error_rate_percent >= 10 or error_count >= 20:
        return "medium"
    else:
        return "low"


def _extract_error_signature(log_entry):
    """Extract a unique error signature from a log entry."""
    payload = log_entry.payload
    if isinstance(payload, dict):
        error_type = payload.get("error_type", "")
        status_code = str(payload.get("httpRequest", {}).get("status", ""))
        message = payload.get("message", "")[:100]
        return f"{error_type}_{status_code}_{hash(message) % 10000}"
    elif isinstance(payload, str):
        return f"text_{hash(payload[:200]) % 100000}"
    return "unknown_error"


def _extract_stack_trace(log_entry):
    """Extract stack trace from a log entry payload."""
    payload = log_entry.payload
    if isinstance(payload, dict):
        for key in ["stack_trace", "stackTrace", "exception", "traceback", "message"]:
            if key in payload:
                return str(payload[key])
        return json.dumps(payload, default=str)
    return str(payload)


@mcp.tool()
def detect_anomalies(
    time_window_minutes: int = 5,
    severity_filter: str = "ERROR"
) -> str:
    """
    Scan GCP Cloud Logging for errors and anomalies across all monitored services.

    Queries recent logs for errors, groups them by service, calculates error rates,
    and creates incident records for any anomalies detected.

    Args:
        time_window_minutes: How far back to scan for errors (default: 5 minutes)
        severity_filter: Minimum log severity to consider (default: ERROR)

    Returns:
        JSON report of detected anomalies and created incidents
    """
    logger.info(f"Scanning for anomalies (window={time_window_minutes}m, severity={severity_filter})")

    audit = create_audit_log(
        agent_name="sentinel",
        action_type="detect_anomalies",
        action_details={
            "time_window_minutes": time_window_minutes,
            "severity_filter": severity_filter
        }
    )

    try:
        client = gcp_logging.Client(project=GCP_PROJECT_ID)

        now = datetime.now(timezone.utc)
        start_time = now - timedelta(minutes=time_window_minutes)

        log_filter = (
            f'severity >= {severity_filter} '
            f'AND timestamp >= "{start_time.isoformat()}" '
            f'AND timestamp <= "{now.isoformat()}" '
            f'AND resource.type = "cloud_run_revision"'
        )

        entries = list(client.list_entries(
            filter_=log_filter,
            order_by=gcp_logging.DESCENDING,
            max_results=500,
            project_ids=[GCP_PROJECT_ID]
        ))

        if not entries:
            result = {
                "status": "all_clear",
                "message": f"No errors found in the last {time_window_minutes} minutes",
                "scanned_at": now.isoformat(),
                "incidents_created": 0
            }
            update_audit_log(audit["id"], status="success", result=result)
            return json.dumps(result, default=str)

        # Group errors by service
        service_errors = {}
        for entry in entries:
            service_name = "unknown"
            if hasattr(entry, "resource") and entry.resource:
                labels = entry.resource.labels or {}
                service_name = labels.get("service_name", labels.get("configuration_name", "unknown"))

            if service_name not in service_errors:
                service_errors[service_name] = {
                    "entries": [],
                    "error_count": 0,
                    "first_error": None,
                    "last_error": None
                }

            service_errors[service_name]["entries"].append(entry)
            service_errors[service_name]["error_count"] += 1

            ts = entry.timestamp
            if service_errors[service_name]["first_error"] is None or ts < service_errors[service_name]["first_error"]:
                service_errors[service_name]["first_error"] = ts
            if service_errors[service_name]["last_error"] is None or ts > service_errors[service_name]["last_error"]:
                service_errors[service_name]["last_error"] = ts

        # Create incidents for each affected service
        incidents_created = []
        for service_name, error_data in service_errors.items():
            error_count = error_data["error_count"]
            error_rate = min(100.0, (error_count / max(time_window_minutes, 1)) * 10)
            severity = _classify_severity(error_count, error_rate)

            sample_entry = error_data["entries"][0]
            error_signature = _extract_error_signature(sample_entry)
            error_message = str(sample_entry.payload)[:500] if sample_entry.payload else "Unknown error"
            stack_trace = _extract_stack_trace(sample_entry)

            incident = create_incident(
                service_name=service_name,
                severity=severity,
                error_signature=error_signature,
                error_message=error_message,
                stack_trace=stack_trace,
                region=GCP_REGION
            )

            incidents_created.append({
                "incident_id": str(incident["id"]),
                "service_name": service_name,
                "severity": severity,
                "error_count": error_count,
                "error_rate_percent": round(error_rate, 2),
                "error_signature": error_signature,
                "error_message": error_message[:200],
                "first_error": error_data["first_error"].isoformat() if error_data["first_error"] else None,
                "last_error": error_data["last_error"].isoformat() if error_data["last_error"] else None
            })

        result = {
            "status": "anomalies_detected",
            "scanned_at": now.isoformat(),
            "time_window_minutes": time_window_minutes,
            "total_errors": len(entries),
            "services_affected": len(service_errors),
            "incidents_created": len(incidents_created),
            "incidents": incidents_created
        }

        update_audit_log(audit["id"], status="success", result=result)
        logger.info(f"Detected {len(incidents_created)} anomalies across {len(service_errors)} services")
        return json.dumps(result, default=str)

    except Exception as e:
        error_msg = f"Failed to scan for anomalies: {str(e)}"
        logger.error(error_msg)
        update_audit_log(audit["id"], status="failed", error_message=error_msg)
        return json.dumps({"status": "error", "message": error_msg})


@mcp.tool()
def get_service_health() -> str:
    """
    Check current health status of all monitored services.

    Retrieves the status of all registered services from the database,
    including their current version, deployment info, and incident counts.

    Returns:
        JSON report of all services and their health status
    """
    logger.info("Checking service health")

    try:
        services = get_all_services()
        open_incidents = get_open_incidents()

        # Map incidents to services
        incident_map = {}
        for incident in open_incidents:
            svc = incident["service_name"]
            if svc not in incident_map:
                incident_map[svc] = []
            incident_map[svc].append({
                "id": str(incident["id"]),
                "severity": incident["severity"],
                "status": incident["status"],
                "error_message": incident["error_message"][:100] if incident["error_message"] else None
            })

        health_report = []
        for svc in services:
            svc_incidents = incident_map.get(svc["name"], [])
            effective_status = svc["status"]
            if svc_incidents:
                max_severity = min(
                    svc_incidents,
                    key=lambda i: ["critical", "high", "medium", "low"].index(i["severity"])
                )["severity"]
                if max_severity in ("critical", "high"):
                    effective_status = "down"
                elif max_severity == "medium":
                    effective_status = "degraded"

            health_report.append({
                "name": svc["name"],
                "type": svc["type"],
                "status": effective_status,
                "current_version": svc["current_version"],
                "region": svc["region"],
                "open_incidents": len(svc_incidents),
                "incidents": svc_incidents
            })

        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_services": len(services),
            "healthy": sum(1 for s in health_report if s["status"] == "healthy"),
            "degraded": sum(1 for s in health_report if s["status"] == "degraded"),
            "down": sum(1 for s in health_report if s["status"] == "down"),
            "services": health_report
        }

        return json.dumps(result, default=str)

    except Exception as e:
        error_msg = f"Failed to check service health: {str(e)}"
        logger.error(error_msg)
        return json.dumps({"status": "error", "message": error_msg})


@mcp.tool()
def acknowledge_incident(incident_id: str) -> str:
    """
    Acknowledge an incident and mark it as 'investigating'.

    Changes the incident status from 'open' to 'investigating' to indicate
    that the agent parliament has started working on it.

    Args:
        incident_id: UUID of the incident to acknowledge

    Returns:
        JSON confirmation of the status change
    """
    logger.info(f"Acknowledging incident: {incident_id}")

    try:
        updated = update_incident(incident_id, status="investigating")
        if not updated:
            return json.dumps({"status": "error", "message": f"Incident {incident_id} not found"})

        create_audit_log(
            agent_name="sentinel",
            action_type="acknowledge_incident",
            action_details={"incident_id": incident_id},
            incident_id=incident_id,
            status="success"
        )

        return json.dumps({
            "status": "acknowledged",
            "incident_id": incident_id,
            "new_status": "investigating",
            "message": f"Incident acknowledged. Investigation chain initiated."
        })

    except Exception as e:
        error_msg = f"Failed to acknowledge incident: {str(e)}"
        logger.error(error_msg)
        return json.dumps({"status": "error", "message": error_msg})


@mcp.tool()
def set_credentials() -> str:
    """
    Check credential status for this agent.

    The Sentinel Agent uses GCP service account credentials (automatic on
    Cloud Run) and does not require external API keys. This tool is provided
    for consistency across all agents.

    Returns:
        JSON status of current credentials
    """
    return json.dumps({
        "status": "ready",
        "agent": "sentinel",
        "gcp_project": GCP_PROJECT_ID,
        "gcp_region": GCP_REGION,
        "message": "Sentinel uses GCP service account credentials. No additional keys needed.",
        "stored_keys": list(get_all_credentials().keys()),
    })


if __name__ == "__main__":
    import sys
    import traceback
    
    try:
        logger.info(f"🚀 Starting Sentinel Agent on {HOST}:{PORT}")
        logger.info(f"   GCP Project: {GCP_PROJECT_ID}")
        logger.info(f"   Region: {GCP_REGION}")
        logger.info(f"   Python path: {sys.path[:3]}")
        logger.info("   Initializing FastMCP server...")
        
        # Start the server with explicit parameters
        mcp.run(transport="sse")
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        sys.exit(0)
    except Exception as e:
        logger.error(f"❌ Agent startup failed: {e}")
        logger.error(f"   Exception type: {type(e).__name__}")
        logger.error(f"   Traceback:\n{traceback.format_exc()}")
        sys.exit(1)
