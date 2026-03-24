---
name: incident-correlation-data-security
description: Checks whether security and data signals are connected. Produces a correlation report if both domains show issues, or a brief summary if only one does.
---

Investigate whether there is a cross-domain incident connecting security and data signals.

Step 1 — Gather information from both domains:
1. Ask the security agent for its current assessment — any threats, anomalies, or suspicious access.
2. Ask the data agent for its current assessment — access patterns, anomalies, pipeline health.

Step 2 — Gate check:
Review what both agents reported. If only ONE domain has actionable findings (the other reports normal activity), present a brief informational summary:
- Note which domain flagged an issue and what it found.
- Note that the other domain shows no related activity.
- Do NOT recommend action — single-domain issues are handled by their own monitor skills.

Only proceed to Step 3 if BOTH domains report issues worth investigating.

Step 3 — Correlate and recommend:
1. Use the employee lookup tool to resolve any user IDs that appear in both domains.
2. Use the cost tool to check for financial impact.
3. Look specifically for connections:
   - Do security flags involve the same users or resources as data access anomalies?
   - Is there a timeline that connects suspicious access with unusual data patterns?
   - Are cost anomalies explained by the incidents found?

Present a unified timeline and recommend coordinated actions. If the evidence suggests a deliberate action (e.g., data exfiltration), escalate clearly.

IMPORTANT: Do NOT use the Discord notification tool or execute any remediation actions during this investigation. Only gather data and make a recommendation. Actions will be taken after human approval.