---
name: morning-briefing
description: Compiles a morning briefing from all domain agents and tools — ops health, security status, data pipeline state, and cost summary.
---

Query all three domain agents and the cost tool to build a morning briefing:

1. Ask the ops agent for current service health and any active incidents.
2. Ask the security agent for overnight security events and risk assessment.
3. Ask the data agent for pipeline status and any access anomalies.
4. Use the cost tool to check budget status across services.

Synthesize everything into a concise morning summary. Lead with anything that needs attention, then cover normal operations briefly. If multiple agents report related issues (e.g., a service degradation that correlates with unusual data access), call that out as a cross-domain concern.
