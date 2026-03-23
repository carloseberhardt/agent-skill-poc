"""
Delivery status agent — demonstrates full async A2A lifecycle.

No LLM — this is a deterministic state machine with hardcoded tracking data.
It demonstrates three A2A interaction patterns:

  Flow A (async/push): "Tell me when package 241234 is delivered"
    → start_work immediately, sleep 30-150s, then complete with push notification

  Flow B (sync): "Where is my package 241234?"
    → immediate completed response with current status

  Flow C (input-required → cancel): "When will my package arrive?"
    → input-required ("What's your tracking number?")
    → follow-up with number → complete, or "cancel" → canceled

Uses a2a-sdk TaskUpdater for spec-compliant state management and
BasePushNotificationSender for push notification delivery.

Run: uv run python mock-agents/delivery_agent.py
Serves on port 5006.
"""

import asyncio
import random
import re

import httpx
import uvicorn
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import (
    BasePushNotificationSender,
    InMemoryPushNotificationConfigStore,
    InMemoryTaskStore,
    TaskUpdater,
)
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    Part,
    TextPart,
)
from a2a.utils.message import new_agent_text_message

from agent_common import setup_wire_logger

_PORT = 5006

wire = setup_wire_logger("delivery", "36")

# ── Simulated tracking data ──────────────────────────────────

PACKAGES = {
    "241234": {
        "status": "in-transit",
        "location": "Chicago, IL — Distribution Center",
        "carrier": "FedEx",
        "eta": "March 21, 2026",
        "destination": "Austin, TX",
    },
    "891011": {
        "status": "out-for-delivery",
        "location": "Austin, TX — On delivery vehicle",
        "carrier": "UPS",
        "eta": "Today by 5pm",
        "destination": "Austin, TX",
    },
    "334455": {
        "status": "delivered",
        "location": "Front porch",
        "carrier": "USPS",
        "eta": "Delivered March 19, 2026",
        "destination": "Denver, CO",
    },
    "667788": {
        "status": "in-transit",
        "location": "Memphis, TN — Sorting Facility",
        "carrier": "FedEx",
        "eta": "March 23, 2026",
        "destination": "Seattle, WA",
    },
    "990011": {
        "status": "label-created",
        "location": "Awaiting pickup",
        "carrier": "DHL",
        "eta": "Pending pickup",
        "destination": "New York, NY",
    },
}


def _extract_tracking(text: str) -> str | None:
    """Extract a tracking number from user input."""
    match = re.search(r"\b(\d{5,})\b", text)
    return match.group(1) if match else None


def _is_notify_request(text: str) -> bool:
    """Check if the user wants to be notified on delivery."""
    lower = text.lower()
    return any(phrase in lower for phrase in [
        "tell me when", "notify me when", "let me know when",
        "alert me when", "notify when", "tell me once",
        "when it's delivered", "when it is delivered",
        "when my package is delivered",
    ])


def _is_cancel(text: str) -> bool:
    """Check if the user wants to cancel."""
    lower = text.lower()
    return any(phrase in lower for phrase in [
        "cancel", "never mind", "nevermind", "forget it",
        "don't bother", "stop tracking", "no thanks",
    ])


def _format_status(tracking: str, pkg: dict) -> str:
    """Format a package status as readable text."""
    return (
        f"Package {tracking} ({pkg['carrier']}):\n"
        f"  Status: {pkg['status']}\n"
        f"  Location: {pkg['location']}\n"
        f"  Destination: {pkg['destination']}\n"
        f"  ETA: {pkg['eta']}"
    )


# ── A2A executor ──────────────────────────────────────────────

class DeliveryAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user_input = context.get_user_input() or ""
        wire.info("◀ received: %s", user_input[:150])

        task_updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        tracking = _extract_tracking(user_input)

        # Flow C: cancel request
        if _is_cancel(user_input):
            wire.info("→ cancel flow")
            await task_updater.cancel(
                message=new_agent_text_message("Tracking request canceled. No problem!")
            )
            return

        # Flow A: async delivery notification (tracking number + notify request)
        if tracking and _is_notify_request(user_input):
            pkg = PACKAGES.get(tracking)
            if not pkg:
                await task_updater.complete(
                    message=new_agent_text_message(
                        f"No package found with tracking number {tracking}. "
                        f"Valid tracking numbers: {', '.join(PACKAGES.keys())}"
                    )
                )
                return

            if pkg["status"] == "delivered":
                await task_updater.complete(
                    message=new_agent_text_message(
                        f"Good news — package {tracking} was already delivered! "
                        f"Location: {pkg['location']}"
                    )
                )
                return

            # Start async tracking — this sets task to "working" and returns
            # to the non-blocking caller. The executor keeps running in the background.
            wire.info("→ async flow: tracking %s, will notify on delivery", tracking)
            await task_updater.start_work(
                message=new_agent_text_message(
                    f"Now tracking package {tracking} ({pkg['carrier']}). "
                    f"Currently: {pkg['status']} at {pkg['location']}. "
                    f"I'll notify you when it's delivered."
                )
            )

            # Simulate delivery delay
            delay = random.uniform(30, 150)
            wire.info("→ simulating delivery in %.0fs", delay)
            await asyncio.sleep(delay)

            # Package "delivered" — add artifact with delivery details and complete
            wire.info("→ delivery complete for %s", tracking)
            await task_updater.add_artifact(
                parts=[Part(root=TextPart(
                    text=(
                        f"Package {tracking} has been delivered!\n"
                        f"Carrier: {pkg['carrier']}\n"
                        f"Delivered to: {pkg['destination']}\n"
                        f"Left at: Front door"
                    )
                ))],
                name="delivery-confirmation",
            )
            await task_updater.complete(
                message=new_agent_text_message(
                    f"Package {tracking} has been delivered to {pkg['destination']}."
                )
            )
            return

        # Flow B: synchronous status check
        if tracking:
            pkg = PACKAGES.get(tracking)
            if not pkg:
                await task_updater.complete(
                    message=new_agent_text_message(
                        f"No package found with tracking number {tracking}. "
                        f"Valid tracking numbers: {', '.join(PACKAGES.keys())}"
                    )
                )
                return

            wire.info("→ sync status check for %s", tracking)
            status_text = _format_status(tracking, pkg)
            await task_updater.add_artifact(
                parts=[Part(root=TextPart(text=status_text))],
                name="package-status",
            )
            await task_updater.complete(
                message=new_agent_text_message(status_text)
            )
            return

        # Flow C: no tracking number — ask for it
        wire.info("→ input-required flow")
        await task_updater.requires_input(
            message=new_agent_text_message(
                "I'd be happy to help with your package! "
                "What's your tracking number?"
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await task_updater.cancel(
            message=new_agent_text_message("Tracking request canceled.")
        )


def build_app():
    agent_card = AgentCard(
        name="delivery_agent",
        description=(
            "Delivery tracking agent. Tracks package delivery status and sends "
            "notifications when packages are delivered. Supports real-time status "
            "checks and async delivery monitoring with push notifications."
        ),
        url=f"http://localhost:{_PORT}",
        version="0.3.0",
        defaultInputModes=["text/plain"],
        defaultOutputModes=["text/plain"],
        capabilities=AgentCapabilities(
            streaming=False,
            push_notifications=True,
        ),
        skills=[
            AgentSkill(
                id="track_delivery",
                name="Track Delivery",
                description=(
                    "Track package delivery status. Can check current location and status "
                    "of a package by tracking number, or set up a notification to alert "
                    "when a package is delivered. Supports async monitoring — ask to be "
                    "notified and the agent will push an update when delivery completes."
                ),
                tags=["delivery", "tracking", "packages", "shipping", "notifications"],
                examples=[
                    "Where is my package 241234?",
                    "Tell me when package 891011 is delivered",
                    "What's the status of tracking number 667788?",
                    "When will my package arrive?",
                ],
            ),
        ],
    )

    # Push notification infrastructure — the SDK handles the rest
    push_config_store = InMemoryPushNotificationConfigStore()
    push_sender = BasePushNotificationSender(
        httpx_client=httpx.AsyncClient(timeout=10),
        config_store=push_config_store,
    )

    executor = DeliveryAgentExecutor()
    request_handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
        push_config_store=push_config_store,
        push_sender=push_sender,
    )

    server = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=request_handler,
    )
    return server.build()


app = build_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=_PORT)
