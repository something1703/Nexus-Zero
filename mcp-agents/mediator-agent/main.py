"""
Nexus-Zero Mediator Agent
==========================
Performs risk assessment and blast radius analysis before approving actions.
Acts as the safety gate between investigation and execution.

MCP Tools:
  - analyze_blast_radius: Calculate impact of an action on dependent services
  - check_guardrails: Validate an action against safety policies
  - produce_recommendation: Final risk-assessed recommendation for human approval
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone

# In Docker: main.py at /app/main.py, common at /app/common/
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP
import google.generativeai as genai

from common.config import GEMINI_MODEL, PORT, HOST
from common.credential_store import get_credential, set_credential, get_all_credentials
from common.db_utils import (
    get_incident, get_service, get_service_dependencies,
    calculate_blast_radius, get_all_services,
    create_audit_log, update_audit_log
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("mediator-agent")

mcp = FastMCP(
    "Nexus-Zero Mediator Agent",
    host=HOST,
    port=PORT
)


def _get_gemini_model():
    """Get Gemini model, configuring API key from runtime store."""
    api_key = get_credential("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set. Call set_credentials first.")
    genai.configure(api_key=api_key)
    return genai.GenerativeModel(GEMINI_MODEL)

# Safety policies
GUARDRAILS = {
    "max_blast_radius": 5,
    "critical_services_require_approval": True,
    "rollback_always_allowed": True,
    "max_scale_factor": 3,
    "blocked_actions_in_peak_hours": ["restart", "scale_down"],
    "peak_hours_utc": (14, 22),
}


@mcp.tool()
def analyze_blast_radius(service_name: str) -> str:
    """
    Calculate the blast radius if a service goes down or is modified.

    Traverses the service dependency graph to find all directly and
    transitively dependent services. Reports criticality levels and
    total number of affected services.

    Args:
        service_name: Name of the service to analyze

    Returns:
        JSON report of blast radius with affected services and criticality
    """
    logger.info(f"Analyzing blast radius for: {service_name}")

    audit = create_audit_log(
        agent_name="mediator",
        action_type="analyze_blast_radius",
        action_details={"service_name": service_name}
    )

    try:
        service = get_service(service_name)
        if not service:
            return json.dumps({"status": "error", "message": f"Service '{service_name}' not found"})

        # Get direct dependencies
        direct_deps = get_service_dependencies(service_name)

        # Get transitive blast radius
        blast_radius = calculate_blast_radius(service_name)

        # Calculate risk score
        total_affected = len(blast_radius)
        critical_deps = [d for d in direct_deps if d["criticality"] == "critical"]
        high_deps = [d for d in direct_deps if d["criticality"] == "high"]

        risk_score = min(1.0, (
            (len(critical_deps) * 0.3) +
            (len(high_deps) * 0.15) +
            (total_affected * 0.05)
        ))

        risk_level = "low"
        if risk_score >= 0.7:
            risk_level = "critical"
        elif risk_score >= 0.5:
            risk_level = "high"
        elif risk_score >= 0.3:
            risk_level = "medium"

        result = {
            "status": "analysis_complete",
            "service_name": service_name,
            "service_type": service["type"],
            "current_status": service["status"],
            "current_version": service["current_version"],
            "rollback_safety_score": float(service["rollback_safety_score"]) if service["rollback_safety_score"] else None,
            "direct_dependents": [
                {
                    "service": d["dependent_service"],
                    "type": d["dependency_type"],
                    "criticality": d["criticality"]
                }
                for d in direct_deps
            ],
            "total_blast_radius": total_affected,
            "blast_radius_chain": [
                {"service": b["service_name"], "hops": b["hop_count"]}
                for b in blast_radius
            ],
            "risk_score": round(risk_score, 2),
            "risk_level": risk_level,
            "critical_dependencies": len(critical_deps),
            "high_dependencies": len(high_deps)
        }

        update_audit_log(audit["id"], status="success", result=result)
        return json.dumps(result, default=str)

    except Exception as e:
        error_msg = f"Blast radius analysis failed: {str(e)}"
        logger.error(error_msg)
        update_audit_log(audit["id"], status="failed", error_message=error_msg)
        return json.dumps({"status": "error", "message": error_msg})


@mcp.tool()
def check_guardrails(
    action_type: str,
    service_name: str,
    action_details: str = "{}"
) -> str:
    """
    Validate a proposed action against safety policies and guardrails.

    Checks the action against predefined safety rules including:
    - Blast radius limits
    - Critical service protection
    - Peak hour restrictions
    - Scale factor limits
    - Manager approval requirements

    Args:
        action_type: Type of action (rollback, scale_up, restart, config_change)
        service_name: Service the action targets
        action_details: JSON string of action parameters

    Returns:
        JSON report of guardrail check results (passed/blocked/warning)
    """
    logger.info(f"Checking guardrails: {action_type} on {service_name}")

    audit = create_audit_log(
        agent_name="mediator",
        action_type="check_guardrails",
        action_details={
            "action_type": action_type,
            "service_name": service_name,
            "action_details": action_details
        }
    )

    try:
        details = json.loads(action_details) if isinstance(action_details, str) else action_details

        checks = []
        all_passed = True
        requires_approval = False

        # Check 1: Blast radius
        blast_radius = calculate_blast_radius(service_name)
        blast_count = len(blast_radius)
        if blast_count > GUARDRAILS["max_blast_radius"]:
            checks.append({
                "check": "blast_radius",
                "status": "blocked",
                "message": f"Blast radius ({blast_count}) exceeds maximum ({GUARDRAILS['max_blast_radius']})",
                "affected_services": [b["service_name"] for b in blast_radius]
            })
            all_passed = False
        else:
            checks.append({
                "check": "blast_radius",
                "status": "passed",
                "message": f"Blast radius ({blast_count}) within limits"
            })

        # Check 2: Critical service protection
        direct_deps = get_service_dependencies(service_name)
        critical_deps = [d for d in direct_deps if d["criticality"] == "critical"]
        if critical_deps and GUARDRAILS["critical_services_require_approval"]:
            requires_approval = True
            checks.append({
                "check": "critical_dependency",
                "status": "warning",
                "message": f"Service has {len(critical_deps)} critical dependent(s). Requires human approval.",
                "critical_dependents": [d["dependent_service"] for d in critical_deps]
            })
        else:
            checks.append({
                "check": "critical_dependency",
                "status": "passed",
                "message": "No critical dependencies affected"
            })

        # Check 3: Rollback always allowed
        if action_type == "rollback" and GUARDRAILS["rollback_always_allowed"]:
            service = get_service(service_name)
            safety_score = float(service["rollback_safety_score"]) if service and service["rollback_safety_score"] else 0.5
            checks.append({
                "check": "rollback_safety",
                "status": "passed",
                "message": f"Rollback allowed (safety score: {safety_score})"
            })

        # Check 4: Peak hours restriction
        current_hour = datetime.now(timezone.utc).hour
        peak_start, peak_end = GUARDRAILS["peak_hours_utc"]
        is_peak = peak_start <= current_hour < peak_end
        if is_peak and action_type in GUARDRAILS["blocked_actions_in_peak_hours"]:
            checks.append({
                "check": "peak_hours",
                "status": "blocked",
                "message": f"Action '{action_type}' is blocked during peak hours ({peak_start}:00-{peak_end}:00 UTC)"
            })
            all_passed = False
        else:
            checks.append({
                "check": "peak_hours",
                "status": "passed",
                "message": "Not in peak hours or action is allowed"
            })

        # Check 5: Scale factor limit
        if action_type == "scale_up":
            scale_to = details.get("to", 1)
            scale_from = details.get("from", 1)
            if scale_from > 0 and scale_to / scale_from > GUARDRAILS["max_scale_factor"]:
                checks.append({
                    "check": "scale_factor",
                    "status": "blocked",
                    "message": f"Scale factor ({scale_to}/{scale_from}={scale_to/scale_from:.1f}x) exceeds maximum ({GUARDRAILS['max_scale_factor']}x)"
                })
                all_passed = False
            else:
                checks.append({
                    "check": "scale_factor",
                    "status": "passed",
                    "message": "Scale factor within limits"
                })

        overall_status = "blocked" if not all_passed else ("requires_approval" if requires_approval else "approved")

        result = {
            "status": overall_status,
            "action_type": action_type,
            "service_name": service_name,
            "all_checks_passed": all_passed,
            "requires_human_approval": requires_approval,
            "checks": checks,
            "total_checks": len(checks),
            "passed": sum(1 for c in checks if c["status"] == "passed"),
            "warnings": sum(1 for c in checks if c["status"] == "warning"),
            "blocked": sum(1 for c in checks if c["status"] == "blocked")
        }

        update_audit_log(audit["id"], status="success", result=result)
        return json.dumps(result, default=str)

    except Exception as e:
        error_msg = f"Guardrail check failed: {str(e)}"
        logger.error(error_msg)
        update_audit_log(audit["id"], status="failed", error_message=error_msg)
        return json.dumps({"status": "error", "message": error_msg})


@mcp.tool()
def produce_recommendation(
    incident_id: str,
    proposed_action: str,
    proposed_action_details: str = "{}"
) -> str:
    """
    Produce a final risk-assessed recommendation for human approval.

    Combines blast radius analysis, guardrail checks, and AI reasoning
    to produce a comprehensive recommendation with safety assessment.
    This is the final gate before the Executor Agent can act.

    Args:
        incident_id: UUID of the incident
        proposed_action: The proposed action (rollback, scale_up, restart, etc.)
        proposed_action_details: JSON string with action parameters

    Returns:
        JSON recommendation with risk assessment for human approval
    """
    logger.info(f"Producing recommendation for incident: {incident_id}")

    audit = create_audit_log(
        agent_name="mediator",
        action_type="produce_recommendation",
        action_details={
            "incident_id": incident_id,
            "proposed_action": proposed_action
        },
        incident_id=incident_id
    )

    try:
        incident = get_incident(incident_id)
        if not incident:
            return json.dumps({"status": "error", "message": f"Incident {incident_id} not found"})

        service_name = incident["service_name"]
        details = json.loads(proposed_action_details) if isinstance(proposed_action_details, str) else proposed_action_details

        # Run blast radius analysis
        blast_radius = calculate_blast_radius(service_name)
        direct_deps = get_service_dependencies(service_name)
        service = get_service(service_name)

        # Run guardrail checks
        guardrail_result = json.loads(check_guardrails(
            action_type=proposed_action,
            service_name=service_name,
            action_details=json.dumps(details)
        ))

        # Calculate overall safety score
        safety_score = 1.0
        risk_factors = []

        blast_count = len(blast_radius)
        if blast_count > 3:
            safety_score -= 0.3
            risk_factors.append(f"High blast radius ({blast_count} services affected)")
        elif blast_count > 0:
            safety_score -= 0.1
            risk_factors.append(f"Moderate blast radius ({blast_count} services affected)")

        if guardrail_result.get("blocked", 0) > 0:
            safety_score -= 0.4
            risk_factors.append("Some guardrail checks were blocked")

        if incident["severity"] == "critical":
            risk_factors.append("Critical severity incident - faster action needed")

        if service and service.get("rollback_safety_score"):
            rollback_score = float(service["rollback_safety_score"])
            if rollback_score < 0.5:
                safety_score -= 0.2
                risk_factors.append(f"Low rollback safety score ({rollback_score})")

        safety_score = max(0.0, min(1.0, safety_score))

        # AI-powered reasoning
        ai_reasoning = "Gemini not configured for deep reasoning"
        gemini_key = get_credential("GEMINI_API_KEY")
        if gemini_key:
            model = _get_gemini_model()
            prompt = f"""You are an SRE risk analyst. Evaluate this proposed remediation:

