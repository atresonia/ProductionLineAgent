import os
import json
import time
import uuid
import random
import threading
import traceback
import psutil
import psycopg2
import psycopg2.extras
from datetime import datetime, timezone
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

LOG_FILE    = "/app/logs/api.log"
CHAOS_FILE  = "/app/chaos/current_fault"   # legacy
CHAOS_JSON  = "/app/chaos/faults.json"     # new primary

# In-memory leak buffer — grows when memory_leak fault is active
_leak_buffer: list = []

# ── Logging ──────────────────────────────────────────────────────────────────

def log(level: str, event: str, **kwargs):
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service":   "api",
        "level":     level,
        "event":     event,
        "memory_mb": round(psutil.Process().memory_info().rss / 1024 / 1024, 1),
        **kwargs,
    }
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
    # Occasionally emit plain-text noise — simulates legacy log lines,
    # nginx-style access logs, or third-party library stderr mixed in.
    if random.random() < 0.04:
        noise = random.choice([
            f'[{datetime.now(timezone.utc).strftime("%d/%b/%Y:%H:%M:%S +0000")}] '
            f'"POST /checkout HTTP/1.1" {kwargs.get("status_code", 200)} 142',
            "INFO  c.zaxxer.hikari.HikariDataSource - HikariPool-1 - Start completed.",
            "WARN  io.netty.channel.DefaultChannelPipeline - An exceptionCaught() was fired",
        ])
        with open(LOG_FILE, "a") as f:
            f.write(noise + "\n")

def log_stacktrace(error: Exception, context: str):
    """Emit a real Python stack trace as plain text into the log file —
    mirrors how uncaught exceptions appear in production logs."""
    tb = traceback.format_exc()
    with open(LOG_FILE, "a") as f:
        f.write(f"[{datetime.now(timezone.utc).isoformat()}] EXCEPTION in {context}:\n")
        f.write(tb)
        f.write("\n")

# ── Chaos control ─────────────────────────────────────────────────────────────

def get_faults() -> list[str]:
    """Return all active faults. Reads JSON format, falls back to legacy string."""
    try:
        with open(CHAOS_JSON) as f:
            data = json.load(f)
            faults = data.get("active_faults", [])
            if isinstance(faults, list):
                return faults
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    # Legacy fallback
    try:
        with open(CHAOS_FILE) as f:
            fault = f.read().strip()
            if fault and fault != "none":
                return [fault]
    except FileNotFoundError:
        pass
    return []


def get_fault() -> str:
    """Legacy single-fault accessor — returns primary fault or 'none'."""
    faults = get_faults()
    return faults[0] if faults else "none"

# ── Database ──────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "db"),
        user=os.getenv("DB_USER", "resolve"),
        password=os.getenv("DB_PASSWORD", "resolve"),
        dbname=os.getenv("DB_NAME", "shopdb"),
        connect_timeout=5,
    )

def wait_for_db(retries=15, delay=2):
    for attempt in range(retries):
        try:
            conn = get_conn()
            conn.close()
            log("INFO", "startup", message="Database connection established")
            return
        except Exception as e:
            log("WARN", "startup", message=f"DB not ready (attempt {attempt+1}/{retries}): {e}")
            time.sleep(delay)
    raise RuntimeError("Could not connect to database after retries")

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "api", "fault": get_fault()})

@app.route("/metrics")
def metrics():
    proc = psutil.Process()
    return jsonify({
        "service":    "api",
        "timestamp":  datetime.now(timezone.utc).isoformat(),
        "memory_mb":  round(proc.memory_info().rss / 1024 / 1024, 1),
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "fault":      get_fault(),
    })

@app.route("/products")
def products():
    start  = time.time()
    faults = get_faults()

    try:
        if "catalog_down" in faults:
            latency = round((time.time() - start) * 1000)
            log("ERROR", "request", endpoint="/products", method="GET",
                status_code=503, latency_ms=latency,
                error="Product catalog service unavailable — upstream dependency failure")
            return jsonify({"error": "Product catalog service unavailable — upstream dependency failure"}), 503

        if "db_down" in faults:
            raise psycopg2.OperationalError(
                "could not connect to server: Connection refused (host=db, port=5432)"
            )

        if "slow_db" in faults:
            log("WARN", "db_query", message="Query running slow — DB latency elevated", latency_hint_ms=2500)
            time.sleep(2.5)

        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT id, name, price, stock FROM products ORDER BY id")
                rows = cur.fetchall()

        latency = round((time.time() - start) * 1000)
        log("INFO", "request", endpoint="/products", method="GET",
            status_code=200, latency_ms=latency)
        return jsonify(list(rows))

    except Exception as e:
        latency = round((time.time() - start) * 1000)
        log("ERROR", "request", endpoint="/products", method="GET",
            status_code=503, latency_ms=latency, error=str(e))
        return jsonify({"error": "Service unavailable", "detail": str(e)}), 503


