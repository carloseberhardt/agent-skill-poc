"""
Security domain agent — A2A-compliant via a2a-sdk, backed by SQLite + own LLM.

This is a real agentic loop: the LLM has domain-specific tools and decides
what to query based on the question. It may call multiple tools, reason
about the results, and call more tools before producing a final answer.

Run: uv run python mock-agents/security_agent.py
Serves on port 5002.
"""

import json
import warnings
from datetime import datetime, timezone

import uvicorn

# langgraph v1.0 moved create_react_agent to langchain.agents, but we don't
# depend on the full langchain package — suppress until we upgrade.
warnings.filterwarnings("ignore", message="create_react_agent has been moved")
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCard, AgentCapabilities, AgentSkill
from a2a.utils import new_agent_text_message
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from agent_common import get_db, get_llm, get_model_name, setup_wire_logger

_PORT = 5002
_MODEL_ENV = "SECURITY_AGENT_MODEL"

wire = setup_wire_logger("security", "35")
llm = get_llm(_MODEL_ENV)
model_name = get_model_name(_MODEL_ENV)


# ── Domain tools ──────────────────────────────────────────────
# The LLM decides which of these to call and in what order.

@tool
def get_security_events(severity: str = "", user_id: str = "", limit: int = 20) -> str:
    """Query security events from monitoring systems.
    Filter by severity (info/warning/critical) and/or user_id. Returns most recent first."""
    conn = get_db()
    try:
        query = "SELECT * FROM security_events WHERE 1=1"
        params = []
        if severity:
            query += " AND severity = ?"
            params.append(severity)
        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        events = conn.execute(query, params).fetchall()
        if not events:
            return "No security events found matching the criteria."

        lines = []
        for ev in events:
            lines.append(
                f"[{ev['severity'].upper()}] {ev['event_type']} | "
                f"User: {ev['user_id'] or 'system'} | IP: {ev['source_ip'] or 'N/A'} | "
                f"Resource: {ev['resource'] or 'N/A'} | Time: {ev['timestamp']}\n"
                f"  Details: {ev['details']}"
            )
        return "\n".join(lines)
    finally:
        conn.close()


@tool
def get_employee_info(user_id: str) -> str:
    """Look up an employee by user ID. Returns role, department, clearance, manager, and notes."""
    conn = get_db()
    try:
        emp = conn.execute("SELECT * FROM employees WHERE id = ?", (user_id,)).fetchone()
        if not emp:
            return f"No employee found with ID '{user_id}'."
        return (
            f"Name: {emp['name']} | Role: {emp['role']} | Department: {emp['department']} | "
            f"Team: {emp['team']} | Clearance: {emp['clearance']} | "
            f"Manager: {emp['manager_id'] or 'none'} | On-call: {emp['on_call_role'] or 'none'}\n"
            f"Notes: {emp['notes']}"
        )
    finally:
        conn.close()


@tool
def log_security_action(user_id: str, action: str, severity: str = "info") -> str:
    """Log a security action (access restriction, account suspension, etc.) as a
    security event. This creates an audit trail — it does not modify data access."""
    conn = get_db()
    try:
        now = datetime.now(timezone.utc)
        conn.execute(
            "INSERT INTO security_events (timestamp, event_type, severity, user_id, "
            "source_ip, resource, details) VALUES (?,?,?,?,?,?,?)",
            (now.isoformat(), "security_action", severity, user_id, None, None,
             f"Action taken for user {user_id}: {action}")
        )
        conn.commit()
        return (
            f"Security event logged for {user_id}: {action}. "
            f"Timestamp: {now.isoformat()}."
        )
    finally:
        conn.close()


@tool
def rotate_credentials(service: str = "auth-service") -> str:
    """Initiate emergency credential rotation for a service."""
    conn = get_db()
    try:
        now = datetime.now(timezone.utc)
        conn.execute(
            "INSERT INTO security_events (timestamp, event_type, severity, user_id, "
            "source_ip, resource, details) VALUES (?,?,?,?,?,?,?)",
            (now.isoformat(), "credential_rotation", "info", None, None, service,
             f"Emergency credential rotation initiated for {service}.")
        )
        conn.commit()
        return f"Credential rotation initiated for {service} at {now.isoformat()}."
    finally:
        conn.close()


# ── Agent setup ───────────────────────────────────────────────

_tools = [get_security_events, get_employee_info,
          log_security_action, rotate_credentials]

_system_prompt = (
    f"You are a security analysis agent running on model: {model_name}. "
    "You have tools to query security events, look up employees, "
    "and take remediation actions.\n\n"
    "Use your tools to investigate before answering. Don't guess — query the data.\n\n"
    "When analyzing:\n"
    "- Check security events for any critical or warning items\n"
    "- If you find suspicious users or IPs, look up employee info for context\n"
    "- Focus on authentication anomalies, network probes, and access violations\n"
    "- If there are no critical or warning events, report that the security posture is clean\n\n"
    "Respond with a JSON object containing:\n"
    '- "summary": 1-2 sentence overview\n'
    '- "findings": array of objects with "severity", "description", "recommendation"\n'
    '- "risk_level": "low", "medium", "high", or "critical"\n'
    f'- "model": "{model_name}"\n'
    "Keep it concise and actionable."
)

_agent = create_react_agent(llm, tools=_tools)


# ── A2A executor ──────────────────────────────────────────────

class SecurityAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user_input = context.get_user_input() or ""
        wire.info("◀ received: %s", user_input[:150])

        messages = [
            {"role": "system", "content": _system_prompt},
            {"role": "user", "content": user_input},
        ]

        wire.info("▶ starting react loop (%s)", model_name)
        result = await _agent.ainvoke({"messages": messages})

        # Log the tool calls that happened during the loop
        for msg in result["messages"]:
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    wire.info("  ⤷ tool_call: %s(%s)", tc["name"], str(tc.get("args", {}))[:80])

        response = result["messages"][-1].content
        wire.info("◀ react loop complete (%d chars)", len(response))

        await event_queue.enqueue_event(new_agent_text_message(response))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        pass


def build_app():
    agent_card = AgentCard(
        name="security_agent",
        description=(
            f"Security domain agent (model: {model_name}). Investigates security events "
            "such as authentication anomalies, network probes, and suspicious logins. "
            "Can log security actions and rotate credentials. Does NOT manage data access "
            "or clearance levels — that is the data agent's responsibility."
        ),
        url=f"http://localhost:{_PORT}",
        version="0.3.0",
        defaultInputModes=["text/plain"],
        defaultOutputModes=["text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[
            AgentSkill(
                id="query_security",
                name="Query Security",
                description=(
                    "Investigate security events: authentication anomalies, failed logins, "
                    "unfamiliar IPs, port scans, and credential issues. Can log security "
                    "actions and rotate credentials. Does NOT handle data access or clearance."
                ),
                tags=["security", "authentication", "alerts", "threat-analysis"],
                examples=[
                    "Are there any security alerts?",
                    "Check for suspicious login attempts",
                    "What's the current risk level?",
                ],
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
