"""Generate ASS subtitle files from frame durations.

Uses ASS format for reliable font, size, position, and color control.
All style parameters are runtime-configurable.
"""

import os

# ASS alignment codes: bottom=2, middle=5, top=8 (all center-aligned)
_ALIGNMENT = {"bottom": 2, "middle": 5, "top": 8}
_MARGIN_V   = {"bottom": 100, "middle": 0, "top": 60}

# &HAABBGGRR format (ASS is BGRA, alpha=00 means fully opaque)
_COLOR = {
    "white":  "&H00FFFFFF",
    "yellow": "&H0000FFFF",
    "black":  "&H00000000",
}


def _ass_time(seconds: float) -> str:
    h  = int(seconds // 3600)
    m  = int((seconds % 3600) // 60)
    s  = int(seconds % 60)
    cs = int((seconds % 1) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _wrap_text(text: str, max_chars: int = 28) -> str:
    """Wrap long captions to multiple lines for ASS (\\N = hard break)."""
    words = text.split()
    lines, current, count = [], [], 0
    for w in words:
        if count + len(w) + (1 if current else 0) > max_chars and current:
            lines.append(" ".join(current))
            current, count = [w], len(w)
        else:
            current.append(w)
            count += len(w) + (1 if len(current) > 1 else 0)
    if current:
        lines.append(" ".join(current))
    return r"\N".join(lines)


def _build_ass_header(font: str, size: int, color: str, position: str,
                      play_res_x: int = 1080, play_res_y: int = 1920) -> str:
    alignment  = _ALIGNMENT.get(position, 2)
    margin_v   = _MARGIN_V.get(position, 100)
    color_code = _COLOR.get(color, "&H00FFFFFF")
    # Storytelling captions are set in Baskerville italic; keep italic for any
    # Baskerville variant (incl. "Libre Baskerville" used in the Linux image).
    italic     = 1 if "baskerville" in font.lower() else 0

    return (
        f"[Script Info]\n"
        f"ScriptType: v4.00+\n"
        f"PlayResX: {play_res_x}\n"
        f"PlayResY: {play_res_y}\n"
        f"WrapStyle: 1\n"
        f"ScaledBorderAndShadow: yes\n\n"
        f"[V4+ Styles]\n"
        f"Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        f"BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        f"BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Main,{font},{size},{color_code},&H000000FF,&H00000000,&H96000000,"
        f"0,{italic},0,0,100,100,0.5,0,1,2.0,2.5,{alignment},60,60,{margin_v},1\n\n"
        f"[Events]\n"
        f"Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


def generate_frame_srt(frames: list[dict], srt_path: str,
                       fade_gap: float = 0.3,
                       caption_style: dict = None,
                       timecodes: list[tuple[float, float]] = None) -> str:
    """
    Build an ASS subtitle file from frame list.
    caption_style keys: font, size, color, position
    timecodes: optional per-frame (start, end) in the RENDERED video (see
    assembler.frame_timecodes) — pass these whenever clips are joined with a
    crossfade, since the overlap makes raw cumulative durations drift.
    Returns path to the generated .ass file.
    """
    style = caption_style or {}
    # Default font is env-overridable so the Linux image can point at a font that
    # actually ships in it (HOB_CAPTION_FONT), while local macOS keeps Baskerville.
    font     = style.get("font") or os.environ.get("HOB_CAPTION_FONT", "Baskerville")
    size     = int(style.get("size", 52))
    color    = style.get("color", "white")
    position = style.get("position", "bottom")

    # Wrap width scales inversely with font size so text fits the frame
    max_chars = max(18, int(28 * 52 / size))

    ass_path = srt_path.replace(".srt", ".ass") if srt_path.endswith(".srt") else srt_path + ".ass"

    entries = []
    cursor = 0.0
    for i, f in enumerate(frames):
        dur     = f["duration"]
        caption = f.get("caption", "").strip()
        if timecodes is not None:
            frame_start, frame_end = timecodes[i]
        else:
            frame_start, frame_end = cursor, cursor + dur
        if caption:
            start = frame_start + fade_gap
            end   = frame_end - fade_gap
            if end > start:
                entries.append((start, end, caption))
        cursor += dur

    with open(ass_path, "w", encoding="utf-8") as fp:
        fp.write(_build_ass_header(font, size, color, position))
        for start, end, text in entries:
            wrapped = _wrap_text(text, max_chars)
            fp.write(
                f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},"
                f"Main,,0,0,0,,{wrapped}\n"
            )

    print(f"[CaptionWriter] {len(entries)} captions | {font} {size}pt {color} {position} → {ass_path}")
    return ass_path
