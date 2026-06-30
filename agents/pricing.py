"""
Reads config/pricing.json and exposes cost helpers.
All code that needs cost figures goes through here — never hardcode prices in Python.
"""
import json
import math
from pathlib import Path

_PRICING_PATH = Path(__file__).parent.parent / "config" / "pricing.json"

_FALLBACK = {
    "kling":     {"standard_5s_usd": 0.08, "pro_5s_usd": 0.14},
    "image_gen": {"flux_portrait_usd": 0.05, "openai_gpt_image_usd": 0.04, "openai_edit_usd": 0.04},
    "music":     {"suno_song_usd": 0.05},
    "voice":     {"elevenlabs_chars_per_dollar": 25000},
    "synclabs":  {"per_second_usd": 0.012},
    "hedra":     {"per_generation_usd": 0.10},
}


def load() -> dict:
    try:
        with open(_PRICING_PATH) as f:
            return json.load(f)
    except Exception:
        return _FALLBACK


def kling_cost(mode: str = "standard", duration_sec: float = 5.0) -> float:
    """Cost for one Kling clip. Billed per 5s block."""
    p = load()["kling"]
    base = p.get(f"{mode}_5s_usd", p.get("standard_5s_usd", 0.08))
    blocks = max(1, math.ceil(duration_sec / 5))
    return round(base * blocks, 4)


def image_cost(kind: str = "flux") -> float:
    """Cost for one image generation or edit call."""
    p = load()["image_gen"]
    return {
        "flux":   p.get("flux_portrait_usd", 0.05),
        "openai": p.get("openai_gpt_image_usd", 0.04),
        "edit":   p.get("openai_edit_usd", 0.04),
    }.get(kind, 0.04)


def upscale_cost(creative: bool = False) -> float:
    """Cost to upscale one still. creative=clarity (AI stills), else aura_sr (real, faithful)."""
    p = load().get("upscale", {})
    return p.get("clarity_usd", 0.05) if creative else p.get("aura_sr_usd", 0.02)


def music_cost() -> float:
    return load()["music"].get("suno_song_usd", 0.05)


def voice_cost(char_count: int) -> float:
    cpd = load()["voice"].get("elevenlabs_chars_per_dollar", 25000)
    return round(char_count / cpd, 4)


def synclabs_cost(duration_sec: float) -> float:
    """Cost for one SyncLabs lip sync job (video source)."""
    p = load().get("synclabs", {"per_second_usd": 0.012})
    return round(p.get("per_second_usd", 0.012) * max(1.0, duration_sec), 4)


def hedra_cost() -> float:
    """Cost for one Hedra character-3 generation (image source)."""
    p = load().get("hedra", {"per_generation_usd": 0.10})
    return round(p.get("per_generation_usd", 0.10), 4)


def higgsfield_cost() -> float:
    """Cost for one Higgsfield generation (always 5s)."""
    p = load().get("higgsfield", {"generation_5s_usd": 0.10})
    return round(p.get("generation_5s_usd", 0.10), 4)


def scene_cost() -> float:
    """GPT-4.1 scene intelligence cost per captioned frame (cached after first run)."""
    p = load().get("scene_intelligence", {"gpt_per_frame_usd": 0.01})
    return round(p.get("gpt_per_frame_usd", 0.01), 4)


def _lookup(dotted: str, default: float = 0.0) -> float:
    """Resolve a dotted key path (e.g. 'kling.standard_5s_usd') in pricing.json."""
    cur = load()
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return default
        cur = cur.get(part)
        if cur is None:
            return default
    return cur if isinstance(cur, (int, float)) else default


def model_cost(model_id: str, duration_sec: float = 5.0) -> float:
    """
    Cost for one generation by any model in config/models.json, via its
    pricing_key. Video models bill per 5s block; image models bill once.
    """
    from agents import model_router
    key = model_router.model_field(model_id, "pricing_key")
    if not key:
        return 0.0
    base = _lookup(key, 0.0)
    if model_router.model_field(model_id, "kind") == "video":
        blocks = max(1, math.ceil(duration_sec / 5))
        return round(base * blocks, 4)
    return round(base, 4)


