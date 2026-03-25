---
name: security-monitor
description: Periodically checks for security threats by reviewing recent security events, access patterns, and potential indicators of compromise.
---

Check for security threats. This is a proactive monitor — look for issues even if nothing has been explicitly reported.

Step 1 — Gather information:
1. Ask the security agent for the current threat assessment and any critical findings — recent alerts, suspicious access patterns, unfamiliar IPs, or privilege escalations.
2. Use the employee lookup tool to identify affected users, their roles, departments, and managers.

Step 2 — Assess and present findings:
Choose ONE of the following based on your assessment:

A) Clear security risk detected (data exfiltration, unauthorized access, unfamiliar IP, etc.):
- Recommend a specific action and indicate which agent should handle it. The recommendation should include notifying the discord channel.
- You MUST include the exact string [EMIT:incident_correlation] in your response. The runtime scans for this literal token to trigger cross-domain correlation. If you found a security threat and omit it, correlation will not run.

B) No threats found (informational findings only):
- Present findings as a summary without recommending action.
- Do NOT include [EMIT:incident_correlation].

IMPORTANT:
- Only use the security agent and employee lookup tool. Do NOT call the data agent — data access monitoring is handled separately.
- Do NOT use the Discord notification tool or execute any remediation actions during this investigation. Only gather data and make a recommendation. Actions will be taken after human approval.
