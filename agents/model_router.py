"""
Model router — picks the best model per shot, or honors an explicit override.

Replaces the old "one global provider for every frame" behaviour. Reads the
declarative catalog in config/models.json (capabilities + routing policy) and
maps each shot to a model id, cost-tier aware:

  · explicit override (UI dropdown / [model:] tag) always wins
  · real photos/videos are NEVER sent to the image step  → "passthrough"
  · otherwise: shot_type + cost_tier → first available model in models.json

cost_tier is derived from the run quality: dev/preview → "draft" (cheap models),
production → "premium" (best models). This bakes in the "test cheap, finish
expensive" discipline.

Pure logic + JSON read — no API calls, unit-testable.
"""

import json
import os

_CATALOG = None
_CATALOG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "models.json")

PASSTHROUGH = "passthrough"   # real media — skip generation at the image step


def catalog() -> dict:
    """Load (and cache) config/models.json."""
    global _CATALOG
    if _CATALOG is None:
        with open(os.path.abspath(_CATALOG_PATH)) as f:
            _CATALOG = json.load(f)
    return _CATALOG


def models() -> dict:
    return catalog().get("models", {})


def get_model(model_id: str) -> dict:
    return models().get(model_id, {})


def model_field(model_id: str, field: str, default=None):
    return get_model(model_id).get(field, default)


def cost_tier_from_quality(quality: str) -> str:
    """dev/preview → draft (cheap); production/final → premium."""
    return "draft" if (quality or "dev").lower() in ("dev", "draft", "preview") else "premium"


# ── Shot classification (uses metadata the pipeline already produces) ─────────

_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".m4v", ".webm"}


def _is_real_media(shot: dict) -> bool:
    """True when the frame is anchored to a user-supplied photo/video."""
    spec = (shot.get("photo_spec") or "").strip()
    if spec.startswith("ai_"):
        return False
    if spec:                                   # explicit filename pin
        return True
    vp = shot.get("visual_path") or ""
    return bool(vp) and os.path.exists(vp)     # auto-matched real file


def _is_video_source(shot: dict) -> bool:
    for p in (shot.get("visual_path", ""), shot.get("photo_spec", "")):
        if p and os.path.splitext(p)[1].lower() in _VIDEO_EXTS:
            return True
    return False


def _image_shot_type(shot: dict) -> str:
    spec = (shot.get("photo_spec") or "").strip()
    if spec == "ai_symbolic":
        return "object"
    return "face"          # ai_portrait or contextual fallback


def _video_shot_type(shot: dict) -> str:
    if shot.get("lipsync"):
        return "dialogue"
    if _is_real_media(shot):
        return "real"
    spec = (shot.get("photo_spec") or "").strip()
    if spec == "ai_symbolic":
        return "landscape"
    if shot.get("is_hero") or shot.get("frame_index") == 0:
        return "hero"
    return "face"


# ── Selection ────────────────────────────────────────────────────────────────

def _valid_override(override: str, kind: str) -> bool:
    o = (override or "").strip().lower()
    return o not in ("", "auto") and model_field(o, "kind") == kind


def select_model(kind: str, shot: dict, cost_tier: str = "draft",
                 override: str = "") -> str:
    """
    Return a model id for this shot, or "passthrough" (image step, real media).
    kind: "image" | "video".  cost_tier: "draft" | "premium".
    """
    cat = catalog()

    # 1. Explicit override wins (when it's a real model of the right kind).
    if _valid_override(override, kind):
        return override.strip().lower()

    # 2. Real media never gets AI-generated at the image step.
    if kind == "image" and (_is_real_media(shot) or _is_video_source(shot)):
        return PASSTHROUGH

    # 3. Auto-route by shot type + cost tier.
    shot_type = _image_shot_type(shot) if kind == "image" else _video_shot_type(shot)
    tier = cost_tier if cost_tier in ("draft", "premium") else "draft"
    prefs = (cat.get("routing", {}).get(kind, {}).get(shot_type, {}).get(tier, []))
    for mid in prefs:
        if mid in models():
            return mid

    # 4. Fallback to the configured default for this kind.
    return cat.get("defaults", {}).get(kind, "")


# ── Vendor fallbacks (Gap #8) ──────────────────────────────────────────────────
def fallbacks_for(model_id: str) -> list[str]:
    """Configured cross-vendor alternates for a model (config/models.json)."""
    return [m for m in catalog().get("fallbacks", {}).get(model_id, []) if m in models()]


def candidates(kind: str, shot: dict, cost_tier: str = "draft", override: str = "") -> list[str]:
    """Ordered failover chain for a shot: primary, then same-slot routing prefs,
    then the primary's configured alternates, then the kind default. Deduped,
    existing models only. Real/passthrough media yields just that sentinel."""
    primary = select_model(kind, shot, cost_tier, override)
    if primary in ("", PASSTHROUGH) or kind == "image" and (_is_real_media(shot) or _is_video_source(shot)):
        return [primary] if primary else []

    cat = catalog()
    shot_type = _image_shot_type(shot) if kind == "image" else _video_shot_type(shot)
    tier = cost_tier if cost_tier in ("draft", "premium") else "draft"
    ordered = [primary]
    ordered += cat.get("routing", {}).get(kind, {}).get(shot_type, {}).get(tier, [])
    ordered += fallbacks_for(primary)
    ordered.append(cat.get("defaults", {}).get(kind, ""))

    seen, out = set(), []
    for mid in ordered:
        if mid and mid in models() and mid not in seen:
            seen.add(mid)
            out.append(mid)
    return out


def run_with_fallback(cands, attempt, *, axis: str = "", logger=None):
    """Try each candidate model until one succeeds; return (result, model_id).

    `attempt(model_id)` performs the real vendor call. A raised exception or a
    falsy result is treated as a failure and the next candidate is tried, so one
    vendor outage degrades independently. Raises the last error if all fail.
    """
    last_exc = None
    for mid in cands:
        try:
            result = attempt(mid)
        except Exception as e:           # vendor error / no key / no balance / timeout
            last_exc = e
            if logger:
                logger(f"[fallback] {axis or 'vendor'}: {mid} failed ({e}); trying next")
            continue
        if result:
            return result, mid
        if logger:
            logger(f"[fallback] {axis or 'vendor'}: {mid} returned no result; trying next")
    if last_exc:
        raise last_exc
    raise RuntimeError(f"all vendors failed for {axis or 'axis'}: {list(cands)}")
