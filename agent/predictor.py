"""
predictor.py — ML-based predictive healing for Resolve

Uses Isolation Forest (anomaly detection) + Linear Regression (ETA prediction)
to catch deteriorating patterns BEFORE they breach thresholds.

Pipeline per service:
  1. Collect rolling window of metric snapshots every poll cycle
  2. Extract 8 features: raw values + 1st/2nd derivatives + rolling std
  3. First BASELINE_WINDOW readings → fit IsolationForest on healthy behavior
  4. After baseline → score each new window; flag if anomaly_score > ANOMALY_THRESHOLD
  5. On anomaly → LinearRegression extrapolation → ETA to threshold breach

Runs alongside check_trends() in monitor.py — does NOT replace it.
"""

import os
import numpy as np
from collections import deque
from datetime import datetime, timezone

# ── Config ────────────────────────────────────────────────────────────────────
BASELINE_WINDOW   = int(os.getenv("PREDICTOR_BASELINE", "30"))   # readings to train on
SCORE_WINDOW      = int(os.getenv("PREDICTOR_SCORE_WIN", "10"))   # readings to score
ANOMALY_THRESHOLD = float(os.getenv("PREDICTOR_THRESHOLD", "0.60"))  # 0–1
ETA_WINDOW        = int(os.getenv("PREDICTOR_ETA_WIN", "10"))     # readings for ETA fit

ERROR_RATE_THRESHOLD = float(os.getenv("ERROR_RATE_THRESHOLD", "15"))
LATENCY_THRESHOLD_MS = float(os.getenv("LATENCY_THRESHOLD_MS", "1500"))
POLL_INTERVAL        = float(os.getenv("POLL_INTERVAL", "5"))


# ── Per-service state ─────────────────────────────────────────────────────────

