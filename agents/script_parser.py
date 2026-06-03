"""Parse structured FRAME-based scripts.

Supports two formats:

Format A (original):
  FRAME
  visual: filename.jpg
  caption: Text to show on screen

Format B (new HOB format):
  Reels

  Frame 1
  Caption line 1
  Caption line 2
  Frame 2
  ...

  Caption:
  Full voiceover text (ignored here, used separately)
"""
import os
import re


def _frame_duration(caption: str, max_dur: float = 9.0) -> float:
    words = len(caption.split()) if caption.strip() else 0
    if words == 0:
        return 2.5
    return max(3.5, min(max_dur, words / 2.0))


def _parse_format_a(raw: str, assets_dir: str) -> list[dict]:
    """Parse FRAME / visual: / caption: format."""
    blocks = [b.strip() for b in raw.strip().split("FRAME") if b.strip()]
    frames = []
    for i, block in enumerate(blocks):
        lines = block.strip().splitlines()
        visual, caption = "", ""
        for line in lines:
            if line.startswith("visual:"):
                visual = line[len("visual:"):].strip()
            elif line.startswith("caption:"):
                caption = line[len("caption:"):].strip()
        visual_path = os.path.join(assets_dir, visual) if visual else ""
        frames.append({
            "frame_id": f"f{i+1:02d}",
            "visual": visual,
            "visual_path": visual_path,
            "caption": caption,
            "duration": _frame_duration(caption),
        })
    return frames


def _parse_format_b(raw: str, assets_dir: str) -> list[dict]:
    """Parse 'Reels / Frame N / text' format.

    Supports inline director notes:
        Frame 3
        I got selected… only to find out it was a big fraud.
        [note: show betrayal — not just sadness, show anger too. Close face, wet eyes.]
    """
    caption_split = re.split(r'\nCaption\s*:', raw, maxsplit=1, flags=re.IGNORECASE)
    reels_section = caption_split[0]
    reels_section = re.sub(r'^Reels\s*\n', '', reels_section.strip(), flags=re.IGNORECASE)

    frame_pattern = re.compile(r'\bFrame\s*\d+\b', re.IGNORECASE)
    parts = frame_pattern.split(reels_section)

    frames = []
    frame_idx = 1
    for part in parts:
        text = part.strip()
        if not text and frame_idx == 1:
            continue

        # Extract annotations
        note_match     = re.search(r'\[note:\s*(.*?)\]',     text, re.IGNORECASE | re.DOTALL)
        photo_match    = re.search(r'\[photo:\s*(.*?)\]',    text, re.IGNORECASE | re.DOTALL)
        duration_match = re.search(r'\[duration:\s*(\d+\.?\d*)\s*s?\]', text, re.IGNORECASE)

        director_note    = note_match.group(1).strip()              if note_match     else ""
        photo_spec       = photo_match.group(1).strip()             if photo_match    else ""
        duration_override = float(duration_match.group(1))          if duration_match else None

        # Remove annotations from display caption
        clean_text = re.sub(r'\[note:.*?\]',     '', text, flags=re.IGNORECASE | re.DOTALL)
        clean_text = re.sub(r'\[photo:.*?\]',    '', clean_text, flags=re.IGNORECASE | re.DOTALL)
        clean_text = re.sub(r'\[duration:.*?\]', '', clean_text, flags=re.IGNORECASE | re.DOTALL)
        caption = re.sub(r'\s+', ' ', " ".join(clean_text.splitlines())).strip()

        duration = duration_override if duration_override is not None else _frame_duration(caption)

        frames.append({
            "frame_id": f"f{frame_idx:02d}",
            "visual": "",
            "visual_path": "",
            "caption": caption,
            "director_note": director_note,
            "photo_spec": photo_spec,
            "duration": duration,
        })
        frame_idx += 1

    return frames


def parse_frame_script(script_path: str, assets_dir: str,
                       max_frame_dur: float = 9.0) -> list[dict]:
    with open(script_path, "r") as f:
        raw = f.read()

    # Detect format
    if "FRAME\n" in raw or raw.strip().startswith("FRAME"):
        frames = _parse_format_a(raw, assets_dir)
    else:
        frames = _parse_format_b(raw, assets_dir)

    # Apply max duration cap (≤5s → 6 Kling credits, >5s → 12 credits)
    if max_frame_dur < 9.0:
        for f in frames:
            f["duration"] = min(f["duration"], max_frame_dur)

    # For format B, try to auto-match visuals from assets_dir by sort order.
    # Skip frames with an explicit photo_spec (handled by pipeline at runtime).
    needs_auto = [f for f in frames if not f["visual_path"] and not f.get("photo_spec", "")]
    if needs_auto and assets_dir:
        try:
            available = sorted([
                fn for fn in os.listdir(assets_dir)
                if os.path.splitext(fn)[1].lower() in {".jpg", ".jpeg", ".png", ".webp"}
                and not fn.startswith("ai_")
            ])
            auto_idx = 0
            for frame in frames:
                if not frame["visual_path"] and not frame.get("photo_spec", ""):
                    if auto_idx < len(available):
                        frame["visual"] = available[auto_idx]
                        frame["visual_path"] = os.path.join(assets_dir, available[auto_idx])
                        auto_idx += 1
        except Exception:
            pass

    print(f"[ScriptParser] Parsed {len(frames)} frames")
    for f in frames:
        spec = f.get("photo_spec", "")
        if spec:
            visual_name = spec
        elif f["visual_path"]:
            visual_name = os.path.basename(f["visual_path"])
        else:
            visual_name = "AI-generate"
        print(f"  {f['frame_id']} ({f['duration']:.1f}s) [{visual_name}]: {f['caption'][:55] or '(silent)'}")

    return frames
