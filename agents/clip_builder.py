import subprocess
import os
import time
import base64
import hashlib
import json
import requests
import jwt
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".heif"}
HEIC_EXTS  = {".heic", ".heif"}

KLING_API_BASE  = "https://api.klingai.com"
KLING_POLL_SEC  = 5
KLING_TIMEOUT   = 360

# Persistent cache directory — clips reused across runs
CLIP_CACHE_DIR = Path.home() / ".hob_cache" / "kling_clips"
CLIP_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _clip_cache_key(image_path: str, motion_prompt: str, duration: float) -> str:
    """MD5 of image bytes + motion prompt + duration = unique clip identity."""
    h = hashlib.md5()
    with open(image_path, "rb") as f:
        h.update(f.read())
    h.update(motion_prompt.encode())
    h.update(str(round(duration, 1)).encode())
    return h.hexdigest()


def _cache_lookup(key: str) -> str | None:
    cached = CLIP_CACHE_DIR / f"{key}.mp4"
    if cached.exists() and cached.stat().st_size > 10_000:
        return str(cached)
    return None


def _cache_store(key: str, clip_path: str):
    import shutil
    dest = str(CLIP_CACHE_DIR / f"{key}.mp4")
    shutil.copy2(clip_path, dest)


def _kling_jwt() -> str:
    access_key = os.environ["KLING_ACCESS_KEY"]
    secret_key  = os.environ["KLING_SECRET_KEY"]
    now = int(time.time())
    payload = {"iss": access_key, "exp": now + 1800, "nbf": now - 5}
    return jwt.encode(payload, secret_key, algorithm="HS256")


def _run(cmd: list[str]):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg error: {result.stderr[-1000:]}")


def heic_to_jpeg(heic_path: str, temp_dir: str) -> str:
    stem = Path(heic_path).stem
    out = str(Path(temp_dir) / f"{stem}_converted.jpg")
    subprocess.run(["sips", "-s", "format", "jpeg", heic_path, "--out", out],
                   check=True, capture_output=True)
    return out


def prepare_image(image_path: str, temp_dir: str, target_w: int, target_h: int) -> str:
    """
    Fix EXIF orientation AND center-crop landscape images to portrait aspect ratio
    before sending to Kling. Kling struggles with landscape input for 9:16 output.
    """
    try:
        from PIL import Image, ImageOps
        img = Image.open(image_path)
        img = ImageOps.exif_transpose(img)  # fix EXIF rotation first

        iw, ih = img.size
        current_ratio = iw / ih

        # Only center-crop landscape (wider than tall) images.
        # Square and portrait images let Kling handle the fit natively.
        if current_ratio <= 1.0:
            return image_path

        # Center-crop width to 9:16 portrait aspect ratio
        target_ratio = target_w / target_h  # e.g. 0.5625
        new_w = int(ih * target_ratio)
        left = (iw - new_w) // 2
        img = img.crop((left, 0, left + new_w, ih))

        stem = Path(image_path).stem
        out = str(Path(temp_dir) / f"{stem}_portrait.jpg")
        img.convert("RGB").save(out, "JPEG", quality=95)
        return out
    except Exception:
        return image_path


def _kenburns(image_path: str, duration: float, output_path: str,
              width: int, height: int, fps: int = 30):
    frames = max(1, int(duration * fps))
    scale_w, scale_h = width * 2, height * 2
    vf = (
        f"scale={scale_w}:{scale_h}:force_original_aspect_ratio=increase,"
        f"crop={scale_w}:{scale_h},"
        f"zoompan=z='min(zoom+0.0008,1.3)':d={frames}:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={width}x{height},"
        f"setsar=1"
    )
    _run([
        "ffmpeg", "-y",
        "-loop", "1", "-framerate", str(fps), "-i", image_path,
        "-vf", vf, "-t", str(duration),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps), "-preset", "fast",
        output_path,
    ])


def _video_trim(video_path: str, duration: float, output_path: str,
                width: int, height: int, fps: int = 30):
    vf = (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
          f"crop={width}:{height},setsar=1")
    _run([
        "ffmpeg", "-y", "-i", video_path, "-ss", "0", "-t", str(duration),
        "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-r", str(fps), "-preset", "fast", output_path,
    ])


def _kling_headers() -> dict:
    return {
        "Authorization": f"Bearer {_kling_jwt()}",
        "Content-Type": "application/json",
    }


def _image_to_base64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _kling_motion_prompt(segment_text: str, motion_prompt: str = "") -> str:
    if motion_prompt:
        return motion_prompt
    return (
        f"Cinematic slow motion, natural ambient movement, emotional atmosphere. "
        f"Subject: {segment_text[:80]}"
    )


