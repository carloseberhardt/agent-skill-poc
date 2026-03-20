# Architecture Review: POC to Production

This document maps the demo implementation to what an enterprise-grade system would require. The core thesis: **the architecture is sound — the delta is infrastructure, not design.** The skill-driven, protocol-native, event-driven model demonstrated in the POC is the same model that runs in production. What changes is the durability, scale, and operational maturity of the components underneath.

---

## What the Demo Demonstrates

The POC demonstrates a **platform agent runtime** — a system whose job is to orchestrate skills, not to be a chatbot. Chat is one skill among many. The runtime:

- Loads skills from portable, open-standard `SKILL.md` definitions (no per-skill code)
- Executes skills by handing instructions + tools to an LLM (instruction-driven, not handler-driven)
- Speaks both **A2A** (agent-to-agent) and **MCP** (model context protocol) through the Agent Gateway
- Fires skills on schedule, in response to events, or on user request
- Presents results through type-aware UI (cards, approvals, chat) driven by skill output
- Chains skills: monitors detect anomalies, emit events, trigger correlation skills

### What the Demo Does NOT Demonstrate (Intentionally)

The following are production concerns that were deliberately omitted to keep the POC focused on the architectural idea:

- Durable persistence (chat history, agent memory, audit trail, event log)
- Authentication and authorization (RBAC, skill permissioning, multi-tenancy)
- Enterprise scheduling (per-skill cron, backpressure, failure recovery)
- Durable event streaming (Kafka/Redpanda, replay, dead-letter queues)
- External-agent-initiated communication (inbound A2A push)
- Observability (structured logging, metrics, distributed tracing)
- Operational concerns (circuit breakers, timeouts, rate limiting, cost tracking)

---

## Key Architectural Decisions

### 1. External Agents Are External Systems

**This is the most important architectural point.**

The domain agents (security agent, data agent) are **not part of this platform**. They are other teams' systems that participate in the platform by speaking A2A. The platform does not build, own, or operate them — it orchestrates them.

Similarly, MCP tool servers (cost API, employee lookup, Discord notifier) are external capabilities exposed by the applications that own them. The platform consumes them; it does not build them.

In the demo, we run mock versions of these agents and tools locally for convenience. In production:

- The security agent is built and operated by the security team
- The data agent is built and operated by the data platform team
- The cost API is an MCP tool exposed by the FinOps system
- The employee lookup is an MCP tool exposed by the HR/identity system
- The Discord notifier is an MCP tool exposed by the notification service

**The platform team builds exactly one thing: the runtime.** Everything else is a participant.

Some internal MCP tools would exist within the platform (date/time utilities, documentation search, platform configuration), but the majority of tools and all domain agents are external.

### 2. The Agent Gateway Is a First-Class Architectural Component

The Agent Gateway is not infrastructure plumbing — it is the **protocol boundary** that enables the entire "external systems participate" model. It must:

- **Route A2A calls** to domain agents, preserving the full agent protocol (multi-turn, autonomous reasoning, task lifecycle)
- **Federate MCP tools** from multiple servers into a single tool surface
- **Handle discovery** — agent cards, tool listings, capability negotiation
- **Separate concerns** — the runtime doesn't need to know where agents live or how to reach them

#### Why Not Context Forge?

Some architectures propose using MCP-only federation (e.g., Context Forge) as the gateway layer. This does not work for this architecture because:

| Capability | Agent Gateway (A2A + MCP) | MCP-Only (e.g., Context Forge) |
|---|---|---|
| Tool calls | Yes | Yes |
| Agent-to-agent reasoning | Yes (A2A protocol) | No — flattens agent cards to tool signatures |
| Async / long-running tasks | Yes (A2A task lifecycle) | No — synchronous request/response blocks |
| External agent push (inbound) | Yes (A2A supports push) | No |
| Agent autonomy | Preserved — external agent does multi-step reasoning | Lost — reduced to a function call |

**Flattening agents to tools** (what Context Forge does) means the platform agent must do all the reasoning. A security team can't ship an agent that knows how to investigate threats — they can only ship a `get_security_events()` function. This defeats the purpose of a multi-agent platform.

**Synchronous blocking** is also a problem even for simple use cases. A domain agent may need 30-60 seconds to investigate a complex query (calling its own tools, reasoning over results). A synchronous tool-call blocks the platform agent's entire execution for that duration. A2A's task lifecycle model handles this natively with polling or push notification.

### 3. The Timer Does Double Duty (And Shouldn't)

In the demo, the 30-second asyncio timer serves two purposes:

1. **Scheduled skill execution** — Fires all scheduled skills (security-monitor, data-access-monitor) every 30 seconds
2. **Event simulation** — Because the external agents can't push events to the platform in the demo, the scheduled monitors effectively poll for problems, then emit internal events (e.g., `incident_correlation`) when they find something

In production, these are separate concerns:

| Concern | Demo Implementation | Production Implementation |
|---|---|---|
| **Scheduled skills** | Single 30s asyncio timer; all skills fire together | Per-skill cron expressions via a job scheduler (APScheduler, Celery Beat, or cloud-native equivalent) |
| **Event-driven skills** | Scheduled monitors poll → detect → emit internal events | External agents push events via A2A; message broker distributes to subscribed skills |
| **Event bus** | In-process `dict[str, list[handler]]` with 30s debounce | Durable message broker (Kafka, Redpanda, cloud pub/sub) with topic-per-event, replay, DLQ |

The real event-driven flow in production:

