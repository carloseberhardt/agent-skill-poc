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

import json
import logging
import os

import httpx
from dotenv import load_dotenv
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

load_dotenv()

logger = logging.getLogger("solis.agent")

_base_url = os.getenv("LITELLM_BASE_URL", "http://localhost:4000")
_model = os.getenv("LITELLM_MODEL", "claude-sonnet-team-b")
_api_key = os.getenv("LITELLM_API_KEY", "")

# Agent Gateway endpoints
_gateway_url = os.getenv("AGENT_GATEWAY_URL", "http://localhost:3000")
_gateway_mcp_url = os.getenv("AGENT_GATEWAY_MCP_URL", "http://localhost:3000/mcp")
_a2a_agents = [a.strip() for a in os.getenv("AGENT_GATEWAY_A2A_AGENTS", "").split(",") if a.strip()]

_llm = ChatOpenAI(model=_model, base_url=_base_url, api_key=_api_key)

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


def _extract_a2a_text(response: dict) -> str:
    """Extract text content from an A2A JSON-RPC response."""
    # A2A message/send returns {result: {type: "task", ...}} with artifacts
    result = response.get("result", response)

    # Check for artifacts (list of parts)
    artifacts = result.get("artifacts", [])
    texts = []
    for artifact in artifacts:
        for part in artifact.get("parts", []):
            if part.get("type") == "text":
                texts.append(part["text"])

    if texts:
        return "\n".join(texts)

    # Check status message
    status = result.get("status", {})
    message = status.get("message", {})
    if isinstance(message, dict):
        for part in message.get("parts", []):
            if part.get("type") == "text":
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
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(agent_url, json=payload)
        resp.raise_for_status()
        return _extract_a2a_text(resp.json())


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
    _tools = mcp_tools + a2a_tools
    _agent = create_react_agent(_llm, tools=_tools)
    _initialized = True
    logger.info("Agent initialized: %d MCP tools, %d A2A tools", len(mcp_tools), len(a2a_tools))


async def invoke(prompt: str, system_prompt: str = "") -> str:
    await ensure_initialized()
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    result = await _agent.ainvoke({"messages": messages})
    return result["messages"][-1].content


async def refresh_tools() -> None:
    """Re-fetch tools from MCP + A2A. Useful after hot-reloading skills."""
    global _tools, _agent, _initialized
    _initialized = False
    mcp_tools = await _init_mcp_tools()
    a2a_tools = await _init_a2a_tools()
    _tools = mcp_tools + a2a_tools
    _agent = create_react_agent(_llm, tools=_tools)
    _initialized = True
    logger.info("Agent tools refreshed — %d MCP + %d A2A = %d total", len(mcp_tools), len(a2a_tools), len(_tools))


async def cleanup() -> None:
    """Shutdown hook — placeholder for client cleanup if needed."""
    global _initialized
    _initialized = False
