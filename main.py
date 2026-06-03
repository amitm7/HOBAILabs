#!/usr/bin/env python3
import argparse
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(override=True)

from agents.segmenter import segment_script
from agents.tts_generator import generate_tts
from agents.matcher import load_media_files, match_media
from agents.clip_builder import build_clips
from agents.caption_generator import generate_srt
from agents.assembler import assemble


def parse_args():
    parser = argparse.ArgumentParser(description="AI Video Generator — end-to-end pipeline")
    parser.add_argument("--script", required=True, help="Path to script .txt file")
    parser.add_argument("--assets", default="assets", help="Folder containing images/videos")
    parser.add_argument("--duration", type=int, required=True, help="Target video duration in seconds")
    parser.add_argument("--width", type=int, default=1080, help="Output width (default: 1080)")
    parser.add_argument("--height", type=int, default=1920, help="Output height — 1920 for 9:16, 1080 for 16:9 square")
    parser.add_argument("--fps", type=int, default=30, help="Frames per second (default: 30)")
    parser.add_argument("--voice", default="nova", help="OpenAI TTS voice: alloy|echo|fable|onyx|nova|shimmer")
    parser.add_argument("--music", default=None, help="Optional path to background music file")
    parser.add_argument("--output", default=None, help="Output file path (default: output/video_TIMESTAMP.mp4)")
    parser.add_argument("--keep-temp", action="store_true", help="Keep temp files for debugging")
    return parser.parse_args()


def main():
    args = parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY not set. Copy .env.example to .env and add your key.")
        sys.exit(1)

    script_path = Path(args.script)
    if not script_path.exists():
        print(f"ERROR: Script file not found: {args.script}")
        sys.exit(1)

    script = script_path.read_text().strip()
    if not script:
        print("ERROR: Script file is empty.")
        sys.exit(1)

    assets_dir = args.assets
    if not os.path.isdir(assets_dir):
        print(f"ERROR: Assets folder not found: {assets_dir}")
        sys.exit(1)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = args.output or f"output/video_{timestamp}.mp4"
    temp_dir = f"temp/run_{timestamp}"

    os.makedirs("output", exist_ok=True)
    os.makedirs(temp_dir, exist_ok=True)

    print(f"\n{'='*50}")
    print(f"  AI Video Generator")
    print(f"  Script : {args.script}")
    print(f"  Assets : {assets_dir}")
    print(f"  Output : {output_path}")
    print(f"  Size   : {args.width}x{args.height} @ {args.fps}fps")
    print(f"  Target : {args.duration}s")
    print(f"{'='*50}\n")

    try:
        # Agent 1: Segment script
        print(">> Agent 1: Segmenting script...")
        segments = segment_script(script, args.duration)

        # Agent 2: Generate TTS audio
        print("\n>> Agent 2: Generating voiceover...")
        audio_segments = generate_tts(segments, temp_dir, voice=args.voice)

        # Agent 3: Match media to segments
        print("\n>> Agent 3: Matching media...")
        media_files = load_media_files(assets_dir)
        assignments = match_media(audio_segments, media_files)

        # Agent 4: Build video clips
        print("\n>> Agent 4: Building clips...")
        clips = build_clips(assignments, temp_dir, args.width, args.height, args.fps)

        # Agent 5: Generate captions
        print("\n>> Agent 5: Generating captions...")
        srt_path = os.path.join(temp_dir, "captions.srt")
        generate_srt(audio_segments, srt_path)

        # Agent 6: Assemble final video
        print("\n>> Agent 6: Assembling final video...")
        assemble(clips, temp_dir, output_path, music_path=args.music, srt_path=srt_path)

        total = sum(c["actual_duration"] for c in clips)
        print(f"\n{'='*50}")
        print(f"  DONE")
        print(f"  Duration : {total:.1f}s")
        print(f"  Output   : {output_path}")
        print(f"{'='*50}\n")

    except Exception as e:
        print(f"\nERROR: {e}")
        sys.exit(1)
    finally:
        if not args.keep_temp and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)


if __name__ == "__main__":
    main()
