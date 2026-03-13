"""
Mock security domain agent — A2A-compliant via a2a-sdk.

In production, this is a real A2A agent operated by a product team.
The coordinator runtime doesn't care what's inside it.

Two skills:
  1. Query security events and compliance status
  2. Log approval decisions back to the security domain

Run: uv run python mock-agents/security_agent.py
Serves on port 5002.
"""

import json
import logging
import os
import random
from datetime import datetime, timezone

import uvicorn
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCard, AgentCapabilities, AgentSkill
from a2a.utils import new_agent_text_message

_PORT = 5002

wire = logging.getLogger("wire")
if os.getenv("WIRE_LOG") == "true":
    logging.basicConfig(level=logging.INFO)
    wire.setLevel(logging.INFO)
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("\033[35m%(asctime)s [wire:security] %(message)s\033[0m", datefmt="%H:%M:%S"))
    wire.addHandler(_h)
    wire.propagate = False
else:
    wire.setLevel(logging.WARNING)

# Deliberately "action-worthy" responses so cross-agent synthesis
# naturally recommends actions, triggering the approval UI.
_SECURITY_EVENTS_HIGH = [
    {"type": "alert", "summary": "Unusual data access pattern: user jdoe accessed 3 sensitive tables outside business hours", "severity": "warning"},
    {"type": "compliance", "summary": "GDPR audit scheduled for next week — 2 datasets missing classification labels", "severity": "action_needed"},
    {"type": "alert", "summary": "Failed login attempts spike for service account etl-prod (23 failures in 1 hour)", "severity": "warning"},
    {"type": "compliance", "summary": "3 S3 buckets with PII data lack encryption-at-rest configuration", "severity": "action_needed"},
    {"type": "alert", "summary": "Data exfiltration risk: bulk download of customer records by user msmith flagged", "severity": "critical"},
    {"type": "compliance", "summary": "Quarterly access review overdue for 4 production databases", "severity": "action_needed"},
    {"type": "alert", "summary": "Privilege escalation detected: user agarcia granted themselves admin on staging cluster", "severity": "critical"},
    {"type": "compliance", "summary": "SOC 2 control gap: 5 service accounts missing MFA enrollment", "severity": "action_needed"},
    {"type": "alert", "summary": "Anomalous API call volume from user tpatel — 12x normal rate over 30 minutes", "severity": "warning"},
    {"type": "alert", "summary": "Unrecognized IP address accessing production database via user kwong credentials", "severity": "critical"},
    {"type": "compliance", "summary": "Data retention policy violation: 2 datasets past 90-day deletion window", "severity": "action_needed"},
]

# Low-severity events for the "all clear" scenario (~30% of queries)
_SECURITY_EVENTS_LOW = [
    {"type": "info", "summary": "All service account credentials rotated successfully on schedule", "severity": "info"},
    {"type": "info", "summary": "Weekly vulnerability scan completed — no new findings", "severity": "info"},
    {"type": "info", "summary": "Firewall rule audit passed — all rules match approved baseline", "severity": "info"},
    {"type": "info", "summary": "Encryption-at-rest verification completed for all production buckets", "severity": "info"},
]

# In-memory audit trail
_audit_log: list[dict] = []
_next_id = 1


def _do_query(q: str) -> dict:
    # ~30% of the time, return an all-clear with only info-level events
    if random.random() < 0.3:
        results = random.sample(_SECURITY_EVENTS_LOW, k=min(3, len(_SECURITY_EVENTS_LOW)))
    else:
        results = random.sample(_SECURITY_EVENTS_HIGH, k=min(3, len(_SECURITY_EVENTS_HIGH)))
    return {
        "agent": "security_agent",
        "domain": "security",
        "results": results,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _log_action(q: str) -> dict:
    """Parse the approval decision from the query and log it."""
    global _next_id

    entry = {
        "audit_id": f"SEC-{_next_id:04d}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "raw_input": q,
        "status": "recorded",
    }
    _audit_log.append(entry)
    _next_id += 1

    return {
        "agent": "security_agent",
        "domain": "security_audit",
        "action": "approval_logged",
        "entry": entry,
        "audit_trail_size": len(_audit_log),
    }


class SecurityAgentExecutor(AgentExecutor):
    """Handles A2A message/send requests for security queries and audit logging."""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user_input = context.get_user_input() or ""
        query = user_input.lower()
        wire.info("◀ received: %s", user_input[:150])

        # Route to audit logging if the message is about an approval decision
        if any(kw in query for kw in ["confirm", "reject", "approv", "decision", "log action", "audit"]):
            result = _log_action(user_input)
            wire.info("▶ audit_log → %s", result.get("entry", {}).get("audit_id", "?"))
        else:
            result = _do_query(user_input)
            severities = [r["severity"] for r in result.get("results", [])]
            wire.info("▶ query → %d events [%s]", len(result.get("results", [])), ", ".join(severities))

        await event_queue.enqueue_event(new_agent_text_message(json.dumps(result)))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        pass


def build_app():
    agent_card = AgentCard(
        name="security_agent",
        description="Security domain agent — monitors alerts, compliance status, and access anomalies. Also logs approval decisions for security actions.",
        url=f"http://localhost:{_PORT}",
        version="0.1.0",
        defaultInputModes=["text/plain"],
        defaultOutputModes=["text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[
            AgentSkill(
                id="query_security",
                name="Query Security",
                description="Query security events, compliance status, and access anomalies.",
                tags=["security", "compliance", "alerts"],
                examples=["Are there any security alerts?", "What's the compliance status?"],
            ),
            AgentSkill(
                id="log_security_action",
                name="Log Security Action",
                description="Log an approval decision (confirmed or rejected) for a security-recommended action. Returns an audit trail entry.",
                tags=["security", "audit", "approval"],
                examples=["Log action confirmed: investigate jdoe access", "Log action rejected: rotate etl-prod credentials"],
            ),
        ],
    )

    executor = SecurityAgentExecutor()
    request_handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
    )

    server = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=request_handler,
    )
    return server.build()


app = build_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=_PORT)
