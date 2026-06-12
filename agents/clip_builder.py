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

from agents import model_router
from agents.cache_store import BlobCache

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".heif"}
HEIC_EXTS  = {".heic", ".heif"}

KLING_API_BASE  = "https://api.klingai.com"
KLING_POLL_SEC  = 5
KLING_TIMEOUT   = 360

# Persistent clip cache — reused across runs; FS by default, S3-backed when
# HOB_CACHE_BACKEND=s3 so paid clips survive container redeploys (see cache_store).
_CLIP_CACHE = BlobCache("kling_clips", ext=".mp4", min_bytes=10_000)
CLIP_CACHE_DIR = _CLIP_CACHE.local_dir   # back-compat: still logged at startup


def _clip_cache_key(image_path: str, motion_prompt: str, duration: float) -> str:
    """MD5 of image bytes + motion prompt + duration = unique clip identity."""
    h = hashlib.md5()
    with open(image_path, "rb") as f:
        h.update(f.read())
    h.update(motion_prompt.encode())
    h.update(str(round(duration, 1)).encode())
    return h.hexdigest()


def _cache_lookup(key: str) -> str | None:
    return _CLIP_CACHE.local_path(key)


def _resolve_model_id(item: dict, provider: str, kling_mode: str) -> str:
    """
    Resolve the video model id for an image frame. A router-supplied
    item["model_id"] wins; otherwise fall back to the legacy global provider so
    existing callers keep working. Returns "" for Ken Burns (no AI).
    """
    mid = (item.get("model_id") or "").strip().lower()
    if mid and mid not in ("auto", "passthrough"):
        return mid
    if provider == "higgsfield":
        return "higgsfield"
    if provider == "kenburns":
        return ""
    return "kling_pro" if kling_mode == "pro" else "kling_std"


def _model_cache_key(model_id: str, media: str, motion: str,
                     duration: float, force_5s: bool) -> str:
    """
    Clip cache key, namespaced by model so switching models never returns the
    wrong clip. Legacy formats are preserved for kling/higgsfield so previously
    generated clips (paid credits) still hit cache and are not re-spent.
    """
    suffix = "_5s" if force_5s else ""
    if model_id == "higgsfield":
        # v2: clips are now trimmed to the requested duration; v1 entries could
        # hold an untrimmed 5s clip under a shorter-duration key, so they must miss.
        token = f"hf2_{motion}"
    elif model_id in ("kling_std", "kling_pro"):
        token = motion + suffix                # legacy — preserves existing cache (always trimmed)
    else:
        token = f"{model_id}|v2|{motion}{suffix}"  # fal models — v2: trimmed to duration
    return _clip_cache_key(media, token, duration)


def _cache_store(key: str, clip_path: str):
    _CLIP_CACHE.put(key, clip_path)


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
    # Cross-platform HEIC decode via pillow-heif — Linux/containers have no
    # macOS `sips`. Fall back to `sips` on a Mac dev box without pillow-heif.
    try:
        from PIL import Image, ImageOps
        import pillow_heif
        pillow_heif.register_heif_opener()
        img = ImageOps.exif_transpose(Image.open(heic_path))
        img.convert("RGB").save(out, "JPEG", quality=95)
        return out
    except Exception:
        subprocess.run(["sips", "-s", "format", "jpeg", heic_path, "--out", out],
                       check=True, capture_output=True)
        return out


def _face_center_x(image_path: str, img_w: int) -> int | None:
    """Return x-center of the largest detected face, or None (cv2 optional)."""
    try:
        import cv2
        img = cv2.imread(image_path)
        if img is None:
            return None
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
        faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(40, 40))
        if len(faces) == 0:
            return None
        x, _, w, _ = max(faces, key=lambda r: r[2] * r[3])  # largest face
        return x + w // 2
    except ImportError:
        return None


