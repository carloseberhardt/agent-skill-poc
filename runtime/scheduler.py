"""
Wraps APScheduler's AsyncIOScheduler for scheduled skill execution.

Each skill with trigger=scheduled gets its cron expression registered here.
On fire, the skill executor runs and the result is pushed via the on_result callback
(wired to SSE broadcast by main.py).
"""

import logging
from collections.abc import Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from runtime.skill_loader import Skill, SkillResult

logger = logging.getLogger("solis.scheduler")


class SkillScheduler:
    def __init__(self, on_result: Callable[[SkillResult], Awaitable[None]]):
        self._scheduler = AsyncIOScheduler()
        self._on_result = on_result

    def register(self, skill: Skill, context_extras: dict | None = None) -> None:
        if not skill.runtime_config.trigger_config:
            logger.warning("Scheduled skill %s has no trigger_config (cron)", skill.name)
            return

        trigger = CronTrigger.from_crontab(skill.runtime_config.trigger_config)

        async def _job():
            from runtime.skill_executor import execute_skill

            logger.info("Scheduler firing skill: %s", skill.name)
            context = {"skill": skill, "trigger": "scheduled", **(context_extras or {})}
            try:
                result = await execute_skill(skill, context)
                if result:
                    await self._on_result(result)
            except Exception:
                logger.exception("Scheduled skill %s failed", skill.name)

        self._scheduler.add_job(_job, trigger, id=f"skill:{skill.name}", name=skill.name)
        logger.info(
            "Registered schedule for %s: %s", skill.name, skill.runtime_config.trigger_config
        )

    def clear(self) -> None:
        """Remove all scheduled jobs (used during hot-reload)."""
        self._scheduler.remove_all_jobs()

    def start(self) -> None:
        self._scheduler.start()

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)
