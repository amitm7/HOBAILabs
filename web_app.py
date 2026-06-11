"""
HOBAILabs Internal Web UI
Run: ~/.pyenv/versions/3.12.3/bin/python3.12 web_app.py
Open: http://localhost:7860
"""
import contextlib
import json
import os
import shutil
import sys
import tempfile
import threading
import uuid
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, send_file

load_dotenv(override=True)

app = Flask(__name__, template_folder="web/templates", static_folder="web/static")
# Per-request cap. Folder uploads are sent in small size-batched chunks by the
# client (see main.js), so each request stays well under this; the ceiling is
# generous headroom for a single large video.
app.config["MAX_CONTENT_LENGTH"] = 256 * 1024 * 1024  # 256MB per request
app.config["MAX_FORM_PARTS"] = 5000

RUNS_DIR = Path(tempfile.gettempdir()) / "hob_runs"
RUNS_DIR.mkdir(parents=True, exist_ok=True)

# Typed asset paths, the folder browser, and /media file serving are all confined
# to this root (plus RUNS_DIR). Set ASSETS_BROWSE_ROOT on hosted deploys to the
# directory that holds user asset folders, e.g. /srv/hob/assets.
ASSETS_BROWSE_ROOT = Path(os.environ.get("ASSETS_BROWSE_ROOT", str(Path.home()))).resolve()


def _path_allowed(p: str) -> bool:
    """True when p resolves inside RUNS_DIR or ASSETS_BROWSE_ROOT."""
    try:
        rp = Path(p).resolve()
    except (OSError, ValueError):
        return False
    return any(rp == root or rp.is_relative_to(root)
               for root in (RUNS_DIR.resolve(), ASSETS_BROWSE_ROOT))

# In-memory run state: run_id → {status, log, output_path}
_runs: dict[str, dict] = {}
_runs_lock = threading.Lock()

MOOD_MAP = {
    "warm nostalgic": "warm amber tones, golden hour side-light, slightly desaturated vintage feel",
    "cold struggle": "cool blue-grey palette, overcast diffused light, high contrast deep shadows",
    "triumphant": "rich warm golds and saffron, directional sunlight, high saturation, hopeful energy",
}


# ── Log capture ──────────────────────────────────────────────────────────────

class _LogCapture:
    """Redirects print() calls into the run's log list and also to terminal."""
    def __init__(self, run_id: str):
        self._run_id = run_id

    def write(self, text: str):
        stripped = text.rstrip()
        if stripped:
            with _runs_lock:
                _runs[self._run_id]["log"].append(stripped)
        sys.__stdout__.write(text)

    def flush(self):
        sys.__stdout__.flush()


# ── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/parse-script", methods=["POST"])
def parse_script():
    script_text = request.json.get("script", "")
    assets_dir  = request.json.get("assets_dir", "").strip()
    if not script_text.strip():
        return jsonify({"frames": []})

    # Validate assets folder if provided
    if assets_dir and not os.path.isdir(assets_dir):
        return jsonify({"error": f"Assets folder not found: {assets_dir}"}), 400
    if assets_dir and not _path_allowed(assets_dir):
        return jsonify({"error": f"Assets folder must be inside {ASSETS_BROWSE_ROOT}"}), 403

    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    tmp.write(script_text)
    tmp.close()

    try:
        from agents.script_parser import parse_frame_script
        smart_match = bool(request.json.get("smart_match", False))
        frames = parse_frame_script(tmp.name, assets_dir or "", smart_match=smart_match)
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    finally:
        os.unlink(tmp.name)

    # Resolve explicit filename photo_specs to visual_path using assets_dir
    for f in frames:
        spec = f.get("photo_spec", "")
        if spec and not spec.startswith("ai_") and not f.get("visual_path") and assets_dir:
            candidate = os.path.join(assets_dir, spec)
            if os.path.exists(candidate):
                f["visual_path"] = candidate

    # Auto-direct: choose a camera move per frame so the user sees the plan.
    # Only fills frames where the user did NOT already write [camera:]/[motion:].
    from agents.auto_director import suggest_camera
    total = len(frames)
    for i, f in enumerate(frames):
        f["camera_auto"] = False
        if not f.get("motion_override"):
            move, reason = suggest_camera(
                f.get("caption", ""), i, total,
                f.get("photo_spec", ""), f.get("visual_path", ""),
            )
            if move:
                f["motion_override"] = move
                f["camera_auto"]     = True
                f["camera_reason"]   = reason

    # Return everything the UI needs — including all parsed annotations so the
    # frame cards pre-fill camera motion, edits, lip sync, and voice from the script.
    result = []
    for f in frames:
        result.append({
            "frame_id":       f["frame_id"],
            "caption":        f.get("caption", ""),
            "duration":       round(f.get("duration", 5.0), 1),
            "photo_spec":     f.get("photo_spec", ""),
            "visual_path":    f.get("visual_path", ""),
            "director_note":  f.get("director_note", ""),
            "motion_override": f.get("motion_override", ""),
            "camera_auto":    f.get("camera_auto", False),
            "camera_reason":  f.get("camera_reason", ""),
            "edit_prompt":    f.get("edit_prompt", ""),
            "lipsync":        bool(f.get("lipsync", False)),
            "voice_override": f.get("voice_override", ""),
            "video_start_sec": f.get("video_start_sec", 0.0),
        })
    return jsonify({"frames": result})


