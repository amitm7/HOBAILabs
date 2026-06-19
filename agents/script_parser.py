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


def extract_caption_block(raw: str) -> str:
    """Return the Format B `Caption:` block used for posting copy."""
    parts = re.split(r'\nCaption\s*:', raw, maxsplit=1, flags=re.IGNORECASE)
    return parts[1].strip() if len(parts) > 1 else ""


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
        edit_match     = re.search(r'\[edit:\s*(.*?)\]',     text, re.IGNORECASE | re.DOTALL)
        motion_match   = re.search(r'\[motion:\s*(.*?)\]',   text, re.IGNORECASE | re.DOTALL)
        camera_match   = re.search(r'\[camera:\s*(.*?)\]',   text, re.IGNORECASE | re.DOTALL)
        lipsync_match  = re.search(r'\[lipsync:\s*(yes|true|1)\s*\]', text, re.IGNORECASE)
        voice_match    = re.search(r'\[voice:\s*(.*?)\]',    text, re.IGNORECASE | re.DOTALL)
        model_match    = re.search(r'\[model:\s*(.*?)\]',    text, re.IGNORECASE | re.DOTALL)
        imgmodel_match = re.search(r'\[imgmodel:\s*(.*?)\]', text, re.IGNORECASE | re.DOTALL)
        vidmodel_match = re.search(r'\[vidmodel:\s*(.*?)\]', text, re.IGNORECASE | re.DOTALL)
        layout_match   = re.search(r'\[layout:\s*(.*?)\]',   text, re.IGNORECASE | re.DOTALL)
        duration_match = re.search(r'\[duration:\s*(\d+\.?\d*)\s*s?\]', text, re.IGNORECASE)
        start_match    = re.search(r'\[start:\s*(\d+\.?\d*)\s*s?\]',    text, re.IGNORECASE)
        end_match      = re.search(r'\[end:\s*(\d+\.?\d*)\s*s?\]',      text, re.IGNORECASE)

        director_note     = note_match.group(1).strip()     if note_match     else ""
        photo_spec        = photo_match.group(1).strip()    if photo_match    else ""
        edit_prompt       = edit_match.group(1).strip()     if edit_match     else ""
        # [motion:] directly sets motion_prompt, bypassing GPT scene design
        # [camera:] is an alias — same effect, more intuitive naming
        motion_raw        = motion_match.group(1).strip()   if motion_match   else ""
        camera_raw        = camera_match.group(1).strip()   if camera_match   else ""
        motion_override   = motion_raw or camera_raw
        # [lipsync: yes] opts this frame into lip sync generation
        # [voice: voice_id] overrides the default ElevenLabs voice for this frame
        lipsync           = bool(lipsync_match)
        voice_override    = voice_match.group(1).strip()    if voice_match    else ""
        # [model: id] forces both image & video model; [imgmodel:]/[vidmodel:]
        # target a single step. Empty/"auto" → router picks the best per shot.
        model_any         = model_match.group(1).strip()    if model_match    else ""
        image_model_override = (imgmodel_match.group(1).strip() if imgmodel_match else "") or model_any
        video_model_override = (vidmodel_match.group(1).strip() if vidmodel_match else "") or model_any
        layout_raw        = layout_match.group(1).strip().lower() if layout_match else ""
        layout            = {"preset": "text_card"} if layout_raw in ("text_card", "text-card", "card") else {}
        duration_override = float(duration_match.group(1)) if duration_match else None
        video_start_sec   = float(start_match.group(1))    if start_match    else 0.0
        video_end_sec     = float(end_match.group(1))      if end_match      else None

        # Remove all annotations from display caption
        clean_text = re.sub(r'\[note:.*?\]',     '', text, flags=re.IGNORECASE | re.DOTALL)
        clean_text = re.sub(r'\[photo:.*?\]',    '', clean_text, flags=re.IGNORECASE | re.DOTALL)
        clean_text = re.sub(r'\[edit:.*?\]',     '', clean_text, flags=re.IGNORECASE | re.DOTALL)
        clean_text = re.sub(r'\[motion:.*?\]',   '', clean_text, flags=re.IGNORECASE | re.DOTALL)
        clean_text = re.sub(r'\[camera:.*?\]',   '', clean_text, flags=re.IGNORECASE | re.DOTALL)
        clean_text = re.sub(r'\[lipsync:.*?\]',  '', clean_text, flags=re.IGNORECASE)
        clean_text = re.sub(r'\[voice:.*?\]',    '', clean_text, flags=re.IGNORECASE | re.DOTALL)
        clean_text = re.sub(r'\[model:.*?\]',    '', clean_text, flags=re.IGNORECASE | re.DOTALL)
        clean_text = re.sub(r'\[imgmodel:.*?\]', '', clean_text, flags=re.IGNORECASE | re.DOTALL)
        clean_text = re.sub(r'\[vidmodel:.*?\]', '', clean_text, flags=re.IGNORECASE | re.DOTALL)
        clean_text = re.sub(r'\[layout:.*?\]',   '', clean_text, flags=re.IGNORECASE | re.DOTALL)
        clean_text = re.sub(r'\[duration:.*?\]', '', clean_text, flags=re.IGNORECASE)
        clean_text = re.sub(r'\[start:.*?\]',    '', clean_text, flags=re.IGNORECASE)
        clean_text = re.sub(r'\[end:.*?\]',      '', clean_text, flags=re.IGNORECASE)
        caption = re.sub(r'\s+', ' ', " ".join(clean_text.splitlines())).strip()

        duration = duration_override if duration_override is not None else _frame_duration(caption)

        frames.append({
            "frame_id":        f"f{frame_idx:02d}",
            "visual":          "",
            "visual_path":     "",
            "caption":         caption,
            "director_note":   director_note,
            "photo_spec":      photo_spec,
            "edit_prompt":     edit_prompt,
            "motion_override": motion_override,
            "lipsync":         lipsync,
            "voice_override":  voice_override,
            "image_model_override": image_model_override,
            "video_model_override": video_model_override,
            "video_start_sec": video_start_sec,
            "video_end_sec":   video_end_sec,
            "layout":          layout,
            "duration":        duration,
        })
        frame_idx += 1

    return frames


