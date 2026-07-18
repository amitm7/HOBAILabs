import subprocess
import os
from pathlib import Path

TRANSITION_DUR = 0.4  # seconds crossfade between clips

# Directory libass searches for the caption font. Defaults to the macOS system
# fonts (Baskerville.ttc) for local dev; override with HOB_FONT_DIR in the Docker
# image / Linux deploy where that path doesn't exist (see Dockerfile).
FONT_DIR = os.environ.get("HOB_FONT_DIR", "/System/Library/Fonts/Supplemental")


def _run(cmd: list[str]):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg error:\n{result.stderr[-2000:]}")


def effective_timecodes(durations: list[float],
                        transition: str = "crossfade",
                        overlaps: list[float] | None = None) -> list[tuple[float, float]]:
    """
    (start, end) of each clip in the RENDERED video.

    Crossfade overlaps consecutive clips at every junction (see
    _build_video_with_transitions: offset += dur - overlap), so each clip after
    the first starts that much earlier than the raw cumulative sum. Every consumer
    of timeline positions (lipsync adelay, ducking windows, captions, voiceover)
    must use these, not raw cumulative durations.

    `overlaps` (len = len(durations)-1) is the per-junction overlap in seconds —
    this is how beat-aware cutting (P1) threads the SAME overlaps used to build
    the video into the timecodes, so captions never desync from the cuts. When
    None, overlaps are uniform from `transition` (byte-identical to prior behaviour).
    """
    n = len(durations)
    if overlaps is None:
        ov = 0.0 if transition == "none" else TRANSITION_DUR
        overlaps = [ov] * max(0, n - 1)
    times, t = [], 0.0
    for i, d in enumerate(durations):
        times.append((t, t + d))
        if i < n - 1:
            t += d - overlaps[i]
    return times


def transition_plan(durations: list[float], beats: list[float], *,
                    near: float = 0.25, cut_dur: float = 0.06,
                    dissolve_dur: float = TRANSITION_DUR) -> list[float]:
    """Per-junction overlap driven by the music beats (P1 rhythm-aware cutting).

    A cut point that lands near a beat becomes a near-instant xfade — it reads as
    a hard cut landing ON the beat (punchy, intentional); off-beat junctions keep
    the soft dissolve, which hides the misalignment. Returns len(durations)-1
    overlaps. No beats (or <2 clips) → all dissolves = today's behaviour.
    """
    n = len(durations)
    if not beats or n < 2:
        return [dissolve_dur] * max(0, n - 1)
    overlaps, t = [], 0.0
    for i in range(n - 1):
        t += durations[i]                       # raw cut point after clip i
        nearest = min(abs(b - t) for b in beats)
        overlaps.append(cut_dur if nearest <= near else dissolve_dur)
    return overlaps


def tempo_grid(total_dur: float, bpm: float) -> list[float]:
    """A synthetic beat grid at `bpm` over `total_dur` seconds. Lets cuts be
    rhythm-aware even with NO music bed (Suno-independent anti-slideshow) — the
    reel still cuts on a steady pulse instead of uniform mush."""
    if bpm <= 0 or total_dur <= 0:
        return []
    step = 60.0 / bpm
    n = int(total_dur / step) + 1
    return [round(i * step, 3) for i in range(n)]


def beat_overlaps(clips: list[dict], music_path: str | None,
                  transition: str = "crossfade", fallback_bpm: float = 0.0) -> list[float] | None:
    """Per-junction overlaps for beat-aware cutting (P1), or None for the uniform
    path. Returns None — today's exact behaviour — when transition is 'none' or
    there are <2 clips. With a music bed, snaps cuts to detected beats; with no
    music but a `fallback_bpm`, snaps to a synthetic tempo grid so cutting stays
    rhythmic without a music credit. Beat detection is best-effort; never raises."""
    if transition == "none" or len(clips) < 2:
        return None
    beats = []
    if music_path:
        try:
            from agents import beat_track
            beats = beat_track.beat_times(music_path)
        except Exception:
            beats = []
    durations = [c["actual_duration"] for c in clips]
    if not beats and fallback_bpm and fallback_bpm > 0:
        beats = tempo_grid(sum(durations), fallback_bpm)
    if not beats:
        return None
    return transition_plan(durations, beats)


