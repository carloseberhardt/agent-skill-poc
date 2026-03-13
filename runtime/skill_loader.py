"""
Loads skills from the skills/ directory.

Each skill is a directory containing:
- SKILL.md: Agent Skills open standard (https://agentskills.io/specification)
  Required frontmatter: name, description. Body is freeform instructions.
- runtime.config.json: Solis-specific runtime config (trigger, ui_type).
  Not part of the open spec — this is the layer that makes a portable skill
  runnable in the Solis runtime. If absent, defaults to manual trigger + chat UI.

No handler.py needed — the skill executor reads SKILL.md instructions and
uses the agent (with MCP tools) to fulfill them.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import frontmatter
from pydantic import BaseModel, ConfigDict

logger = logging.getLogger("solis.skill_loader")


class RuntimeConfig(BaseModel):
    trigger: Literal["manual", "scheduled", "event"] = "manual"
    trigger_config: str | None = None
    ui_type: Literal["chat", "card", "form", "approval", "none"] = "chat"


class SkillResult(BaseModel):
    skill_name: str
    ui_type: str
    content: dict[str, Any]
    timestamp: datetime


class Skill(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: str
    instructions: str
    path: Path
    runtime_config: RuntimeConfig


def load_skills(skills_dir: Path) -> list[Skill]:
    skills: list[Skill] = []

    if not skills_dir.is_dir():
        logger.warning("Skills directory not found: %s", skills_dir)
        return skills

    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        try:
            # Parse SKILL.md (open standard)
            skill_md_path = skill_dir / "SKILL.md"
            if not skill_md_path.exists():
                logger.warning("Skipping %s: no SKILL.md", skill_dir.name)
                continue

            post = frontmatter.load(str(skill_md_path))
            name = post.metadata.get("name")
            description = post.metadata.get("description")
            if not name or not description:
                logger.warning(
                    "Skipping %s: SKILL.md missing name or description",
                    skill_dir.name,
                )
                continue

            # Parse runtime.config.json (Solis-specific)
            config_path = skill_dir / "runtime.config.json"
            if config_path.exists():
                runtime_config = RuntimeConfig(**json.loads(config_path.read_text()))
            else:
                runtime_config = RuntimeConfig()

            skill = Skill(
                name=name,
                description=description,
                instructions=post.content.strip(),
                path=skill_dir,
                runtime_config=runtime_config,
            )
            skills.append(skill)
            logger.info(
                "Loaded skill: %s (trigger=%s, ui=%s)",
                name,
                runtime_config.trigger,
                runtime_config.ui_type,
            )

        except Exception:
            logger.exception("Failed to load skill from %s", skill_dir.name)

    return skills
