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
