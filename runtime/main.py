"""
Solis POC runtime entry point.

Loads skills, wires up the scheduler and event bus, starts the HTTP API.
One process, one event loop, no microservices.
"""

import logging
import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from runtime import api
from runtime.api import broadcast_result
from runtime.event_bus import EventBus
from runtime.scheduler import SkillScheduler
from runtime.skill_loader import load_skills

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("solis.main")


def main():
    skills_dir = Path("skills")
    logger.info("Loading skills from %s", skills_dir.resolve())

    skills = load_skills(skills_dir)
    if not skills:
        logger.warning("No skills loaded — the runtime will start but won't do much")

    event_bus = EventBus()
    event_bus.set_result_handler(broadcast_result)

    scheduler = SkillScheduler(on_result=broadcast_result)

    # Context extras passed into every skill handler
    context_extras = {"event_bus": event_bus}

    for skill in skills:
        trigger = skill.runtime_config.trigger
        if trigger == "scheduled":
            # In demo mode, override long cron schedules to fire every minute
            if os.getenv("DEMO_MODE") == "true" and skill.runtime_config.trigger_config:
                logger.info("DEMO_MODE: overriding %s schedule to every minute", skill.name)
                skill.runtime_config.trigger_config = "* * * * *"
            scheduler.register(skill, context_extras=context_extras)
        elif trigger == "event":
            event_name = skill.runtime_config.trigger_config
            if event_name:
                event_bus.subscribe(event_name, skill, context_extras=context_extras)

    api.init(skills, scheduler, event_bus)

    # Scheduler starts in the FastAPI lifespan hook (needs a running event loop)
    logger.info("Runtime ready — %d skill(s) loaded", len(skills))
    logger.info("API + UI at http://localhost:8000")
    logger.info("API docs at http://localhost:8000/docs")

    uvicorn.run(api.app, host="0.0.0.0", port=8000, log_level="info")


if __name__ == "__main__":
    main()
