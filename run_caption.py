"""
Caption-only storytelling pipeline.
- Scene Intelligence (GPT-4o) designs cinematic visuals + emotion-specific motion for each frame
- AI Image Generation (gpt-image-1) creates missing visuals
- Kling AI animates images into moving video; Ken Burns fallback
- Captions overlaid on screen; optional background music

Usage:
  python run_caption.py --script surabhi_story.txt --assets surabhi_assets/ \
                        --subject "Surabhi" --music path/to/song.mp3 \
                        --output output/surabhi.mp4
"""
import argparse
import os
import shutil
import tempfile
from dotenv import load_dotenv

from agents.script_parser import parse_frame_script
from agents.scene_intelligence import design_all_scenes
from agents.clip_builder import build_clips
from agents.caption_writer import generate_frame_srt
from agents.assembler import assemble_caption_only
from agents.image_generator import generate_contextual_image, generate_symbolic_image
from agents.safety import moderate_script, check_face_sanity
from agents.pricing import estimate as estimate_cost, load as load_pricing


def _generate_with_sanity_check(generate_fn, frame: dict, assets_dir: str,
                                max_retries: int = 2, model_id: str = "") -> str:
    """Run generate_fn; retry up to max_retries times if Gate B sanity check fails."""
    path = None
    for attempt in range(max_retries + 1):
        path = generate_fn(frame, assets_dir, model_id=model_id)
        if check_face_sanity(path, frame["frame_id"]):
            return path
        if attempt < max_retries:
            print(f"[Safety] Gate B: {frame['frame_id']} — regenerating (attempt {attempt + 2}/{max_retries + 1})")
            try:
                os.remove(path)
            except Exception:
                pass
    print(f"[Safety] Gate B: {frame['frame_id']} — using last result after {max_retries + 1} attempts.")
    return path


def _apply_predefined_scenes(frames: list[dict], scenes_module) -> list[dict]:
    """Apply hand-crafted scene designs when API quota is unavailable."""
    scene_map = getattr(scenes_module, "SURABHI_SCENES", {})
    for f in frames:
        fid = f["frame_id"]
        if fid in scene_map:
            f["scene"] = scene_map[fid]
    return frames

load_dotenv(override=True)




