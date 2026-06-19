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


@app.route("/brand")
def brand_page():
    return render_template("brand.html")


@app.route("/guide")
def guide_page():
    from flask import send_from_directory
    return send_from_directory(Path(__file__).parent / "docs", "OPERATOR_GUIDE.html")


@app.route("/extract-brief", methods=["POST"])
def extract_brief_route():
    """Paste a brand brief → structured fields (parse-only; never invents claims)."""
    text = (request.json or {}).get("text", "")
    try:
        from agents.brand import extract_brief
        return jsonify({"fields": extract_brief(text)})
    except Exception as e:
        return jsonify({"error": str(e), "fields": {}}), 500


@app.route("/parse-script", methods=["POST"])
def parse_script():
    script_text = request.json.get("script", "")
    assets_dir  = request.json.get("assets_dir", "").strip()
    subject_name = request.json.get("subject_name", "") or ""
    subject_desc = request.json.get("subject_description", "") or ""
    detect_speakers = request.json.get("detect_speakers", True)
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

    # Speaker / cast detection (auto, with UI override) — tags each frame with
    # who is speaking so a quoted line (the kid, the father) gets the right
    # face + voice instead of the narrator's.
    cast = []
    if detect_speakers:
        try:
            from agents import cast as cast_mod
            cast = cast_mod.detect_cast(frames, subject_name, subject_desc)
        except Exception as e:
            print(f"[Pipeline] speaker detection skipped ({e})")

    # Pickable creative suggestions (camera / image-edit / director-note) the
    # operator can click to fill the editable fields — or ignore.
    if request.json.get("suggest", True):
        try:
            from agents import suggestions
            suggestions.suggest_for_frames(frames)
        except Exception as e:
            print(f"[Pipeline] suggestions skipped ({e})")

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
            "speaker_id":     f.get("speaker_id", "narrator"),
            "speaker_label":  f.get("speaker_label", ""),
            "suggestions":    f.get("suggestions", {}),
        })
    return jsonify({"frames": result, "cast": cast})


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
        approved = data.get("approved_frame_ids")
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
            approved_ids=set(approved) if approved is not None else None,
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
    prompt = (data.get("prompt") or "").strip()
    session_id = data.get("session_id", str(uuid.uuid4()))

    run_dir = RUNS_DIR / session_id
    run_dir.mkdir(parents=True, exist_ok=True)
    music_path = str(run_dir / "music.mp3")

    try:
        from agents.music_generator import generate_music, compose_music_brief
        if not prompt:
            # No user prompt — compose a proper Suno brief from the story itself
            # (genre + tempo + instruments + emotion arc, 15-30 descriptors).
            prompt = compose_music_brief(data.get("captions") or [],
                                         mood=data.get("mood", ""))
            print(f"[MusicGen] Composed brief: {prompt}")
        generate_music(prompt, music_path)
        return jsonify({"music_path": music_path, "session_id": session_id,
                        "prompt_used": prompt})
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
    # Brand mode: HARD-BLOCK render until all mandatories pass (BRAND_PLAN §5) —
    # checked before any credits are spent.
    if data.get("mode") == "brand":
        from agents.brand import validate_mandatories
        missing = validate_mandatories(data.get("frames", []), data.get("brand") or {})
        if missing:
            return jsonify({"error": "Brand requirements missing", "missing": missing}), 400
    session_id = data.get("session_id", str(uuid.uuid4()))

    run_dir = RUNS_DIR / session_id
    run_dir.mkdir(parents=True, exist_ok=True)

    with _runs_lock:
        _runs[session_id] = {"status": "running", "log": [], "output_path": None,
                             "clips": {}, "events": []}

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
        sent, esent = 0, 0
        import time
        while True:
            with _runs_lock:
                run = _runs.get(run_id, {})
                log = list(run.get("log", []))
                events = list(run.get("events", []))
                status = run.get("status", "running")

            for line in log[sent:]:
                yield f"data: {json.dumps({'line': line})}\n\n"
            sent = len(log)

            # Typed events (e.g. progressive clip-ready) — emitted as-is so the
            # client can react per frame instead of waiting for the whole render.
            for ev in events[esent:]:
                yield f"data: {json.dumps(ev)}\n\n"
            esent = len(events)

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


