"""
Shared utilities for A2A agents — LLM setup, DB access, wire logging.

Each agent imports what it needs from here. Keeps agent files focused
on their domain logic.
"""

import logging
import os
import sqlite3

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()

_LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "http://localhost:4000")
_LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "")
_DB_PATH = os.getenv("DEMO_DB_PATH", "./demo.db")


def get_llm(model_env_var: str, fallback_model: str = "claude-sonnet-team-b") -> ChatOpenAI:
    """Create a ChatOpenAI instance pointed at LiteLLM with the agent's own model."""
    model = os.getenv(model_env_var, fallback_model)
    return ChatOpenAI(
        model=model,
        base_url=_LITELLM_BASE_URL,
        api_key=_LITELLM_API_KEY,
    )


def get_model_name(model_env_var: str, fallback_model: str = "claude-sonnet-team-b") -> str:
    """Return the model name string for this agent."""
    return os.getenv(model_env_var, fallback_model)


def get_db() -> sqlite3.Connection:
    """Get a SQLite connection with Row factory."""
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def setup_wire_logger(name: str, color_code: str = "33") -> logging.Logger:
    """Set up a colored wire logger for an agent."""
    wire = logging.getLogger(f"wire.{name}")
    if os.getenv("WIRE_LOG") == "true":
        logging.basicConfig(level=logging.INFO)
        wire.setLevel(logging.INFO)
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter(
            f"\033[{color_code}m%(asctime)s [wire:{name}] %(message)s\033[0m",
            datefmt="%H:%M:%S",
        ))
        wire.addHandler(h)
        wire.propagate = False
    else:
        wire.setLevel(logging.WARNING)
    return wire
