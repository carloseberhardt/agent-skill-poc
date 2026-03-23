"""
Thin wrapper around LangGraph's create_react_agent using ChatOpenAI
pointed at the LiteLLM proxy, with dual tool loading:
  - MCP tools from Agent Gateway (cost API, employee lookup)
  - A2A agents wrapped as LangChain tools (data agent, security agent)

A2A communication uses the a2a-sdk Client for spec-compliant message
sending, agent card resolution, and push notification support. Agents
that declare push_notifications=True in their agent card get non-blocking
calls with a callback URL — the SDK handles the wire protocol.

The model is an environment concern, not an application concern.
Switching from anthropic/claude-sonnet to watsonx/granite requires
changing one env var — no code changes.

Skills call agent.invoke() — they never import LangChain or LiteLLM directly.
"""

import asyncio
import json
import logging
import os
import time
import warnings
from typing import Any

# langgraph v1.0 moved create_react_agent to langchain.agents, but we don't
# depend on the full langchain package — suppress until we upgrade.
warnings.filterwarnings("ignore", message="create_react_agent has been moved")

import httpx
from a2a.client import A2ACardResolver, ClientFactory, ClientConfig, Client, create_text_message_object
from a2a.types import (
    Message as A2AMessage,
    PushNotificationConfig,
    Task as A2ATask,
    TaskState,
)
from a2a.utils.message import get_message_text
from a2a.utils.parts import get_text_parts
from dotenv import load_dotenv
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

load_dotenv()

logger = logging.getLogger("solis.agent")
wire = logging.getLogger("solis.wire")

_base_url = os.getenv("LITELLM_BASE_URL", "http://localhost:4000")
_model = os.getenv("LITELLM_MODEL", "claude-sonnet-team-b")
_api_key = os.getenv("LITELLM_API_KEY", "")

# Agent Gateway endpoints
_gateway_url = os.getenv("AGENT_GATEWAY_URL", "http://localhost:3000")
_gateway_mcp_url = os.getenv("AGENT_GATEWAY_MCP_URL", "http://localhost:3000/mcp")
_a2a_agents = [a.strip() for a in os.getenv("AGENT_GATEWAY_A2A_AGENTS", "").split(",") if a.strip()]
_callback_url = os.getenv("RUNTIME_CALLBACK_URL", "http://localhost:8000/a2a-callback")

_llm = ChatOpenAI(model=_model, base_url=_base_url, api_key=_api_key)

# A2A agent names — used to distinguish agent calls from tool calls in activity feed
_a2a_tool_names: set[str] = set()

# A2A SDK clients — one per agent, reused across calls
_a2a_clients: dict[str, Client] = {}

# Pending async A2A tasks — task_id → context for correlating push notifications
_pending_tasks: dict[str, dict] = {}


class _ActivityCallbackHandler(AsyncCallbackHandler):
    """Emits activity feed items when tools are called, with request/response detail."""

    def __init__(self, skill_name: str = ""):
        super().__init__()
        self.skill_name = skill_name
        # Map run_id → (tool_name, input_args) so on_tool_end can pair them
        self._pending: dict[str, tuple[str, str]] = {}

    async def on_tool_start(self, serialized: dict[str, Any], input_str: str, **kwargs: Any) -> None:
        from runtime.api import broadcast_activity
        name = serialized.get("name", kwargs.get("name", "unknown"))
        prefix = f"[{self.skill_name}] " if self.skill_name else ""
        category = "agent" if name in _a2a_tool_names else "tool"
        label = "Agent call" if category == "agent" else "Tool call"

        # Use clean tool inputs dict if available, fall back to input_str
        inputs = kwargs.get("inputs")
        if isinstance(inputs, dict):
            clean_input = json.dumps(inputs)
        else:
            clean_input = str(inputs or input_str)[:4000]

        # Store input for pairing with result
        run_id = str(kwargs.get("run_id", ""))
        self._pending[run_id] = (name, clean_input)

        await broadcast_activity(f"{prefix}{label}: {name}", category,
                                 detail={"type": "request", "tool": name, "input": clean_input})

    async def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        from runtime.api import broadcast_activity

        # Try to get tool name from the output object or pending map
        run_id = str(kwargs.get("run_id", ""))
        tool_name = getattr(output, "name", None) or ""
        input_args = ""

        # Try matching by run_id first
        if run_id in self._pending:
            tool_name, input_args = self._pending.pop(run_id)
        elif tool_name:
            # Match by tool name if run_id didn't work
            for rid, (pname, pinput) in list(self._pending.items()):
                if pname == tool_name:
                    input_args = pinput
                    del self._pending[rid]
                    break

        if not tool_name:
            tool_name = "unknown"

        prefix = f"[{self.skill_name}] " if self.skill_name else ""
        category = "agent" if tool_name in _a2a_tool_names else "tool"
        label = "Agent result" if category == "agent" else "Tool result"

        # Extract clean text content from the output
        if hasattr(output, "content"):
            content = output.content
            if isinstance(content, list):
                # Extract text parts from content blocks
                texts = [p.get("text", "") if isinstance(p, dict) else str(p) for p in content]
                output_str = "\n".join(t for t in texts if t)
            else:
                output_str = str(content)
        else:
            output_str = str(output)

        await broadcast_activity(f"{prefix}{label}: {tool_name}", category,
                                 detail={"type": "response", "tool": tool_name,
                                         "input": input_args, "output": output_str[:8000]})

