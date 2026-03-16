---
name: incident-correlation
description: Correlates security, ops, and data signals when patterns suggest a connected incident — e.g., unusual access causing service degradation.
---

Multiple domain signals suggest a connected incident. Investigate and recommend — do NOT take action yet.

Step 1 — Gather information from all three domains:
1. Ask the ops agent for the infrastructure perspective — what services are affected and how.
2. Ask the security agent for the security perspective — any threats, anomalies, or suspicious access.
3. Ask the data agent for the data perspective — access patterns, pipeline health, data anomalies.
4. Use the employee lookup tool to resolve any user IDs and find reporting chains.
5. Use the cost tool to assess financial impact.

Step 2 — Correlate and recommend:
Look specifically for connections between domains:
- Is a service outage caused by or correlated with unusual data access?
- Do security flags involve the same users or resources as operational issues?
- Are cost anomalies explained by the incidents found in ops/security?

Present a unified timeline and recommend coordinated actions. If the evidence suggests
a deliberate action (e.g., data exfiltration causing service degradation), escalate clearly.

IMPORTANT: Do NOT use the Discord notification tool or execute any remediation actions during this investigation. Only gather data and make a recommendation. Actions will be taken after human approval.
