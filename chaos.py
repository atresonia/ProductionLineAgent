#!/usr/bin/env python3
"""
chaos.py — inject and clear faults in the Resolve demo environment

Usage:
  python chaos.py bad_deploy              # add bad_deploy to active faults
  python chaos.py slow_db                 # add slow_db (stacks with existing faults)
  python chaos.py bad_deploy slow_db      # add multiple faults at once
  python chaos.py --set bad_deploy        # replace all faults with exactly bad_deploy
  python chaos.py none                    # Clear all faults, restore normal operation
"""

import json
import sys
import os

CHAOS_DIR   = "./chaos"
CHAOS_FILE  = "./chaos/current_fault"   # legacy — kept for backward compat
CHAOS_JSON  = "./chaos/faults.json"     # new primary storage

FAULT_DESCRIPTIONS = {
    "bad_deploy":        "Payment gateway misconfigured — /checkout returns 500 (PAYMENT_GATEWAY_URL wrong in v2.1)",
    "memory_leak":       "Memory leak active — API memory grows ~512KB per checkout request",
    "slow_db":           "DB slowdown — all queries take 2.5s, causing latency cascade",
    "db_down":           "DB connection refused — upstream services return 503",
    "catalog_down":      "Product catalog service unavailable — /products returns 503, /checkout unaffected",
    "checkout_degraded": "Payment gateway intermittent — /checkout fails ~40% of requests, /products unaffected",
}


def set_faults(faults: list[str]) -> None:
    """Write the active fault list to both JSON and legacy file."""
    os.makedirs(CHAOS_DIR, exist_ok=True)
    # Primary: JSON format
    with open(CHAOS_JSON, "w") as f:
        json.dump({"active_faults": faults}, f)
    # Legacy: first fault or "none" (services that haven't updated still read this)
    with open(CHAOS_FILE, "w") as f:
        f.write(faults[0] if faults else "none")


def get_active_faults() -> list[str]:
    """Read currently active faults from disk."""
    try:
        with open(CHAOS_JSON) as f:
            data = json.load(f)
            faults = data.get("active_faults", [])
            if isinstance(faults, list):
                return faults
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    try:
        with open(CHAOS_FILE) as f:
            fault = f.read().strip()
            if fault and fault != "none":
                return [fault]
    except FileNotFoundError:
        pass
    return []


def set_fault(fault: str) -> None:
    """Backward-compatible single-fault setter (still used by older code paths)."""
    if fault == "none":
        set_faults([])
    else:
        set_faults([fault])
    print(f"\n  [chaos] Fault injected : {fault}")
    print(f"  [chaos] Effect         : {FAULT_DESCRIPTIONS.get(fault, 'N/A')}")
    if fault != "none":
        print(f"\n  Run 'python chaos.py none' to restore normal operation.\n")
    else:
        print()


if __name__ == "__main__":
    valid = list(FAULT_DESCRIPTIONS.keys())

    if len(sys.argv) < 2:
        print(__doc__)
        print(f"Valid faults: {', '.join(valid)}, none")
        sys.exit(1)

    args = sys.argv[1:]

    # --set flag: replace all faults with exactly the listed ones
    replace_mode = "--set" in args
    if replace_mode:
        args = [a for a in args if a != "--set"]

    # Clear all faults
    if args == ["none"]:
        set_faults([])
        print("\n  [chaos] All faults cleared — system operating normally\n")
        sys.exit(0)

    # Validate each requested fault
    for f in args:
        if f not in valid:
            print(f"  [chaos] Unknown fault: '{f}'")
            print(f"  Valid faults: {', '.join(valid)}, none")
            sys.exit(1)

    if replace_mode:
        existing = []
        active = args
    else:
        # Additive: merge with existing faults (preserve order, no duplicates)
        existing = get_active_faults()
        active = existing + [f for f in args if f not in existing]

    set_faults(active)

    print(f"\n  [chaos] {len(active)} fault(s) active:")
    for f in active:
        new_marker = "  [NEW]" if f not in existing else ""
        print(f"    • {f}: {FAULT_DESCRIPTIONS[f]}{new_marker}")
    print(f"\n  Run 'python chaos.py none' to restore normal operation.\n")