@app.route("/clip/<run_id>/<frame_id>")
def clip(run_id: str, frame_id: str):
    """Serve a single finished clip for progressive reveal during a render."""
    with _runs_lock:
        run = _runs.get(run_id, {})
        path = (run.get("clips") or {}).get(frame_id)
    if not path or not os.path.exists(path):
        return "Not ready", 404
    return send_file(path, mimetype="video/mp4")


@app.route("/redo-still", methods=["POST"])
def redo_still():
    """
    Regenerate the still for ONE frame (per-frame redo). Synchronous: the editor
    tweaks a frame's note/photo/edit/camera, clicks 🔄, and gets just that image
    back without re-running the whole pipeline. Cache-aware, so an unchanged frame
    costs nothing. The new still is written into the session assets dir, so the
    later full render reuses it.
    """
    data = request.json or {}
    err = _check_assets_dir(data)
    if err:
        return err
    frame_payload = data.get("frame")
    if not frame_payload or not frame_payload.get("frame_id"):
        return jsonify({"error": "No frame supplied"}), 400

    session_id = data.get("session_id", str(uuid.uuid4()))
    run_dir = RUNS_DIR / session_id
    run_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = str(run_dir / "assets")

    quality       = data.get("quality", "dev")
    max_frame_dur = 5.0 if quality == "dev" else 9.0
    subject_name  = (data.get("subject_name") or "").strip()
    subject_desc  = data.get("subject_description", "")
    mood          = data.get("mood", "")
    brand         = data.get("brand") if data.get("mode") == "brand" else None

    # Build just this one frame through the shared frame builder, then regenerate.
    one = dict(data)
    one["frames"] = [frame_payload]
    try:
        frames = _build_frames_from_payload(one, max_frame_dur)
        # Force a fresh image even if a same-prompt cached file exists — the editor
        # explicitly asked to redo this frame.
        frames = _generate_stills(frames, assets_dir, subject_name, subject_desc, mood,
                                  cost_tier=("draft" if quality == "dev" else "premium"),
                                  face_ref=bool(data.get("face_ref")), brand=brand,
                                  force_regen_ids={frame_payload["frame_id"]})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

    f = frames[0]
    vp = f.get("visual_path", "")
    _VIDEO_EXTS = {".mp4", ".mov", ".avi", ".m4v", ".webm"}
    is_video = bool(vp and os.path.splitext(vp)[1].lower() in _VIDEO_EXTS)
    return jsonify({
        "frame_id": f["frame_id"],
        "path":     vp,
        "is_video": is_video,
        "exists":   bool(vp and os.path.exists(vp)),
    })


# ── Shared frame/still helpers (used by both preview and full render) ──────────

def _build_frames_from_payload(data: dict, max_frame_dur: float) -> list[dict]:
    """Build the frames list from the UI payload (shared by preview + render)."""
    input_assets = data.get("assets_dir", "").strip()
    frames = []
    for fd in data.get("frames", []):
        _speaker_id = (fd.get("speaker_id") or "narrator")
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
            "speaker_id":      _speaker_id,
            "product_beat":    bool(fd.get("product_beat", False)),
            # Per-frame caption overrides (blank = use the global caption style).
            "caption_position":  (fd.get("caption_position") or "").strip(),
            "caption_max_lines": fd.get("caption_max_lines") or "",
        })

    # Resolve speaker_id → gender/age/label. Prefer the cast carried from the
    # UI (honours manual overrides, no extra LLM call); else detect once.
    from agents import cast as cast_mod
    if data.get("cast"):
        cast_mod.apply_cast(frames, data["cast"],
                            data.get("subject_name", ""), data.get("subject_description", ""))
    elif data.get("detect_speakers", True):
        try:
            cast_mod.detect_cast(frames, data.get("subject_name", ""),
                                data.get("subject_description", ""))
        except Exception as e:
            print(f"[Pipeline] speaker detection skipped ({e})")
    return frames