def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--script",   required=True)
    parser.add_argument("--assets",   required=True)
    parser.add_argument("--subject",  default="the subject")
    parser.add_argument("--music",    default=None)
    parser.add_argument("--output",   default="output/caption_video.mp4")
    parser.add_argument("--width",    type=int, default=1080)
    parser.add_argument("--height",   type=int, default=1920)
    parser.add_argument("--fps",      type=int, default=30)
    parser.add_argument("--skip-scene-ai", action="store_true",
                        help="Skip scene intelligence (use generic motion prompts)")
    parser.add_argument("--dev", action="store_true",
                        help="Dev mode: cap frames at 5s so all clips use 5s Kling tier (6 credits each, "
                             "no quality loss — just tighter pacing). Half the credit cost.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse script and show cost estimate without rendering anything")
    parser.add_argument("--face-lock", action="store_true",
                        help="V1 face consistency: generate first ai_portrait once, reuse same still "
                             "for all subsequent ai_portrait frames (different motion per frame)")
    parser.add_argument("--provider", default="kling",
                        choices=["kling", "higgsfield", "kenburns"],
                        help="Legacy global animation provider (used as router fallback)")
    parser.add_argument("--smart-match", action="store_true",
                        help="Use GPT-4o to content-match folder images to frames (reads names/text "
                             "in photos); only fills unpinned frames, falls back to sort-order")
    parser.add_argument("--image-model", default="auto",
                        help="Image model id from config/models.json, or 'auto' to route per shot")
    parser.add_argument("--video-model", default="auto",
                        help="Video model id from config/models.json, or 'auto' to route per shot")
    parser.add_argument("--lipsync", action="store_true",
                        help="Apply lip sync to all video-source frames using SyncLabs; "
                             "image frames use Hedra talking portrait (requires SYNCLABS_API_KEY / HEDRA_API_KEY)")
    parser.add_argument("--voice-id", default="",
                        help="ElevenLabs voice ID for lip sync audio (falls back to ELEVENLABS_VOICE_ID env var)")
    parser.add_argument("--keep-temp", action="store_true")
    args = parser.parse_args()

    max_frame_dur = 5.0 if args.dev else 9.0
    if args.dev:
        print("[Pipeline] DEV mode: frame durations capped at 5s → 6 Kling credits each (full quality)")

    # Gate A: Moderate script text before spending any credits
    with open(args.script, "r") as _sf:
        moderate_script(_sf.read())

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    temp_dir = tempfile.mkdtemp(prefix="hob_caption_")
    print(f"[Pipeline] Temp dir: {temp_dir}")

    pipeline_success = False
    try:
        # 1. Parse script frames
        frames = parse_frame_script(args.script, args.assets, max_frame_dur=max_frame_dur,
                                    smart_match=args.smart_match)

        # 2. Scene Intelligence — emotion-specific cinematic design per frame
        if not args.skip_scene_ai:
            try:
                frames = design_all_scenes(frames, subject_name=args.subject)
            except Exception:
                print("[Pipeline] GPT-4o unavailable — using pre-defined scenes")

        # Apply pre-defined scenes for any frame still missing scene data
        try:
            import surabhi_scenes
            frames = _apply_predefined_scenes(frames, surabhi_scenes)
        except ImportError:
            pass

        # 1b. Dry-run: show plan and cost estimate, then exit
        if args.dry_run:
            use_kling = bool(os.environ.get("KLING_ACCESS_KEY"))
            pricing   = load_pricing()
            _VIDEO_EXTS = {".mp4", ".mov", ".avi", ".m4v", ".webm"}
            first_portrait_counted = False
            print("\n[Dry Run] ── Frame Plan ──────────────────────────────────────────")
            dr_frames = []
            for f in frames:
                spec  = f.get("photo_spec", "")
                vpath = f.get("visual_path", "")
                dur   = f["duration"]
                is_video = vpath and os.path.splitext(vpath)[1].lower() in _VIDEO_EXTS
                # --lipsync auto-flags video frames; [lipsync: yes] flags any frame
                lipsync = bool(f.get("lipsync")) or (args.lipsync and is_video)
                parts = []

                # Image source
                if spec == "ai_portrait":
                    if args.face_lock and first_portrait_counted:
                        parts.append("FACE-LOCKED (free reuse)")
                    else:
                        c = pricing["image_gen"]["flux_portrait_usd"]
                        parts.append(f"Flux gen (~${c:.2f})")
                        first_portrait_counted = True
                elif spec == "ai_symbolic":
                    c = pricing["image_gen"]["openai_gpt_image_usd"]
                    parts.append(f"GPT gen (~${c:.2f})")
                elif spec:
                    parts.append(f"real photo [{spec}]")
                elif is_video:
                    parts.append(f"real video [{os.path.basename(vpath)}]")
                elif vpath:
                    parts.append(f"real photo [{os.path.basename(vpath)}]")
                else:
                    c = pricing["image_gen"]["flux_portrait_usd"]
                    parts.append(f"Flux gen (~${c:.2f})")

                # Edit pass
                if f.get("edit_prompt"):
                    c = pricing["image_gen"]["openai_edit_usd"]
                    parts.append(f"+ edit (~${c:.2f})")

                # Lipsync replaces animation; else Kling / Ken Burns
                if lipsync:
                    if is_video:
                        c = pricing.get("synclabs", {}).get("per_second_usd", 0.012) * max(1, dur)
                        parts.append(f"🎙 SyncLabs (~${c:.2f})")
                    else:
                        c = pricing.get("hedra", {}).get("per_generation_usd", 0.10)
                        parts.append(f"🎙 Hedra (~${c:.2f})")
                elif not is_video:
                    if args.provider == "kenburns" or not use_kling:
                        parts.append("Ken Burns ($0)")
                    elif args.provider == "higgsfield":
                        c = pricing.get("higgsfield", {}).get("generation_5s_usd", 0.10)
                        parts.append(f"Higgsfield (~${c:.2f})")
                    else:
                        c = pricing["kling"]["standard_5s_usd"]
                        parts.append(f"Kling (~${c:.2f})")

                dr_frames.append({**f, "visual_path": vpath, "lipsync": lipsync,
                                  "edit_prompt": f.get("edit_prompt","")})
                print(f"[Dry Run]  {f['frame_id']}  {dur:.1f}s  {'  '.join(parts)}")

            b = estimate_cost(dr_frames, kling_mode="standard",
                              force_5s=args.dev, music_type="none",
                              provider=args.provider, skip_scene_ai=args.skip_scene_ai,
                              image_model=args.image_model, video_model=args.video_model)
            print(f"[Dry Run] ────────────────────────────────────────────────────────")
            if b["scene"]["count"]:
                print(f"[Dry Run]  {b['scene']['count']:>2} × Scene AI (GPT-4.1)   ${b['scene']['usd']:.2f}  (cached after 1st run → $0)")
            if b["images"]["count"]:
                print(f"[Dry Run]  {b['images']['count']:>2} × Image gen (fal/GPT)  ${b['images']['usd']:.2f}")
            if b["edits"]["count"]:
                print(f"[Dry Run]  {b['edits']['count']:>2} × Image edits          ${b['edits']['usd']:.2f}")
            if b["animation"]["count"]:
                print(f"[Dry Run]  {b['animation']['count']:>2} × {b['animation']['provider'].title()} clips     ${b['animation']['usd']:.2f}")
            if b.get("lipsync", {}).get("count"):
                print(f"[Dry Run]  {b['lipsync']['count']:>2} × Lip sync (Hedra/Sync) ${b['lipsync']['usd']:.2f}")
                print(f"[Dry Run]     + lip-sync voice (11Labs)  ${b['lipsync_audio']['usd']:.2f}")
            print(f"[Dry Run]  ─────────────────────────────────────────────────────")
            print(f"[Dry Run]  Estimated total: ~${b['total']:.2f} USD  (excludes music/voiceover — added at render)")
            print(f"[Dry Run]  Prices from config/pricing.json — update file when vendor rates change.")
            print(f"[Dry Run]  Remove --dry-run to execute.")
            pipeline_success = True
            return

        # 3. Three-tier visual assignment (with Gate B sanity check + face-lock)
        from agents import model_router
        cost_tier = model_router.cost_tier_from_quality("dev" if args.dev else "production")
        for f in frames:
            f["image_model_override"] = f.get("image_model_override") or (
                args.image_model if args.image_model != "auto" else "")
            f["video_model_override"] = f.get("video_model_override") or (
                args.video_model if args.video_model != "auto" else "")

        first_portrait_path = None
        for f in frames:
            photo_spec = f.get("photo_spec", "")
            img_model = model_router.select_model(
                "image", f, cost_tier, override=f.get("image_model_override", ""))
            mid = "" if img_model == model_router.PASSTHROUGH else img_model

            if photo_spec and not photo_spec.startswith("ai_"):
                # Tier 1: explicit real photo
                candidate = os.path.join(args.assets, photo_spec)
                if os.path.exists(candidate):
                    f["visual_path"] = candidate
                    if first_portrait_path is None:
                        first_portrait_path = candidate
                else:
                    print(f"[Pipeline] {f['frame_id']}: {photo_spec} not found → AI portrait fallback")
                    path = _generate_with_sanity_check(generate_contextual_image, f, args.assets, model_id=mid)
                    f["visual_path"] = path
                    if first_portrait_path is None:
                        first_portrait_path = path

            elif photo_spec == "ai_portrait":
                # Tier 2: AI contextual portrait (age/era-accurate)
                if args.face_lock and first_portrait_path is not None:
                    print(f"[FaceConsistency] {f['frame_id']}: reusing locked portrait — same face, different motion.")
                    f["visual_path"] = first_portrait_path
                else:
                    path = _generate_with_sanity_check(generate_contextual_image, f, args.assets, model_id=mid)
                    f["visual_path"] = path
                    if first_portrait_path is None:
                        first_portrait_path = path

            elif photo_spec == "ai_symbolic":
                # Tier 3: AI symbolic/metaphorical (objects only, no people)
                f["visual_path"] = _generate_with_sanity_check(generate_symbolic_image, f, args.assets, model_id=mid)

            elif not f["visual_path"] or not os.path.exists(f["visual_path"]):
                # No annotation, no sort-order match → AI contextual fallback
                path = _generate_with_sanity_check(generate_contextual_image, f, args.assets, model_id=mid)
                f["visual_path"] = path
                if first_portrait_path is None:
                    first_portrait_path = path

        # 3b. Image edit pass — applied between generation and Kling
        for f in frames:
            if f.get("edit_prompt") and f.get("visual_path") and os.path.exists(f["visual_path"]):
                from agents.image_editor import edit_image
                src = f["visual_path"]
                # Write edited copy into the temp dir, never next to the source
                # photo (avoids polluting the user's asset folder).
                base = os.path.splitext(os.path.basename(src))[0]
                edited = os.path.join(temp_dir, f"{base}_edited.jpg")
                try:
                    f["visual_path"] = edit_image(src, f["edit_prompt"], edited)
                except Exception as e:
                    print(f"[Pipeline] Image edit failed for {f['frame_id']} ({e}) — using original")

        # 3c. Lip sync pass — audio-driven duration + talking portrait / video sync
        _VIDEO_EXTS_LS = {".mp4", ".mov", ".avi", ".m4v", ".webm"}
        if args.lipsync:
            # --lipsync flag: auto-mark all video-source frames for lipsync
            for f in frames:
                vp = f.get("visual_path", "")
                if vp and os.path.splitext(vp)[1].lower() in _VIDEO_EXTS_LS:
                    f["lipsync"] = True

        has_lipsync = any(f.get("lipsync") for f in frames)
        if has_lipsync:
            from agents.lipsync_coordinator import run_lipsync_pass
            voice_id = args.voice_id or os.environ.get("ELEVENLABS_VOICE_ID", "")
            frames = run_lipsync_pass(frames, temp_dir, default_voice_id=voice_id)

        # 4. Build clip assignments (include motion prompt from scene intelligence)
        assignments = []
        for f in frames:
            # [motion:] / [camera:] override takes priority over GPT-designed prompt
            motion = (
                f.get("motion_override")
                or f.get("scene", {}).get("motion_prompt", "")
            )
            assignments.append({
                "segment_id":        f["frame_id"],
                "actual_duration":   f["duration"],
                "media_path":        f["visual_path"],
                "text":              f.get("caption", ""),
                "motion_prompt":     motion,
                "video_start_sec":   f.get("video_start_sec", 0.0),
                # Lipsync fields — clip_ready frames bypass Kling/Higgsfield
                "clip_ready":        bool(f.get("lipsync_clip_path")),
                "lipsync_clip_path": f.get("lipsync_clip_path", ""),
                "has_lipsync_audio": bool(f.get("lipsync_clip_path")),
                # Router picks the video model per shot (cost-tier aware).
                "model_id":          model_router.select_model(
                    "video", f, cost_tier, override=f.get("video_model_override", "")),
            })

        # 5. Build clips — AI provider animates each image with emotion-specific motion
        clips = build_clips(assignments, temp_dir, args.width, args.height, args.fps,
                            provider=args.provider)

        # 6. Generate ASS captions from frame durations
        srt_path = os.path.join(temp_dir, "captions.srt")
        ass_path = generate_frame_srt(frames, srt_path)  # returns .ass path

        # 7. Assemble
        assemble_caption_only(
            clips, temp_dir, args.output,
            music_path=args.music,
            srt_path=ass_path,  # pass .ass directly
        )

        pipeline_success = True
        total = sum(f["duration"] for f in frames)
        print(f"\n[Pipeline] Done! {total:.1f}s → {args.output}")

    finally:
        if pipeline_success and not args.keep_temp:
            shutil.rmtree(temp_dir, ignore_errors=True)
        elif not pipeline_success:
            print(f"[Pipeline] Pipeline failed — temp dir preserved for debugging: {temp_dir}")
        else:
            print(f"[Pipeline] Temp kept: {temp_dir}")


if __name__ == "__main__":
    main()
