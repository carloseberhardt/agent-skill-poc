"""
Generic skill executor — replaces per-skill handler.py files.

Reads SKILL.md instructions, builds prompts based on ui_type and context,
calls the agent, and parses the response into a SkillResult.
"""

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone

from runtime import agent
from runtime.skill_loader import Skill, SkillResult

logger = logging.getLogger("solis.skill_executor")

# Trace logger — dedicated to the skill execution chain so you can follow
# the flow: skill triggered → tools called → events emitted → skills triggered
trace = logging.getLogger("solis.trace")

# Matches [INVOKE:skill-name] markers that the chat skill LLM may produce
_INVOKE_PATTERN = re.compile(r"\[INVOKE:([\w-]+)\]")

# Matches [EMIT:event_name] markers that non-chat skills may produce to trigger events
_EMIT_PATTERN = re.compile(r"\[EMIT:([\w_]+)\]")

# Output format instructions appended to skill instructions based on ui_type
_FORMAT_INSTRUCTIONS = {
    "card": (
        "\n\nRespond with a JSON object containing exactly these keys:\n"
        '- "title": a short headline\n'
        '- "bullets": an array of 3-5 concise bullet point strings\n'
        "Do not include any text outside the JSON object."
    ),
    "approval": (
        "\n\nRespond with a JSON object containing exactly these keys:\n"
        '- "title": a short headline\n'
        '- "bullets": an array of 3-5 key findings\n'
        '- "action_recommended": true or false\n'
        '- "action": if action_recommended is true, a string describing the recommended action. '
        "If false, omit this key.\n"
        '- "target_agent": if action_recommended is true, the agent that should '
        "handle this action. Use the short agent name (e.g. 'security-agent' not "
        "'security-agent_query_security'). If false, omit this key.\n"
        "Do not include any text outside the JSON object."
    ),
    "chat": "\n\nRespond conversationally in plain text. Keep responses concise and helpful.",
}


def _build_system_prompt(skill: Skill, all_skills: list[Skill] | None = None) -> str:
    """Build system prompt from SKILL.md instructions + format instructions."""
    base = skill.instructions

    # For chat skills, inject the list of available skills dynamically
    if skill.runtime_config.ui_type == "chat" and all_skills:
        skill_list = "\n".join(
            f"- {s.name}: {s.description}"
            for s in all_skills
            if s.name != skill.name
        )
        base += (
            f"\n\nAvailable skills in this runtime:\n{skill_list}\n\n"
            "When to trigger skills:\n"
            "- If the user asks for a FRESH briefing or wants to re-run a skill, trigger it.\n"
            "- If the user asks something that spans multiple product domains, trigger cross-agent.\n"
            "- If the user asks about results that already exist in the recent skill output, "
            "answer from that context directly. Do NOT re-trigger the skill.\n\n"
            "Include exactly [INVOKE:skill-name] in your response when triggering a skill.\n"
            "Tell the user you are kicking it off and results will appear shortly.\n"
            "Do not include [INVOKE:...] unless you are actually triggering a skill."
        )

    format_extra = _FORMAT_INSTRUCTIONS.get(skill.runtime_config.ui_type, "")
    return base + format_extra


def _build_user_prompt(skill: Skill, context: dict) -> str:
    """Build user prompt from trigger context."""
    trigger = context.get("trigger", "manual")
    parts = []

    # Include recent history for chat skills
    if skill.runtime_config.ui_type == "chat":
        from runtime.api import get_result_history

        recent = get_result_history()
        if recent:
            lines = [
                f"[{r.skill_name} @ {r.timestamp.isoformat()}] {json.dumps(r.content)}"
                for r in recent
            ]
            parts.append(f"Recent skill output from the runtime:\n" + "\n".join(lines))

    # User input (chat)
    if "input" in context:
        parts.append(f"User message: {context['input']}")

    # Event payload
    if trigger == "event":
        payload = context.get("payload", {})
        if payload:
            parts.append(f"Event payload: {json.dumps(payload)}")
        parts.append("This skill was triggered by an event. Execute your instructions now.")

    # Scheduled trigger — just a nudge
    if trigger == "scheduled":
        parts.append("This skill was triggered by its schedule. Execute your instructions now.")

    # Manual trigger with no input
    if not parts:
        parts.append("This skill was triggered manually. Execute your instructions now.")

    return "\n\n".join(parts)


def _strip_code_fences(text: str) -> str:
    # Extract JSON from within code fences anywhere in the response
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Fallback: strip fences at start/end
    text = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
    text = re.sub(r"\n?```\s*$", "", text.strip())
    return text


def _repair_json(text: str) -> str:
    """Fix common LLM JSON issues: unescaped newlines inside string values."""
    # Replace literal newlines inside JSON strings with \\n.
    # Walk char-by-char tracking whether we're inside a quoted string.
    result = []
    in_string = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == '\\' and in_string:
            # Escaped char — keep both
            result.append(ch)
            if i + 1 < len(text):
                i += 1
                result.append(text[i])
            i += 1
            continue
        if ch == '"':
            in_string = not in_string
        elif ch == '\n' and in_string:
            result.append('\\n')
            i += 1
            continue
        result.append(ch)
        i += 1
    return ''.join(result)