def _kling_submit(image_path: str, segment_text: str, duration: float,
                  width: int, height: int, motion_prompt: str = "",
                  force_5s: bool = False, kling_mode: str = "pro") -> str:
    """Submit a task to Kling and return task_id immediately (non-blocking)."""
    aspect = "9:16" if height > width else ("16:9" if width > height else "1:1")
    # Kling v3 supports exact durations 3–15s; clamp and round to nearest int
    if force_5s:
        kling_dur = "5"
    else:
        kling_dur = str(max(3, min(15, round(duration))))

    payload = {
        "model_name": "kling-v3",
        "image": _image_to_base64(image_path),
        "prompt": _kling_motion_prompt(segment_text, motion_prompt),
        "negative_prompt": "blurry, distorted, text, watermark, subtitles, captions, logo, low quality, static",
        "cfg_scale": 0.5,
        "mode": kling_mode,
        "duration": kling_dur,
        "aspect_ratio": aspect,
    }

    resp = requests.post(
        f"{KLING_API_BASE}/v1/videos/image2video",
        headers=_kling_headers(),
        json=payload,
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"Kling submit failed {resp.status_code}: {resp.text[:300]}")

    task_id = resp.json()["data"]["task_id"]
    return task_id


def _extend_clip(kling_path: str, target_duration: float, output_path: str,
                  width: int, height: int, fps: int = 30):
    """
    Extend a Kling clip shorter than target_duration using Ken Burns on the last frame.
    Concat: original Kling clip + Ken Burns extension = exact target_duration.
    """
    kling_dur = float(subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", kling_path],
        capture_output=True, text=True
    ).stdout.strip() or "5")

    gap = target_duration - kling_dur
    if gap <= 0.1:
        import shutil
        shutil.copy2(kling_path, output_path)
        return

    # Extract last frame of Kling clip
    last_frame = output_path.replace(".mp4", "_lastframe.jpg")
    _run(["ffmpeg", "-y", "-sseof", "-0.1", "-i", kling_path,
          "-vframes", "1", "-q:v", "2", last_frame])

    # Ken Burns on last frame for the gap duration
    ext_path = output_path.replace(".mp4", "_ext.mp4")
    _kenburns(last_frame, gap, ext_path, width, height, fps)

    # Concat Kling + extension
    list_file = output_path.replace(".mp4", "_list.txt")
    with open(list_file, "w") as f:
        f.write(f"file '{os.path.abspath(kling_path)}'\n")
        f.write(f"file '{os.path.abspath(ext_path)}'\n")
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
          "-c", "copy", output_path])

    # Cleanup
    for p in [last_frame, ext_path, list_file]:
        try:
            os.remove(p)
        except Exception:
            pass


def _kling_poll_and_download(task_id: str, duration: float, output_path: str,
                              width: int, height: int, seg_id: str,
                              force_5s: bool = False) -> str:
    """Poll a submitted Kling task until complete, then download and trim."""
    elapsed = 0
    while elapsed < KLING_TIMEOUT:
        time.sleep(KLING_POLL_SEC)
        elapsed += KLING_POLL_SEC
        status_resp = requests.get(
            f"{KLING_API_BASE}/v1/videos/image2video/{task_id}",
            headers=_kling_headers(),
            timeout=15,
        )
        status_resp.raise_for_status()
        data = status_resp.json().get("data", {})
        task_status = data.get("task_status", "")

        if task_status == "succeed":
            video_url = data["task_result"]["videos"][0]["url"]
            video_resp = requests.get(video_url, timeout=120)
            raw_path = output_path.replace(".mp4", "_raw.mp4")
            with open(raw_path, "wb") as f:
                f.write(video_resp.content)
            if force_5s and duration > 5.5:
                # Trim Kling to 5s then extend with Ken Burns to target duration
                trimmed = output_path.replace(".mp4", "_5s.mp4")
                _video_trim(raw_path, min(5.0, duration), trimmed, width, height)
                _extend_clip(trimmed, duration, output_path, width, height)
                os.remove(trimmed)
            else:
                _video_trim(raw_path, duration, output_path, width, height)
            os.remove(raw_path)
            print(f"[Kling] ✓ {seg_id} complete")
            return output_path

        elif task_status == "failed":
            raise RuntimeError(f"Kling task failed: {data}")

        print(f"[Kling] {seg_id}: {task_status} ({elapsed}s)...")

    raise TimeoutError(f"Kling task {task_id} timed out after {KLING_TIMEOUT}s")


