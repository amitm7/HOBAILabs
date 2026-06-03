import subprocess
import os
from pathlib import Path

TRANSITION_DUR = 0.4  # seconds crossfade between clips

# macOS system font directory containing Baskerville.ttc
FONT_DIR = "/System/Library/Fonts/Supplemental"


def _run(cmd: list[str]):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg error:\n{result.stderr[-2000:]}")


def _concat_clips_hard(clips: list[dict], output_path: str):
    """Hard cut: simple stream-copy concat."""
    import tempfile
    list_file = tempfile.mktemp(suffix="_list.txt")
    with open(list_file, "w") as f:
        for c in clips:
            f.write(f"file '{os.path.abspath(c['clip_path'])}'\n")
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", output_path])
    os.remove(list_file)


def _build_video_with_transitions(clips: list[dict], output_path: str):
    """Concat clips with xfade crossfade transitions."""
    n = len(clips)
    if n == 1:
        _run(["ffmpeg", "-y", "-i", clips[0]["clip_path"], "-c", "copy", output_path])
        return

    inputs = []
    for c in clips:
        inputs += ["-i", os.path.abspath(c["clip_path"])]

    filter_parts = []
    offset = 0.0
    prev_label = "[0:v]"

    for i in range(1, n):
        offset += clips[i - 1]["actual_duration"] - TRANSITION_DUR
        next_label = f"[x{i}]" if i < n - 1 else "[vout]"
        filter_parts.append(
            f"{prev_label}[{i}:v]xfade=transition=fade:"
            f"duration={TRANSITION_DUR}:offset={offset:.3f}{next_label}"
        )
        prev_label = next_label

    _run([
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", ";".join(filter_parts),
        "-map", "[vout]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", "-preset", "fast",
        output_path,
    ])


def _concat_audio(clips: list[dict], output_path: str):
    """Concat all TTS audio segments into one track."""
    n = len(clips)
    audio_inputs = []
    for c in clips:
        audio_inputs += ["-i", os.path.abspath(c["audio_path"])]
    filter_chain = "".join(f"[{i}:a]" for i in range(n)) + f"concat=n={n}:v=0:a=1[aout]"
    _run([
        "ffmpeg", "-y",
        *audio_inputs,
        "-filter_complex", filter_chain,
        "-map", "[aout]",
        output_path,
    ])


def _subtitle_filter(sub_path: str) -> str:
    """Build ffmpeg subtitles filter string for an ASS file."""
    escaped = os.path.abspath(sub_path).replace("\\", "/").replace("'", r"\'").replace(":", r"\:")
    return f"subtitles='{escaped}':fontsdir='{FONT_DIR}'"


def assemble_caption_only(clips: list[dict], temp_dir: str, output_path: str,
                          music_path: str = None, srt_path: str = None,
                          transition: str = "crossfade"):
    """
    Caption-only assembly: visuals + captions + optional music (no voiceover).
    Accepts either .srt or .ass path; .ass is used when present.
    """
    temp = Path(temp_dir)

    # Prefer .ass over .srt
    sub_path = None
    if srt_path:
        ass_candidate = srt_path.replace(".srt", ".ass") if srt_path.endswith(".srt") else srt_path + ".ass"
        if os.path.exists(ass_candidate) and os.path.getsize(ass_candidate) > 0:
            sub_path = ass_candidate
        elif os.path.exists(srt_path) and os.path.getsize(srt_path) > 0:
            sub_path = srt_path

    # Step 1: Video — crossfade or hard cut
    raw_video = str(temp / "raw_video.mp4")
    if transition == "none":
        print("[Assembler] Building hard-cut concat...")
        _concat_clips_hard(clips, raw_video)
    else:
        print("[Assembler] Building crossfade transitions...")
        _build_video_with_transitions(clips, raw_video)

    # Step 2: Final merge — video + captions + optional music
    print("[Assembler] Final merge...")

    vf_str = _subtitle_filter(sub_path) if sub_path else "null"

    if music_path and os.path.exists(music_path):
        total_dur = sum(c["actual_duration"] for c in clips)
        cmd = [
            "ffmpeg", "-y",
            "-i", raw_video,
            "-stream_loop", "-1", "-i", music_path,
            "-filter_complex",
            f"[0:v]{vf_str}[vout];"
            f"[1:a]volume=0.25,afade=t=out:st={max(0,total_dur-3):.1f}:d=3[aout]",
            "-map", "[vout]",
            "-map", "[aout]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k", "-shortest",
            output_path,
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-i", raw_video,
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-filter_complex", f"[0:v]{vf_str}[vout]",
            "-map", "[vout]",
            "-map", "1:a",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "128k", "-shortest",
            output_path,
        ]

    _run(cmd)
    print(f"[Assembler] Output → {output_path}")


def assemble(clips: list[dict], temp_dir: str, output_path: str,
             music_path: str = None, srt_path: str = None):
    """Full assembly with voiceover: transitions + audio + captions + optional music."""
    temp = Path(temp_dir)

    raw_video = str(temp / "raw_video.mp4")
    print("[Assembler] Building transitions...")
    _build_video_with_transitions(clips, raw_video)

    raw_audio = str(temp / "raw_audio.mp3")
    print("[Assembler] Concatenating audio...")
    _concat_audio(clips, raw_audio)

    print("[Assembler] Final merge...")

    sub_path = None
    if srt_path:
        ass_candidate = srt_path.replace(".srt", ".ass") if srt_path.endswith(".srt") else srt_path + ".ass"
        if os.path.exists(ass_candidate) and os.path.getsize(ass_candidate) > 0:
            sub_path = ass_candidate
        elif os.path.exists(srt_path) and os.path.getsize(srt_path) > 0:
            sub_path = srt_path

    vf_str = _subtitle_filter(sub_path) if sub_path else None

    if music_path and os.path.exists(music_path):
        cmd = [
            "ffmpeg", "-y",
            "-i", raw_video,
            "-i", raw_audio,
            "-i", music_path,
            "-filter_complex",
            "[1:a][2:a]amix=inputs=2:weights=1 0.15:duration=first[aout]",
            "-map", "0:v",
            "-map", "[aout]",
        ]
        if vf_str:
            cmd += ["-vf", vf_str]
        else:
            cmd += ["-c:v", "copy"]
        cmd += ["-c:a", "aac", "-b:a", "192k", "-shortest", output_path]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-i", raw_video,
            "-i", raw_audio,
            "-map", "0:v",
            "-map", "1:a",
        ]
        if vf_str:
            cmd += ["-vf", vf_str, "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-preset", "fast", "-crf", "18"]
        else:
            cmd += ["-c:v", "copy"]
        cmd += ["-c:a", "aac", "-b:a", "192k", "-shortest", output_path]

    _run(cmd)
    print(f"[Assembler] Output → {output_path}")
