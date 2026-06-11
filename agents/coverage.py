"""
Multi-shot coverage (ROADMAP #15.3-4) — cover ONE story beat with multiple shots:
the matched primary media + 1-2 still B-roll images, played as sub-shots inside
the beat's duration, under one caption.

Opt-in and additive. Default behaviour (one media per frame) is unchanged when
coverage is off or no spare images fit.

Two halves:
  · assign_coverage(frames, assets_dir)  — picks extra B-roll per eligible beat
    (LLM content match, reusing image_matcher's cached descriptions — no CLIP/GPU
    dependency at this scale), and stores it on frame["extra_media"].
  · expand_assignment(base, frame)       — splits a built clip assignment into
    sub-assignments (one per media), dividing the beat duration and giving each a
    gentle, distinct camera move. Captions are untouched: they come from frames[]
    whose duration == the sum of the sub-shot durations, so the caption spans them.

Excluded from coverage: lip-sync beats (a talking face is one continuous shot),
silent beats, and beats too short to split.
"""

import os

MIN_BEAT_FOR_SPLIT = 5.0     # seconds — shorter beats stay single-shot
MIN_SUB_DURATION   = 2.5     # seconds — each sub-shot is at least this long
MAX_EXTRA          = 2       # at most 2 B-roll shots (=> up to 3 shots per beat)

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
_DERIVED = ("_edit", "_edited", "_portrait", "_symbolic", "_norm", "_5s",
            "_raw", "_lastframe", "_ext", "_hf", "_noaudio", "_trimtmp", "_upload")


# ── Duration split + sub-shot motion ──────────────────────────────────────────

