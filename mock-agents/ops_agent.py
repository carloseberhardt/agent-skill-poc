"""
Ops/SRE domain agent — A2A-compliant via a2a-sdk, backed by SQLite + own LLM.

Monitors service health, reasons about incidents and cascading failures.
Can execute remediation actions (scale, restart) that change the DB state.
Uses its own LLM model (OPS_AGENT_MODEL).

Run: uv run python mock-agents/ops_agent.py
Serves on port 5006.
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

_PORT = 5006
_MODEL_ENV = "OPS_AGENT_MODEL"

wire = setup_wire_logger("ops", "36")
llm = get_llm(_MODEL_ENV)
model_name = get_model_name(_MODEL_ENV)


def _query_ops_context() -> str:
    """Pull service health and metrics from SQLite for the LLM."""
    conn = get_db()
    try:
        # Service inventory with current status
        services = conn.execute(
            "SELECT name, team, tier, status, description FROM services ORDER BY tier"
        ).fetchall()

        lines = ["## Service Inventory\n"]
        for svc in services:
            status_icon = {"healthy": "✓", "degraded": "⚠", "stressed": "⚠", "stale": "⚠", "down": "✗"}.get(svc["status"], "?")
            lines.append(
                f"- {status_icon} **{svc['name']}** [Tier {svc['tier']}] — Status: {svc['status'].upper()}\n"
                f"  Team: {svc['team']} | {svc['description']}"
            )

        # Recent metrics for each service (last 6 hours for detail)
        lines.append("\n## Recent Metrics (last 6 hours)\n")
        for svc in services:
            metrics = conn.execute(
                "SELECT * FROM service_metrics WHERE service = ? "
                "ORDER BY timestamp DESC LIMIT 6",
                (svc["name"],),
            ).fetchall()

            if not metrics:
                continue

            latest = metrics[0]
            lines.append(f"\n### {svc['name']}")

            # Show latest values
            parts = []
            if latest["latency_ms"] is not None:
                parts.append(f"Latency: {latest['latency_ms']:.0f}ms")
            if latest["error_rate"] is not None:
                parts.append(f"Error rate: {latest['error_rate']:.2%}")
            if latest["request_count"] is not None:
                parts.append(f"Requests: {latest['request_count']:,}")
            if latest["connection_count"] is not None:
                parts.append(f"Connections: {latest['connection_count']}")
            if latest["cpu_percent"] is not None:
                parts.append(f"CPU: {latest['cpu_percent']:.0f}%")
            lines.append(f"Current: {' | '.join(parts)}")

            if latest["notes"]:
                lines.append(f"⚠ {latest['notes']}")

            # Show trend (compare latest vs 6 hours ago)
            if len(metrics) >= 5:
                oldest = metrics[-1]
                if latest["latency_ms"] is not None and oldest["latency_ms"] is not None:
                    change = latest["latency_ms"] - oldest["latency_ms"]
                    if abs(change) > 10:
                        direction = "↑" if change > 0 else "↓"
                        lines.append(f"Latency trend: {direction} {abs(change):.0f}ms over 6h")
                if latest["connection_count"] is not None and oldest["connection_count"] is not None:
                    change = latest["connection_count"] - oldest["connection_count"]
                    if abs(change) > 20:
                        direction = "↑" if change > 0 else "↓"
                        lines.append(f"Connections trend: {direction} {abs(change)} over 6h")

        return "\n".join(lines)
    finally:
        conn.close()


def _execute_action(raw_input: str) -> dict:
    """Execute an ops remediation action and update the DB to reflect it."""
    now = datetime.now(timezone.utc)
    conn = get_db()
    lower = raw_input.lower()

    try:
        is_rejected = any(kw in lower for kw in ["reject", "denied", "deny"])
        if is_rejected:
            return {
                "action": "rejected",
                "summary": "Remediation action rejected by operator. No changes made.",
                "timestamp": now.isoformat(),
            }

        actions_taken = []

        # Identify which services are mentioned
        known_services = ["payments-api", "customer-db", "analytics-pipeline",
                         "auth-service", "reporting-dashboard"]
        mentioned_services = [s for s in known_services if s in lower]

        # If no specific service mentioned, target degraded/stressed services
        if not mentioned_services:
            rows = conn.execute(
                "SELECT name FROM services WHERE status != 'healthy'"
            ).fetchall()
            mentioned_services = [r["name"] for r in rows]

        for service in mentioned_services:
            # Update service status to healthy
            conn.execute(
                "UPDATE services SET status = 'healthy' WHERE name = ?",
                (service,)
            )

            # Insert recovery metrics — normal values
            recovery_metrics = {
                "payments-api": (82, 0.001, 15000, None, 42.0),
                "customer-db": (14, 0.0, None, 95, 38.0),
                "analytics-pipeline": (None, 0.0, None, None, None),
                "auth-service": (9, 0.0005, 8000, None, 22.0),
                "reporting-dashboard": (42, 0.0, 180, None, 12.0),
            }
            metrics = recovery_metrics.get(service, (50, 0.0, None, None, 30.0))
            conn.execute(
                "INSERT INTO service_metrics (service, timestamp, latency_ms, error_rate, "
                "request_count, connection_count, cpu_percent, notes) VALUES (?,?,?,?,?,?,?,?)",
                (service, now.isoformat(), *metrics,
                 f"RECOVERED — remediation applied by operator at {now.strftime('%H:%M')}")
            )

            actions_taken.append(f"{service}: status → healthy, recovery metrics logged")

        # If analytics-pipeline was fixed, also update the dataset freshness
        if "analytics-pipeline" in mentioned_services:
            conn.execute(
                "UPDATE datasets SET pipeline_status = 'healthy', last_refresh = ? "
                "WHERE name = 'analytics-summary'",
                (now.isoformat(),)
            )
            actions_taken.append("analytics-summary dataset: pipeline_status → healthy")

        if not actions_taken:
            actions_taken.append("No specific remediation target identified. Action logged.")

        conn.commit()

        return {
            "action": "executed",
            "actions_taken": actions_taken,
            "summary": f"Remediation applied to {len(mentioned_services)} service(s). " +
                       " ".join(actions_taken),
            "timestamp": now.isoformat(),
        }
    finally:
        conn.close()


class OpsAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user_input = context.get_user_input() or ""
        wire.info("◀ received: %s", user_input[:150])

        # Route action/remediation messages
        lower = user_input.lower()
        if any(kw in lower for kw in ["confirm", "reject", "approv", "scale",
                                       "restart", "remediat", "recover", "fix"]):
            result = _execute_action(user_input)
            wire.info("▶ action → %s", result.get("summary", "?")[:100])
            await event_queue.enqueue_event(new_agent_text_message(json.dumps(result)))
            return

        ops_context = _query_ops_context()

        system_prompt = (
            f"You are an SRE/Ops analysis agent (model: {model_name}). "
            "You monitor service health, detect incidents, and reason about cascading failures. "
            "You have access to the following service metrics and status data.\n\n"
            "When analyzing:\n"
            "- Identify services that are degraded, stressed, or failing\n"
            "- Look for cascading patterns (e.g., DB stress → API latency → pipeline failures)\n"
            "- Assess impact by service tier (Tier 1 = critical, Tier 3 = low impact)\n"
            "- Recommend concrete remediation steps\n"
            "- Consider whether issues are correlated or independent\n\n"
            "Respond with your analysis as a JSON object with these keys:\n"
            '- "summary": 1-2 sentence overview of infrastructure health\n'
            '- "service_status": object mapping service names to {"status", "concern"}\n'
            '- "incidents": array of objects with "description", "severity" (low/medium/high/critical), '
            '"affected_services", "recommended_action"\n'
            '- "overall_health": "healthy", "degraded", or "critical"\n'
            '- "model": the model name you are running on\n'
            "Keep it concise and ops-focused."
        )

        user_prompt = f"Analyze the current infrastructure state:\n\n{ops_context}"
        if user_input:
            user_prompt += f"\n\nSpecific question: {user_input}"

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
        name="ops_agent",
        description=(
            f"Ops/SRE domain agent (model: {model_name}). Monitors service health, "
            "detects incidents and cascading failures. Can execute remediation actions "
            "(scale, restart, recover) that change service state."
        ),
        url=f"http://localhost:{_PORT}",
        version="0.1.0",
        defaultInputModes=["text/plain"],
        defaultOutputModes=["text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[
            AgentSkill(
                id="check_service_health",
                name="Check Service Health",
                description="Analyze infrastructure health: service status, metrics trends, incident detection, and cascading failure analysis.",
                tags=["ops", "sre", "infrastructure", "incidents", "health"],
                examples=[
                    "What's the current service health?",
                    "Are there any active incidents?",
                    "What's happening with payments-api latency?",
                ],
            ),
            AgentSkill(
                id="execute_remediation",
                name="Execute Remediation",
                description="Execute a remediation action (scale up, restart, recover) on affected services. Changes service state and logs recovery metrics.",
                tags=["ops", "remediation", "scale", "restart", "recovery"],
                examples=[
                    "Scale up customer-db",
                    "Restart payments-api",
                    "Approve: recover all degraded services",
                ],
            ),
        ],
    )

    executor = OpsAgentExecutor()
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
