"""
config.py — load and expose the resolve.config.yaml triage priority config

Provides:
  get_service_priority(service_name)  -> {"priority": str, "reason": str}
  get_endpoint_priority(service, ep)  -> {"priority": str, "reason": str}
  get_triage_rules()                  -> list[str]  (formatted text lines)
  get_triage_context()                -> str         (full formatted block)

If the config file is missing, returns safe defaults and logs a warning.
"""

import logging
import os
from typing import Any

log = logging.getLogger(__name__)

_CONFIG_PATH = os.getenv(
    "RESOLVE_CONFIG",
    os.path.join(os.path.dirname(__file__), "..", "resolve.config.yaml"),
)

_config: dict[str, Any] | None = None


def _load() -> dict[str, Any]:
    global _config
    if _config is not None:
        return _config
    try:
        import yaml
        with open(_CONFIG_PATH) as f:
            _config = yaml.safe_load(f) or {}
    except FileNotFoundError:
        log.warning("resolve.config.yaml not found at %s — using defaults", _CONFIG_PATH)
        _config = {}
    except ImportError:
        log.warning("pyyaml not installed — pip install pyyaml. Using defaults.")
        _config = {}
    except Exception as exc:
        log.warning("Failed to load resolve.config.yaml: %s — using defaults", exc)
        _config = {}
    return _config


def get_service_priority(service_name: str) -> dict[str, str]:
    cfg = _load()
    svc = cfg.get("services", {}).get(service_name, {})
    return {
        "priority": svc.get("priority", "high"),
        "reason":   svc.get("reason", f"Service {service_name} — no priority config found"),
    }


def get_endpoint_priority(service_name: str, endpoint: str) -> dict[str, str]:
    cfg = _load()
    svc = cfg.get("services", {}).get(service_name, {})
    ep  = svc.get("endpoints", {}).get(endpoint)
    if ep:
        return {
            "priority": ep.get("priority", "high"),
            "reason":   ep.get("reason", f"{service_name}{endpoint} — no endpoint priority config"),
        }
    return get_service_priority(service_name)


def get_triage_rules() -> list[str]:
    cfg   = _load()
    rules = cfg.get("triage_rules", [])
    lines = []
    for r in rules:
        lines.append(
            f"- [{r.get('name', 'Rule')}] "
            f"When: {r.get('condition', '')} → "
            f"{r.get('action', '')}"
        )
    return lines


def get_triage_context() -> str:
    cfg = _load()
    if not cfg:
        return "No triage config found — using default severity-based triage."

    lines = ["=== Triage Priority Configuration ===\n"]

    services = cfg.get("services", {})
    if services:
        lines.append("Service Priorities:")
        for svc_name, svc in services.items():
            priority = svc.get("priority", "unknown")
            reason   = svc.get("reason", "")
            lines.append(f"  {svc_name}: [{priority.upper()}] {reason}")
            endpoints = svc.get("endpoints", {})
            for ep_path, ep in endpoints.items():
                ep_priority = ep.get("priority", "unknown")
                ep_reason   = ep.get("reason", "")
                lines.append(f"    {ep_path}: [{ep_priority.upper()}] {ep_reason}")
        lines.append("")

    rules = cfg.get("triage_rules", [])
    if rules:
        lines.append("Triage Rules (apply in order):")
        for r in rules:
            lines.append(f"  [{r.get('name', 'Rule')}]")
            lines.append(f"    When: {r.get('condition', '')}")
            lines.append(f"    Do:   {r.get('action', '')}")
        lines.append("")

    return "\n".join(lines)
