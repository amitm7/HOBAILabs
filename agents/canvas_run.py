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
}
_SCENE_FIELDS = {"image_prompt", "emotion", "camera_angle"}


def edit_frame(state: dict, frame_id: str, fields: dict) -> dict:
    """Edit one shot's text/prompt from the board. A visual edit makes downstream stages
    stale (cascade from storyboard). A DURATION-only edit leaves the still unchanged, so it
    only invalidates from Video (timing/clip length) — no wasteful still regen."""
    visual_changed = False
    for f in state["frames"]:
        if f.get("frame_id") == frame_id:
            for k, v in (fields or {}).items():
                if k == "duration":
                    try:
                        f["duration"] = max(1.0, min(15.0, round(float(v), 1)))
                    except (TypeError, ValueError):
                        pass
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
    elif not visual_changed and state["stages"]["video"].get("status") in ("done", "approved", "generating"):
        invalidate_from(state, "video")   # duration-only → just re-time the clips
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


def chat(state: dict, message: str) -> dict:
    """The command box (our Studio-Chat equivalent): a natural-language refinement
    that re-plans the shot list via shot_planner. Reuses the brain; degrades safely
    (a planning failure just keeps the prior shots). Re-planning resets downstream."""
    msg = (message or "").strip()
    if not msg:
        return state
    state["brief"] = (state.get("brief", "") + "\n\nRefinement: " + msg).strip()
    from agents import shot_planner
    new_frames = shot_planner.plan(state["brief"], scope=state.get("scope", "general"),
                                   mood=state.get("mood", ""),
                                   target_seconds=state.get("target_seconds", 0))
    if new_frames:
        state["frames"] = new_frames
    invalidate_from(state, "script")
    state["stages"]["script"].update(status="done")
    state["stages"]["storyboard"].update(ready=True)
    state["board"] = board_cards(state["frames"])
    state["costs"] = stage_costs(state["frames"], quality=state.get("quality", "dev"))
    return state


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
        "board": state.get("board", []),
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
        "caption_style": state.get("caption_style") or {},   # operator caption settings
        "orientation": state.get("orientation") or "portrait",
        "story_type": state.get("story_type") or "real",     # 'real' (HOB) | 'ai' (fiction)
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
        # T3 plan-time auto-fill: SUGGESTED world/length/voice derived from the brief.
        # The UI fills only empty fields (marked ✨); nothing applies without the operator.
        "suggestions": state.get("suggestions", {}),
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
            " \"narrator_profile\": \"<voice direction, <=8 words, e.g. 'deep warm unhurried male, reverent'>\"}"
        )}], json_mode=True, max_tokens=200, model_tier="fast")
        data = llm.json_loads_lenient(res) or {}
        out = {
            "world_style": str(data.get("world_style") or "").strip()[:120],
            "world_setting": str(data.get("world_setting") or "").strip()[:120],
            "narrator_profile": str(data.get("narrator_profile") or "").strip()[:80],
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
