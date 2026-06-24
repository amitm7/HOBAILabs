"""Low-cost growth helpers.

Story intake is intentionally a draft step: AI proposes editable Format B frames,
then the operator reviews/edits before any render spend happens.
"""

from __future__ import annotations

import json
import re
from typing import Any


_STORY_FRAME_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "frames": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "role": {
                        "type": "string",
                        "enum": ["hook", "context", "struggle", "turning_point", "outcome", "cta"],
                    },
                    "caption": {"type": "string"},
                    "voiceover": {"type": "string"},
                    "visual_need": {
                        "type": "string",
                        "enum": ["real_photo_preferred", "real_video_preferred", "ai_symbolic", "text_card"],
                    },
                    "media_query": {"type": "string"},
                    "motion_hint": {"type": "string"},
                    "duration": {"type": "number"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "operator_note": {"type": "string"},
                },
                "required": [
                    "role",
                    "caption",
                    "voiceover",
                    "visual_need",
                    "media_query",
                    "motion_hint",
                    "duration",
                    "confidence",
                    "operator_note",
                ],
            },
        },
        "posting_caption": {"type": "string"},
        "tone_note": {"type": "string"},
    },
    "required": ["frames", "posting_caption", "tone_note"],
}


def _clean_line(text: Any, limit: int = 220) -> str:
    out = re.sub(r"\s+", " ", str(text or "")).strip()
    out = out.replace("[", "(").replace("]", ")")
    return out[:limit].rstrip()


def _fallback_story_draft(story: str, max_frames: int = 10) -> dict:
    text = re.sub(r"\s+", " ", (story or "").strip())
    if not text:
        return {"frames": [], "posting_caption": "", "tone_note": "Empty story fallback."}
    sentences = re.split(r"(?<=[.!?])\s+", text)
    beats = [s.strip() for s in sentences if s.strip()]
    if len(beats) > max_frames:
        chunk = max(1, len(beats) // max_frames)
        merged = []
        for i in range(0, len(beats), chunk):
            merged.append(" ".join(beats[i:i + chunk]))
            if len(merged) >= max_frames:
                break
        beats = merged
    frames = []
    for i, beat in enumerate(beats[:max_frames], 1):
        words = beat.split()
        caption = " ".join(words[:18]).rstrip(",;:")
        role = "hook" if i == 1 else ("outcome" if i == len(beats[:max_frames]) else "context")
        frames.append({
            "role": role,
            "caption": caption,
            "voiceover": beat,
            "visual_need": "real_photo_preferred" if i % 2 else "ai_symbolic",
            "media_query": caption,
            "motion_hint": "slow push-in" if i == 1 else "gentle motion",
            "duration": 4.0,
            "confidence": "low",
            "operator_note": "Fallback segmentation; review this beat before rendering.",
        })
    return {"frames": frames, "posting_caption": text, "tone_note": "Offline fallback draft."}


def _story_draft_via_llm(story: str, *, max_frames: int, target_seconds: int = 45,
                         tone: str = "", audience: str = "") -> dict:
    from agents import llm

    system = (
        "You are the HOBAILabs story director. Turn raw human stories into short, editable "
        "Instagram Reel frame plans. Preserve dignity. Do not invent facts, numbers, claims, "
        "brands, or outcomes. Prefer real media when the beat needs the real person/product; "
        "use ai_symbolic for abstract/trauma/pain beats; use text_card only for punchy statements. "
        "Keep captions short and visual. The human operator will edit before render."
    )
    user = {
        "story": story,
        "max_frames": max_frames,
        "target_seconds": target_seconds,
        "tone": tone or "emotional, respectful, hopeful",
        "audience": audience or "Instagram Reels viewers",
        "instructions": [
            "Create a complete mini-arc: hook, context, struggle, turning point, outcome.",
            "Use 6-12 frames unless max_frames is lower.",
            "Each caption should fit on screen and avoid long paragraphs.",
            "Flag uncertain facts in operator_note instead of inventing.",
        ],
    }
    raw = llm.chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ],
        max_tokens=2200,
        temperature=0.35,
        model_tier="reasoning",
        json_schema={"name": "story_frame_draft", "schema": _STORY_FRAME_SCHEMA},
    )
    return llm.json_loads_lenient(raw)