@app.route("/media")
def serve_media():
    """
    Serve a matched local photo/video so the UI can show a thumbnail.
    Safety: only serves image/video files inside RUNS_DIR or ASSETS_BROWSE_ROOT —
    never arbitrary filesystem paths.
    """
    path = request.args.get("path", "")
    if not path or not _path_allowed(path):
        return "Forbidden", 403
    if not os.path.isfile(path):
        return "Not found", 404
    ext = os.path.splitext(path)[1].lower()
    allowed = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".heif",
               ".mp4", ".mov", ".avi", ".m4v", ".webm"}
    if ext not in allowed:
        return "Unsupported type", 415
    return send_file(path)


@app.route("/browse-dirs")
def browse_dirs():
    """List subfolders + media count under ASSETS_BROWSE_ROOT for the folder picker."""
    req = request.args.get("path", "") or str(ASSETS_BROWSE_ROOT)
    try:
        cur = Path(req).resolve()
    except (OSError, ValueError):
        return jsonify({"error": "bad path"}), 400
    if not (cur == ASSETS_BROWSE_ROOT or cur.is_relative_to(ASSETS_BROWSE_ROOT)):
        return jsonify({"error": "outside the allowed root"}), 403
    if not cur.is_dir():
        return jsonify({"error": "not a folder"}), 404

    dirs, media_count = [], 0
    try:
        for entry in sorted(os.scandir(cur), key=lambda e: e.name.lower()):
            if entry.name.startswith("."):
                continue
            if entry.is_dir(follow_symlinks=False):
                dirs.append(entry.name)
            elif os.path.splitext(entry.name)[1].lower() in _MEDIA_UPLOAD_EXTS:
                media_count += 1
    except OSError as e:
        return jsonify({"error": str(e)}), 400

    parent = str(cur.parent) if cur != ASSETS_BROWSE_ROOT else ""
    return jsonify({"path": str(cur), "parent": parent,
                    "dirs": dirs, "media_count": media_count})


@app.route("/upload-photo", methods=["POST"])
def upload_photo():
    session_id = request.form.get("session_id", str(uuid.uuid4()))
    frame_id = request.form.get("frame_id", "unknown")
    file = request.files.get("photo")
    if not file:
        return jsonify({"error": "no file"}), 400

    assets_dir = RUNS_DIR / session_id / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(file.filename).suffix.lower() or ".jpg"
    filename = f"{frame_id}_upload{ext}"
    save_path = assets_dir / filename
    file.save(str(save_path))

    return jsonify({"tmp_path": str(save_path), "session_id": session_id})


_MEDIA_UPLOAD_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".heif",
                      ".mp4", ".mov", ".avi", ".m4v", ".webm"}


