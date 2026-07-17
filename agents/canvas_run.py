"""
Canvas run — the staged "Director Canvas" orchestrator (docs/AGENTIC_CANVAS_PLAN.md).

A NEW staged flow that **reuses** existing agents + engine services. It runs the
pipeline one gate at a time (Script → Storyboard → Keyframes → Audio → Video →
Final Cut), showing a per-stage cost and waiting for human approval between paid
steps — the answer to galleri5's "Agentic Canvas" without forking the engine.

THE BRIGHT LINE (build-feature rule #1; AGENTIC_CANVAS_PLAN §3):
- This module **sequences and gates**; it NEVER re-implements cost/cache/
  governance/routing/assembly.
- Cost = `pricing.estimate()` (server truth), sliced per stage. Never hardcoded.
- Real-vs-AI is read from `model_router` (real media = PASSTHROUGH, never
  re-judged here, so a real photo can't be silently AI-regenerated).
- The linear `_run_inner` stays untouched. Canvas state is stored INSIDE the run
  payload (`run_store`), so there is no parallel store and it is restart-safe.

Stage execution split (this slice):
- "script" + "storyboard" are cheap text stages run in-process via agents
  (`shot_planner`, `scene_intelligence`), and degrade gracefully offline.
- The paid stages ("keyframes", "audio", "video", "finalcut") are scaffolded with
  server-truth cost + gating here; their generation reuses the existing pipeline
  (wired at the route layer) — never re-implemented in this module.
"""
from __future__ import annotations

# Stdlib only. Agent/vendor imports stay lazy + inside functions (this module must import
# cheaply and never drag the pipeline in); these three back the locations disk cache.
import hashlib
import json
from pathlib import Path

# Ordered pipeline. Each stage gates the next; paid stages reserve spend per stage.
STAGES = ["script", "storyboard", "keyframes", "audio", "video", "finalcut"]

STAGE_META = {
    "script":     {"label": "Script & Shots", "paid": False,
                   "blurb": "Your brief becomes an editable shot list."},
    "storyboard": {"label": "Storyboard",     "paid": False,
                   "blurb": "Camera framing, action beats and motion arrows per shot."},
    "keyframes":  {"label": "Key Frames",     "paid": True,
                   "blurb": "Anchor frames — real photos pass through untouched."},
    "audio":      {"label": "Audio",          "paid": True,
                   "blurb": "Per-scene voiceover."},
    "video":      {"label": "Video Clips",    "paid": True,
                   "blurb": "Animated scene clips."},
    "finalcut":   {"label": "Final Cut",      "paid": True,
                   "blurb": "Beat-aware assembly into the finished reel."},
}

# Three-colour asset legend — the moat made visible (AGENTIC_CANVAS_PLAN §5c).
ASSET_REAL = "real"          # 🟢 real footage, passes through untouched (our moat)
ASSET_AI = "ai"              # 🟡 AI symbolic/object — labeled
ASSET_AI_PERSON = "ai_person"  # 🔴 AI likeness of a person — consent-gated


# ── Asset classification (reads model_router; never re-decides generation) ──────

def asset_kind(frame: dict) -> str:
    """Classify a frame's visual source for the canvas legend. Reuses
    model_router so the real-media judgment lives in exactly one place."""
    from agents import model_router
    if model_router._is_real_media(frame) or model_router._is_video_source(frame):
        return ASSET_REAL
    spec = (frame.get("photo_spec") or "").strip()
    if spec == "ai_portrait":
        return ASSET_AI_PERSON      # likeness — gate on consent before spend
    return ASSET_AI                 # ai_symbolic / object


# ── Motion arrow (structured, not decorative — our UX edge over a drawn arrow) ──

_ARROW_RULES = (
    ("push", "in"), ("zoom in", "in"), ("dolly in", "in"),
    ("pull", "out"), ("zoom out", "out"), ("dolly out", "out"),
    ("pan left", "left"), ("truck left", "left"),
    ("pan right", "right"), ("truck right", "right"),
    ("tilt up", "up"), ("crane up", "up"), ("boom up", "up"),
    ("tilt down", "down"), ("crane down", "down"), ("boom down", "down"),
    ("orbit", "orbit"), ("360", "orbit"), ("arc", "orbit"),
)


def motion_arrow(motion: str) -> str:
    """Map a camera-move phrase to a direction token the board renders as an SVG
    arrow. Falls back to a gentle 'in' (slow push) like our pipeline default."""
    m = (motion or "").lower()
    for needle, token in _ARROW_RULES:
        if needle in m:
            return token
    return "in"


# ── Per-stage cost (server truth via pricing.estimate, sliced) ──────────────────

def stage_costs(frames: list[dict], *, quality: str = "dev",
                music_type: str = "none") -> dict:
    """USD per stage. Calls pricing.estimate ONCE (the single estimator) and slices
    its breakdown — never re-derives a price. Safe-degrades to zeros on failure."""
    try:
        from agents import model_router
        from agents.pricing import estimate
        voice_chars = sum(len(f.get("caption") or "") for f in frames
                          if not f.get("lipsync")) if music_type == "voiceover" else \
            sum(len(f.get("caption") or "") for f in frames)
        b = estimate(
            frames,
            force_5s=(quality == "dev"),
            music_type=music_type,
            voice_chars=voice_chars,
            cost_tier=model_router.cost_tier_from_quality(quality),
            image_model="auto",
            video_model="auto",
        )
        return {
            "script":     0.0,
            "storyboard": round(b["scene"]["usd"], 4),
            "keyframes":  round(b["images"]["usd"] + b["edits"]["usd"], 4),
            "audio":      round(b["voice"]["usd"] + b["lipsync_audio"]["usd"], 4),
            "video":      round(b["animation"]["usd"] + b["lipsync"]["usd"], 4),
            "finalcut":   round(b["music"]["usd"], 4),
        }
    except Exception as e:  # pricing must never block the board
        print(f"[Canvas] stage_costs degraded ({e})")
        return {s: 0.0 for s in STAGES}


# ── Board cards (storyboard view data) ─────────────────────────────────────────

def stage_etas(frames: list[dict], *, quality: str = "dev") -> dict:
    """Rough wall-clock estimate per stage (seconds) for the '~Nm' hint. Deliberately
    coarse — a hint, not a promise. Stills and clips generate in PARALLEL (each model has
    a concurrency cap), so the wall-clock is BATCHES × per-item, not n × per-item — a 32-
    shot Video render is ~8 batches of ~70s (~9 min), NOT 32×70s (~37 min). Estimating it
    sequentially made the number needlessly scary."""
    import math
    n = max(1, len(frames))
    prod = quality != "dev"
    IMG_PAR, VID_PAR = 4, 4                       # rough concurrent-generation caps
    img_batches = math.ceil(n / IMG_PAR)
    vid_batches = math.ceil(n / VID_PAR)
    return {
        "script":     0,
        "storyboard": n * 4,
        "keyframes":  img_batches * (16 if prod else 8),
        "audio":      n * 3,
        "video":      vid_batches * (70 if prod else 35),
        "finalcut":   20,
    }