def frame_timecodes(frames: list[dict], clips: list[dict],
                    transition: str = "crossfade",
                    overlaps: list[float] | None = None) -> list[tuple[float, float]]:
    """
    Effective (start, end) per FRAME in the rendered video. Multi-shot coverage
    splits a frame into clips named '<frame_id>_<n>'; a frame's window spans all
    of its sub-clips. `overlaps` threads beat-aware per-junction overlaps so the
    burned captions land exactly where the cuts do.
    """
    times = effective_timecodes([c["actual_duration"] for c in clips], transition, overlaps)
    out = []
    for f in frames:
        fid = f["frame_id"]
        spans = [t for c, t in zip(clips, times)
                 if c["segment_id"] == fid or c["segment_id"].startswith(fid + "_")]
        if spans:
            out.append((spans[0][0], spans[-1][1]))
        else:
            prev_end = out[-1][1] if out else 0.0
            out.append((prev_end, prev_end + float(f.get("duration", 0.0))))
    return out


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


def _build_video_with_transitions(clips: list[dict], output_path: str,
                                  overlaps: list[float] | None = None):
    """Concat clips with xfade transitions. `overlaps` is the per-junction xfade
    duration (len n-1); near-zero overlaps read as a hard cut on the beat. None →
    uniform TRANSITION_DUR (today's behaviour)."""
    n = len(clips)
    if overlaps is None:
        overlaps = [TRANSITION_DUR] * max(0, n - 1)
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
        ov = overlaps[i - 1] if i - 1 < len(overlaps) else TRANSITION_DUR
        offset += clips[i - 1]["actual_duration"] - ov
        next_label = f"[x{i}]" if i < n - 1 else "[vout]"
        filter_parts.append(
            f"{prev_label}[{i}:v]xfade=transition=fade:"
            f"duration={ov:.3f}:offset={offset:.3f}{next_label}"
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


_SUBS_AVAILABLE = None      # cached: does this ffmpeg have the libass `subtitles` filter?
_SUBS_WARNED = False


def _subtitles_available() -> bool:
    """Whether the ffmpeg on PATH can burn ASS/SRT captions (needs libass).
    Homebrew's ffmpeg 8.x is sometimes built WITHOUT libass, in which case the
    `subtitles` filter simply doesn't exist and the whole assemble crashes."""
    global _SUBS_AVAILABLE
    if _SUBS_AVAILABLE is None:
        try:
            out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                                 capture_output=True, text=True, timeout=10).stdout
            _SUBS_AVAILABLE = any(line.split()[1:2] == ["subtitles"]
                                  for line in out.splitlines() if line.strip())
        except Exception:
            _SUBS_AVAILABLE = False
    return _SUBS_AVAILABLE


def _subtitle_filter(sub_path: str) -> str:
    """Build the ffmpeg subtitles filter string for an ASS file, or a passthrough
    (`null`) if this ffmpeg lacks libass — a missing OPTIONAL caption filter must
    degrade to a clean reel, never crash the assemble and lose every rendered clip."""
    global _SUBS_WARNED
    if not _subtitles_available():
        if not _SUBS_WARNED:
            _SUBS_WARNED = True
            print("[Assembler] ffmpeg has no libass/`subtitles` filter — rendering "
                  "WITHOUT burned-in captions (install an ffmpeg built --enable-libass "
                  "to restore them).")
            try:
                from agents import degradation
                degradation.report("assemble", "warn",
                                   "ffmpeg built without libass — captions were NOT burned "
                                   "into this reel; video otherwise complete")
            except Exception:
                pass
        return "null"
    escaped = os.path.abspath(sub_path).replace("\\", "/").replace("'", r"\'").replace(":", r"\:")
    return f"subtitles='{escaped}':fontsdir='{FONT_DIR}'"


def _strip_audio(clip_path: str, out_path: str) -> str:
    """Strip audio stream from a clip (for video-only concat when lipsync audio is handled separately)."""
    _run(["ffmpeg", "-y", "-i", clip_path, "-c:v", "copy", "-an", out_path])
    return out_path