@app.route("/upload-folder", methods=["POST"])
def upload_folder():
    """Upload a whole local folder of photos/videos (browser 'webkitdirectory').

    Saves the media into this session's assets dir and returns that server-side
    path, which the existing parse-script / generate flow then matches against.
    This replaces typing a server path — required now the app is hosted, since
    the server cannot see a user's local disk. Subfolders are flattened to their
    base filename; non-media files are ignored.
    """
    session_id = request.form.get("session_id", str(uuid.uuid4()))
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "no files"}), 400

    assets_dir = RUNS_DIR / session_id / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    saved = 0
    for f in files:
        name = os.path.basename((f.filename or "").replace("\\", "/"))
        if not name or os.path.splitext(name)[1].lower() not in _MEDIA_UPLOAD_EXTS:
            continue
        f.save(str(assets_dir / name))
        saved += 1

    if not saved:
        return jsonify({"error": "no images or videos found in that folder"}), 400
    return jsonify({"assets_dir": str(assets_dir), "session_id": session_id, "count": saved})


@app.route("/pricing")
def get_pricing():
    try:
        from agents.pricing import load as load_pricing
        return jsonify(load_pricing())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/models")
def list_models():
    """Model catalog + routing policy for the UI dropdowns and cost estimate."""
    try:
        from agents import model_router
        return jsonify(model_router.catalog())
    except Exception as e:
        return jsonify({"error": str(e), "models": {}}), 500


@app.route("/api/estimate", methods=["POST"])
def api_estimate():
    """
    Server-side cost estimate from the UI payload. Single source of truth:
    agents/pricing.estimate() + the real model router — the UI only renders the
    returned breakdown, so prices and routing can never drift from billing.
    """
    data = request.json or {}
    try:
        quality = data.get("quality", "dev")
        max_frame_dur = 5.0 if quality == "dev" else 9.0
        frames = _build_frames_from_payload(data, max_frame_dur)
        music_type = data.get("music_type", "none")
        voice_chars = (sum(len(f.get("caption") or "") for f in frames
                           if not f.get("lipsync"))
                       if music_type == "voiceover" else 0)
        from agents import model_router
        from agents.pricing import estimate
        video_model = data.get("video_model", "auto") or "auto"
        b = estimate(
            frames,
            force_5s=(quality == "dev"),
            music_type=music_type,
            voice_chars=voice_chars,
            provider=("kenburns" if video_model == "kenburns" else "kling"),
            cost_tier=model_router.cost_tier_from_quality(quality),
            image_model=data.get("image_model", "auto") or "auto",
            video_model=video_model,
            multi_shot=bool(data.get("multi_shot")),
        )
        b["multi_shot"] = bool(data.get("multi_shot"))
        return jsonify(b)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/voices")
def list_voices():
    try:
        from agents.tts_generator import get_voices
        return jsonify({"voices": get_voices()})
    except Exception as e:
        return jsonify({"error": str(e), "voices": []}), 500


