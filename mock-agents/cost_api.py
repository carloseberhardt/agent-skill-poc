"""
Mock cloud cost API — pure MCP tool server.

Demonstrates the MCP side of the dual-protocol story:
A2A for agents that think, MCP for tools that do.

Run: uv run python mock-agents/cost_api.py
Serves on port 5003.
"""

import logging
import os
import random

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("cost-api", host="0.0.0.0", port=5003)

wire = logging.getLogger("wire")
if os.getenv("WIRE_LOG") == "true":
    logging.basicConfig(level=logging.INFO)
    wire.setLevel(logging.INFO)
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("\033[32m%(asctime)s [wire:cost] %(message)s\033[0m", datefmt="%H:%M:%S"))
    wire.addHandler(_h)
    wire.propagate = False
else:
    wire.setLevel(logging.WARNING)

# Base cost data — randomized at query time
_BASE_SERVICES = {
    "Spark": {"current_month": 42000, "previous_month": 10500},
    "S3": {"current_month": 8200, "previous_month": 7900},
    "Redshift": {"current_month": 15400, "previous_month": 14800},
    "Lambda": {"current_month": 3100, "previous_month": 3200},
    "EKS": {"current_month": 12600, "previous_month": 12100},
}

# Different anomaly scenarios that rotate each call
_ANOMALY_SCENARIOS = [
    {
        "service": "Spark",
        "multiplier": 3.5,
        "project": "Alpha",
        "note": "Spark costs up ~300% — 3 new jobs created this month",
    },
    {
        "service": "Redshift",
        "multiplier": 2.8,
        "project": "Beta",
        "note": "Redshift costs surged — unoptimized queries from new analytics dashboard",
    },
    {
        "service": "EKS",
        "multiplier": 3.0,
        "project": "Alpha",
        "note": "EKS costs spiked — autoscaler over-provisioned GPU nodes for ML training",
    },
    {
        "service": "Lambda",
        "multiplier": 4.0,
        "project": "Gamma",
        "note": "Lambda invocations exploded — recursive trigger bug in event pipeline",
    },
]


def _generate_cost_data() -> dict:
    """Build cost data with light randomization and a rotating anomaly."""
    scenario = random.choice(_ANOMALY_SCENARIOS)

    services = {}
    for name, base in _BASE_SERVICES.items():
        # ±15% jitter on base values
        jitter = random.uniform(0.85, 1.15)
        current = round(base["current_month"] * jitter)
        previous = round(base["previous_month"] * jitter)

        # Apply the anomaly multiplier to the chosen service
        if name == scenario["service"]:
            current = round(base["previous_month"] * scenario["multiplier"] * jitter)

        change_pct = round((current - previous) / previous * 100, 1) if previous else 0
        services[name] = {"current_month": current, "previous_month": previous, "change_pct": change_pct}

    # Build project totals from service data
    projects = {
        "Alpha": {
            "total": services["Spark"]["current_month"] + services["EKS"]["current_month"],
            "top_service": max(["Spark", "EKS"], key=lambda s: services[s]["current_month"]),
        },
        "Beta": {
            "total": services["Redshift"]["current_month"] + services["Lambda"]["current_month"],
            "top_service": max(["Redshift", "Lambda"], key=lambda s: services[s]["current_month"]),
        },
        "Gamma": {
            "total": services["S3"]["current_month"] + services["Lambda"]["current_month"],
            "top_service": max(["S3", "Lambda"], key=lambda s: services[s]["current_month"]),
        },
    }

    # Attach anomaly note to the affected project
    projects[scenario["project"]]["note"] = scenario["note"]
    for pname in projects:
        if "note" not in projects[pname]:
            projects[pname]["note"] = "Steady, no anomalies"

    return {"services": services, "projects": projects}


@mcp.tool()
def get_cost_data(
    service: str | None = None,
    project: str | None = None,
    period: str = "current_month",
) -> dict:
    """Get cloud infrastructure cost data.

    Returns cost breakdowns by service and project, including month-over-month
    change percentages. Useful for detecting cost anomalies and budget overruns.

    Args:
        service: Filter by service name (e.g. "Spark", "S3", "Redshift"). Optional.
        project: Filter by project name (e.g. "Alpha", "Beta"). Optional.
        period: Time period — "current_month" or "previous_month". Default: current_month.
    """
    wire.info("◀ get_cost_data(service=%s, project=%s, period=%s)", service, project, period)
    cost_data = _generate_cost_data()
    result = {}

    if service:
        svc = cost_data["services"].get(service)
        if svc:
            result["service"] = {service: svc}
        else:
            result["error"] = f"Unknown service: {service}"
            result["available_services"] = list(cost_data["services"].keys())
    elif project:
        proj = cost_data["projects"].get(project)
        if proj:
            result["project"] = {project: proj}
        else:
            result["error"] = f"Unknown project: {project}"
            result["available_projects"] = list(cost_data["projects"].keys())
    else:
        result["services"] = cost_data["services"]
        result["projects"] = cost_data["projects"]
        result["total_spend"] = sum(
            s["current_month"] for s in cost_data["services"].values()
        )
        result["anomalies"] = [
            f"{name}: costs up {data['change_pct']}% month-over-month"
            for name, data in cost_data["services"].items()
            if abs(data["change_pct"]) > 20
        ]

    anomalies = result.get("anomalies", [])
    wire.info("▶ cost_data → total=$%s, anomalies=%d", result.get("total_spend", "?"), len(anomalies))
    return result


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