def shot_form(frame: dict) -> str:
    """S28 step 0 — a shot's storytelling FORM, derived deterministically from data
    the planner already produces (so it stays true through every script edit):
      dialogue  — a line spoken by a named character (in-scene performance)
      narration — a line carried by the narrator (VO over illustration)
      silent    — no line (atmosphere/action beat)
    The operator can force it via frame["form_override"] (detect → declare)."""
    ov = (frame.get("form_override") or "").strip().lower()
    if ov in ("dialogue", "narration", "silent"):
        return ov
    if not (frame.get("caption") or "").strip():
        return "silent"
    sid = (frame.get("speaker_id") or "narrator").strip().lower()
    return "narration" if sid in ("", "narrator") else "dialogue"


def story_form(frames: list[dict]) -> dict:
    """Story-level verdict + counts: 🎬 cinematic (dialogue-driven), 🎙 narrated,
    or mixed. Thresholds are deliberately simple: any meaningful dialogue presence
    (>=20% of spoken lines) makes it mixed; dialogue majority makes it cinematic."""
    counts = {"dialogue": 0, "narration": 0, "silent": 0}
    for f in frames or []:
        counts[shot_form(f)] += 1
    spoken = counts["dialogue"] + counts["narration"]
    if spoken == 0:
        form = "silent"
    elif counts["dialogue"] >= max(1, spoken * 0.5):
        form = "cinematic"
    elif counts["dialogue"] >= max(1, round(spoken * 0.2)):
        form = "mixed"
    else:
        form = "narrated"
    return {"form": form, **counts}


def form_warnings(state: dict) -> list[dict]:
    """S28 live checks (computed fresh so they track cast/script edits):
    dialogue without distinct voices = radio-drama with one actor."""
    frames = state.get("frames") or []
    sf = story_form(frames)
    warns = []
    if sf["dialogue"]:
        speakers = {(f.get("speaker_id") or "") for f in frames
                    if shot_form(f) == "dialogue"}
        speakers.discard(""); speakers.discard("narrator")
        chars = {c.get("id"): c for c in state.get("characters") or []}
        unvoiced = sorted(s for s in speakers if not chars.get(s, {}).get("voice_id"))
        if len(speakers) >= 2 and unvoiced:
            warns.append({"id": "dialogue_voices_missing", "severity": "warn",
                          "frame_id": "",
                          "message": f"cinematic dialogue detected, but these characters have no OWN voice yet: {', '.join(unvoiced)} — their lines will all sound like the narrator",
                          "fix": "Assign a voice per character on the Character sheet (T4)."})
    return warns


def board_cards(frames: list[dict]) -> list[dict]:
    """Per-shot board data the UI renders as storyboard cards: shot grammar, the
    motion arrow, the emotion/beat, the (editable) generation prompt, and the
    real-vs-AI badge."""
    cards = []
    for f in frames:
        scene = f.get("scene") or {}
        motion = f.get("motion_override") or scene.get("motion_prompt") or ""
        kind = asset_kind(f)
        # A real-passthrough shot shows the real photo; an AI shot may carry a real
        # reference image (the face/scene the generation is conditioned on).
        real_path = (f.get("visual_path") or f.get("photo_spec") or "") if kind == ASSET_REAL else ""
        cards.append({
            "frame_id":   f.get("frame_id"),
            "review":     f.get("review_status") or "",   # S30 P3: editorial QA verdict
            "form":       shot_form(f),              # S28: dialogue | narration | silent
            "form_override": f.get("form_override") or "",
            "overlays":   f.get("overlays") or [],   # T14 Frame Composer spec per shot
            "caption":    f.get("caption", ""),
            "shot_size":  f.get("shot_size") or "",
            "camera":     scene.get("camera_angle") or "",
            "motion":     motion,
            "arrow":      motion_arrow(motion),
            "emotion":    scene.get("emotion") or "",
            "note":       f.get("director_note") or "",
            # The actual image-generation prompt — visible AND editable, like the
            # competitor's per-node prompt box. Empty until the Storyboard stage runs.
            "image_prompt":    scene.get("image_prompt") or "",
            "negative_prompt": f.get("negative_prompt") or "",
            "asset_kind": kind,
            "real_path":  real_path,                         # real photo, shown untouched
            "ref_path":   f.get("character_ref_path") or "",  # real ref for AI likeness
            # Ambient re-create (ladder rung 3) is identity-safe → only offered on
            # NON-person shots. Person shots keep Restore (rungs 0-1).
            "can_recreate": not bool(f.get("uses_talent")),
            "recreated":    bool(f.get("recreated_from_real")),
            "restored":     bool(f.get("restored")),
            # Reality–Fidelity ladder selector (rung 1d): current rung, the rungs allowed
            # for THIS shot (person shots never get ambient re-create), and the auto-suggest.
            "fidelity":           current_fidelity(f),
            "fidelity_options":   fidelity_options(f),
            "fidelity_suggested": f.get("fidelity_suggested") or "",
            "fidelity_reason":    f.get("fidelity_reason") or "",
            "quality_score":      f.get("quality_score"),
            # Source-swap state (the per-shot Replace row): an AI swap is labeled, and a
            # shot swapped away from real footage offers a one-tap revert to that footage.
            "ai_likeness":     bool(f.get("ai_likeness")),
            "forced_ai":       bool(f.get("forced_ai")),
            "can_revert_real": bool(f.get("orig_visual")) and kind != ASSET_REAL,
            # Generative upscale (final-render quality lift): offered once the shot has a
            # still image (a real photo, or a rendered AI still). Routed real→faithful,
            # AI→creative by the route. `upscaled` shows the badge + hides the button.
            "can_upscale":     _is_image(f.get("visual_path") or real_path) and not f.get("upscaled"),
            "upscaled":        bool(f.get("upscaled")),
            # Pencil-sketch storyboard panel (planning view) — path if rendered, else ''
            "storyboard_art":  f.get("storyboard_art") or "",
            # Content-fit flag (C4): a reason string if the matched photo may not fit the beat
            "match_flag":      f.get("match_flag") or "",
            "duration":   f.get("duration"),
        })
    return cards


# ── Reality–Fidelity ladder selector (rung 1d) ──────────────────────────────────
# The rungs already shipped (Passthrough=0, Restore=1, Re-create ambient=3) surfaced as
# a per-shot choice + a quality auto-suggest. Person re-create (rung 4) is intentionally
# NOT offered here — it's the consent-gated path, deferred. See REAL_MEDIA_QUALITY_LADDER.

FIDELITY_RUNGS = ("passthrough", "restore", "recreate")


def current_fidelity(frame: dict) -> str:
    """Which rung this shot is currently on, from the flags the routes set."""
    if frame.get("recreated_from_real"):
        return "recreate"
    if frame.get("restored"):
        return "restore"
    return "passthrough"


def fidelity_options(frame: dict) -> list[str]:
    """Rungs offered for THIS shot. Only REAL footage has a ladder (an AI shot is already
    generated). A shot that shows the real person never gets ambient re-create — that
    would synthesize a likeness — so it's capped at Restore (identity stays real)."""
    if asset_kind(frame) != ASSET_REAL:
        return []
    if frame.get("uses_talent"):
        return ["passthrough", "restore"]
    return ["passthrough", "restore", "recreate"]


