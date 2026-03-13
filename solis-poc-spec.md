# Solis POC: Chat Is a Skill

## What This Is

A proof-of-concept that demonstrates a different architectural foundation for enterprise AI agents. The central argument:

> **Chat is not the product. The agent runtime is the product. Chat is just one skill the runtime can execute.**

This POC is not production code. It is a working demonstration designed to make an idea undeniable to an audience familiar with the current Solis architecture. It should be buildable, runnable, and explainable in a 20-minute conversation.

---

## The Problem with Chat-First

Current enterprise agent platforms — including Solis today — are chat-first. The human initiates every interaction. Nothing happens until someone sends a message. The agent is reactive by design.

This is the **pitcher problem**: the agent is a fielder. Nothing moves until the human throws the ball.

A skill-runtime inverts this. The agent has a persistent runtime with goals, listeners, and scheduled jobs. Chat is just the most human-legible way to interact with that runtime — available when you need it, but not the only thing happening.

**From:** User → Chat → Agent → Response  
**To:** Agent runtime (always running) ← User interacts via whichever skill fits the moment

---

## Architecture

### Core Concepts

**Skill** — A directory containing a `SKILL.md` (metadata + instructions) and optionally a handler. A skill declares:
- What it does
- How it is triggered (manual, scheduled, or event)
- What UI it needs, if any
- What external agents or tools it calls

**Runtime** — A lightweight persistent process that:
- Loads skills from a registry directory on startup
- Maintains a scheduler for time-based triggers
- Maintains an event bus for event-based triggers  
- Exposes an HTTP API for manual invocation and UI interaction
- Routes skill output to the appropriate interface (chat window, card, form, digest email, etc.)

**UI Contract** — When a skill needs human interaction, it requests a UI surface rather than assuming a chat window. This is where A2UI / agent-driven dynamic UI becomes load-bearing. For this POC, implement a simple version: skills emit a `ui_type` in their response (`chat`, `card`, `form`, `approval`) and the frontend renders accordingly.

---

## Skill Format

