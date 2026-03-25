"""
Seed the demo SQLite database with scenario data.

Two independent scenario threads, plus a normal baseline that's always present.
The threads are designed so each monitor catches something different, and the
incident-correlation skill only finds the "aha!" when both are active together.

Threads (independently toggled):
  Thread 1 — Data anomaly: James Liu pulling bulk PII at odd hours, but from
             his normal IP. Data monitor flags it; security monitor sees nothing.
  Thread 2 — Security anomaly: Unfamiliar IP brute-forcing auth, probing
             customer-db. Security monitor flags it; data monitor sees nothing.
  Both     — Correlation connects them: the unfamiliar IP from Thread 2 also
             shows up in James Liu's most recent PII access from Thread 1.
             Suggests compromised credentials + active exfiltration.

Usage:
  uv run python seed_db.py              # random thread selection
  uv run python seed_db.py --all        # both threads active (worst day)
  uv run python seed_db.py --quiet      # only normal baseline (boring day)
  uv run python seed_db.py --data       # only data anomaly thread
  uv run python seed_db.py --security   # only security anomaly thread

Run: uv run python seed_db.py
"""

import argparse
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path("demo.db")

now = datetime.now(timezone.utc)

# The suspicious external IP — shared across threads so correlation can connect them
SUSPICIOUS_IP = f"198.51.100.{random.randint(10, 99)}"
JAMES_NORMAL_IP = "10.0.1.47"


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


def seed_security_events(conn: sqlite3.Connection, data_anomaly: bool, security_anomaly: bool):
    """Security events.

    - data_anomaly: no security events for James Liu (data monitor catches this, not security)
    - security_anomaly: unfamiliar IP probing auth-service, failed logins, port scan
    - both: the unfamiliar IP also appears in James Liu's access (correlation connects them)
    """
    rows = []

    if security_anomaly:
        # Unfamiliar IP brute-forcing auth — this is a network/auth threat, NOT data access
        rows.append((
            (now - timedelta(hours=6)).isoformat(),
            "failed_login_burst", "warning", None, SUSPICIOUS_IP,
            "auth-service",
            f"14 failed login attempts from IP {SUSPICIOUS_IP} in 3-minute window targeting "
            f"auth-service. Multiple usernames attempted. No successful auth."
        ))
        rows.append((
            (now - timedelta(hours=5, minutes=45)).isoformat(),
            "port_scan", "warning", None, SUSPICIOUS_IP,
            "customer-db",
            f"Port scan detected from IP {SUSPICIOUS_IP} against customer-db (ports 5432, 3306, 27017). "
            f"This IP is not in any known allow-list. Geo: Eastern Europe."
        ))
        rows.append((
            (now - timedelta(hours=5, minutes=30)).isoformat(),
            "unfamiliar_ip", "critical", None, SUSPICIOUS_IP,
            "auth-service",
            f"IP {SUSPICIOUS_IP} successfully authenticated as user 'jliu' after prior failed attempts. "
            f"This IP has not been seen in the last 90 days of access logs. "
            f"User's normal IPs: {JAMES_NORMAL_IP}, 10.0.1.48."
            if data_anomaly else
            f"IP {SUSPICIOUS_IP} not recognized. No successful authentication, but probing continues. "
            f"Recommend firewall block pending investigation."
        ))

        if not data_anomaly:
            # Security-only: the IP never got in, just probing
            rows.append((
                (now - timedelta(hours=5)).isoformat(),
                "firewall_block", "info", None, SUSPICIOUS_IP,
                "perimeter",
                f"Auto-blocked IP {SUSPICIOUS_IP} after repeated probe attempts. "
                f"No successful access to any internal system."
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


def seed_data_access_logs(conn: sqlite3.Connection, data_anomaly: bool, security_anomaly: bool):
    """Access logs.

    - data_anomaly: James Liu pulling bulk PII at odd hours from his NORMAL IP.
      Data monitor catches the volume; security sees nothing (familiar IP, no auth alerts).
    - both active: James Liu's most recent access switches to SUSPICIOUS_IP.
      This is the detail that lets correlation connect the two threads.
    """
    rows = []

    if data_anomaly:
        nights = random.randint(2, 4)
        base_row_count = random.randint(30000, 60000)

        for days_ago in range(nights, 0, -1):
            access_time = (now - timedelta(days=days_ago)).replace(
                hour=random.randint(1, 4),
                minute=random.randint(0, 59),
                second=0, microsecond=0,
            )
            # Key difference: when both threads are active, the most recent night
            # uses the suspicious IP. Otherwise it's always James's normal IP.
            if days_ago == 1 and security_anomaly:
                ip = SUSPICIOUS_IP
            else:
                ip = JAMES_NORMAL_IP
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
            "analytics-summary", "jliu", access_time.isoformat(),
            "select", 40 + days_ago * 5, JAMES_NORMAL_IP, 800,
        ))

    # Sarah Chen — normal access pattern during business hours, always present
    today = now.replace(minute=0, second=0, microsecond=0)
    for hour in [9, 10, 11, 13, 14, 15, 16, 17]:
        ts = today.replace(hour=hour).isoformat()
        rows.append((
            "customer-360", "schen", ts, "select", 20 + hour * 3,
            "10.0.1.22", 200 + hour * 20,
        ))

    for hour in [9, 11, 14, 16]:
        ts = today.replace(hour=hour, minute=30).isoformat()
        rows.append((
            "sales-events", "schen", ts, "select", 50 + hour * 5,
            "10.0.1.22", 1200,
        ))

    # Other normal access
    rows.append((
        "hr-directory", "mwebb", (now - timedelta(hours=3)).isoformat(),
        "select", 15, "10.0.2.11", 50,
    ))
    rows.append((
        "analytics-summary", "psharma", (now - timedelta(hours=1)).isoformat(),
        "select", 75, "10.0.3.5", 300,
    ))

    conn.executemany(
        "INSERT INTO data_access_logs (dataset, user_id, timestamp, query_type, "
        "row_count, source_ip, duration_ms) VALUES (?,?,?,?,?,?,?)",
        rows,
    )


