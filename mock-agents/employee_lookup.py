"""
Mock employee directory — pure MCP tool server.

Creates cross-domain connections: security flags "jdoe" -> employee
lookup resolves who they are, what department, who manages them.

Run: uv run python mock-agents/employee_lookup.py
Serves on port 5004.
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("employee-lookup", host="0.0.0.0", port=5004)

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
}


@mcp.tool()
def lookup_employee(name_or_id: str) -> dict:
    """Look up an employee by username, ID, or partial name.

    Returns employee details including department, role, manager, and clearance level.
    Useful for resolving user IDs from security alerts or access logs.

    Args:
        name_or_id: Employee username (e.g. "jdoe"), ID, or partial name to search for.
    """
    key = name_or_id.lower().strip()

    # Direct ID match
    if key in _EMPLOYEES:
        return _EMPLOYEES[key]

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
