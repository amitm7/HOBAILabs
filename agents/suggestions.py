"""
Per-frame creative suggestions for the UI (parse-time, cheap, optional).

The operator shouldn't have to know cinematography. For each beat this proposes
a few PICKABLE options the user can click to fill — and then freely edit — the
camera, image-edit, and director-note fields. Nothing is auto-applied: these are
suggestions, and every target field stays a free-text input the user controls.

One batched fast-tier LLM call for the whole script. Safe no-op on any failure
(frames simply carry no suggestions and the fields stay empty/auto as before).
"""

_SCHEMA = {"name": "suggestions", "schema": {
    "type": "object", "additionalProperties": False,
    "properties": {
        "frames": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "frame_id": {"type": "string"},
                "camera":   {"type": "array", "items": {"type": "string"}},
                "edits":    {"type": "array", "items": {"type": "string"}},
                "notes":    {"type": "array", "items": {"type": "string"}},
            },
            "required": ["frame_id", "camera", "edits", "notes"],
        }},
    },
    "required": ["frames"],
}}

_PROMPT = (
    "You are a reel director proposing OPTIONS an operator can pick from (they "
    "can also edit or ignore them). For each story beat below, suggest:\n"
    "- camera: 2-3 camera moves/angles in plain words (e.g. 'slow push-in', "
    "'crane up', 'extreme close-up', '360 orbit', 'static hold').\n"
    "- edits: 1-3 optional photo-edit ideas that heighten the mood (e.g. 'add "
    "soft monsoon rain', 'warmer golden light', 'mist in the background'). Empty "
    "list if the beat needs none.\n"
    "- notes: 1-2 short director notes — emotional direction, NOT description "
    "(e.g. 'show quiet defiance, not just sadness', 'hold on the hands').\n"
    "Keep every item under 8 words. Match the beat's emotion and story position.\n\n"
    "BEATS (id: caption):\n{beats}\n\n"
    'Reply ONLY as JSON: {{"frames":[{{"frame_id","camera":[],"edits":[],"notes":[]}}]}}.'
)


def suggest_for_frames(frames: list[dict], max_each: int = 3) -> None:
    """Attach f['suggestions'] = {camera:[], edits:[], notes:[]} to each captioned
    frame, in place. Best-effort: silent on failure."""
    captioned = [f for f in frames if (f.get("caption") or "").strip()]
    if not captioned:
        return
    beats = "\n".join(f"{f['frame_id']}: {f['caption'].strip()}" for f in captioned)
    try:
        from agents import llm
        text = llm.chat(
            [{"role": "user", "content": _PROMPT.format(beats=beats)}],
            json_mode=True, json_schema=_SCHEMA,
            max_tokens=1800, model_tier="fast",
        )
        data = llm.json_loads_lenient(text)
    except Exception as e:
        print(f"[Suggestions] skipped ({e})")
        return

    by_id = {row.get("frame_id"): row for row in data.get("frames", []) if row.get("frame_id")}
    for f in frames:
        row = by_id.get(f["frame_id"])
        if not row:
            continue
        f["suggestions"] = {
            "camera": [s for s in (row.get("camera") or []) if s][:max_each],
            "edits":  [s for s in (row.get("edits")  or []) if s][:max_each],
            "notes":  [s for s in (row.get("notes")  or []) if s][:max_each],
        }


# ── Vision-grounded per-frame suggestion (post-Preview, triggered, cached) ─────
# The standard chips above are TEXT-only (the image doesn't exist at parse time).
# Once a still exists, this proposes the single best camera move + one director
# note by actually LOOKING at the frame — far more accurate. Triggered per-frame
# from the UI (not automatic) and cached by image+caption so re-clicks never re-pay.
# Image edits are intentionally NOT suggested — that's the operator's creative call.

# Canonical camera vocabulary the model must pick from (matches the UI dropdown).
CAMERA_MOVES = [
    "static", "slow push in", "dolly in", "dolly out", "crane up", "crane down",
    "tilt up", "tilt down", "arc left", "arc right", "360 orbit", "crash zoom in",
    "crash zoom out", "overhead", "dutch angle", "Hitchcock zoom",
    "extreme close on eyes", "handheld", "bullet time", "super 8mm", "whip pan",
]

_IMG_SCHEMA = {"name": "frame_suggestion", "schema": {
    "type": "object", "additionalProperties": False,
    "properties": {"camera": {"type": "string"}, "note": {"type": "string"}},
    "required": ["camera", "note"],
}}


def _img_cache():
    import os
    from pathlib import Path
    from agents._kv import KVStore
    return KVStore(Path(os.environ.get(
        "HOB_CACHE_DIR", str(Path.home() / ".hob_cache"))) / "frame_suggestions.db",
        table="frame_suggest")


def suggest_from_image(image_path: str, caption: str = "", options: list[str] | None = None) -> dict:
    """Look at the actual still + caption → {camera, note}: the single best camera
    move (from `options`) and one short director note. Cached by image bytes +
    caption. Returns {} on any failure (caller treats as no-op)."""
    import hashlib
    import json
    import os
    if not image_path or not os.path.exists(image_path):
        return {}
    options = options or CAMERA_MOVES
    try:
        with open(image_path, "rb") as fp:
            digest = hashlib.md5(fp.read() + caption.encode("utf-8") + b"|v1").hexdigest()
    except OSError:
        return {}

    cache = _img_cache()
    hit = cache.get(digest)
    if hit:
        try:
            return json.loads(hit)
        except Exception:
            pass

    sys_prompt = (
        "You are a reel director. LOOK at this frame image and its caption, then:\n"
        f"- camera: choose the SINGLE best camera move from EXACTLY this list: {options}. "
        "Pick what suits what you actually see (subject position, space, energy).\n"
        "- note: one short director note (<8 words) — emotional direction tailored to "
        "the image (e.g. 'hold on the eyes, quiet defiance').\n"
        'Reply ONLY as JSON: {"camera": "<one of the list>", "note": "<short note>"}.'
    )
    try:
        from agents import llm
        raw = llm.chat(
            [{"role": "system", "content": sys_prompt},
             {"role": "user", "content": [
                 {"type": "text", "text": f"Caption: {caption or '(silent frame)'}"},
                 {"type": "image", "path": image_path}]}],
            json_mode=True, json_schema=_IMG_SCHEMA, max_tokens=200, model_tier="fast",
        )
        data = llm.json_loads_lenient(raw)
    except Exception as e:
        print(f"[Suggestions] vision suggest skipped ({e})")
        return {}

    cam = (data.get("camera") or "").strip()
    if cam and cam not in options:   # snap free-text to the closest known move
        low = cam.lower()
        cam = next((m for m in options if m.lower() in low or low in m.lower()), cam)
    out = {"camera": cam, "note": (data.get("note") or "").strip()}
    try:
        cache.set(digest, json.dumps(out))
    except Exception:
        pass
    return out
