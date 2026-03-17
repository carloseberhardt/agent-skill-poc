"""
Timer-based monitor scheduler using asyncio.

All monitor skills share a single 30-second timer. The timer starts paused
so the demo can begin with an explanation before unpausing.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from runtime.skill_loader import Skill, SkillResult

logger = logging.getLogger("solis.scheduler")

TICK_INTERVAL = 30  # seconds


class SkillScheduler:
    def __init__(
        self,
        on_result: Callable[[SkillResult], Awaitable[None]],
        on_activity: Callable[[str, str], Awaitable[None]] | None = None,
        on_state_change: Callable[[], None] | None = None,
    ):
        self._on_result = on_result
        self._on_activity = on_activity
        self._on_state_change = on_state_change
        self._monitors: list[tuple[Skill, dict | None]] = []
        self._running = False
        self._task: asyncio.Task | None = None
        self._next_tick: float | None = None  # epoch time of next tick

    # ── Registration ──────────────────────────────────────────────

    def register_monitor(self, skill: Skill, context_extras: dict | None = None) -> None:
        self._monitors.append((skill, context_extras))
        logger.info("Registered monitor: %s", skill.name)

    def clear(self) -> None:
        self._monitors.clear()

    # ── Timer control ─────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._next_tick = time.time() + TICK_INTERVAL
        self._task = asyncio.ensure_future(self._loop())
        logger.info("Monitor timer started (interval=%ds)", TICK_INTERVAL)
        self._notify_state_change()

    def pause(self) -> None:
        if not self._running:
            return
        self._running = False
        self._next_tick = None
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("Monitor timer paused")
        self._notify_state_change()

    async def run_now(self) -> None:
        """Run one tick immediately, regardless of timer state."""
        logger.info("Monitor timer: run_now triggered")
        if self._on_activity:
            await self._on_activity("Monitor tick (manual)", "skill")
        await self._tick()

    @property
    def status(self) -> dict:
        return {
            "state": "running" if self._running else "paused",
            "interval": TICK_INTERVAL,
            "monitors": [s.name for s, _ in self._monitors],
            "next_tick": self._next_tick,
        }

    # ── Internal ──────────────────────────────────────────────────

    async def _loop(self) -> None:
        try:
            while self._running:
                self._next_tick = time.time() + TICK_INTERVAL
                self._notify_state_change()
                await asyncio.sleep(TICK_INTERVAL)
                if not self._running:
                    break
                if self._on_activity:
                    await self._on_activity("Monitor tick (scheduled)", "skill")
                await self._tick()
        except asyncio.CancelledError:
            pass

    async def _tick(self) -> None:
        from runtime.skill_executor import execute_skill

        async def _run_monitor(skill, extras):
            logger.info("Monitor firing: %s", skill.name)
            if self._on_activity:
                await self._on_activity(f"Monitor running: {skill.name}", "skill")
            context = {"skill": skill, "trigger": "scheduled", **(extras or {})}
            try:
                result = await execute_skill(skill, context)
                if result:
                    await self._on_result(result)
            except Exception:
                logger.exception("Monitor skill %s failed", skill.name)

        await asyncio.gather(*[_run_monitor(s, e) for s, e in self._monitors])

    def _notify_state_change(self) -> None:
        if self._on_state_change:
            self._on_state_change()

    def shutdown(self) -> None:
        self.pause()
