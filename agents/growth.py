"""Low-cost growth pilot helpers.

These functions produce editable drafts and metadata only. They intentionally do
not auto-render or multiply spend; routes using them sit behind governance.
"""

from __future__ import annotations

import re


def story_to_script(story: str, max_frames: int = 10) -> str:
    """Segment long prose into an editable Format B draft scaffold."""
    text = re.sub(r"\s+", " ", (story or "").strip())
    if not text:
        return "Reels\n\nCaption:\n"
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
    lines = ["Reels", ""]
    for i, beat in enumerate(beats[:max_frames], 1):
        words = beat.split()
        caption = " ".join(words[:18]).rstrip(",;:")
        lines += [f"Frame {i}", caption, ""]
    lines += ["Caption:", text]
    return "\n".join(lines)


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