def prepare_image(image_path: str, temp_dir: str, target_w: int, target_h: int) -> str:
    """
    Fix EXIF orientation AND smart-crop landscape images to portrait before Kling.
    Crop window is centered on the detected face; falls back to center-crop if
    no face is found or cv2 is not installed.
    """
    try:
        from PIL import Image, ImageOps
        img = Image.open(image_path)
        img = ImageOps.exif_transpose(img)

        iw, ih = img.size
        if iw / ih <= 1.0:
            return image_path  # already portrait or square — pass through

        target_ratio = target_w / target_h  # e.g. 0.5625 for 9:16
        new_w = int(ih * target_ratio)

        # Try face-aware crop; fall back to blind center crop
        face_cx = _face_center_x(image_path, iw)
        if face_cx is not None:
            left = max(0, min(iw - new_w, face_cx - new_w // 2))
            print(f"[ClipBuilder] Face-aware crop: face at x={face_cx}, window [{left}:{left+new_w}]")
        else:
            left = (iw - new_w) // 2

        img = img.crop((left, 0, left + new_w, ih))
        stem = Path(image_path).stem
        out = str(Path(temp_dir) / f"{stem}_portrait.jpg")
        img.convert("RGB").save(out, "JPEG", quality=95)
        return out
    except Exception:
        return image_path


def _get_video_duration(video_path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", video_path],
        capture_output=True, text=True
    )
    try:
        return max(0.0, float(result.stdout.strip()))
    except (ValueError, AttributeError):
        return 0.0


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
                width: int, height: int, fps: int = 30, start_sec: float = 0.0):
    """Trim/normalize a video to target duration. Extends with freeze-frame if source is shorter."""
    import shutil
    vf = (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
          f"crop={width}:{height},setsar=1")

    src_dur = _get_video_duration(video_path)
    available = max(0.0, src_dur - start_sec) if src_dur > 0 else duration
    trim_sec = min(duration, available) if available > 0 else duration

    trimmed = output_path.replace(".mp4", "_trimtmp.mp4")
    _run([
        "ffmpeg", "-y", "-i", video_path,
        "-ss", str(start_sec), "-t", str(trim_sec),
        "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-r", str(fps), "-preset", "fast", trimmed,
    ])

    if src_dur > 0 and available < duration - 0.1:
        print(f"[ClipBuilder] Raw video ({src_dur:.1f}s) shorter than frame ({duration:.1f}s) — extending with freeze-frame.")
        _extend_clip(trimmed, duration, output_path, width, height, fps)
        try:
            os.remove(trimmed)
        except Exception:
            pass
    else:
        shutil.move(trimmed, output_path)


def _kling_headers() -> dict:
    return {
        "Authorization": f"Bearer {_kling_jwt()}",
        "Content-Type": "application/json",
    }


def _image_to_base64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _kling_motion_prompt(segment_text: str, motion_prompt: str = "") -> str:
    # Image-to-video prompts must describe MOTION only — the reference image
    # already carries appearance/lighting, and re-describing the subject in text
    # (e.g. echoing the caption) creates competing instructions and drift.
    if motion_prompt:
        return motion_prompt
    return ("Slow gentle push-in, natural ambient movement — hair, fabric and "
            "foliage stirring softly, subtle light shift, cinematic and calm")


# Map our plain-English camera vocabulary → Kling's structured camera_control.
# Kling "simple" type takes a config with exactly the 6 axes; only one is non-zero.
# Named types (left/right_turn_forward, forward_up, down_back) are pre-baked moves.
def _kling_camera_control(motion_prompt: str):
    """Return a Kling camera_control dict for a known move, or None for ambient/unknown."""
    p = (motion_prompt or "").lower()

    def simple(**kw):
        cfg = {"horizontal": 0, "vertical": 0, "pan": 0, "tilt": 0, "roll": 0, "zoom": 0}
        cfg.update(kw)
        return {"type": "simple", "config": cfg}

    rules = [
        (["crash zoom in"],                                          simple(zoom=10)),
        (["crash zoom out"],                                         simple(zoom=-10)),
        (["zoom in", "dolly in", "push in", "push-in", "move toward",
          "approach", "close up", "close-up"],                       simple(zoom=6)),
        (["zoom out", "dolly out", "pull back", "pull-back",
          "pull out", "reveal", "wide"],                             simple(zoom=-6)),
        (["crane up", "jib up", "rise", "lift"],                     simple(vertical=6)),
        (["crane down", "jib down", "lower", "descend"],             simple(vertical=-6)),
        (["tilt up", "look up"],                                     simple(tilt=6)),
        (["tilt down", "look down", "overhead"],                     simple(tilt=-6)),
        (["dutch", "roll"],                                          simple(roll=6)),
        (["arc left", "orbit left", "pan left", "turn left"],        {"type": "left_turn_forward"}),
        (["arc right", "orbit right", "pan right", "turn right",
          "360", "orbit"],                                           {"type": "right_turn_forward"}),
        (["forward up"],                                             {"type": "forward_up"}),
        (["down back"],                                              {"type": "down_back"}),
    ]
    for keywords, control in rules:
        if any(k in p for k in keywords):
            return control
    return None  # static / ambient / unrecognized → no structured camera move


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

    base_payload = {
        "model_name": "kling-v3",
        "image": _image_to_base64(image_path),
        "prompt": _kling_motion_prompt(segment_text, motion_prompt),
        "negative_prompt": "blurry, distorted, text, watermark, subtitles, captions, logo, "
                           "low quality, static, morphing faces, extra limbs, flickering",
        "cfg_scale": 0.5,
        "mode": kling_mode,
        "duration": kling_dur,
        "aspect_ratio": aspect,
    }

    # Use Kling's structured camera_control when the motion maps to a known move.
    # If Kling rejects it (schema/version/mode mismatch → HTTP 400, which is NOT
    # billed), retry once WITHOUT camera_control so the clip still renders via the
    # text prompt. This never wastes credits on a bad camera_control request.
    camera = _kling_camera_control(motion_prompt)
    attempts = []
    if camera:
        attempts.append({**base_payload, "camera_control": camera})
    attempts.append(base_payload)

    last_err = ""
    for idx, payload in enumerate(attempts):
        resp = requests.post(
            f"{KLING_API_BASE}/v1/videos/image2video",
            headers=_kling_headers(),
            json=payload,
            timeout=30,
        )
        if resp.ok:
            if camera and idx == 0:
                print(f"[Kling] camera_control applied: {camera.get('type')}")
            elif camera:
                print(f"[Kling] camera_control rejected — using prompt-only motion")
            return resp.json()["data"]["task_id"]
        last_err = f"{resp.status_code}: {resp.text[:200]}"
        # Only fall through to the no-camera attempt on a 4xx (bad request / not billed)
        if resp.status_code >= 500:
            break

    raise RuntimeError(f"Kling submit failed {last_err}")


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


def _fit_clip_to_duration(raw_path: str, duration: float, output_path: str,
                          width: int, height: int, fps: int = 30):
    """
    Make a downloaded provider clip exactly `duration` long: extend short clips
    with a Ken Burns freeze, TRIM long ones (providers like Higgsfield return a
    fixed 5s regardless of the request — coverage sub-shots are 2.5–4s), and
    move as-is when already within tolerance. Removes raw_path.
    """
    import shutil
    raw_dur = _get_video_duration(raw_path)
    if raw_dur and duration > raw_dur + 0.1:
        _extend_clip(raw_path, duration, output_path, width, height, fps)
        os.remove(raw_path)
    elif raw_dur and raw_dur > duration + 0.15:
        _video_trim(raw_path, duration, output_path, width, height, fps)
        os.remove(raw_path)
    else:
        shutil.move(raw_path, output_path)


def _build_one_clip(item: dict, temp_dir: str, width: int, height: int,
                    fps: int, use_kling: bool, force_5s: bool = False,
                    kling_mode: str = "pro", provider: str = "kling") -> dict:
    """Build a single clip — used in parallel execution."""
    seg_id    = item["segment_id"]
    duration  = item["actual_duration"]
    clip_path = str(Path(temp_dir) / f"clip_{seg_id}.mp4")
    media     = item["media_path"]
    ext       = os.path.splitext(media)[1].lower()
    is_image  = ext in IMAGE_EXTS
    motion    = item.get("motion_prompt", "")

    if ext in HEIC_EXTS:
        media = heic_to_jpeg(media, temp_dir)

    if is_image:
        media = prepare_image(media, temp_dir, width, height)

    # ── Lip sync clip_ready bypass ───────────────────────────────────────────
    # Lipsync coordinator already produced a finished clip — skip all animation
    if item.get("clip_ready") and item.get("lipsync_clip_path"):
        import shutil as _shutil
        _shutil.copy2(item["lipsync_clip_path"], clip_path)
        print(f"[ClipBuilder] {seg_id} → clip_ready (lipsync, skipping animation)")
        return {**item, "clip_path": clip_path, "has_lipsync_audio": True}

    # ── AI image-to-video (model chosen per shot by the router) ───────────────
    if is_image:
        model_id = _resolve_model_id(item, provider, kling_mode)
        backend  = model_router.model_field(model_id, "backend") if model_id else ""
        aspect   = "9:16" if height > width else ("16:9" if width > height else "1:1")

        # Shared clip cache (paid clips reused for free) — namespaced by model.
        if backend in ("kling", "higgsfield", "fal"):
            cache_key = _model_cache_key(model_id, media, motion, duration, force_5s)
            cached = _cache_lookup(cache_key)
            if cached:
                import shutil
                shutil.copy2(cached, clip_path)
                print(f"[Cache] ✓ {seg_id} — reused {model_id} cached clip (₹0)")
                return {**item, "clip_path": clip_path, "cached": True}

        # ── Higgsfield (deferred to throttled pool; 4-concurrent limit) ───────
        if backend == "higgsfield":
            item["_clip_path"]   = clip_path
            item["_cache_key"]   = cache_key
            item["_media"]       = media
            item["_provider"]    = "higgsfield"
            item["_model_id"]    = model_id
            item["_hf_prompt"]   = _kling_motion_prompt(item.get("text", ""), motion)
            item["_hf_aspect"]   = aspect
            item["_hf_deferred"] = True
            return {**item, "clip_path": clip_path, "pending": True}

        # ── fal.ai video models (Seedance, Veo, Hailuo) — deferred to pool ────
        if backend == "fal":
            item["_clip_path"]    = clip_path
            item["_cache_key"]    = cache_key
            item["_media"]        = media
            item["_provider"]     = "fal"
            item["_model_id"]     = model_id
            item["_fal_prompt"]   = _kling_motion_prompt(item.get("text", ""), motion)
            item["_fal_aspect"]   = aspect
            item["_fal_duration"] = 5 if force_5s else max(3, min(15, round(duration)))
            item["_fal_deferred"] = True
            return {**item, "clip_path": clip_path, "pending": True}

        # ── Kling (DEFERRED to the throttled pool) ───────────────────────────
        # Submission is deferred (like Higgsfield/fal) so we never fire more
        # parallel submits than the account's resource-pack limit. Submitting
        # all frames at once previously triggered 429 code 1303
        # ("parallel task over resource pack limit") on the extra frames →
        # Ken Burns. The pool caps in-flight Kling jobs at max_concurrent and
        # retries 1303 instead of dropping to a static fallback.
        if backend == "kling":
            kmode = model_router.model_field(model_id, "kling_mode", kling_mode)
            item["_clip_path"]      = clip_path
            item["_cache_key"]      = cache_key
            item["_media"]          = media
            item["_force_5s"]       = force_5s
            item["_kling_mode"]     = kmode
            item["_provider"]       = "kling"
            item["_model_id"]       = model_id
            item["_kling_text"]     = item.get("text", "")
            item["_kling_motion"]   = motion
            item["_kling_dur"]      = duration
            item["_kling_deferred"] = True
            return {**item, "clip_path": clip_path, "pending": True}

        # ── Ken Burns (no AI model) ──────────────────────────────────────────
        _kenburns(media, duration, clip_path, width, height, fps)
        return {**item, "clip_path": clip_path}

    # ── Raw video trim ────────────────────────────────────────────────────────
    _video_trim(media, duration, clip_path, width, height, fps,
                start_sec=item.get("video_start_sec", 0.0))
    return {**item, "clip_path": clip_path}


def build_clips(assignments: list[dict], temp_dir: str,
                width: int, height: int, fps: int = 30,
                force_5s: bool = False, kling_mode: str = "pro",
                provider: str = "kling") -> list[dict]:
    use_kling = bool(os.environ.get("KLING_ACCESS_KEY") and os.environ.get("KLING_SECRET_KEY"))
    print(f"[ClipBuilder] Provider: {provider} (auto-routed per shot) | "
          f"tier: {'draft' if force_5s else 'premium'} | Cache: {CLIP_CACHE_DIR}")

    # Phase 1: Submit all AI tasks simultaneously, Ken Burns immediately
    clips, pending = [], []

    for item in assignments:
        result = _build_one_clip(item, temp_dir, width, height, fps,
                                 use_kling, force_5s, kling_mode, provider)
        if result.get("pending"):
            pending.append(result)
        else:
            clips.append(result)
            if not result.get("cached"):
                print(f"[ClipBuilder] {result['segment_id']} → {result['clip_path']} ({result['actual_duration']:.1f}s)")

    # Phase 2: Poll all pending tasks in parallel (Kling + Higgsfield mixed)
    if pending:
        print(f"[ClipBuilder] Polling {len(pending)} pending tasks in parallel…")

        def poll_one(item):
            prov = item.get("_provider", "kling")
            try:
                if prov == "higgsfield":
                    from agents import higgsfield as hf
                    # Submit here (inside the capped pool) so we never exceed
                    # Higgsfield's 4-concurrent limit. Retry briefly if a slot
                    # is momentarily full instead of dropping to Ken Burns.
                    if item.get("_hf_deferred"):
                        gen_id = None
                        for attempt in range(6):
                            try:
                                gen_id = hf.submit(item["_media"], item["_hf_prompt"],
                                                   aspect_ratio=item["_hf_aspect"])
                                break
                            except Exception as se:
                                if "concurrent" in str(se).lower() and attempt < 5:
                                    print(f"[Higgsfield] {item['segment_id']} waiting for a free slot…")
                                    time.sleep(20)
                                    continue
                                raise
                        item["_hf_gen_id"] = gen_id
                        print(f"[Higgsfield] Submitted {item['segment_id']} → {gen_id}")

                    raw = item["_clip_path"].replace(".mp4", "_hf_raw.mp4")
                    hf.poll_and_download(item["_hf_gen_id"], raw)
                    _fit_clip_to_duration(raw, item["actual_duration"],
                                          item["_clip_path"], width, height, fps)

                elif prov == "fal":
                    from agents import fal_video
                    # Submit inside the capped pool (respects per-model concurrency).
                    if item.get("_fal_deferred"):
                        item["_fal_handle"] = fal_video.submit(
                            item["_model_id"], item["_media"], item["_fal_prompt"],
                            aspect_ratio=item["_fal_aspect"], duration=item["_fal_duration"])
                    raw = item["_clip_path"].replace(".mp4", "_fal_raw.mp4")
                    fal_video.poll_and_download(item["_fal_handle"], raw)
                    _fit_clip_to_duration(raw, item["actual_duration"],
                                          item["_clip_path"], width, height, fps)

                else:
                    # Submit inside the capped pool so we never exceed Kling's
                    # parallel-task limit. On 429 code 1303 ("parallel task over
                    # resource pack limit") wait for a slot and retry instead of
                    # falling back to a static Ken Burns clip.
                    if item.get("_kling_deferred"):
                        task_id = None
                        for attempt in range(8):
                            try:
                                task_id = _kling_submit(
                                    item["_media"], item["_kling_text"], item["_kling_dur"],
                                    width, height, motion_prompt=item["_kling_motion"],
                                    force_5s=item.get("_force_5s", False),
                                    kling_mode=item.get("_kling_mode", "pro"),
                                )
                                break
                            except Exception as se:
                                m = str(se).lower()
                                limit_hit = ("1303" in m or "parallel task" in m
                                             or "resource pack limit" in m or "429" in m)
                                if limit_hit and attempt < 7:
                                    print(f"[Kling] {item['segment_id']} waiting for a free slot…")
                                    time.sleep(15)
                                    continue
                                raise
                        item["_kling_task_id"] = task_id
                        print(f"[Kling v3] Submitted {item['segment_id']} → task {task_id}")
                    _kling_poll_and_download(
                        item["_kling_task_id"], item["actual_duration"],
                        item["_clip_path"], width, height,
                        item["segment_id"], force_5s=item.get("_force_5s", False),
                    )

                _cache_store(item["_cache_key"], item["_clip_path"])
                return {**item, "clip_path": item["_clip_path"]}

            except Exception as e:
                print(f"[ClipBuilder] {prov} poll failed for {item['segment_id']} ({e}) — Ken Burns fallback")
                _kenburns(item["_media"], item["actual_duration"],
                          item["_clip_path"], width, height, fps)
                return {**item, "clip_path": item["_clip_path"]}

        # Throttle to the strictest concurrency among the models actually in use
        # (e.g. Higgsfield 4, Veo 2) so we never exceed a provider's limit.
        caps = [model_router.model_field(it.get("_model_id", ""), "max_concurrent", 8)
                for it in pending]
        max_workers = min(caps) if caps else len(pending)
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(pending)))) as executor:
            futures = {executor.submit(poll_one, item): item for item in pending}
            for future in as_completed(futures):
                result = future.result()
                clips.append(result)
                print(f"[ClipBuilder] {result['segment_id']} → done ({result['actual_duration']:.1f}s)")

    # Restore original order
    order = {item["segment_id"]: i for i, item in enumerate(assignments)}
    clips.sort(key=lambda c: order.get(c["segment_id"], 999))
    return clips
