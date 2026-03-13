"""
HTTP API for the Solis skill runtime.

Endpoints:
  POST /invoke/{skill_name}  — Manually trigger any skill
  POST /event/{event_name}   — Emit an event to the bus
  GET  /skills               — List loaded skills and status
  GET  /status               — Runtime health and uptime
  GET  /events               — SSE stream of skill results
  POST /reload-skills        — Hot-reload skills from disk
"""

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from runtime.event_bus import EventBus
from runtime.scheduler import SkillScheduler
from runtime.skill_executor import execute_skill
from runtime.skill_loader import Skill, SkillResult, load_skills

logger = logging.getLogger("solis.api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start the scheduler once the event loop is running."""
    if _scheduler:
        _scheduler.start()
        logger.info("Scheduler started")
    yield
    if _scheduler:
        _scheduler.shutdown()
        logger.info("Scheduler stopped")


app = FastAPI(title="Solis POC Runtime", version="0.1.0", lifespan=lifespan)

# Injected by main.py via init()
_skills: list[Skill] = []
_scheduler: SkillScheduler | None = None
_event_bus: EventBus | None = None
_start_time: float = time.time()

# SSE client queues — one per connected browser tab
_sse_clients: set[asyncio.Queue] = set()

# Recent skill results — the runtime's memory. Any skill (especially chat) can
# read this to reason about what other skills have produced without re-running them.
# This is a runtime concern, not a skill concern.
_result_history: list[SkillResult] = []
_MAX_HISTORY = 20


def init(skills: list[Skill], scheduler: SkillScheduler, event_bus: EventBus) -> None:
    global _skills, _scheduler, _event_bus, _start_time
    _skills = skills
    _scheduler = scheduler
    _event_bus = event_bus
    _start_time = time.time()


def get_result_history() -> list[SkillResult]:
    return list(_result_history)


async def broadcast_result(result: SkillResult) -> None:
    """Push a skill result to all connected SSE clients and store in history."""
    _result_history.append(result)
    if len(_result_history) > _MAX_HISTORY:
        _result_history.pop(0)

    data = result.model_dump_json()
    dead: list[asyncio.Queue] = []
    for queue in _sse_clients:
        try:
            queue.put_nowait(data)
        except asyncio.QueueFull:
            dead.append(queue)
    for q in dead:
        _sse_clients.discard(q)


def _find_skill(name: str) -> Skill | None:
    return next((s for s in _skills if s.name == name), None)


@app.post("/invoke/{skill_name}")
async def invoke_skill(skill_name: str, request: Request):
    skill = _find_skill(skill_name)
    if not skill:
        return JSONResponse({"error": f"Skill '{skill_name}' not found"}, status_code=404)

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    context = {
        "skill": skill,
        "trigger": "manual",
        "event_bus": _event_bus,
        "all_skills": _skills,
        **body,
    }
    result = await execute_skill(skill, context)
    if result and result.skill_name != "chat":
        _result_history.append(result)
        if len(_result_history) > _MAX_HISTORY:
            _result_history.pop(0)
    return result.model_dump() if result else {"status": "ok"}


@app.post("/event/{event_name}")
async def emit_event(event_name: str, request: Request):
    if not _event_bus:
        return JSONResponse({"error": "Event bus not initialized"}, status_code=500)

    payload = {}
    try:
        payload = await request.json()
    except Exception:
        pass

    await _event_bus.emit(event_name, payload)
    return JSONResponse({"status": "emitted", "event": event_name}, status_code=202)


@app.post("/action")
async def handle_action(request: Request):
    """Send an approval decision directly to the target agent via A2A.

    The frontend sends the action_payload + decision. The runtime forwards
    it as an A2A message to the target agent through the gateway — no LLM
    in the loop, no event bus, just a direct message.
    """
    body = await request.json()
    decision = body.get("decision", "")
    action = body.get("action", "")
    target_agent = body.get("target_agent", "")
    skill_name = body.get("skill_name", "")
    title = body.get("title", "")

    if not target_agent:
        return JSONResponse({"error": "No target_agent specified"}, status_code=400)

    from runtime import agent as agent_mod

    # Build a message describing the decision for the target agent
    message = (
        f"Log action {decision}: {action}\n"
        f"Source: {skill_name} — {title}\n"
        f"Decision: {decision}"
    )

    try:
        gateway_url = agent_mod._gateway_url
        agent_url = f"{gateway_url}/{target_agent}"
        response_text = await agent_mod._call_a2a_agent(agent_url, message)

        # Broadcast a status card so the UI shows confirmation
        result = SkillResult(
            skill_name=f"action → {target_agent}",
            ui_type="card",
            content={
                "title": f"Action {decision.title()}",
                "bullets": [
                    f"Decision: **{decision}**",
                    f"Action: {action[:200]}",
                    f"Logged with: {target_agent}",
                    f"Agent response: {response_text[:200]}",
                ],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            timestamp=datetime.now(timezone.utc),
        )
        await broadcast_result(result)
        return result.model_dump()

    except Exception:
        logger.exception("Failed to send action to %s", target_agent)
        return JSONResponse(
            {"error": f"Could not reach agent '{target_agent}'"},
            status_code=502,
        )


@app.post("/clear")
async def clear_history():
    """Clear all skill result history — resets the runtime's conversational context."""
    _result_history.clear()
    return {"status": "cleared"}


@app.get("/skills")
async def list_skills():
    return [
        {
            "name": s.name,
            "description": s.description,
            "trigger": s.runtime_config.trigger,
            "trigger_config": s.runtime_config.trigger_config,
            "ui_type": s.runtime_config.ui_type,
        }
        for s in _skills
    ]


@app.get("/status")
async def status():
    return {
        "status": "running",
        "uptime_seconds": round(time.time() - _start_time, 1),
        "skills_loaded": len(_skills),
        "event_subscriptions": _event_bus.subscriptions if _event_bus else {},
    }


@app.post("/reload-skills")
async def reload_skills():
    """Hot-reload skills from disk without restarting the runtime."""
    global _skills
    skills_dir = Path("skills")
    new_skills = load_skills(skills_dir)

    # Re-register schedules and events
    if _scheduler:
        _scheduler.clear()
    if _event_bus:
        _event_bus.clear()

    context_extras = {"event_bus": _event_bus}
    demo_mode = os.getenv("DEMO_MODE") == "true"

    for skill in new_skills:
        trigger = skill.runtime_config.trigger
        if trigger == "scheduled":
            if demo_mode and skill.runtime_config.trigger_config:
                skill.runtime_config.trigger_config = "* * * * *"
            if _scheduler:
                _scheduler.register(skill, context_extras=context_extras)
        elif trigger == "event":
            event_name = skill.runtime_config.trigger_config
            if event_name and _event_bus:
                _event_bus.subscribe(event_name, skill, context_extras=context_extras)

    _skills = new_skills

    # Also refresh MCP tools — agents may have been registered since startup
    from runtime import agent
    await agent.refresh_tools()

    logger.info("Reloaded %d skill(s)", len(new_skills))
    return {"status": "reloaded", "skills_loaded": len(new_skills), "tools_available": agent.has_tools()}


@app.get("/events")
async def sse_events(request: Request):
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    _sse_clients.add(queue)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield {"event": "skill_result", "data": data}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": ""}
        finally:
            _sse_clients.discard(queue)

    return EventSourceResponse(event_generator())


# Static files must be mounted last — it's a catch-all.
# Access the UI at http://localhost:8000, not by opening index.html from disk.
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
