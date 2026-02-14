"""
Nexus-Zero Historian Agent
===========================
Searches incident history and playbooks for similar past incidents.
Uses pattern matching and pgvector similarity to find known solutions.

MCP Tools:
  - search_playbooks: Find playbooks matching an error pattern
  - find_similar_incidents: Search historical incidents by similarity
  - get_recommended_solutions: Get ranked solutions for an incident
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server.fastmcp import FastMCP

from common.config import PORT, HOST
from common.db_utils import (
    get_incident, search_playbooks_by_pattern,
    find_similar_past_incidents, get_playbook_with_solutions,
    create_audit_log, update_audit_log
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("historian-agent")

mcp = FastMCP(
    "Nexus-Zero Historian Agent",
    description="Searches incident history and playbooks for similar past incidents and known solutions."
)


@mcp.tool()
def search_playbooks(
    error_message: str,
    service_name: str = ""
) -> str:
    """
    Search the playbook library for solutions matching an error pattern.

    Queries the playbooks database using regex pattern matching against the
    error message. Returns matching playbooks ranked by success rate, along
    with their recommended solutions.

    Args:
        error_message: The error message or pattern to search for
        service_name: Optional service name to narrow the search

    Returns:
        JSON report of matching playbooks with ranked solutions
    """
    logger.info(f"Searching playbooks for: {error_message[:100]}")

    audit = create_audit_log(
        agent_name="historian",
        action_type="search_playbooks",
        action_details={
            "error_message": error_message[:200],
            "service_name": service_name
        }
    )

    try:
        matches = search_playbooks_by_pattern(error_message)

        # Filter by service name if provided
        if service_name and matches:
            filtered = []
            for match in matches:
                svc_pattern = match.get("service_pattern", "")
                if not svc_pattern or service_name in svc_pattern:
                    filtered.append(match)
            if filtered:
                matches = filtered

        if not matches:
            result = {
                "status": "no_playbooks_found",
                "message": "No matching playbooks found for this error pattern",
                "search_query": error_message[:200],
                "recommendation": "This may be a new type of incident. Consider creating a playbook after resolution."
            }
            update_audit_log(audit["id"], status="success", result=result)
            return json.dumps(result, default=str)

        playbook_results = []
        for match in matches:
            solutions = match.get("solutions", [])
            if isinstance(solutions, str):
                solutions = json.loads(solutions)

            # Filter out null solutions
            solutions = [s for s in solutions if s and s.get("action_type")]

            playbook_results.append({
                "playbook_id": str(match["id"]),
                "name": match["name"],
                "description": match["description"],
                "category": match["category"],
                "success_rate": float(match["success_rate"]) if match["success_rate"] else 0,
                "avg_resolution_time_minutes": match["avg_resolution_time_minutes"],
                "times_used": match["times_used"],
                "solutions": solutions
            })

        result = {
            "status": "playbooks_found",
            "total_matches": len(playbook_results),
            "search_query": error_message[:200],
            "playbooks": playbook_results,
            "top_recommendation": playbook_results[0] if playbook_results else None
        }

        update_audit_log(audit["id"], status="success", result=result)
        logger.info(f"Found {len(playbook_results)} matching playbooks")
        return json.dumps(result, default=str)

    except Exception as e:
        error_msg = f"Playbook search failed: {str(e)}"
        logger.error(error_msg)
        update_audit_log(audit["id"], status="failed", error_message=error_msg)
        return json.dumps({"status": "error", "message": error_msg})


@mcp.tool()
def find_similar_incidents(
    incident_id: str,
    max_results: int = 5
) -> str:
    """
    Search for historical incidents similar to the current one.

    Uses error signature matching and text similarity to find past incidents
    that had similar symptoms. Returns their root causes and resolutions
    to help inform the current investigation.

    Args:
        incident_id: UUID of the current incident to find matches for
        max_results: Maximum number of similar incidents to return

    Returns:
        JSON report of similar past incidents with their resolutions
    """
    logger.info(f"Finding similar incidents for: {incident_id}")

    audit = create_audit_log(
        agent_name="historian",
        action_type="find_similar_incidents",
        action_details={"incident_id": incident_id, "max_results": max_results},
        incident_id=incident_id
    )

    try:
        incident = get_incident(incident_id)
        if not incident:
            return json.dumps({"status": "error", "message": f"Incident {incident_id} not found"})

        similar = find_similar_past_incidents(
            error_signature=incident["error_signature"],
            service_name=incident["service_name"],
            limit=max_results
        )

        if not similar:
            # Try broader search without service filter
            similar = find_similar_past_incidents(
                error_signature=incident["error_signature"],
                limit=max_results
            )

        if not similar:
            result = {
                "status": "no_similar_incidents",
                "incident_id": incident_id,
                "message": "No similar past incidents found. This appears to be a novel incident.",
                "recommendation": "Proceed with fresh investigation using Detective Agent findings."
            }
            update_audit_log(audit["id"], status="success", result=result)
            return json.dumps(result, default=str)

        similar_results = []
        for inc in similar:
            similar_results.append({
                "incident_id": str(inc["id"]),
                "service_name": inc["service_name"],
                "severity": inc["severity"],
                "error_signature": inc["error_signature"],
                "error_message": inc["error_message"][:200] if inc["error_message"] else None,
                "root_cause": inc["root_cause"],
                "resolution_action": inc["resolution_action"],
                "resolution_time_seconds": inc["resolution_time_seconds"],
                "confidence_score": float(inc["confidence_score"]) if inc["confidence_score"] else None,
                "occurred_at": inc["first_seen_at"].isoformat() if inc["first_seen_at"] else None,
                "resolved_at": inc["resolved_at"].isoformat() if inc["resolved_at"] else None
            })

        result = {
            "status": "similar_incidents_found",
            "incident_id": incident_id,
            "total_matches": len(similar_results),
            "similar_incidents": similar_results,
            "most_relevant": similar_results[0] if similar_results else None,
            "pattern_summary": f"Found {len(similar_results)} past incidents with similar error signature '{incident['error_signature']}'"
        }

        update_audit_log(audit["id"], status="success", result=result)
        logger.info(f"Found {len(similar_results)} similar past incidents")
        return json.dumps(result, default=str)

    except Exception as e:
        error_msg = f"Similar incident search failed: {str(e)}"
        logger.error(error_msg)
        update_audit_log(audit["id"], status="failed", error_message=error_msg)
        return json.dumps({"status": "error", "message": error_msg})


@mcp.tool()
def get_recommended_solutions(incident_id: str) -> str:
    """
    Get ranked recommended solutions for an incident.

    Combines playbook matching and historical incident analysis to produce
    a ranked list of recommended actions. Each solution includes success
    rates, prerequisites, and expected resolution times.

    Args:
        incident_id: UUID of the incident needing solutions

    Returns:
        JSON report with ranked solutions and confidence levels
    """
    logger.info(f"Getting solutions for incident: {incident_id}")

    audit = create_audit_log(
        agent_name="historian",
        action_type="get_recommended_solutions",
        action_details={"incident_id": incident_id},
        incident_id=incident_id
    )

    try:
        incident = get_incident(incident_id)
        if not incident:
            return json.dumps({"status": "error", "message": f"Incident {incident_id} not found"})

        # Search playbooks for matching solutions
        error_msg = incident.get("error_message", "") or incident.get("error_signature", "")
        playbooks = search_playbooks_by_pattern(error_msg)

        # Search for similar past incidents
        similar = find_similar_past_incidents(
            error_signature=incident["error_signature"],
            service_name=incident["service_name"]
        )

        # Build ranked solution list
        solutions = []

        # Add playbook solutions
        for pb in playbooks:
            pb_solutions = pb.get("solutions", [])
            if isinstance(pb_solutions, str):
                pb_solutions = json.loads(pb_solutions)

            for sol in pb_solutions:
                if not sol or not sol.get("action_type"):
                    continue
                solutions.append({
                    "source": "playbook",
                    "source_name": pb["name"],
                    "action_type": sol["action_type"],
                    "action_details": sol["action_details"],
                    "prerequisites": sol.get("prerequisites"),
                    "post_checks": sol.get("post_checks"),
                    "expected_resolution_time_minutes": sol.get("expected_resolution_time_minutes"),
                    "success_rate": float(pb["success_rate"]) if pb["success_rate"] else 0,
                    "times_used": pb["times_used"],
                    "confidence": float(pb["success_rate"]) / 100 if pb["success_rate"] else 0.5,
                    "rank": sol.get("rank", 99)
                })

        # Add solutions from similar incidents
        for inc in similar:
            if inc.get("resolution_action"):
                solutions.append({
                    "source": "historical_incident",
                    "source_name": f"Incident on {inc['service_name']} ({inc['first_seen_at'].strftime('%Y-%m-%d') if inc['first_seen_at'] else 'unknown'})",
                    "action_type": inc["resolution_action"],
                    "action_details": {"from_incident": str(inc["id"])},
                    "expected_resolution_time_minutes": inc["resolution_time_seconds"] // 60 if inc["resolution_time_seconds"] else None,
                    "success_rate": 100.0,
                    "confidence": float(inc["confidence_score"]) if inc["confidence_score"] else 0.7,
                    "rank": 1
                })

        # Sort by confidence then rank
        solutions.sort(key=lambda s: (-s["confidence"], s.get("rank", 99)))

        result = {
            "status": "solutions_found" if solutions else "no_solutions_found",
            "incident_id": incident_id,
            "service_name": incident["service_name"],
            "total_solutions": len(solutions),
            "playbooks_matched": len(playbooks),
            "historical_matches": len(similar),
            "recommended_solutions": solutions[:10],
            "top_recommendation": solutions[0] if solutions else None,
            "message": f"Found {len(solutions)} potential solutions from {len(playbooks)} playbooks and {len(similar)} past incidents"
        }

        update_audit_log(audit["id"], status="success", result=result)
        return json.dumps(result, default=str)

    except Exception as e:
        error_msg = f"Solution recommendation failed: {str(e)}"
        logger.error(error_msg)
        update_audit_log(audit["id"], status="failed", error_message=error_msg)
        return json.dumps({"status": "error", "message": error_msg})


if __name__ == "__main__":
    logger.info(f"Starting Historian Agent on {HOST}:{PORT}")
    mcp.run(transport="sse", host=HOST, port=PORT)
