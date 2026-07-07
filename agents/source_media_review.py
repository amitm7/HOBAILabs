"""Source-media review (PROVENANCE_PLAN Slice B, Item 3).

Before any paid generation, inspect every REAL file the operator supplied and
write a standardized `source_media_review.json` into the run dir. This is the
evidence side of the real-media-preservation moat: the credential's per-frame
"tier: real" rows point at media we demonstrably received, hashed, and passed
through untouched — a content hash recorded BEFORE the pipeline ran is what
makes "never AI-regenerated" auditable rather than asserted.

Best-effort by design (build-feature rule 4/13): a probe failure degrades to a
partial review + ledger warn; it never blocks a render.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".heif"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".m4v", ".webm", ".mkv"}


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _probe_video(path: str) -> dict:
    """width/height/duration/codec via ffprobe (same tool the assembler trusts)."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,codec_name",
         "-show_entries", "format=duration",
         "-of", "json", path],
        capture_output=True, text=True, timeout=30)
    info = json.loads(out.stdout or "{}")
    stream = (info.get("streams") or [{}])[0]
    return {
        "width": stream.get("width"),
        "height": stream.get("height"),
        "codec": stream.get("codec_name"),
        "duration": round(float((info.get("format") or {}).get("duration") or 0), 2),
    }


def _probe_image(path: str) -> dict:
    from PIL import Image, ImageOps
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)
        return {"width": img.width, "height": img.height, "codec": (img.format or "").lower()}


def review_frames(frames: list[dict]) -> dict:
    """Probe every distinct real source file referenced by the frames.

    Returns {"reviewed_at", "files": [{file, frame_ids, kind, bytes, sha256,
    width, height, codec[, duration][, error]}], "errors": int}. `file` is the
    basename (review rows may travel with the export bundle; local paths stay
    local). Frames with no resolved real media (AI specs, empty) are skipped.
    """
    by_path: dict[str, list[str]] = {}
    for f in frames:
        spec = (f.get("photo_spec") or "").strip()
        if spec.startswith("ai_"):
            continue
        vp = (f.get("visual_path") or "").strip()
        if vp and os.path.isfile(vp):
            by_path.setdefault(vp, []).append(f.get("frame_id", ""))

    files, errors = [], 0
    for path, fids in sorted(by_path.items()):
        ext = os.path.splitext(path)[1].lower()
        row = {"file": os.path.basename(path), "frame_ids": fids,
               "kind": "video" if ext in VIDEO_EXTS else "image",
               "bytes": os.path.getsize(path)}
        try:
            row["sha256"] = _sha256(path)
            row.update(_probe_video(path) if row["kind"] == "video" else _probe_image(path))
        except Exception as e:                 # partial row beats no row
            row["error"] = str(e)[:120]
            errors += 1
        files.append(row)
    return {"reviewed_at": int(time.time()), "files": files, "errors": errors}


def write_review(run_dir: Path, frames: list[dict]) -> dict | None:
    """Run the review and persist source_media_review.json. Never raises.

    Emits a ledger warn when real media exists but the review had probe errors,
    so a gap in the evidence trail is visible instead of silent.
    """
    try:
        rev = review_frames(frames)
        (Path(run_dir) / "source_media_review.json").write_text(
            json.dumps(rev, indent=2))
        n = len(rev["files"])
        print(f"[SourceReview] {n} real source file(s) hashed + probed"
              + (f" ({rev['errors']} probe error(s))" if rev["errors"] else ""))
        if rev["errors"]:
            from agents import degradation
            degradation.report("provenance", "warn",
                               f"source media review incomplete: {rev['errors']} of {n} files failed probing")
        return rev
    except Exception as e:
        try:
            from agents import degradation
            degradation.report("provenance", "warn", f"source media review skipped ({e})")
        except Exception:
            pass
        return None
