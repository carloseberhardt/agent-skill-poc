"""
Data platform domain agent — A2A-compliant via a2a-sdk, backed by SQLite + own LLM.

Reasons about data access patterns, pipeline health, dataset classification,
and anomaly detection. Can execute actions (pause pipelines, revoke dataset access)
that change the DB state. Uses its own LLM model (DATA_AGENT_MODEL).

Run: uv run python mock-agents/data_agent.py
Serves on port 5001.
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

_PORT = 5001
_MODEL_ENV = "DATA_AGENT_MODEL"

wire = setup_wire_logger("data", "33")
llm = get_llm(_MODEL_ENV)
model_name = get_model_name(_MODEL_ENV)


def _query_data_context() -> str:
    """Pull data platform context from SQLite for the LLM."""
    conn = get_db()
    try:
        # Dataset inventory with status
        datasets = conn.execute(
            "SELECT name, classification, owner_team, pipeline_status, last_refresh, "
            "refresh_interval_hours, row_count, description FROM datasets ORDER BY classification DESC"
        ).fetchall()

        # Recent access logs with user details
        access_logs = conn.execute(
            "SELECT dal.*, e.name as user_name, e.role, e.department "
            "FROM data_access_logs dal "
            "JOIN employees e ON dal.user_id = e.id "
            "ORDER BY dal.timestamp DESC LIMIT 20"
        ).fetchall()

        # Access anomalies — users with unusually high row counts
        anomalies = conn.execute(
            "SELECT dal.user_id, e.name, e.role, e.department, "
            "dal.dataset, dal.row_count, dal.timestamp, dal.source_ip "
            "FROM data_access_logs dal "
            "JOIN employees e ON dal.user_id = e.id "
            "WHERE dal.row_count > 5000 "
            "ORDER BY dal.row_count DESC"
        ).fetchall()

        lines = ["## Dataset Inventory\n"]
        for ds in datasets:
            status = "⚠ STALE" if ds["pipeline_status"] == "stale" else "✓ healthy"
            lines.append(
                f"- **{ds['name']}** [{ds['classification'].upper()}] — {status}\n"
                f"  Owner: {ds['owner_team']} | Rows: {ds['row_count']:,} | "
                f"  Last refresh: {ds['last_refresh']} | Refresh interval: {ds['refresh_interval_hours']}h"
            )

        lines.append("\n## Recent Data Access\n")
        for a in access_logs:
            lines.append(
                f"- {a['user_name']} ({a['department']}) → {a['dataset']} "
                f"at {a['timestamp']} | {a['row_count']:,} rows | IP: {a['source_ip']}"
            )

        if anomalies:
            lines.append("\n## Access Anomalies (high row count)\n")
            for a in anomalies:
                lines.append(
                    f"- **{a['name']}** ({a['role']}, {a['department']}) → {a['dataset']} "
                    f"| {a['row_count']:,} rows at {a['timestamp']} from IP {a['source_ip']}"
                )

        return "\n".join(lines)
    finally:
        conn.close()


def _execute_action(raw_input: str) -> dict:
    """Execute a data platform action and update the DB."""
    now = datetime.now(timezone.utc)
    conn = get_db()
    lower = raw_input.lower()

    try:
        is_rejected = any(kw in lower for kw in ["reject", "denied", "deny"])
        if is_rejected:
            return {
                "action": "rejected",
                "summary": "Data platform action rejected by operator. No changes made.",
                "timestamp": now.isoformat(),
            }

        actions_taken = []

        # Pause/stop a pipeline
        if "pause" in lower or "stop" in lower:
            # Find mentioned datasets/pipelines
            datasets = conn.execute("SELECT name FROM datasets").fetchall()
            for ds in datasets:
                if ds["name"] in lower:
                    conn.execute(
                        "UPDATE datasets SET pipeline_status = 'paused' WHERE name = ?",
                        (ds["name"],)
                    )
                    actions_taken.append(f"Pipeline paused for dataset {ds['name']}.")

        # Resume/restart a pipeline
        if "resume" in lower or "restart" in lower or "refresh" in lower:
            datasets = conn.execute("SELECT name FROM datasets").fetchall()
            for ds in datasets:
                if ds["name"] in lower:
                    conn.execute(
                        "UPDATE datasets SET pipeline_status = 'healthy', last_refresh = ? "
                        "WHERE name = ?",
                        (now.isoformat(), ds["name"])
                    )
                    actions_taken.append(f"Pipeline restarted for dataset {ds['name']}. Status: healthy.")

        # Revoke dataset access for a user
        if "revoke" in lower or "restrict" in lower or "remove access" in lower:
            user_ids = []
            for uid in ["jliu", "schen", "mwebb", "psharma", "dtorres", "akim"]:
                if uid in lower:
                    user_ids.append(uid)
            name_map = {"james liu": "jliu", "sarah chen": "schen", "marcus webb": "mwebb",
                         "priya sharma": "psharma", "dana torres": "dtorres", "alex kim": "akim"}
            for name, uid in name_map.items():
                if name in lower and uid not in user_ids:
                    user_ids.append(uid)

            for uid in user_ids:
                deleted = conn.execute(
                    "DELETE FROM data_access_logs WHERE user_id = ? AND row_count > 5000",
                    (uid,)
                ).rowcount
                actions_taken.append(
                    f"Revoked anomalous data access for {uid}. "
                    f"Removed {deleted} high-volume access entries."
                )

        if not actions_taken:
            actions_taken.append("Action acknowledged and logged. No specific data platform changes identified.")

        conn.commit()

        return {
            "action": "executed",
            "actions_taken": actions_taken,
            "summary": " ".join(actions_taken),
            "timestamp": now.isoformat(),
        }
    finally:
        conn.close()


class DataAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user_input = context.get_user_input() or "general query"
        wire.info("◀ received: %s", user_input[:150])

        # Route action messages
        lower = user_input.lower()
        if any(kw in lower for kw in ["confirm", "reject", "approv", "pause", "stop",
                                       "resume", "revoke", "restrict access"]):
            result = _execute_action(user_input)
            wire.info("▶ action → %s", result.get("summary", "?")[:100])
            await event_queue.enqueue_event(new_agent_text_message(json.dumps(result)))
            return

        data_context = _query_data_context()

        system_prompt = (
            f"You are a data platform analysis agent (model: {model_name}). "
            "You monitor dataset health, access patterns, and pipeline status. "
            "You have access to the following data platform telemetry.\n\n"
            "When analyzing:\n"
            "- Check pipeline health — are any datasets stale or behind schedule?\n"
            "- Look for unusual access patterns — volume, timing, user role vs data sensitivity\n"
            "- Flag potential data quality issues or access policy violations\n"
            "- Note any correlations between access patterns and pipeline problems\n\n"
            "Respond with your analysis as a JSON object with these keys:\n"
            '- "summary": 1-2 sentence overview of data platform health\n'
            '- "pipeline_status": object mapping dataset names to status ("healthy", "stale", "failing")\n'
            '- "access_concerns": array of strings describing any concerning access patterns\n'
            '- "recommendations": array of actionable recommendations\n'
            '- "model": the model name you are running on\n'
            "Keep it concise and data-driven."
        )

        user_prompt = f"Analyze the data platform based on this telemetry:\n\n{data_context}"
        if user_input and user_input != "general query":
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
        name="data_agent",
        description=(
            f"Data platform domain agent (model: {model_name}). Monitors dataset health, "
            "pipeline status, and data access patterns. Can execute actions (pause pipelines, "
            "revoke access) that change data platform state."
        ),
        url=f"http://localhost:{_PORT}",
        version="0.2.0",
        defaultInputModes=["text/plain"],
        defaultOutputModes=["text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[
            AgentSkill(
                id="query_data_platform",
                name="Query Data Platform",
                description="Analyze data platform health: dataset freshness, pipeline status, access patterns, and anomalies.",
                tags=["data", "platform", "pipelines", "access-patterns"],
                examples=[
                    "What's the health of our data pipelines?",
                    "Any unusual data access patterns?",
                    "Which datasets are stale?",
                ],
            ),
            AgentSkill(
                id="execute_data_action",
                name="Execute Data Action",
                description="Execute a data platform action (pause pipeline, revoke access, restart refresh). Changes dataset and access state.",
                tags=["data", "action", "pipeline", "access-control"],
                examples=[
                    "Pause analytics-pipeline",
                    "Revoke jliu access to customer-pii",
                    "Restart analytics-summary refresh",
                ],
            ),
        ],
    )

    executor = DataAgentExecutor()
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
