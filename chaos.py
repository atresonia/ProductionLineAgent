#!/usr/bin/env python3
"""
chaos.py — inject and clear faults in the Resolve demo environment

Usage:
  python chaos.py bad_deploy     # Payment gateway misconfigured (v2.1 deploy)
  python chaos.py memory_leak    # API memory grows with each request
  python chaos.py slow_db        # DB queries take 2.5s (latency cascade)
  python chaos.py db_down        # DB connection refused (upstream cascade)
  python chaos.py none           # Clear all faults, restore normal operation
"""

import sys
import os

CHAOS_DIR  = "./chaos"
CHAOS_FILE = "./chaos/current_fault"

FAULT_DESCRIPTIONS = {
    "bad_deploy":   "Payment gateway misconfigured — /checkout returns 500 (PAYMENT_GATEWAY_URL wrong in v2.1)",
    "memory_leak":  "Memory leak active — API memory grows ~512KB per checkout request",
    "slow_db":      "DB slowdown — all queries take 2.5s, causing latency cascade",
    "db_down":      "DB connection refused — upstream services return 503",
    "none":         "No fault — system operating normally",
}

def set_fault(fault: str):
    os.makedirs(CHAOS_DIR, exist_ok=True)
    with open(CHAOS_FILE, "w") as f:
        f.write(fault)
    print(f"\n  [chaos] Fault injected : {fault}")
    print(f"  [chaos] Effect         : {FAULT_DESCRIPTIONS[fault]}")
    if fault != "none":
        print(f"\n  Run 'python chaos.py none' to restore normal operation.\n")
    else:
        print()

if __name__ == "__main__":
    valid = list(FAULT_DESCRIPTIONS.keys())

    if len(sys.argv) != 2 or sys.argv[1] not in valid:
        print(__doc__)
        print(f"Valid faults: {', '.join(valid)}")
        sys.exit(1)

    set_fault(sys.argv[1])
