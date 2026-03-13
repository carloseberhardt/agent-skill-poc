---
name: security-monitor
description: Monitors security events and triggers cross-domain analysis when critical issues are detected.
---

Query the security agent for recent events.
If any event has severity "critical" or "action_needed", include [EMIT:cross_domain_query] in your response.
Present all findings as a card regardless.