def suggest_fidelity(frame: dict, score: dict | None = None) -> dict:
    """Recommend a rung from the shot's quality + whether it shows a person.
    `score` is `restore.quality_score(...)`. Conservative by design: when unsure, keep
    it real (passthrough); a person + low quality → Restore (never re-create the person);
    only amateur AMBIENT B-roll is pushed toward Re-create."""
    opts = fidelity_options(frame)
    if not opts:
        return {"suggested": None, "reason": ""}
    s = (score or {}).get("score")
    reason = (score or {}).get("reason") or ""
    if s is None:
        return {"suggested": "passthrough", "reason": "couldn't assess — kept real"}
    if s >= 0.8:
        return {"suggested": "passthrough", "reason": f"looks clean ({reason})"}
    if "recreate" not in opts:   # person shot, low quality → clean it, keep them real
        return {"suggested": "restore", "reason": f"{reason} — clean it, keep them real"}
    if s <= 0.45:                # ambient + amateur → re-create cinematically
        return {"suggested": "recreate", "reason": f"{reason} — amateur B-roll, re-create"}
    return {"suggested": "restore", "reason": f"{reason} — clean it up"}


def score_fidelity(state: dict) -> list[dict]:
    """Score every REAL shot and store its auto-suggestion on the frame (read back by
    board_cards). Reuses restore.quality_score. Best-effort per shot."""
    from agents import restore
    out = []
    for f in state.get("frames", []):
        opts = fidelity_options(f)
        if not opts:
            continue
        spec = str(f.get("photo_spec") or "")
        src = f.get("visual_path") or (spec if spec and not spec.startswith("ai_") else "")
        try:
            q = restore.quality_score(src) if src else {"score": None, "reason": "no media"}
        except Exception as e:
            q = {"score": None, "reason": f"assess failed ({e})"}
        sug = suggest_fidelity(f, q)
        f["quality_score"] = q.get("score")
        f["quality_reason"] = q.get("reason")
        f["fidelity_suggested"] = sug["suggested"]
        f["fidelity_reason"] = sug["reason"]
        out.append({"frame_id": f.get("frame_id"), "score": q.get("score"),
                    "suggested": sug["suggested"], "reason": sug["reason"],
                    "options": opts})
    return out


def revert_passthrough(state: dict, frame_id: str) -> dict:
    """Reality–Fidelity rung 0: drop any restore/re-create override and put the shot back
    on the ORIGINAL real media (preserved as `orig_visual` by the restore/recreate routes
    before they overwrite). The moat's default — untouched real footage."""
    f = next((x for x in state.get("frames", []) if x.get("frame_id") == frame_id), None)
    if not f:
        raise ValueError("unknown shot")
    orig = f.get("orig_visual")
    if orig:
        f["visual_path"] = orig
        f["photo_spec"] = orig
    for k in ("restored", "recreated_from_real", "forced_ai", "ai_likeness"):
        f.pop(k, None)
    # An AI swap conditioned on an operator face leaves a character_ref_path; reverting to
    # the untouched real photo drops it too. (A Characters-anchored shot has no orig_visual,
    # so it isn't offered this undo and its anchor is preserved.)
    if orig:
        f.pop("character_ref_path", None)
    return f


# ── Attach operator-supplied images (the moat: your real media) ─────────────────

ASSET_MODES = {"real", "reference", "scene"}


def attach_asset(state: dict, *, path: str, mode: str = "reference",
                 frame_id: str | None = None, all_talent: bool = False) -> dict:
    """Attach an operator photo to shot(s). Path validation/security is the route's
    job (`_path_allowed`); here we only set frame keys the shared engine reads:
      - 'real':      the photo passes through UNTOUCHED (the moat) — non-AI photo_spec
                     so model_router routes it to PASSTHROUGH (never regenerated).
      - 'reference': keep the shot an AI likeness but condition it on the real face.
      - 'scene':     mood/scene reference for a symbolic (no-person) shot.
    Attaching an image changes the render, so downstream stages are invalidated."""
    mode = mode if mode in ASSET_MODES else "reference"
    path = (path or "").strip()
    if not path:
        raise ValueError("no image path")
    if all_talent:
        targets = [f for f in state["frames"] if f.get("uses_talent")]
    else:
        targets = [f for f in state["frames"] if f.get("frame_id") == frame_id]
    if not targets:
        raise ValueError("no target shot for the image")
    for f in targets:
        _preserve_real(f)                   # so 'Use real photo' can undo an AI swap
        if mode == "real":
            f["photo_spec"] = path          # non-AI spec → model_router PASSTHROUGH
            f["visual_path"] = path
            for k in ("character_ref_path", "ai_likeness", "forced_ai",
                      "restored", "recreated_from_real", "orig_visual"):
                f.pop(k, None)              # it IS real now — no override, no undo needed
        elif mode == "reference":
            f["character_ref_path"] = path  # AI likeness conditioned on the operator face
            f["photo_spec"] = "ai_portrait"
            f["ai_likeness"] = True         # labeled 'AI · likeness' + flagged in provenance
            f.pop("visual_path", None)
            f.pop("restored", None); f.pop("recreated_from_real", None)
        else:  # scene
            f["character_ref_path"] = path
            if not (f.get("photo_spec") or "").startswith("ai_"):
                f["photo_spec"] = "ai_symbolic"
    state["last_affected"] = len(targets)   # for the operator-facing confirmation hint
    if state["stages"]["storyboard"]["status"] in ("done", "approved"):
        invalidate_from(state, "storyboard")
    state["board"] = board_cards(state["frames"])
    state["costs"] = stage_costs(state["frames"], quality=state.get("quality", "dev"))
    return state


_IMG_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".bmp")


def _is_image(path: str) -> bool:
    return bool(path) and str(path).lower().endswith(_IMG_SUFFIXES)


def _real_source(frame: dict) -> str:
    """The shot's real media path (visual_path, or a non-AI photo_spec), else ''."""
    spec = str(frame.get("photo_spec") or "")
    return frame.get("visual_path") or (spec if spec and not spec.startswith("ai_") else "")


def _preserve_real(frame: dict) -> None:
    """Stash the original real media (once) before an AI swap, so the operator can undo
    back to their untouched footage. No-op for a shot that has no real source."""
    if "orig_visual" not in frame:
        src = _real_source(frame)
        if src:
            frame["orig_visual"] = src


def set_ai_generic(state: dict, frame_id: str) -> dict:
    """Replace a shot's visual with a FULLY AI-generated image — no real footage, no face
    reference. The escape hatch for a matched real photo the operator dislikes that Restore
    can't fix (bad *content*, not bad quality). Identity-safe: a generic figure/scene from
    the shot's own prompt, never conditioned on a real person. Person shots → a generic
    `ai_portrait`; ambient → `ai_symbolic`. Original real media is preserved (`orig_visual`)
    so 'Use real photo' can revert."""
    f = next((x for x in state.get("frames", []) if x.get("frame_id") == frame_id), None)
    if not f:
        raise ValueError("unknown shot")
    _preserve_real(f)
    f["photo_spec"] = "ai_portrait" if f.get("uses_talent") else "ai_symbolic"
    f["forced_ai"] = True
    for k in ("visual_path", "character_ref_path", "ai_likeness",
              "restored", "recreated_from_real"):
        f.pop(k, None)                      # generic — no real media, no identity reference
    if state["stages"]["storyboard"]["status"] in ("done", "approved", "generating"):
        invalidate_from(state, "storyboard")
    state["board"] = board_cards(state["frames"])
    state["costs"] = stage_costs(state["frames"], quality=state.get("quality", "dev"))
    return state