INCIDENT:
- Service: {service_name}
- Error: {incident['error_message'][:300]}
- Root Cause: {incident.get('root_cause', 'Under investigation')}
- Severity: {incident['severity']}

PROPOSED ACTION: {proposed_action}
DETAILS: {json.dumps(details)}

RISK FACTORS:
- Blast radius: {blast_count} services affected
- Direct dependencies: {json.dumps([d['dependent_service'] for d in direct_deps])}
- Guardrail status: {guardrail_result.get('status', 'unknown')}
- Safety score: {safety_score}

Provide a brief (3-4 sentences) risk assessment and your recommendation (proceed/caution/abort). Be concise."""

            response = model.generate_content(prompt)
            ai_reasoning = response.text.strip()

        # Determine final verdict
        if guardrail_result.get("status") == "blocked":
            verdict = "BLOCKED"
            verdict_reason = "Action blocked by guardrails. Manual override required."
        elif safety_score >= 0.7:
            verdict = "RECOMMENDED"
            verdict_reason = "Low risk. Safe to proceed with human approval."
        elif safety_score >= 0.4:
            verdict = "CAUTION"
            verdict_reason = "Medium risk. Proceed with careful monitoring."
        else:
            verdict = "NOT_RECOMMENDED"
            verdict_reason = "High risk. Consider alternative actions."

        requires_approval = (
            guardrail_result.get("requires_human_approval", True) or
            verdict in ("CAUTION", "NOT_RECOMMENDED", "BLOCKED")
        )

        result = {
            "status": "recommendation_ready",
            "incident_id": incident_id,
            "service_name": service_name,
            "incident_severity": incident["severity"],
            "proposed_action": proposed_action,
            "proposed_action_details": details,
            "verdict": verdict,
            "verdict_reason": verdict_reason,
            "safety_score": round(safety_score, 2),
            "risk_factors": risk_factors,
            "blast_radius": {
                "total_affected": blast_count,
                "services": [{"name": b["service_name"], "hops": b["hop_count"]} for b in blast_radius]
            },
            "guardrail_results": {
                "status": guardrail_result.get("status"),
                "passed": guardrail_result.get("passed", 0),
                "blocked": guardrail_result.get("blocked", 0),
                "warnings": guardrail_result.get("warnings", 0)
            },
            "ai_reasoning": ai_reasoning,
            "requires_human_approval": requires_approval,
            "estimated_downtime_seconds": details.get("expected_resolution_time_minutes", 5) * 60 if details.get("expected_resolution_time_minutes") else 300,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        update_audit_log(audit["id"], status="success", result=result)
        logger.info(f"Recommendation: {verdict} (safety={safety_score})")
        return json.dumps(result, default=str)

    except Exception as e:
        error_msg = f"Recommendation production failed: {str(e)}"
        logger.error(error_msg)
        update_audit_log(audit["id"], status="failed", error_message=error_msg)
        return json.dumps({"status": "error", "message": error_msg})


@mcp.tool()
def set_credentials(
    gemini_api_key: str = "",
) -> str:
    """
    Inject runtime credentials for this agent.

    Call this BEFORE using produce_recommendation with AI reasoning.
    Credentials are stored in memory only — they vanish when the container
    scales to zero.

    Args:
        gemini_api_key: Google Gemini API key for AI-powered risk reasoning

    Returns:
        JSON confirmation of which credentials were set
    """
    set_creds = []
    if gemini_api_key:
        set_credential("GEMINI_API_KEY", gemini_api_key)
        set_creds.append("GEMINI_API_KEY")

    logger.info(f"Credentials set: {set_creds}")
    return json.dumps({
        "status": "credentials_updated",
        "keys_set": set_creds,
        "stored_keys": list(get_all_credentials().keys()),
        "message": "Credentials stored in memory. They will be cleared when the container scales to zero."
    })


if __name__ == "__main__":
    logger.info(f"Starting Mediator Agent on {HOST}:{PORT}")
    mcp.run(transport="sse")
