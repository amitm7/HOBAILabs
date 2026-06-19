"""HOB IP / property watermark resolver.

Each reel belongs to one IP (HOB Originals, The HOB Show, Unfiltered HOB, …); its
transparent PNG is laid over the whole video in BOTH story and brand modes. This is
HOB's own property branding — separate from the brand-collab advertiser logo.

Registry: config/watermarks.json maps IP name → PNG filename in deploy/watermarks/.
Everything degrades to a no-op: an unknown IP or a missing PNG returns "" so the
render proceeds without a watermark instead of failing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_CONFIG = _ROOT / "config" / "watermarks.json"
_DIR = _ROOT / "deploy" / "watermarks"


def _load() -> dict:
    try:
        return json.loads(_CONFIG.read_text(encoding="utf-8")).get("ips", {}) or {}
    except Exception:
        return {}


def list_ips() -> list[dict]:
    """[{id, label, available}] for the UI dropdown. `available` = PNG present."""
    out = []
    for ip_id, fname in _load().items():
        path = _DIR / fname if fname else None
        out.append({
            "id": ip_id,
            "label": ip_id,
            "available": bool(fname and path and path.exists()),
        })
    return out


def watermark_for(ip_id: str) -> str:
    """Resolve an IP name → absolute PNG path, or "" if unknown/not yet added."""
    fname = (_load().get((ip_id or "").strip(), "") or "").strip()
    if not fname:
        return ""
    path = _DIR / fname
    return str(path) if path.exists() else ""