def _parse_json_response(response: str, skill: Skill) -> dict:
    """Parse LLM JSON response with fallback to raw text."""
    cleaned = _strip_code_fences(response)
    # Try parsing as-is first
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        pass
    # Try repairing common LLM issues (unescaped newlines in strings)
    try:
        repaired = _repair_json(cleaned)
        return json.loads(repaired)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Could not parse JSON from %s response, using raw text", skill.name)
        if skill.runtime_config.ui_type in ("card", "approval"):
            return {"title": skill.name, "bullets": [response]}
        return {"message": response}


async def execute_skill(skill: Skill, context: dict) -> SkillResult:
    """Execute any skill using its SKILL.md instructions — no handler.py needed."""
    all_skills = context.get("all_skills", [])
    trigger = context.get("trigger", "manual")
    trace_id = context.get("trace_id", uuid.uuid4().hex[:8])

    trace.info("[%s] ▶ SKILL %s (trigger=%s, ui=%s)",
               trace_id, skill.name, trigger, skill.runtime_config.ui_type)

    # Emit activity for skill start
    from runtime.api import broadcast_activity
    await broadcast_activity(f"Skill executing: {skill.name} ({trigger})", "skill")

    # Ensure agent is initialized (loads MCP tools on first call)
    await agent.ensure_initialized()

    # Non-chat skills need MCP tools to query domain agents.
    # Without tools the LLM would hallucinate data — fail loudly instead.
    if skill.runtime_config.ui_type != "chat" and not agent.has_tools():
        logger.error("Skill %s requires MCP tools but none are available", skill.name)
        return SkillResult(
            skill_name=skill.name,
            ui_type=skill.runtime_config.ui_type,
            content={
                "title": f"{skill.name} — no tools available",
                "bullets": [
                    "This skill requires tools but none are loaded.",
                    "Check that Agent Gateway is running and MCP servers / A2A agents are available.",
                    "See: infra/agent-gateway/docker-compose.yml",
                ],
            },
            timestamp=datetime.now(timezone.utc),
        )

    system_prompt = _build_system_prompt(skill, all_skills if all_skills else None)
    user_prompt = _build_user_prompt(skill, context)

    response = await agent.invoke(user_prompt, system_prompt=system_prompt)

    trace.info("[%s]   ← %s responded (%d chars)", trace_id, skill.name, len(response))

    ui_type = skill.runtime_config.ui_type
    now = datetime.now(timezone.utc)

    # Chat: handle [INVOKE:...] markers
    if ui_type == "chat":
        event_bus = context.get("event_bus")
        all_skills_map = {s.name: s for s in all_skills} if all_skills else {}

        invoked = set()
        for match in _INVOKE_PATTERN.finditer(response):
            skill_name = match.group(1)
            if skill_name in invoked:
                continue
            invoked.add(skill_name)
            target = all_skills_map.get(skill_name)
            if target:
                trace.info("[%s]   ⤷ INVOKE %s (from chat)", trace_id, skill_name)

                async def _run_skill(s=target):
                    from runtime.api import broadcast_result
                    ctx = {"skill": s, "trigger": "chat", "event_bus": event_bus,
                           "all_skills": all_skills, "trace_id": trace_id}
                    result = await execute_skill(s, ctx)
                    if result:
                        await broadcast_result(result)
                asyncio.create_task(_run_skill())

        response = _INVOKE_PATTERN.sub("", response).strip()
        content = {"message": response}

    # Card/approval: parse JSON
    elif ui_type in ("card", "approval"):
        # Check for [EMIT:...] markers in raw response before parsing JSON
        # Deduplicate — only emit each event name once per skill execution
        event_bus = context.get("event_bus")
        emitted = set()
        for emit_match in _EMIT_PATTERN.finditer(response):
            event_name = emit_match.group(1)
            if event_name in emitted:
                continue
            emitted.add(event_name)
            if event_bus:
                trace.info("[%s]   ⤷ EMIT %s (from %s)", trace_id, event_name, skill.name)
                asyncio.create_task(event_bus.emit(event_name,
                    {"source_skill": skill.name, "trace_id": trace_id}))

        # Strip emit markers before parsing JSON
        clean_response = _EMIT_PATTERN.sub("", response).strip()

        parsed = _parse_json_response(clean_response, skill)
        parsed.setdefault("timestamp", now.isoformat())

        # A2UI: skill decides its own UI type based on findings
        action_recommended = parsed.get("action_recommended", False)
        if action_recommended:
            ui_type = "approval"
            parsed.setdefault("action_payload", {
                "type": f"{skill.name}_action",
                "description": parsed.get("action", ""),
                "target_agent": parsed.get("target_agent", ""),
            })
        elif ui_type == "approval" and not action_recommended:
            ui_type = "card"

        content = parsed

    else:
        content = {"message": response}

    trace.info("[%s] ✓ SKILL %s complete → %s", trace_id, skill.name, ui_type)
    await broadcast_activity(f"Skill complete: {skill.name} → {ui_type}", "skill")

    # Provenance: how/why was this skill triggered?
    trigger_source = None
    if trigger == "event":
        trigger_source = context.get("payload", {}).get("event_name") or skill.runtime_config.trigger_config
    elif trigger == "chat":
        trigger_source = "chat skill"

    return SkillResult(
        skill_name=skill.name,
        ui_type=ui_type,
        content=content,
        timestamp=now,
        trigger_type=trigger,
        trigger_source=trigger_source,
    )
