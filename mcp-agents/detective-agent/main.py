"""
Nexus-Zero Detective Agent
===========================
Performs root cause analysis by correlating GCP logs with GitHub commits.
This is the second agent in the investigation chain.

MCP Tools:
  - analyze_logs: Deep analysis of error logs for a specific incident
  - correlate_with_commits: Find GitHub commits that may have caused the incident
  - investigate_root_cause: Full investigation combining logs + commits + Gemini reasoning
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone, timedelta

# In Docker: main.py at /app/main.py, common at /app/common/
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP
from google.cloud import logging as gcp_logging
from github import Github, GithubException
import google.generativeai as genai

from common.config import GCP_PROJECT_ID, GEMINI_MODEL, PORT, HOST
from common.credential_store import get_credential, set_credential, get_all_credentials
from common.db_utils import (
    get_incident, update_incident, create_audit_log,
    update_audit_log, get_recent_deployments
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("detective-agent")

mcp = FastMCP(
    "Nexus-Zero Detective Agent"
)


def _get_gemini_model():
    """Get Gemini model, configuring API key from runtime store."""
    api_key = get_credential("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set. Call set_credentials first.")
    genai.configure(api_key=api_key)
    return genai.GenerativeModel(GEMINI_MODEL)


def _get_github_client():
    """Create GitHub client using runtime credentials."""
    token = get_credential("GITHUB_TOKEN")
    if not token:
        raise ValueError("GITHUB_TOKEN not set. Call set_credentials first.")
    return Github(token)


def _query_gcp_logs(service_name, time_window_minutes=30, severity="ERROR"):
    """Query GCP logs for a specific service."""
    client = gcp_logging.Client(project=GCP_PROJECT_ID)

    now = datetime.now(timezone.utc)
    start_time = now - timedelta(minutes=time_window_minutes)

    log_filter = (
        f'severity >= {severity} '
        f'AND timestamp >= "{start_time.isoformat()}" '
        f'AND timestamp <= "{now.isoformat()}" '
        f'AND resource.type = "cloud_run_revision" '
        f'AND resource.labels.service_name = "{service_name}"'
    )

    entries = list(client.list_entries(
        filter_=log_filter,
        order_by=gcp_logging.DESCENDING,
        max_results=100,
        project_ids=[GCP_PROJECT_ID]
    ))

    parsed = []
    for entry in entries:
        parsed.append({
            "timestamp": entry.timestamp.isoformat() if entry.timestamp else None,
            "severity": entry.severity,
            "payload": str(entry.payload)[:1000] if entry.payload else None,
            "trace": entry.trace,
            "labels": dict(entry.labels) if entry.labels else {}
        })

    return parsed


def _get_recent_commits(repo_name=None, since_minutes=30):
    """Get recent commits from a GitHub repository."""
    if not repo_name:
        repo_name = get_credential("GITHUB_REPO")
    if not repo_name:
        raise ValueError("GITHUB_REPO not set. Call set_credentials first.")
    gh = _get_github_client()
    repo = gh.get_repo(repo_name)

    since = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
    commits = list(repo.get_commits(since=since))

    results = []
    for commit in commits[:20]:
        files_changed = []
        for f in commit.files or []:
            files_changed.append({
                "filename": f.filename,
                "status": f.status,
                "additions": f.additions,
                "deletions": f.deletions,
                "patch": f.patch[:500] if f.patch else None
            })

        results.append({
            "sha": commit.sha[:12],
            "message": commit.commit.message,
            "author": commit.commit.author.name if commit.commit.author else "unknown",
            "date": commit.commit.author.date.isoformat() if commit.commit.author else None,
            "files_changed": files_changed,
            "total_changes": commit.stats.total if commit.stats else 0
        })

    return results


@mcp.tool()
def analyze_logs(
    incident_id: str,
    time_window_minutes: int = 30
) -> str:
    """
    Perform deep analysis of GCP error logs for a specific incident.

    Retrieves detailed log entries from GCP Cloud Logging for the affected
    service and extracts error patterns, stack traces, and timing information.

    Args:
        incident_id: UUID of the incident to analyze
        time_window_minutes: How far back to search for related logs

    Returns:
        JSON report with detailed log analysis
    """
    logger.info(f"Analyzing logs for incident: {incident_id}")

    audit = create_audit_log(
        agent_name="detective",
        action_type="analyze_logs",
        action_details={"incident_id": incident_id, "time_window": time_window_minutes},
        incident_id=incident_id
    )

    try:
        incident = get_incident(incident_id)
        if not incident:
            return json.dumps({"status": "error", "message": f"Incident {incident_id} not found"})

        service_name = incident["service_name"]
        logs = _query_gcp_logs(service_name, time_window_minutes)

        # Extract unique error patterns
        error_patterns = {}
        for log in logs:
            payload = log.get("payload", "")
            pattern_key = payload[:100] if payload else "unknown"
            if pattern_key not in error_patterns:
                error_patterns[pattern_key] = {
                    "count": 0,
                    "first_seen": log["timestamp"],
                    "last_seen": log["timestamp"],
                    "sample_payload": payload[:500]
                }
            error_patterns[pattern_key]["count"] += 1
            error_patterns[pattern_key]["last_seen"] = log["timestamp"]

        result = {
            "status": "analysis_complete",
            "incident_id": incident_id,
            "service_name": service_name,
            "total_log_entries": len(logs),
            "unique_error_patterns": len(error_patterns),
            "time_window_minutes": time_window_minutes,
            "error_patterns": list(error_patterns.values())[:10],
            "raw_logs_sample": logs[:5]
        }

        update_audit_log(audit["id"], status="success", result=result)
        return json.dumps(result, default=str)

    except Exception as e:
        error_msg = f"Log analysis failed: {str(e)}"
        logger.error(error_msg)
        update_audit_log(audit["id"], status="failed", error_message=error_msg)
        return json.dumps({"status": "error", "message": error_msg})


@mcp.tool()
def correlate_with_commits(
    incident_id: str,
    time_window_minutes: int = 60
) -> str:
    """
    Find GitHub commits that may have caused the incident.

    Searches for commits made within the specified time window before the
    incident occurred, looking for code changes that could explain the failure.

    Args:
        incident_id: UUID of the incident to investigate
        time_window_minutes: How far back to search for commits

    Returns:
        JSON report of suspect commits with file changes and diffs
    """
    logger.info(f"Correlating commits for incident: {incident_id}")

    audit = create_audit_log(
        agent_name="detective",
        action_type="correlate_commits",
        action_details={"incident_id": incident_id, "time_window": time_window_minutes},
        incident_id=incident_id
    )

    try:
        incident = get_incident(incident_id)
        if not incident:
            return json.dumps({"status": "error", "message": f"Incident {incident_id} not found"})

        github_repo = get_credential("GITHUB_REPO")
        if not github_repo:
            return json.dumps({
                "status": "error",
                "message": "GITHUB_REPO not configured. Call set_credentials first."
            })

        commits = _get_recent_commits(github_repo, time_window_minutes)

        if not commits:
            result = {
                "status": "no_commits_found",
                "incident_id": incident_id,
                "message": f"No commits found in the last {time_window_minutes} minutes"
            }
            update_audit_log(audit["id"], status="success", result=result)
            return json.dumps(result, default=str)

        # Rank commits by suspicion level
        ranked_commits = []
        error_keywords = ["fix", "bug", "error", "crash", "database", "connection",
                          "timeout", "memory", "pool", "config", "deploy"]

        for commit in commits:
            suspicion_score = 0
            reasons = []

            # Check commit message for error-related keywords
            msg_lower = commit["message"].lower()
            for keyword in error_keywords:
                if keyword in msg_lower:
                    suspicion_score += 10
                    reasons.append(f"Commit message contains '{keyword}'")

            # Check if config files were changed
            for f in commit["files_changed"]:
                fname = f["filename"].lower()
                if any(kw in fname for kw in ["config", "env", "database", "db", "pool"]):
                    suspicion_score += 20
                    reasons.append(f"Changed config file: {f['filename']}")
                if f["deletions"] > f["additions"]:
                    suspicion_score += 5
                    reasons.append(f"More deletions than additions in {f['filename']}")

            # More total changes = higher risk
            if commit["total_changes"] > 100:
                suspicion_score += 15
                reasons.append(f"Large changeset ({commit['total_changes']} changes)")

            commit["suspicion_score"] = suspicion_score
            commit["suspicion_reasons"] = reasons
            ranked_commits.append(commit)

        ranked_commits.sort(key=lambda c: c["suspicion_score"], reverse=True)

        # Update incident with top suspect
        if ranked_commits and ranked_commits[0]["suspicion_score"] > 0:
            top_suspect = ranked_commits[0]
            update_incident(
                incident_id,
                suspect_commit_id=top_suspect["sha"],
                suspect_file_path=top_suspect["files_changed"][0]["filename"] if top_suspect["files_changed"] else None
            )

        result = {
            "status": "correlation_complete",
            "incident_id": incident_id,
            "total_commits_analyzed": len(commits),
            "suspect_commits": ranked_commits[:5],
            "top_suspect": ranked_commits[0] if ranked_commits else None,
            "time_window_minutes": time_window_minutes
        }

        update_audit_log(audit["id"], status="success", result=result)
        return json.dumps(result, default=str)

    except Exception as e:
        error_msg = f"Commit correlation failed: {str(e)}"
        logger.error(error_msg)
        update_audit_log(audit["id"], status="failed", error_message=error_msg)
        return json.dumps({"status": "error", "message": error_msg})


@mcp.tool()
def investigate_root_cause(
    incident_id: str,
    time_window_minutes: int = 30
) -> str:
    """
    Full root cause investigation combining logs, commits, and AI reasoning.

    This is the primary investigation tool that:
    1. Analyzes GCP error logs for the affected service
    2. Correlates with recent GitHub commits
    3. Uses Gemini AI to reason about the likely root cause
    4. Updates the incident record with findings

    Args:
        incident_id: UUID of the incident to investigate
        time_window_minutes: How far back to search for evidence

    Returns:
        JSON report with root cause analysis and confidence score
    """
    logger.info(f"Full investigation for incident: {incident_id}")

    audit = create_audit_log(
        agent_name="detective",
        action_type="investigate_root_cause",
        action_details={"incident_id": incident_id},
        incident_id=incident_id
    )

    try:
        incident = get_incident(incident_id)
        if not incident:
            return json.dumps({"status": "error", "message": f"Incident {incident_id} not found"})

        service_name = incident["service_name"]

        # Step 1: Gather evidence from logs
        logs = _query_gcp_logs(service_name, time_window_minutes)
        log_summary = [
            {"timestamp": l["timestamp"], "payload": l["payload"][:300]}
            for l in logs[:10]
        ]

        # Step 2: Gather evidence from commits
        commits = []
        github_repo = get_credential("GITHUB_REPO")
        if github_repo:
            try:
                commits = _get_recent_commits(github_repo, time_window_minutes * 2)
            except Exception as e:
                logger.warning(f"GitHub query failed: {e}")

        commit_summary = [
            {
                "sha": c["sha"],
                "message": c["message"],
                "author": c["author"],
                "files": [f["filename"] for f in c["files_changed"]],
                "changes": c["total_changes"]
            }
            for c in commits[:10]
        ]

        # Step 3: Use Gemini to reason about root cause
        root_cause = "Unable to determine root cause (Gemini not configured)"
        confidence_score = 0.5
        suspect_commit = None
        suspect_file = None

        gemini_key = get_credential("GEMINI_API_KEY")
        if gemini_key:
            model = _get_gemini_model()
            prompt = f"""You are an expert SRE investigating a production incident.

