---
name: service-incident-response
description: Coordinates incident response when service degradation is detected — queries ops, data, and cost domains, identifies on-call, recommends remediation.
---

A service incident has been detected. Investigate and recommend — do NOT take action yet.

Step 1 — Gather information:
1. Ask the ops agent for details on the affected service(s) — current metrics, severity, and cascading impact.
2. Ask the data agent whether any data pipelines are affected and if there are unusual access patterns correlating with the incident.
3. Use the cost tool to check the budget impact of the affected services.
4. Use the employee lookup tool to find who is on call for the affected team.

Step 2 — Assess and recommend:
Synthesize findings into an incident summary:
- What's broken and how bad is it (service tier, user impact)
- What's the likely root cause based on cross-domain evidence
- What's the recommended remediation (scale up, restart, investigate further)
- Who needs to be notified (on-call staff, managers)

If immediate action is recommended, specify the action and which agent should handle it.

IMPORTANT: Do NOT use the Discord notification tool or execute any remediation actions during this investigation. Only gather data and make a recommendation. Actions will be taken after human approval.
