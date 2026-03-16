"""
Seed the demo SQLite database with scenario data.

Each run randomly activates a subset of scenario threads, so re-seeding
during a demo produces different agent behavior from the same skills.

Threads (independently toggled):
  Thread 1 — Data exfiltration: suspicious after-hours PII access
  Thread 2 — Normal baseline: always present, healthy services and access
  Thread 3 — Budget creep: costs climbing with no volume increase

Usage:
  uv run python seed_db.py              # random thread selection
  uv run python seed_db.py --all        # all threads active
  uv run python seed_db.py --quiet      # only normal baseline (boring day)
  uv run python seed_db.py --chaos      # all threads active (worst day)

Run: uv run python seed_db.py
"""

import argparse
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path("demo.db")

now = datetime.now(timezone.utc)


def create_tables(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS employees (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            role TEXT NOT NULL,
            department TEXT NOT NULL,
            team TEXT NOT NULL,
            manager_id TEXT,
            email TEXT,
            on_call_role TEXT,
            discord_handle TEXT,
            clearance TEXT DEFAULT 'internal',
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS services (
            name TEXT PRIMARY KEY,
            team TEXT NOT NULL,
            tier INTEGER NOT NULL,
            owner_id TEXT,
            status TEXT DEFAULT 'healthy',
            description TEXT
        );

        CREATE TABLE IF NOT EXISTS service_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            latency_ms REAL,
            error_rate REAL,
            request_count INTEGER,
            connection_count INTEGER,
            cpu_percent REAL,
            notes TEXT,
            FOREIGN KEY (service) REFERENCES services(name)
        );

        CREATE TABLE IF NOT EXISTS security_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            event_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            user_id TEXT,
            source_ip TEXT,
            resource TEXT,
            details TEXT
        );

        CREATE TABLE IF NOT EXISTS datasets (
            name TEXT PRIMARY KEY,
            classification TEXT NOT NULL,
            owner_team TEXT NOT NULL,
            pipeline_status TEXT DEFAULT 'healthy',
            last_refresh TEXT,
            refresh_interval_hours INTEGER DEFAULT 1,
            row_count INTEGER,
            description TEXT
        );

        CREATE TABLE IF NOT EXISTS data_access_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dataset TEXT NOT NULL,
            user_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            query_type TEXT DEFAULT 'select',
            row_count INTEGER DEFAULT 0,
            source_ip TEXT,
            duration_ms INTEGER,
            FOREIGN KEY (dataset) REFERENCES datasets(name),
            FOREIGN KEY (user_id) REFERENCES employees(id)
        );

        CREATE TABLE IF NOT EXISTS cost_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service TEXT NOT NULL,
            project TEXT,
            period TEXT NOT NULL,
            spend REAL NOT NULL,
            budget REAL,
            notes TEXT,
            FOREIGN KEY (service) REFERENCES services(name)
        );
    """)


def seed_employees(conn: sqlite3.Connection):
    """Employees are always the same — they're the cast, not the scenario."""
    employees = [
        ("schen", "Sarah Chen", "Data Engineer", "Engineering", "Data Platform",
         "dtorres", "sarah.chen@acme.com", None, "schen_dev", "secret",
         "Owns ETL pipelines for customer datasets. Reliable, consistent access patterns."),
        ("mwebb", "Marcus Webb", "Security Analyst", "Security", "Security",
         "akim", "marcus.webb@acme.com", "security", "mwebb_sec", "top-secret",
         "On-call security. Primary responder for access anomalies and compliance issues."),
        ("psharma", "Priya Sharma", "Site Reliability Engineer", "Engineering", "Infrastructure",
         "akim", "priya.sharma@acme.com", "ops", "psharma_ops", "secret",
         "On-call SRE. Handles incidents for tier-1 services including payments-api."),
        ("jliu", "James Liu", "Financial Analyst", "Finance", "Finance",
         "dtorres", "james.liu@acme.com", None, "jliu_fin", "internal",
         "Finance team analyst. Typical access is small queries during business hours on financial datasets."),
        ("dtorres", "Dana Torres", "Engineering Manager", "Engineering", "Data Platform",
         "akim", "dana.torres@acme.com", None, "dtorres_mgr", "secret",
         "Manages Data Platform team. James Liu's skip-level manager. Escalation point for data incidents."),
        ("akim", "Alex Kim", "VP Engineering", "Engineering", "Leadership",
         None, "alex.kim@acme.com", None, "akim_vp", "top-secret",
         "VP Engineering. Final escalation for cross-domain incidents."),
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO employees VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        employees,
    )


