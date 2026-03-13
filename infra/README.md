# Infrastructure

Self-contained infrastructure for the Solis POC. Each service has its own compose file.

## LiteLLM Proxy

Model-agnostic LLM gateway. Virtual keys, team-based model routing.

```bash
cd infra/litellm
cp .env.example .env          # set your master key
docker compose up -d
```

Runs on `http://localhost:4000`. Models are registered per-team via API.

## Agent Gateway

A2A agent routing + MCP tool federation. Uses `ghcr.io/agentgateway/agentgateway:0.12.0` with static config.

```bash
cd infra/agent-gateway
docker compose up -d
```

Admin UI at `http://localhost:15000`.
A2A + MCP listener at `http://localhost:3000`.

### Configuration

Routes are defined in `config.yaml`:
- **A2A routes**: `/data-agent/*` → port 5001, `/security-agent/*` → port 5002
- **MCP federation**: `/mcp` → cost-api (5003) + employee-lookup (5004)

No registration step needed — Agent Gateway uses static config. Just start the backends.

### Key details

- Config uses `host.docker.internal` for container→host networking
- A2A routes have `a2a: {}` policy for protocol handling
- MCP backends use SSE transport for federation
- CORS is enabled on all routes for browser access

## Startup Order

1. LiteLLM proxy (`infra/litellm`)
2. Agent Gateway (`infra/agent-gateway`) — `docker compose up -d`
3. MCP servers: `cost_api.py` (5003), `employee_lookup.py` (5004)
4. A2A agents: `data_agent.py` (5001), `security_agent.py` (5002)
5. Main runtime (`uv run python -m runtime.main`)