# Fields the operator may edit per shot from the board (the editable prompt box).
EDITABLE_FRAME_FIELDS = {
    "caption", "director_note", "motion_override", "negative_prompt", "image_prompt",
    "emotion", "camera_angle",   # nested under scene (B1)
    "form_override",             # S28: force a shot's form (dialogue|narration|silent)
}
_SCENE_FIELDS = {"image_prompt", "emotion", "camera_angle"}

# S30 Phase 3: per-asset editorial review states (galleri5 teardown §10.4 item 4) —
# a QA verdict on each shot's current asset, layered UNDER the pipeline governance
# gates (this is "is it good", not "is it allowed"). Pure metadata: setting it never
# cascades or invalidates anything.
REVIEW_STATES = ("", "needs_review", "approved", "production_ready", "rejected")


def edit_frame(state: dict, frame_id: str, fields: dict) -> dict:
    """Edit one shot's text/prompt from the board. A visual edit makes downstream stages
    stale (cascade from storyboard). A DURATION-only edit leaves the still unchanged, so it
    only invalidates from Video (timing/clip length) — no wasteful still regen."""
    visual_changed = False
    timing_changed = False
    for f in state["frames"]:
        if f.get("frame_id") == frame_id:
            for k, v in (fields or {}).items():
                if k == "duration":
                    try:
                        f["duration"] = max(1.0, min(15.0, round(float(v), 1)))
                        timing_changed = True
                    except (TypeError, ValueError):
                        pass
                    continue
                if k == "review_status":
                    # S30 Phase 3: editorial QA verdict — metadata only, NEVER a
                    # visual change (must not cascade-invalidate anything).
                    if str(v) in REVIEW_STATES:
                        if str(v):
                            f["review_status"] = str(v)
                        else:
                            f.pop("review_status", None)
                    continue
                if k not in EDITABLE_FRAME_FIELDS:
                    continue
                if k in _SCENE_FIELDS:
                    f.setdefault("scene", {})[k] = str(v)
                else:
                    f[k] = str(v)
                visual_changed = True
            break
    else:
        raise ValueError(f"unknown frame {frame_id!r}")
    if visual_changed and state["stages"]["storyboard"]["status"] in ("done", "approved"):
        invalidate_from(state, "storyboard")
    elif timing_changed and state["stages"]["video"].get("status") in ("done", "approved", "generating"):
        invalidate_from(state, "video")   # duration-only → just re-time the clips
    # metadata-only edits (review_status) invalidate NOTHING — a QA verdict is not a change
    state["board"] = board_cards(state["frames"])
    state["costs"] = stage_costs(state["frames"], quality=state.get("quality", "dev"))
    return state


def redistribute_durations(state: dict, target_seconds: float) -> dict:
    """Rescale all shot durations proportionally to hit a target total (clamped 1–15s per
    shot). Only re-times — invalidates from Video, not the stills."""
    frames = state.get("frames", [])
    if not frames or target_seconds <= 0:
        return state
    cur = sum(float(f.get("duration") or 4) for f in frames) or 1.0
    scale = target_seconds / cur
    for f in frames:
        f["duration"] = max(1.0, min(15.0, round(float(f.get("duration") or 4) * scale, 1)))
    if state["stages"]["video"].get("status") in ("done", "approved", "generating"):
        invalidate_from(state, "video")
    state["board"] = board_cards(frames)
    return state


def _replan_from_brief(state: dict) -> dict:
    """Re-run the planner on state['brief'] IN PLACE and reset everything downstream.
    Shared by chat() (append a refinement) and replan_brief() (edit the whole story) so
    the reset rules live in exactly one place."""
    from agents import shot_planner
    new_frames = shot_planner.plan(state["brief"], scope=state.get("scope", "general"),
                                   mood=state.get("mood", ""),
                                   target_seconds=state.get("target_seconds", 0))
    if new_frames:
        state["frames"] = new_frames
    # A new shot list invalidates every downstream stage — generated stills/clips for
    # shots that changed or vanished must not linger (they'd show the OLD story).
    invalidate_from(state, "script")
    state["stages"]["script"].update(status="done")
    state["stages"]["storyboard"].update(ready=True)
    state["board"] = board_cards(state["frames"])
    state["costs"] = stage_costs(state["frames"], quality=state.get("quality", "dev"))
    return state


def chat(state: dict, message: str) -> dict:
    """The command box (our Studio-Chat equivalent): a natural-language refinement
    that re-plans the shot list via shot_planner. Reuses the brain; degrades safely
    (a planning failure just keeps the prior shots). Re-planning resets downstream."""
    msg = (message or "").strip()
    if not msg:
        return state
    state["brief"] = (state.get("brief", "") + "\n\nRefinement: " + msg).strip()
    return _replan_from_brief(state)


def replan_brief(state: dict, brief: str, *, scope: str = "", quality: str = "",
                 target_seconds: int | None = None, story_type: str = "") -> dict:
    """Edit-story: REPLACE the whole brief (+ optionally scope/quality/length/type) and
    re-plan in place. This is why the entry box is reachable after planning — you go back,
    change the story, and the SAME canvas rebuilds instead of stranding you or forcing a
    brand-new run. Everything downstream resets (see _replan_from_brief)."""
    b = (brief or "").strip()
    if not b:
        return state
    state["brief"] = b
    if scope:
        state["scope"] = scope
    if quality:
        state["quality"] = quality
    if target_seconds is not None:
        state["target_seconds"] = target_seconds
    if story_type in STORY_TYPES:
        state["story_type"] = story_type
    return _replan_from_brief(state)


# ── State machine ──────────────────────────────────────────────────────────────

def _fresh_stages() -> dict:
    st = {}
    for i, s in enumerate(STAGES):
        st[s] = {
            "status": "pending",       # pending | generating | done | approved
            "ready": i == 0,           # first stage is immediately runnable
            "paid": STAGE_META[s]["paid"],
        }
    return st


STORY_TYPES = {"real", "ai"}


def new_canvas(brief: str, *, scope: str = "general", mood: str = "",
               talent: dict | None = None, product: dict | None = None,
               quality: str = "dev", target_seconds: int = 0,
               story_type: str = "real") -> dict:
    """Create a canvas and run the (free) script stage. Reuses shot_planner, which
    already degrades to a sentence split offline — so this is network-safe.
    target_seconds: 0 = auto (length follows the story; rich stories run minutes).
    story_type: 'real' (HOB authentic — match/passthrough real media) or 'ai' (fiction —
    everything generated; the real-media tools are hidden, characters are defined on the
    sheet). It's a MODE FLAG into the shared engine, not a fork."""
    from agents import shot_planner
    story_type = story_type if story_type in STORY_TYPES else "real"
    frames = shot_planner.plan(brief, scope=scope, talent=talent,
                               product=product, mood=mood, target_seconds=target_seconds)
    stages = _fresh_stages()
    stages["script"].update(status="done")
    stages["storyboard"].update(ready=True)
    state = {
        "brief": brief, "scope": scope, "mood": mood, "quality": quality,
        "target_seconds": target_seconds, "story_type": story_type,
        "frames": frames,
        "stages": stages,
        "costs": stage_costs(frames, quality=quality),
        "board": board_cards(frames),
    }
    return state


