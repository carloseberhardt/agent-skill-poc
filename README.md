# POC: Chat Is a Skill

## The Baseball Problem

Most enterprise agents today work the same way: a human sends a message, the agent responds. The game doesn't continue until a person throws the ball. This is the wrong mental model for what an agent should be.

## Chat Is a Skill

Conversational interaction is one way to work with an agent. It is not the agent. An agent should have a persistent runtime with goals, schedules, and event listeners. It should be *doing* things: compiling briefings, monitoring for anomalies, coordinating across domains. People can set the goals and schedules, but they shouldn't need to be the trigger for the activity. Chat is just the most human-legible way to interact with that runtime. It's a skill the runtime can execute, not the product itself.

## What This Demos

This POC runs skills on a single runtime to make the argument tangible. Skills are **code-free** — each is just a `SKILL.md` (instructions) + `runtime.config.json` (trigger/UI config). The agent uses two protocols through [Agent Gateway](https://agentgateway.dev): **A2A** for domain agents that think, **MCP** for tools that do.

Two domain agents, three tools, three interaction patterns:

- **A2A agents** — Security and Data Platform agents, each running their own LLM model. They reason about their domain, not just return data. The architecture is protocol-native, not model-native.
- **MCP tools** — Cost API, Employee Lookup, and Discord Notifier. Stateless data retrieval and actions. No LLM needed.
- **A2A push notifications** — Agents can push events back to the runtime via callbacks, triggering event-driven skills without human input.

All backed by a shared SQLite database with scenario data. Each run of `seed_db.py` randomly activates a different combination of scenario threads — a data exfiltration, a slow-burn budget creep, or a quiet day where nothing is wrong. The agents discover whatever story is in the data. Re-seed mid-demo to show the same skills producing different results from different data.

The UI includes a real-time **activity feed** showing events, skill triggers, and tool calls as they happen — making the runtime's cause-and-effect chain visible.

### Skills

- **Chat** — A conversational skill. Routes questions to agents and tools. Delete the `skills/chat/` directory and the runtime keeps running. Chat was never the product.
- **Security Escalation** — Event-driven (`security_alert`). Investigates across security + data + employee records and recommends action.
- **Data Access Review** — Event-driven (`data_anomaly`). Reviews data access anomalies — unusual query patterns, after-hours PII access, bulk extractions.
- **Incident Correlation** — Event-driven (`incident_correlation`). Correlates signals across security and data agents when patterns suggest a connected incident.

### Key Architectural Points

**Skills are handler-free.** No `handler.py` needed. Each skill's `SKILL.md` body contains natural language instructions. The runtime's generic executor reads those instructions, adds output format guidance based on `ui_type`, and lets the LangGraph agent (with A2A + MCP tools) fulfill them. Non-chat skills fail explicitly if tools aren't available — no silent hallucination.

**Dual protocol: A2A + MCP.** Domain agents speak A2A via `a2a-sdk`. Pure tools speak MCP via `FastMCP`. Agent Gateway routes both. The LangGraph agent sees one unified tool list — the skill doesn't know or care which protocol a tool uses.

**Each agent brings its own model.** The security agent might run GPT-4o, the data agent Granite. The runtime doesn't dictate how agents are built — only that they support A2A. This is the protocol-native story.

**Adding a skill is a "wow" moment.** Create a folder, add two files, click "Reload Skills" in the UI. The new skill appears with a "Run" button. No code, no restart, no new agent registration (skills reuse existing tools).

**Skills follow an open standard.** Each skill has a `SKILL.md` with [Agent Skills spec](https://agentskills.io/specification) frontmatter — the same format used by Claude Code, NanoClaw, and the broader ecosystem. Runtime-specific config (`trigger`, `ui_type`) lives in a separate `runtime.config.json`. A skill written for any spec-compliant tool works here without modification.

**The runtime has memory.** When skills produce results, the runtime stores them. The chat skill can answer questions about what other skills have already produced without re-running them.

**Skills control their own UI.** A skill declares what UI surface it needs — chat, card, approval, form — or decides dynamically at runtime based on what it finds. This is A2UI (agent-driven UI).

**The model is an environment variable.** The runtime's LLM is accessed through a LiteLLM proxy. Switching models is a one-line `.env` change. Each agent has its own model config.

**Actions have consequences.** When you approve an action (restrict a user's access, notify security), the agent executes it through the appropriate agents and tools. The next time any skill runs, the world has changed.

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
# Edit .env with your LiteLLM virtual key, model names, and optional Discord webhook URL

# Seed the database — re-run anytime for a different scenario
uv run python seed_db.py           # random thread selection
uv run python seed_db.py --all     # all threads active (worst day)
uv run python seed_db.py --quiet   # only normal baseline (boring day)
```

### Start

```bash
# Terminal 1 — MCP: cost API (port 5003)
uv run python mock-agents/cost_api.py

# Terminal 2 — MCP: employee lookup (port 5004)
uv run python mock-agents/employee_lookup.py

# Terminal 3 — MCP: Discord notifier (port 5005)
uv run python mock-agents/discord_notifier.py

# Terminal 4 — A2A: data platform agent (port 5001)
uv run python mock-agents/data_agent.py

# Terminal 5 — A2A: security agent (port 5002)
uv run python mock-agents/security_agent.py

# Terminal 6 — runtime (port 8000)
uv run python -m runtime.main
```

Open http://localhost:8000. The runtime is running with the activity feed visible on the right. No chat input is visible — because chat hasn't been activated yet.

### Demo Walkthrough

1. **Chat.** Click "Run" next to `chat`. Ask "Who is on call right now?" — watch the activity feed show the tool calls in real-time.
2. **Security escalation.** Fire `security_alert` event from the right pane. Watch the activity feed: event received → skill triggered → A2A agent call → MCP tool call → result. The approval card shows "Triggered by: event security_alert".
3. **Data access review.** Fire `data_anomaly` event. A card appears showing the data agent's findings about anomalous access patterns.
4. **Incident correlation.** Fire `incident_correlation` event. Cross-domain analysis across security and data agents, with employee and cost lookups.
5. **Approve an action.** Click "Approve" on an approval card. The agent executes the action and posts to Discord. Activity feed shows the entire flow.
6. **View a skill.** Click the `{}` button next to any skill to see its SKILL.md — "this is all a skill is, a markdown file."
7. **Re-seed and re-run.** Run `uv run python seed_db.py` in the terminal. Different scenario, different findings, same skills.
8. **The wow moment.** Create a new skill folder, two files, click Reload. It works.
9. **The architecture slide.** "Two agents, two models, two protocols. A2A for agents. MCP for tools. Agent Gateway for routing. The skill just says what it wants in English."

Re-seed the database between demo runs (`uv run python seed_db.py`) to get a different combination of active scenario threads. The `--all` flag activates everything; `--quiet` gives a boring day with no anomalies.

## What This Is Not

This is not production code. It is not a framework. It is a working demonstration of an architectural direction — buildable, runnable, and explainable in a 20-minute conversation.

## The Enterprise Gap

This POC intentionally ignores the hard problems that make enterprise software enterprise software: multi-tenancy, RBAC, audit trails, skill permissioning, cost attribution, durable event streaming, secrets management, approval workflow integration. Those are the real engineering problems to solve; and they're solvable, because the runtime architecture gives them a natural home.