def split_durations(total: float, n: int,
                    min_each: float = MIN_SUB_DURATION) -> list[float]:
    """Split `total` into at most `n` parts, each >= min_each, summing EXACTLY to
    total (last part absorbs rounding). Returns [total] if it can't make 2 parts."""
    if total < min_each * 2 or n < 2:
        return [round(total, 2)]
    n = min(n, int(total // min_each))
    if n < 2:
        return [round(total, 2)]
    base = round(total / n, 2)
    parts = [base] * (n - 1)
    parts.append(round(total - base * (n - 1), 2))
    return parts


_GENTLE_MOVES = ["slow push in", "slow pan right", "slow zoom out", "slow pan left"]


def _sub_motions(primary_motion: str, n: int) -> list[str]:
    """Keep the primary's move on shot 1; give B-roll gentle, varied moves."""
    out = [primary_motion or "slow push in"]
    i = 0
    while len(out) < n:
        out.append(_GENTLE_MOVES[i % len(_GENTLE_MOVES)])
        i += 1
    return out


def expand_assignment(base: dict, frame: dict) -> list[dict]:
    """Return [base] normally, or several sub-assignments when the frame carries
    extra_media and the beat is long enough / not a special (lip-sync) clip."""
    extras = [p for p in (frame.get("extra_media") or []) if p and os.path.exists(p)]
    if (not extras
            or frame.get("lipsync")
            or base.get("clip_ready")
            or base.get("has_lipsync_audio")
            or float(base.get("actual_duration", 0)) < MIN_BEAT_FOR_SPLIT):
        return [base]

    media = [base["media_path"], *extras]
    durs = split_durations(float(base["actual_duration"]), len(media))
    if len(durs) < 2:
        return [base]

    media = media[:len(durs)]
    motions = _sub_motions(base.get("motion_prompt", ""), len(media))
    out = []
    for i, (m, d) in enumerate(zip(media, durs)):
        out.append({
            **base,
            "segment_id":      f"{base['segment_id']}_{i + 1}",
            "media_path":      m,
            "actual_duration": d,
            "motion_prompt":   motions[i],
            # only the primary honours a video start offset
            "video_start_sec": base.get("video_start_sec", 0.0) if i == 0 else 0.0,
        })
    return out


def expand_all(assignments: list[dict], frames: list[dict]) -> list[dict]:
    """Expand a per-frame assignment list into sub-shots where coverage applies.
    `assignments` and `frames` are aligned 1:1 by order."""
    by_id = {f["frame_id"]: f for f in frames}
    out = []
    for a in assignments:
        frame = by_id.get(a["segment_id"], {})
        out.extend(expand_assignment(a, frame))
    return out


# ── B-roll selection (LLM content match, reuses cached descriptions) ───────────

def _is_broll_candidate(fn: str) -> bool:
    if os.path.splitext(fn)[1].lower() not in _IMAGE_EXTS:
        return False
    if fn.startswith("ai_") or fn.startswith("_"):
        return False
    low = fn.lower()
    return not any(m in low for m in _DERIVED)


def _rank_extras(eligible: list[dict], descriptions: dict, max_extra: int) -> dict:
    """One LLM call: for each beat, return a list of image numbers that ALSO fit
    it as extra B-roll (may be empty). Returns {frame_id: [paths]}."""
    from agents import llm

    items = list(descriptions.items())          # [(path, desc), ...]
    if not items:
        return {}
    img_list = "\n".join(f"{i + 1}. [{os.path.basename(p)}] {d}"
                         for i, (p, d) in enumerate(items))
    beat_list = "\n".join(f"{f['frame_id']}: {f.get('caption', '') or '(no caption)'}"
                          for f in eligible)
    prompt = (
        "You are a film editor adding B-ROLL to cover story beats. Each beat "
        "already has a main shot; you may add a few SUPPORTING still images that "
        "ALSO fit the same beat (extra texture, context, detail).\n\n"
        f"CANDIDATE IMAGES (number, [filename], description):\n{img_list}\n\n"
        f"STORY BEATS (id: caption):\n{beat_list}\n\n"
        f"For each beat, list 0 to {max_extra} image numbers that genuinely fit it "
        "as supporting B-roll, best first. It is fine — and common — to return an "
        "empty list when nothing fits. Never reuse the same image for two beats.\n"
        'Reply ONLY as JSON mapping beat id to a list of numbers, e.g. '
        '{"f02": [4, 7], "f05": []}.'
    )
    text = llm.chat([{"role": "user", "content": prompt}],
                    json_mode=True, max_tokens=700, model_tier="reasoning")
    mapping = llm.json_loads_lenient(text)

    paths = [p for p, _ in items]
    out = {}
    for f in eligible:
        nums = mapping.get(f["frame_id"]) or []
        picks = []
        if isinstance(nums, list):
            for n in nums:
                try:
                    idx = int(n) - 1
                except (TypeError, ValueError):
                    continue
                if 0 <= idx < len(paths):
                    picks.append(paths[idx])
        out[f["frame_id"]] = picks
    return out


def assign_coverage(frames: list[dict], assets_dir: str,
                    max_extra: int = MAX_EXTRA) -> int:
    """Pick extra B-roll for eligible beats and set frame["extra_media"].
    Returns the number of beats given coverage. Safe no-op on any error or when
    there are no spare images / eligible beats. Reuses the matcher's cached
    image descriptions, so cost is near-zero on a re-run."""
    if not assets_dir or not os.path.isdir(assets_dir):
        return 0

    eligible = [f for f in frames
                if (f.get("caption") or "").strip()
                and not f.get("lipsync")
                and f.get("visual_path") and os.path.exists(f["visual_path"])
                and float(f.get("duration", 0)) >= MIN_BEAT_FOR_SPLIT]
    if not eligible:
        return 0

    used = {os.path.basename(f["visual_path"]) for f in frames if f.get("visual_path")}
    try:
        candidates = [os.path.join(assets_dir, fn)
                      for fn in sorted(os.listdir(assets_dir))
                      if _is_broll_candidate(fn) and fn not in used]
    except OSError:
        return 0
    if not candidates:
        return 0

    try:
        from agents import image_matcher
        descriptions = image_matcher.describe_images(candidates)
        mapping = _rank_extras(eligible, descriptions, max_extra)
    except Exception as e:
        print(f"[Coverage] selection failed ({e}) — single-shot")
        return 0

    taken, n = set(), 0
    for f in eligible:
        picks = []
        for p in mapping.get(f["frame_id"], []):
            if p not in taken and os.path.exists(p):
                picks.append(p)
                taken.add(p)
            if len(picks) >= max_extra:
                break
        if picks:
            f["extra_media"] = picks
            n += 1
            print(f"[Coverage] {f['frame_id']} → +{len(picks)} B-roll: "
                  f"{', '.join(os.path.basename(p) for p in picks)}")
    if n:
        print(f"[Coverage] {n} beat(s) covered with multiple shots")
    return n
