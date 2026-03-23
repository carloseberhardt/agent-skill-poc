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
    ui_type: Literal["chat", "card", "form", "approval", "none"] = "card"


class SkillResult(BaseModel):
    skill_name: str
    ui_type: str
    content: dict[str, Any]
    timestamp: datetime
    trigger_type: str = "manual"
    trigger_source: str | None = None


class Skill(BaseModel):
    """A skill loaded from a SKILL.md directory.

    Follows the Agent Skills spec's progressive disclosure model:
      1. Discovery — name + description loaded at startup (lightweight)
      2. Activation — full instructions read from SKILL.md on first invoke
      3. Execution — agent follows instructions with tools

    Instructions are NOT loaded at discovery time. Call get_instructions()
    when the skill is actually invoked.
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: str
    path: Path
    runtime_config: RuntimeConfig

    # Cached instructions — None until first activation
    _instructions_cache: str | None = None

    def get_instructions(self) -> str:
        """Activate the skill: read full SKILL.md instructions from disk.

        Cached after first read so repeated invocations don't hit disk.
        """
        if self._instructions_cache is not None:
            return self._instructions_cache

        skill_md_path = self.path / "SKILL.md"
        if not skill_md_path.exists():
            logger.warning("SKILL.md not found at activation time: %s", skill_md_path)
            return ""

        post = frontmatter.load(str(skill_md_path))
        self._instructions_cache = post.content.strip()
        logger.info("Skill activated (instructions loaded): %s", self.name)
        return self._instructions_cache

    def invalidate_cache(self) -> None:
        """Clear cached instructions — used after hot-reload."""
        self._instructions_cache = None


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

            # Discovery: load only name + description (not the full instructions).
            # Instructions are read on demand when the skill is invoked —
            # this follows the Agent Skills progressive disclosure model.
            skill = Skill(
                name=name,
                description=description,
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