def parse_frame_script(script_path: str, assets_dir: str,
                       max_frame_dur: float = 9.0,
                       smart_match: bool = False) -> list[dict]:
    with open(script_path, "r") as f:
        raw = f.read()

    # Format A uses "visual:" and "caption:" label lines per frame block.
    # Format B uses "Frame N" / "FRAME N" headers with raw text below.
    # Detect by presence of "visual:" — without it, always use Format B.
    if "visual:" in raw:
        frames = _parse_format_a(raw, assets_dir)
    else:
        frames = _parse_format_b(raw, assets_dir)

    # Apply max duration cap (≤5s → 6 Kling credits, >5s → 12 credits)
    if max_frame_dur < 9.0:
        for f in frames:
            f["duration"] = min(f["duration"], max_frame_dur)

    # For format B, try to auto-match visuals from assets_dir by sort order.
    # Skip frames with an explicit photo_spec (handled by pipeline at runtime).
    _MEDIA_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov", ".avi", ".m4v", ".webm"}

    # Pipeline-generated / derived artifacts that must NOT be auto-matched as
    # source media — otherwise an edited copy left in the folder shifts the
    # sort-order alignment and a frame shows the next frame's photo.
    _DERIVED_MARKERS = ("_edit", "_edited", "_portrait", "_symbolic", "_norm",
                        "_5s", "_raw", "_lastframe", "_ext", "_hf", "_noaudio", "_trimtmp")

    def _is_source_media(fn: str) -> bool:
        if os.path.splitext(fn)[1].lower() not in _MEDIA_EXTS:
            return False
        if fn.startswith("ai_") or fn.startswith("_"):
            return False
        low = fn.lower()
        return not any(m in low for m in _DERIVED_MARKERS)

    needs_auto = [f for f in frames if not f["visual_path"] and not f.get("photo_spec", "")]
    if needs_auto and assets_dir:
        # Smart tier (opt-in): content-aware GPT-4o matching for unpinned frames.
        # Safe + additive — fills what it can; positional handles the rest below.
        if smart_match:
            try:
                from agents.image_matcher import smart_match as _smart_match
                _smart_match(frames, assets_dir, _is_source_media)
            except Exception as e:
                print(f"[ScriptParser] smart match unavailable ({e}) — positional fallback")

        # Positional fallback for any frame still without a visual. Exclude files
        # already used (by pins or smart match) so we don't double-assign.
        try:
            used = {os.path.basename(f["visual_path"]) for f in frames if f.get("visual_path")}
            used |= {f["photo_spec"] for f in frames
                     if f.get("photo_spec") and not f["photo_spec"].startswith("ai_")}
            available = sorted(fn for fn in os.listdir(assets_dir)
                               if _is_source_media(fn) and fn not in used)
            auto_idx = 0
            for frame in frames:
                if not frame["visual_path"] and not frame.get("photo_spec", ""):
                    if auto_idx < len(available):
                        frame["visual"] = available[auto_idx]
                        frame["visual_path"] = os.path.join(assets_dir, available[auto_idx])
                        frame["photo_spec"] = available[auto_idx]  # expose filename to UI
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
