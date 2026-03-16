---
name: incident-correlation
description: Correlates security and data signals when patterns suggest a connected incident — e.g., suspicious access aligned with data anomalies or cost spikes.
---

Multiple domain signals suggest a connected incident. Investigate and recommend — do NOT take action yet.

Step 1 — Gather information from both domains:
1. Ask the security agent for the security perspective — any threats, anomalies, or suspicious access.
2. Ask the data agent for the data perspective — access patterns, pipeline health, data anomalies.
3. Use the employee lookup tool to resolve any user IDs and find reporting chains.
4. Use the cost tool to assess financial impact.

Step 2 — Correlate and recommend:
Look specifically for connections between domains:
- Do security flags involve the same users or resources as data access anomalies?
- Are cost anomalies explained by the incidents found in security or data access?
- Is there a timeline that connects suspicious access with unusual data patterns?

Present a unified timeline and recommend coordinated actions. If the evidence suggests
a deliberate action (e.g., data exfiltration), escalate clearly.

IMPORTANT: Do NOT use the Discord notification tool or execute any remediation actions during this investigation. Only gather data and make a recommendation. Actions will be taken after human approval.
