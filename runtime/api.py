"""
HTTP API for the Solis skill runtime.

Endpoints:
  POST /invoke/{skill_name}  — Manually trigger any skill
  POST /event/{event_name}   — Emit an event to the bus
  GET  /skills               — List loaded skills and status
  GET  /status               — Runtime health and uptime
  GET  /events               — SSE stream of skill results
  POST /reload-skills        — Hot-reload skills from disk
  POST /timer/start          — Unpause the monitor timer
  POST /timer/pause          — Pause the monitor timer
  POST /timer/run-now        — Trigger one monitor tick immediately
  GET  /timer/status         — Current timer state
"""

import asyncio
import json
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
    """Timer starts paused — no auto-start."""
    logger.info("Runtime started (monitor timer paused)")
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

# Chat conversation history — stores recent user/assistant exchanges so the
# chat skill has conversational context across invocations. Without this,
# each chat message is independent and the LLM can't follow multi-turn flows
# (e.g. delivery agent asking for a tracking number, user providing it next).
_chat_history: list[dict[str, str]] = []  # [{"role": "user"|"assistant", "content": "..."}]
_MAX_CHAT_HISTORY = 20  # messages, not turns


def init(skills: list[Skill], scheduler: SkillScheduler, event_bus: EventBus) -> None:
    global _skills, _scheduler, _event_bus, _start_time
    _skills = skills
    _scheduler = scheduler
    _event_bus = event_bus
    _start_time = time.time()
    # Wire state-change callback to broadcast timer status via SSE
    scheduler._on_state_change = _broadcast_timer_status


def get_result_history() -> list[SkillResult]:
    return list(_result_history)


def get_chat_history() -> list[dict[str, str]]:
    return list(_chat_history)


def append_chat_history(role: str, content: str) -> None:
    _chat_history.append({"role": role, "content": content})
    while len(_chat_history) > _MAX_CHAT_HISTORY:
        _chat_history.pop(0)


async def broadcast_result(result: SkillResult) -> None:
    """Push a skill result to all connected SSE clients and store in history."""
    _result_history.append(result)
    if len(_result_history) > _MAX_HISTORY:
        _result_history.pop(0)

    data = result.model_dump_json()
    _broadcast_sse("skill_result", data)


