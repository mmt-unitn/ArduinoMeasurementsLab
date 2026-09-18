#!/usr/bin/env python3
"""Regenerates the synthetic reference data of one or every experiment.

    python tools/synth/generate.py            # every generator
    python tools/synth/generate.py s1 s2      # only those

Every generator is seeded from its parameter file, so running this twice in a
row produces identical bytes and `git status` stays clean unless a parameter or
a model actually changed. That is the property that makes the shipped reference
data reviewable.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

#: experiment id -> module in this directory
GENERATORS = {
    "s1": "s1_daq_static",
    "s3": "s3_aliasing_filtering",
}


def main(argv: list) -> int:
    wanted = [a.lower() for a in argv] or list(GENERATORS)
    unknown = [w for w in wanted if w not in GENERATORS]
    if unknown:
        print(f"unknown generator(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"available: {', '.join(sorted(GENERATORS))}", file=sys.stderr)
        return 2
    for name in wanted:
        module = importlib.import_module(GENERATORS[name])
        module.generate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
