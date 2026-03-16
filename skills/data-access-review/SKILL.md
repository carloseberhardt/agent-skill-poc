---
name: data-access-review
description: Reviews data access anomalies — unusual query patterns, after-hours PII access, or bulk extractions that deviate from baseline behavior.
---

A data anomaly has been detected. Investigate and assess risk — do NOT take action yet.

Step 1 — Gather information:
1. Ask the data agent about the anomaly — what datasets are involved, which users, what access patterns are unusual, and how they compare to baseline behavior.
2. Use the employee lookup tool to identify the flagged users — their role, department, clearance level, and manager.

Step 2 — Assess risk:
- Compare the flagged access against what's normal for that user's role and department.
- Consider time of access, volume of data, IP addresses, and data classification.
- If the access is explainable (e.g., a data engineer running scheduled ETL), note that.
- If the access is suspicious (e.g., a finance analyst pulling 50k PII rows at 3am), flag it clearly.

Present findings as a concise risk assessment. Do not recommend actions — this is an informational review that feeds into broader correlation.

IMPORTANT: Do NOT use the Discord notification tool or execute any remediation actions. Only gather data and present findings.