def story_to_draft(story: str, max_frames: int = 10, *, target_seconds: int = 45,
                   tone: str = "", audience: str = "") -> dict:
    """Return an editable story-frame draft with status and Format B script."""
    try:
        max_frames = max(1, min(int(max_frames or 10), 20))
    except Exception:
        max_frames = 10
    try:
        target_seconds = max(15, min(int(target_seconds or 45), 180))
    except Exception:
        target_seconds = 45

    status = "ai_draft"
    note = "AI-generated editable draft. Review every frame before preview/render."
    try:
        draft = _story_draft_via_llm(
            story,
            max_frames=max_frames,
            target_seconds=target_seconds,
            tone=tone,
            audience=audience,
        )
    except Exception as e:
        draft = _fallback_story_draft(story, max_frames=max_frames)
        status = "fallback_draft"
        note = f"Fallback draft used because AI story intake was unavailable: {e}"

    frames = draft.get("frames") if isinstance(draft, dict) else []
    if not isinstance(frames, list) or not frames:
        draft = _fallback_story_draft(story, max_frames=max_frames)
        frames = draft.get("frames", [])
        status = "fallback_draft"
        note = "Fallback draft used because AI returned no usable frames."

    frames = frames[:max_frames]
    posting_caption = _clean_line((draft or {}).get("posting_caption") or story, 1800)
    script = draft_to_format_b(frames, posting_caption)
    return {
        "status": status,
        "confidence": "ai" if status == "ai_draft" else "fallback",
        "note": note,
        "script": script,
        "frames_meta": frames,
        "tone_note": _clean_line((draft or {}).get("tone_note", ""), 300),
    }


def _photo_annotation(visual_need: str) -> str:
    if visual_need == "ai_symbolic":
        return "[photo: ai_symbolic]"
    return ""


def draft_to_format_b(frames: list[dict], posting_caption: str = "") -> str:
    """Convert structured story-frame metadata into the existing Format B script."""
    lines = ["Reels", ""]
    for i, frame in enumerate(frames, 1):
        caption = _clean_line(frame.get("caption"), 140) or f"Story beat {i}"
        visual_need = _clean_line(frame.get("visual_need"), 80)
        media_query = _clean_line(frame.get("media_query"), 180)
        operator_note = _clean_line(frame.get("operator_note"), 180)
        confidence = _clean_line(frame.get("confidence"), 30)
        role = _clean_line(frame.get("role"), 40)
        motion = _clean_line(frame.get("motion_hint"), 120)
        try:
            duration = max(2.5, min(float(frame.get("duration") or 4), 9.0))
        except Exception:
            duration = 4.0

        note_parts = [
            f"role={role}" if role else "",
            f"visual_need={visual_need}" if visual_need else "",
            f"media_query={media_query}" if media_query else "",
            f"confidence={confidence}" if confidence else "",
            operator_note,
        ]
        lines += [f"Frame {i}", caption]
        photo = _photo_annotation(visual_need)
        if photo:
            lines.append(photo)
        if visual_need == "text_card":
            lines.append("[layout: text_card]")
        if motion:
            lines.append(f"[camera: {motion}]")
        lines.append(f"[note: {' | '.join(p for p in note_parts if p)}]")
        lines.append(f"[duration: {duration:.1f}s]")
        lines.append("")
    lines += ["Caption:", posting_caption.strip()]
    return "\n".join(lines)


def story_to_script(story: str, max_frames: int = 10, **kwargs) -> str:
    """Return an editable Format B draft script for route/backward compatibility."""
    return story_to_draft(story, max_frames=max_frames, **kwargs)["script"]


def hook_candidates(frames: list[dict]) -> list[dict]:
    """Return editable first-frame alternatives without fake prediction scores."""
    first = (frames[0].get("caption") if frames else "") or "This story changed everything."
    compact = first[:90].rstrip()
    return [
        {"line": compact, "note": "Original direct hook", "confidence": "placeholder"},
        {"line": "I almost gave up before everything changed.", "note": "Curiosity gap draft", "confidence": "placeholder"},
        {"line": "Nobody believed this journey would end here.", "note": "Conflict-first draft", "confidence": "placeholder"},
    ]


def caption_language_variants(frames: list[dict], languages: list[str]) -> dict:
    """Caption-localization scaffold; not a translation until wired to an LLM."""
    out = {}
    for lang in languages:
        out[lang] = [
            {
                "frame_id": f.get("frame_id"),
                "source_caption": f.get("caption", ""),
                "draft_caption": "",
                "status": "draft_scaffold",
                "note": f"Translation for {lang} must be supplied by operator or LLM.",
            }
            for f in frames
        ]
    return out


def render_variants(payload: dict) -> list[dict]:
    """Return governed rerender payload descriptors for format/cutdown pilots."""
    variants = []
    for orientation in payload.get("orientations") or ["portrait", "landscape"]:
        clone = dict(payload)
        clone["orientation"] = orientation
        variants.append({"kind": "orientation", "name": orientation, "payload": clone})
    return variants
