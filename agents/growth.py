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
                    # STR-2: structured fields that make prevention + the contract
                    # validator deterministic (no NLP). Empty string when N/A.
                    "verbatim_quote": {"type": "string"},
                    "blocking": {"type": "string"},
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
                    "verbatim_quote",
                    "blocking",
                ],
            },
        },
        "posting_caption": {"type": "string"},
        "tone_note": {"type": "string"},
    },
    "required": ["frames", "posting_caption", "tone_note"],
}


def _clean_line(text: Any, limit: int = 600) -> str:
    out = re.sub(r"\s+", " ", str(text or "")).strip()
    out = out.replace("[", "(").replace("]", ")")
    if len(out) <= limit:
        return out
    # Don't cut mid-word (that's what dropped "Waiting for Papa" → "…Child's").
    # Trim to the last whole word inside the limit and signal the cut.
    cut = out[:limit]
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > 0 else cut).rstrip() + "…"


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
        "brands, or outcomes. Keep captions short and visual. The human operator edits before render.\n"
        "HARD CONTRACTS (the render pipeline enforces these — honoring them keeps the script honest):\n"
        "• A beat that SHOWS A PERSON uses visual_need=real_photo_preferred (operator pins a real "
        "photo) — NEVER ai_symbolic. ai_symbolic renders objects/textures/settings ONLY: its "
        "media_query must contain NO people, faces, hands, or figures.\n"
        "• If the source story has a pivotal QUOTED line, set verbatim_quote to the exact words AND "
        "make those words appear in this beat's caption or a text_card — never just tease it.\n"
        "• For any beat with TWO OR MORE people in action, fill blocking: who moves, who stays, the "
        "prop and the direction (e.g. 'daughter runs toward father at the door; he stands').\n"
        "• Flag uncertain facts and any real-person consent need in operator_note. Leave verbatim_quote "
        "and blocking as empty strings when they do not apply."
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
            "People-beats: visual_need=real_photo_preferred, never ai_symbolic.",
            "ai_symbolic media_query = objects/settings only, zero people.",
            "Pivotal quotes: set verbatim_quote AND put the words on screen.",
            "Two-person action beats: fill blocking (who moves toward whom + prop).",
        ],
    }
    raw = llm.chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ],
        # Headroom for a full structured draft (up to 20 frames × ~11 fields). At
        # 2200 a 10-frame draft truncated → JSON parse failed → silent fallback to
        # the dumb segmenter. Sonnet 4.6 caps at 64K output, so 6000 is safe.
        max_tokens=6000,
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
        # Note-class fields carry verbatim quotes, blocking and consent instructions —
        # generous caps (word-safe) so they no longer truncate mid-sentence (STR-2).
        media_query = _clean_line(frame.get("media_query"), 600)
        operator_note = _clean_line(frame.get("operator_note"), 600)
        verbatim_quote = _clean_line(frame.get("verbatim_quote"), 400)
        blocking = _clean_line(frame.get("blocking"), 400)
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
            f'verbatim_quote="{verbatim_quote}"' if verbatim_quote else "",
            f"blocking={blocking}" if blocking else "",
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


_TRANSLATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "frames": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "frame_id": {"type": "string"},
                    "caption": {"type": "string"},
                    "voiceover": {"type": "string"},
                },
                "required": ["frame_id", "caption", "voiceover"],
            },
        },
    },
    "required": ["frames"],
}


def _fid(frame: dict, index: int) -> str:
    """Stable per-frame key shared by the translation request and its result merge."""
    return str(frame.get("frame_id") or index)


def _translate_via_llm(frames: list[dict], lang_name: str) -> dict:
    from agents import llm

    system = (
        f"You are a professional subtitle and voiceover translator. Translate the "
        f"caption and voiceover of each Instagram Reel frame into {lang_name}, written "
        f"in that language's native script.\n"
        "RULES:\n"
        "• Natural, spoken phrasing — it is read aloud AND shown on screen, not a literal gloss.\n"
        "• Preserve meaning, tone, every name and every number exactly. Never add or drop a fact.\n"
        "• Keep captions short enough to fit a 9:16 phone screen.\n"
        "• Return every frame in the same order, keyed by its frame_id. Empty source → empty translation."
    )
    payload = {
        "target_language": lang_name,
        "frames": [
            {
                "frame_id": _fid(f, i),
                "caption": _clean_line(f.get("caption", ""), 600),
                "voiceover": _clean_line(f.get("voiceover") or f.get("caption", ""), 600),
            }
            for i, f in enumerate(frames)
        ],
    }
    raw = llm.chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        max_tokens=4000,
        temperature=0.2,
        model_tier="reasoning",
        json_schema={"name": "frame_translation", "schema": _TRANSLATION_SCHEMA},
    )
    return llm.json_loads_lenient(raw)


def caption_language_variants(frames: list[dict], languages: list[str]) -> dict:
    """Translate each frame's caption + voiceover into every operator-chosen language.

    `languages` is expected pre-validated against agents.languages.SUPPORTED_LANGUAGES
    by the caller; unknown codes are skipped here as a safety net. Each language is an
    independent LLM call so one failure never blocks the others.
    """
    from agents.languages import SUPPORTED_LANGUAGES, language_name

    out: dict[str, list[dict]] = {}
    for lang in languages:
        if lang not in SUPPORTED_LANGUAGES:
            continue
        name = language_name(lang)
        try:
            result = _translate_via_llm(frames, name)
            by_id = {str(t.get("frame_id")): t for t in (result.get("frames") or [])}
            status = "translated"
            note = f"Machine translation into {name}; operator should review before render."
        except Exception as e:
            by_id = {}
            status = "error"
            note = f"Translation into {name} failed — operator can supply captions manually: {e}"
        out[lang] = [
            {
                "frame_id": f.get("frame_id"),
                "source_caption": f.get("caption", ""),
                "draft_caption": (by_id.get(_fid(f, i)) or {}).get("caption", ""),
                "draft_voiceover": (by_id.get(_fid(f, i)) or {}).get("voiceover", ""),
                "status": status,
                "note": note,
            }
            for i, f in enumerate(frames)
        ]
    return out


def translate_frames(frames: list[dict], lang: str) -> list[dict]:
    """Return a copy of `frames` with caption + voiceover translated into `lang`,
    ready to hand to the voiceover / caption / assembly stages for a re-render that
    reuses the original visuals. Unknown language returns an untouched copy."""
    from agents.languages import SUPPORTED_LANGUAGES, language_name

    if lang not in SUPPORTED_LANGUAGES:
        return [dict(f) for f in frames]
    result = _translate_via_llm(frames, language_name(lang))
    by_id = {str(t.get("frame_id")): t for t in (result.get("frames") or [])}
    out = []
    for i, f in enumerate(frames):
        clone = dict(f)
        t = by_id.get(_fid(f, i)) or {}
        if t.get("caption"):
            clone["caption"] = t["caption"]
        if t.get("voiceover"):
            clone["voiceover"] = t["voiceover"]
        clone["lang"] = lang
        out.append(clone)
    return out


def render_variants(payload: dict) -> list[dict]:
    """Return governed rerender payload descriptors for format/cutdown pilots."""
    variants = []
    for orientation in payload.get("orientations") or ["portrait", "landscape"]:
        clone = dict(payload)
        clone["orientation"] = orientation
        variants.append({"kind": "orientation", "name": orientation, "payload": clone})
    return variants