_TERMINAL_STATES = {TaskState.completed, TaskState.canceled, TaskState.failed, TaskState.rejected}

# Tools and agent are initialized lazily on first invoke.
_tools: list = []
_agent = None
_initialized = False


async def _init_mcp_tools() -> list:
    """Connect to Agent Gateway MCP endpoint and load tools. Returns empty list on failure."""
    if not _gateway_mcp_url:
        logger.warning("No AGENT_GATEWAY_MCP_URL set — no MCP tools")
        return []

    logger.info("Connecting to Agent Gateway MCP at %s", _gateway_mcp_url)

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        client = MultiServerMCPClient({
            "agent-gateway": {
                "transport": "streamable_http",
                "url": _gateway_mcp_url,
            }
        })
        tools = await client.get_tools()
        if not tools:
            logger.warning("Agent Gateway returned 0 MCP tools — are MCP servers running?")
        else:
            logger.info("Loaded %d MCP tool(s) from Agent Gateway", len(tools))
        return tools
    except Exception:
        logger.error("Could not connect to Agent Gateway MCP at %s", _gateway_mcp_url, exc_info=True)
        return []


def _extract_task_text(task: A2ATask) -> str:
    """Extract text from an A2A Task — checks artifacts first, then status message."""
    texts = []
    for artifact in (task.artifacts or []):
        texts.extend(get_text_parts(artifact.parts))
    if texts:
        return "\n".join(texts)

    # Fall back to status message
    if task.status.message:
        return get_message_text(task.status.message)

    return f"Task {task.id}: {task.status.state.value}"


async def _call_a2a_agent(client: Client, agent_name: str, query: str, push_capable: bool) -> str:
    """Send an A2A message via SDK client. Returns text for the LLM.

    For push-capable agents, sends non-blocking — the SDK injects
    push_notification_config from the ClientConfig automatically.
    """
    wire.info("A2A ▶ %s: %s", agent_name, query[:200])
    message = create_text_message_object(content=query)

    async for event in client.send_message(message):
        # Direct message response (no task created)
        if isinstance(event, A2AMessage):
            text = get_message_text(event)
            wire.info("A2A ◀ %s → message: %s", agent_name, text[:200])
            return text

        # ClientEvent = tuple[Task, UpdateEvent | None]
        task, _update = event
        state = task.status.state

        if state == TaskState.working:
            # Non-blocking — agent accepted and is working in background
            _pending_tasks[task.id] = {
                "agent_name": agent_name,
                "query": query,
                "timestamp": time.time(),
            }
            status_msg = get_message_text(task.status.message) if task.status.message else "working"
            wire.info("A2A ◀ %s → task %s (working): %s", agent_name, task.id, status_msg[:200])
            return (
                f"Task accepted (ID: {task.id}, state: working). "
                f"Agent says: {status_msg}. "
                f"The agent will send a notification when done."
            )

        if state == TaskState.input_required:
            # Agent needs more info — return the question to the LLM
            question = get_message_text(task.status.message) if task.status.message else "More information needed."
            # Track so follow-up messages can reference this task
            _pending_tasks[task.id] = {
                "agent_name": agent_name,
                "query": query,
                "timestamp": time.time(),
                "state": "input-required",
            }
            wire.info("A2A ◀ %s → task %s (input-required): %s", agent_name, task.id, question[:200])
            return (
                f"Agent needs more information (task ID: {task.id}). "
                f"Agent says: {question}"
            )

        if state in _TERMINAL_STATES:
            # Completed/failed/canceled/rejected — extract final answer
            text = _extract_task_text(task)
            wire.info("A2A ◀ %s → task %s (%s): %s", agent_name, task.id, state.value, text[:200])
            return text

        # Submitted or unknown — just report
        wire.info("A2A ◀ %s → task %s (%s)", agent_name, task.id, state.value)
        return f"Task {task.id} is in state: {state.value}"

    return "No response from agent."


