"""Plan QC — slideshow-risk scorer (quality gate, plan/storyboard stage).

The #1 known quality failure of generated reels is the "slideshow feel": a chain
of same-shaped, same-length, motionless cards. Every fix so far runs AFTER spend
(beat-cutting, motion chaining). This module scores the PLAN — before any paid
generation — on the structural patterns that reliably predict slideshow output,
so the board can warn while the fix is still free.

Clean-room note: the *idea* of pre-generation slideshow scoring appears in other
open tools; this implementation is written from scratch against OUR frame dict
(photo_spec / shot_size / scene.motion_prompt / duration / caption) and OUR
thresholds. No external code or text was copied (AGPL hygiene).

Pure + deterministic: no I/O, no LLM, no network — safe to run on every plan
edit. Dimensions score 0–5 (lower is better); a dimension with no data yet
(e.g. motion before the Storyboard stage runs) scores 0 with a note rather
than guessing.
"""

from __future__ import annotations

# Dimension → (threshold≥3 warning, fix hint) — kept together so the board's
# warning text and the scoring stay in one place.
_FIXES = {
    "repetition": ("several consecutive shots share the same framing",
                   "vary shot_size / camera across neighbouring shots"),
    "motion_monotony": ("most shots have no or identical motion",
                        "give key shots distinct motion prompts (push/pan/track)"),
    "duration_monotony": ("shots are all nearly the same length",
                          "vary durations with the beat — long holds only where the story earns them"),
    "static_ratio": ("mostly still images with no real video source",
                     "animate hero shots or add real video clips"),
    "caption_wall": ("captions are long enough to read like slides",
                     "split long captions across shots or trim to one thought per shot"),
    "coverage": ("long beats are covered by a single shot",
                 "enable multi-shot coverage on beats over ~6s"),
}


def _clamp(x: float) -> int:
    return max(0, min(5, int(round(x))))


def _longest_run(values: list) -> int:
    best = run = 1 if values else 0
    for a, b in zip(values, values[1:]):
        run = run + 1 if (a == b and a not in ("", None)) else 1
        best = max(best, run)
    return best


def score_plan(frames: list[dict]) -> dict:
    """Score a shot plan for slideshow risk.

    Returns {"total": float, "risk": "low|medium|high",
             "dimensions": {name: {"score": int, "note": str}},
             "warnings": [{"dim", "message", "fix"}]}.
    """
    n = len(frames or [])
    if n < 2:
        return {"total": 0.0, "risk": "low", "dimensions": {}, "warnings": []}

    dims: dict[str, dict] = {}

    # 1 · repetition — identical framing running back-to-back.
    framings = [(f.get("shot_size") or "").strip().lower() for f in frames]
    if any(framings):
        run = _longest_run(framings)
        dims["repetition"] = {"score": _clamp((run - 1) * 5 / max(2, n - 1)),
                              "note": f"longest identical-framing run: {run}/{n}"}
    else:
        dims["repetition"] = {"score": 0, "note": "no shot sizes planned yet"}

    # 2 · motion monotony — empty or copy-pasted motion prompts.
    motions = [((f.get("motion_override") or (f.get("scene") or {}).get("motion_prompt")
                 or "").strip().lower()) for f in frames]
    with_data = [m for m in motions if m]
    if with_data:
        empty = motions.count("")
        distinct = len(set(with_data))
        dup_share = 1 - distinct / len(with_data)
        dims["motion_monotony"] = {
            "score": _clamp(5 * (empty / n * 0.5 + dup_share * 0.5)),
            "note": f"{empty}/{n} shots without motion; {distinct} distinct motions"}
    else:
        dims["motion_monotony"] = {"score": 0, "note": "not storyboarded yet"}

    # 3 · duration monotony — low variance = metronome cutting.
    durs = [float(f.get("duration") or 0) for f in frames]
    mean = sum(durs) / n
    if mean > 0:
        var = sum((d - mean) ** 2 for d in durs) / n
        cv = (var ** 0.5) / mean            # coefficient of variation
        dims["duration_monotony"] = {"score": _clamp(5 * max(0.0, 1 - cv / 0.35)),
                                     "note": f"duration spread cv={cv:.2f}"}
    else:
        dims["duration_monotony"] = {"score": 0, "note": "no durations set"}

    # 4 · static ratio — stills with no real-video source and no motion text.
    video_exts = (".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv")
    static = sum(
        1 for f, m in zip(frames, motions)
        if not m and not str(f.get("visual_path") or f.get("photo_spec") or ""
                             ).lower().endswith(video_exts))
    dims["static_ratio"] = {"score": _clamp(5 * static / n),
                            "note": f"{static}/{n} shots are unanimated stills"}

    # 5 · caption wall — text too long to land inside its shot.
    long_caps = sum(1 for f in frames if len((f.get("caption") or "").strip()) > 140)
    dims["caption_wall"] = {"score": _clamp(5 * long_caps / n),
                            "note": f"{long_caps}/{n} captions over 140 chars"}

    # 6 · coverage — long beats carried by a single shot.
    long_single = sum(1 for f in frames
                      if float(f.get("duration") or 0) > 6 and not f.get("multi_shot"))
    dims["coverage"] = {"score": _clamp(5 * long_single / max(1, n // 2)),
                        "note": f"{long_single} beats >6s with single-shot coverage"}

    scored = [d["score"] for d in dims.values()]
    total = round(sum(scored) / len(scored), 2)
    risk = "high" if total >= 3 else "medium" if total >= 1.5 else "low"
    warnings = [{"dim": name, "message": _FIXES[name][0], "fix": _FIXES[name][1]}
                for name, d in dims.items() if d["score"] >= 3]
    return {"total": total, "risk": risk, "dimensions": dims, "warnings": warnings}