@app.route("/checkout", methods=["POST"])
def checkout():
    start   = time.time()
    faults  = get_faults()
    req_id  = str(uuid.uuid4())[:8]
    payload = request.get_json(silent=True) or {}

    # ── checkout_degraded: ~40% of requests fail intermittently ──────────
    if "checkout_degraded" in faults:
        if random.random() < 0.4:
            latency = round((time.time() - start) * 1000)
            log("ERROR", "request", endpoint="/checkout", method="POST",
                status_code=500, latency_ms=latency, request_id=req_id,
                error="Payment gateway upstream timeout — intermittent connectivity to payments.internal",
                detail="Upstream payment processor returned ETIMEDOUT after 30s")
            return jsonify({
                "error": "Payment gateway upstream timeout — intermittent connectivity to payments.internal",
                "code":  "PAYMENT_GATEWAY_TIMEOUT",
                "request_id": req_id,
            }), 500

    # ── bad_deploy fires first: payment gateway misconfigured ─────────────
    if "bad_deploy" in faults:
        latency = round((time.time() - start) * 1000)
        log("ERROR", "request", endpoint="/checkout", method="POST",
            status_code=500, latency_ms=latency, request_id=req_id,
            error="Payment gateway connection refused — PAYMENT_GATEWAY_URL misconfigured in v2.1 deploy",
            detail="PAYMENT_GATEWAY_URL misconfigured in v2.1 deploy — env var override missing")
        return jsonify({
            "error": "Payment gateway connection refused — PAYMENT_GATEWAY_URL misconfigured in v2.1 deploy",
            "code":  "PAYMENT_GATEWAY_ERROR",
            "request_id": req_id,
        }), 500

    # ── memory_leak: buffer grows with each request ────────────────────────
    if "memory_leak" in faults:
        _leak_buffer.extend([b"x" * 1024] * 512)   # +512 KB per request
        log("WARN", "memory", message="Memory growing",
            memory_mb=round(psutil.Process().memory_info().rss / 1024 / 1024, 1),
            leak_buffer_kb=len(_leak_buffer))

    try:
        if "db_down" in faults:
            raise psycopg2.OperationalError(
                "could not connect to server: Connection refused (host=db, port=5432)"
            )

        if "slow_db" in faults:
            time.sleep(2.5)

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO orders (product_id, amount, status) VALUES (%s, %s, %s) RETURNING id",
                    (1, payload.get("amount", 99.99), "completed"),
                )
                order_id = cur.fetchone()[0]
            conn.commit()

        latency = round((time.time() - start) * 1000)
        log("INFO", "request", endpoint="/checkout", method="POST",
            status_code=200, latency_ms=latency, request_id=req_id, order_id=order_id)
        return jsonify({"status": "ok", "order_id": order_id, "request_id": req_id})

    except psycopg2.OperationalError as e:
        latency = round((time.time() - start) * 1000)
        log("ERROR", "request", endpoint="/checkout", method="POST",
            status_code=503, latency_ms=latency, request_id=req_id, error=str(e))
        log_stacktrace(e, "/checkout db_down")
        return jsonify({"error": "Database unavailable", "detail": str(e)}), 503

    except Exception as e:
        latency = round((time.time() - start) * 1000)
        log("ERROR", "request", endpoint="/checkout", method="POST",
            status_code=500, latency_ms=latency, request_id=req_id, error=str(e))
        log_stacktrace(e, "/checkout")
        return jsonify({"error": "Internal server error"}), 500

# ── Background: emit metrics every 10 s ──────────────────────────────────────

def metrics_emitter():
    while True:
        time.sleep(10)
        proc = psutil.Process()
        log("INFO", "metrics",
            memory_mb=round(proc.memory_info().rss / 1024 / 1024, 1),
            cpu_percent=psutil.cpu_percent(interval=0.5),
            fault=get_fault())

threading.Thread(target=metrics_emitter, daemon=True).start()

# ── Startup ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    os.makedirs("/app/logs", exist_ok=True)
    wait_for_db()
    log("INFO", "startup", message="API service starting", port=8000)
    app.run(host="0.0.0.0", port=8000)
