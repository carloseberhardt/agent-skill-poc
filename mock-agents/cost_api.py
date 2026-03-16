"""
Cloud cost API — pure MCP tool server backed by SQLite.

Returns cost data by service/project/period. Detects anomalies from actual data.

Run: uv run python mock-agents/cost_api.py
Serves on port 5003.
"""

import logging
import os
import sqlite3

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

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

_DB_PATH = os.getenv("DEMO_DB_PATH", "./demo.db")


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@mcp.tool()
def get_cost_data(
    service: str | None = None,
    project: str | None = None,
) -> dict:
    """Get cloud infrastructure cost data from the cost database.

    Returns cost breakdowns by service including spend, budget, month-over-month
    change, and anomaly flags. Can filter by service or project name.

    Args:
        service: Filter by service name (e.g. "payments-api"). Optional.
        project: Filter by project/team name (e.g. "Data Platform"). Optional.
    """
    wire.info("◀ get_cost_data(service=%s, project=%s)", service, project)
    conn = _get_db()
    try:
        if service:
            rows = conn.execute(
                "SELECT * FROM cost_records WHERE service = ? ORDER BY period DESC", (service,)
            ).fetchall()
            if not rows:
                all_services = [r["service"] for r in conn.execute("SELECT DISTINCT service FROM cost_records").fetchall()]
                return {"error": f"Unknown service: {service}", "available_services": all_services}
            records = [dict(r) for r in rows]
            return {"service": service, "records": records}

        if project:
            rows = conn.execute(
                "SELECT * FROM cost_records WHERE project = ? ORDER BY period DESC", (project,)
            ).fetchall()
            if not rows:
                all_projects = [r["project"] for r in conn.execute("SELECT DISTINCT project FROM cost_records").fetchall()]
                return {"error": f"Unknown project: {project}", "available_projects": all_projects}
            records = [dict(r) for r in rows]
            total_current = sum(r["spend"] for r in records if r["period"] == records[0]["period"])
            return {"project": project, "records": records, "total_current_spend": total_current}

        # All services — current period summary with anomalies
        # Get the most recent period
        latest = conn.execute("SELECT MAX(period) as p FROM cost_records").fetchone()["p"]
        prev_row = conn.execute(
            "SELECT DISTINCT period FROM cost_records WHERE period < ? ORDER BY period DESC LIMIT 1", (latest,)
        ).fetchone()
        prev_period = prev_row["period"] if prev_row else None

        services = {}
        current_rows = conn.execute(
            "SELECT * FROM cost_records WHERE period = ?", (latest,)
        ).fetchall()

        total_spend = 0
        total_budget = 0
        anomalies = []

        for row in current_rows:
            svc = row["service"]
            spend = row["spend"]
            budget = row["budget"] or 0
            total_spend += spend
            total_budget += budget

            entry = {
                "spend": spend,
                "budget": budget,
                "over_budget": spend > budget if budget else False,
                "budget_pct": round(spend / budget * 100, 1) if budget else None,
                "notes": row["notes"],
            }

            # Get previous period for MoM comparison
            if prev_period:
                prev = conn.execute(
                    "SELECT spend FROM cost_records WHERE service = ? AND period = ?",
                    (svc, prev_period),
                ).fetchone()
                if prev:
                    change_pct = round((spend - prev["spend"]) / prev["spend"] * 100, 1)
                    entry["previous_spend"] = prev["spend"]
                    entry["change_pct"] = change_pct
                    if abs(change_pct) > 15 or spend > budget:
                        anomalies.append(
                            f"{svc}: ${spend:,.0f} (budget ${budget:,.0f}, "
                            f"{'+' if change_pct > 0 else ''}{change_pct}% MoM)"
                        )

            services[svc] = entry

        result = {
            "period": latest,
            "services": services,
            "total_spend": total_spend,
            "total_budget": total_budget,
            "anomalies": anomalies,
        }
        wire.info("▶ cost_data → total=$%s, anomalies=%d", total_spend, len(anomalies))
        return result
    finally:
        conn.close()


@mcp.tool()
def get_budget_status() -> dict:
    """Get a quick budget vs actual summary for all services.

    Returns each service's current spend relative to budget,
    flagging any over-budget services. Good for a quick health check.
    """
    wire.info("◀ get_budget_status()")
    conn = _get_db()
    try:
        latest = conn.execute("SELECT MAX(period) as p FROM cost_records").fetchone()["p"]
        rows = conn.execute(
            "SELECT service, spend, budget, notes FROM cost_records WHERE period = ?", (latest,)
        ).fetchall()

        statuses = []
        for row in rows:
            budget = row["budget"] or 0
            spend = row["spend"]
            status = "over" if budget and spend > budget else "ok"
            statuses.append({
                "service": row["service"],
                "spend": spend,
                "budget": budget,
                "status": status,
                "pct_of_budget": round(spend / budget * 100, 1) if budget else None,
                "notes": row["notes"],
            })

        over_count = sum(1 for s in statuses if s["status"] == "over")
        wire.info("▶ budget_status → %d services, %d over budget", len(statuses), over_count)
        return {"period": latest, "services": statuses, "over_budget_count": over_count}
    finally:
        conn.close()


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
