"""
S34 batch runner — walk a folder of SKUs and shoot every one of them.

Point it at a root folder. It goes SKU folder by SKU folder, shoots the full campaign, and
writes the finished images back INTO that SKU's own folder (a `photoshoot/` subfolder, matching
the convention the agency already uses by hand).

A ledger records what has been done, keyed by a hash of the SKU's input photos plus the config
version. So:
  · re-running over 500 folders where 499 are finished does 1 folder of work
  · replacing one photo re-queues only that SKU
  · a crash or a Ctrl-C loses at most the SKU in flight

Usage:
    python tools/shoot_batch.py --inbox ~/Desktop/PhotoShoot                 # dry run, costs nothing
    python tools/shoot_batch.py --inbox ~/Desktop/PhotoShoot --go
    python tools/shoot_batch.py --inbox ~/Desktop/PhotoShoot --go --limit 3  # try 3 first
    python tools/shoot_batch.py --inbox ~/Desktop/PhotoShoot --go --force    # redo finished SKUs
    python tools/shoot_batch.py --inbox ~/Desktop/PhotoShoot --status        # what's done so far

See docs/PRODUCT_SHOOT_PLAN.md §11.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import sqlite3
import sys
import time

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Only load .env when this file is RUN as a script. Imported as a library (agents/shoot.py
# does this lazily inside a request) an override=True load would silently re-apply the
# on-disk .env over the server's own environment mid-request — which is how a production
# HOB_AUTH_DISABLED could be resurrected from a file nobody meant to deploy.
if __name__ == "__main__":
    load_dotenv(".env", override=True)

_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("cp", os.path.join(_here, "shoot_campaign.py"))
cp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cp)                       # reuse run_sku, don't fork the pipeline
bo = cp.bo

CONFIG_VERSION = "s34-p0-2026-08-09"               # bump to invalidate every finished job
LEDGER = os.path.join(os.path.expanduser("~"), ".hob_cache", "shoot_jobs.db")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")


# ── ledger ───────────────────────────────────────────────────────────────────

def _db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    con = sqlite3.connect(LEDGER)
    con.execute("""CREATE TABLE IF NOT EXISTS shoot_jobs (
        sku TEXT NOT NULL, input_hash TEXT NOT NULL, config_version TEXT NOT NULL,
        status TEXT NOT NULL, frames INT DEFAULT 0, cost_usd REAL DEFAULT 0,
        continuity TEXT DEFAULT '', error TEXT DEFAULT '', updated_at INT,
        PRIMARY KEY (sku, input_hash, config_version))""")
    con.commit()
    return con


def _input_hash(sku_dir: str) -> str:
    """Content hash of the SKU's INPUT photos only — outputs must not change the key, or
    every finished SKU would re-queue itself forever."""
    h = hashlib.sha256()
    for name in sorted(os.listdir(sku_dir)):
        path = os.path.join(sku_dir, name)
        if not os.path.isfile(path) or not name.lower().endswith(IMAGE_EXT):
            continue
        h.update(name.encode())
        with open(path, "rb") as f:
            h.update(f.read(1 << 20))              # first 1MB is plenty to fingerprint a photo
        h.update(str(os.path.getsize(path)).encode())
    return h.hexdigest()[:16]


def _done(con, sku: str, ih: str) -> bool:
    row = con.execute(
        "SELECT status FROM shoot_jobs WHERE sku=? AND input_hash=? AND config_version=?",
        (sku, ih, CONFIG_VERSION)).fetchone()
    # REVIEW counts as done for skip purposes — the frames shipped, a human decides.
    return bool(row) and row[0] in ("DONE", "REVIEW", "PARTIAL")


def _record(con, sku: str, ih: str, res: dict) -> None:
    con.execute("""INSERT OR REPLACE INTO shoot_jobs
        (sku, input_hash, config_version, status, frames, cost_usd, continuity, error, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        (sku, ih, CONFIG_VERSION, res.get("status", "FAILED"),
         len([f for f in res.get("frames", []) if f.get("ok")]),
         res.get("cost_usd", 0.0), json.dumps(res.get("continuity", {})),
         res.get("error", ""), int(time.time())))
    con.commit()


