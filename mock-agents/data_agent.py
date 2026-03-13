"""
Mock data platform domain agent — A2A-compliant via a2a-sdk.

In production, this is a real A2A agent operated by a product team.
The coordinator runtime doesn't care what's inside it.

Run: uv run python mock-agents/data_agent.py
Serves on port 5001.
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

_PORT = 5001

wire = logging.getLogger("wire")
if os.getenv("WIRE_LOG") == "true":
    logging.basicConfig(level=logging.INFO)
    wire.setLevel(logging.INFO)
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("\033[33m%(asctime)s [wire:data] %(message)s\033[0m", datefmt="%H:%M:%S"))
    wire.addHandler(_h)
    wire.propagate = False
else:
    wire.setLevel(logging.WARNING)

_ACTIVITIES = [
    {"type": "activity", "summary": "3 new Spark jobs created in project Alpha", "severity": "info"},
    {"type": "activity", "summary": "Query volume up 40% week-over-week on production lakehouse", "severity": "info"},
    {"type": "activity", "summary": "New Iceberg table registered: customer_360_v2", "severity": "info"},
    {"type": "activity", "summary": "Data ingestion pipeline for sales_events paused by admin", "severity": "warning"},
    {"type": "activity", "summary": "Schema migration completed on analytics warehouse", "severity": "info"},
    {"type": "activity", "summary": "Automated data quality checks flagged 12 records in orders_staging", "severity": "warning"},
    {"type": "activity", "summary": "New dbt project 'finance_metrics' deployed to production", "severity": "info"},
    {"type": "activity", "summary": "Lakehouse storage grew 18% this week — approaching 80% capacity", "severity": "warning"},
    {"type": "activity", "summary": "Real-time streaming pipeline for clickstream_events went live", "severity": "info"},
    {"type": "activity", "summary": "Scheduled maintenance window completed for Redshift cluster prod-analytics", "severity": "info"},
    {"type": "activity", "summary": "Delta table compaction job finished for inventory_snapshots (saved 340 GB)", "severity": "info"},
    {"type": "activity", "summary": "ML feature store refresh completed — 24 features updated", "severity": "info"},
]

_ACCESS_PATTERNS = [
    {"type": "access", "summary": "User jdoe ran 47 queries against sensitive_customers table in the last 24h", "severity": "warning"},
    {"type": "access", "summary": "Service account etl-prod accessed 12 datasets across 3 projects", "severity": "info"},
    {"type": "access", "summary": "New IAM role 'analyst-readonly' granted access to production lakehouse", "severity": "info"},
    {"type": "access", "summary": "Bulk export of financial_transactions initiated by user msmith", "severity": "warning"},
    {"type": "access", "summary": "User agarcia queried hr_compensation table 8 times in the last hour", "severity": "warning"},
    {"type": "access", "summary": "User tpatel granted temporary write access to staging environment", "severity": "info"},
    {"type": "access", "summary": "User kwong accessed production ML model registry for the first time", "severity": "info"},
    {"type": "access", "summary": "Cross-account data share activated between project Alpha and project Gamma", "severity": "info"},
    {"type": "access", "summary": "Service account analytics-bot ran 200+ queries in a 15-minute window", "severity": "warning"},
    {"type": "access", "summary": "User agarcia downloaded full customer_segments dataset via Jupyter notebook", "severity": "warning"},
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
        wire.info("◀ received: %s", query[:150])
        result = _do_query(query)
        wire.info("▶ query → %d results", len(result.get("results", [])))
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
