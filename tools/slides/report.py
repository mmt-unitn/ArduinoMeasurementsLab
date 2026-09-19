"""Turns tools/slides/overflow.js output into a per-deck overflow report.

Reads the JSON on stdin; exits non-zero when any slide is too tall, so the
check can gate a build.
"""
import json
import os
import sys

limit = int(os.environ.get("USABLE", "720"))
deck = os.environ.get("DECK", "(deck)")

try:
    d = json.loads(sys.stdin.read())
except Exception:
    print(f"{deck:28s} MEASUREMENT FAILED")
    sys.exit(2)

if "slides" not in d:
    print(f"{deck:28s} MEASUREMENT FAILED: {d.get('error', 'no slides')}")
    sys.exit(2)

bad = [s for s in d["slides"] if s["h"] > limit]
print(f"{deck:28s} {d['n']:3d} slides, {len(bad):2d} over {limit}px")
for s in bad:
    print(f"      {s['h']:5d}px  {s['title'][:62]}")
sys.exit(1 if bad else 0)
