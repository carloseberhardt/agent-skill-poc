---
name: security-escalation
description: Investigates security alerts by correlating security events with data access patterns and employee information. Recommends and routes actions.
---

A security event has been detected. Investigate and recommend — do NOT take action yet.

Step 1 — Gather information:
1. Ask the security agent for the current threat assessment and details on critical findings.
2. Ask the data agent about related data access patterns — especially any anomalies involving the same users or resources flagged by security.
3. Use the employee lookup tool to identify affected users, their roles, departments, and managers.

Step 2 — Assess and recommend:
- If there is a clear security risk (data exfiltration, unauthorized access, unfamiliar IP), recommend a specific action and indicate which agent should handle it.
- If the findings are informational only, present them as a summary without recommending action.

IMPORTANT: Do NOT use the Discord notification tool or execute any remediation actions during this investigation. Only gather data and make a recommendation. Actions will be taken after human approval.
