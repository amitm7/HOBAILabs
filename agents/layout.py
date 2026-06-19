"""Per-frame layout rendering.

LAY-0 starts intentionally small: the first real preset is a full-bleed text card.
Future half/split/PIP/overlay presets should extend this module instead of adding
new compositor branches elsewhere.
"""

from __future__ import annotations

import os
import textwrap


def text_card_layout(text: str, *, bg: str = "#111111", fg: str = "#ffffff") -> dict:
    return {"preset": "text_card", "text": text, "bg": bg, "fg": fg}


def is_layout_frame(frame: dict) -> bool:
    layout = frame.get("layout") or {}
    return layout.get("preset") == "text_card"


def _hex_to_rgb(value: str, default: tuple[int, int, int]) -> tuple[int, int, int]:
    h = (value or "").strip().lstrip("#")
    if len(h) == 6:
        try:
            return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            pass
    return default


def _font(size: int):
    from PIL import ImageFont

    here = os.path.dirname(__file__)
    candidates = [
        os.path.abspath(os.path.join(here, "..", "deploy", "fonts", "Montserrat-Regular.ttf")),
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _fit_text_block(text: str, width: int, height: int) -> tuple[list[str], int, int]:
    """Return (wrapped lines, font size, top y) guaranteed to fit vertically."""
    max_w = int(width * 0.82)
    max_h = int(height * 0.72)
    size = max(54, int(width * 0.075))
    floor = max(24, int(size * 0.45))
    while size >= floor:
        wrap_chars = max(10, int(max_w / (size * 0.58)))
        lines = textwrap.wrap(text, width=wrap_chars) or [""]
        line_h = int(size * 1.2)
        total_h = line_h * len(lines)
        if total_h <= max_h:
            return lines, size, max(0, (height - total_h) // 2)
        size -= 3
    wrap_chars = max(14, int(max_w / (floor * 0.52)))
    lines = textwrap.wrap(text, width=wrap_chars) or [""]
    line_h = int(floor * 1.15)
    total_h = line_h * len(lines)
    return lines, floor, max(0, (height - min(total_h, max_h)) // 2)


def render_layout_frame(frame: dict, out_path: str, width: int = 1080, height: int = 1920) -> str:
    """Render a layout frame to a still image and return `out_path`."""
    from PIL import Image, ImageDraw

    layout = frame.get("layout") or {}
    if layout.get("preset") != "text_card":
        raise ValueError(f"Unsupported layout preset: {layout.get('preset')}")

    text = (layout.get("text") or frame.get("caption") or "").strip()
    bg = _hex_to_rgb(layout.get("bg", "#111111"), (17, 17, 17))
    fg = _hex_to_rgb(layout.get("fg", "#ffffff"), (255, 255, 255))
    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)

    lines, size, y = _fit_text_block(text, width, height)
    font = _font(size)
    line_h = int(size * 1.2)
    for line in lines:
        try:
            bbox = draw.textbbox((0, 0), line, font=font)
            tw = bbox[2] - bbox[0]
        except Exception:
            tw = len(line) * size * 0.5
        draw.text(((width - tw) / 2, y), line, fill=fg, font=font)
        y += line_h

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    img.save(out_path, "JPEG", quality=94)
    return out_path
