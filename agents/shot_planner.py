"""
Shot planner (Studio Mode, MODE3_PLAN P2): turn a free-text brief into an
editable frames[] list — the SAME frame schema the Story/Brand front doors
produce, so the rest of the engine (`_build_frames_from_payload`,
`_generate_stills`, `clip_builder`, `assembler`) runs unchanged.

Two scopes:
- "commerce": one locked subject × N camera setups (intro → lookbook → detail →
  over-shoulder → walking → product hero → final). Mirrors the jewelry/fashion
  ad workflow. Product beats are flagged so the real product image is used.
- "general": emotional beats, like Story mode.

Pluggable + safe:
- One LLM call via agents/llm.py (NEVER a vendor SDK), json_schema-validated.
- Disk-cached by a hash of (brief, scope, talent, product) — re-planning is free.
- Graceful fallback to a sentence/line split so a planning failure never blocks
  the user (build-feature rule #4).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from agents import llm

PLAN_CACHE_DIR = Path.home() / ".hob_cache" / "shot_plans"
PLAN_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Default per-shot negative prompt surfaced in the UI (editable). Mirrors the
# continuity-lock discipline from the masterclass samples.
DEFAULT_NEGATIVE = (
    "blurry, distorted, deformed hands, extra limbs, extra fingers, "
    "morphing face, facial inconsistency, logo distortion, text artifacts, "
    "watermark, low quality, oversaturated, cartoon, anime"
)

_PLAN_SCHEMA = {"name": "shot_plan", "schema": {
    "type": "object", "additionalProperties": False,
    "properties": {
        "frames": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "caption":        {"type": "string"},
                "director_note":  {"type": "string"},
                "motion":         {"type": "string"},
                "shot_size":      {"type": "string"},
                "product_beat":   {"type": "boolean"},
                "uses_talent":    {"type": "boolean"},
                "duration":       {"type": "number"},
            },
            "required": ["caption", "director_note", "motion", "shot_size",
                         "product_beat", "uses_talent", "duration"],
        }},
    },
    "required": ["frames"],
}}

_COMMERCE_SYSTEM = """You are a commercial director planning a vertical 9:16 product/fashion ad reel.
You receive a brief plus (optionally) a locked Talent (the on-screen person) and a locked Product.
Plan ONE subject across a sequence of camera setups that cut together as a premium ad:
intro pose → lookbook/lifestyle → product detail close-up → over-shoulder/angle → motion/walking → PRODUCT HERO → final hero frame.
Rules:
- The SAME person and the SAME product across every shot (continuity is everything).
- Mark product_beat=true on the close-up/hero shots that must show the REAL product crisply.
- Mark uses_talent=true on every shot that shows the locked person.
- caption = the on-screen / spoken line for that beat (you MAY write it; keep it short and natural).
- director_note = visual direction (framing, lighting, posture) — never on-screen text.
- motion = ONE short camera move (e.g. "slow push-in", "lookbook pan", "macro pull-back", "360 orbit").
- shot_size = wide establishing | medium | close-up | extreme close-up | detail insert.
- 6-8 frames. Ground everything in real, premium e-commerce aesthetics."""

_GENERAL_SYSTEM = """You are a cinematic director planning a vertical 9:16 short reel from a free-text brief.
Produce an emotional beat sequence that cuts together as one film (not a slideshow).
Rules:
- caption = the on-screen / spoken line for that beat (short, natural; you MAY write it).
- director_note = visual direction for the shot (framing, light, posture) — never on-screen text.
- motion = ONE short camera move.
- shot_size = wide establishing | medium | close-up | extreme close-up | detail insert. Vary it between consecutive beats.
- product_beat=false and uses_talent=true unless the beat is clearly an object-only/symbolic shot.
- 5-8 frames. Ground everything in authentic, specific real-world detail."""


def _cache_key(brief: str, scope: str, talent: dict | None, product: dict | None) -> str:
    h = hashlib.md5()
    h.update(f"{scope}|{brief}".encode())
    if talent:
        h.update(f"|tal:{talent.get('name','')}|{talent.get('descriptor','')}".encode())
    if product:
        h.update(f"|prd:{product.get('name','')}|{json.dumps(product.get('specs',{}),sort_keys=True)}".encode())
    return h.hexdigest()


def _user_message(brief: str, scope: str, talent: dict | None, product: dict | None) -> str:
    lines = [f"Brief:\n{brief.strip()[:4000]}"]
    if talent:
        d = talent.get("descriptor", "")
        lines.append(f"\nLocked Talent (on screen in every uses_talent shot): "
                     f"{talent.get('name','')}" + (f" — {d}" if d else ""))
    if product and scope == "commerce":
        specs = product.get("specs", {})
        spec_str = ", ".join(f"{k}: {v}" for k, v in specs.items()) if specs else ""
        lines.append(f"\nLocked Product (must appear identical, crisp on product_beat shots): "
                     f"{product.get('name','')}" + (f" ({spec_str})" if spec_str else ""))
    return "\n".join(lines)


def _to_frames(raw_frames: list[dict], scope: str) -> list[dict]:
    """Normalise planner output into the UI frame schema."""
    frames = []
    for i, rf in enumerate(raw_frames, start=1):
        fid = f"f{i:02d}"
        product_beat = bool(rf.get("product_beat"))
        uses_talent = bool(rf.get("uses_talent", not product_beat))
        # Object-only/symbolic shots that don't show the talent → ai_symbolic.
        photo_spec = "ai_symbolic" if (not uses_talent and not product_beat) else "ai_portrait"
        try:
            dur = float(rf.get("duration") or 0) or 5.0
        except (TypeError, ValueError):
            dur = 5.0
        frames.append({
            "frame_id":        fid,
            "caption":         (rf.get("caption") or "").strip(),
            "director_note":   (rf.get("director_note") or "").strip(),
            "motion_override": (rf.get("motion") or "").strip(),
            "photo_spec":      photo_spec,
            "product_beat":    product_beat,
            "uses_talent":     uses_talent,
            "shot_size":       (rf.get("shot_size") or "").strip(),
            "duration":        round(max(2.0, min(15.0, dur)), 1),
            "negative_prompt": DEFAULT_NEGATIVE,
            "continuity_lock": "",
        })
    return frames


def _fallback_frames(brief: str, scope: str) -> list[dict]:
    """No-LLM degradation: split the brief into beats so the user still gets cards."""
    chunks = [c.strip() for c in re.split(r"(?<=[.!?])\s+|\n+", brief or "") if c.strip()]
    if not chunks:
        chunks = ["Opening shot", "Detail shot", "Hero shot"]
    chunks = chunks[:8]
    raw = []
    n = len(chunks)
    for i, c in enumerate(chunks):
        is_hero = (i == n - 1)
        raw.append({
            "caption": c[:120], "director_note": "",
            "motion": "slow push-in" if not is_hero else "macro pull-back",
            "shot_size": "close-up" if (scope == "commerce" and i % 2) else "medium",
            "product_beat": bool(scope == "commerce" and is_hero),
            "uses_talent": True, "duration": 5.0,
        })
    return _to_frames(raw, scope)


def plan(brief: str, *, scope: str = "general", talent: dict | None = None,
         product: dict | None = None, mood: str = "") -> list[dict]:
    """
    Expand a brief into an editable frames[] list. Cached + safe.
    scope: "commerce" | "general". talent/product: optional locked-asset dicts
    (as returned by product_surface.get_talent / get_product).
    """
    brief = (brief or "").strip()
    if not brief:
        return _fallback_frames("", scope)

    scope = scope if scope in ("commerce", "general") else "general"
    key = _cache_key(brief, scope, talent, product)
    cache_path = PLAN_CACHE_DIR / f"{key}.json"
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text())
        except Exception:
            pass

    system = _COMMERCE_SYSTEM if scope == "commerce" else _GENERAL_SYSTEM
    if mood:
        system += f"\nOverall mood: {mood}."
    try:
        text = llm.chat(
            [{"role": "system", "content": system},
             {"role": "user", "content": _user_message(brief, scope, talent, product)}],
            json_mode=True, json_schema=_PLAN_SCHEMA,
            temperature=0.8, max_tokens=2500, model_tier="reasoning",
        )
        raw = llm.json_loads_lenient(text)
        frames = _to_frames(raw.get("frames", []), scope)
        if not frames:
            frames = _fallback_frames(brief, scope)
        cache_path.write_text(json.dumps(frames))
        print(f"[ShotPlanner] {scope}: planned {len(frames)} shots")
        return frames
    except Exception as e:
        print(f"[ShotPlanner] plan failed ({e}) — falling back to a sentence split")
        return _fallback_frames(brief, scope)
