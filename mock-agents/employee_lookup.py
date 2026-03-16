"""
Employee directory — pure MCP tool server backed by SQLite.

Resolves user IDs from security alerts, finds managers, lists on-call staff.

Run: uv run python mock-agents/employee_lookup.py
Serves on port 5004.
"""

import logging
import os
import sqlite3

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

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

_DB_PATH = os.getenv("DEMO_DB_PATH", "./demo.db")


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@mcp.tool()
def lookup_employee(name_or_id: str) -> dict:
    """Look up an employee by username, ID, or partial name.

    Returns employee details including department, role, manager, and clearance level.
    Useful for resolving user IDs from security alerts or access logs.

    Args:
        name_or_id: Employee username (e.g. "jliu"), ID, or partial name to search for.
    """
    wire.info("◀ lookup_employee(%s)", name_or_id)
    key = name_or_id.lower().strip()

    conn = _get_db()
    try:
        # Direct ID match
        row = conn.execute("SELECT * FROM employees WHERE id = ?", (key,)).fetchone()
        if row:
            emp = dict(row)
            # Resolve manager name
            if emp.get("manager_id"):
                mgr = conn.execute("SELECT name FROM employees WHERE id = ?", (emp["manager_id"],)).fetchone()
                emp["manager_name"] = mgr["name"] if mgr else emp["manager_id"]
            wire.info("▶ found → %s (%s, %s)", emp["name"], emp["role"], emp["department"])
            return emp

        # Partial name/id search
        rows = conn.execute(
            "SELECT * FROM employees WHERE LOWER(name) LIKE ? OR LOWER(id) LIKE ?",
            (f"%{key}%", f"%{key}%"),
        ).fetchall()

        if len(rows) == 1:
            emp = dict(rows[0])
            if emp.get("manager_id"):
                mgr = conn.execute("SELECT name FROM employees WHERE id = ?", (emp["manager_id"],)).fetchone()
                emp["manager_name"] = mgr["name"] if mgr else emp["manager_id"]
            wire.info("▶ found → %s (%s, %s)", emp["name"], emp["role"], emp["department"])
            return emp

        if rows:
            matches = [{"id": r["id"], "name": r["name"], "department": r["department"], "role": r["role"]} for r in rows]
            return {"matches": matches, "note": "Multiple matches found. Use a specific ID for full details."}

        # List available IDs on miss
        all_ids = [r["id"] for r in conn.execute("SELECT id FROM employees").fetchall()]
        return {"error": f"No employee found matching '{name_or_id}'", "available_ids": all_ids}
    finally:
        conn.close()


@mcp.tool()
def list_on_call() -> dict:
    """List all employees currently on call, grouped by role.

    Returns on-call staff with their contact info and role.
    Useful for incident response — find who to notify.
    """
    wire.info("◀ list_on_call()")
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT id, name, role, department, on_call_role, email, discord_handle "
            "FROM employees WHERE on_call_role IS NOT NULL"
        ).fetchall()
        result = [dict(r) for r in rows]
        wire.info("▶ on_call → %d staff", len(result))
        return {"on_call": result, "count": len(result)}
    finally:
        conn.close()


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
