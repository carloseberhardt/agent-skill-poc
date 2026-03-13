"""
Mock data platform domain agent — A2A-compliant via a2a-sdk.

In production, this is a real A2A agent operated by a product team.
The coordinator runtime doesn't care what's inside it.

Run: uv run python mock-agents/data_agent.py
Serves on port 5001.
"""

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

_PORT = 5001

_ACTIVITIES = [
    {"type": "activity", "summary": "3 new Spark jobs created in project Alpha", "severity": "info"},
    {"type": "activity", "summary": "Query volume up 40% week-over-week on production lakehouse", "severity": "info"},
    {"type": "activity", "summary": "New Iceberg table registered: customer_360_v2", "severity": "info"},
    {"type": "activity", "summary": "Data ingestion pipeline for sales_events paused by admin", "severity": "warning"},
    {"type": "activity", "summary": "Schema migration completed on analytics warehouse", "severity": "info"},
]

_ACCESS_PATTERNS = [
    {"type": "access", "summary": "User jdoe ran 47 queries against sensitive_customers table in the last 24h", "severity": "warning"},
    {"type": "access", "summary": "Service account etl-prod accessed 12 datasets across 3 projects", "severity": "info"},
    {"type": "access", "summary": "New IAM role 'analyst-readonly' granted access to production lakehouse", "severity": "info"},
    {"type": "access", "summary": "Bulk export of financial_transactions initiated by user msmith", "severity": "warning"},
]


def _do_query(q: str) -> dict:
    q = q.lower()
    if "access" in q:
        results = random.sample(_ACCESS_PATTERNS, k=min(3, len(_ACCESS_PATTERNS)))
    elif "new" in q or "yesterday" in q:
        results = random.sample(_ACTIVITIES, k=min(3, len(_ACTIVITIES)))
    else:
        results = random.sample(_ACTIVITIES + _ACCESS_PATTERNS, k=3)

    return {
        "agent": "data_agent",
        "domain": "data_platform",
        "results": results,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


class DataAgentExecutor(AgentExecutor):
    """Handles A2A message/send requests by querying mock data."""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user_input = context.get_user_input()
        query = user_input or "general query"
        result = _do_query(query)
        import json
        await event_queue.enqueue_event(new_agent_text_message(json.dumps(result)))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        pass


def build_app():
    agent_card = AgentCard(
        name="data_agent",
        description="Data platform domain agent — queries activity, access patterns, and pipeline status.",
        url=f"http://localhost:{_PORT}",
        version="0.1.0",
        defaultInputModes=["text/plain"],
        defaultOutputModes=["text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[
            AgentSkill(
                id="query_data_platform",
                name="Query Data Platform",
                description="Query the data platform for recent activity, access patterns, and pipeline status.",
                tags=["data", "platform", "activity"],
                examples=["What happened on the data platform today?", "Show recent access patterns"],
            )
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