def _world_clause(world: dict) -> str:
    """A compact art-direction + setting clause injected into EVERY shot so the reel's
    look and world stay consistent (same palace/forest, one art style) — P2 Worlds."""
    parts = []
    style = str(world.get("style") or "").strip()
    setting = str(world.get("setting") or "").strip()
    if style:
        parts.append(f"Art direction (keep consistent across every shot): {style}")
    if setting:
        parts.append(f"Same world/setting throughout: {setting}")
    return ". ".join(parts)


def set_world(state: dict, *, style: str = "", setting: str = "") -> dict:
    """Set the story's World/Context — a global art-direction + setting stamped onto every
    frame (`frame["world_style"]`) and injected into generation, so the whole reel shares
    one look and world. The consistency layer fiction needs (same Ayodhya, one style)."""
    w = state.setdefault("world", {})
    w["style"] = str(style or "").strip()
    w["setting"] = str(setting or "").strip()
    clause = _world_clause(w)
    for f in state["frames"]:
        if clause:
            f["world_style"] = clause
        else:
            f.pop("world_style", None)
    if state["stages"]["keyframes"].get("status") in ("done", "approved", "generating"):
        invalidate_from(state, "keyframes")
    state["board"] = board_cards(state["frames"])
    return state


def run_stage(state: dict, stage: str) -> dict:
    """Execute a non-paid stage in-process (reusing agents). Paid stages are gated
    here and dispatched to the existing pipeline by the caller — never rendered in
    this module (the bright line)."""
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}")
    frames = state["frames"]

    if stage == "script":
        # Re-plan from the (possibly edited) brief; shot_planner is cached + safe.
        from agents import shot_planner
        state["frames"] = shot_planner.plan(
            state.get("brief", ""), scope=state.get("scope", "general"),
            mood=state.get("mood", ""))
        frames = state["frames"]

    elif stage == "storyboard":
        # Enrich frames with director scenes (emotion / motion / camera). Degrade
        # gracefully (rule #4): offline we still build cards from planner output.
        try:
            from agents import scene_intelligence
            scene_intelligence.design_all_scenes(frames)
        except Exception as e:
            print(f"[Canvas] storyboard scene design degraded ({e})")

    elif STAGE_META[stage]["paid"]:
        # Bright line: paid generation reuses the existing engine; this module does
        # not render. The route layer dispatches and streams; we only gate + cost.
        raise PaidStageDispatch(stage)

    state["stages"][stage].update(status="done")
    state["board"] = board_cards(frames)
    state["costs"] = stage_costs(frames, quality=state.get("quality", "dev"))
    return state


# Gated flow: Script → Storyboard → Key Frames → Video → Final Cut. Audio is chosen
# in the audio bar and produced inside Final Cut, so it is not a separate gate.
_NEXT_GATE = {"script": "storyboard", "storyboard": "keyframes",
              "keyframes": "video", "video": "finalcut"}


def approve(state: dict, stage: str) -> dict:
    """Approve a finished stage and unlock the next one's Generate button."""
    state["stages"][stage].update(status="approved")
    nxt = _NEXT_GATE.get(stage)
    if nxt:
        state["stages"][nxt].update(ready=True)
    return state


def invalidate_from(state: dict, stage: str) -> dict:
    """Cascade: editing/re-running an upstream stage marks it and everything
    downstream 'pending' so stale boards/renders can't ship (reference-chaining)."""
    hit = False
    for s in STAGES:
        if s == stage:
            hit = True
        if hit:
            ready = (s == stage)
            state["stages"][s].update(status="pending", ready=ready)
    return state


def derive_characters(state: dict) -> list[dict]:
    """Characters/Assets stage (our moat-respecting take on galleri5's stage 2): run
    cast detection to surface the REAL people in the story, so the operator can anchor
    each to their real photo + consent — instead of inventing synthetic character sheets.
    Tags frames with speaker_id; merges any refs/consent already set."""
    from agents import cast
    try:
        members = cast.detect_cast(state["frames"])     # also tags frames[].speaker_id
    except Exception as e:
        print(f"[Canvas] cast detection degraded ({e})")
        members = [{"id": "narrator", "label": "Narrator", "gender": "", "age_bracket": ""}]
    existing = {c["id"]: c for c in state.get("characters", [])}
    chars = []
    for m in members:
        prev = existing.get(m["id"], {})
        chars.append({
            **m,
            "ref_path": prev.get("ref_path", ""),
            "consent": bool(prev.get("consent", False)),
            # Story-level sheet attributes — seed from cast (gender/age), operator fills rest.
            "role":      prev.get("role", ""),
            "name":      prev.get("name", m.get("label", "")),
            "gender":    prev.get("gender", m.get("gender", "")),
            "age":       prev.get("age", m.get("age_bracket", "")),
            "skin_tone": prev.get("skin_tone", ""),
            "hair":      prev.get("hair", ""),
            "clothing":  prev.get("clothing", ""),
            "source":    prev.get("source", ""),
        })
    state["characters"] = chars
    return chars


# Story-level character sheet attributes (A1). Frame overrides still win (the resolver
# only fills a character's shots that don't set their own).
# T11: "species" carries non-human anatomy (e.g. "vanara — monkey-like face, long curved
# monkey tail") so mythological characters keep consistent anatomy across every shot —
# the Hanuman snake-tail/monkey-tail drift was this attribute not existing.
# T4: "voice_id" gives each character their own narrator (radio-drama dialogue) —
# resolved per-frame by cast.voice_for_frame via the render data's voice_map.
CHARACTER_ATTRS = ("role", "name", "gender", "age", "skin_tone", "hair", "clothing",
                   "species", "voice_id", "source")


def _character_appearance(char: dict) -> str:
    """Compact appearance clause from a character's attributes, injected into that
    character's shots so their look (anatomy/age/gender/skin/hair/wardrobe) stays
    consistent. Wardrobe + species are phrased as INVARIANTS ("always…", "exactly…")
    — soft phrasing let outfits and anatomy drift shot-to-shot (T11)."""
    look = " ".join(str(char.get(k) or "").strip()
                    for k in ("age", "gender", "skin_tone", "hair") if char.get(k)).strip()
    who = look or (char.get("name") or char.get("label") or "").strip()
    species = str(char.get("species") or "").strip()
    clothing = str(char.get("clothing") or "").strip()
    parts = [p for p in (
        (f"a {species} — this exact anatomy in every shot" if species else ""),
        who,
        (f"always wearing {clothing} (the same outfit in every scene)" if clothing else ""),
    ) if p]
    return ", ".join(parts)


