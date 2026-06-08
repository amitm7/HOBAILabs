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
    """Hard cut: normalize all clips to one resolution/format, then stream-copy concat.
    Concat demuxer with -c copy requires identical codec params across inputs;
    mixed resolutions (e.g. Higgsfield 768x1168 vs 1080x1920) otherwise fail."""
    import tempfile
    # Target = largest-area clip (the correctly-sized pipeline clips).
    target_w, target_h = 1080, 1920
    best_area = 0
    for c in clips:
        wh = _probe_wh(os.path.abspath(c["clip_path"]))
        if wh and wh[0] * wh[1] > best_area:
            best_area = wh[0] * wh[1]
            target_w, target_h = wh

    norm_dir = os.path.dirname(os.path.abspath(clips[0]["clip_path"]))
    norm_paths = []
    for i, c in enumerate(clips):
        npath = os.path.join(norm_dir, f"_hcnorm_{i:02d}.mp4")
        _normalize_clip(os.path.abspath(c["clip_path"]), npath, target_w, target_h)
        norm_paths.append(npath)

    list_file = tempfile.mktemp(suffix="_list.txt")
    with open(list_file, "w") as f:
        for p in norm_paths:
            f.write(f"file '{p}'\n")
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", output_path])
    os.remove(list_file)
    for p in norm_paths:
        try:
            os.remove(p)
        except Exception:
            pass