def seed_services(conn: sqlite3.Connection):
    """Services — always healthy in this simplified demo."""
    services = [
        ("payments-api", "Payments", 1, "psharma", "healthy",
         "Core payment processing API. Handles all transaction flows."),
        ("customer-db", "Data Platform", 1, "schen", "healthy",
         "Primary customer database. Source of truth for customer records."),
        ("analytics-pipeline", "Data Platform", 2, "schen", "healthy",
         "Hourly ETL pipeline feeding analytics and reporting."),
        ("auth-service", "Security", 1, "mwebb", "healthy",
         "Authentication and authorization service. Handles all login flows."),
        ("reporting-dashboard", "Finance", 3, "jliu", "healthy",
         "Internal reporting dashboard for finance and leadership."),
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO services VALUES (?,?,?,?,?,?)",
        services,
    )


def seed_service_metrics(conn: sqlite3.Connection):
    """Metrics for all services — healthy baseline."""
    rows = []

    for hours_ago in range(24, 0, -1):
        ts = (now - timedelta(hours=hours_ago)).isoformat()

        # --- payments-api ---
        latency = 75 + (hours_ago % 5) * 3
        rows.append(("payments-api", ts, latency, 0.001, 15000, None, 45.0, None))

        # --- customer-db ---
        connections = 85 + (hours_ago % 8) * 5
        db_latency = 12 + (hours_ago % 3) * 2
        cpu = 35 + (hours_ago % 6) * 3
        rows.append(("customer-db", ts, db_latency, 0.0, None, connections, cpu, None))

        # --- analytics-pipeline ---
        rows.append(("analytics-pipeline", ts, None, 0.0, None, None, None, None))

        # --- auth-service ---
        auth_latency = 8 + (hours_ago % 4) * 1.5
        rows.append(("auth-service", ts, auth_latency, 0.0005, 8000, None, 22.0, None))

        # --- reporting-dashboard ---
        if 8 <= (24 - hours_ago) % 24 <= 18:
            dash_requests = 200 + (hours_ago % 6) * 30
            dash_latency = 45 + (hours_ago % 3) * 5
        else:
            dash_requests = 10
            dash_latency = 40
        rows.append(("reporting-dashboard", ts, dash_latency, 0.0, dash_requests, None, 12.0, None))

    conn.executemany(
        "INSERT INTO service_metrics (service, timestamp, latency_ms, error_rate, "
        "request_count, connection_count, cpu_percent, notes) VALUES (?,?,?,?,?,?,?,?)",
        rows,
    )


def seed_security_events(conn: sqlite3.Connection, exfiltration: bool):
    """Security events. Exfiltration thread adds the James Liu anomaly."""
    rows = []

    if exfiltration:
        # Pick a random employee to be the suspicious actor? No — James Liu is the
        # character for this, but vary the details slightly each seed.
        suspicious_ip = f"198.51.100.{random.randint(10, 99)}"
        normal_ip = "10.0.1.47"
        nights = random.randint(2, 4)
        base_row_count = random.randint(30000, 60000)

        for days_ago in range(nights, 0, -1):
            access_time = (now - timedelta(days=days_ago)).replace(
                hour=random.randint(1, 4),
                minute=random.randint(0, 59),
                second=0, microsecond=0,
            )
            ip = suspicious_ip if days_ago == 1 else normal_ip
            severity = "critical" if days_ago == 1 else "warning"
            row_count = base_row_count + days_ago * random.randint(1000, 5000)

            rows.append((
                access_time.isoformat(), "after_hours_access", severity, "jliu", ip,
                "customer-pii",
                f"User jliu accessed customer-pii dataset at {access_time.strftime('%H:%M')} "
                f"from IP {ip}. Row count: {row_count}. "
                f"Normal access pattern for this user is <500 rows during business hours."
            ))

        rows.append((
            (now - timedelta(days=1)).replace(hour=3, minute=15).isoformat(),
            "unfamiliar_ip", "critical", "jliu", suspicious_ip,
            "customer-db",
            f"Login from unrecognized IP {suspicious_ip} for user jliu. "
            f"This IP has not been seen in the last 90 days of access logs. "
            f"User's normal IPs: {normal_ip}, 10.0.1.48."
        ))

        rows.append((
            (now - timedelta(days=1)).replace(hour=3, minute=30).isoformat(),
            "data_exfiltration_risk", "critical", "jliu", suspicious_ip,
            "customer-pii",
            f"Bulk data access detected: {base_row_count} rows extracted from customer-pii "
            f"in single session. This is 100x the user's typical query volume. Flagged for review."
        ))

    # Normal/routine security events — always present
    rows.append((
        (now - timedelta(hours=8)).isoformat(),
        "credential_rotation", "info", None, None, "auth-service",
        "Scheduled credential rotation completed for 12 service accounts. All successful."
    ))
    rows.append((
        (now - timedelta(hours=12)).isoformat(),
        "vulnerability_scan", "info", None, None, None,
        "Weekly vulnerability scan completed. No new findings. 2 known low-severity items unchanged."
    ))
    rows.append((
        (now - timedelta(hours=6)).isoformat(),
        "access_review", "info", "schen", "10.0.1.22", "customer-360",
        "Routine access: schen queried customer-360 dataset during business hours. Normal pattern."
    ))
    rows.append((
        (now - timedelta(hours=4)).isoformat(),
        "firewall_audit", "info", None, None, None,
        "Firewall rule audit passed. All rules match approved baseline configuration."
    ))

    conn.executemany(
        "INSERT INTO security_events (timestamp, event_type, severity, user_id, "
        "source_ip, resource, details) VALUES (?,?,?,?,?,?,?)",
        rows,
    )


