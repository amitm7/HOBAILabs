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
    parser.add_argument("--keep-temp", action="store_true")
    args = parser.parse_args()

    max_frame_dur = 5.0 if args.dev else 9.0
    if args.dev:
        print("[Pipeline] DEV mode: frame durations capped at 5s → 6 Kling credits each (full quality)")

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    temp_dir = tempfile.mkdtemp(prefix="hob_caption_")
    print(f"[Pipeline] Temp dir: {temp_dir}")

    try:
        # 1. Parse script frames
        frames = parse_frame_script(args.script, args.assets, max_frame_dur=max_frame_dur)

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

        # 3. Three-tier visual assignment
        for f in frames:
            photo_spec = f.get("photo_spec", "")

            if photo_spec and not photo_spec.startswith("ai_"):
                # Tier 1: explicit real photo
                candidate = os.path.join(args.assets, photo_spec)
                if os.path.exists(candidate):
                    f["visual_path"] = candidate
                else:
                    print(f"[Pipeline] {f['frame_id']}: {photo_spec} not found → AI portrait fallback")
                    f["visual_path"] = generate_contextual_image(f, args.assets)

            elif photo_spec == "ai_portrait":
                # Tier 2: AI contextual portrait (age/era-accurate)
                f["visual_path"] = generate_contextual_image(f, args.assets)

            elif photo_spec == "ai_symbolic":
                # Tier 3: AI symbolic/metaphorical (objects only, no people)
                f["visual_path"] = generate_symbolic_image(f, args.assets)

            elif not f["visual_path"] or not os.path.exists(f["visual_path"]):
                # No annotation, no sort-order match → AI contextual fallback
                f["visual_path"] = generate_contextual_image(f, args.assets)

        # 4. Build clip assignments (include motion prompt from scene intelligence)
        assignments = []
        for f in frames:
            assignments.append({
                "segment_id":   f["frame_id"],
                "actual_duration": f["duration"],
                "media_path":   f["visual_path"],
                "text":         f.get("caption", ""),
                "motion_prompt": f.get("scene", {}).get("motion_prompt", ""),
            })

        # 5. Build clips — Kling animates each image with emotion-specific motion
        clips = build_clips(assignments, temp_dir, args.width, args.height, args.fps)

        # 6. Generate ASS captions from frame durations
        srt_path = os.path.join(temp_dir, "captions.srt")
        ass_path = generate_frame_srt(frames, srt_path)  # returns .ass path

        # 7. Assemble
        assemble_caption_only(
            clips, temp_dir, args.output,
            music_path=args.music,
            srt_path=ass_path,  # pass .ass directly
        )

        total = sum(f["duration"] for f in frames)
        print(f"\n[Pipeline] Done! {total:.1f}s → {args.output}")

    finally:
        if not args.keep_temp:
            shutil.rmtree(temp_dir, ignore_errors=True)
        else:
            print(f"[Pipeline] Temp kept: {temp_dir}")


if __name__ == "__main__":
    main()
