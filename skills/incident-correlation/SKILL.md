---
name: incident-correlation-data-security
description: Checks whether security and data signals are connected. Produces a correlation report if both domains show issues, or a brief summary if only one does.
---

Investigate whether there is a cross-domain incident connecting security and data signals.

Step 1 — Gather information from both domains:
1. Ask the security agent for its current assessment — any threats, anomalies, or suspicious access.
2. Ask the data agent for its current assessment — access patterns, anomalies, pipeline health.

Step 2 — Gate check (MANDATORY — do this before any further analysis):
Evaluate each domain independently. A domain has "actionable findings" ONLY if its agent explicitly reported threats, anomalies, or policy violations. A domain has ZERO actionable findings if its agent reported normal activity, no threats, or all-clear status. Do NOT reinterpret normal activity as suspicious to justify a correlation.

State your gate verdict explicitly before continuing:
- "GATE: SINGLE-DOMAIN — only [domain] reported issues." → Go to Step 2a.
- "GATE: PROCEED — both domains reported issues." → Go to Step 3.

Step 2a — Single-domain summary (when only one domain has findings):
Respond with a JSON object using these keys:
- "title": "[Single-Domain] Summary: [brief description of the finding]"
- "bullets": 2-3 bullets noting what the reporting domain found and that the other domain showed no related activity.
- "action_recommended": false

Do NOT proceed to Step 3. Do NOT call additional tools (employee lookup, cost, etc.). Do NOT fabricate connections. Stop here.

Step 3 — Correlate and recommend (ONLY when both domains reported issues):
1. Use the employee lookup tool to resolve any user IDs that appear in both domains.
2. Use the cost tool to check for financial impact.
3. Look specifically for connections:
   - Do security flags involve the same users or resources as data access anomalies?
   - Is there a timeline that connects suspicious access with unusual data patterns?
   - Are cost anomalies explained by the incidents found?

Present a unified timeline and recommend coordinated actions. Respond with a JSON object using these keys:
- "title": "Cross-Domain Incident: [brief description]"
- "bullets": 3-5 key findings from the correlation.
- "action_recommended": true
- "action": a string describing the recommended action.
- "target_agent": the agent that should handle this action.

If the evidence suggests a deliberate action (e.g., data exfiltration), escalate clearly.

IMPORTANT: Do NOT add characterizations (e.g. "suspicious", "anomalous", "requires forensic review") to data points unless the source agent explicitly flagged them that way. Only include facts that came directly from tool calls.

IMPORTANT: Do NOT use the Discord notification tool or execute any remediation actions during this investigation. Only gather data and make a recommendation. Actions will be taken after human approval.
