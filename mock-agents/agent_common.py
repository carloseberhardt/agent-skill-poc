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

_LLM_GATEWAY_URL = os.getenv("LLM_GATEWAY_URL", "http://localhost:4000")
_LLM_API_KEY = os.getenv("LLM_API_KEY", os.getenv("LITELLM_API_KEY", "not-needed"))
_DB_PATH = os.getenv("DEMO_DB_PATH", "./demo.db")

# Model route → actual provider model name.
# The route (env var value) is the path prefix in Agent Gateway.
# The model name is what the provider expects in the request body.
_MODEL_NAMES = {
    "bedrock": "mistral.mistral-large-3-675b-instruct",
    "watsonx": "openai/gpt-oss-120b",
}

_DEFAULT_ROUTE = "bedrock"


def get_llm(model_env_var: str, fallback_route: str = _DEFAULT_ROUTE) -> ChatOpenAI:
    """Create a ChatOpenAI instance routed through Agent Gateway.

    The env var value (e.g. 'watsonx') selects the gateway route path.
    The actual provider model name is resolved from _MODEL_NAMES.
    """
    route = os.getenv(model_env_var, fallback_route)
    model = _MODEL_NAMES.get(route, route)
    base_url = f"{_LLM_GATEWAY_URL}/{route}/v1"
    return ChatOpenAI(
        model=model,
        base_url=base_url,
        api_key=_LLM_API_KEY,
    )


def get_model_name(model_env_var: str, fallback_route: str = _DEFAULT_ROUTE) -> str:
    """Return the display name for this agent's model."""
    route = os.getenv(model_env_var, fallback_route)
    return _MODEL_NAMES.get(route, route)


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
