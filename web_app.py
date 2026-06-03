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
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100MB uploads

RUNS_DIR = Path(tempfile.gettempdir()) / "hob_runs"
RUNS_DIR.mkdir(parents=True, exist_ok=True)

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

    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    tmp.write(script_text)
    tmp.close()

    try:
        from agents.script_parser import parse_frame_script
        frames = parse_frame_script(tmp.name, assets_dir or "")
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

    # Return only what the UI needs
    result = []
    for f in frames:
        result.append({
            "frame_id":     f["frame_id"],
            "caption":      f.get("caption", ""),
            "duration":     round(f.get("duration", 5.0), 1),
            "photo_spec":   f.get("photo_spec", ""),
            "visual_path":  f.get("visual_path", ""),
            "director_note": f.get("director_note", ""),
        })
    return jsonify({"frames": result})


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


@app.route("/run", methods=["POST"])
def run_pipeline():
    data = request.json or {}
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


def _run_inner(run_id: str, data: dict, run_dir: Path):
    from agents.caption_writer import generate_frame_srt
    from agents.clip_builder import build_clips
    from agents.image_generator import generate_contextual_image, generate_symbolic_image
    from agents.scene_intelligence import design_all_scenes
    from agents.assembler import assemble_caption_only

    input_assets  = data.get("assets_dir", "").strip()   # user's photo/video folder
    quality      = data.get("quality", "dev")
    max_frame_dur = 5.0 if quality == "dev" else 9.0
    subject_name  = data.get("subject_name", "the subject") or "the subject"
    subject_description = data.get("subject_description", "")
    mood          = data.get("mood", "")
    transition    = data.get("transition", "crossfade")
    kling_mode    = data.get("kling_mode", "pro")
    caption_style = data.get("caption_style", {})
    orientation   = data.get("orientation", "portrait")
    width, height = (1080, 1920) if orientation == "portrait" else (1920, 1080)
    fps           = int(data.get("fps", 30))

    if quality == "dev":
        print(f"[Pipeline] DEV mode — 5s clips, Kling {kling_mode}")

    # ── Build frames from UI payload ──────────────────────────────────────
    frames = []
    for fd in data.get("frames", []):
        caption = fd.get("caption", "").strip()
        words = len(caption.split()) if caption else 0
        auto_dur = 2.5 if words == 0 else max(3.5, min(max_frame_dur, words / 2.0))
        # UI-supplied duration override takes precedence; fall back to auto
        raw_dur = fd.get("duration")
        try:
            duration = float(raw_dur) if raw_dur not in (None, "") else auto_dur
        except (ValueError, TypeError):
            duration = auto_dur
        duration = max(2.0, min(15.0, duration))

        photo_spec = fd.get("photo_spec", "")
        photo_tmp  = fd.get("photo_tmp_path", "")

        if photo_spec == "uploaded" and photo_tmp and os.path.exists(photo_tmp):
            # Browser-uploaded file
            visual_path = photo_tmp
            photo_spec  = ""
        elif photo_spec and not photo_spec.startswith("ai_") and photo_spec != "uploaded":
            # Named file (from assets folder auto-match or [photo: filename] annotation)
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
            "frame_id":     fd["frame_id"],
            "caption":      caption,
            "visual":       "",
            "visual_path":  visual_path,
            "photo_spec":   photo_spec,
            "director_note": fd.get("director_note", ""),
            "duration":     duration,
        })

    print(f"[Pipeline] {len(frames)} frames | subject: {subject_name} | mood: {mood or 'default'}")

    # ── Scene Intelligence ─────────────────────────────────────────────────
    frames = design_all_scenes(frames, subject_name=subject_name,
                               subject_description=subject_description)

    # ── Apply mood to every AI image prompt ────────────────────────────────
    mood_suffix = MOOD_MAP.get(mood, "")
    if mood_suffix:
        for f in frames:
            ip = f.get("scene", {}).get("image_prompt", "")
            if ip:
                f["scene"]["image_prompt"] = ip + ". " + mood_suffix

    # ── Image generation ───────────────────────────────────────────────────
    assets_dir = str(run_dir / "assets")
    os.makedirs(assets_dir, exist_ok=True)

    for f in frames:
        ps = f.get("photo_spec", "")
        if ps == "ai_portrait":
            f["visual_path"] = generate_contextual_image(f, assets_dir)
        elif ps == "ai_symbolic":
            f["visual_path"] = generate_symbolic_image(f, assets_dir)
        elif not f["visual_path"] or not os.path.exists(f["visual_path"]):
            f["visual_path"] = generate_contextual_image(f, assets_dir)

    # ── Build clips ────────────────────────────────────────────────────────
    clip_temp = tempfile.mkdtemp(prefix="hob_clips_")
    try:
        assignments = [
            {
                "segment_id":      f["frame_id"],
                "actual_duration": f["duration"],
                "media_path":      f["visual_path"],
                "text":            f.get("caption", ""),
                "motion_prompt":   f.get("scene", {}).get("motion_prompt", ""),
            }
            for f in frames
        ]
        clips = build_clips(assignments, clip_temp, width, height, fps,
                            force_5s=(quality == "dev"), kling_mode=kling_mode)

        # ── Captions ───────────────────────────────────────────────────────
        srt_path = os.path.join(clip_temp, "captions.srt")
        ass_path = generate_frame_srt(frames, srt_path, caption_style=caption_style)

        # ── Music / Voice-over ────────────────────────────────────────────
        music_path = None
        if data.get("music_type") == "upload" and data.get("music_path"):
            music_path = data["music_path"]
        elif data.get("music_type") == "generate" and data.get("music_path"):
            music_path = data["music_path"]
        elif data.get("music_type") == "voiceover":
            from agents.tts_generator import generate_voiceover_track
            voice_id = data.get("voice_id", "")
            vo_path = str(run_dir / "voiceover.mp3")
            print(f"[Pipeline] Generating voice-over track ({len(frames)} frames)…")
            music_path = generate_voiceover_track(frames, vo_path, voice_id)

        # ── Assemble ───────────────────────────────────────────────────────
        output_path = str(run_dir / "output.mp4")
        assemble_caption_only(clips, clip_temp, output_path,
                              music_path=music_path, srt_path=ass_path,
                              transition=transition)

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
