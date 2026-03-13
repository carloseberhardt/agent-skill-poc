"""
Mock cloud cost API — pure MCP tool server.

Demonstrates the MCP side of the dual-protocol story:
A2A for agents that think, MCP for tools that do.

Run: uv run python mock-agents/cost_api.py
Serves on port 5003.
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("cost-api", host="0.0.0.0", port=5003)

# Mock cost data with a deliberate anomaly for the cost-anomaly skill to find
_COST_DATA = {
    "services": {
        "Spark": {"current_month": 42000, "previous_month": 10500, "change_pct": 300},
        "S3": {"current_month": 8200, "previous_month": 7900, "change_pct": 3.8},
        "Redshift": {"current_month": 15400, "previous_month": 14800, "change_pct": 4.1},
        "Lambda": {"current_month": 3100, "previous_month": 3200, "change_pct": -3.1},
        "EKS": {"current_month": 12600, "previous_month": 12100, "change_pct": 4.1},
    },
    "projects": {
        "Alpha": {
            "total": 48200,
            "top_service": "Spark",
            "spark_cost": 38000,
            "note": "Spark costs up 300% — 3 new jobs created this month",
        },
        "Beta": {
            "total": 18500,
            "top_service": "Redshift",
            "redshift_cost": 11200,
            "note": "Steady, no anomalies",
        },
        "Gamma": {
            "total": 14600,
            "top_service": "S3",
            "s3_cost": 6800,
            "note": "Steady, no anomalies",
        },
    },
}


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
    result = {}

    if service:
        svc = _COST_DATA["services"].get(service)
        if svc:
            result["service"] = {service: svc}
        else:
            result["error"] = f"Unknown service: {service}"
            result["available_services"] = list(_COST_DATA["services"].keys())
    elif project:
        proj = _COST_DATA["projects"].get(project)
        if proj:
            result["project"] = {project: proj}
        else:
            result["error"] = f"Unknown project: {project}"
            result["available_projects"] = list(_COST_DATA["projects"].keys())
    else:
        result["services"] = _COST_DATA["services"]
        result["projects"] = _COST_DATA["projects"]
        result["total_spend"] = sum(
            s["current_month"] for s in _COST_DATA["services"].values()
        )
        result["anomalies"] = [
            f"{name}: costs up {data['change_pct']}% month-over-month"
            for name, data in _COST_DATA["services"].items()
            if abs(data["change_pct"]) > 20
        ]

    return result


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