def _probe_wh(path: str):
    """Return (width, height) of a video, or None."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", path],
        capture_output=True, text=True,
    )
    try:
        w, h = r.stdout.strip().split("x")
        return int(w), int(h)
    except Exception:
        return None


def _normalize_clip(src: str, dst: str, width: int, height: int):
    """
    Re-encode a clip to a uniform RESOLUTION, pixel format, color range, fps, SAR.

    Two problems this prevents in xfade/concat:
      1. Color RANGE mismatch (video=tv, Ken-Burns JPEG=pc) → graph reconfigures
         mid-stream and silently drops frames, collapsing the timeline.
      2. RESOLUTION mismatch (e.g. Higgsfield returns 768x1168 while video/Ken-Burns
         are 1080x1920) → "input link parameters do not match" → assembly crashes,
         producing an empty output file.

    We scale-to-fill + center-crop to the exact target (no black bars), matching how
    _kenburns / _video_trim already frame their clips.
    """
    vf = (f"scale={width}:{height}:force_original_aspect_ratio=increase:"
          f"in_range=full:out_range=tv,crop={width}:{height},"
          f"format=yuv420p,fps=30,setsar=1")
    _run([
        "ffmpeg", "-y", "-i", src,
        "-vf", vf,
        "-color_range", "tv",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "18",
        "-an", dst,
    ])


def _build_video_with_transitions(clips: list[dict], output_path: str):
    """Concat clips with xfade crossfade transitions."""
    n = len(clips)
    if n == 1:
        _run(["ffmpeg", "-y", "-i", clips[0]["clip_path"], "-c", "copy", output_path])
        return

    # Determine the target resolution = the largest-area clip (the correctly-sized
    # pipeline clips; AI clips like Higgsfield's 768x1168 get scaled up to match).
    target_w, target_h = 1080, 1920
    best_area = 0
    for c in clips:
        wh = _probe_wh(os.path.abspath(c["clip_path"]))
        if wh and wh[0] * wh[1] > best_area:
            best_area = wh[0] * wh[1]
            target_w, target_h = wh

    # Step 1: normalize every clip to identical resolution + params.
    norm_dir = os.path.dirname(os.path.abspath(clips[0]["clip_path"]))
    norm_paths = []
    for i, c in enumerate(clips):
        npath = os.path.join(norm_dir, f"_norm_{i:02d}.mp4")
        _normalize_clip(os.path.abspath(c["clip_path"]), npath, target_w, target_h)
        norm_paths.append(npath)

    inputs = []
    for p in norm_paths:
        inputs += ["-i", p]

    # Step 2: chain xfades on the normalized clips.
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

    for p in norm_paths:
        try:
            os.remove(p)
        except Exception:
            pass


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


def _strip_audio(clip_path: str, out_path: str) -> str:
    """Strip audio stream from a clip (for video-only concat when lipsync audio is handled separately)."""
    _run(["ffmpeg", "-y", "-i", clip_path, "-c:v", "copy", "-an", out_path])
    return out_path


def _assemble_with_lipsync(clips: list[dict], temp: Path, output_path: str,
                            music_path: str | None, sub_path: str | None,
                            transition: str):
    """
    Assembly when some clips carry embedded lipsync audio.

    Strategy:
    - Strip audio from lipsync clips for the video concat step
    - Extract lipsync audio per clip, position it with adelay
    - Mix: lipsync audio at 100%, music ducked to 10% during lipsync windows
    - Burn captions on top
    """
    lipsync_clips = [(i, c) for i, c in enumerate(clips) if c.get("has_lipsync_audio")]
    print(f"[Assembler] Mixed mode: {len(lipsync_clips)} lipsync clip(s) with embedded audio")

    # ── Prepare clips: strip audio from lipsync clips for clean video concat ─
    prepped = []
    for c in clips:
        if c.get("has_lipsync_audio"):
            stripped = str(temp / f"noaudio_{c['segment_id']}.mp4")
            _strip_audio(c["clip_path"], stripped)
            prepped.append({**c, "clip_path": stripped})
        else:
            prepped.append(c)

    # ── Build video track (same as normal) ────────────────────────────────────
    raw_video = str(temp / "raw_video.mp4")
    if transition == "none":
        _concat_clips_hard(prepped, raw_video)
    else:
        _build_video_with_transitions(prepped, raw_video)

    # ── Compute timecodes per clip ────────────────────────────────────────────
    timecodes: list[tuple[float, float]] = []
    t = 0.0
    for c in clips:
        dur = c["actual_duration"]
        timecodes.append((t, t + dur))
        t += dur
    total_dur = t

    # ── Extract lipsync audio files with positional delay ─────────────────────
    ls_audio_files: list[tuple[str, float]] = []  # (path, start_time)
    for i, c in enumerate(clips):
        if not c.get("has_lipsync_audio"):
            continue
        start_sec = timecodes[i][0]
        ls_path   = str(temp / f"ls_audio_{c['segment_id']}.aac")
        _run(["ffmpeg", "-y", "-i", c["clip_path"], "-vn", "-c:a", "aac", ls_path])
        ls_audio_files.append((ls_path, start_sec))

    # ── Build final ffmpeg command ─────────────────────────────────────────────
    # Inputs: raw_video [0], lipsync audio files [1..N], music [N+1] (optional)
    cmd = ["ffmpeg", "-y", "-i", raw_video]
    for ls_path, _ in ls_audio_files:
        cmd += ["-i", ls_path]

    music_idx = None
    if music_path and os.path.exists(music_path):
        cmd += ["-stream_loop", "-1", "-i", music_path]
        music_idx = 1 + len(ls_audio_files)

    # ── filter_complex ────────────────────────────────────────────────────────
    filters: list[str] = []

    # Position each lipsync audio stream at its start time using adelay
    ls_labels: list[str] = []
    for idx, (_, start_sec) in enumerate(ls_audio_files):
        stream_idx = idx + 1
        delay_ms   = int(start_sec * 1000)
        label      = f"[ls{idx}]"
        filters.append(f"[{stream_idx}:a]adelay={delay_ms}|{delay_ms}{label}")
        ls_labels.append(label)

    # Mix all lipsync streams into one
    if len(ls_labels) == 1:
        filters.append(f"{ls_labels[0]}anull[lsout]")
    else:
        mix_inputs = "".join(ls_labels)
        filters.append(f"{mix_inputs}amix=inputs={len(ls_labels)}:normalize=0[lsout]")

    # Music with dynamic ducking: 10% during lipsync windows, 25% elsewhere
    if music_idx is not None:
        ls_windows = [timecodes[i] for i, c in enumerate(clips) if c.get("has_lipsync_audio")]
        if ls_windows:
            between_expr = "+".join(
                f"between(t,{s:.3f},{e:.3f})" for s, e in ls_windows
            )
            duck_expr = f"if(gt({between_expr},0),0.10,0.25)"
        else:
            duck_expr = "0.25"

        fade_start = max(0, total_dur - 3)
        filters.append(
            f"[{music_idx}:a]volume=expr='{duck_expr}',"
            f"afade=t=out:st={fade_start:.1f}:d=3[mout]"
        )
        filters.append("[lsout][mout]amix=inputs=2:weights=1 1:normalize=0[aout]")
    else:
        # No music — lipsync audio is the only audio
        filters.append("[lsout]anull[aout]")

    # Video with captions
    vf_str = _subtitle_filter(sub_path) if sub_path else "null"
    filters.append(f"[0:v]{vf_str}[vout]")

    cmd += ["-filter_complex", ";".join(filters)]
    cmd += ["-map", "[vout]", "-map", "[aout]"]
    cmd += [
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k", "-shortest",
        output_path,
    ]

    print("[Assembler] Lipsync mixed assembly…")
    _run(cmd)
    print(f"[Assembler] Output → {output_path}")


def assemble_caption_only(clips: list[dict], temp_dir: str, output_path: str,
                          music_path: str = None, srt_path: str = None,
                          transition: str = "crossfade"):
    """
    Caption-only assembly: visuals + captions + optional music (no voiceover).
    Accepts either .srt or .ass path; .ass is used when present.
    Automatically routes to lipsync-aware assembly when any clip has embedded audio.
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

    # Route to lipsync-aware assembly if any clip carries embedded audio
    if any(c.get("has_lipsync_audio") for c in clips):
        _assemble_with_lipsync(clips, temp, output_path, music_path, sub_path, transition)
        return

    # ── Standard assembly (no lipsync) ────────────────────────────────────────
    raw_video = str(temp / "raw_video.mp4")
    if transition == "none":
        print("[Assembler] Building hard-cut concat...")
        _concat_clips_hard(clips, raw_video)
    else:
        print("[Assembler] Building crossfade transitions...")
        _build_video_with_transitions(clips, raw_video)

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