def set_character(state: dict, char_id: str, *, ref_path: str = "",
                  consent: bool | None = None, attrs: dict | None = None) -> dict:
    """Update a story-level character: its real reference photo (+ consent) AND its
    appearance attributes (role/name/gender/age/skin/hair/clothing/source). The values
    propagate to every shot the character speaks in — the face reference for identity,
    and an appearance clause for a consistent look — so they stay the same person across
    the reel without editing each frame. (Per-frame overrides still take precedence.)"""
    char = next((c for c in state.get("characters", []) if c["id"] == char_id), None)
    if char is None:
        raise ValueError("unknown character")
    if ref_path:
        char["ref_path"] = ref_path
    if consent is not None:
        char["consent"] = bool(consent)
    if attrs:
        for k in CHARACTER_ATTRS:
            if k in attrs:
                char[k] = str(attrs[k]).strip()

    ref = char.get("ref_path", "")
    appearance = _character_appearance(char)
    for f in state["frames"]:
        if f.get("speaker_id") != char_id:
            continue
        if ref:
            f["character_ref_path"] = ref
            if not (f.get("photo_spec") or "").startswith("ai_") and not f.get("visual_path"):
                f["photo_spec"] = "ai_portrait"   # AI likeness, conditioned on the real face
        if appearance:
            f["character_appearance"] = appearance   # consistent look for this character's shots
    if state["stages"]["keyframes"].get("status") in ("done", "approved", "generating"):
        invalidate_from(state, "keyframes")
    state["board"] = board_cards(state["frames"])
    return state


# ── Location anchoring (S30 Phase 1 — the S28 "character sheet for places") ─────────
# Mirrors the character-sheet machinery one level up: derive the story's distinct
# PLACES, tag every frame with its location_id (like speaker_id), and propagate an
# INVARIANT location clause (+ optional generated plate) onto that location's shots so
# the same cave/kitchen/street stays the same place across the reel (T11 phrasing —
# soft wording is how outfits and anatomy drifted).

LOCATION_ATTRS = ("label", "description", "time_of_day")

_LOCATION_SCHEMA = {"name": "locations", "schema": {
    "type": "object", "additionalProperties": False,
    "properties": {
        "locations": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "id":          {"type": "string"},
                "label":       {"type": "string"},
                "description": {"type": "string"},
                "time_of_day": {"type": "string"},
            },
            "required": ["id", "label", "description", "time_of_day"],
        }},
        "by_frame": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "frame_id":    {"type": "string"},
                "location_id": {"type": "string"},
            },
            "required": ["frame_id", "location_id"],
        }},
    },
    "required": ["locations", "by_frame"],
}}


def _loc_key(loc: dict) -> str:
    """Normalised label — the re-derive fallback for when the model churns an id."""
    return " ".join((loc.get("label") or "").lower().split())


# Disk cache for the derive pass — same convention as scene_intelligence's
# (~/.hob_cache/<thing>/<md5>.json, "cached after first run, then $0"). 🏞 Locations is a
# reasoning-tier completion and re-deriving is a DESIGNED flow ("re-derive keeps work"),
# so an uncached button re-spent a full completion on every click of an unchanged story.
LOCATION_CACHE_DIR = Path.home() / ".hob_cache" / "locations"
LOCATION_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _locations_cache_key(beat_list: str) -> str:
    # Keyed on the BEATS only — deliberately not on the existing location ids, even though
    # they ride the prompt. Keying on them would miss on every re-derive (the ids only
    # exist from the 2nd click on), i.e. exactly the case this cache is for. A hit replays
    # the same locations, so ids are stable for free; the reuse-ids instruction only has to
    # earn its keep on a miss (= the story actually changed).
    return hashlib.md5(beat_list.encode()).hexdigest()


def _locations_cache_load(key: str) -> dict | None:
    p = LOCATION_CACHE_DIR / f"{key}.json"
    if p.exists():
        try:
            with open(p) as f:
                return json.load(f)
        except Exception:
            pass
    return None


def _locations_cache_save(key: str, data: dict) -> None:
    try:
        with open(LOCATION_CACHE_DIR / f"{key}.json", "w") as f:
            json.dump(data, f)
    except Exception:
        pass          # a cache write must never break the stage (rule #4)