def seed_datasets(conn: sqlite3.Connection):
    """Dataset catalog — all healthy."""
    datasets = [
        ("customer-pii", "pii", "Data Platform", "healthy",
         (now - timedelta(hours=1)).isoformat(), 1, 2400000,
         "Customer personally identifiable information. Highly restricted access."),
        ("customer-360", "internal", "Data Platform", "healthy",
         (now - timedelta(hours=1)).isoformat(), 1, 8500000,
         "Unified customer view. Aggregated, no raw PII. Broadly accessible."),
        ("sales-events", "internal", "Data Platform", "healthy",
         (now - timedelta(minutes=30)).isoformat(), 1, 45000000,
         "Real-time sales event stream. High volume, used by analytics and ML."),
        ("financial-transactions", "confidential", "Finance", "healthy",
         (now - timedelta(hours=2)).isoformat(), 4, 12000000,
         "Financial transaction records. Restricted to finance team and auditors."),
        ("analytics-summary", "internal", "Data Platform", "healthy",
         (now - timedelta(hours=1)).isoformat(), 1, 950000,
         "Pre-computed analytics rollups. Fed by analytics-pipeline."),
        ("hr-directory", "confidential", "HR", "healthy",
         (now - timedelta(hours=12)).isoformat(), 24, 6200,
         "Employee directory and org chart. Updated daily."),
        ("model-features", "internal", "Data Platform", "healthy",
         (now - timedelta(hours=1)).isoformat(), 1, 3200000,
         "ML feature store. Used by model training and inference pipelines."),
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO datasets VALUES (?,?,?,?,?,?,?,?)",
        datasets,
    )


def seed_data_access_logs(conn: sqlite3.Connection, exfiltration: bool):
    """Access logs. Exfiltration thread adds James Liu's anomalous pattern."""
    rows = []

    if exfiltration:
        suspicious_ip = f"198.51.100.{random.randint(10, 99)}"
        normal_ip = "10.0.1.47"
        nights = random.randint(2, 4)
        base_row_count = random.randint(30000, 60000)

        for days_ago in range(nights, 0, -1):
            access_time = (now - timedelta(days=days_ago)).replace(
                hour=random.randint(1, 4),
                minute=random.randint(0, 59),
                second=0, microsecond=0,
            )
            ip = suspicious_ip if days_ago == 1 else normal_ip
            row_count = base_row_count + days_ago * random.randint(1000, 5000)

            rows.append((
                "customer-pii", "jliu", access_time.isoformat(),
                "select", row_count, ip, 45000 + days_ago * 5000,
            ))

    # James's normal daytime access — always present for contrast
    for days_ago in range(5, 0, -1):
        access_time = (now - timedelta(days=days_ago)).replace(
            hour=10, minute=30, second=0, microsecond=0
        )
        rows.append((
            "financial-transactions", "jliu", access_time.isoformat(),
            "select", 200 + days_ago * 30, "10.0.1.47", 800,
        ))

    # Sarah Chen — normal access pattern, always present
    for hours_ago in range(8, 0, -1):
        ts = (now - timedelta(hours=hours_ago)).isoformat()
        rows.append((
            "customer-360", "schen", ts, "select", 150 + hours_ago * 20,
            "10.0.1.22", 200 + hours_ago * 50,
        ))

    for hours_ago in range(12, 0, -2):
        ts = (now - timedelta(hours=hours_ago)).isoformat()
        rows.append((
            "sales-events", "schen", ts, "select", 5000 + hours_ago * 100,
            "10.0.1.22", 1200,
        ))

    # Other normal access
    rows.append((
        "hr-directory", "mwebb", (now - timedelta(hours=3)).isoformat(),
        "select", 15, "10.0.2.11", 50,
    ))
    rows.append((
        "analytics-summary", "psharma", (now - timedelta(hours=1)).isoformat(),
        "select", 500, "10.0.3.5", 300,
    ))

    conn.executemany(
        "INSERT INTO data_access_logs (dataset, user_id, timestamp, query_type, "
        "row_count, source_ip, duration_ms) VALUES (?,?,?,?,?,?,?)",
        rows,
    )


