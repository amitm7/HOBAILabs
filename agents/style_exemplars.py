"""
Style exemplars — few-shot guidance from past hand-edited projects.

Reads exemplars/<project>/exemplar.json (see exemplars/README.md) and turns them
into concise text snippets injected into the LLM "brain" prompts so automated
scene design and image matching imitate the lab's house style.

This is in-context guidance, NOT model training. Gated by USE_EXEMPLARS so the
default pipeline behaviour is unchanged. Folders whose name starts with "_"
(e.g. _template) are ignored.
"""

import glob
import json
import os

EXEMPLARS_DIR = os.path.join(os.path.dirname(__file__), "..", "exemplars")

_STYLE_KEYS = ("mood", "palette", "pacing", "camera", "transitions", "captions", "music")


def _load() -> list[dict]:
    out = []
    pattern = os.path.join(os.path.abspath(EXEMPLARS_DIR), "*", "exemplar.json")
    for jp in sorted(glob.glob(pattern)):
        if os.path.basename(os.path.dirname(jp)).startswith("_"):
            continue  # skip _template and other underscore folders
        try:
            with open(jp) as f:
                out.append(json.load(f))
        except Exception:
            pass
    return out


def enabled() -> bool:
    """True only when explicitly turned on AND at least one real exemplar exists."""
    flag = os.environ.get("USE_EXEMPLARS", "").lower() in ("1", "true", "yes")
    return flag and bool(_load())


def style_preamble() -> str:
    """Concise 'house style' note aggregated across exemplars (deduped per dimension)."""
    exemplars = _load()
    if not exemplars:
        return ""
    values: dict[str, list[str]] = {k: [] for k in _STYLE_KEYS}
    for e in exemplars:
        style = e.get("style", {}) or {}
        for k in _STYLE_KEYS:
            v = (style.get(k) or "").strip()
            if v and v not in values[k]:
                values[k].append(v)
    lines = ["This lab has a consistent hand-made house style — match it:"]
    for k in _STYLE_KEYS:
        if values[k]:
            lines.append(f"- {k}: " + " / ".join(values[k][:3]))
    return "\n".join(lines) if len(lines) > 1 else ""


def _frames(limit: int) -> list[dict]:
    rows = []
    for e in _load():
        for fr in e.get("frames", []):
            rows.append(fr)
            if len(rows) >= limit:
                return rows
    return rows


def scene_examples(n: int = 3) -> str:
    """Few-shot examples for scene design: caption → emotion/shot/motion/duration."""
    rows = _frames(n)
    if not rows:
        return ""
    out = ["Examples of how this lab turns a beat into a shot (imitate this judgment):"]
    for fr in rows:
        out.append(
            f'- Beat: "{fr.get("caption","")[:90]}" -> '
            f'emotion={fr.get("emotion","")}, shot={fr.get("shot_type","")}, '
            f'motion={fr.get("motion","")}, ~{fr.get("duration_sec","")}s'
        )
    return "\n".join(out)


def matching_examples(n: int = 4) -> str:
    """Few-shot examples for media matching: caption → media choice + why."""
    rows = _frames(n)
    if not rows:
        return ""
    out = ["Examples of how this lab picks media for a beat (imitate this judgment):"]
    for fr in rows:
        kind = "REAL VIDEO" if fr.get("media_type") == "video" else "photo"
        out.append(
            f'- Beat: "{fr.get("caption","")[:80]}" -> chose a {kind} '
            f'({fr.get("shot_type","")}) because: {fr.get("why_this_media","")[:120]}'
        )
    return "\n".join(out)
