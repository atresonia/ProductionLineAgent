import os
import json
import time
import uuid
import threading
import psutil
import requests as http
from datetime import datetime, timezone
from flask import Flask, request, jsonify

app = Flask(__name__)

API_URL    = os.getenv("API_URL", "http://api:8000")
LOG_FILE   = "/app/logs/frontend.log"

# ── Logging ───────────────────────────────────────────────────────────────────

def log(level: str, event: str, **kwargs):
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service":   "frontend",
        "level":     level,
        "event":     event,
        "memory_mb": round(psutil.Process().memory_info().rss / 1024 / 1024, 1),
        **kwargs,
    }
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "frontend"})

@app.route("/")
def index():
    start = time.time()
    try:
        r = http.get(f"{API_URL}/products", timeout=8)
        latency = round((time.time() - start) * 1000)
        log("INFO", "request", endpoint="/", method="GET",
            status_code=200, latency_ms=latency, upstream="api")
        return jsonify({"status": "ok", "product_count": len(r.json())})
    except Exception as e:
        latency = round((time.time() - start) * 1000)
        log("ERROR", "request", endpoint="/", method="GET",
            status_code=502, latency_ms=latency, upstream="api", error=str(e))
        return jsonify({"error": "Bad gateway", "detail": str(e)}), 502

@app.route("/checkout", methods=["POST"])
def checkout():
    start  = time.time()
    req_id = str(uuid.uuid4())[:8]
    try:
        r = http.post(
            f"{API_URL}/checkout",
            json={"amount": 99.99, "request_id": req_id},
            timeout=12,
        )
        latency = round((time.time() - start) * 1000)
        level = "INFO" if r.status_code == 200 else "ERROR"
        log(level, "request", endpoint="/checkout", method="POST",
            status_code=r.status_code, latency_ms=latency,
            upstream="api", request_id=req_id)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        latency = round((time.time() - start) * 1000)
        log("ERROR", "request", endpoint="/checkout", method="POST",
            status_code=502, latency_ms=latency,
            upstream="api", request_id=req_id, error=str(e))
        return jsonify({"error": "Bad gateway", "detail": str(e)}), 502

# ── Background: emit metrics every 10 s ──────────────────────────────────────

def metrics_emitter():
    while True:
        time.sleep(10)
        proc = psutil.Process()
        log("INFO", "metrics",
            memory_mb=round(proc.memory_info().rss / 1024 / 1024, 1),
            cpu_percent=psutil.cpu_percent(interval=0.5))

threading.Thread(target=metrics_emitter, daemon=True).start()

# ── Startup ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    os.makedirs("/app/logs", exist_ok=True)
    log("INFO", "startup", message="Frontend service starting", port=3000)
    app.run(host="0.0.0.0", port=3000)
