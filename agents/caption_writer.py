"""Generate ASS subtitle files from frame durations.

Uses ASS format for reliable font, size, position, and color control.
All style parameters are runtime-configurable.
"""

import os

# ASS alignment codes: bottom=2, middle=5, top=8 (all center-aligned)
_ALIGNMENT = {"bottom": 2, "middle": 5, "top": 8}
# Bottom captions need to clear Instagram/Reels UI chrome on real phones.
_MARGIN_V   = {"bottom": 320, "middle": 0, "top": 60}

# &HAABBGGRR format (ASS is BGRA, alpha=00 means fully opaque)
_COLOR = {
    "white":  "&H00FFFFFF",
    "yellow": "&H0000FFFF",
    "black":  "&H00000000",
}


def _apply_highlights(text: str, base_color: str, highlight_color: str = "yellow") -> str:
    """Convert ==keyword== spans into ASS color tags."""
    import re

    reset = _COLOR.get(base_color, _COLOR["white"])
    hi = _COLOR.get(highlight_color, _COLOR["yellow"])
    return re.sub(
        r"==(.+?)==",
        lambda m: f"{{\\c{hi}}}{m.group(1)}{{\\c{reset}}}",
        text,
    )


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


def _max_chars_for(size: int) -> int:
    """Characters per line that fit at a given font size (28 chars at 52pt)."""
    return max(12, int(28 * 52 / size))


def _fit_caption(text: str, base_size: int, max_lines: int) -> tuple[str, int]:
    """
    Wrap `text` and pick a font size so it fits in at most `max_lines` lines.

    max_lines<=0 → no cap: wrap at the base size (legacy behaviour).
    Otherwise → cap at max_lines; if the text still overflows, shrink the font
    (down to ~60% of base) so nothing is lost and it never spills the frame.
    Returns (wrapped_text, font_size).
    """
    if not max_lines or max_lines <= 0:
        return _wrap_text(text, _max_chars_for(base_size)), base_size

    size  = base_size
    floor = max(20, int(base_size * 0.6))
    while size >= floor:
        wrapped = _wrap_text(text, _max_chars_for(size))
        if wrapped.count("\\N") + 1 <= max_lines:
            return wrapped, size
        size -= 2

    # Floor reached and still over: force into exactly max_lines by widening the
    # line length (longer lines beat invisible/overflowing text).
    forced_chars = max(_max_chars_for(floor), (len(text) // max_lines) + 8)
    return _wrap_text(text, forced_chars), floor


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
    font       = style.get("font") or os.environ.get("HOB_CAPTION_FONT", "Baskerville")
    size       = int(style.get("size", 52))
    color      = style.get("color", "white")
    highlight_color = style.get("highlight_color", "yellow")
    g_position = style.get("position", "bottom")          # global default
    g_max_lines = int(style.get("max_lines", 0) or 0)     # 0 = unlimited

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
                # Per-frame overrides fall back to the global defaults.
                pos = (f.get("caption_position") or "").strip() or g_position
                ml_raw = f.get("caption_max_lines")
                try:
                    ml = int(ml_raw) if ml_raw not in (None, "", 0, "0") else g_max_lines
                except (TypeError, ValueError):
                    ml = g_max_lines
                wrapped, fs = _fit_caption(caption, size, ml)
                wrapped = _apply_highlights(wrapped, color, highlight_color)
                align    = _ALIGNMENT.get(pos, 2)
                margin_v = _MARGIN_V.get(pos, 100)
                # Inline override: alignment per line (+ shrunk size when capped).
                tag = f"{{\\an{align}" + (f"\\fs{fs}" if fs != size else "") + "}"
                entries.append((start, end, margin_v, tag + wrapped))
        cursor += dur

    with open(ass_path, "w", encoding="utf-8") as fp:
        fp.write(_build_ass_header(font, size, color, g_position))
        for start, end, margin_v, text in entries:
            fp.write(
                f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},"
                f"Main,,0,0,{margin_v},,{text}\n"
            )

    print(f"[CaptionWriter] {len(entries)} captions | {font} {size}pt {color} "
          f"{g_position} | max_lines={g_max_lines or '∞'} → {ass_path}")
    return ass_path
