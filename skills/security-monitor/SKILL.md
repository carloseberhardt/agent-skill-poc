---
name: security-monitor
description: Periodically checks for security threats by reviewing recent security events, access patterns, and potential indicators of compromise.
---

Check for security threats. This is a proactive monitor — look for issues even if nothing has been explicitly reported.

Step 1 — Gather information:
1. Ask the security agent for the current threat assessment and any critical findings — recent alerts, suspicious access patterns, unfamiliar IPs, or privilege escalations.
2. Use the employee lookup tool to identify affected users, their roles, departments, and managers.

Step 2 — Assess and recommend:
- If there is a clear security risk (data exfiltration, unauthorized access, unfamiliar IP), recommend a specific action and indicate which agent should handle it. If there is a risk, the recommendation should include notifying the discord channel.
- If the findings are informational only, present them as a summary without recommending action.

If you find any suspicious activity or threats, emit an incident correlation event so the runtime can cross-reference with other signals:
[EMIT:incident_correlation]

IMPORTANT:
- Only use the security agent and employee lookup tool. Do NOT call the data agent — data access monitoring is handled separately.
- Do NOT use the Discord notification tool or execute any remediation actions during this investigation. Only gather data and make a recommendation. Actions will be taken after human approval.