class ServicePredictor:
    """
    Maintains metric history and ML models for one service.
    """

    def __init__(self, service: str):
        self.service  = service
        self.history: deque[dict] = deque(maxlen=BASELINE_WINDOW + SCORE_WINDOW + 5)
        self.model    = None   # IsolationForest, fitted after baseline
        self.baseline_fitted = False
        self.last_warning: str | None = None

    # ── Feature extraction ────────────────────────────────────────────────────

    def _features(self, window: list[dict]) -> np.ndarray:
        """
        Extract 8 features from a window of readings:
          0  error_rate (current)
          1  p95_ms     (current)
          2  memory_mb  (current)
          3  Δ error_rate  (1st derivative — rate of change)
          4  Δ p95_ms
          5  Δ memory_mb
          6  σ error_rate  (rolling std — captures oscillation)
          7  σ p95_ms
        """
        err  = np.array([r["error_rate"] for r in window], dtype=float)
        p95  = np.array([r["p95"]        for r in window], dtype=float)
        mem  = np.array([r["memory"]     for r in window], dtype=float)

        # Derivatives (mean of pairwise differences)
        d_err = float(np.mean(np.diff(err))) if len(err) > 1 else 0.0
        d_p95 = float(np.mean(np.diff(p95))) if len(p95) > 1 else 0.0
        d_mem = float(np.mean(np.diff(mem))) if len(mem) > 1 else 0.0

        return np.array([
            err[-1], p95[-1], mem[-1],
            d_err,   d_p95,   d_mem,
            float(np.std(err)), float(np.std(p95)),
        ], dtype=float)

    def _feature_matrix(self, readings: list[dict]) -> np.ndarray:
        """Build a (N, 8) matrix — one feature vector per reading."""
        rows = []
        for i in range(1, len(readings) + 1):
            window = list(readings[max(0, i - SCORE_WINDOW):i])
            rows.append(self._features(window))
        return np.array(rows)

    # ── Model lifecycle ───────────────────────────────────────────────────────

    def _fit_baseline(self, readings: list[dict]) -> None:
        from sklearn.ensemble import IsolationForest
        X = self._feature_matrix(readings)
        self.model = IsolationForest(
            n_estimators=100,
            contamination=0.05,   # expect ~5% anomalies in baseline
            random_state=42,
        )
        self.model.fit(X)
        self.baseline_fitted = True

    # ── ETA prediction ────────────────────────────────────────────────────────

    def _eta(self, values: list[float], threshold: float) -> float | None:
        """
        Fit a linear regression on the last ETA_WINDOW values and extrapolate
        to find how many poll cycles until the threshold is breached.
        Returns minutes, or None if the trend is flat/falling.
        """
        from sklearn.linear_model import LinearRegression
        if len(values) < 3:
            return None
        tail = values[-ETA_WINDOW:]
        x = np.arange(len(tail)).reshape(-1, 1)
        y = np.array(tail)
        reg = LinearRegression().fit(x, y)
        slope = reg.coef_[0]
        if slope <= 0:
            return None
        current = tail[-1]
        steps_to_threshold = (threshold - current) / slope
        if steps_to_threshold <= 0:
            return None
        return round(steps_to_threshold * POLL_INTERVAL / 60, 1)

    # ── Anomaly score ─────────────────────────────────────────────────────────

    def _anomaly_score(self, reading: dict) -> float:
        """
        Returns a score in [0, 1] — higher = more anomalous.
        IsolationForest.decision_function returns negative = anomalous,
        positive = normal. We normalise to [0,1].
        """
        window = list(self.history)[-SCORE_WINDOW:]
        if len(window) < 2:
            return 0.0
        feat = self._features(window).reshape(1, -1)
        raw  = self.model.decision_function(feat)[0]
        # decision_function ~ [-0.5, 0.5]; flip and normalise to [0,1]
        score = float(np.clip(0.5 - raw, 0, 1))
        return round(score, 3)

    # ── Main entry point ──────────────────────────────────────────────────────

    def ingest(self, error_rate: float, p95: float, memory: float) -> dict | None:
        """
        Feed one new metric snapshot. Returns a prediction dict if an anomaly
        is detected, else None.

        Prediction dict:
          {service, anomaly_score, eta_error_min, eta_latency_min,
           predicted_error_rate, predicted_p95, confidence_pct, timestamp}
        """
        reading = {"error_rate": error_rate, "p95": p95, "memory": memory}
        self.history.append(reading)

        readings = list(self.history)
        n = len(readings)

        # Phase 1 — collecting baseline
        if n < BASELINE_WINDOW:
            return None

        # Phase 2 — fit model once baseline is full
        if not self.baseline_fitted:
            self._fit_baseline(readings[:BASELINE_WINDOW])

        # Phase 3 — score
        score = self._anomaly_score(reading)
        if score < ANOMALY_THRESHOLD:
            self.last_warning = None
            return None

        # Anomaly detected — compute ETAs
        err_history = [r["error_rate"] for r in readings]
        p95_history = [r["p95"]        for r in readings]

        eta_err = self._eta(err_history, ERROR_RATE_THRESHOLD)
        eta_p95 = self._eta(p95_history, LATENCY_THRESHOLD_MS)

        # Predict values 5 poll cycles ahead via linear extrapolation
        def _extrapolate(vals: list[float], steps: int = 5) -> float:
            if len(vals) < 2:
                return vals[-1]
            tail = vals[-ETA_WINDOW:]
            x    = np.arange(len(tail)).reshape(-1, 1)
            reg  = LinearRegression().fit(x, np.array(tail))
            return round(float(reg.predict([[len(tail) + steps]])[0]), 1)

        from sklearn.linear_model import LinearRegression
        pred_err = max(0, _extrapolate(err_history))
        pred_p95 = max(0, _extrapolate(p95_history))

        # Confidence: scale score to 50–99%
        confidence = int(50 + score * 49)

        result = {
            "service":             self.service,
            "anomaly_score":       score,
            "eta_error_min":       eta_err,
            "eta_latency_min":     eta_p95,
            "predicted_error_rate": pred_err,
            "predicted_p95_ms":    pred_p95,
            "confidence_pct":      confidence,
            "timestamp":           datetime.now(timezone.utc).isoformat(),
        }
        self.last_warning = result
        return result

    def reset(self) -> None:
        """Call after incident resolves to start fresh."""
        self.history.clear()
        self.model            = None
        self.baseline_fitted  = False
        self.last_warning     = None


# ── Module-level predictors (one per service) ─────────────────────────────────

_predictors: dict[str, ServicePredictor] = {
    "api":      ServicePredictor("api"),
    "frontend": ServicePredictor("frontend"),
}


def predict(service: str, error_rate: float, p95: float, memory: float) -> dict | None:
    """Feed a new reading and return a prediction dict if anomalous, else None."""
    return _predictors[service].ingest(error_rate, p95, memory)


def reset(service: str | None = None) -> None:
    """Reset predictor state after an incident resolves."""
    targets = [service] if service else list(_predictors)
    for s in targets:
        if s in _predictors:
            _predictors[s].reset()


def format_warning(pred: dict) -> str:
    """Human-readable warning string from a prediction dict."""
    svc   = pred["service"]
    score = pred["anomaly_score"]
    conf  = pred["confidence_pct"]
    lines = [
        f"ML anomaly on {svc} — score {score:.2f} ({conf}% confidence)",
        f"  predicted error_rate in 25s: {pred['predicted_error_rate']}%",
        f"  predicted p95 in 25s:        {pred['predicted_p95_ms']}ms",
    ]
    if pred["eta_error_min"]:
        lines.append(f"  ETA to error threshold:  ~{pred['eta_error_min']}m")
    if pred["eta_latency_min"]:
        lines.append(f"  ETA to latency threshold: ~{pred['eta_latency_min']}m")
    return "\n".join(lines)