def estimate(frames: list[dict], kling_mode: str = "standard",
             force_5s: bool = False, music_type: str = "none",
             voice_chars: int = 0, provider: str = "kling",
             skip_scene_ai: bool = False, cost_tier: str = "",
             image_model: str = "auto", video_model: str = "auto",
             multi_shot: bool = False, approved_ids: set | None = None) -> dict:
    """
    Estimate total render cost across the WHOLE pipeline.

    Counts every paid service:
      - Scene intelligence (GPT-4.1) per captioned frame
      - Image generation (Flux / fal.ai for portraits, GPT-image for symbolic)
      - Image edits (gpt-image-1)
      - Animation: Kling OR Higgsfield (per provider) — skipped for video sources & kenburns
      - Lip sync: SyncLabs (video) or Hedra (image) — replaces animation on those frames
      - Lip-sync audio (ElevenLabs TTS) per lipsync frame
      - Music (Suno) or voiceover track (ElevenLabs)

    frame dict keys used: photo_spec, visual_path, duration, edit_prompt, lipsync, caption
    """
    from agents import model_router

    scene_count    = 0;  scene_total    = 0.0
    anim_count     = 0;  anim_total     = 0.0   # router-resolved video models
    image_count    = 0;  image_total    = 0.0
    edit_count     = 0;  edit_total     = 0.0
    lipsync_count  = 0;  lipsync_total  = 0.0
    ls_audio_chars = 0

    _VIDEO_EXTS = {".mp4", ".mov", ".avi", ".m4v", ".webm"}
    tier = cost_tier or ("draft" if force_5s else "premium")
    g_img = image_model if image_model != "auto" else ""
    g_vid = video_model if video_model != "auto" else ""
    used_models = set()

    for f in frames:
        spec     = f.get("photo_spec", "")
        vpath    = f.get("visual_path", "")
        dur      = f.get("duration", 5.0)
        caption  = (f.get("caption") or "").strip()
        is_video = bool(vpath and Path(vpath).suffix.lower() in _VIDEO_EXTS)

        # Scene intelligence — one GPT call per captioned frame (unless skipped)
        if caption and not skip_scene_ai:
            scene_count += 1;  scene_total += scene_cost()

        # Image generation cost — router picks the model per shot
        if spec in ("ai_portrait", "ai_symbolic"):
            img_model = model_router.select_model(
                "image", f, tier, override=f.get("image_model_override", "") or g_img)
            if img_model != model_router.PASSTHROUGH:
                image_count += 1;  image_total += model_cost(img_model)

        # Image edit cost
        if f.get("edit_prompt"):
            edit_count += 1;  edit_total += image_cost("edit")

        # Lipsync frames: vendor cost + ElevenLabs audio cost, skip animation
        if f.get("lipsync"):
            lipsync_count += 1
            lipsync_total += synclabs_cost(dur) if is_video else hedra_cost()
            ls_audio_chars += len(caption)
            continue

        # Animation cost — router picks the video model (videos already move; kenburns is free)
        if not is_video and (spec.startswith("ai_") or vpath):
            vid_model = model_router.select_model(
                "video", f, tier, override=f.get("video_model_override", "") or g_vid)
            # Approval gate: unapproved frames fall back to free Ken Burns, so they
            # contribute no animation cost (mirrors web_app._video_model_for).
            if approved_ids is not None and f.get("frame_id") not in approved_ids:
                pass  # unapproved → free Ken Burns
            elif provider == "kenburns" and not (f.get("video_model_override") or g_vid):
                pass  # explicit Ken Burns, free
            elif vid_model:
                from agents.coverage import MAX_EXTRA, split_durations
                clip_dur = 5.0 if force_5s else dur
                # Multi-shot coverage: the beat is split into sub-shots, each
                # billed at its real duration. With extra_media already assigned
                # we know the count; with multi_shot requested but not yet
                # assigned, charge the UPPER BOUND (actual picks happen at
                # render time, so quote the worst case, never less).
                n_extra = len(f.get("extra_media") or [])
                if multi_shot and not n_extra and caption:
                    n_extra = MAX_EXTRA
                parts = split_durations(clip_dur, 1 + n_extra) if n_extra else [clip_dur]
                for part_dur in parts:
                    anim_count += 1;  anim_total += model_cost(vid_model, part_dur)
                used_models.add(vid_model)

    ls_audio_total = voice_cost(ls_audio_chars)
    music_total    = music_cost() if music_type == "generate" else 0.0
    voice_total    = voice_cost(voice_chars) if music_type == "voiceover" else 0.0

    total = round(
        scene_total + anim_total + image_total + edit_total
        + lipsync_total + ls_audio_total + music_total + voice_total, 4
    )

    anim_label = ", ".join(sorted(used_models)) if used_models else "auto"

    return {
        "scene":      {"count": scene_count,   "usd": round(scene_total, 4)},
        "animation":  {"count": anim_count,    "usd": round(anim_total, 4), "provider": anim_label},
        "images":     {"count": image_count,   "usd": round(image_total, 4)},
        "edits":      {"count": edit_count,    "usd": round(edit_total, 4)},
        "lipsync":    {"count": lipsync_count, "usd": round(lipsync_total, 4)},
        "lipsync_audio": {"usd": round(ls_audio_total, 4)},
        "music":      {"usd": round(music_total, 4)},
        "voice":      {"usd": round(voice_total, 4)},
        "total":      total,
        # Back-compat alias so old callers reading ["kling"] still work
        "kling":      {"count": anim_count,    "usd": round(anim_total, 4)},
    }