def _assemble_with_lipsync(clips: list[dict], temp: Path, output_path: str,
                            music_path: str | None, sub_path: str | None,
                            transition: str, overlaps: list[float] | None = None):
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
        _build_video_with_transitions(prepped, raw_video, overlaps)

    # ── Compute timecodes per clip (accounting for crossfade overlap) ─────────
    timecodes = effective_timecodes([c["actual_duration"] for c in clips], transition, overlaps)
    total_dur = timecodes[-1][1] if timecodes else 0.0

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
                          transition: str = "crossfade", is_voiceover: bool = False,
                          bg_music_path: str = None,
                          overlaps: list[float] | None = None):
    """
    Caption-only assembly: visuals + captions + optional music or voiceover.
    Accepts either .srt or .ass path; .ass is used when present.

    is_voiceover=True means `music_path` is a full-length narration track that is
    already timed to the video — play it ONCE at full volume (no loop, no ducking).
    Background music (is_voiceover=False) loops and is ducked under the video.
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
        _assemble_with_lipsync(clips, temp, output_path, music_path, sub_path,
                               transition, overlaps)
        return

    # ── Standard assembly (no lipsync) ────────────────────────────────────────
    raw_video = str(temp / "raw_video.mp4")
    if transition == "none":
        print("[Assembler] Building hard-cut concat...")
        _concat_clips_hard(clips, raw_video)
    else:
        n_cuts = sum(1 for o in (overlaps or []) if o < TRANSITION_DUR)
        print(f"[Assembler] Building transitions...{f' ({n_cuts} beat-synced cuts)' if n_cuts else ''}")
        _build_video_with_transitions(clips, raw_video, overlaps)

    print("[Assembler] Final merge...")
    vf_str = _subtitle_filter(sub_path) if sub_path else "null"

    if music_path and os.path.exists(music_path) and is_voiceover \
            and bg_music_path and os.path.exists(bg_music_path):
        # Brand/ad mode: announcer VO (full) mixed OVER looped background music
        # ducked underneath — both play at once (Tata-style spot).
        total_dur = sum(c["actual_duration"] for c in clips)
        cmd = [
            "ffmpeg", "-y",
            "-i", raw_video,
            "-i", music_path,
            "-stream_loop", "-1", "-i", bg_music_path,
            "-filter_complex",
            f"[0:v]{vf_str}[vout];"
            f"[1:a]apad,volume=1.0[vo];"
            f"[2:a]volume=0.18,afade=t=out:st={max(0,total_dur-3):.1f}:d=3[bg];"
            f"[vo][bg]amix=inputs=2:normalize=0[aout]",
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k", "-shortest",
            output_path,
        ]
    elif music_path and os.path.exists(music_path) and is_voiceover:
        # Voiceover: already timed to the video — play once, full volume, NO loop.
        # apad guards against a track a hair shorter than the video; -shortest trims.
        cmd = [
            "ffmpeg", "-y",
            "-i", raw_video,
            "-i", music_path,
            "-filter_complex",
            f"[0:v]{vf_str}[vout];[1:a]apad,volume=1.0[aout]",
            "-map", "[vout]",
            "-map", "[aout]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k", "-shortest",
            output_path,
        ]
    elif music_path and os.path.exists(music_path):
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


def apply_brand_overlay(in_path: str, out_path: str, disclosure_text: str = "",
                        logo_path: str = "", logo_corner: str = "tr",
                        disclosure_secs: float = 3.0,
                        watermark_path: str = "", width: int = 0, height: int = 0):
    """
    Final overlay post-pass (isolated re-encode, so the assembly branches stay untouched):
      · `watermark_path`: HOB IP/property PNG laid FULL-FRAME over the whole video
        (both story and brand modes) — its alpha shows the video through. Scaled to
        width×height when given; otherwise composited at native size.
      · `disclosure_text` (brand): burns "Paid partnership with …" top-centre for the
        first `disclosure_secs`.
      · `logo_path` (brand): a small advertiser logo bug in a corner for the whole video.
    No-op (plain copy) when nothing is requested. Degrades to copy on ffmpeg error.
    Layer order: video → IP watermark → advertiser logo → disclosure (top).
    """
    import shutil
    has_wm   = bool(watermark_path) and os.path.exists(watermark_path)
    has_logo = bool(logo_path) and os.path.exists(logo_path)
    has_disc = bool(disclosure_text)
    if not has_wm and not has_logo and not has_disc:
        shutil.copy2(in_path, out_path)
        return out_path

    # Disclosure text needs drawtext (freetype). Homebrew's default ffmpeg ships
    # WITHOUT it, and the old behaviour was catastrophic-but-quiet: the whole
    # overlay pass failed → clean copy → the PROVENANCE DISCLOSURE silently never
    # burned (a governance label, not a nicety — same failure class as the S31
    # subject_name bug). Fallback: render the text to a transparent PNG with PIL
    # and composite it with plain `overlay`, which every ffmpeg build has.
    disc_png = ""
    if has_disc and not _has_drawtext():
        try:
            disc_png = _disclosure_png(disclosure_text, width or 1080)
            print("[Assembler] ffmpeg has no drawtext — disclosure via PIL overlay")
        except Exception as e:
            print(f"[Assembler] PIL disclosure fallback failed ({e})")

    inputs = ["-i", in_path]
    idx = 1
    wm_idx = logo_idx = disc_idx = None
    if has_wm:
        inputs += ["-i", watermark_path]; wm_idx = idx; idx += 1
    if has_logo:
        inputs += ["-i", logo_path]; logo_idx = idx; idx += 1
    if disc_png:
        inputs += ["-i", disc_png]; disc_idx = idx; idx += 1

    chain, last = [], "[0:v]"
    if has_wm:
        # Full-frame IP watermark for the whole duration; PNG alpha does the blend.
        if width and height:
            chain.append(f"[{wm_idx}:v]scale={width}:{height}[wm]")
            chain.append(f"{last}[wm]overlay=0:0[vw]")
        else:
            chain.append(f"{last}[{wm_idx}:v]overlay=0:0[vw]")
        last = "[vw]"
    if has_logo:
        margin = 40
        pos = {"tr": f"W-w-{margin}:{margin}", "tl": f"{margin}:{margin}",
               "br": f"W-w-{margin}:H-h-{margin}", "bl": f"{margin}:H-h-{margin}"}.get(
               logo_corner, f"W-w-{margin}:{margin}")
        chain.append(f"[{logo_idx}:v]scale=-1:90[lg]")
        chain.append(f"{last}[lg]overlay={pos}[v1]")
        last = "[v1]"
    if disc_idx is not None:
        # PIL-rendered disclosure PNG, top-centred, first `disclosure_secs` only.
        chain.append(f"{last}[{disc_idx}:v]overlay=(W-w)/2:60:"
                     f"enable='lte(t,{disclosure_secs})'[vout]")
        last = "[vout]"
    elif has_disc:
        font = _brand_font_arg()
        safe = disclosure_text.replace("'", r"\'").replace(":", r"\:")
        chain.append(
            f"{last}drawtext={font}text='{safe}':fontcolor=white:fontsize=34:"
            f"box=1:boxcolor=black@0.45:boxborderw=14:x=(w-text_w)/2:y=60:"
            f"enable='lte(t,{disclosure_secs})'[vout]")
        last = "[vout]"

    try:
        _run(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(chain),
              "-map", last, "-map", "0:a?",
              "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "18",
              "-c:a", "copy", out_path])
    except Exception as e:
        print(f"[Assembler] brand overlay failed ({e}) — using clean output")
        # A lost disclosure/watermark is a GOVERNANCE gap, not a cosmetic one —
        # surface it on the canvas render report, never just the console.
        try:
            from agents import degradation
            what = " + ".join(x for x, on in (("disclosure", has_disc),
                                              ("logo", has_logo),
                                              ("IP watermark", has_wm)) if on)
            degradation.report("provenance", "alert",
                               f"overlay pass failed — {what} NOT burned into the reel "
                               f"({str(e)[:100]})")
        except Exception:
            pass
        shutil.copy2(in_path, out_path)
    finally:
        if disc_png:
            try:
                os.remove(disc_png)
            except OSError:
                pass
    return out_path


def _has_drawtext() -> bool:
    """True if this ffmpeg build has the drawtext filter (needs freetype).
    Probed once per process — Homebrew's default build lacks it."""
    global _DRAWTEXT_OK
    if _DRAWTEXT_OK is None:
        try:
            r = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                               capture_output=True, text=True, timeout=15)
            _DRAWTEXT_OK = " drawtext " in r.stdout
        except Exception:
            _DRAWTEXT_OK = False
    return _DRAWTEXT_OK