Skills follow the [Agent Skills open standard](https://agentskills.io/specification) — the same spec used by Claude Code, NanoClaw, and the broader ecosystem. This is intentional: the POC should demonstrate that Solis can be a runtime for skills written to an open standard, not a proprietary format.

### SKILL.md

The standard defines only these frontmatter fields: `name`, `description`, `license`, `compatibility`, `metadata`, and `allowed-tools`. The body is freeform Markdown instructions for the agent.

Runtime-specific concerns (trigger type, schedule, UI contract) do **not** belong in SKILL.md frontmatter — they are not part of the open spec and would break interoperability. They live in a separate `runtime.config.json` file alongside SKILL.md.

### runtime.config.json

Each skill directory may include a `runtime.config.json` that the Solis runtime reads. This file is invisible to spec-compliant tools that only read SKILL.md.

```json
{
  "trigger": "scheduled",
  "trigger_config": "0 8 * * 1-5",
  "ui_type": "card"
}
```

Valid `trigger` values: `manual`, `scheduled`, `event`  
Valid `ui_type` values: `chat`, `card`, `form`, `approval`, `none`  
`trigger_config` is a cron string for `scheduled`, an event name string for `event`, and omitted for `manual`.

---

## Project Structure

```
solis-poc/
├── CLAUDE.md                    # Instructions for Claude Code
├── README.md                    # The argument, in plain language
├── runtime/
│   ├── main.py                  # Entry point — starts runtime, loads skills
│   ├── skill_loader.py          # Reads SKILL.md + runtime.config.json, validates both
│   ├── scheduler.py             # APScheduler-based trigger execution
│   ├── event_bus.py             # Simple asyncio-based event pub/sub
│   ├── api.py                   # HTTP API (FastAPI — lightweight, async-native)
│   └── agent.py                 # LangGraph/LiteLLM wrapper — model is an env var
├── skills/
│   ├── chat/
│   │   ├── SKILL.md
│   │   ├── runtime.config.json
│   │   └── handler.py
│   ├── morning-briefing/
│   │   ├── SKILL.md
│   │   ├── runtime.config.json
│   │   └── handler.py
│   └── cross-agent/
│       ├── SKILL.md
│       ├── runtime.config.json
│       └── handler.py
├── mock-agents/
│   └── domain_agent.py          # Simulates a product team's domain agent
├── frontend/
│   └── index.html               # Single-file UI — no framework needed
├── pyproject.toml               # Dependencies (uv or pip)
└── .env.example                 # LITELLM_BASE_URL, LITELLM_MODEL
```

---

## Implementation Spec

### Runtime (`runtime/main.py`)

Keep this under 100 lines. Responsibilities:
1. Load all skills from `./skills/` directory
2. Register scheduled skills with the scheduler
3. Subscribe event-driven skills to the event bus
4. Start the HTTP API
5. Log which skills are active and how they are triggered

No dependency injection frameworks. No microservices. One process. Use `asyncio` throughout — FastAPI, APScheduler, and the event bus all run on the same event loop.

### Skill Loader (`runtime/skill_loader.py`)

Reads each skill directory. Expects:
- `SKILL.md` with YAML frontmatter per the [Agent Skills spec](https://agentskills.io/specification): required fields are `name` and `description`. Optional: `license`, `compatibility`, `metadata`, `allowed-tools`.
- `runtime.config.json` with Solis-specific fields: `trigger` (manual | scheduled | event), optional `trigger_config` (cron string or event name), optional `ui_type` (chat | card | form | approval | none). If absent, defaults to `trigger: manual`, `ui_type: chat`.
- Optional `handler.py` containing an async `run(context: dict) -> SkillResult` function.

Returns a typed `list[Skill]` merging both sources. Use `pydantic` models for `Skill` and `SkillResult` — this gives free validation and clear contracts. Validates SKILL.md against the spec and runtime.config.json separately. Logs warnings for malformed skills, never crashes on a bad skill.

Include a comment noting the separation is intentional: a skill written for Claude Code or any spec-compliant tool works here without modification. runtime.config.json is the Solis-specific layer.

### Scheduler (`runtime/scheduler.py`)

Wraps `APScheduler` (use `AsyncIOScheduler`). For each skill with `trigger: scheduled`, register the cron expression from `trigger_config`. On fire, invoke the skill handler and route output per `ui_type`.

### Event Bus (`runtime/event_bus.py`)

Simple asyncio-based pub/sub. Maintain a `dict[str, list[Callable]]` of event name → handlers. Skills declare `trigger: event` and a `trigger_config: event_name`. External systems (or other skills) emit events via the API. Keep it in-process for the POC — note in comments where Redpanda or Kafka would replace this in production.

### API (`runtime/api.py`)

Four endpoints:

```
POST /invoke/:skillName       — Manually trigger any skill
POST /event/:eventName        — Emit an event to the bus  
GET  /skills                  — List loaded skills and their status
GET  /status                  — Runtime health, uptime, active schedules
```

Use FastAPI. It is async-native, has automatic OpenAPI docs at `/docs` (useful for the demo), and is already familiar to most Python ML/AI engineers. Mount the frontend static file from the same process.

### Agent Wrapper (`runtime/agent.py`)

Thin wrapper around LangGraph's `create_react_agent` using a `ChatOpenAI` model instance (from `langchain_openai`) pointed at the local LiteLLM proxy. Accepts a prompt, an optional system prompt, and an optional tool list. Returns a response string. Skills call this — they never import LangChain or LiteLLM directly.

This is the single choke point for all LLM calls. The model is not hardcoded — it reads from the `LITELLM_MODEL` environment variable (default: `anthropic/claude-sonnet-4-20250514`). Switching to `watsonx/ibm/granite-3-8b-instruct` or any other LiteLLM-supported model requires no code changes. This is intentional and should be called out explicitly in a comment: *the model is an environment concern, not an application concern.*

The LiteLLM proxy base URL is read from `LITELLM_BASE_URL` (default: `http://localhost:4000`).

Keep this under 40 lines. No retry logic, streaming, or token counting in the POC. Use `python-dotenv` to load `.env` so Claude Code can configure the environment without editing code.

---

## The Three Demo Skills

### 1. `skills/chat/`

**Purpose:** Demonstrate that conversational interaction is just one skill.

**SKILL.md frontmatter:**
```yaml
---
name: chat
description: Conversational interface to the agent runtime. Use when the user wants to ask questions, get help, or interact with the agent directly.
---
```

**runtime.config.json:**
```json
{ "trigger": "manual", "ui_type": "chat" }
```

**Handler behavior:**
- Accepts a user message
- Calls `agent.py` with a system prompt that describes the other available skills
- Returns a response
- The frontend renders this in a chat UI *because the skill requested it*, not because chat is the default

**The demo point:** This is indistinguishable from a normal chat agent — but it's just one skill. Delete this directory and the runtime keeps running, executing the other skills on schedule.

### 2. `skills/morning-briefing/`

**Purpose:** Demonstrate proactive, scheduled execution — no human required.

**SKILL.md frontmatter:**
```yaml
---
name: morning-briefing
description: Compiles a morning briefing from available domain agents and delivers a structured summary. Runs automatically on weekday mornings.
---
```

**runtime.config.json:**
```json
{ "trigger": "scheduled", "trigger_config": "0 8 * * 1-5", "ui_type": "card" }
```

**Handler behavior:**
- Calls `mock-agents/domain-agent.ts` with a "what's new since yesterday?" query
- Calls `agent.py` to synthesize a brief, scannable summary
- Returns a structured card payload (title, bullet points, timestamp, link)
- The frontend renders this as a card, not a chat message

**The demo point:** It's 8am Monday. Nobody typed anything. The agent already did something and has a result waiting. The pitcher didn't throw the ball.

### 3. `skills/cross-agent/`

**Purpose:** Demonstrate multi-agent orchestration as a skill, with a dynamic UI appropriate to the result.

**SKILL.md frontmatter:**
```yaml
---
name: cross-agent
description: Queries multiple domain agents and synthesizes a cross-domain insight. Use when a task requires data or actions spanning more than one product team's domain.
---
```

**runtime.config.json:**
```json
{ "trigger": "event", "trigger_config": "cross_domain_query", "ui_type": "approval" }
```

**Handler behavior:**
- Triggered by a `cross_domain_query` event (can be fired manually via the API or by the chat skill)
- Calls `mock-agents/domain-agent.ts` twice, simulating two different product team agents with different data
- Calls `agent.py` to synthesize a response and identify if any action is recommended
- If an action is recommended, returns an `approval` UI type with the proposed action and a confirm/reject payload
- If informational only, returns a `card`

**The demo point:** The skill decided what UI to generate based on what it found. The runtime didn't assume chat. A2UI is not a gimmick — it's the natural consequence of skills that know what they need.

---

## Mock Domain Agent (`mock-agents/domain_agent.py`)

A minimal FastAPI app (separate process, separate port — e.g. `localhost:5001`) that simulates a product team's domain agent. Accepts a `POST /query` with a `{"query": str}` body, returns plausible-looking structured data. Does not call an LLM — hardcoded or lightly randomized responses are fine. The point is to have something to call over HTTP.

Running it as a separate process is intentional: it makes the inter-agent call feel real, not like a function call. Include a comment: *"In production, this is a real A2A-compliant agent operated by a product team. The coordinator runtime doesn't care what's inside it."*

---

## Frontend (`frontend/index.html`)

Single HTML file. No framework. Vanilla JS + CSS.

Three panes:
1. **Skill status panel** (left) — fetches `GET /skills` on load, shows each skill, its trigger type, and last execution time. Allows manual invocation of any skill via `POST /invoke/:skillName`.
2. **Main content area** (center) — renders skill output. Switches rendering based on `ui_type`:
   - `chat` → message bubbles
   - `card` → styled card component
   - `approval` → card with Confirm / Reject buttons
   - `form` → rendered form fields (for future skills)
3. **Event emitter** (right, collapsible) — allows firing `POST /event/:eventName` with a JSON payload, for demo purposes

**The demo point:** The frontend is not a chat app that also happens to show cards. It is a skill output renderer that happens to support chat as one output type.

Keep it functional and clean. This is not a design exercise — but it should not be embarrassing.

---

## CLAUDE.md

```markdown
# Solis POC

This is a proof-of-concept demonstrating a skill-runtime architecture for enterprise AI agents.

## Goal
Make the argument that "chat is a skill, not the product" tangible and runnable.

## Build Order
1. Implement runtime/skill_loader.py first — everything depends on it
2. Implement runtime/scheduler.py and runtime/event_bus.py
3. Implement runtime/agent.py (LangGraph + LiteLLM wrapper — read LITELLM_BASE_URL and LITELLM_MODEL from .env)
4. Implement runtime/api.py (FastAPI)
5. Implement runtime/main.py to wire everything together
6. Implement skills in order: chat → morning-briefing → cross-agent
7. Implement mock-agents/domain_agent.py (separate FastAPI process on port 5001)
8. Implement frontend/index.html last

## Principles
- Prefer simple over clever
- No unnecessary abstractions — this is a demo, not a framework
- Every file should be explainable in 2 minutes
- Comments should explain *why*, not *what*
- When in doubt, do less

## Prerequisites
The LiteLLM proxy must be running before starting the POC runtime:
```
cd $HOME/projects/litellm-proxy && docker compose up -d
```

Copy `.env.example` to `.env` and verify `LITELLM_BASE_URL` and `LITELLM_MODEL` are set correctly.

## Running
```
# Terminal 1 — mock domain agent
python mock-agents/domain_agent.py

# Terminal 2 — main runtime
uv run python -m runtime.main
# or: pip install -r requirements.txt && python -m runtime.main
```

The runtime should start, log loaded skills, and serve the frontend at localhost:3000.
Default model: anthropic/claude-sonnet-4-20250514 (via LiteLLM proxy at localhost:4000)
Override: set LITELLM_MODEL=watsonx/ibm/granite-3-8b-instruct in .env
```

---

## README.md (The Argument)

The README should make the case before showing any code. Suggested structure:

1. **The pitcher problem** — One paragraph. Nothing happens until a human initiates. This is the wrong mental model for an agent.

2. **Chat is a skill** — One paragraph. Conversational interaction is one way to work with an agent. It is not the agent.

3. **What this demos** — Three bullets, one per skill, plain language.

4. **How to run it** — start the LiteLLM proxy, run the mock agent, run the runtime. Three commands. The `.env.example` explains the two environment variables that matter.

5. **What this is not** — Not production code. Not a framework. Not a replacement for Solis. A working demonstration of a direction.

6. **The enterprise gap** — One paragraph acknowledging what this POC doesn't solve: multi-tenancy, RBAC, audit trails, skill permissioning, cost attribution. The POC proves the concept; these are the real engineering problems for Solis to solve.

---

## LiteLLM Proxy Setup

This POC requires a running LiteLLM proxy. Use the existing setup at `$HOME/projects/litellm-proxy` — it already has scripts configured for both Anthropic and watsonx.ai models.

```bash
cd $HOME/projects/litellm-proxy
docker compose up -d
```

The proxy exposes port `4000` by default. The POC runtime reads `LITELLM_BASE_URL` (default: `http://localhost:4000`) and `LITELLM_MODEL` (default: `anthropic/claude-sonnet-4-20250514`).

To demo against watsonx.ai instead:
```bash
LITELLM_MODEL=watsonx/ibm/granite-3-8b-instruct npm run dev
```

This is a deliberate demo affordance. When someone in the room asks "but does this only work with Anthropic?" — the answer is a one-line environment variable change, live, in front of them.

---

## Tech Stack

| Concern | Choice | Rationale |
|---|---|---|
| Language | Python 3.11+ | LangGraph is Python-native; familiar to ML/AI engineers; readable by the IBM audience |
| Package manager | uv (or pip) | uv is fast; pip is universal — either works |
| LLM orchestration | LangGraph (`create_react_agent`) | Framework-level orchestration; model-agnostic; Python-native |
| Model proxy | LiteLLM (`$HOME/projects/litellm-proxy`) | Single env var switches between Anthropic, watsonx.ai, or any provider; kills the model debate before it starts |
| Scheduling | APScheduler | Async-native, cron support, no external daemon |
| HTTP | FastAPI | Async-native, auto OpenAPI docs, familiar to Python devs |
| Validation | Pydantic | Free type safety on Skill and SkillResult models |
| Frontend | Vanilla HTML/JS | Zero framework overhead; readable by anyone |
| Dependencies | Minimize aggressively | The fewer dependencies, the more credible the "you can understand this" claim |

---

## Success Criteria

The POC succeeds if someone who has never seen it before can:

1. Read the README and understand the argument without running the code
2. Run `npm install && npm run dev` and see the runtime start
3. Watch the morning briefing appear in the UI without clicking anything
4. Fire a cross-domain query and see an approval card rendered — not a chat message
5. Comment out the `chat` skill directory and observe that the runtime continues working

If those five things are true, the demo does its job.
