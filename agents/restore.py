"""
Restore — Reality–Fidelity ladder, rung 1 (docs/REAL_MEDIA_QUALITY_LADDER.md).

NON-generative cleanup of the operator's REAL footage so amateur phone media reads
cinematic without faking anyone: upscale, denoise, contrast-adaptive sharpen, a light
grade, and (video) stabilization + deflicker. Same content, same identity, same claims —
it IS the real footage, just cleaner. **Zero authenticity cost** (vs. rung ≥4 which
synthesizes a person and needs consent).

All ffmpeg, no extra deps. Best-effort: returns the ORIGINAL path on any failure
(build-feature rule #4 — graceful degradation; a clean-up step must never break a reel).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".heif"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".m4v", ".webm", ".mkv"}


def _run(cmd: list[str], timeout: int = 240) -> None:
    subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)


def restore_image(in_path: str, out_path: str, *, target_h: int = 1920,
                  grade: bool = True) -> str:
    """Upscale toward `target_h` (capped at 4×), denoise, sharpen, light grade."""
    vf = [
        f"scale=-2:'min({target_h},ih*4)':flags=lanczos",   # upscale (no more than 4×)
        "hqdn3d=2:1:3:3",                                    # denoise
        "cas=0.4",                                           # contrast-adaptive sharpen
    ]
    if grade:
        vf.append("eq=contrast=1.06:saturation=1.08:brightness=0.01")
    _run(["ffmpeg", "-y", "-i", in_path, "-vf", ",".join(vf), "-q:v", "2", out_path])
    return out_path


def restore_video(in_path: str, out_path: str, *, target_h: int = 1920,
                  stabilize: bool = True, grade: bool = True) -> str:
    """Stabilize (2-pass vidstab) + denoise + upscale + sharpen + deflicker + grade."""
    # vidstab's transform file is embedded in the filtergraph, so its path must be
    # free of spaces/parens (which the source filename often has) — use a temp dir.
    trf_dir = tempfile.mkdtemp(prefix="hob_vidstab_")
    trf = os.path.join(trf_dir, "t.trf")
    if stabilize:
        try:
            _run(["ffmpeg", "-y", "-i", in_path, "-vf",
                  f"vidstabdetect=shakiness=6:result={trf}", "-f", "null", "-"])
        except Exception:
            stabilize = False
    vf = []
    if stabilize and os.path.exists(trf):
        vf.append(f"vidstabtransform=input={trf}:smoothing=22:crop=black")
    vf += [
        "hqdn3d=2:1:3:3",
        f"scale=-2:'min({target_h},ih*2)':flags=lanczos",
        "cas=0.3",
        "deflicker",
    ]
    if grade:
        vf.append("eq=contrast=1.05:saturation=1.06")
    vf.append("format=yuv420p")   # encoder-safe pixel format
    try:
        _run(["ffmpeg", "-y", "-i", in_path, "-vf", ",".join(vf),
              "-c:a", "copy", "-movflags", "+faststart", out_path], timeout=420)
    finally:
        shutil.rmtree(trf_dir, ignore_errors=True)
    return out_path


def _probe_resolution(path: str) -> tuple[int, int]:
    """(w, h) of the first video/image stream via ffprobe, or (0, 0) on failure."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", path],
            capture_output=True, text=True, timeout=30).stdout.strip()
        w, h = out.split("x")[:2]
        return int(w), int(h)
    except Exception:
        return 0, 0


def _blur_variance(img_path: str) -> float | None:
    """Variance of the Laplacian (a standard focus/sharpness measure). Higher =
    sharper. None if OpenCV is unavailable or the image can't be read."""
    try:
        import cv2  # type: ignore
        im = cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)
        if im is None:
            return None
        return float(cv2.Laplacian(im, cv2.CV_64F).var())
    except Exception:
        return None


def quality_score(path: str) -> dict:
    """Heuristic quality of a REAL media file → the input for the Fidelity auto-suggest
    (docs/REAL_MEDIA_QUALITY_LADDER.md §6). Combines resolution (ffprobe) and sharpness
    (Laplacian variance, OpenCV). Non-fatal: degrades to score=None ('unknown') so the
    selector still works without OpenCV/ffprobe. Returns:
      {score: 0-1|None, lowres, blurry, short_side, blur, reason}."""
    if not path or not os.path.isfile(path):
        return {"score": None, "lowres": False, "blurry": False,
                "short_side": 0, "blur": None, "reason": "no media"}
    ext = os.path.splitext(path)[1].lower()
    w, h = _probe_resolution(path)
    short_side = min(w, h) if (w and h) else 0
    lowres = bool(short_side) and short_side < 720

    blur = None
    if ext in IMAGE_EXTS:
        blur = _blur_variance(path)
    elif ext in VIDEO_EXTS:
        d = tempfile.mkdtemp(prefix="hob_q_")
        try:
            fr = os.path.join(d, "f.jpg")
            subprocess.run(["ffmpeg", "-y", "-ss", "00:00:01", "-i", path,
                            "-frames:v", "1", fr], capture_output=True, timeout=40)
            if os.path.exists(fr):
                blur = _blur_variance(fr)
        except Exception:
            pass
        finally:
            shutil.rmtree(d, ignore_errors=True)

    blurry = blur is not None and blur < 80.0      # empirical: <80 reads soft at 9:16
    score, reasons = 1.0, []
    if lowres:
        score -= 0.4; reasons.append(f"low-res ({short_side}p)")
    elif short_side and short_side < 1080:
        score -= 0.15; reasons.append(f"{short_side}p")
    if blurry:
        score -= 0.4; reasons.append("soft/blurry")
    elif blur is not None and blur < 150:
        score -= 0.15; reasons.append("slightly soft")
    # No resolution AND no blur read → we genuinely don't know; signal unknown.
    if not short_side and blur is None:
        return {"score": None, "lowres": False, "blurry": False,
                "short_side": 0, "blur": None, "reason": "couldn't assess"}
    score = max(0.0, round(score, 2))
    return {"score": score, "lowres": lowres, "blurry": blurry,
            "short_side": short_side, "blur": blur,
            "reason": ", ".join(reasons) if reasons else "looks clean"}


def restore_file(in_path: str, out_dir: str, **kw) -> str:
    """Restore one media file. Returns the restored path, or the ORIGINAL on any
    failure / unsupported type (so callers can use the result unconditionally)."""
    if not in_path or not os.path.isfile(in_path):
        return in_path
    ext = os.path.splitext(in_path)[1].lower()
    if ext not in IMAGE_EXTS | VIDEO_EXTS:
        return in_path
    base = os.path.splitext(os.path.basename(in_path))[0]
    out_ext = ext if ext in VIDEO_EXTS else ".jpg"
    out_path = os.path.join(out_dir, f"{base}_restored{out_ext}")
    try:
        os.makedirs(out_dir, exist_ok=True)
        if ext in VIDEO_EXTS:
            return restore_video(in_path, out_path, **kw)
        return restore_image(in_path, out_path, **kw)
    except Exception as e:
        print(f"[Restore] {os.path.basename(in_path)} skipped ({e}) — using original")
        return in_path