def _build_one_clip(item: dict, temp_dir: str, width: int, height: int,
                    fps: int, use_kling: bool, force_5s: bool = False,
                    kling_mode: str = "pro") -> dict:
    """Build a single clip — used in parallel execution."""
    seg_id   = item["segment_id"]
    duration = item["actual_duration"]
    clip_path = str(Path(temp_dir) / f"clip_{seg_id}.mp4")
    media     = item["media_path"]
    ext       = os.path.splitext(media)[1].lower()
    is_image  = ext in IMAGE_EXTS
    motion    = item.get("motion_prompt", "")

    if ext in HEIC_EXTS:
        media = heic_to_jpeg(media, temp_dir)

    if is_image:
        media = prepare_image(media, temp_dir, width, height)

    if is_image and use_kling:
        # Cache key includes force_5s flag so 5s and 10s versions are cached separately
        cache_suffix = "_5s" if force_5s else ""
        cache_key = _clip_cache_key(media, motion + cache_suffix, duration)
        cached = _cache_lookup(cache_key)
        if cached:
            import shutil
            shutil.copy2(cached, clip_path)
            kling_dur = 5 if force_5s else max(3, min(15, round(duration)))
            print(f"[Cache] ✓ {seg_id} — reused cached clip (₹0 / {kling_dur}s saved)")
            return {**item, "clip_path": clip_path, "cached": True}

        try:
            task_id = _kling_submit(media, item.get("text", ""), duration,
                                    width, height, motion_prompt=motion,
                                    force_5s=force_5s, kling_mode=kling_mode)
            kling_dur = 5 if force_5s else max(3, min(15, round(duration)))
            print(f"[Kling v3] Submitted {seg_id} → task {task_id} ({kling_dur}s, {kling_mode} mode)")
            item["_kling_task_id"] = task_id
            item["_clip_path"] = clip_path
            item["_cache_key"] = cache_key
            item["_media"] = media
            item["_force_5s"] = force_5s
            item["_kling_mode"] = kling_mode
            return {**item, "clip_path": clip_path, "pending": True}
        except Exception as e:
            print(f"[ClipBuilder] Kling submit failed ({e}) — Ken Burns fallback")
            _kenburns(media, duration, clip_path, width, height, fps)

    elif is_image:
        _kenburns(media, duration, clip_path, width, height, fps)
    else:
        _video_trim(media, duration, clip_path, width, height, fps)

    return {**item, "clip_path": clip_path}


def build_clips(assignments: list[dict], temp_dir: str,
                width: int, height: int, fps: int = 30,
                force_5s: bool = False, kling_mode: str = "pro") -> list[dict]:
    temp_path = Path(temp_dir)
    use_kling = bool(os.environ.get("KLING_ACCESS_KEY") and os.environ.get("KLING_SECRET_KEY"))
    mode = "5s+extend" if force_5s else "full duration"
    provider = f"Kling AI (parallel, {mode}, {kling_mode})" if use_kling else "Ken Burns (FFmpeg)"
    print(f"[ClipBuilder] Provider: {provider}  |  Cache: {CLIP_CACHE_DIR}")

    # Phase 1: Submit ALL Kling tasks simultaneously (parallel), Ken Burns immediately
    clips = []
    pending = []

    for item in assignments:
        result = _build_one_clip(item, temp_dir, width, height, fps, use_kling, force_5s, kling_mode)
        if result.get("pending"):
            pending.append(result)
        else:
            clips.append(result)
            if not result.get("cached"):
                print(f"[ClipBuilder] {result['segment_id']} → {result['clip_path']} ({result['actual_duration']:.1f}s)")

    # Phase 2: Poll all pending Kling tasks in parallel
    if pending:
        print(f"[ClipBuilder] Polling {len(pending)} Kling tasks in parallel...")

        def poll_one(item):
            try:
                _kling_poll_and_download(
                    item["_kling_task_id"],
                    item["actual_duration"],
                    item["_clip_path"],
                    width, height,
                    item["segment_id"],
                    force_5s=item.get("_force_5s", False),
                )
                # Store in cache
                _cache_store(item["_cache_key"], item["_clip_path"])
                return {**item, "clip_path": item["_clip_path"]}
            except Exception as e:
                print(f"[ClipBuilder] Kling poll failed for {item['segment_id']} ({e}) — Ken Burns fallback")
                _kenburns(item["_media"], item["actual_duration"],
                          item["_clip_path"], width, height, fps)
                return {**item, "clip_path": item["_clip_path"]}

        with ThreadPoolExecutor(max_workers=len(pending)) as executor:
            futures = {executor.submit(poll_one, item): item for item in pending}
            for future in as_completed(futures):
                result = future.result()
                clips.append(result)
                print(f"[ClipBuilder] {result['segment_id']} → done ({result['actual_duration']:.1f}s)")

    # Restore original order
    order = {item["segment_id"]: i for i, item in enumerate(assignments)}
    clips.sort(key=lambda c: order.get(c["segment_id"], 999))

    return clips
