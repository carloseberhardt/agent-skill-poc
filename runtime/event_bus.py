"""
Simple asyncio-based event pub/sub.

Skills with trigger=event subscribe to a named event. External systems (or other
skills) emit events via the API or programmatically. Handlers fire as async tasks
so emitters are never blocked.

Includes debounce: if the same event fires multiple times within a short window,
only the first triggers subscribers. This prevents duplicate skill runs when
multiple monitors emit the same event in the same tick.

In production, replace this with Redpanda, Kafka, or any durable event broker.
This is in-process for the POC.
"""

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable

from runtime.skill_loader import Skill, SkillResult

logger = logging.getLogger("solis.event_bus")

DEBOUNCE_SECONDS = 30


class EventBus:
    def __init__(self):
        self._subscribers: dict[str, list[Callable]] = defaultdict(list)
        self._on_result: Callable[[SkillResult], Awaitable[None]] | None = None
        self._on_activity: Callable[[str, str], Awaitable[None]] | None = None
        self._last_emit: dict[str, float] = {}  # event_name → epoch time

    def set_result_handler(self, handler: Callable[[SkillResult], Awaitable[None]]) -> None:
        self._on_result = handler

    def set_activity_handler(self, handler: Callable[[str, str], Awaitable[None]]) -> None:
        self._on_activity = handler

    def subscribe(self, event_name: str, skill: Skill, context_extras: dict | None = None) -> None:
        async def _handler(payload: dict):
            from runtime.skill_executor import execute_skill

            logger.info("Event '%s' firing skill: %s", event_name, skill.name)
            if self._on_activity:
                await self._on_activity(f"Event '{event_name}' → triggering skill: {skill.name}", "event")
            context = {
                "skill": skill,
                "trigger": "event",
                "payload": payload,
                **(context_extras or {}),
            }
            try:
                result = await execute_skill(skill, context)
                if result and self._on_result:
                    await self._on_result(result)
            except Exception:
                logger.exception("Event skill %s failed", skill.name)

        self._subscribers[event_name].append(_handler)
        logger.info("Subscribed %s to event '%s'", skill.name, event_name)

    def clear(self) -> None:
        """Remove all subscriptions (used during hot-reload)."""
        self._subscribers.clear()
        self._last_emit.clear()

    async def emit(self, event_name: str, payload: dict | None = None) -> None:
        handlers = self._subscribers.get(event_name, [])
        if not handlers:
            logger.warning("No subscribers for event '%s'", event_name)
            return

        # Debounce: skip if this event fired recently
        now = time.time()
        last = self._last_emit.get(event_name, 0)
        if now - last < DEBOUNCE_SECONDS:
            logger.info("Debounced event '%s' (fired %.1fs ago)", event_name, now - last)
            if self._on_activity:
                await self._on_activity(
                    f"Event '{event_name}' debounced (already fired this tick)", "event"
                )
            return

        self._last_emit[event_name] = now
        logger.info("Emitting event '%s' to %d subscriber(s)", event_name, len(handlers))
        for handler in handlers:
            asyncio.create_task(handler(payload or {}))

    @property
    def subscriptions(self) -> dict[str, int]:
        return {name: len(handlers) for name, handlers in self._subscribers.items()}
