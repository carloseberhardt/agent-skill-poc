# POC: Chat Is a Skill

## The Baseball Problem

Most enterprise agents today work the same way: a human sends a message, the agent responds. The game doesn't continue until a person throws the ball. This is the wrong mental model for what an agent should be.

## Chat Is a Skill

Conversational interaction is one way to work with an agent. It is not the agent. An agent should have a persistent runtime with goals, schedules, and event listeners. It should be *doing* things: compiling briefings, monitoring for anomalies, coordinating across domains. People can set the goals and schedules, but they shouldn't need to be the trigger for the activity. Chat is just the most human-legible way to interact with that runtime. It's a skill the runtime can execute, not the product itself.

## What This Demos

This POC runs skills on a single runtime to make the argument tangible. Skills are **code-free** — each is just a `SKILL.md` (instructions) + `runtime.config.json` (trigger/UI config). The agent uses two protocols through [Agent Gateway](https://agentgateway.dev): **A2A** for domain agents that think, **MCP** for tools that do.

- **Chat** — A conversational skill. Indistinguishable from a normal chat agent, except it's just one skill. Click "Run" to activate it. Delete the `skills/chat/` directory and the runtime keeps running. Chat was never the product.
- **Morning Briefing** — A scheduled skill that fires on weekday mornings, queries a domain agent via **A2A** and cost data via **MCP**, synthesizes a summary, and delivers it as a card. Nobody typed anything. The agent acted on its own.
- **Cross-Agent Insight** — An event-driven skill that queries two A2A agents (data platform + security) and an MCP tool (employee lookup), synthesizes a cross-domain finding, and dynamically decides whether to show an informational card or an approval card. The skill chose its own UI.
- **Cost Anomaly** — Uses the MCP cost API tool to detect anomalies. Just `SKILL.md` + `runtime.config.json`. No code.
- **Security Monitor** — A scheduled skill that queries the security agent via A2A. If it finds critical events, it emits a `cross_domain_query` event — triggering the cross-agent skill automatically. Nobody clicked anything.

### Key Architectural Points

**Skills are handler-free.** No `handler.py` needed. Each skill's `SKILL.md` body contains natural language instructions. The runtime's generic executor reads those instructions, adds output format guidance based on `ui_type`, and lets the LangGraph agent (with A2A + MCP tools) fulfill them. Non-chat skills fail explicitly if tools aren't available — no silent hallucination.

**Dual protocol: A2A + MCP.** Domain agents (data platform, security) speak A2A via `a2a-sdk`. Pure tools (cost API, employee lookup) speak MCP via `FastMCP`. Agent Gateway routes both. The LangGraph agent sees one unified tool list — the skill doesn't know or care which protocol a tool uses.

**Adding a skill is a "wow" moment.** Create a folder, add two files, click "Reload Skills" in the UI. The new skill appears with a "Run" button. No code, no restart, no new agent registration (skills reuse existing tools).

**Skills follow an open standard.** Each skill has a `SKILL.md` with [Agent Skills spec](https://agentskills.io/specification) frontmatter — the same format used by Claude Code, NanoClaw, and the broader ecosystem. Runtime-specific config (`trigger`, `ui_type`) lives in a separate `runtime.config.json`. A skill written for any spec-compliant tool works here without modification.

**The runtime has memory.** When skills produce results, the runtime stores them. The chat skill can answer questions about what other skills have already produced without re-running them.

**Skills control their own UI.** A skill declares what UI surface it needs — chat, card, approval, form — or decides dynamically at runtime based on what it finds. This is A2UI (agent-driven UI).

**The model is an environment variable.** The LLM is accessed through a LiteLLM proxy. Switching from Anthropic Claude to IBM Granite to any other model is a one-line `.env` change.

## How to Run It

### Prerequisites

1. Python 3.11+ and [uv](https://docs.astral.sh/uv/).
2. Docker and Docker Compose (for infrastructure).

### Infrastructure Setup

```bash
# 1. Start LiteLLM proxy
cd infra/litellm
cp .env.example .env          # set your master key
docker compose up -d

# 2. Start Agent Gateway (A2A + MCP gateway)
cd infra/agent-gateway
docker compose up -d
# Admin UI at http://localhost:15000
```

### Application Setup

```bash
cp .env.example .env
# Edit .env with your LiteLLM virtual key and model name
```

### Start

```bash
# Terminal 1 — MCP: cost API (port 5003)
uv run python mock-agents/cost_api.py

# Terminal 2 — MCP: employee lookup (port 5004)
uv run python mock-agents/employee_lookup.py

# Terminal 3 — A2A: data platform agent (port 5001)
uv run python mock-agents/data_agent.py

# Terminal 4 — A2A: security agent (port 5002)
uv run python mock-agents/security_agent.py

# Terminal 5 — runtime (port 8000)
uv run python -m runtime.main
```

Open http://localhost:8000. The runtime is running. No chat input is visible — because chat hasn't been activated yet.

### Demo Walkthrough

1. **Morning briefing.** Click "Run" next to `morning-briefing`. Agent queries data agent via **A2A** through Agent Gateway. Card appears with a synthesized summary. In production, this fires at 8am automatically.
2. **Cost anomaly.** Click "Run" next to `cost-anomaly`. Agent queries cost API via **MCP** through Agent Gateway. Card shows Spark cost spike (up 300%).
3. **Cross-agent insight.** Fire `cross_domain_query` event from the right pane. Agent queries both A2A agents + employee lookup MCP tool. "Who is jdoe? They're in Finance. Why are they accessing sensitive tables at 2am?"
4. **Chat.** Click "Run" next to `chat`. Ask: "Tell me about jdoe's recent activity." Agent uses A2A + MCP tools to build a picture.
5. **The event chain.** Set `DEMO_MODE=true`. Security monitor fires automatically, detects critical alert, emits event, cross-agent skill fires on its own. Nobody typed anything.
6. **The wow moment.** Create a new skill folder, two files, click Reload. It works.
7. **The architecture slide.** "A2A for agents. MCP for tools. Agent Gateway for security and observability. The skill just says what it wants in English."

Set `DEMO_MODE=true` in `.env` to override scheduled skills to fire every minute — useful for live demos.

## What This Is Not

This is not production code. It is not a framework. It is a working demonstration of an architectural direction — buildable, runnable, and explainable in a 20-minute conversation.

## The Enterprise Gap

This POC intentionally ignores the hard problems that make enterprise software enterprise software: multi-tenancy, RBAC, audit trails, skill permissioning, cost attribution, durable event streaming, secrets management, approval workflow integration. Those are the real engineering problems to solve; and they're solvable, because the runtime architecture gives them a natural home.
