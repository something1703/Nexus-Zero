"""
Executor Agent — Nexus-Zero Agent Parliament
=============================================
Gated remediation engine. Executes approved actions:
  - Rollback deployments via Cloud Run revision traffic splitting
  - Scale services up/down via Cloud Run instance controls
  - Apply config changes
  - Human-in-the-loop approval gate for high-risk actions

Every action is audit-logged. Nothing runs without explicit approval.
"""

import os
import sys
import json
import datetime

# In Docker: main.py at /app/main.py, common at /app/common/
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP
from common.config import PORT, HOST
from common.credential_store import get_credential, set_credential, get_all_credentials
from common.db_utils import (
    get_db_cursor,
    update_incident,
    create_audit_log,
    update_audit_log,
    record_config_change,
)

# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "Executor Agent",
    host=HOST,
    port=PORT
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
APPROVAL_TIMEOUT_MINUTES = 30  # Auto-reject if not approved within this window
ALLOWED_ACTION_TYPES = {"rollback", "scale", "restart", "config_change", "custom"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_pending_actions(incident_id: int | None = None) -> list[dict]:
    """Fetch audit-log entries that are pending approval."""
    with get_db_cursor() as cur:
        if incident_id:
            cur.execute(
                """
                SELECT al.id, al.incident_id, al.agent_name, al.action_type,
                       al.action_details, al.status, al.created_at,
                       i.title AS incident_title, i.severity
                FROM audit_logs al
                JOIN incidents i ON i.id = al.incident_id
                WHERE al.status = 'pending_approval'
                  AND al.incident_id = %s
                ORDER BY al.created_at ASC
                """,
                (incident_id,),
            )
        else:
            cur.execute(
                """
                SELECT al.id, al.incident_id, al.agent_name, al.action_type,
                       al.action_details, al.status, al.created_at,
                       i.title AS incident_title, i.severity
                FROM audit_logs al
                JOIN incidents i ON i.id = al.incident_id
                WHERE al.status = 'pending_approval'
                ORDER BY al.created_at ASC
                """
            )
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in rows]


def _simulate_rollback(service_name: str, details: dict) -> dict:
    """
    Simulate a Cloud Run rollback.
    In production this would call the Cloud Run Admin API to shift traffic
    back to a previous revision.
    """
    target_revision = details.get("target_revision", "previous")
    return {
        "action": "rollback",
        "service": service_name,
        "target_revision": target_revision,
        "status": "completed",
        "message": (
            f"Traffic for {service_name} shifted to revision '{target_revision}'. "
            "New instances are healthy."
        ),
        "simulated": True,
    }


def _simulate_scale(service_name: str, details: dict) -> dict:
    """
    Simulate scaling a Cloud Run service.
    In production this would update min/max instances via the Admin API.
    """
    direction = details.get("direction", "up")
    factor = details.get("factor", 2)
    return {
        "action": "scale",
        "service": service_name,
        "direction": direction,
        "factor": factor,
        "status": "completed",
        "message": (
            f"Scaled {service_name} {direction} by factor {factor}. "
            "Instance count updated."
        ),
        "simulated": True,
    }


def _simulate_restart(service_name: str, details: dict) -> dict:
    """Simulate restarting a Cloud Run service (deploy same image)."""
    return {
        "action": "restart",
        "service": service_name,
        "status": "completed",
        "message": f"Service {service_name} restarted with a fresh revision.",
        "simulated": True,
    }


def _simulate_config_change(service_name: str, details: dict) -> dict:
    """Simulate applying a config / env-var change."""
    config_key = details.get("config_key", "UNKNOWN")
    new_value = details.get("new_value", "UNKNOWN")
    return {
        "action": "config_change",
        "service": service_name,
        "config_key": config_key,
        "status": "completed",
        "message": f"Config '{config_key}' updated on {service_name}.",
        "simulated": True,
    }


ACTION_EXECUTORS = {
    "rollback": _simulate_rollback,
    "scale": _simulate_scale,
    "restart": _simulate_restart,
    "config_change": _simulate_config_change,
}

# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def get_pending_approvals(incident_id: int | None = None) -> str:
    """
    List all actions waiting for human approval.

    Args:
        incident_id: Optionally filter by a specific incident.

    Returns:
        JSON array of pending actions with audit-log IDs, action types,
        details, and the originating agent.
    """
    try:
        pending = _get_pending_actions(incident_id)

        if not pending:
            return json.dumps({
                "pending_count": 0,
                "actions": [],
                "message": "No actions are currently waiting for approval.",
            })

        # Check for timed-out actions and auto-reject them
        now = datetime.datetime.utcnow()
        active = []
        for action in pending:
            created = action["created_at"]
            if isinstance(created, str):
                created = datetime.datetime.fromisoformat(created)
            age_minutes = (now - created.replace(tzinfo=None)).total_seconds() / 60

            if age_minutes > APPROVAL_TIMEOUT_MINUTES:
                # Auto-reject expired actions
                update_audit_log(
                    action["id"],
                    status="rejected",
                    result={"reason": "auto-rejected: approval timeout exceeded"},
                )
            else:
                action["age_minutes"] = round(age_minutes, 1)
                action["timeout_remaining_minutes"] = round(
                    APPROVAL_TIMEOUT_MINUTES - age_minutes, 1
                )
                # Serialize datetime for JSON
                action["created_at"] = str(action["created_at"])
                active.append(action)

        return json.dumps({
            "pending_count": len(active),
            "actions": active,
        }, default=str)

    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def approve_action(audit_log_id: int, approved_by: str = "operator") -> str:
    """
    Approve a pending action and immediately execute it.

    This is the human-in-the-loop gate. Once approved the Executor runs
    the remediation (rollback / scale / restart / config change) and
    records the full result in the audit log.

    Args:
        audit_log_id: The audit_logs.id of the action to approve.
        approved_by:  Who approved it (operator name or "auto").

    Returns:
        JSON with execution result, or error if the action is invalid.
    """
    try:
        # Fetch the pending action
        with get_db_cursor() as cur:
            cur.execute(
                """
                SELECT al.id, al.incident_id, al.agent_name, al.action_type,
                       al.action_details, al.status,
                       i.affected_service
                FROM audit_logs al
                JOIN incidents i ON i.id = al.incident_id
                WHERE al.id = %s
                """,
                (audit_log_id,),
            )
            row = cur.fetchone()

        if not row:
            return json.dumps({"error": f"Audit log {audit_log_id} not found."})

        cols = ["id", "incident_id", "agent_name", "action_type",
                "action_details", "status", "affected_service"]
        action = dict(zip(cols, row))

        if action["status"] != "pending_approval":
            return json.dumps({
                "error": (
                    f"Action {audit_log_id} has status '{action['status']}' — "
                    "only 'pending_approval' actions can be approved."
                )
            })

        # Mark as executing
        update_audit_log(audit_log_id, status="executing")

        # Parse action details
        details = action["action_details"]
        if isinstance(details, str):
            details = json.loads(details)

        service_name = action["affected_service"] or details.get("service", "unknown")
        action_type = action["action_type"]

        # Execute the action
        executor = ACTION_EXECUTORS.get(action_type)
        if executor:
            result = executor(service_name, details)
        else:
            # Custom action — just mark as completed
            result = {
                "action": action_type,
                "service": service_name,
                "status": "completed",
                "message": f"Custom action '{action_type}' executed.",
                "simulated": True,
            }

        result["approved_by"] = approved_by
        result["approved_at"] = datetime.datetime.utcnow().isoformat()

        # Record config change if applicable
        if action_type == "config_change":
            record_config_change(
                service_name=service_name,
                change_type="env_var",
                old_value=details.get("old_value", {}),
                new_value=details.get("new_value", {}),
                changed_by=f"executor-agent (approved by {approved_by})",
            )

        # Update audit log with result
        update_audit_log(
            audit_log_id,
            status="completed",
            result=result,
        )

        # Update incident status to mitigated
        update_incident(
            action["incident_id"],
            status="mitigated",
            resolution_notes=(
                f"Action '{action_type}' on {service_name} executed successfully. "
                f"Approved by {approved_by}."
            ),
        )

        return json.dumps({
            "status": "executed",
            "audit_log_id": audit_log_id,
            "incident_id": action["incident_id"],
            "result": result,
        }, default=str)

    except Exception as e:
        # Mark as failed in audit log
        try:
            update_audit_log(audit_log_id, status="failed", result={"error": str(e)})
        except Exception:
            pass
        return json.dumps({"error": str(e)})