def _sku_dirs(inbox: str) -> list[str]:
    """A SKU folder is any subfolder holding at least one image at its own level."""
    out = []
    for name in sorted(os.listdir(inbox)):
        d = os.path.join(inbox, name)
        if not os.path.isdir(d) or name.startswith((".", "_")):
            continue
        if any(f.lower().endswith(IMAGE_EXT) for f in os.listdir(d)
               if os.path.isfile(os.path.join(d, f))):
            out.append(name)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inbox", required=True, help="folder containing one subfolder per SKU")
    ap.add_argument("--go", action="store_true", help="actually generate (default is a dry run)")
    ap.add_argument("--force", action="store_true", help="redo SKUs already marked done")
    ap.add_argument("--limit", type=int, default=0, help="stop after N SKUs")
    ap.add_argument("--status", action="store_true", help="print the ledger and exit")
    ap.add_argument("--model", default="nano_banana_edit")
    ap.add_argument("--shots", default="", help="override; default is the product type's list")
    ap.add_argument("--outdir", default="photoshoot",
                    help="subfolder inside each SKU folder for the results")
    ap.add_argument("--cap-sku", type=float, default=1.00,
                    help="max USD per SKU including re-rolls (0 = no cap)")
    ap.add_argument("--cap-run", type=float, default=0.0,
                    help="max USD for the whole run; stops cleanly when reached (0 = no cap)")
    a = ap.parse_args()

    inbox = os.path.expanduser(a.inbox)
    con = _db()

    if a.status:
        rows = con.execute("""SELECT sku, status, frames, cost_usd, continuity FROM shoot_jobs
                              WHERE config_version=? ORDER BY updated_at DESC""",
                           (CONFIG_VERSION,)).fetchall()
        if not rows:
            print("ledger empty")
            return 0
        print(f"{'sku':<26} {'status':<10} {'frames':>6} {'cost':>7}  continuity")
        print("─" * 78)
        for sku, st, fr, c, cont in rows:
            d = json.loads(cont or "{}")
            cs = (f"person {d.get('person','-')} loc {d.get('location','-')} "
                  f"hero {d.get('hero','-')} styling {d.get('styling','-')}") if d else ""
            print(f"{sku:<26} {st:<10} {fr:>6} ${c:>6.2f}  {cs}")
        print(f"\ntotal spent: ${sum(r[3] for r in rows):.2f} over {len(rows)} SKUs")
        return 0

    if not os.path.isdir(inbox):
        print(f"not a folder: {inbox}", file=sys.stderr)
        return 1

    skus = _sku_dirs(inbox)
    shots = [s.strip() for s in a.shots.split(",") if s.strip()] or None

    pending = []
    for sku in skus:
        ih = _input_hash(os.path.join(inbox, sku))
        if not a.force and _done(con, sku, ih):
            continue
        pending.append((sku, ih))
    if a.limit:
        pending = pending[:a.limit]

    from agents import model_router, pricing
    key = (model_router.model_field(a.model, "pricing_key") or "").split(".")[-1]
    per = pricing.load()["image_gen"].get(key, 0.05)
    est = per * (len(shots) if shots else 6) * len(pending)

    print(f"inbox   : {inbox}")
    print(f"found   : {len(skus)} SKU folders")
    print(f"pending : {len(pending)}   (already done: {len(skus) - len(pending)})")
    print(f"shots   : {', '.join(shots) if shots else 'auto by product type (6, or 7 for sets)'}")
    print(f"model   : {a.model}")
    print(f"estimate: ~${est:.2f}  (+ ~{len(pending) * ((len(shots) if shots else 6) + 1)} vision calls)")
    print(f"output  : <SKU>/{a.outdir}/")
    print(f"caps    : ${a.cap_sku:.2f}/SKU"
          f"{f' · ${a.cap_run:.2f}/run' if a.cap_run else ' · no run cap'}")
    if not a.go:
        print("\ndry run — nothing spent. Add --go to run.")
        if pending:
            print("first few:", ", ".join(s for s, _ in pending[:5]))
        return 0
    if not pending:
        print("\nnothing to do.")
        return 0

    spent, done, failed, review = 0.0, 0, 0, 0
    for n, (sku, ih) in enumerate(pending, 1):
        print(f"\n[{n}/{len(pending)}] {sku}")
        out_dir = os.path.join(inbox, sku, a.outdir)
        if a.cap_run and spent >= a.cap_run:
            print(f"\nrun cap ${a.cap_run:.2f} reached — stopping cleanly. "
                  f"Re-run to continue; the ledger keeps the finished SKUs.")
            break
        try:
            res = cp.run_sku(sku, inbox, a.model, shots, out_dir, cap=a.cap_sku)
        except KeyboardInterrupt:
            print("\ninterrupted — ledger is up to date, re-run to resume.")
            return 130
        except Exception as e:                     # one bad SKU must not stop the batch
            res = {"sku": sku, "status": "FAILED", "error": str(e)[:200],
                   "frames": [], "cost_usd": 0.0}
            print(f"    FAILED  {str(e)[:90]}")
        _record(con, sku, ih, res)
        spent += res.get("cost_usd", 0.0)
        if res.get("status") in ("DONE", "REVIEW", "PARTIAL"):
            done += 1
            if res.get("parked"):
                review += len(res["parked"])
            c = res.get("continuity", {})
            print(f"    {res['status']}  ${res['cost_usd']:.2f}  →  {out_dir}"
                  + (f"   parked: {', '.join(res['parked'])}" if res.get("parked") else ""))
            if c:
                print(f"    continuity: person {c['person']} location {c['location']} "
                      f"hero {c['hero']} styling {c['styling']}")
        else:
            failed += 1

    print(f"\n{'─' * 60}")
    print(f"done {done}   failed {failed}   frames parked for review {review}   "
          f"spent ${spent:.2f}")
    if review:
        print("parked frames are still written to disk — check `qc.reject_reason` in "
              "_campaign.json, then re-run with --force to retry that SKU.")
    print(f"results are inside each SKU folder under {a.outdir}/")
    print("re-run the same command any time — finished SKUs are skipped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