async def _init_a2a_tools() -> list:
    """Discover A2A agents via Agent Gateway and wrap each skill as a LangChain tool.

    Uses the a2a-sdk ClientFactory for spec-compliant agent card resolution
    and Client for message sending. Agents declaring push_notifications=True
    get non-blocking calls with push notification config.
    """
    if not _a2a_agents:
        logger.info("No A2A agents configured")
        return []

    tools = []
    for agent_name in _a2a_agents:
        # Resolve agent card via SDK — Agent Gateway serves cards at /{name}/.well-known/agent.json
        card_path = f"{agent_name}/.well-known/agent.json"
        try:
            push_capable = False
            async with httpx.AsyncClient(timeout=10) as http_client:
                resolver = A2ACardResolver(http_client, _gateway_url)
                card = await resolver.get_agent_card(relative_card_path=card_path)

            # Check if agent supports push notifications
            if card.capabilities and card.capabilities.push_notifications:
                push_capable = True
                logger.info("Agent %s supports push notifications", agent_name)

            # Build client config — push-capable agents get non-blocking + callback URL.
            # Explicit timeout: A2A agents may call LLMs that take 20s+.
            client_config = ClientConfig(
                streaming=False,
                polling=push_capable,  # polling=True → blocking=False in the SDK
                httpx_client=httpx.AsyncClient(timeout=60),
                push_notification_configs=(
                    [PushNotificationConfig(url=_callback_url)]
                    if push_capable else []
                ),
            )

            # Create SDK client from the resolved card
            client = ClientFactory(client_config).create(card)
            _a2a_clients[agent_name] = client

        except Exception:
            logger.warning("Could not connect to A2A agent %s — skipping", agent_name, exc_info=True)
            continue

        for skill in card.skills:
            skill_name = f"{agent_name}_{skill.id}"
            skill_desc = skill.description or card.description or agent_name

            # Build closure — capture values without exposing them as tool parameters.
            # Using default args for Client would leak it into the JSON schema.
            def _make_runner(_c=client, _n=agent_name, _p=push_capable):
                async def _run(query: str) -> str:
                    return await _call_a2a_agent(_c, _n, query, push_capable=_p)
                return _run

            tool = StructuredTool.from_function(
                coroutine=_make_runner(),
                name=skill_name,
                description=skill_desc,
            )
            tools.append(tool)
            logger.info("Registered A2A tool: %s (push=%s)", skill_name, push_capable)

    logger.info("Loaded %d A2A tool(s)", len(tools))
    return tools


# ── Pending task accessors (used by /a2a-callback) ──────────────

def get_pending_task(task_id: str) -> dict | None:
    """Look up a pending async task by ID. Returns None if not found."""
    return _pending_tasks.get(task_id)


def resolve_pending_task(task_id: str) -> dict | None:
    """Look up and remove a pending async task. Returns the context or None."""
    return _pending_tasks.pop(task_id, None)


def has_tools() -> bool:
    """Check whether the agent has tools available (MCP or A2A)."""
    return bool(_tools)


async def ensure_initialized() -> None:
    global _tools, _agent, _initialized
    if _initialized:
        return
    mcp_tools = await _init_mcp_tools()
    a2a_tools = await _init_a2a_tools()
    _a2a_tool_names.update(t.name for t in a2a_tools)
    _tools = mcp_tools + a2a_tools
    _agent = create_react_agent(_llm, tools=_tools)
    _initialized = True
    logger.info("Agent initialized: %d MCP tools, %d A2A tools", len(mcp_tools), len(a2a_tools))


_MAX_RETRIES = 2


async def invoke(prompt: str, system_prompt: str = "", skill_name: str = "") -> str:
    await ensure_initialized()
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    wire.info("LLM ▶ %s → %s", _model, prompt[:150])
    callback = _ActivityCallbackHandler(skill_name=skill_name)

    last_err = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            result = await _agent.ainvoke({"messages": messages}, config={"callbacks": [callback]})
            break
        except Exception as e:
            last_err = e
            if attempt < _MAX_RETRIES and "BadRequestError" in type(e).__name__:
                logger.warning("Agent call failed (attempt %d/%d), retrying: %s",
                               attempt + 1, _MAX_RETRIES + 1, str(e)[:200])
                from runtime.api import broadcast_activity
                await broadcast_activity(
                    f"{'[' + skill_name + '] ' if skill_name else ''}Retrying after model error (attempt {attempt + 2})",
                    "info",
                )
                continue
            raise
    else:
        raise last_err

    response = result["messages"][-1].content
    # Log tool calls from intermediate messages
    for msg in result["messages"]:
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                args_str = json.dumps(tc.get("args", {}))[:120]
                wire.info("LLM ⤷ tool_call %s(%s)", tc["name"], args_str)
        if hasattr(msg, "name") and msg.type == "tool":
            wire.info("LLM ⤶ tool_result %s → %s", msg.name, str(msg.content)[:150])
    wire.info("LLM ◀ %s (%d chars)", _model, len(response))
    return response


async def refresh_tools() -> None:
    """Re-fetch tools from MCP + A2A. Useful after hot-reloading skills."""
    global _tools, _agent, _initialized
    _initialized = False
    _a2a_tool_names.clear()
    # Close existing A2A clients before re-creating
    for client in _a2a_clients.values():
        try:
            await client.close()
        except Exception:
            pass
    _a2a_clients.clear()
    mcp_tools = await _init_mcp_tools()
    a2a_tools = await _init_a2a_tools()
    _a2a_tool_names.update(t.name for t in a2a_tools)
    _tools = mcp_tools + a2a_tools
    _agent = create_react_agent(_llm, tools=_tools)
    _initialized = True
    logger.info("Agent tools refreshed — %d MCP + %d A2A = %d total", len(mcp_tools), len(a2a_tools), len(_tools))


async def cleanup() -> None:
    """Shutdown hook — close A2A clients."""
    global _initialized
    _initialized = False
    for client in _a2a_clients.values():
        try:
            await client.close()
        except Exception:
            pass
    _a2a_clients.clear()
