"""Posting kit — the caption + hashtags + cover pick that ship WITH a finished reel.

Extracted from web_app.py (S31): it was inline in the Story door's route layer, so the
only surface that could produce it was /story — Canvas, which is becoming the one
creator mode, had no access to it at all. Post-render packaging is an engine service,
not a property of whichever door happened to grow it first.

Deliberately cheap and deterministic (no LLM, no vendor spend): hashtags are ranked from
the story's own words, and the caption defaults to the reel's own lines. It also keeps us
on the right side of BRAND_PLAN §5 — this never drafts ad copy, which is why the Brand
door refused it and why `story_only=True` (the default) still refuses.

    build(frames, caption="", cover_frame_id="", story_only=True) -> dict
        {"caption", "hashtags", "cover_frame_id"}
"""

from __future__ import annotations

import re

# Common English filler — cutting it is what makes the ranked tags read like the story
# rather than like a stopword list.
_STOP = {
    "about", "after", "again", "also", "and", "because", "before", "being",
    "from", "have", "into", "just", "more", "most", "that", "their", "there",
    "this", "through", "with", "without", "where", "while", "your",
}

# Always-on discovery tags, before the story's own words.
_BASE = ["reels", "shorts", "storytelling", "inspiration", "journey"]


class BrandCopyRefused(Exception):
    """Raised when asked to draft posting copy for a brand/ad run.

    BRAND_PLAN §5: AI never writes brand ad claims — brand on-screen/spoken copy is
    operator-supplied verbatim. A hashtag ranker is harmless on a story and is NOT
    harmless on an ad, where "generated caption" means "generated claim".
    """


def hashtags(text: str, frames: list[dict], limit: int = 12) -> list[str]:
    """Rank hashtags from the story's own words. Cheap: no model, no spend."""
    source = " ".join([text] + [f.get("caption", "") for f in frames or []])
    words = re.findall(r"[A-Za-z][A-Za-z0-9]{3,}", source.lower())
    ranked, seen = [], set()
    for w in words:
        if w in _STOP or w in seen:
            continue
        seen.add(w)
        ranked.append(w)
    out: list[str] = []
    for tag in _BASE + ranked:
        clean = re.sub(r"[^A-Za-z0-9]", "", tag.title())
        if clean and clean.lower() not in {t.lower().lstrip("#") for t in out}:
            out.append("#" + clean)
        if len(out) >= limit:
            break
    return out


def build(frames: list[dict], *, caption: str = "", cover_frame_id: str = "",
          mode: str = "story") -> dict:
    """Caption + hashtags + cover frame for a finished reel.

    caption defaults to the reel's own spoken lines — the story already wrote it.
    """
    if (mode or "story") == "brand":
        raise BrandCopyRefused("Posting kit AI copy is story-mode only.")
    frames = frames or []
    caption = (caption or "").strip()
    if not caption:
        caption = "\n".join(f.get("caption", "") for f in frames if f.get("caption"))
    return {
        "caption": caption,
        "hashtags": hashtags(caption, frames),
        "cover_frame_id": cover_frame_id or (frames[0].get("frame_id") if frames else ""),
    }
