"""
Auto-director: choose a camera move for each frame from the caption alone.

The end user gives a plain script + photos. They should NOT have to know cinematography.
This picks a sensible camera move per frame based on emotional keywords and story
position, so the UI can pre-fill the 🎥 field with a suggestion the user can keep or change.

Rule-based and instant (no API cost). The user always sees and can override the choice.
"""

import re

_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".m4v", ".webm"}

# Emotional keyword → (camera move, why). Checked in order; FIRST match wins,
# so the list is ordered by specificity — aspiration before triumph before strength.
_RULES = [
    # Hope / dream / aspiration — "I imagined the ramp" must beat the literal-ramp triumph rule
    (r"\b(dream|dreamt|hope|spark|imagine|imagined|watched|thinking|wished|believed|"
     r"one day|ek din|someday|chalungi|chalegi)\b",
     "dolly in", "hope / dream"),

    # Triumph / victory / peak — the actual win or the actual ramp walk
    (r"\b(won|win|winner|won 1st|runner-up|champion|trophy|crown|award|viral|"
     r"everywhere|conquer|roshan|walked the ramp|on the ramp|first prize)\b",
     "360 orbit", "triumph"),

    # Strength / pride / farming work — comes before marriage so "tractors even after marriage" = strength
    (r"\b(tractor|tractors|plough|ploughing|farming|farmer|fields|kisan|harvest|"
     r"proud|strength|strong|powerful|fought back|i fought|fought|rose|stood tall|"
     r"independent|financially)\b",
     "crane up", "strength / pride"),

    # Loss / illness / grief — stillness
    (r"\b(diagnosed|arthritis|illness|disease|bedridden|cancer|lost my|lost her|"
     r"shattered|broke|broken|useless|empty|died|death|funeral)\b",
     "static", "grief / loss"),
    (r"\b(pain|cried|crying|tears|suffering|gave up|helpless|alone|lonely)\b",
     "slow dolly out", "isolation"),

    # Longing / restlessness
    (r"\b(meant for more|wanted|longing|yearn|restless|something more|escape)\b",
     "dolly in", "longing"),

    # Marriage / home / tradition — gentle, intimate
    (r"\b(married|marriage|wedding|home|husband|wife|family|mother|tradition|ghunghat|sasural)\b",
     "slow dolly in", "intimacy"),
]


def suggest_camera(caption: str, frame_index: int, total_frames: int,
                   photo_spec: str = "", visual_path: str = "") -> tuple[str, str]:
    """
    Return (camera_move, reason) for a frame.
    Empty string means "no camera move" (video sources already have motion).

    frame_index is 0-based.
    """
    # Video sources already move — don't impose a camera move
    ext = ""
    if visual_path and "." in visual_path:
        ext = "." + visual_path.rsplit(".", 1)[-1].lower()
    spec_ext = ""
    if photo_spec and "." in photo_spec:
        spec_ext = "." + photo_spec.rsplit(".", 1)[-1].lower()
    if ext in _VIDEO_EXTS or spec_ext in _VIDEO_EXTS:
        return "", "real video — keeps its own motion"

    text = (caption or "").lower()

    # Opening frame is the scroll-stopper — hook beats everything
    if frame_index == 0:
        return "crash zoom in", "opening hook"

    # Keyword rules (ordered by specificity)
    for pattern, move, reason in _RULES:
        if re.search(pattern, text):
            return move, reason

    # Closing frame — intimate
    if frame_index == total_frames - 1:
        return "slow dolly in", "closing — intimate"

    # Safe default
    return "dolly in", "gentle default"