def seed_cost_records(conn: sqlite3.Connection, budget_creep: bool):
    """Cost records. Budget creep inflates analytics-pipeline."""
    rows = []

    months = [
        (now.replace(day=1), "current"),
        ((now.replace(day=1) - timedelta(days=1)).replace(day=1), "month-1"),
        ((now.replace(day=1) - timedelta(days=32)).replace(day=1), "month-2"),
        ((now.replace(day=1) - timedelta(days=63)).replace(day=1), "month-3"),
    ]

    # payments-api
    payments_budget = 18000
    payments_costs = [16500, 17500, 17200, 16800]
    for (month_dt, label), spend in zip(months, payments_costs):
        period = month_dt.strftime("%Y-%m")
        rows.append(("payments-api", "Payments", period, spend, payments_budget, None))

    # analytics-pipeline
    pipeline_budget = 8000
    if budget_creep:
        pipeline_costs = [9200, 8000, 6950, 6050]
        pipeline_note = "15% MoM increase — no corresponding data volume growth"
    else:
        pipeline_costs = [7200, 7000, 6950, 6800]
        pipeline_note = None
    for (month_dt, label), spend in zip(months, pipeline_costs):
        period = month_dt.strftime("%Y-%m")
        notes = pipeline_note if label == "current" else None
        rows.append(("analytics-pipeline", "Data Platform", period, spend, pipeline_budget, notes))

    # customer-db
    cdb_budget = 12000
    cdb_costs = [11200, 11500, 11200, 11000]
    for (month_dt, label), spend in zip(months, cdb_costs):
        period = month_dt.strftime("%Y-%m")
        rows.append(("customer-db", "Data Platform", period, spend, cdb_budget, None))

    # auth-service — always under budget
    auth_budget = 5000
    auth_costs = [3200, 3100, 3150, 3050]
    for (month_dt, label), spend in zip(months, auth_costs):
        period = month_dt.strftime("%Y-%m")
        rows.append(("auth-service", "Security", period, spend, auth_budget, None))

    # reporting-dashboard — always well under budget
    dash_budget = 2000
    dash_costs = [850, 800, 820, 790]
    for (month_dt, label), spend in zip(months, dash_costs):
        period = month_dt.strftime("%Y-%m")
        rows.append(("reporting-dashboard", "Finance", period, spend, dash_budget, None))

    conn.executemany(
        "INSERT INTO cost_records (service, project, period, spend, budget, notes) "
        "VALUES (?,?,?,?,?,?)",
        rows,
    )


def main():
    parser = argparse.ArgumentParser(description="Seed demo.db with scenario data")
    parser.add_argument("--all", "--chaos", action="store_true",
                        help="Activate all scenario threads (worst day)")
    parser.add_argument("--quiet", action="store_true",
                        help="Only normal baseline (boring day)")
    args = parser.parse_args()

    # Decide which threads are active
    if args.quiet:
        exfiltration = False
        budget_creep = False
    elif args.all:
        exfiltration = True
        budget_creep = True
    else:
        # Random selection — each thread independently toggled
        exfiltration = random.random() < 0.5
        budget_creep = random.random() < 0.4

    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(str(DB_PATH))
    try:
        create_tables(conn)
        seed_employees(conn)
        seed_services(conn)
        seed_service_metrics(conn)
        seed_security_events(conn, exfiltration=exfiltration)
        seed_datasets(conn)
        seed_data_access_logs(conn, exfiltration=exfiltration)
        seed_cost_records(conn, budget_creep=budget_creep)
        conn.commit()

        # Print summary
        tables = ["employees", "services", "service_metrics", "security_events",
                   "datasets", "data_access_logs", "cost_records"]
        print(f"Created {DB_PATH}\n")
        for table in tables:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  {table}: {count} rows")

        print(f"\nActive threads:")
        print(f"  {'✓' if exfiltration else '·'} Data exfiltration (suspicious PII access)")
        print(f"  ✓ Normal baseline (always on)")
        print(f"  {'✓' if budget_creep else '·'} Budget creep (analytics-pipeline cost growth)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
