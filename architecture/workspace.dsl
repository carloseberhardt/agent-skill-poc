workspace "Solis Platform Agent" "Enterprise skill runtime — orchestrates domain agents (A2A) and tool servers (MCP) to execute declarative, instruction-driven skills." {

    !identifiers hierarchical

    model {

        # ════════════════════════════════════════════════════════════════
        # People
        # ════════════════════════════════════════════════════════════════

        platformUser = person "Platform User" "Interacts with the runtime via chat, reviews skill output, approves recommended actions."
        skillAuthor = person "Skill Author" "Writes SKILL.md definitions and runtime configs. Does not write handler code."

        # ════════════════════════════════════════════════════════════════
        # External Systems — Domain Agents (A2A)
        # ════════════════════════════════════════════════════════════════
        # These are NOT built for the platform. They are each application's
        # own agent, speaking A2A. The platform orchestrates them.

        securityAgent = softwareSystem "Security Agent" "Security team's own agent. Investigates threats, reviews access patterns, assesses risk. Speaks A2A." {
            tags "External" "A2A" "Demonstrated"
        }

        dataAgent = softwareSystem "Data Agent" "Data platform team's own agent. Monitors data access, pipeline health, anomaly detection. Speaks A2A." {
            tags "External" "A2A" "Demonstrated"
        }

        futureAgentN = softwareSystem "Other Domain Agents" "Additional domain agents from other teams — compliance, infrastructure, customer support, etc. Each speaks A2A." {
            tags "External" "A2A" "Production"
        }

        # ════════════════════════════════════════════════════════════════
        # External Systems — MCP Tool Servers
        # ════════════════════════════════════════════════════════════════
        # External applications expose capabilities as MCP tools.
        # Most applications won't have their own agents — just tools.

        costApi = softwareSystem "Cost API" "FinOps system exposing cost data as an MCP tool." {
            tags "External" "MCP" "Demonstrated"
        }

        employeeLookup = softwareSystem "Employee Lookup" "HR/Identity system exposing employee resolution as an MCP tool." {
            tags "External" "MCP" "Demonstrated"
        }

        discordNotifier = softwareSystem "Notification Service" "Messaging system (Discord, Slack, email) exposing notification as an MCP tool." {
            tags "External" "MCP" "Demonstrated"
        }

        futureToolN = softwareSystem "Other MCP Tool Servers" "Additional tool servers — ITSM, CI/CD, documentation, cloud APIs. Each exposes MCP tools." {
            tags "External" "MCP" "Production"
        }

        # ════════════════════════════════════════════════════════════════
        # External Systems — Infrastructure
        # ════════════════════════════════════════════════════════════════

        llmProvider = softwareSystem "LLM Provider" "Model inference (Anthropic, IBM watsonx, Azure OpenAI, etc.). Accessed via LiteLLM proxy." {
            tags "External" "Demonstrated"
        }

        identityProvider = softwareSystem "Identity Provider" "OAuth2/OIDC provider for user authentication and RBAC." {
            tags "External" "Production"
        }

        # ════════════════════════════════════════════════════════════════
        # The Platform — this is the only thing we build
        # ════════════════════════════════════════════════════════════════

        solisPlatform = softwareSystem "Solis Platform Agent" "Skill runtime that orchestrates domain agents and tool servers to execute declarative, proactive, event-driven skills." {
            tags "Platform"

            # ── Containers ─────────────────────────────────────────────

            runtime = container "Skill Runtime" "Core execution engine. Loads skills, builds prompts, invokes LLM with tools, parses results, manages lifecycle." "Python / FastAPI / LangGraph" {
                tags "Demonstrated"

                # ── Components ─────────────────────────────────────────

                skillLoader = component "Skill Loader" "Parses SKILL.md (open standard) and runtime.config.json. Validates and registers skills." "Python / Pydantic" {
                    tags "Demonstrated"
                }

                skillExecutor = component "Skill Executor" "Generic executor for all skills. Builds prompts from instructions + context, calls agent, parses typed responses (card, approval, chat)." "Python" {
                    tags "Demonstrated"
                }

                agentOrchestrator = component "Agent Orchestrator" "LLM + tool orchestration loop. Presents unified tool surface (A2A + MCP) to the model. Manages retries and callbacks." "LangGraph / LiteLLM" {
                    tags "Demonstrated"
                }

                scheduler = component "Scheduler" "Executes skills on cron schedules. POC: single 30s timer. Production: per-skill cron via APScheduler or equivalent." "Python / asyncio" {
                    tags "Demonstrated"
                    description "POC: single 30-second asyncio timer fires all scheduled skills together. Production: per-skill cron expressions, backpressure, failure recovery."
                }

                eventBus = component "Event Bus" "Receives events (internal or external), routes to subscribed skills. POC: in-process pub/sub. Production: backed by message broker." "Python / asyncio" {
                    tags "Demonstrated"
                    description "POC: in-process dict with debounce. Production: consumer on message broker topics. Durable, replayable, with dead-letter queue."
                }

                toolRouter = component "Tool Router" "Routes tool calls through the Agent Gateway. Distinguishes A2A agents from MCP tools. Handles discovery and capability refresh." "Python / httpx" {
                    tags "Demonstrated"
                }

                apiLayer = component "API Layer" "HTTP endpoints for skill invocation, event emission, timer control, SSE streaming. Serves frontend." "FastAPI / SSE" {
                    tags "Demonstrated"
                }

                memoryManager = component "Memory Manager" "Manages platform agent's persistent memory — accumulated knowledge, conversation context, cross-skill state." "Python" {
                    tags "Production"
                    description "Not demonstrated in POC. Production: reads/writes to persistence store. Enables the platform agent to learn and retain context across sessions."
                }
            }

            agentGateway = container "Agent Gateway" "Protocol boundary. Routes A2A agent calls and federates MCP tool servers into a single surface. Handles discovery, auth passthrough." "Agent Gateway (agentgateway)" {
                tags "Demonstrated" "Critical"
                description "First-class architectural component, not infrastructure plumbing. Enables the 'external systems participate' model by speaking both A2A and MCP natively. NOT replaceable by MCP-only solutions (e.g. Context Forge) — see architecture review."
            }

            persistenceStore = container "Persistence Store" "Durable storage for platform operational state — chat history, skill execution audit trail, agent memory, event log, approval records." "PostgreSQL / equivalent" {
                tags "Production"
                description "Not demonstrated in POC — all state is in-memory and lost on restart. Production: durable, queryable, with retention policies."
            }

            messageBroker = container "Message Broker" "Durable event streaming. Replaces in-process event bus. Enables external agents to push events, replay, dead-letter queues." "Kafka / Redpanda" {
                tags "Production"
                description "Not demonstrated in POC — events are in-process pub/sub. Production: topic-per-event-type, consumer groups, exactly-once delivery."
            }

            modelProxy = container "Model Proxy" "Routes LLM requests to configured providers. Handles model selection, team routing, cost tracking, rate limiting." "LiteLLM" {
                tags "Demonstrated"
            }

            frontend = container "Frontend" "Web UI rendering skill output by type (card, approval, chat). Receives real-time updates via SSE." "HTML / JS / SSE" {
                tags "Demonstrated"
                description "POC: single-file vanilla HTML/JS. Production: embedded in existing portal or built with framework."
            }

            skillRegistry = container "Skill Registry" "Versioned store for SKILL.md definitions and runtime configs. Supports deployment pipeline, validation, rollback." "Storage / API" {
                tags "Production"
                description "Not demonstrated in POC — skills loaded from local filesystem. Production: versioned, with CI/CD integration."
            }
        }

        # ════════════════════════════════════════════════════════════════
        # Relationships
        # ════════════════════════════════════════════════════════════════

        # --- People → Platform ---
        platformUser -> solisPlatform.frontend "Uses" "HTTPS"
        platformUser -> solisPlatform.runtime "Invokes skills, approves actions" "HTTPS"
        skillAuthor -> solisPlatform.skillRegistry "Publishes skill definitions" "CI/CD"

        # --- Frontend → Runtime ---
        solisPlatform.frontend -> solisPlatform.runtime.apiLayer "Invokes skills, receives SSE events" "HTTP / SSE"

        # --- Runtime internal (component-level) ---
        solisPlatform.runtime.apiLayer -> solisPlatform.runtime.skillExecutor "Triggers skill execution"
        solisPlatform.runtime.apiLayer -> solisPlatform.runtime.eventBus "Emits events from API / A2A callbacks"
        solisPlatform.runtime.skillExecutor -> solisPlatform.runtime.agentOrchestrator "Invokes LLM with tools"
        solisPlatform.runtime.skillExecutor -> solisPlatform.runtime.eventBus "Emits events from skill output"
        solisPlatform.runtime.scheduler -> solisPlatform.runtime.skillExecutor "Fires scheduled skills"
        solisPlatform.runtime.eventBus -> solisPlatform.runtime.skillExecutor "Fires event-triggered skills"
        solisPlatform.runtime.skillLoader -> solisPlatform.runtime.scheduler "Registers scheduled skills"
        solisPlatform.runtime.skillLoader -> solisPlatform.runtime.eventBus "Subscribes event-triggered skills"
        solisPlatform.runtime.agentOrchestrator -> solisPlatform.runtime.toolRouter "Routes tool/agent calls"
        solisPlatform.runtime.skillExecutor -> solisPlatform.runtime.memoryManager "Reads/writes agent memory"
        solisPlatform.runtime.memoryManager -> solisPlatform.persistenceStore "Persists memory, chat history" "SQL"

        # --- Runtime → Containers ---
        solisPlatform.runtime.toolRouter -> solisPlatform.agentGateway "Routes A2A + MCP calls" "HTTP"
        solisPlatform.runtime.agentOrchestrator -> solisPlatform.modelProxy "LLM inference" "HTTP"
        solisPlatform.runtime.eventBus -> solisPlatform.messageBroker "Publishes/consumes events" "Kafka protocol"
        solisPlatform.runtime.apiLayer -> solisPlatform.persistenceStore "Stores skill results, audit trail" "SQL"
        solisPlatform.runtime.skillLoader -> solisPlatform.skillRegistry "Loads skill definitions"

        # --- Containers → External ---
        solisPlatform.agentGateway -> securityAgent "A2A message/send" "A2A / JSON-RPC"
        solisPlatform.agentGateway -> dataAgent "A2A message/send" "A2A / JSON-RPC"
        solisPlatform.agentGateway -> futureAgentN "A2A message/send" "A2A / JSON-RPC"
        solisPlatform.agentGateway -> costApi "MCP tool calls" "MCP / HTTP"
        solisPlatform.agentGateway -> employeeLookup "MCP tool calls" "MCP / HTTP"
        solisPlatform.agentGateway -> discordNotifier "MCP tool calls" "MCP / HTTP"
        solisPlatform.agentGateway -> futureToolN "MCP tool calls" "MCP / HTTP"
        solisPlatform.modelProxy -> llmProvider "Model inference" "HTTPS"

        # --- External → Platform (inbound push — production only) ---
        securityAgent -> solisPlatform.runtime.apiLayer "Pushes events via A2A callback" "A2A / HTTP" {
            tags "Production"
        }
        dataAgent -> solisPlatform.runtime.apiLayer "Pushes events via A2A callback" "A2A / HTTP" {
            tags "Production"
        }
    }

    # ════════════════════════════════════════════════════════════════════
    # Views
    # ════════════════════════════════════════════════════════════════════

    views {

        # ── Level 1: System Context ───────────────────────────────────
        systemContext solisPlatform "L1_SystemContext" "Level 1 — System Context: The platform agent and all external systems it interacts with." {
            include *
            autoLayout lr 400 100
        }

        # ── Level 2: Containers ───────────────────────────────────────
        container solisPlatform "L2_Containers" "Level 2 — Containers: The deployable units within the platform." {
            include *

            # Include external systems to show what connects to what
            include securityAgent
            include dataAgent
            include futureAgentN
            include costApi
            include employeeLookup
            include discordNotifier
            include futureToolN
            include llmProvider
            include platformUser
            include identityProvider

            autoLayout lr 400 100
        }

        # ── Level 3: Components (Runtime) ─────────────────────────────
        component solisPlatform.runtime "L3_RuntimeComponents" "Level 3 — Components: Inside the Skill Runtime." {
            include *

            # Include connected containers
            include solisPlatform.agentGateway
            include solisPlatform.modelProxy
            include solisPlatform.persistenceStore
            include solisPlatform.messageBroker
            include solisPlatform.frontend
            include solisPlatform.skillRegistry

            autoLayout lr 400 100
        }

        # ── Styles ────────────────────────────────────────────────────

        styles {

            # Platform — the thing we build
            element "Platform" {
                shape RoundedBox
                background #1168bd
                color #ffffff
                fontSize 24
            }

            # External systems
            element "External" {
                shape RoundedBox
                background #999999
                color #ffffff
            }

            # A2A agents — distinct from MCP tools
            element "A2A" {
                shape RoundedBox
                background #e07020
                color #ffffff
            }

            # MCP tool servers
            element "MCP" {
                shape RoundedBox
                background #6b9e3f
                color #ffffff
            }

            # Components/containers demonstrated in the POC
            element "Demonstrated" {
                border solid
            }

            # Production-only components (not in the demo)
            element "Production" {
                border dashed
                opacity 75
            }

            # The Agent Gateway — highlighted as critical
            element "Critical" {
                background #c41230
                color #ffffff
            }

            # People
            element "Person" {
                shape Person
                background #08427b
                color #ffffff
            }

            # Software System
            element "Software System" {
                shape RoundedBox
            }

            # Containers
            element "Container" {
                shape RoundedBox
                background #438dd5
                color #ffffff
            }

            # Components
            element "Component" {
                shape RoundedBox
                background #85bbf0
                color #000000
            }

            # Relationships
            relationship "Relationship" {
                thickness 2
                color #707070
                style solid
            }

            # Production-only relationships
            relationship "Production" {
                style dashed
                color #aaaaaa
            }
        }
    }

}