async def broadcast_activity(message: str, category: str = "info", detail: dict | None = None) -> None:
    """Push an activity feed item to all connected SSE clients."""
    payload = {
        "message": message,
        "category": category,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if detail:
        payload["detail"] = detail
    _broadcast_sse("activity", json.dumps(payload))


def _broadcast_sse(event_type: str, data: str) -> None:
    """Push an SSE event to all connected clients."""
    dead: list[asyncio.Queue] = []
    for queue in _sse_clients:
        try:
            queue.put_nowait((event_type, data))
        except asyncio.QueueFull:
            dead.append(queue)
    for q in dead:
        _sse_clients.discard(q)


def _broadcast_timer_status() -> None:
    """Push current timer status to all SSE clients."""
    if _scheduler:
        data = json.dumps(_scheduler.status)
        _broadcast_sse("timer_status", data)


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

    await broadcast_activity(f"Skill invoked: {skill_name}", "skill")

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

    await broadcast_activity(f"Event received: {event_name}", "event")
    await _event_bus.emit(event_name, payload)
    return JSONResponse({"status": "emitted", "event": event_name}, status_code=202)


@app.post("/action")
async def handle_action(request: Request):
    """Handle an approval decision by routing it through the runtime agent.

    The frontend sends the decision + context. The runtime agent decides
    how to execute — which agents to call, which tools to use, what to
    notify. Same path as any other skill execution.
    """
    body = await request.json()
    decision = body.get("decision", "")
    action = body.get("action", "")
    target_agent = body.get("target_agent", "")
    skill_name = body.get("skill_name", "")
    title = body.get("title", "")

    await broadcast_activity(f"Action {decision}: {action or title}", "skill")

    from runtime import agent as agent_mod

    # Build a prompt for the runtime agent describing what was approved/rejected
    prompt = (
        f"The user has {decision} an action from the {skill_name} skill.\n\n"
        f"Title: {title}\n"
        f"Action: {action}\n"
        f"Decision: {decision}\n\n"
    )

    if "confirmed" in decision.lower() or "approved" in decision.lower():
        prompt += (
            "Execute this action now using your available tools and agents. "
            "Use as many agents and tools as needed to fully carry out the action. "
        )
    else:
        prompt += (
            "The action was rejected. Log this decision. "
            "No remediation should be taken."
        )

    system_prompt = (
        "You are executing an approved action from the Solis runtime. "
        "You MUST actually call your tools to carry out the action — do not "
        "claim to have done something without making the tool call. "
        "If the action requires an agent, send the request to that agent. "
        "If a tool call fails, report the failure honestly.\n\n"
        "Only include facts that came directly from your tool call results. "
        "Do not fabricate confirmation messages or invent details.\n\n"
        "Respond with a JSON object containing:\n"
        '- "title": a short summary of what was done\n'
        '- "bullets": array of 3-5 bullet points describing actions taken\n'
        "Do not include any text outside the JSON object."
    )

    try:
        await agent_mod.ensure_initialized()
        response = await agent_mod.invoke(prompt, system_prompt=system_prompt, skill_name=f"action:{skill_name}")

        # Parse the response
        from runtime.skill_executor import _parse_json_response, _strip_code_fences
        from runtime.skill_loader import Skill, RuntimeConfig
        dummy_skill = Skill(
            name="action", description="", instructions="",
            path=__import__("pathlib").Path("."),
            runtime_config=RuntimeConfig(ui_type="card"),
        )
        content = _parse_json_response(response, dummy_skill)
        content.setdefault("timestamp", datetime.now(timezone.utc).isoformat())

        result = SkillResult(
            skill_name=f"action → {skill_name}",
            ui_type="card",
            content=content,
            timestamp=datetime.now(timezone.utc),
        )
        await broadcast_result(result)
        return result.model_dump()

    except Exception:
        logger.exception("Failed to execute action for %s", skill_name)
        return JSONResponse(
            {"error": "Action execution failed"},
            status_code=500,
        )


@app.post("/a2a-callback/{agent_name}")
@app.post("/a2a-callback")
async def a2a_callback(request: Request, agent_name: str | None = None):
    """Receive push notifications from A2A agents.

    The a2a-sdk's BasePushNotificationSender POSTs the full Task object here.
    Each agent gets a unique callback URL (/a2a-callback/{agent_name}) so we
    can attribute pushes without relying on task ID lookups, which lose a race
    with the push notification.
    """
    from a2a.types import Task as A2ATask, TaskState
    from a2a.utils.parts import get_text_parts
    from a2a.utils.message import get_message_text
    from runtime.agent import get_pending_task, resolve_pending_task, _extract_task_text

    body = await request.json()
    logger.info("A2A callback received (agent=%s): %s", agent_name, json.dumps(body)[:200])

    # Parse as proper A2A Task
    try:
        task = A2ATask.model_validate(body)
    except Exception:
        logger.warning("Could not parse A2A callback as Task, falling back to raw handling")
        if _event_bus:
            await _event_bus.emit("agent_notification", {"raw": body})
        return JSONResponse({"status": "received", "event": "agent_notification"}, status_code=202)

    task_id = task.id
    state = task.status.state
    text = _extract_task_text(task)

    # Resolve agent name: URL path (always available) > pending task lookup > fallback
    pending = get_pending_task(task_id)
    resolved_agent = agent_name or (pending or {}).get("agent_name") or "unknown"
    original_query = (pending or {}).get("query", "")

    await broadcast_activity(
        f"Push notification from {resolved_agent}: task {task_id} → {state.value}",
        "agent",
        detail={"type": "push_notification", "agent": resolved_agent, "task_id": task_id, "state": state.value},
    )

    if state in {TaskState.completed, TaskState.failed, TaskState.canceled, TaskState.rejected}:
        # Terminal state — build a SkillResult and broadcast
        if pending:
            resolve_pending_task(task_id)

        result = SkillResult(
            skill_name=f"notification → {resolved_agent}",
            ui_type="card",
            content={
                "title": f"Update from {resolved_agent}",
                "bullets": [text] if text else [f"Task {state.value}"],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "task_id": task_id,
                "original_query": original_query,
            },
            timestamp=datetime.now(timezone.utc),
            trigger_type="push_notification",
            trigger_source=resolved_agent,
        )
        await broadcast_result(result)
        logger.info("A2A push → SkillResult for task %s from %s (%s)", task_id, resolved_agent, state.value)
    else:
        # Non-terminal update (working, input-required, etc.) — just log activity
        logger.info("A2A push → task %s from %s: %s", task_id, resolved_agent, state.value)

    return JSONResponse({"status": "received", "task_id": task_id, "state": state.value}, status_code=202)


@app.post("/clear")
async def clear_history():
    """Clear all skill result history and chat history — resets the runtime's conversational context."""
    _result_history.clear()
    _chat_history.clear()
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


# ── Timer endpoints ───────────────────────────────────────────────

@app.post("/timer/start")
async def timer_start():
    if not _scheduler:
        return JSONResponse({"error": "Scheduler not initialized"}, status_code=500)
    _scheduler.start()
    return _scheduler.status


@app.post("/timer/pause")
async def timer_pause():
    if not _scheduler:
        return JSONResponse({"error": "Scheduler not initialized"}, status_code=500)
    _scheduler.pause()
    return _scheduler.status


@app.post("/timer/run-now")
async def timer_run_now():
    if not _scheduler:
        return JSONResponse({"error": "Scheduler not initialized"}, status_code=500)
    await _scheduler.run_now()
    return _scheduler.status


@app.get("/timer/status")
async def timer_status():
    if not _scheduler:
        return JSONResponse({"error": "Scheduler not initialized"}, status_code=500)
    return _scheduler.status


# ── Status & skill definitions ────────────────────────────────────

@app.get("/status")
async def status():
    timer = _scheduler.status if _scheduler else {}
    return {
        "status": "running",
        "uptime_seconds": round(time.time() - _start_time, 1),
        "skills_loaded": len(_skills),
        "model": os.getenv("LITELLM_MODEL", "unknown"),
        "event_subscriptions": _event_bus.subscriptions if _event_bus else {},
        "timer": timer,
    }


@app.get("/skills/{skill_name}/definition")
async def skill_definition(skill_name: str):
    """Return the raw SKILL.md content and runtime.config.json for a skill."""
    skill = _find_skill(skill_name)
    if not skill:
        return JSONResponse({"error": f"Skill '{skill_name}' not found"}, status_code=404)

    skill_md = ""
    config_json = None
    skill_md_path = skill.path / "SKILL.md"
    config_path = skill.path / "runtime.config.json"

    if skill_md_path.exists():
        skill_md = skill_md_path.read_text()
    if config_path.exists():
        config_json = json.loads(config_path.read_text())

    return {"skill_md": skill_md, "runtime_config": config_json}


@app.post("/reload-skills")
async def reload_skills():
    """Hot-reload skills from disk without restarting the runtime."""
    global _skills
    skills_dir = Path("skills")
    new_skills = load_skills(skills_dir)

    # Preserve timer running state across reload
    was_running = _scheduler._running if _scheduler else False

    # Re-register schedules and events
    if _scheduler:
        _scheduler.pause()
        _scheduler.clear()
    if _event_bus:
        _event_bus.clear()

    context_extras = {"event_bus": _event_bus}

    for skill in new_skills:
        trigger = skill.runtime_config.trigger
        if trigger == "scheduled":
            if _scheduler:
                _scheduler.register_monitor(skill, context_extras=context_extras)
        elif trigger == "event":
            event_name = skill.runtime_config.trigger_config
            if event_name and _event_bus:
                _event_bus.subscribe(event_name, skill, context_extras=context_extras)

    _skills = new_skills

    # Restore timer state
    if was_running and _scheduler:
        _scheduler.start()

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
                    item = await asyncio.wait_for(queue.get(), timeout=30.0)
                    if isinstance(item, tuple):
                        event_type, data = item
                    else:
                        event_type, data = "skill_result", item
                    yield {"event": event_type, "data": data}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": ""}
        finally:
            _sse_clients.discard(queue)

    return EventSourceResponse(event_generator())


# Static files must be mounted last — it's a catch-all.
# Access the UI at http://localhost:8000, not by opening index.html from disk.
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
