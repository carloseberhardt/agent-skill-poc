"""
Thin wrapper around LangGraph's create_react_agent using ChatOpenAI
pointed at the LiteLLM proxy, with dual tool loading:
  - MCP tools from Agent Gateway (cost API, employee lookup)
  - A2A agents wrapped as LangChain tools (data agent, security agent)

The model is an environment concern, not an application concern.
Switching from anthropic/claude-sonnet to watsonx/granite requires
changing one env var — no code changes.

Skills call agent.invoke() — they never import LangChain or LiteLLM directly.
"""

import asyncio
import json
import logging
import os
from typing import Any

import httpx
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

_llm = ChatOpenAI(model=_model, base_url=_base_url, api_key=_api_key)

# A2A agent names — used to distinguish agent calls from tool calls in activity feed
_a2a_tool_names: set[str] = set()


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


def _is_text_part(part: dict) -> bool:
    """Check if an A2A part is a text part — handles both 'type' and 'kind' keys."""
    return part.get("type") == "text" or part.get("kind") == "text"


def _extract_a2a_text(response: dict) -> str:
    """Extract text content from an A2A JSON-RPC response."""
    # A2A message/send returns {result: {type: "task", ...}} with artifacts
    result = response.get("result", response)
    texts = []

    # Check for artifacts (list of parts)
    for artifact in result.get("artifacts", []):
        for part in artifact.get("parts", []):
            if _is_text_part(part):
                texts.append(part["text"])

    if texts:
        return "\n".join(texts)

    # Check for direct parts on result (kind: "message" format from a2a-sdk)
    for part in result.get("parts", []):
        if _is_text_part(part):
            texts.append(part["text"])

    if texts:
        return "\n".join(texts)

    # Check status message
    status = result.get("status", {})
    message = status.get("message", {})
    if isinstance(message, dict):
        for part in message.get("parts", []):
            if _is_text_part(part):
                texts.append(part["text"])
    elif isinstance(message, str):
        texts.append(message)

    if texts:
        return "\n".join(texts)

    return json.dumps(result)


async def _call_a2a_agent(agent_url: str, query: str) -> str:
    """Send an A2A message/send request and return the text response."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"type": "text", "text": query}],
                "messageId": "tool-call-1",
            }
        },
    }
    wire.info("A2A ▶ POST %s", agent_url)
    wire.info("A2A ▶ %s", query[:200])
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(agent_url, json=payload)
        resp.raise_for_status()
        text = _extract_a2a_text(resp.json())
        wire.info("A2A ◀ %s → %s", agent_url.split("/")[-1], text[:200])
        return text


async def _init_a2a_tools() -> list:
    """Discover A2A agents via Agent Gateway and wrap each skill as a LangChain tool."""
    if not _a2a_agents:
        logger.info("No A2A agents configured")
        return []

    tools = []
    for agent_name in _a2a_agents:
        card_url = f"{_gateway_url}/{agent_name}/.well-known/agent.json"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(card_url)
                resp.raise_for_status()
                card = resp.json()
        except Exception:
            logger.warning("Could not fetch agent card from %s — skipping", card_url)
            continue

        agent_desc = card.get("description", agent_name)
        # Use the URL from the agent card — Agent Gateway rewrites it to point through itself
        agent_url = card.get("url", f"{_gateway_url}/{agent_name}")

        for skill in card.get("skills", []):
            skill_id = skill.get("id", agent_name)
            skill_name = f"{agent_name}_{skill_id}"
            skill_desc = skill.get("description", agent_desc)

            # Capture for closure
            _url = agent_url

            async def _run(query: str, url: str = _url) -> str:
                return await _call_a2a_agent(url, query)

            tool = StructuredTool.from_function(
                coroutine=_run,
                name=skill_name,
                description=skill_desc,
            )
            tools.append(tool)
            logger.info("Registered A2A tool: %s → %s", skill_name, agent_url)

    logger.info("Loaded %d A2A tool(s)", len(tools))
    return tools


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
    mcp_tools = await _init_mcp_tools()
    a2a_tools = await _init_a2a_tools()
    _a2a_tool_names.update(t.name for t in a2a_tools)
    _tools = mcp_tools + a2a_tools
    _agent = create_react_agent(_llm, tools=_tools)
    _initialized = True
    logger.info("Agent tools refreshed — %d MCP + %d A2A = %d total", len(mcp_tools), len(a2a_tools), len(_tools))


async def cleanup() -> None:
    """Shutdown hook — placeholder for client cleanup if needed."""
    global _initialized
    _initialized = False
