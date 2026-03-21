#!/usr/bin/env python3
"""
seed_logs.py — Generate realistic log files for testing the agent without Docker.

Usage:
  python3 seed_logs.py                    # seed normal + bad_deploy fault
  python3 seed_logs.py --fault memory_leak
  python3 seed_logs.py --fault slow_db
  python3 seed_logs.py --fault db_down
  python3 seed_logs.py --fault none       # healthy logs only

This writes to ./logs/ so the agent can run locally without Docker.
"""

import argparse
import json
import os
import random
from datetime import datetime, timezone, timedelta

LOG_DIR   = "./logs"
CHAOS_DIR = "./chaos"
os.makedirs(LOG_DIR,   exist_ok=True)
os.makedirs(CHAOS_DIR, exist_ok=True)

def ts(offset_seconds: int = 0) -> str:
    t = datetime.now(timezone.utc) - timedelta(seconds=abs(offset_seconds))
    return t.isoformat()

def req(service, endpoint, status, latency, error=None, extra=None):
    entry = {
        "timestamp":   ts(random.randint(0, 180)),
        "service":     service,
        "event":       "request",
        "endpoint":    endpoint,
        "method":      "POST" if endpoint == "/checkout" else "GET",
        "status_code": status,
        "latency_ms":  latency,
    }
    if error:
        entry["error"]  = error
        entry["level"]  = "ERROR"
    if extra:
        entry.update(extra)
    return json.dumps(entry)

def metric(service, memory_mb, offset=0):
    return json.dumps({
        "timestamp":  ts(offset),
        "service":    service,
        "event":      "metrics",
        "memory_mb":  memory_mb,
        "cpu_pct":    round(random.uniform(5, 40), 1),
    })

def write_healthy_logs(fault: str):
    api_lines      = []
    frontend_lines = []

    # 3 minutes of healthy traffic
    for i in range(120):
        api_lines.append(req("api", "/products",  200, random.randint(8,  45)))
        api_lines.append(req("api", "/health",    200, random.randint(2,  10)))
        frontend_lines.append(req("frontend", "/", 200, random.randint(15, 60)))

    # Healthy checkouts
    for i in range(40):
        api_lines.append(req("api", "/checkout", 200, random.randint(120, 280)))

    # Startup event
    api_lines.insert(0, json.dumps({
        "timestamp": ts(300),
        "service":   "api",
        "event":     "startup",
        "version":   "v2.1",
        "message":   "api service started",
        "level":     "INFO",
    }))

    # Memory metrics (healthy)
    for i in range(18):
        api_lines.append(metric("api",      180 + random.randint(-5, 5), offset=i*10))
        frontend_lines.append(metric("frontend", 95  + random.randint(-3, 3), offset=i*10))

    return api_lines, frontend_lines


