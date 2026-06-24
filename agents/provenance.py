"""Authenticity / provenance labeling (Gap #5).

Trust depends on the audience knowing what is real vs. AI. This computes a
provenance summary for a reel and a human-readable label, distinguishing:

  - real footage (user photos/videos, untouched)
  - AI-generated *symbolic/synthetic* visuals (ai_symbolic — no real person)
  - AI *likeness of a named real person* (ai_portrait face and/or cloned voice)

The third tier is the authenticity-moat exception (see MARKET_FIT_REVIEW §4): it is
only allowed with the consent recorded by governance (Gap #4), and it is the case a
provenance label most needs to disclose. The summary is written as a per-run
`provenance.json` artifact, served, badged in the UI, and shipped in the export.
"""

from __future__ import annotations

# Tier constants (ascending AI-on-real-human visibility).
REAL = "real"
AI_SYMBOLIC = "ai_symbolic"
AI_PORTRAIT = "ai_portrait"


def classify_frame(frame: dict) -> str:
    """Per-frame provenance tier from its photo_spec / visual source."""
    spec = (frame.get("photo_spec") or "").strip()
    if spec == "ai_symbolic":
        return AI_SYMBOLIC
    if spec == "ai_portrait":
        return AI_PORTRAIT
    if spec.startswith("ai_"):
        return AI_SYMBOLIC          # any other synthetic visual, no named person
    return REAL                      # explicit filename pin or matched real media


def summarize(data: dict) -> dict:
    """Provenance summary + label for a whole reel payload."""
    frames = data.get("frames") or []
    subject = (data.get("subject_name") or "").strip()
    counts = {REAL: 0, AI_SYMBOLIC: 0, AI_PORTRAIT: 0}
    for f in frames:
        counts[classify_frame(f)] += 1

    ai_face = counts[AI_PORTRAIT] > 0 and bool(subject)
    ai_voice = bool(data.get("voice_clone")) or any(f.get("lipsync") for f in frames)
    real_person_ai = ai_face or (ai_voice and bool(subject))

    modalities = [m for m, on in (("face", ai_face), ("voice", ai_voice and bool(subject))) if on]
    if real_person_ai:
        label = f"Contains AI {' & '.join(modalities)} likeness of a real person ({subject}) — consented"
        tier = AI_PORTRAIT
    elif counts[AI_SYMBOLIC] > 0 and counts[REAL] == 0:
        label = "AI-generated visuals (symbolic/synthetic) — no real person depicted"
        tier = AI_SYMBOLIC
    elif counts[AI_SYMBOLIC] > 0:
        label = "Mixed: real footage + AI-generated symbolic visuals"
        tier = AI_SYMBOLIC
    else:
        label = "Real footage"
        tier = REAL

    return {
        "label": label,
        "tier": tier,
        "real_person_ai": real_person_ai,
        "modalities": modalities,
        "subject": subject,
        "counts": counts,
    }


def badge(summary: dict) -> str:
    """Short UI badge text with a tier glyph."""
    glyph = {REAL: "🟢", AI_SYMBOLIC: "🔵", AI_PORTRAIT: "🟠"}.get(summary.get("tier"), "•")
    return f"{glyph} {summary.get('label', '')}"
