"""
Data platform domain agent — A2A-compliant via a2a-sdk, backed by SQLite + own LLM.

This is a real agentic loop: the LLM has domain-specific tools and decides
what to query based on the question. It may call multiple tools, reason
about the results, and call more tools before producing a final answer.

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
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from agent_common import get_db, get_llm, get_model_name, setup_wire_logger

_PORT = 5001
_MODEL_ENV = "DATA_AGENT_MODEL"

wire = setup_wire_logger("data", "33")
llm = get_llm(_MODEL_ENV)
model_name = get_model_name(_MODEL_ENV)


# ── Domain tools ──────────────────────────────────────────────

@tool
def get_dataset_inventory() -> str:
    """List all datasets with their classification, pipeline status, freshness, and row count."""
    conn = get_db()
    try:
        datasets = conn.execute(
            "SELECT name, classification, owner_team, pipeline_status, last_refresh, "
            "refresh_interval_hours, row_count, description FROM datasets ORDER BY classification DESC"
        ).fetchall()

        lines = []
        for ds in datasets:
            status = "STALE" if ds["pipeline_status"] == "stale" else ds["pipeline_status"]
            lines.append(
                f"{ds['name']} [{ds['classification'].upper()}] — {status} | "
                f"Owner: {ds['owner_team']} | Rows: {ds['row_count']:,} | "
                f"Last refresh: {ds['last_refresh']} | Interval: {ds['refresh_interval_hours']}h\n"
                f"  {ds['description']}"
            )
        return "\n".join(lines)
    finally:
        conn.close()


@tool
def get_recent_access(user_id: str = "", dataset: str = "", limit: int = 20) -> str:
    """Query recent data access logs. Optionally filter by user_id and/or dataset name."""
    conn = get_db()
    try:
        query = (
            "SELECT dal.*, e.name as user_name, e.role, e.department "
            "FROM data_access_logs dal "
            "JOIN employees e ON dal.user_id = e.id "
            "WHERE 1=1"
        )
        params = []
        if user_id:
            query += " AND dal.user_id = ?"
            params.append(user_id)
        if dataset:
            query += " AND dal.dataset = ?"
            params.append(dataset)
        query += " ORDER BY dal.timestamp DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        if not rows:
            return "No access logs found matching the criteria."

        lines = []
        for a in rows:
            lines.append(
                f"{a['user_name']} ({a['role']}, {a['department']}) → {a['dataset']} "
                f"at {a['timestamp']} | {a['row_count']:,} rows | "
                f"IP: {a['source_ip']} | Duration: {a['duration_ms']}ms"
            )
        return "\n".join(lines)
    finally:
        conn.close()


@tool
def get_access_anomalies(min_row_count: int = 5000) -> str:
    """Find data access entries with unusually high row counts. Helps identify
    potential exfiltration, runaway queries, or policy violations."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT dal.user_id, e.name, e.role, e.department, "
            "dal.dataset, dal.row_count, dal.timestamp, dal.source_ip "
            "FROM data_access_logs dal "
            "JOIN employees e ON dal.user_id = e.id "
            "WHERE dal.row_count > ? "
            "ORDER BY dal.row_count DESC",
            (min_row_count,),
        ).fetchall()

        if not rows:
            return f"No access entries with row count > {min_row_count}. Access patterns appear normal."

        lines = []
        for a in rows:
            lines.append(
                f"{a['name']} ({a['role']}, {a['department']}) → {a['dataset']} "
                f"| {a['row_count']:,} rows at {a['timestamp']} from IP {a['source_ip']}"
            )
        return "\n".join(lines)
    finally:
        conn.close()


@tool
def get_employee_info(user_id: str) -> str:
    """Look up an employee by user ID. Returns role, department, clearance, and notes."""
    conn = get_db()
    try:
        emp = conn.execute("SELECT * FROM employees WHERE id = ?", (user_id,)).fetchone()
        if not emp:
            return f"No employee found with ID '{user_id}'."
        return (
            f"Name: {emp['name']} | Role: {emp['role']} | Department: {emp['department']} | "
            f"Team: {emp['team']} | Clearance: {emp['clearance']} | "
            f"Manager: {emp['manager_id'] or 'none'}\n"
            f"Notes: {emp['notes']}"
        )
    finally:
        conn.close()


@tool
def pause_pipeline(dataset: str) -> str:
    """Pause the data pipeline for a specific dataset."""
    conn = get_db()
    try:
        existing = conn.execute("SELECT name, pipeline_status FROM datasets WHERE name = ?", (dataset,)).fetchone()
        if not existing:
            return f"Dataset '{dataset}' not found."
        conn.execute("UPDATE datasets SET pipeline_status = 'paused' WHERE name = ?", (dataset,))
        conn.commit()
        return f"Pipeline paused for dataset '{dataset}'. Previous status: {existing['pipeline_status']}."
    finally:
        conn.close()


@tool
def revoke_anomalous_access(user_id: str) -> str:
    """Revoke high-volume data access entries for a user. Removes access log entries
    with row count > 5000, simulating an access restriction."""
    conn = get_db()
    try:
        deleted = conn.execute(
            "DELETE FROM data_access_logs WHERE user_id = ? AND row_count > 5000",
            (user_id,)
        ).rowcount
        conn.commit()
        if deleted == 0:
            return f"No high-volume access entries found for user '{user_id}'. No changes made."
        return f"Revoked {deleted} high-volume access entries for user '{user_id}'."
    finally:
        conn.close()


# ── Agent setup ───────────────────────────────────────────────

_tools = [get_dataset_inventory, get_recent_access, get_access_anomalies,
          get_employee_info, pause_pipeline, revoke_anomalous_access]

_system_prompt = (
    f"You are a data platform analysis agent running on model: {model_name}. "
    "You have tools to inspect datasets, query access logs, find anomalies, "
    "look up employees, and take remediation actions.\n\n"
    "Use your tools to investigate before answering. Don't guess — query the data.\n\n"
    "When analyzing:\n"
    "- Check dataset health — are any pipelines stale or behind schedule?\n"
    "- Look at access patterns — volume, timing, user role vs data sensitivity\n"
    "- If you find anomalies, look up the user to understand if the access fits their role\n"
    "- Flag potential data quality issues or access policy violations\n\n"
    "Respond with a JSON object containing:\n"
    '- "summary": 1-2 sentence overview of data platform health\n'
    '- "pipeline_status": object mapping dataset names to status\n'
    '- "access_concerns": array of strings describing any concerning access patterns\n'
    '- "recommendations": array of actionable recommendations\n'
    f'- "model": "{model_name}"\n'
    "Keep it concise and data-driven."
)

_agent = create_react_agent(llm, tools=_tools)


# ── A2A executor ──────────────────────────────────────────────

class DataAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user_input = context.get_user_input() or "Analyze the current state of the data platform."
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
        name="data_agent",
        description=(
            f"Data platform domain agent (model: {model_name}). Investigates dataset health, "
            "pipeline status, and data access patterns using its own tools and reasoning. "
            "Can execute actions (pause pipelines, revoke access)."
        ),
        url=f"http://localhost:{_PORT}",
        version="0.3.0",
        defaultInputModes=["text/plain"],
        defaultOutputModes=["text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[
            AgentSkill(
                id="query_data_platform",
                name="Query Data Platform",
                description="Investigate data platform health: dataset freshness, pipeline status, access patterns, and anomalies. Uses tools to query data and reason about findings.",
                tags=["data", "platform", "pipelines", "access-patterns"],
                examples=[
                    "What's the health of our data pipelines?",
                    "Any unusual data access patterns?",
                    "Which datasets are stale?",
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
