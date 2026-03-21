#!/usr/bin/env python3
"""
generate_dashboard.py — create a fake-but-realistic Grafana-style
dashboard screenshot for the memory_leak demo scenario.

Run this once before the demo:
  pip install matplotlib numpy
  python generate_dashboard.py

Output: assets/grafana_memory_spike.png
"""

import os
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from datetime import datetime, timedelta

os.makedirs("assets", exist_ok=True)

# ── Generate realistic memory data ────────────────────────────────────────────
np.random.seed(42)
minutes      = 30
timestamps   = [datetime(2026, 3, 21, 9, 30) + timedelta(minutes=i) for i in range(minutes)]
labels       = [t.strftime("%H:%M") for t in timestamps]

# Normal memory: ~245MB with small noise
normal_mem   = 245 + np.random.normal(0, 3, 15)
# Leak starts at minute 15 — climbs steeply
leak_mem     = [normal_mem[-1] + (i ** 1.6) * 8 + np.random.normal(0, 4)
                for i in range(1, 16)]
memory       = np.concatenate([normal_mem, leak_mem])

# Error rate: near-zero, then spikes at minute 20
error_rate   = np.concatenate([
    np.random.uniform(0, 0.5, 20),
    np.random.uniform(12, 95, 10),
])

# ── Plot ──────────────────────────────────────────────────────────────────────
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 6), facecolor="#111827")
fig.suptitle("Resolve Demo  ·  api service  ·  Last 30 minutes",
             color="#F9FAFB", fontsize=13, fontweight="bold", y=0.98)

for ax in (ax1, ax2):
    ax.set_facecolor("#1F2937")
    ax.tick_params(colors="#9CA3AF", labelsize=8)
    ax.spines[:].set_color("#374151")
    ax.grid(True, color="#374151", linewidth=0.5, linestyle="--")

# Memory panel
ax1.plot(labels, memory, color="#60A5FA", linewidth=2)
ax1.fill_between(range(len(memory)), memory, alpha=0.15, color="#60A5FA")
ax1.axvline(x=15, color="#FBBF24", linewidth=1.5, linestyle="--", alpha=0.8)
ax1.text(15.2, memory.max() * 0.9, "leak start\n09:45", color="#FBBF24",
         fontsize=7, va="top")
ax1.set_ylabel("Memory (MB)", color="#9CA3AF", fontsize=9)
ax1.set_title("Memory Usage", color="#D1D5DB", fontsize=10, pad=4)
ax1.set_xticks(range(0, len(labels), 3))
ax1.set_xticklabels(labels[::3], rotation=0)
ax1.yaxis.label.set_color("#9CA3AF")

# Error rate panel
colors = ["#EF4444" if e > 5 else "#34D399" for e in error_rate]
ax2.bar(range(len(error_rate)), error_rate, color=colors, width=0.8)
ax2.axhline(y=15, color="#FBBF24", linewidth=1, linestyle="--", alpha=0.7)
ax2.text(len(error_rate) - 1, 16, "alert threshold 15%",
         color="#FBBF24", fontsize=7, ha="right")
ax2.set_ylabel("Error Rate (%)", color="#9CA3AF", fontsize=9)
ax2.set_title("Request Error Rate", color="#D1D5DB", fontsize=10, pad=4)
ax2.set_xticks(range(0, len(labels), 3))
ax2.set_xticklabels(labels[::3], rotation=0)
ax2.set_ylim(0, 105)

# Legend
patches = [
    mpatches.Patch(color="#60A5FA", label="api memory"),
    mpatches.Patch(color="#34D399", label="error rate <15%"),
    mpatches.Patch(color="#EF4444", label="error rate >15%"),
]
fig.legend(handles=patches, loc="lower center", ncol=3,
           facecolor="#1F2937", edgecolor="#374151",
           labelcolor="#D1D5DB", fontsize=8, bbox_to_anchor=(0.5, 0.01))

plt.tight_layout(rect=[0, 0.06, 1, 0.96])
out = "assets/grafana_memory_spike.png"
plt.savefig(out, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
print(f"  Dashboard screenshot saved → {out}")
