#!/usr/bin/env python3
"""T10 UI smoke harness (docs/L99_ARCH_PLAN.md).

Headless-Chrome screenshots of the golden views + marker checks + pixel drift vs
stored goldens. Every UI regression in the 2026-07 hardening cycle was caught by
hand-screenshotting — this codifies that loop.

Usage:
  python3 tools/ui_smoke.py                 # run checks against goldens
  python3 tools/ui_smoke.py --update        # (re)record goldens from current UI
  python3 tools/ui_smoke.py --base http://localhost:7860 --run <canvas_run_id>

Exit 0 = all pass. Non-zero = failures listed on stdout.
Goldens live in tools/ui_goldens/ (committed). Diff tolerance is generous (RMS>12)
— it catches "page broke / went blank / theme leaked", not anti-aliasing noise.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import urllib.request

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
HERE = os.path.dirname(os.path.abspath(__file__))
GOLDENS = os.path.join(HERE, "ui_goldens")

# (name, path, size, must-contain markers in served HTML)
VIEWS = [
    ("canvas_empty", "/canvas",  "1600,1000", ["Director Canvas", "board-viewport", "[hidden]"]),
    ("story",        "/",        "1600,1000", []),
    ("studio",       "/studio",  "1600,1000", []),
    ("brand",        "/brand",   "1600,1000", []),
]


def shot(base: str, path: str, size: str, out: str) -> None:
    subprocess.run(
        [CHROME, "--headless", "--disable-gpu", "--hide-scrollbars",
         f"--window-size={size}", "--force-device-scale-factor=1",
         "--virtual-time-budget=6000", f"--screenshot={out}", base + path],
        capture_output=True, timeout=120)


def rms_diff(a: str, b: str) -> float:
    from PIL import Image, ImageChops
    ia, ib = Image.open(a).convert("RGB"), Image.open(b).convert("RGB")
    if ia.size != ib.size:
        ib = ib.resize(ia.size)
    h = ImageChops.difference(ia, ib).histogram()
    total = sum(h[i % 256] * (i % 256) ** 2 for i in range(len(h)))
    npix = ia.size[0] * ia.size[1] * 3
    return (total / npix) ** 0.5


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:7860")
    ap.add_argument("--run", default="", help="canvas run id for a populated-board view")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--threshold", type=float, default=12.0)
    args = ap.parse_args()

    views = list(VIEWS)
    if args.run:
        views.append(("canvas_run", f"/canvas?run={args.run}", "1600,1000",
                      ["Director Canvas"]))

    os.makedirs(GOLDENS, exist_ok=True)
    failures: list[str] = []
    for name, path, size, markers in views:
        # 1. HTTP + marker check on the served HTML
        try:
            with urllib.request.urlopen(args.base + path, timeout=15) as r:
                html = r.read().decode("utf-8", "replace")
                if r.status != 200:
                    failures.append(f"{name}: HTTP {r.status}")
                    continue
        except Exception as e:
            failures.append(f"{name}: unreachable ({e})")
            continue
        for m in markers:
            if m not in html:
                failures.append(f"{name}: marker missing from HTML: {m!r}")

        # 2. Screenshot + golden compare
        cur = os.path.join(tempfile.gettempdir(), f"uismoke_{name}.png")
        try:
            shot(args.base, path, size, cur)
        except Exception as e:
            failures.append(f"{name}: screenshot failed ({e})")
            continue
        if not os.path.exists(cur) or os.path.getsize(cur) < 20_000:
            failures.append(f"{name}: screenshot missing/blank")
            continue
        gold = os.path.join(GOLDENS, f"{name}.png")
        if args.update or not os.path.exists(gold):
            import shutil
            shutil.copy2(cur, gold)
            print(f"  [golden {'updated' if args.update else 'recorded'}] {name}")
            continue
        d = rms_diff(gold, cur)
        status = "OK" if d <= args.threshold else "DRIFT"
        print(f"  [{status}] {name}: rms={d:.1f} (threshold {args.threshold})")
        if d > args.threshold:
            failures.append(f"{name}: pixel drift rms={d:.1f} > {args.threshold} "
                            f"(compare {cur} vs {gold}; --update if intentional)")

    if failures:
        print("\nUI SMOKE FAILURES:")
        for f in failures:
            print(" ✗", f)
        return 1
    print("\nUI smoke: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