def derive_locations(state: dict) -> list[dict]:
    """Locations stage: ONE LLM pass over the beats → the story's distinct physical
    places (screenplay-slugline granularity, deduped), tagging frames[].location_id.
    Safe no-op on any failure: frames stay untagged and generation behaves exactly as
    today. Merges plate/attrs already set by the operator (re-derive keeps work)."""
    frames = state.get("frames") or []
    # `or {}` throughout: a frame can carry scene=None (board_cards/image_matcher/plan_qc
    # all guard the same access) and dict.get's default does NOT apply to a present None —
    # this sits outside the try below, so an unguarded .get() 500s the whole route.
    beats = [f for f in frames
             if (f.get("caption") or (f.get("scene") or {}).get("image_prompt"))]
    state.pop("locations_warning", None)
    if not beats:
        state.setdefault("locations", [])
        return state["locations"]          # a real answer: nothing to anchor
    beat_list = "\n".join(
        f"{f['frame_id']}: {(f.get('caption') or '').strip()[:160]} "
        f"[{((f.get('scene') or {}).get('image_prompt') or '')[:120]}]"
        for f in beats)
    existing = [l for l in (state.get("locations") or []) if l.get("id")]
    sys = (
        "You analyse a story reel's beats and extract its distinct physical LOCATIONS "
        "(screenplay-slugline granularity: 'cave interior', 'chai stall at dawn' — not "
        "camera angles). DEDUPE aggressively: the same place at the same rough time is "
        "ONE location; most stories have 1-4. Use short stable snake_case ids. "
        "description = the place itself (architecture, surfaces, key props, light), "
        "NEVER the people in it. Assign EVERY beat to exactly one location.")
    if existing:
        # Re-derive: hand the model the ids it already chose and require reuse. A churned
        # id ("cave" → "cave_interior") orphans that location's operator edits AND its PAID
        # plate, so id stability here is a money question, not a tidiness one.
        sys += ("\nThese locations ALREADY EXIST — reuse the EXACT id for the same place; "
                "mint a new id ONLY for a genuinely new place: "
                + "; ".join(f'{l["id"]} = {l.get("label") or l["id"]}' for l in existing))
    user = (f"Beats:\n{beat_list}\n\n"
            'Reply ONLY as JSON: {"locations":[{"id","label","description","time_of_day"}],'
            ' "by_frame":[{"frame_id","location_id"}]}.')
    ckey = _locations_cache_key(beat_list)
    try:
        data = _locations_cache_load(ckey)
        if data is not None:
            print("[Canvas] locations: cache hit — no spend")
        else:
            from agents import llm
            text = llm.chat([{"role": "system", "content": sys},
                             {"role": "user", "content": user}],
                            json_mode=True, json_schema=_LOCATION_SCHEMA,
                            max_tokens=1200, model_tier="reasoning")
            data = llm.json_loads_lenient(text)
        locs = [l for l in data.get("locations", []) if (l.get("id") or "").strip()]
        if not locs:
            raise ValueError("no locations returned")
        _locations_cache_save(ckey, data)     # only ever cache a USABLE result
    except Exception as e:
        print(f"[Canvas] location derivation degraded ({e}) — shots stay unanchored")
        try:
            from agents import degradation
            degradation.report("plan", "warn",
                               f"location derivation unavailable ({e}); shots render without a location anchor")
        except Exception:
            pass
        # Distinguish FAILED from "this story has no distinct places". Both used to
        # return the same empty list, so the UI reported an outage as a confident
        # negative and the operator never retried.
        state["locations_warning"] = (
            "Couldn't derive locations just now — shots will render without a location "
            "anchor. Nothing was charged; try again.")
        state.setdefault("locations", [])
        return state["locations"]

    prev_by_id = {l["id"]: l for l in existing}
    prev_by_label = {}
    for l in existing:
        if _loc_key(l):
            prev_by_label.setdefault(_loc_key(l), l)
    claimed, merged = set(), []
    for l in locs:
        # id first, then the normalised label — so operator work survives an id churn the
        # prompt above failed to prevent. First match wins; a prev can only be claimed once
        # (two new locations must never inherit the same paid plate).
        prev = prev_by_id.get(l["id"]) or prev_by_label.get(_loc_key(l))
        if prev is not None and prev["id"] in claimed:
            prev = None
        prev = prev or {}
        if prev.get("id"):
            claimed.add(prev["id"])
        merged.append({
            "id": l["id"],
            "label":       prev.get("label") or l.get("label", l["id"]),
            "description": prev.get("description") or l.get("description", ""),
            "time_of_day": prev.get("time_of_day") or l.get("time_of_day", ""),
            "plate_path":  prev.get("plate_path", ""),
            "source":      prev.get("source", ""),
        })
    # A location this pass dropped whose plate was PAID for: never bin that silently.
    lost = [l for l in existing
            if l["id"] not in claimed and (l.get("plate_path") or "").strip()]
    if lost:
        try:
            from agents import degradation
            degradation.report("plan", "warn",
                               "re-derive dropped location(s) carrying a paid plate: "
                               + ", ".join(l.get("label") or l["id"] for l in lost)
                               + " — the plate files are kept on disk, but no shot uses them now")
        except Exception:
            pass

    state["locations"] = merged
    by_frame = {b.get("frame_id"): b.get("location_id") for b in data.get("by_frame", [])}
    known = {l["id"] for l in merged}
    before = {f.get("frame_id"): (f.get("location_clause", ""), f.get("location_ref_path", ""))
              for f in frames}
    for f in frames:
        lid = by_frame.get(f.get("frame_id"), "")
        if lid in known:
            f["location_id"] = lid
        else:
            # Dropped / renamed / unassigned → clear the WHOLE tag. A leftover clause keeps
            # riding this shot's prompt (and its still cache key) while being invisible on
            # the Locations sheet — pinning the shot to a place the story no longer has.
            for k in ("location_id", "location_clause", "location_ref_path"):
                f.pop(k, None)
    # Re-apply any locations that already carry operator work (plate/edited attrs) so
    # the fresh frame tags immediately pick up the existing clause + plate.
    for l in merged:
        if l.get("plate_path") or l.get("description"):
            _propagate_location(state, l)
    after = {f.get("frame_id"): (f.get("location_clause", ""), f.get("location_ref_path", ""))
             for f in frames}
    # The clause rides the prompt → the still's cache key, so moving it must invalidate
    # already-rendered keyframes — the rule set_location already follows. Only when it
    # actually moved: an unchanged re-derive must not force a needless re-render.
    if after != before and state.get("stages", {}).get("keyframes", {}).get("status") in (
            "done", "approved", "generating"):
        invalidate_from(state, "keyframes")
    return merged


def _location_clause(loc: dict) -> str:
    """Invariant setting clause for one location's shots. Same lesson as T11: soft
    phrasing lets the place redraw itself shot-to-shot, so the wording pins it."""
    label = (loc.get("label") or "").strip()
    desc = (loc.get("description") or "").strip()
    tod = (loc.get("time_of_day") or "").strip()
    if not (label or desc):
        return ""
    bits = ", ".join(b for b in (desc, tod) if b)
    return (f"Setting — this EXACT location in every shot set here: {label or 'the location'}"
            + (f" ({bits})" if bits else "")
            + ". Same geometry, same architecture, same light direction whenever it appears")


def _propagate_location(state: dict, loc: dict) -> None:
    """Stamp one location's clause (+ plate ref) onto every frame tagged with it.
    The FACE reference still wins the single-ref edit path (S19/S20: identity beats
    place); the plate ref is carried for the D5 multi-ref follow-up."""
    clause = _location_clause(loc)
    plate = (loc.get("plate_path") or "").strip()
    for f in state.get("frames") or []:
        if f.get("location_id") != loc["id"]:
            continue
        if clause:
            f["location_clause"] = clause
        else:
            f.pop("location_clause", None)
        if plate:
            f["location_ref_path"] = plate
        else:
            f.pop("location_ref_path", None)


BRAND_FIELDS = ("name", "cta_text", "logo_path", "product_name", "vo_mode", "music_mode",
                "vo_audio_path", "music_audio_path")


def set_brand(state: dict, brand: dict | None) -> dict:
    """Set this canvas's brand mandatories (S31). Empty/absent ⇒ back to story mode.

    Copy is stored VERBATIM and never generated — BRAND_PLAN §5 ("AI never writes brand
    ad claims"). This module only stores it; `brand.validate_mandatories` (armed at the
    render route once web_app._canvas_mode(state) == "brand") decides whether it's
    complete enough to spend on, and `_run_inner` burns the disclosure.
    """
    b = dict(state.get("brand") or {})
    for k in BRAND_FIELDS:
        if k in (brand or {}):
            b[k] = str((brand or {})[k]).strip()
    if "disclosure" in (brand or {}):
        b["disclosure"] = bool((brand or {})["disclosure"])
    if "mandatories" in (brand or {}) and isinstance((brand or {})["mandatories"], dict):
        b["mandatories"] = {k: bool(v) for k, v in (brand or {})["mandatories"].items()}
    # Everything blank ⇒ the operator cleared it; drop the key so the run is a story
    # again rather than a brand run that can never satisfy its own mandatories.
    if not any(str(b.get(k) or "").strip() for k in ("name", "cta_text", "logo_path")):
        state.pop("brand", None)
    else:
        b.setdefault("disclosure", True)   # sponsored disclosure defaults ON
        state["brand"] = b
    return state


def set_location(state: dict, loc_id: str, *, plate_path: str = "",
                 attrs: dict | None = None, propagate: bool = True) -> dict:
    """Update a story-level location (attrs and/or its generated plate) and propagate
    to every frame tagged with it — the exact mirror of set_character.

    propagate=False stores the values only. The plate route applies just-typed attrs
    BEFORE generating (so the prompt and the content hash use them), then applies the
    plate after — and the first of those two calls has no reason to walk every frame,
    invalidate and rebuild the whole board when the second call will do it anyway.
    """
    loc = next((l for l in state.get("locations", []) if l["id"] == loc_id), None)
    if loc is None:
        raise ValueError("unknown location")
    if plate_path:
        loc["plate_path"] = plate_path
    if attrs:
        for k in LOCATION_ATTRS:
            if k in attrs:
                loc[k] = str(attrs[k]).strip()
    if not propagate:
        return state
    _propagate_location(state, loc)
    if state.get("stages", {}).get("keyframes", {}).get("status") in (
            "done", "approved", "generating"):
        invalidate_from(state, "keyframes")
    state["board"] = board_cards(state["frames"])
    return state