def _generate_stills(frames: list[dict], assets_dir: str, subject_name: str,
                     subject_description: str, mood: str,
                     cost_tier: str = "draft", face_ref: bool = False,
                     brand: dict | None = None,
                     force_regen_ids: set | None = None) -> list[dict]:
    """
    Scene intelligence + image generation + edit pass — everything BEFORE animation.
    Mutates frames to set visual_path to the final still for each frame.
    Cache-aware: re-runs reuse generated images and edited results (by prompt hash),
    so calling this for Preview then again for Render costs nothing the second time.
    brand: when set (brand mode), product/logo beats are real-only (never
    generated), scene design gets campaign framing context, and generated frames
    pass a brand-safety critique.
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

    # Per-frame redo: drop any cached still for these frames so the prompt-hash
    # file-reuse check misses and the image regenerates fresh.
    if force_regen_ids:
        import glob
        for fid in force_regen_ids:
            for old in glob.glob(os.path.join(assets_dir, f"ai_portrait_{fid}_*.jpg")) \
                     + glob.glob(os.path.join(assets_dir, f"ai_symbolic_{fid}_*.jpg")):
                try:
                    os.remove(old)
                except OSError:
                    pass

    # Brand campaign context for visual direction (NOT on-screen copy).
    extra_context = ""
    if brand:
        from agents import brand as brand_mod
        extra_context = brand_mod.brand_scene_context(brand)

    # Scene intelligence (parallel, cached; treatment pass plans the whole reel)
    frames = design_all_scenes(frames, subject_name=subject_name,
                               subject_description=subject_description, mood=mood,
                               extra_context=extra_context)

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
    # face_ref: the first generated portrait of EACH speaker becomes that
    # speaker's identity reference, reused for their later portraits (same
    # person across frames/ages, via gpt-image edit). Keyed per speaker so the
    # narrator and a quoted kid don't borrow each other's face.
    first_portrait_by_speaker: dict[str, str] = {}
    for f in frames:
        # Real-only: product/logo beats are NEVER AI-generated (BRAND_PLAN §5).
        if f.get("product_beat"):
            if not (f.get("visual_path") and os.path.exists(f["visual_path"])):
                print(f"[Brand] {f['frame_id']}: product beat has no real asset — leaving blank")
            continue
        ps = f.get("photo_spec", "")
        img_model = model_router.select_model(
            "image", f, cost_tier, override=f.get("image_model_override", ""))
        mid = "" if img_model == model_router.PASSTHROUGH else img_model
        sid = f.get("speaker_id", "narrator")
        ref = first_portrait_by_speaker.get(sid, "") if face_ref else ""
        if ps == "ai_portrait":
            f["visual_path"] = generate_contextual_image(f, assets_dir, model_id=mid,
                                                         reference_path=ref)
            first_portrait_by_speaker.setdefault(sid, f["visual_path"])
        elif ps == "ai_symbolic":
            f["visual_path"] = generate_symbolic_image(f, assets_dir, model_id=mid)
        elif not f["visual_path"] or not os.path.exists(f["visual_path"]):
            f["visual_path"] = generate_contextual_image(f, assets_dir, model_id=mid,
                                                         reference_path=ref)
            first_portrait_by_speaker.setdefault(sid, f["visual_path"])

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

    # Brand-safety pass on GENERATED frames (real product/logo beats are exempt —
    # they're real assets). Best-effort; degrades to pass.
    if brand:
        from agents.safety import critique_brand
        for f in frames:
            if f.get("product_beat"):
                continue
            ps = f.get("photo_spec", "")
            vp = f.get("visual_path", "")
            generated = ps.startswith("ai_") or "ai_portrait_" in vp or "ai_symbolic_" in vp
            if generated and vp and os.path.exists(vp):
                critique_brand(vp, f["frame_id"], brand)

    # Motion grounding — rewrite each motion prompt by LOOKING at the final
    # still (fast tier, cached), so animation prompts never reference things
    # that aren't in the frame.
    from agents.scene_intelligence import ground_all_motions
    ground_all_motions(frames)

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
    subject_name  = (data.get("subject_name") or "").strip()   # optional
    subject_desc  = data.get("subject_description", "")
    mood          = data.get("mood", "")

    print("[Preview] Generating still images (no animation — cheap pre-check)…")
    frames = _build_frames_from_payload(data, max_frame_dur)
    assets_dir = str(run_dir / "assets")
    brand = data.get("brand") if data.get("mode") == "brand" else None
    frames = _generate_stills(frames, assets_dir, subject_name, subject_desc, mood,
                              face_ref=bool(data.get("face_ref")), brand=brand)

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
    subject_name  = (data.get("subject_name") or "").strip()   # optional
    subject_description = data.get("subject_description", "")
    mood          = data.get("mood", "")
    transition    = data.get("transition", "crossfade")
    kling_mode    = data.get("kling_mode", "pro")
    provider      = data.get("provider", "kling")
    is_brand      = data.get("mode") == "brand"
    brand         = data.get("brand") or {} if is_brand else {}
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
                              mood, cost_tier=cost_tier,
                              face_ref=bool(data.get("face_ref")),
                              brand=brand or None)

    # ── Lip sync pass (between edit and build_clips) ────────────────────────
    clip_temp = tempfile.mkdtemp(prefix="hob_clips_")
    if any(f.get("lipsync") for f in frames):
        from agents.lipsync_coordinator import run_lipsync_pass
        default_voice = data.get("voice_id", "") or os.environ.get("ELEVENLABS_VOICE_ID", "")
        frames = run_lipsync_pass(frames, clip_temp, default_voice_id=default_voice,
                                  voice_map=data.get("speaker_voices"))

    # ── Multi-shot coverage (opt-in): add B-roll sub-shots to eligible beats ─
    # After lip-sync (matching run_caption.py) so eligibility sees final
    # lipsync flags/durations. B-roll candidates come from the USER's folder
    # (input_assets); the run dir only holds AI stills, which are never B-roll.
    if data.get("multi_shot"):
        from agents import coverage
        coverage.assign_coverage(frames, input_assets or assets_dir)

    # ── Brand: auto-append the CTA end-card as a final ~3s beat ──────────────
    if is_brand:
        from agents import brand as brand_mod
        cta_img = str(run_dir / "assets" / "cta_card.jpg")
        try:
            brand_mod.build_cta_card(brand, cta_img, width, height)
            frames.append({
                "frame_id": "cta", "caption": "", "visual_path": cta_img,
                "duration": 3.0, "motion_override": "static", "speaker_id": "narrator",
                "lipsync": False, "product_beat": False,
            })
            print("[Brand] CTA end-card appended")
        except Exception as e:
            print(f"[Brand] CTA card failed ({e}) — no end-card")

    # ── Approval gate ────────────────────────────────────────────────────────
    # If the UI sent an approved-frame list, only those frames get paid animation
    # (Kling/Higgsfield/fal). Unapproved frames fall back to free Ken Burns, so the
    # editor still sees a complete cut and can redo just the frames they rejected.
    # Absent list = everything approved (back-compat).
    approved = data.get("approved_frame_ids")
    approved_set = set(approved) if approved is not None else None

    def _video_model_for(f):
        if approved_set is not None and f["frame_id"] not in approved_set \
           and not f.get("lipsync_clip_path"):
            return "kenburns"   # unapproved → free Ken Burns (sentinel in clip_builder)
        return model_router.select_model(
            "video", f, cost_tier, override=f.get("video_model_override", ""))

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
                # Router picks the video model per shot (cost-tier aware), unless
                # the approval gate forced this frame to Ken Burns.
                "model_id":          _video_model_for(f),
            }
            for f in frames
        ]
        # Multi-shot coverage splits eligible beats into sub-shots (no-op otherwise).
        from agents import coverage
        assignments = coverage.expand_all(base_assignments, frames)

        # Progressive reveal: copy each finished clip into the run dir and emit a
        # typed SSE event so the UI can show it the moment it lands. Sub-shots
        # (f02_1, f02_2…) map back to their parent frame_id for display.
        def _on_clip_ready(seg_id, clip_path):
            frame_id = seg_id.split("_")[0] if "_" in seg_id else seg_id
            dst = str(run_dir / f"clip_{frame_id}.mp4")
            try:
                if clip_path and os.path.exists(clip_path):
                    shutil.copy2(clip_path, dst)
            except OSError:
                return
            with _runs_lock:
                run = _runs.get(run_id)
                if run is None:
                    return
                if frame_id in run.setdefault("clips", {}):
                    return   # first sub-shot is enough to reveal the frame
                run["clips"][frame_id] = dst
                run.setdefault("events", []).append({
                    "type": "clip_ready", "frame_id": frame_id,
                    "url": f"/clip/{run_id}/{frame_id}",
                })

        clips = build_clips(assignments, clip_temp, width, height, fps,
                            force_5s=(quality == "dev"), kling_mode=kling_mode,
                            provider=provider, on_clip_ready=_on_clip_ready)

        # Effective per-frame windows in the rendered video — crossfade overlaps
        # clips, so every timing consumer below uses these, not raw durations.
        frame_times = frame_timecodes(frames, clips, transition)

        # ── Captions ───────────────────────────────────────────────────────
        # Burning subtitles is optional. When disabled, skip caption generation
        # entirely and pass no subtitle file to the assembler (clean video).
        captions_on = caption_style.get("enabled", True)
        if captions_on:
            srt_path = os.path.join(clip_temp, "captions.srt")
            ass_path = generate_frame_srt(frames, srt_path, caption_style=caption_style,
                                          timecodes=frame_times)
        else:
            ass_path = None
            print("[Pipeline] Captions disabled — rendering without subtitles")

        # ── Music / Voice-over ────────────────────────────────────────────
        music_path, bg_music_path, is_vo = None, None, False
        if is_brand:
            # Brand audio: announcer VO (full) over ducked background music.
            music_path, bg_music_path, is_vo = _brand_audio(brand, run_dir)
        elif data.get("music_type") == "upload" and data.get("music_path"):
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
                is_vo = True

        # ── Assemble ───────────────────────────────────────────────────────
        output_path = str(run_dir / "output.mp4")
        assemble_target = str(run_dir / "_raw_output.mp4") if is_brand else output_path
        assemble_caption_only(clips, clip_temp, assemble_target,
                              music_path=music_path, srt_path=ass_path,
                              transition=transition, is_voiceover=is_vo,
                              bg_music_path=bg_music_path)

        # Brand post-pass: burned-in disclosure + optional corner logo bug.
        if is_brand:
            from agents.assembler import apply_brand_overlay
            from agents.brand import disclosure_text
            apply_brand_overlay(
                assemble_target, output_path,
                disclosure_text=disclosure_text(brand) if brand.get("disclosure", True) else "",
                logo_path=brand.get("logo_path", "") if brand.get("logo_bug") else "",
                logo_corner=brand.get("logo_corner", "tr"))

        total = sum(f["duration"] for f in frames)
        print(f"\n✓ Done! {total:.1f}s → output ready")
        with _runs_lock:
            _runs[run_id]["output_path"] = output_path

    finally:
        shutil.rmtree(clip_temp, ignore_errors=True)


def _brand_audio(brand: dict, run_dir: Path) -> tuple:
    """
    Resolve (vo_track, bg_music_track, is_voiceover) for a brand render.
    VO: brand-supplied audio, or an AI announcer reading the brand's script (draft).
    BG: brand-supplied music, or AI-generated. The assembler mixes VO over ducked BG.
    """
    vo_track = None
    if brand.get("vo_mode") == "brand_audio" and (brand.get("vo_audio_path") or "").strip():
        vo_track = brand["vo_audio_path"]
    elif (brand.get("announcer_script") or "").strip():
        # AI announcer DRAFT reads the brand-supplied script verbatim.
        try:
            from agents.tts_generator import generate_single_tts
            vo_path = str(run_dir / "announcer.mp3")
            voice_id = (brand.get("vo_voice_id") or os.environ.get("ELEVENLABS_VOICE_ID", "")).strip()
            if voice_id:
                generate_single_tts(brand["announcer_script"], vo_path, voice_id)
                vo_track = vo_path
            else:
                print("[Brand] no ELEVENLABS voice for AI announcer — skipping VO")
        except Exception as e:
            print(f"[Brand] announcer VO failed ({e}) — no VO")

    bg_track = None
    if brand.get("music_mode") == "brand_audio" and (brand.get("music_audio_path") or "").strip():
        bg_track = brand["music_audio_path"]
    elif brand.get("music_mode") == "ai":
        # Optional AI music; degrade silently if it fails/takes too long.
        try:
            from agents.music_generator import compose_music_brief, generate_music
            bg_path = str(run_dir / "brand_music.mp3")
            brief = compose_music_brief([], mood=brand.get("objective", ""))
            generate_music(brief, bg_path)
            bg_track = bg_path
        except Exception as e:
            print(f"[Brand] AI music failed ({e}) — no background music")

    if vo_track and bg_track:
        return vo_track, bg_track, True       # VO over ducked music (mixed)
    if vo_track:
        return vo_track, None, True            # VO only
    if bg_track:
        return bg_track, None, False           # music only (ducked under video)
    return None, None, False


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("HOBAILabs Web UI → http://localhost:7860")
    app.run(host="0.0.0.0", port=7860, debug=False, threaded=True)
