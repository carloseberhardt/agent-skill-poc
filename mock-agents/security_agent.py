"""
Security domain agent — A2A-compliant via a2a-sdk, backed by SQLite + own LLM.

Analyzes security events, assesses threat severity, recommends responses.
Can execute actions (restrict access, log audit entries) that change the DB.
Uses its own LLM model (SECURITY_AGENT_MODEL) to reason about findings.

Run: uv run python mock-agents/security_agent.py
Serves on port 5002.
"""

import json
from datetime import datetime, timezone

import uvicorn
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCard, AgentCapabilities, AgentSkill
from a2a.utils import new_agent_text_message

from agent_common import get_db, get_llm, get_model_name, setup_wire_logger

_PORT = 5002
_MODEL_ENV = "SECURITY_AGENT_MODEL"

wire = setup_wire_logger("security", "35")
llm = get_llm(_MODEL_ENV)
model_name = get_model_name(_MODEL_ENV)


def _query_security_data() -> str:
    """Pull security events and context from SQLite, return as text for the LLM."""
    conn = get_db()
    try:
        events = conn.execute(
            "SELECT * FROM security_events ORDER BY severity DESC, timestamp DESC"
        ).fetchall()

        # Also get data access anomalies for context
        access_anomalies = conn.execute(
            "SELECT dal.*, e.name as user_name, e.role, e.department "
            "FROM data_access_logs dal "
            "JOIN employees e ON dal.user_id = e.id "
            "WHERE dal.row_count > 10000 "
            "ORDER BY dal.timestamp DESC LIMIT 10"
        ).fetchall()

        lines = ["## Security Events\n"]
        for ev in events:
            lines.append(
                f"- [{ev['severity'].upper()}] {ev['event_type']} | "
                f"User: {ev['user_id'] or 'system'} | IP: {ev['source_ip'] or 'N/A'} | "
                f"Resource: {ev['resource'] or 'N/A'} | Time: {ev['timestamp']}\n"
                f"  Details: {ev['details']}"
            )

        if access_anomalies:
            lines.append("\n## High-Volume Data Access (potential anomalies)\n")
            for a in access_anomalies:
                lines.append(
                    f"- {a['user_name']} ({a['role']}, {a['department']}) accessed "
                    f"{a['dataset']} at {a['timestamp']} — {a['row_count']:,} rows "
                    f"from IP {a['source_ip']}"
                )

        return "\n".join(lines)
    finally:
        conn.close()


def _execute_action(raw_input: str) -> dict:
    """Execute a security action and record the consequences in the DB."""
    now = datetime.now(timezone.utc)
    conn = get_db()
    lower = raw_input.lower()

    try:
        # Determine if this is an approval or rejection
        is_approved = any(kw in lower for kw in ["confirm", "approv", "approved"])
        is_rejected = any(kw in lower for kw in ["reject", "denied", "deny"])

        if is_rejected:
            # Log the rejection but don't change state
            conn.execute(
                "INSERT INTO security_events (timestamp, event_type, severity, user_id, "
                "source_ip, resource, details) VALUES (?,?,?,?,?,?,?)",
                (now.isoformat(), "action_rejected", "info", None, None, None,
                 f"Action rejected by operator: {raw_input[:500]}")
            )
            conn.commit()
            return {
                "action": "rejected",
                "summary": "Action rejected and logged. No changes made to access controls.",
                "audit_timestamp": now.isoformat(),
            }

        # Approved — figure out what to do based on the message content
        actions_taken = []

        # Restrict user access — look for user IDs in the message
        user_ids = []
        for uid in ["jliu", "schen", "mwebb", "psharma", "dtorres", "akim"]:
            if uid in lower:
                user_ids.append(uid)
        # Also check for names
        name_map = {"james liu": "jliu", "sarah chen": "schen", "marcus webb": "mwebb",
                     "priya sharma": "psharma", "dana torres": "dtorres", "alex kim": "akim"}
        for name, uid in name_map.items():
            if name in lower and uid not in user_ids:
                user_ids.append(uid)

        if "restrict" in lower or "revoke" in lower or "suspend" in lower or user_ids:
            for uid in (user_ids or ["unknown"]):
                # Log the access restriction
                conn.execute(
                    "INSERT INTO security_events (timestamp, event_type, severity, user_id, "
                    "source_ip, resource, details) VALUES (?,?,?,?,?,?,?)",
                    (now.isoformat(), "access_restricted", "info", uid, None, None,
                     f"Access restricted for user {uid} by operator action. "
                     f"Pending investigation. Original request: {raw_input[:300]}")
                )
                # Remove the anomalous access logs (simulates cutting off access)
                deleted = conn.execute(
                    "DELETE FROM data_access_logs WHERE user_id = ? AND row_count > 10000",
                    (uid,)
                ).rowcount
                actions_taken.append(
                    f"Restricted access for {uid}. "
                    f"Removed {deleted} anomalous access log entries."
                )

        # Rotate credentials
        if "rotate" in lower or "credential" in lower:
            conn.execute(
                "INSERT INTO security_events (timestamp, event_type, severity, user_id, "
                "source_ip, resource, details) VALUES (?,?,?,?,?,?,?)",
                (now.isoformat(), "credential_rotation", "info", None, None, "auth-service",
                 "Emergency credential rotation initiated by operator action.")
            )
            actions_taken.append("Emergency credential rotation initiated.")

        # Generic approval if no specific action was matched
        if not actions_taken:
            conn.execute(
                "INSERT INTO security_events (timestamp, event_type, severity, user_id, "
                "source_ip, resource, details) VALUES (?,?,?,?,?,?,?)",
                (now.isoformat(), "action_approved", "info", None, None, None,
                 f"Action approved by operator: {raw_input[:500]}")
            )
            actions_taken.append("Action approved and logged.")

        conn.commit()

        return {
            "action": "executed",
            "actions_taken": actions_taken,
            "summary": " ".join(actions_taken),
            "audit_timestamp": now.isoformat(),
        }
    finally:
        conn.close()


class SecurityAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user_input = context.get_user_input() or ""
        wire.info("◀ received: %s", user_input[:150])

        # Route action/approval messages to the action handler
        lower = user_input.lower()
        if any(kw in lower for kw in ["confirm", "reject", "approv", "decision",
                                       "log action", "restrict", "revoke", "suspend"]):
            result = _execute_action(user_input)
            wire.info("▶ action → %s", result.get("summary", "?")[:100])
            await event_queue.enqueue_event(new_agent_text_message(json.dumps(result)))
            return

        # Query security data and let the LLM reason about it
        security_context = _query_security_data()

        system_prompt = (
            f"You are a security analysis agent (model: {model_name}). "
            "You analyze security events and data access patterns to identify threats. "
            "You have access to the following security data from your monitoring systems.\n\n"
            "When analyzing:\n"
            "- Assess the severity and potential impact of each finding\n"
            "- Look for correlations between events (same user, same timeframe, same resource)\n"
            "- Identify patterns that suggest insider threat, data exfiltration, or unauthorized access\n"
            "- Recommend specific actions when warranted\n\n"
            "Respond with your analysis as a JSON object with these keys:\n"
            '- "summary": 1-2 sentence overview\n'
            '- "findings": array of objects, each with "severity", "description", "recommendation"\n'
            '- "risk_level": overall risk level ("low", "medium", "high", "critical")\n'
            '- "model": the model name you are running on\n'
            "Keep it concise and actionable."
        )

        user_prompt = f"Analyze the current security posture based on this data:\n\n{security_context}"
        if user_input:
            user_prompt += f"\n\nSpecific question from the user: {user_input}"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        wire.info("▶ calling LLM (%s)", model_name)
        response = await llm.ainvoke(messages)
        result_text = response.content
        wire.info("◀ LLM response (%d chars)", len(result_text))

        await event_queue.enqueue_event(new_agent_text_message(result_text))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        pass


def build_app():
    agent_card = AgentCard(
        name="security_agent",
        description=(
            f"Security domain agent (model: {model_name}). Analyzes security events, "
            "data access anomalies, and compliance status. Can execute security actions "
            "(restrict access, rotate credentials) that change system state."
        ),
        url=f"http://localhost:{_PORT}",
        version="0.2.0",
        defaultInputModes=["text/plain"],
        defaultOutputModes=["text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[
            AgentSkill(
                id="query_security",
                name="Query Security",
                description="Analyze security events, access anomalies, and compliance status. Returns risk assessment and recommendations.",
                tags=["security", "compliance", "alerts", "threat-analysis"],
                examples=["Are there any security alerts?", "Analyze recent access anomalies", "What's the current risk level?"],
            ),
            AgentSkill(
                id="execute_security_action",
                name="Execute Security Action",
                description="Execute a security action (restrict access, rotate credentials, log decision). Changes system state and returns confirmation.",
                tags=["security", "action", "restrict", "remediation"],
                examples=["Restrict jliu access pending investigation", "Approve: rotate service credentials"],
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