INCIDENT DETAILS:
- Service: {service_name}
- Error: {incident['error_message'][:500]}
- Stack Trace: {incident['stack_trace'][:500] if incident.get('stack_trace') else 'N/A'}
- Severity: {incident['severity']}

RECENT ERROR LOGS ({len(logs)} entries):
{json.dumps(log_summary, default=str)}

RECENT COMMITS ({len(commits)} commits):
{json.dumps(commit_summary, default=str)}

Based on this evidence, provide your analysis in the following JSON format:
{{
    "root_cause": "A clear explanation of what caused this incident",
    "confidence_score": 0.85,
    "suspect_commit": "sha_if_applicable_or_null",
    "suspect_file": "file_path_if_applicable_or_null",
    "reasoning": "Step by step reasoning",
    "recommended_action": "What should be done to fix this"
}}

Only respond with valid JSON, nothing else."""

            response = model.generate_content(prompt)
            response_text = response.text.strip()
            if response_text.startswith("```"):
                response_text = response_text.split("\n", 1)[1].rsplit("```", 1)[0]

            analysis = json.loads(response_text)
            root_cause = analysis.get("root_cause", root_cause)
            confidence_score = float(analysis.get("confidence_score", 0.5))
            suspect_commit = analysis.get("suspect_commit")
            suspect_file = analysis.get("suspect_file")

        # Step 4: Update the incident record
        update_fields = {
            "root_cause": root_cause,
            "confidence_score": confidence_score,
            "status": "investigating"
        }
        if suspect_commit:
            update_fields["suspect_commit_id"] = suspect_commit
        if suspect_file:
            update_fields["suspect_file_path"] = suspect_file

        update_incident(incident_id, **update_fields)

        result = {
            "status": "investigation_complete",
            "incident_id": incident_id,
            "service_name": service_name,
            "root_cause": root_cause,
            "confidence_score": confidence_score,
            "suspect_commit": suspect_commit,
            "suspect_file": suspect_file,
            "evidence": {
                "log_entries_analyzed": len(logs),
                "commits_analyzed": len(commits),
                "log_sample": log_summary[:3],
                "commit_sample": commit_summary[:3]
            },
            "recommended_action": analysis.get("recommended_action", "Further investigation needed") if gemini_key else "Configure Gemini for AI-powered analysis"
        }

        update_audit_log(audit["id"], status="success", result=result)
        logger.info(f"Investigation complete. Root cause: {root_cause[:100]}")
        return json.dumps(result, default=str)

    except Exception as e:
        error_msg = f"Root cause investigation failed: {str(e)}"
        logger.error(error_msg)
        update_audit_log(audit["id"], status="failed", error_message=error_msg)
        return json.dumps({"status": "error", "message": error_msg})


@mcp.tool()
def set_credentials(
    gemini_api_key: str = "",
    github_token: str = "",
    github_repo: str = "",
) -> str:
    """
    Inject runtime credentials for this agent.

    Call this BEFORE using investigate_root_cause or correlate_with_commits.
    Credentials are stored in memory only — they vanish when the container
    scales to zero.

    Args:
        gemini_api_key: Google Gemini API key for AI reasoning
        github_token:   GitHub personal access token for commit correlation
        github_repo:    GitHub repo in 'owner/repo' format (e.g. 'acme/my-app')

    Returns:
        JSON confirmation of which credentials were set
    """
    set_creds = []
    if gemini_api_key:
        set_credential("GEMINI_API_KEY", gemini_api_key)
        set_creds.append("GEMINI_API_KEY")
    if github_token:
        set_credential("GITHUB_TOKEN", github_token)
        set_creds.append("GITHUB_TOKEN")
    if github_repo:
        set_credential("GITHUB_REPO", github_repo)
        set_creds.append("GITHUB_REPO")

    logger.info(f"Credentials set: {set_creds}")
    return json.dumps({
        "status": "credentials_updated",
        "keys_set": set_creds,
        "stored_keys": list(get_all_credentials().keys()),
        "message": "Credentials stored in memory. They will be cleared when the container scales to zero."
    })


if __name__ == "__main__":
    logger.info(f"Starting Detective Agent on {HOST}:{PORT}")
    mcp.run(transport="sse", host=HOST, port=PORT)
