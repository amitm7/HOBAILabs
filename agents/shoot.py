"""
S34 product-photoshoot seam — the one entry point the web app and the CLI both use.

The vertical's implementation currently lives in `tools/shoot_bakeoff.py` and
`tools/shoot_campaign.py`, which grew there while the approach was being measured. This
module is the seam that keeps that an implementation detail: `web_app` imports
`agents.shoot`, never a path inside `tools/`.

`ponytail:` one documented indirection instead of a big mechanical move mid-build. When the
approach stops changing, move the bodies into this file and delete the loader — nothing that
imports `agents.shoot` has to change when that happens.

Public surface:
    scan(inbox)                 → SKU folders with their ledger status
    run_sku(inbox, sku, ...)    → shoot one SKU, return its manifest
    status()                    → the ledger
"""

import importlib.util
import os

_TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
_cache: dict = {}


def _mod(name: str):
    if name not in _cache:
        spec = importlib.util.spec_from_file_location(name, os.path.join(_TOOLS, f"{name}.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        _cache[name] = m
    return _cache[name]


def _batch():
    return _mod("shoot_batch")


def _campaign():
    return _mod("shoot_campaign")


def scan(inbox: str) -> list[dict]:
    """Every SKU folder under `inbox`, with whether it still needs shooting.

    `pending` is the ledger's answer, so the UI shows the same truth the CLI does — a SKU
    already shot is not offered again unless its photos changed.
    """
    b = _batch()
    inbox = os.path.expanduser(inbox)
    if not os.path.isdir(inbox):
        return []
    con = b._db()
    out = []
    for sku in b._sku_dirs(inbox):
        ih = b._input_hash(os.path.join(inbox, sku))
        out.append({"sku": sku, "input_hash": ih, "pending": not b._done(con, sku, ih)})
    con.close()
    return out


def run_sku(inbox: str, sku: str, *, model: str = "nano_banana_edit",
            outdir: str = "photoshoot", cap: float = 1.00,
            brand: str = "default", force: bool = False) -> dict:
    """Shoot one SKU and record it in the same ledger the CLI uses."""
    b, c = _batch(), _campaign()
    inbox = os.path.expanduser(inbox)
    sku_dir = os.path.join(inbox, sku)
    if not os.path.isdir(sku_dir):
        return {"sku": sku, "status": "FAILED", "error": "no such SKU folder",
                "frames": [], "cost_usd": 0.0}

    ih = b._input_hash(sku_dir)
    con = b._db()
    if not force and b._done(con, sku, ih):
        con.close()
        return {"sku": sku, "status": "SKIPPED", "frames": [], "cost_usd": 0.0}

    res = c.run_sku(sku, inbox, model, None, os.path.join(sku_dir, outdir),
                    quiet=True, brand=brand, cap=cap)
    b._record(con, sku, ih, res)
    con.close()
    return res


def retry_frame(inbox: str, sku: str, shot: str, *, model: str = "nano_banana_edit",
                outdir: str = "photoshoot", brand: str = "default",
                cap: float = 0.30) -> dict:
    """Re-shoot one frame of an already-shot SKU. Derived frames re-crop for free."""
    c = _campaign()
    inbox = os.path.expanduser(inbox)
    out_dir = os.path.join(inbox, sku, outdir)
    res = c.retry_shot(sku, inbox, shot, out_dir, model=model, brand=brand, cap=cap)
    if res.get("ok"):                       # keep the ledger's status in step with the manifest
        b = _batch()
        con = b._db()
        ih = b._input_hash(os.path.join(inbox, sku))
        man_path = os.path.join(out_dir, "_campaign.json")
        try:
            import json as _j
            man = _j.load(open(man_path))
            b._record(con, sku, ih, man)
        except Exception:
            pass
        con.close()
    return res


def campaign(inbox: str, sku: str, outdir: str = "photoshoot") -> dict:
    """The stored manifest for a shot SKU — frames, QC verdicts, what is parked."""
    p = os.path.join(os.path.expanduser(inbox), sku, outdir, "_campaign.json")
    if not os.path.exists(p):
        return {}
    import json as _j
    with open(p) as f:
        return _j.load(f)


def mint_personas(brand: str = "default", count: int = 30,
                  model: str = "nano_banana") -> dict:
    """Mint a brand's casting pool. One-time, and the faces every campaign inherits."""
    p = _mod("shoot_persona")
    spec = p.load_spec(brand, _mod("shoot_bakeoff").PERSONA)
    pool = p._mint(brand, spec, min(count, len(p.VARIANTS)), model)
    if not pool:
        return {"minted": 0, "error": "nothing minted"}
    import json as _j
    with open(p._pool_path(brand), "w") as f:
        _j.dump(pool, f, indent=2)
    if not os.path.exists(p.spec_path(brand)):
        with open(p.spec_path(brand), "w") as f:
            f.write(spec)
    return {"minted": len(pool), "brand": brand}


def set_persona_dropped(brand: str, ids: list[str], dropped: bool = True) -> dict:
    """Cull (or restore) faces. The brand's 20-minute review, done once."""
    p = _mod("shoot_persona")
    pool = p.load_pool(brand)
    for x in pool:
        if x["id"] in ids:
            x["dropped"] = dropped
    import json as _j
    with open(p._pool_path(brand), "w") as f:
        _j.dump(pool, f, indent=2)
    return {"active": len([x for x in pool if not x.get("dropped")]), "total": len(pool)}


def status(limit: int = 200) -> list[dict]:
    b = _batch()
    con = b._db()
    rows = con.execute(
        "SELECT sku, status, frames, cost_usd, updated_at FROM shoot_jobs "
        "WHERE config_version=? ORDER BY updated_at DESC LIMIT ?",
        (b.CONFIG_VERSION, limit)).fetchall()
    con.close()
    return [{"sku": r[0], "status": r[1], "frames": r[2],
             "cost_usd": r[3], "updated_at": r[4]} for r in rows]


def price_per_frame(model: str = "nano_banana_edit") -> float:
    from agents import model_router, pricing
    key = (model_router.model_field(model, "pricing_key") or "").split(".")[-1]
    return pricing.load()["image_gen"].get(key, 0.05)


def personas(brand: str = "default") -> list[dict]:
    """The brand's casting pool, for the onboarding/review screen."""
    p = _mod("shoot_persona")
    return [{k: v for k, v in x.items() if k != "variant"} for x in p.load_pool(brand)]
