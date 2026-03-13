"""
Mock employee directory — pure MCP tool server.

Creates cross-domain connections: security flags "jdoe" -> employee
lookup resolves who they are, what department, who manages them.

Run: uv run python mock-agents/employee_lookup.py
Serves on port 5004.
"""

import logging
import os

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("employee-lookup", host="0.0.0.0", port=5004)

wire = logging.getLogger("wire")
if os.getenv("WIRE_LOG") == "true":
    logging.basicConfig(level=logging.INFO)
    wire.setLevel(logging.INFO)
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("\033[34m%(asctime)s [wire:employee] %(message)s\033[0m", datefmt="%H:%M:%S"))
    wire.addHandler(_h)
    wire.propagate = False
else:
    wire.setLevel(logging.WARNING)

_EMPLOYEES = {
    "jdoe": {
        "id": "jdoe",
        "name": "Jane Doe",
        "department": "Finance",
        "role": "Senior Financial Analyst",
        "manager": "Robert Chen",
        "location": "New York",
        "clearance": "confidential",
        "notes": "Authorized for financial datasets. Recent project: Q1 audit preparation.",
    },
    "msmith": {
        "id": "msmith",
        "name": "Marcus Smith",
        "department": "Data Engineering",
        "role": "Staff Data Engineer",
        "manager": "Sarah Kim",
        "location": "Austin",
        "clearance": "top-secret",
        "notes": "Lead on customer_360 pipeline. Authorized for bulk data exports.",
    },
    "rchen": {
        "id": "rchen",
        "name": "Robert Chen",
        "department": "Finance",
        "role": "VP of Finance",
        "manager": "Elena Vasquez",
        "location": "New York",
        "clearance": "top-secret",
        "notes": "Department head. Approver for financial data access requests.",
    },
    "skim": {
        "id": "skim",
        "name": "Sarah Kim",
        "department": "Data Engineering",
        "role": "Director of Data Engineering",
        "manager": "Elena Vasquez",
        "location": "Austin",
        "clearance": "top-secret",
        "notes": "Manages data platform team. Escalation contact for data incidents.",
    },
    "agarcia": {
        "id": "agarcia",
        "name": "Ana Garcia",
        "department": "People Analytics",
        "role": "HR Data Analyst",
        "manager": "Robert Chen",
        "location": "Chicago",
        "clearance": "confidential",
        "notes": "Authorized for HR and compensation datasets. Leads quarterly workforce reporting.",
    },
    "tpatel": {
        "id": "tpatel",
        "name": "Tariq Patel",
        "department": "Machine Learning",
        "role": "ML Engineer",
        "manager": "Sarah Kim",
        "location": "Seattle",
        "clearance": "secret",
        "notes": "Builds and deploys production ML models. Temporary staging write access for model rollout.",
    },
    "kwong": {
        "id": "kwong",
        "name": "Kevin Wong",
        "department": "Product",
        "role": "Product Manager",
        "manager": "Elena Vasquez",
        "location": "San Francisco",
        "clearance": "confidential",
        "notes": "Cross-functional PM for data products. Recently onboarded to ML model registry.",
    },
}


@mcp.tool()
def lookup_employee(name_or_id: str) -> dict:
    """Look up an employee by username, ID, or partial name.

    Returns employee details including department, role, manager, and clearance level.
    Useful for resolving user IDs from security alerts or access logs.

    Args:
        name_or_id: Employee username (e.g. "jdoe"), ID, or partial name to search for.
    """
    wire.info("◀ lookup_employee(%s)", name_or_id)
    key = name_or_id.lower().strip()

    # Direct ID match
    if key in _EMPLOYEES:
        emp = _EMPLOYEES[key]
        wire.info("▶ found → %s (%s, %s)", emp["name"], emp["role"], emp["department"])
        return emp

    # Partial name search
    matches = [
        emp for emp in _EMPLOYEES.values()
        if key in emp["name"].lower() or key in emp["id"].lower()
    ]
    if len(matches) == 1:
        return matches[0]
    if matches:
        return {
            "matches": [{"id": m["id"], "name": m["name"], "department": m["department"]} for m in matches],
            "note": "Multiple matches found. Use a specific ID to get full details.",
        }
    return {"error": f"No employee found matching '{name_or_id}'", "available_ids": list(_EMPLOYEES.keys())}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