@mcp.tool()
def reject_action(audit_log_id: int, reason: str = "Rejected by operator") -> str:
    """
    Reject a pending action. The remediation will NOT be executed.

    Args:
        audit_log_id: The audit_logs.id of the action to reject.
        reason:       Human-readable reason for rejection.

    Returns:
        JSON confirmation of rejection.
    """
    try:
        with get_db_cursor() as cur:
            cur.execute(
                "SELECT id, status FROM audit_logs WHERE id = %s",
                (audit_log_id,),
            )
            row = cur.fetchone()

        if not row:
            return json.dumps({"error": f"Audit log {audit_log_id} not found."})

        if row[1] != "pending_approval":
            return json.dumps({
                "error": (
                    f"Action {audit_log_id} has status '{row[1]}' — "
                    "only 'pending_approval' actions can be rejected."
                )
            })

        update_audit_log(
            audit_log_id,
            status="rejected",
            result={"reason": reason, "rejected_at": datetime.datetime.utcnow().isoformat()},
        )

        return json.dumps({
            "status": "rejected",
            "audit_log_id": audit_log_id,
            "reason": reason,
        })

    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def execute_emergency_action(
    incident_id: int,
    action_type: str,
    service_name: str,
    action_details: str,
    operator: str = "emergency-auto",
) -> str:
    """
    Bypass the approval gate for P0/critical emergencies.

    Use ONLY when the incident severity is 'critical' and immediate
    action is required to prevent data loss or prolonged outage.
    A full audit trail is still recorded.

    Args:
        incident_id:    The incident driving this emergency action.
        action_type:    One of: rollback, scale, restart, config_change, custom.
        service_name:   The Cloud Run service to act on.
        action_details: JSON string with action-specific parameters.
        operator:       Who triggered the emergency (for audit).

    Returns:
        JSON with execution result.
    """
    try:
        if action_type not in ALLOWED_ACTION_TYPES:
            return json.dumps({
                "error": f"Unknown action_type '{action_type}'. Allowed: {sorted(ALLOWED_ACTION_TYPES)}"
            })

        # Verify the incident is actually critical
        with get_db_cursor() as cur:
            cur.execute(
                "SELECT id, severity, status FROM incidents WHERE id = %s",
                (incident_id,),
            )
            row = cur.fetchone()

        if not row:
            return json.dumps({"error": f"Incident {incident_id} not found."})

        severity = row[1]
        if severity != "critical":
            return json.dumps({
                "error": (
                    f"Emergency execution requires severity='critical', "
                    f"but incident {incident_id} has severity='{severity}'. "
                    "Use the normal approve_action flow instead."
                )
            })

        # Parse details
        details = json.loads(action_details) if isinstance(action_details, str) else action_details

        # Create audit log entry (skip pending, go straight to executing)
        audit_id = create_audit_log(
            incident_id=incident_id,
            agent_name="executor-agent",
            action_type=action_type,
            action_details=details,
            status="executing",
        )

        # Execute immediately
        executor = ACTION_EXECUTORS.get(action_type)
        if executor:
            result = executor(service_name, details)
        else:
            result = {
                "action": action_type,
                "service": service_name,
                "status": "completed",
                "message": f"Emergency custom action '{action_type}' executed.",
                "simulated": True,
            }

        result["emergency"] = True
        result["triggered_by"] = operator
        result["executed_at"] = datetime.datetime.utcnow().isoformat()

        # Update audit log
        update_audit_log(audit_id, status="completed", result=result)

        # Update incident
        update_incident(
            incident_id,
            status="mitigated",
            resolution_notes=(
                f"EMERGENCY: {action_type} on {service_name} by {operator}. "
                "Approval gate bypassed due to critical severity."
            ),
        )

        return json.dumps({
            "status": "emergency_executed",
            "audit_log_id": audit_id,
            "incident_id": incident_id,
            "result": result,
        }, default=str)

    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def get_execution_history(
    incident_id: int | None = None,
    limit: int = 20,
) -> str:
    """
    Retrieve the audit trail of all executed, rejected, and failed actions.

    Args:
        incident_id: Optionally filter by incident.
        limit:       Max rows to return (default 20).

    Returns:
        JSON array of audit log entries with full execution results.
    """
    try:
        with get_db_cursor() as cur:
            if incident_id:
                cur.execute(
                    """
                    SELECT al.id, al.incident_id, al.agent_name, al.action_type,
                           al.action_details, al.status, al.result,
                           al.created_at, al.completed_at,
                           i.title AS incident_title
                    FROM audit_logs al
                    JOIN incidents i ON i.id = al.incident_id
                    WHERE al.incident_id = %s
                      AND al.status IN ('completed', 'rejected', 'failed')
                    ORDER BY al.completed_at DESC NULLS LAST
                    LIMIT %s
                    """,
                    (incident_id, limit),
                )
            else:
                cur.execute(
                    """
                    SELECT al.id, al.incident_id, al.agent_name, al.action_type,
                           al.action_details, al.status, al.result,
                           al.created_at, al.completed_at,
                           i.title AS incident_title
                    FROM audit_logs al
                    JOIN incidents i ON i.id = al.incident_id
                    WHERE al.status IN ('completed', 'rejected', 'failed')
                    ORDER BY al.completed_at DESC NULLS LAST
                    LIMIT %s
                    """,
                    (limit,),
                )
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
            history = [dict(zip(cols, r)) for r in rows]

        return json.dumps({
            "total": len(history),
            "history": history,
        }, default=str)

    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def set_credentials(
    slack_bot_token: str = "",
    slack_channel: str = "",
) -> str:
    """
    Inject runtime credentials for this agent.

    Credentials are stored in memory only — they vanish when the container
    scales to zero.

    Args:
        slack_bot_token: Slack Bot token for posting remediation updates
        slack_channel:   Slack channel to post to (e.g. '#incidents')

    Returns:
        JSON confirmation of which credentials were set
    """
    set_creds = []
    if slack_bot_token:
        set_credential("SLACK_BOT_TOKEN", slack_bot_token)
        set_creds.append("SLACK_BOT_TOKEN")
    if slack_channel:
        set_credential("SLACK_CHANNEL", slack_channel)
        set_creds.append("SLACK_CHANNEL")

    return json.dumps({
        "status": "credentials_updated",
        "keys_set": set_creds,
        "stored_keys": list(get_all_credentials().keys()),
        "message": "Credentials stored in memory. They will be cleared when the container scales to zero."
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print(f"🚀 Executor Agent starting on {HOST}:{PORT}")
    mcp.run(transport="sse")