def public_state(state: dict) -> dict:
    """The board view sent to the client — costs, stage statuses, board cards and
    the asset legend. (Frames carry internal keys; the board is the view model.)"""
    etas = stage_etas(state.get("frames", []), quality=state.get("quality", "dev"))
    return {
        "brief": state.get("brief", ""),
        "scope": state.get("scope", "general"),
        "quality": state.get("quality", "dev"),
        "target_seconds": state.get("target_seconds", 0),
        "stages": [
            {"id": s, **STAGE_META[s], **state["stages"][s],
             "cost_usd": state.get("costs", {}).get(s, 0.0),
             "eta_sec": etas.get(s, 0)}
            for s in STAGES
        ],
        # Board cards enriched with the live form tag (S28) so canvases saved before
        # the classifier existed still show correct tags without a board rebuild.
        "board": [
            {**card, "form": shot_form(fmap.get(card.get("frame_id"), card))}
            for fmap in [{f.get("frame_id"): f for f in state.get("frames") or []}]
            for card in state.get("board", [])
        ],
        "total_cost_usd": round(sum(state.get("costs", {}).values()), 4),
        "legend": {"real": ASSET_REAL, "ai": ASSET_AI, "ai_person": ASSET_AI_PERSON},
        "render_id": state.get("render_id", ""),   # set once a full render is dispatched
        "restoring": bool(state.get("restoring", False)),
        "restore_done": state.get("restore_done", 0),
        "restore_total": state.get("restore_total", 0),
        "sketching": bool(state.get("sketching", False)),
        "sketch_done": state.get("sketch_done", 0),
        "sketch_total": state.get("sketch_total", 0),
        "checking": bool(state.get("checking", False)),
        "check_done": state.get("check_done", 0),
        "check_total": state.get("check_total", 0),
        "characters": state.get("characters", []),
        # S30 Phase 1 location anchoring: the story's places (the "character sheet
        # for places") — label/description/time_of_day + the generated plate.
        "locations": state.get("locations", []),
        "caption_style": state.get("caption_style") or {},   # operator caption settings
        "orientation": state.get("orientation") or "portrait",
        "story_type": state.get("story_type") or "real",     # 'real' (HOB) | 'ai' (fiction)
        # S31: operator-supplied brand mandatories. Non-empty ⇒ the run is a branded ad
        # (web_app._canvas_mode) ⇒ brand.validate_mandatories gates the paid render and
        # the sponsored disclosure burns in. Copy is ALWAYS verbatim (BRAND_PLAN §5).
        "brand": state.get("brand") or {},
        # "" unless the last derive FAILED — an outage must not read as "no locations".
        "locations_warning": state.get("locations_warning", ""),
        "world": state.get("world") or {},                    # P2: global art-direction/setting
        "ip": state.get("ip", ""),                            # watermark IP id
        "transition": state.get("transition") or "crossfade",
        # Set when the last Final Cut produced no audible audio (e.g. the music
        # engine failed) — the board must show this, never a clean 'done ✓'.
        "audio_warning": state.get("audio_warning", ""),
        # T1 Degradation Ledger: the last render's quality receipt — every fallback/
        # QC event that fired ({step, severity: info|warn|alert, msg, ts}).
        "render_report": state.get("render_report", []),
        # A2 story-review contract gate: deterministic plan-time warnings
        # ({id, severity, frame_id, message, fix}) — shown in the board report.
        "plan_review": state.get("plan_review", []),
        # Item 8 slideshow-risk gate (plan_qc): {total: 0-5, risk: low|medium|high} —
        # the board's pre-spend "will this feel like a slideshow?" chip.
        "plan_qc": state.get("plan_qc", {}),
        # Reel-level mood (auto-fill slice 1): saved operator value; the mood input
        # ✨-fills from suggestions.mood only while this is empty.
        "mood": state.get("mood", ""),
        # T3 plan-time auto-fill: SUGGESTED world/length/voice derived from the brief.
        # The UI fills only empty fields (marked ✨); nothing applies without the operator.
        "suggestions": state.get("suggestions", {}),
        # S28 detect→declare: story-level form verdict + live warnings (always fresh).
        "story_form": story_form(state.get("frames") or []),
        "form_warnings": form_warnings(state),
        # T13 language versions: reviewed translations per language + the render id of
        # each finished language version (original render is never overwritten).
        "translations": state.get("translations", {}),
        "language_renders": state.get("language_renders", {}),
    }


class PaidStageDispatch(Exception):
    """Signal that a paid stage must be dispatched to the existing render pipeline
    (the route layer handles spend reservation + generation), not run here."""
    def __init__(self, stage: str):
        super().__init__(f"paid stage {stage!r} must dispatch to the render pipeline")
        self.stage = stage


def plan_suggestions(brief: str, story_type: str = "real") -> dict:
    """T3 plan-time auto-fill (L99_ARCH_PLAN): derive SUGGESTED settings from the brief —
    world style/setting, a target length if the brief states one, and a narrator-voice
    profile. Suggestions only: the UI fills EMPTY fields, marks them ✨, and nothing is
    applied to generation until the operator acts (world needs Apply; voice is a hint).
    One cheap fast-tier call; safe no-op ({}) on any failure."""
    from agents import llm
    try:
        res = llm.chat([{"role": "user", "content": (
            "You prepare production settings for a short 9:16 story reel.\n"
            f"STORY BRIEF:\n{brief[:4000]}\n\n"
            f"Story type: {'fully AI-generated fiction/mythology' if story_type == 'ai' else 'real people, real photos'}.\n"
            "Reply JSON only:\n"
            "{\"world_style\": \"<art direction, <=12 words, e.g. 'epic cinematic photorealism, warm golden filmic grade'>\",\n"
            " \"world_setting\": \"<the story's world/place/era, <=12 words>\",\n"
            " \"target_seconds\": <int seconds ONLY if the brief explicitly states a length, else 0>,\n"
            " \"narrator_profile\": \"<voice direction, <=8 words, e.g. 'deep warm unhurried male, reverent'>\",\n"
            " \"mood\": \"<overall emotional tone, <=6 words, e.g. 'bittersweet, quietly hopeful'>\"}"
        )}], json_mode=True, max_tokens=220, model_tier="fast")
        data = llm.json_loads_lenient(res) or {}
        out = {
            "world_style": str(data.get("world_style") or "").strip()[:120],
            "world_setting": str(data.get("world_setting") or "").strip()[:120],
            "narrator_profile": str(data.get("narrator_profile") or "").strip()[:80],
            # Reel-level mood suggestion (auto-fill slice 1). Per-shot EMOTION stays
            # operator-owned (owner carve-out); like every suggestion the UI fills the
            # field only when EMPTY, marked ✨ — the operator always decides.
            "mood": str(data.get("mood") or "").strip()[:60],
        }
        try:
            ts = int(data.get("target_seconds") or 0)
            out["target_seconds"] = ts if 0 < ts <= 600 else 0
        except (TypeError, ValueError):
            out["target_seconds"] = 0
        return out
    except Exception as e:
        print(f"[Canvas] plan suggestions skipped ({e})")
        return {}
