"""
Discord notification tool — pure MCP tool server.

Posts messages to a Discord channel via webhook.
If DISCORD_WEBHOOK_URL is not set, logs the message and returns a mock success.

Run: uv run python mock-agents/discord_notifier.py
Serves on port 5005.
"""

import logging
import os
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

mcp = FastMCP("discord-notifier", host="0.0.0.0", port=5005)

wire = logging.getLogger("wire")
if os.getenv("WIRE_LOG") == "true":
    logging.basicConfig(level=logging.INFO)
    wire.setLevel(logging.INFO)
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("\033[33m%(asctime)s [wire:discord] %(message)s\033[0m", datefmt="%H:%M:%S"))
    wire.addHandler(_h)
    wire.propagate = False
else:
    wire.setLevel(logging.WARNING)

_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")


@mcp.tool()
def send_discord_message(message: str) -> dict:
    """Send a notification message to Discord.

    Posts a formatted message to the configured Discord webhook channel.
    Use for incident notifications, escalation alerts, or status updates.

    Args:
        message: The message to post. Supports Discord markdown formatting.
    """
    wire.info("◀ send_discord_message(msg=%s)", message[:100])
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    formatted = f"**[Solis Runtime]** ({ts})\n{message}"

    if not _WEBHOOK_URL:
        wire.info("▶ discord (mock) → message logged, no webhook configured")
        return {
            "status": "mock_sent",
            "message_preview": message[:200],
            "note": "DISCORD_WEBHOOK_URL not set — message logged but not delivered to Discord.",
        }

    try:
        resp = httpx.post(
            _WEBHOOK_URL,
            json={"content": formatted},
            timeout=10,
        )
        resp.raise_for_status()
        wire.info("▶ discord → sent to webhook (status %d)", resp.status_code)
        return {
            "status": "sent",
            "message_preview": message[:200],
        }
    except Exception as e:
        wire.info("▶ discord → FAILED: %s", str(e))
        return {
            "status": "error",
            "error": str(e),
            "message_preview": message[:200],
        }


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