@app.route("/generate-music", methods=["POST"])
def generate_music_route():
    data = request.json or {}
    prompt = data.get("prompt", "Emotional Bollywood instrumental, struggle to triumph")
    session_id = data.get("session_id", str(uuid.uuid4()))

    run_dir = RUNS_DIR / session_id
    run_dir.mkdir(parents=True, exist_ok=True)
    music_path = str(run_dir / "music.mp3")

    try:
        from agents.music_generator import generate_story_music
        generate_story_music(prompt, music_path)
        return jsonify({"music_path": music_path, "session_id": session_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _check_assets_dir(data: dict):
    """Reject payloads whose typed assets_dir escapes the allowed roots."""
    assets_dir = (data.get("assets_dir") or "").strip()
    if assets_dir and not _path_allowed(assets_dir):
        return jsonify({"error": f"Assets folder must be inside {ASSETS_BROWSE_ROOT}"}), 403
    return None


@app.route("/run", methods=["POST"])
def run_pipeline():
    data = request.json or {}
    err = _check_assets_dir(data)
    if err:
        return err
    session_id = data.get("session_id", str(uuid.uuid4()))

    run_dir = RUNS_DIR / session_id
    run_dir.mkdir(parents=True, exist_ok=True)

    with _runs_lock:
        _runs[session_id] = {"status": "running", "log": [], "output_path": None}

    thread = threading.Thread(
        target=_execute_pipeline,
        args=(session_id, data, run_dir),
        daemon=True,
    )
    thread.start()

    return jsonify({"run_id": session_id})


@app.route("/preview", methods=["POST"])
def preview_stills():
    """
    Generate only the STILL images (cheap) — no animation, no assembly.
    Lets the user see every image and add edits before paying for Kling/Higgsfield.
    Generated stills are cached in the session folder, so the later full render
    reuses them and only pays for animation.
    """
    data = request.json or {}
    err = _check_assets_dir(data)
    if err:
        return err
    session_id = data.get("session_id", str(uuid.uuid4()))

    run_dir = RUNS_DIR / session_id
    run_dir.mkdir(parents=True, exist_ok=True)

    with _runs_lock:
        _runs[session_id] = {"status": "running", "log": [], "stills": None}

    thread = threading.Thread(
        target=_execute_preview, args=(session_id, data, run_dir), daemon=True,
    )
    thread.start()
    return jsonify({"run_id": session_id})


@app.route("/preview-result/<run_id>")
def preview_result(run_id: str):
    with _runs_lock:
        run = _runs.get(run_id, {})
    return jsonify({"stills": run.get("stills") or [], "status": run.get("status", "running")})


@app.route("/progress/<run_id>")
def progress(run_id: str):
    def generate():
        sent = 0
        import time
        while True:
            with _runs_lock:
                run = _runs.get(run_id, {})
                log = run.get("log", [])
                status = run.get("status", "running")

            for line in log[sent:]:
                yield f"data: {json.dumps({'line': line})}\n\n"
            sent = len(log)

            if status in ("done", "error"):
                yield f"data: {json.dumps({'done': True, 'status': status})}\n\n"
                break

            time.sleep(0.4)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/output/<run_id>")
def output(run_id: str):
    with _runs_lock:
        run = _runs.get(run_id, {})
    path = run.get("output_path")
    if not path or not os.path.exists(path):
        return "Not ready", 404
    return send_file(path, mimetype="video/mp4")


@app.route("/download/<run_id>")
def download(run_id: str):
    with _runs_lock:
        run = _runs.get(run_id, {})
    path = run.get("output_path")
    if not path or not os.path.exists(path):
        return "Not ready", 404
    return send_file(path, as_attachment=True, download_name="hobaigabs_reel.mp4")


# ── Shared frame/still helpers (used by both preview and full render) ──────────

def _build_frames_from_payload(data: dict, max_frame_dur: float) -> list[dict]:
    """Build the frames list from the UI payload (shared by preview + render)."""
    input_assets = data.get("assets_dir", "").strip()
    frames = []
    for fd in data.get("frames", []):
        caption = fd.get("caption", "").strip()
        words = len(caption.split()) if caption else 0
        auto_dur = 2.5 if words == 0 else max(3.5, min(max_frame_dur, words / 2.0))
        raw_dur = fd.get("duration")
        try:
            duration = float(raw_dur) if raw_dur not in (None, "") else auto_dur
        except (ValueError, TypeError):
            duration = auto_dur
        duration = max(2.0, min(15.0, duration))

        photo_spec = fd.get("photo_spec", "")
        photo_tmp  = fd.get("photo_tmp_path", "")

        if photo_spec == "uploaded" and photo_tmp and os.path.exists(photo_tmp):
            visual_path = photo_tmp
            photo_spec  = ""
        elif photo_spec and not photo_spec.startswith("ai_") and photo_spec != "uploaded":
            candidate = os.path.join(input_assets, photo_spec) if input_assets else ""
            if candidate and os.path.exists(candidate):
                visual_path = candidate
                photo_spec  = ""
            else:
                print(f"[Pipeline] Warning: {photo_spec} not found in assets folder — will AI-generate")
                visual_path = ""
        else:
            visual_path = ""

        frames.append({
            "frame_id":        fd["frame_id"],
            "caption":         caption,
            "visual":          "",
            "visual_path":     visual_path,
            "photo_spec":      photo_spec,
            "director_note":   fd.get("director_note", ""),
            "edit_prompt":     fd.get("edit_prompt", "").strip(),
            "motion_override": fd.get("motion_override", "").strip(),
            "lipsync":         bool(fd.get("lipsync", False)),
            "voice_override":  fd.get("voice_override", "").strip(),
            "image_model_override": (fd.get("image_model_override", "") or "").strip(),
            "video_model_override": (fd.get("video_model_override", "") or "").strip(),
            "video_start_sec": float(fd.get("video_start_sec") or 0.0),
            "duration":        duration,
        })
    return frames


def _generate_stills(frames: list[dict], assets_dir: str, subject_name: str,
                     subject_description: str, mood: str,
                     cost_tier: str = "draft") -> list[dict]:
    """
    Scene intelligence + image generation + edit pass — everything BEFORE animation.
    Mutates frames to set visual_path to the final still for each frame.
    Cache-aware: re-runs reuse generated images and edited results (by prompt hash),
    so calling this for Preview then again for Render costs nothing the second time.
    """
    import hashlib
    from agents.scene_intelligence import design_all_scenes
    from agents.image_generator import generate_contextual_image, generate_symbolic_image
    from agents.safety import moderate_frames
    from agents import model_router

    # Gate A: moderate user-editable prompt text before spending any credits.
    # (Gate B face-sanity runs inside the image generators themselves.)
    moderate_frames(frames)

    os.makedirs(assets_dir, exist_ok=True)

    # Scene intelligence (parallel, cached)
    frames = design_all_scenes(frames, subject_name=subject_name,
                               subject_description=subject_description)

    # Apply mood to every AI image prompt
    mood_suffix = MOOD_MAP.get(mood, "")
    if mood_suffix:
        for f in frames:
            ip = f.get("scene", {}).get("image_prompt", "")
            if ip:
                f["scene"]["image_prompt"] = ip + ". " + mood_suffix

    # Image generation (cache-aware via generate_* file checks).
    # The router picks the image model per shot (cost-tier aware); real photos
    # return "passthrough" and are never sent to an image model.
    for f in frames:
        ps = f.get("photo_spec", "")
        img_model = model_router.select_model(
            "image", f, cost_tier, override=f.get("image_model_override", ""))
        mid = "" if img_model == model_router.PASSTHROUGH else img_model
        if ps == "ai_portrait":
            f["visual_path"] = generate_contextual_image(f, assets_dir, model_id=mid)
        elif ps == "ai_symbolic":
            f["visual_path"] = generate_symbolic_image(f, assets_dir, model_id=mid)
        elif not f["visual_path"] or not os.path.exists(f["visual_path"]):
            f["visual_path"] = generate_contextual_image(f, assets_dir, model_id=mid)

    # Edit pass — prompt-hashed filename so identical edits are reused (no re-pay)
    for f in frames:
        prompt = f.get("edit_prompt", "")
        if prompt and f.get("visual_path") and os.path.exists(f["visual_path"]):
            from agents.image_editor import edit_image
            src = f["visual_path"]
            phash = hashlib.md5(prompt.encode()).hexdigest()[:8]
            # Always write the edited copy into the RUN dir, never next to the
            # source — otherwise edited files pollute the user's photo folder
            # and shift the auto-match order on the next parse.
            base = os.path.splitext(os.path.basename(src))[0]
            edited = os.path.join(assets_dir, f"{base}_edit_{phash}.jpg")
            if os.path.exists(edited) and os.path.getsize(edited) > 10_000:
                f["visual_path"] = edited  # cached edit — reuse, no cost
                print(f"[ImageEditor] {f['frame_id']}: reusing cached edit")
            else:
                try:
                    f["visual_path"] = edit_image(src, prompt, edited)
                except Exception as e:
                    print(f"[Pipeline] Image edit failed for {f['frame_id']} ({e}) — using original")

    return frames


# ── Pipeline execution ────────────────────────────────────────────────────────

def _execute_pipeline(run_id: str, data: dict, run_dir: Path):
    log = _LogCapture(run_id)

    def _finish(status: str):
        with _runs_lock:
            _runs[run_id]["status"] = status

    try:
        with contextlib.redirect_stdout(log):
            _run_inner(run_id, data, run_dir)
        _finish("done")
    except Exception as e:
        import traceback
        with _runs_lock:
            _runs[run_id]["log"].append(f"✗ Error: {e}")
            _runs[run_id]["log"].append(traceback.format_exc())
        _finish("error")


def _execute_preview(run_id: str, data: dict, run_dir: Path):
    log = _LogCapture(run_id)

    def _finish(status: str):
        with _runs_lock:
            _runs[run_id]["status"] = status

    try:
        with contextlib.redirect_stdout(log):
            _preview_inner(run_id, data, run_dir)
        _finish("done")
    except Exception as e:
        import traceback
        with _runs_lock:
            _runs[run_id]["log"].append(f"✗ Error: {e}")
            _runs[run_id]["log"].append(traceback.format_exc())
        _finish("error")


def _preview_inner(run_id: str, data: dict, run_dir: Path):
    """Generate only the stills and record their paths — no animation."""
    quality       = data.get("quality", "dev")
    max_frame_dur = 5.0 if quality == "dev" else 9.0
    subject_name  = data.get("subject_name", "the subject") or "the subject"
    subject_desc  = data.get("subject_description", "")
    mood          = data.get("mood", "")

    print("[Preview] Generating still images (no animation — cheap pre-check)…")
    frames = _build_frames_from_payload(data, max_frame_dur)
    assets_dir = str(run_dir / "assets")
    frames = _generate_stills(frames, assets_dir, subject_name, subject_desc, mood)

    _VIDEO_EXTS = {".mp4", ".mov", ".avi", ".m4v", ".webm"}
    stills = []
    for f in frames:
        vp = f.get("visual_path", "")
        is_video = vp and os.path.splitext(vp)[1].lower() in _VIDEO_EXTS
        stills.append({
            "frame_id": f["frame_id"],
            "path":     vp,
            "is_video": bool(is_video),
            "exists":   bool(vp and os.path.exists(vp)),
        })
    with _runs_lock:
        _runs[run_id]["stills"] = stills
    print(f"[Preview] ✓ {len(stills)} stills ready — review and add edits, then Generate Video.")


def _run_inner(run_id: str, data: dict, run_dir: Path):
    from agents.caption_writer import generate_frame_srt
    from agents.clip_builder import build_clips
    from agents.image_generator import generate_contextual_image, generate_symbolic_image
    from agents.scene_intelligence import design_all_scenes
    from agents.assembler import assemble_caption_only, frame_timecodes

    input_assets  = data.get("assets_dir", "").strip()   # user's photo/video folder
    quality      = data.get("quality", "dev")
    max_frame_dur = 5.0 if quality == "dev" else 9.0
    subject_name  = data.get("subject_name", "the subject") or "the subject"
    subject_description = data.get("subject_description", "")
    mood          = data.get("mood", "")
    transition    = data.get("transition", "crossfade")
    kling_mode    = data.get("kling_mode", "pro")
    provider      = data.get("provider", "kling")
    # Global model defaults from the UI ("auto" = let the router pick per shot).
    global_img_model = (data.get("image_model", "") or "").strip()
    global_vid_model = (data.get("video_model", "") or "").strip()
    caption_style = data.get("caption_style", {})
    orientation   = data.get("orientation", "portrait")
    width, height = (1080, 1920) if orientation == "portrait" else (1920, 1080)
    fps           = int(data.get("fps", 30))

    if quality == "dev":
        print(f"[Pipeline] DEV mode — 5s clips, Kling {kling_mode}")

    # ── Build frames + generate stills (cache-aware; reuses Preview results) ─
    from agents import model_router
    cost_tier = model_router.cost_tier_from_quality(quality)
    frames = _build_frames_from_payload(data, max_frame_dur)
    print(f"[Pipeline] {len(frames)} frames | subject: {subject_name} | mood: {mood or 'default'} | tier: {cost_tier}")

    # Per-frame override falls back to the global UI default, then to auto.
    for f in frames:
        f["image_model_override"] = f.get("image_model_override") or global_img_model
        f["video_model_override"] = f.get("video_model_override") or global_vid_model

    assets_dir = str(run_dir / "assets")
    frames = _generate_stills(frames, assets_dir, subject_name, subject_description,
                              mood, cost_tier=cost_tier)

    # ── Lip sync pass (between edit and build_clips) ────────────────────────
    clip_temp = tempfile.mkdtemp(prefix="hob_clips_")
    if any(f.get("lipsync") for f in frames):
        from agents.lipsync_coordinator import run_lipsync_pass
        default_voice = data.get("voice_id", "") or os.environ.get("ELEVENLABS_VOICE_ID", "")
        frames = run_lipsync_pass(frames, clip_temp, default_voice_id=default_voice)

    # ── Multi-shot coverage (opt-in): add B-roll sub-shots to eligible beats ─
    # After lip-sync (matching run_caption.py) so eligibility sees final
    # lipsync flags/durations. B-roll candidates come from the USER's folder
    # (input_assets); the run dir only holds AI stills, which are never B-roll.
    if data.get("multi_shot"):
        from agents import coverage
        coverage.assign_coverage(frames, input_assets or assets_dir)

    # ── Build clips ────────────────────────────────────────────────────────
    try:
        base_assignments = [
            {
                "segment_id":        f["frame_id"],
                "actual_duration":   f["duration"],
                "media_path":        f["visual_path"],
                "text":              f.get("caption", ""),
                "motion_prompt":     (
                    f.get("motion_override")
                    or f.get("scene", {}).get("motion_prompt", "")
                ),
                "video_start_sec":   f.get("video_start_sec", 0.0),
                "clip_ready":        bool(f.get("lipsync_clip_path")),
                "lipsync_clip_path": f.get("lipsync_clip_path", ""),
                "has_lipsync_audio": bool(f.get("lipsync_clip_path")),
                # Router picks the video model per shot (cost-tier aware).
                "model_id":          model_router.select_model(
                    "video", f, cost_tier, override=f.get("video_model_override", "")),
            }
            for f in frames
        ]
        # Multi-shot coverage splits eligible beats into sub-shots (no-op otherwise).
        from agents import coverage
        assignments = coverage.expand_all(base_assignments, frames)
        clips = build_clips(assignments, clip_temp, width, height, fps,
                            force_5s=(quality == "dev"), kling_mode=kling_mode,
                            provider=provider)

        # Effective per-frame windows in the rendered video — crossfade overlaps
        # clips, so every timing consumer below uses these, not raw durations.
        frame_times = frame_timecodes(frames, clips, transition)

        # ── Captions ───────────────────────────────────────────────────────
        srt_path = os.path.join(clip_temp, "captions.srt")
        ass_path = generate_frame_srt(frames, srt_path, caption_style=caption_style,
                                      timecodes=frame_times)

        # ── Music / Voice-over ────────────────────────────────────────────
        music_path = None
        if data.get("music_type") == "upload" and data.get("music_path"):
            music_path = data["music_path"]
        elif data.get("music_type") == "generate" and data.get("music_path"):
            music_path = data["music_path"]
        elif data.get("music_type") == "voiceover":
            from agents.tts_generator import generate_voiceover_track
            voice_id = data.get("voice_id", "")
            vo_path  = str(run_dir / "voiceover.mp3")
            # One slot per frame, sized to the frame's effective stride so the
            # concatenated track stays aligned with the rendered video. Lipsync
            # frames keep their slot but as SILENCE (their audio is embedded in
            # the clip) — dropping the slot would shift all later narration early.
            vo_frames = []
            for i, f in enumerate(frames):
                start = frame_times[i][0]
                end   = frame_times[i + 1][0] if i + 1 < len(frames) else frame_times[i][1]
                vo_frames.append({
                    **f,
                    "duration": round(max(0.1, end - start), 3),
                    "caption":  "" if f.get("lipsync_clip_path") else f.get("caption", ""),
                })
            spoken = sum(1 for f in vo_frames if (f.get("caption") or "").strip())
            if spoken:
                print(f"[Pipeline] Generating voice-over track ({spoken} spoken frames)…")
                music_path = generate_voiceover_track(vo_frames, vo_path, voice_id)

        # ── Assemble ───────────────────────────────────────────────────────
        output_path = str(run_dir / "output.mp4")
        assemble_caption_only(clips, clip_temp, output_path,
                              music_path=music_path, srt_path=ass_path,
                              transition=transition,
                              is_voiceover=(data.get("music_type") == "voiceover"))

        total = sum(f["duration"] for f in frames)
        print(f"\n✓ Done! {total:.1f}s → output ready")
        with _runs_lock:
            _runs[run_id]["output_path"] = output_path

    finally:
        shutil.rmtree(clip_temp, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("HOBAILabs Web UI → http://localhost:7860")
    app.run(host="0.0.0.0", port=7860, debug=False, threaded=True)
