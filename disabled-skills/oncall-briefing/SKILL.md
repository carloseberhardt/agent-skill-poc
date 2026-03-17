---
name: oncall-briefing
description: Generates a shift handoff briefing for the incoming on-call team — current status across security, data, and cost, plus who to contact.
---

Produce a concise on-call shift briefing for whoever is coming on duty.

Step 1 — Gather context:
1. Use the employee lookup tool to find who is currently on call and their roles.
2. Ask the security agent for the current threat posture — any active alerts, recent incidents, or ongoing investigations.
3. Ask the data agent for data platform health — pipeline status, any access anomalies, and any recent actions taken.
4. Use the cost tool to check budget status across services — flag anything over budget or trending high.

Step 2 — Notify the team:
BEFORE producing your final response, use the Discord notification tool to post a brief summary to the channel. Include: overall status, active issues (if any), and who is on call. This must happen before you respond.

Step 3 — Compile the briefing card:
- Lead with the overall status: is it a quiet shift or are there active issues?
- List who is on call and their contact info.
- Summarize security posture in 1-2 lines.
- Summarize data platform health in 1-2 lines.
- Note any cost concerns.
- If there are active incidents or recent actions, highlight what the incoming team needs to know.

IMPORTANT: Only include facts from your tool calls. Do not invent incidents or status details.
