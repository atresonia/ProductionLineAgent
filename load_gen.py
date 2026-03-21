#!/usr/bin/env python3
"""
load_gen.py — simulate realistic traffic against the frontend

Sends a mix of product page views and checkout attempts continuously.
Run this in a separate terminal during the demo so the agent sees
real traffic patterns rather than silence.

Usage:
  python load_gen.py              # default: ~2 req/s
  python load_gen.py --rps 5     # 5 requests per second
"""

import argparse
import random
import time
import requests
from datetime import datetime

FRONTEND = "http://localhost:3000"

def ts():
    return datetime.now().strftime("%H:%M:%S")

def hit_products():
    r = requests.get(f"{FRONTEND}/", timeout=8)
    status = r.status_code
    symbol = "✓" if status == 200 else "✗"
    print(f"  {ts()}  {symbol}  GET  /products  →  {status}")

def hit_checkout():
    r = requests.post(f"{FRONTEND}/checkout", timeout=12)
    status = r.status_code
    symbol = "✓" if status == 200 else "✗"
    print(f"  {ts()}  {symbol}  POST /checkout  →  {status}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rps", type=float, default=2.0,
                        help="Requests per second (default: 2)")
    args = parser.parse_args()

    interval = 1.0 / args.rps
    print(f"\n  Load generator started — {args.rps} req/s → {FRONTEND}")
    print(f"  Ctrl+C to stop\n")

    while True:
        try:
            # 70% product views, 30% checkouts
            if random.random() < 0.7:
                hit_products()
            else:
                hit_checkout()
        except requests.exceptions.ConnectionError:
            print(f"  {ts()}  ✗  Connection refused — is docker compose up?")
        except Exception as e:
            print(f"  {ts()}  ✗  {e}")
        time.sleep(interval)

if __name__ == "__main__":
    main()
