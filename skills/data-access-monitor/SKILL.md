---
name: data-access-monitor
description: Periodically checks for data access anomalies — unusual query patterns, after-hours PII access, or bulk extractions that deviate from baseline behavior.
---

Check for data access anomalies. This is a proactive monitor — look for issues even if nothing has been explicitly reported.

Step 1 — Gather information:
1. Ask the data agent about recent data access patterns — what datasets are being accessed, which users are active, what patterns are unusual, and how they compare to baseline behavior.
2. Use the employee lookup tool to identify any flagged users — their role, department, clearance level, and manager.

Step 2 — Assess risk:
- Compare flagged access against what's normal for each user's role and department.
- Consider time of access, volume of data, IP addresses, and data classification.
- If the access is explainable (e.g., a data engineer running scheduled ETL), note that.
- If the access is suspicious (e.g., a finance analyst pulling 50k PII rows at 3am), flag it clearly.

Present findings as a concise risk assessment.

- If the access is clearly suspicious, recommend a specific data-domain action (e.g., revoke access, pause pipeline) and indicate the data agent should handle it.
- If the findings are informational only (e.g., normal patterns, explainable access), present them as a summary without recommending action.

If you find any suspicious or anomalous activity, emit an incident correlation event so the runtime can cross-reference with other signals:
[EMIT:incident_correlation]

IMPORTANT:
- Only use the data agent and employee lookup tool. Do NOT call the security agent — security monitoring is handled separately.
- Do NOT use the Discord notification tool or execute any remediation actions. Only gather data and present findings.