_DRAWTEXT_OK = None


def _disclosure_png(text: str, video_width: int) -> str:
    """Render the disclosure line to a transparent PNG (white on translucent black
    box — visually matching the drawtext style) for ffmpeg `overlay` compositing."""
    import tempfile
    from PIL import Image, ImageDraw, ImageFont
    font = None
    for cand in ("deploy/fonts/Montserrat-Regular.ttf", "deploy/fonts/Satoshi-Medium.ttf"):
        p = os.path.join(os.path.dirname(__file__), "..", cand)
        if os.path.exists(p):
            try:
                font = ImageFont.truetype(p, 34)
                break
            except Exception:
                pass
    if font is None:
        font = ImageFont.load_default(size=34)
    pad = 14
    probe = Image.new("RGBA", (8, 8))
    box = ImageDraw.Draw(probe).textbbox((0, 0), text, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    tw = min(tw, max(100, video_width - 80))
    img = Image.new("RGBA", (tw + 2 * pad, th + 2 * pad), (0, 0, 0, 115))
    ImageDraw.Draw(img).text((pad - box[0], pad - box[1]), text,
                             font=font, fill=(255, 255, 255, 255))
    out = tempfile.mktemp(suffix="_disc.png")
    img.save(out)
    return out


def _brand_font_arg() -> str:
    """fontfile= for drawtext, preferring the bundled Montserrat; '' to let ffmpeg
    pick a default (some builds need fontconfig)."""
    here = os.path.dirname(__file__)
    cand = os.path.abspath(os.path.join(here, "..", "deploy", "fonts", "Montserrat-Regular.ttf"))
    if os.path.exists(cand):
        return f"fontfile='{cand}':"
    return ""


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
