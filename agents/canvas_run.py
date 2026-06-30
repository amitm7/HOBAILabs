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
    """Rough wall-clock estimate per stage (seconds) for the '~Nm' hint. Scales with
    shot count + tier; deliberately coarse — it's a hint, not a promise."""
    n = max(1, len(frames))
    prod = quality != "dev"
    return {
        "script":     0,
        "storyboard": n * 4,
        "keyframes":  n * (16 if prod else 8),
        "audio":      n * 3,
        "video":      n * (70 if prod else 35),
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
            "duration":   f.get("duration"),
        })
    return cards


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
        if mode == "real":
            f["photo_spec"] = path          # non-AI spec → model_router PASSTHROUGH
            f["visual_path"] = path
            f.pop("character_ref_path", None)
        elif mode == "reference":
            f["character_ref_path"] = path  # AI likeness conditioned on the real face
            f["photo_spec"] = "ai_portrait"
            f.pop("visual_path", None)
        else:  # scene
            f["character_ref_path"] = path
            if not (f.get("photo_spec") or "").startswith("ai_"):
                f["photo_spec"] = "ai_symbolic"
    if state["stages"]["storyboard"]["status"] in ("done", "approved"):
        invalidate_from(state, "storyboard")
    state["board"] = board_cards(state["frames"])
    state["costs"] = stage_costs(state["frames"], quality=state.get("quality", "dev"))
    return state


# Fields the operator may edit per shot from the board (the editable prompt box).
EDITABLE_FRAME_FIELDS = {
    "caption", "director_note", "motion_override", "negative_prompt", "image_prompt",
}


def edit_frame(state: dict, frame_id: str, fields: dict) -> dict:
    """Edit one shot's text/prompt from the board. Editing a shot makes every
    downstream stage stale (reference-chaining), so we cascade-invalidate from the
    storyboard — a re-approve regenerates only what changed."""
    for f in state["frames"]:
        if f.get("frame_id") == frame_id:
            for k, v in (fields or {}).items():
                if k not in EDITABLE_FRAME_FIELDS:
                    continue
                if k == "image_prompt":
                    f.setdefault("scene", {})["image_prompt"] = str(v)
                else:
                    f[k] = str(v)
            break
    else:
        raise ValueError(f"unknown frame {frame_id!r}")
    # Only reset downstream if the storyboard had already run; if we're still at the
    # script stage there is nothing downstream to invalidate.
    if state["stages"]["storyboard"]["status"] in ("done", "approved"):
        invalidate_from(state, "storyboard")
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


def new_canvas(brief: str, *, scope: str = "general", mood: str = "",
               talent: dict | None = None, product: dict | None = None,
               quality: str = "dev", target_seconds: int = 0) -> dict:
    """Create a canvas and run the (free) script stage. Reuses shot_planner, which
    already degrades to a sentence split offline — so this is network-safe.
    target_seconds: 0 = auto (length follows the story; rich stories run minutes)."""
    from agents import shot_planner
    frames = shot_planner.plan(brief, scope=scope, talent=talent,
                               product=product, mood=mood, target_seconds=target_seconds)
    stages = _fresh_stages()
    stages["script"].update(status="done")
    stages["storyboard"].update(ready=True)
    state = {
        "brief": brief, "scope": scope, "mood": mood, "quality": quality,
        "target_seconds": target_seconds,
        "frames": frames,
        "stages": stages,
        "costs": stage_costs(frames, quality=quality),
        "board": board_cards(frames),
    }
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
        chars.append({**m, "ref_path": prev.get("ref_path", ""),
                      "consent": bool(prev.get("consent", False))})
    state["characters"] = chars
    return chars


def set_character(state: dict, char_id: str, *, ref_path: str = "",
                  consent: bool | None = None) -> dict:
    """Anchor a character to a real reference photo (+ consent) and link it to that
    character's shots so their identity stays consistent — the real face, conditioned."""
    char = next((c for c in state.get("characters", []) if c["id"] == char_id), None)
    if char is None:
        raise ValueError("unknown character")
    if ref_path:
        char["ref_path"] = ref_path
    if consent is not None:
        char["consent"] = bool(consent)
    ref = char.get("ref_path", "")
    if ref:
        for f in state["frames"]:
            if f.get("speaker_id") == char_id:
                f["character_ref_path"] = ref
                if not (f.get("photo_spec") or "").startswith("ai_") and not f.get("visual_path"):
                    f["photo_spec"] = "ai_portrait"   # AI likeness, conditioned on the real face
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
        "characters": state.get("characters", []),
    }


class PaidStageDispatch(Exception):
    """Signal that a paid stage must be dispatched to the existing render pipeline
    (the route layer handles spend reservation + generation), not run here."""
    def __init__(self, stage: str):
        super().__init__(f"paid stage {stage!r} must dispatch to the render pipeline")
        self.stage = stage