```
External security agent detects threat
  → A2A push to Agent Gateway
  → Gateway routes to platform runtime's inbound A2A endpoint
  → Runtime maps agent + event type to internal event
  → Message broker publishes to "security_alert" topic
  → Subscribed skills (security-monitor, incident-correlation) receive and execute
```

This eliminates the polling loop entirely for event-driven skills. Scheduled skills still need a scheduler, but each skill runs on its own cron schedule — not all lumped into a single timer.

### 4. The Platform Needs Its Own Persistence Layer

The demo stores nothing durably. All state is in-process and lost on restart:

| Data | Demo | Production |
|---|---|---|
| **Chat history** | Not stored; each chat invocation gets recent `_result_history` as context | Persistent conversation store with session management |
| **Skill execution results** | In-memory list, max 20, cleared on restart | Durable audit log — every execution, every tool call, every decision |
| **Agent memory** | None | Persistent memory store — the platform agent accumulates knowledge over time |
| **Event log** | In-process, lost on restart | Durable event stream with replay capability |
| **Approval decisions** | Ephemeral; not linked back to original skill result | Tracked in approval workflow system with audit trail |
| **Skill definitions** | Read from disk at startup / hot-reload | Skill registry (potentially versioned, with deployment pipeline) |

This is entirely separate from whatever databases the external agents use. The platform's persistence is for its own operational state.

---

## Component-by-Component: Demo vs. Production

### Runtime Core

| Component | Demo | Production | Notes |
|---|---|---|---|
| **Skill Loader** | Reads `SKILL.md` + `runtime.config.json` from `skills/` directory | Skill registry with versioning, validation, deployment pipeline | The `SKILL.md` format itself is production-ready (open standard) |
| **Skill Executor** | Builds prompts, calls agent, parses response | Same — but with timeout, retry, circuit breaker, cost tracking | The instruction-driven execution model doesn't change |
| **Agent Wrapper** | LangGraph `create_react_agent` with `ChatOpenAI` via LiteLLM | Same or equivalent — framework is swappable | Model-agnostic via LiteLLM; framework-agnostic by design |
| **Scheduler** | Single 30s `asyncio.sleep` loop | APScheduler or equivalent with per-skill cron | Fundamental change in scheduling granularity |
| **Event Bus** | In-process pub/sub with debounce | Message broker (Kafka/Redpanda) | Durability, replay, backpressure, distributed processing |
| **API Layer** | FastAPI with SSE | Same — FastAPI is production-grade | Add auth middleware, rate limiting |
| **Frontend** | Single-file vanilla HTML/JS | Production UI framework (React, etc.) or embedded in existing portal | The A2UI pattern (agent drives UI type) carries over |

### Infrastructure

| Component | Demo | Production | Notes |
|---|---|---|---|
| **Agent Gateway** | Docker container, static YAML config | Same technology, managed deployment | Already production-grade software; config becomes dynamic |
| **LiteLLM Proxy** | Docker container, static config | Managed LiteLLM or equivalent model routing layer | Already production-grade; add team/budget policies |
| **Model Provider** | Single provider (Anthropic or IBM via LiteLLM) | Multi-provider with fallback, cost routing, compliance controls | LiteLLM handles this natively |

### Operational Concerns (Not Demonstrated)

| Concern | Production Requirement |
|---|---|
| **Authentication** | OAuth2/OIDC for users; mTLS or API keys for agent-to-agent |
| **Authorization** | RBAC per skill — who can run what, who can approve what |
| **Multi-tenancy** | Isolated skill sets, agent access, data per tenant |
| **Observability** | Structured logging (JSON), metrics (Prometheus), distributed tracing (OpenTelemetry) |
| **Cost tracking** | Token usage per skill execution, per model, per team |
| **Rate limiting** | Per-user, per-skill, per-model limits |
| **Circuit breakers** | Fail-fast when external agents or tools are down |
| **Timeouts** | Per-skill execution timeout; per-tool-call timeout |
| **Secrets management** | Vault or equivalent; no env vars for secrets |
| **Deployment** | Container orchestration (Kubernetes), CI/CD, blue-green deploys |

---

## The Narrative for Your Presentation

**Slide 1 — The Thesis**: Chat is a skill, not the product. The agent runtime is the product.

**Slide 2 — The Demo**: Show the runtime executing skills — scheduled monitors detecting threats, event-driven correlation connecting signals across domains, chat as one UI among many, human-in-the-loop approvals triggering real actions.

**Slide 3 — The Architecture (C4 Context)**: The platform agent at the center, surrounded by the systems it orchestrates — domain agents speaking A2A, tool servers speaking MCP, LLM providers, notification systems, users.

**Slide 4 — The Agent Gateway**: Why A2A + MCP matters. Why tool-flattening (Context Forge) doesn't scale. External agents bring reasoning, not just functions.

**Slide 5 — The Delta**: Here's what the demo simplifies, and what production adds. The architecture doesn't change — the infrastructure underneath it matures.

**Slide 6 — The Skill Format**: Portable, open-standard, no vendor lock-in. Same `SKILL.md` works in Claude Code, NanoClaw, or our runtime.

---

## Summary

The POC succeeds at its goal: demonstrating that the skill-runtime architecture works end-to-end with minimal code (~3K LOC). The path to production is adding infrastructure maturity — persistence, auth, scheduling, eventing — not redesigning the system. The core abstractions (skill format, instruction-driven execution, protocol-native tool access, event-driven coordination) are the production abstractions.