def apply_fault(api_lines, frontend_lines, fault: str):
    if fault == "bad_deploy":
        # 85% error rate on /checkout
        for i in range(80):
            api_lines.append(req(
                "api", "/checkout", 500,
                random.randint(180, 320),
                error="PAYMENT_GATEWAY_URL points to decommissioned endpoint: "
                      "https://legacy-payments.internal/v1/charge returned 503"
            ))
        # A few successful ones before the fault
        for i in range(12):
            api_lines.append(req("api", "/checkout", 200, random.randint(150, 280)))
        # Stack trace
        api_lines.append(
            "EXCEPTION in /checkout handler:\n"
            "Traceback (most recent call last):\n"
            '  File "/app/app.py", line 142, in checkout\n'
            "    response = requests.post(PAYMENT_GATEWAY_URL, json=payload, timeout=5)\n"
            '  File "/usr/local/lib/python3.11/site-packages/requests/models.py", line 974, in raise_for_status\n'
            "    raise HTTPError(http_error_msg, response=self)\n"
            "requests.exceptions.HTTPError: 503 Service Unavailable: "
            "https://legacy-payments.internal/v1/charge is decommissioned"
        )
        # Frontend 502s cascading
        for i in range(70):
            frontend_lines.append(req(
                "frontend", "/checkout", 502,
                random.randint(200, 380),
                error="upstream api returned 500"
            ))

    elif fault == "memory_leak":
        # Memory growing linearly
        for i in range(20):
            api_lines.append(metric("api", 180 + i * 18, offset=-(20-i)*15))
        api_lines.append(metric("api", 890))  # current high
        # Latency degrading
        for i in range(60):
            api_lines.append(req("api", "/checkout", 200, random.randint(300 + i*12, 600 + i*15)))
        # Warning log
        api_lines.append(json.dumps({
            "timestamp": ts(30),
            "service":   "api",
            "event":     "memory_warning",
            "memory_mb": 890,
            "level":     "WARN",
            "message":   "Memory usage approaching limit: 890MB / 1024MB",
        }))

    elif fault == "slow_db":
        # High latency on all db-touching endpoints
        for i in range(80):
            api_lines.append(req("api", "/checkout", 200, random.randint(2400, 3200)))
            api_lines.append(req("api", "/products", 200, random.randint(2100, 2900)))
        # Cascade to frontend
        for i in range(60):
            frontend_lines.append(req("frontend", "/", 200, random.randint(2600, 3500)))
        # DB slow query log
        api_lines.append(json.dumps({
            "timestamp": ts(60),
            "service":   "api",
            "event":     "slow_query",
            "query_ms":  2847,
            "level":     "WARN",
            "message":   "Query exceeded threshold: SELECT * FROM orders WHERE ... took 2847ms",
        }))

    elif fault == "db_down":
        # 100% errors, DB unreachable
        for i in range(100):
            api_lines.append(req(
                "api", "/checkout", 503,
                random.randint(50, 120),
                error="psycopg2.OperationalError: could not connect to server: Connection refused"
            ))
            api_lines.append(req(
                "api", "/products", 503,
                random.randint(40, 100),
                error="psycopg2.OperationalError: could not connect to server: Connection refused"
            ))
        # Frontend 100% fail
        for i in range(80):
            frontend_lines.append(req(
                "frontend", "/", 503,
                random.randint(80, 150),
                error="api returned 503: DB unreachable"
            ))
        api_lines.append(
            "EXCEPTION: Database connection failed\n"
            "Traceback (most recent call last):\n"
            '  File "/app/app.py", line 87, in get_products\n'
            "    conn = psycopg2.connect(DATABASE_URL)\n"
            "psycopg2.OperationalError: could not connect to server: Connection refused\n"
            "    Is the server running on host 'db' and accepting TCP/IP connections on port 5432?"
        )

    return api_lines, frontend_lines


def main():
    parser = argparse.ArgumentParser(description="Seed log files for Resolve agent testing")
    parser.add_argument("--fault", default="bad_deploy",
                        choices=["bad_deploy", "memory_leak", "slow_db", "db_down", "none"],
                        help="Fault type to inject into logs")
    args = parser.parse_args()

    api_lines, frontend_lines = write_healthy_logs(args.fault)

    if args.fault != "none":
        api_lines, frontend_lines = apply_fault(api_lines, frontend_lines, args.fault)
        # Write chaos file
        with open(os.path.join(CHAOS_DIR, "current_fault"), "w") as f:
            f.write(args.fault)
    else:
        with open(os.path.join(CHAOS_DIR, "current_fault"), "w") as f:
            f.write("none")

    # Shuffle to make logs feel real (mixed timestamps)
    random.shuffle(api_lines)
    random.shuffle(frontend_lines)

    with open(os.path.join(LOG_DIR, "api.log"), "w") as f:
        f.write("\n".join(api_lines) + "\n")

    with open(os.path.join(LOG_DIR, "frontend.log"), "w") as f:
        f.write("\n".join(frontend_lines) + "\n")

    print(f"Seeded logs: fault={args.fault}")
    print(f"  {LOG_DIR}/api.log      ({len(api_lines)} lines)")
    print(f"  {LOG_DIR}/frontend.log ({len(frontend_lines)} lines)")
    print(f"  {CHAOS_DIR}/current_fault = {args.fault}")
    print()
    print(f"Now run:  cd agent && python3 agent.py --demo {args.fault if args.fault != 'none' else 'bad_deploy'}")


if __name__ == "__main__":
    main()
