"""
Simple asyncio-based event pub/sub.

Skills with trigger=event subscribe to a named event. External systems (or other
skills) emit events via the API or programmatically. Handlers fire as async tasks
so emitters are never blocked.

In production, replace this with Redpanda, Kafka, or any durable event broker.
This is in-process for the POC.
"""

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable

from runtime.skill_loader import Skill, SkillResult

logger = logging.getLogger("solis.event_bus")


class EventBus:
    def __init__(self):
        self._subscribers: dict[str, list[Callable]] = defaultdict(list)
        self._on_result: Callable[[SkillResult], Awaitable[None]] | None = None

    def set_result_handler(self, handler: Callable[[SkillResult], Awaitable[None]]) -> None:
        self._on_result = handler

    def subscribe(self, event_name: str, skill: Skill, context_extras: dict | None = None) -> None:
        async def _handler(payload: dict):
            from runtime.skill_executor import execute_skill

            logger.info("Event '%s' firing skill: %s", event_name, skill.name)
            context = {
                "skill": skill,
                "trigger": "event",
                "payload": payload,
                # Propagate trace_id from the emitter so the chain is traceable
                "trace_id": payload.get("trace_id"),
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

    async def emit(self, event_name: str, payload: dict | None = None) -> None:
        handlers = self._subscribers.get(event_name, [])
        if not handlers:
            logger.warning("No subscribers for event '%s'", event_name)
            return
        logger.info("Emitting event '%s' to %d subscriber(s)", event_name, len(handlers))
        for handler in handlers:
            asyncio.create_task(handler(payload or {}))

    @property
    def subscriptions(self) -> dict[str, int]:
        return {name: len(handlers) for name, handlers in self._subscribers.items()}