def seed_cost_records(conn: sqlite3.Connection):
    """Cost records — always normal. No budget anomaly thread."""
    rows = []

    months = [
        (now.replace(day=1), "current"),
        ((now.replace(day=1) - timedelta(days=1)).replace(day=1), "month-1"),
        ((now.replace(day=1) - timedelta(days=32)).replace(day=1), "month-2"),
        ((now.replace(day=1) - timedelta(days=63)).replace(day=1), "month-3"),
    ]

    for service, project, budget, costs in [
        ("payments-api",        "Payments",      18000, [16500, 17500, 17200, 16800]),
        ("analytics-pipeline",  "Data Platform",  8000, [7200, 7000, 6950, 6800]),
        ("customer-db",         "Data Platform", 12000, [11200, 11500, 11200, 11000]),
        ("auth-service",        "Security",       5000, [3200, 3100, 3150, 3050]),
        ("reporting-dashboard", "Finance",        2000, [850, 800, 820, 790]),
    ]:
        for (month_dt, _label), spend in zip(months, costs):
            period = month_dt.strftime("%Y-%m")
            rows.append((service, project, period, spend, budget, None))

    conn.executemany(
        "INSERT INTO cost_records (service, project, period, spend, budget, notes) "
        "VALUES (?,?,?,?,?,?)",
        rows,
    )


def main():
    parser = argparse.ArgumentParser(description="Seed demo.db with scenario data")
    parser.add_argument("--all", "--chaos", action="store_true",
                        help="Both anomaly threads active (worst day — correlation connects them)")
    parser.add_argument("--quiet", action="store_true",
                        help="Only normal baseline (boring day — monitors find nothing)")
    parser.add_argument("--data", action="store_true",
                        help="Only data anomaly thread (James Liu bulk PII, security clean)")
    parser.add_argument("--security", action="store_true",
                        help="Only security anomaly thread (unfamiliar IP probing, data clean)")
    args = parser.parse_args()

    # Decide which threads are active
    if args.quiet:
        data_anomaly = False
        security_anomaly = False
    elif args.all:
        data_anomaly = True
        security_anomaly = True
    elif args.data:
        data_anomaly = True
        security_anomaly = False
    elif args.security:
        data_anomaly = False
        security_anomaly = True
    else:
        # Random selection — each thread independently toggled
        data_anomaly = random.random() < 0.5
        security_anomaly = random.random() < 0.5

    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(str(DB_PATH))
    try:
        create_tables(conn)
        seed_employees(conn)
        seed_services(conn)
        seed_service_metrics(conn)
        seed_security_events(conn, data_anomaly=data_anomaly, security_anomaly=security_anomaly)
        seed_datasets(conn)
        seed_data_access_logs(conn, data_anomaly=data_anomaly, security_anomaly=security_anomaly)
        seed_cost_records(conn)
        conn.commit()

        # Print summary
        tables = ["employees", "services", "service_metrics", "security_events",
                   "datasets", "data_access_logs", "cost_records"]
        print(f"Created {DB_PATH}\n")
        for table in tables:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  {table}: {count} rows")

        print(f"\nActive threads:")
        print(f"  {'✓' if data_anomaly else '·'} Data anomaly (James Liu bulk PII access)")
        print(f"  {'✓' if security_anomaly else '·'} Security anomaly (unfamiliar IP probing)")
        print(f"  ✓ Normal baseline (always on)")
        if data_anomaly and security_anomaly:
            print(f"\n  ⚡ Both active — correlation will connect: IP {SUSPICIOUS_IP}")
            print(f"     appears in both security events AND James Liu's latest PII access")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
