"""
HOBAILabs Internal Web UI
Run: ~/.pyenv/versions/3.12.3/bin/python3.12 web_app.py
Open: http://localhost:7860
"""
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
import zipfile
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, make_response, render_template, request, send_file

from agents import auth

load_dotenv(override=True)

app = Flask(__name__, template_folder="web/templates", static_folder="web/static")
# Per-request cap. Folder uploads are sent in small size-batched chunks by the
# client (see main.js), so each request stays well under this; the ceiling is
# generous headroom for a single large video.
app.config["MAX_CONTENT_LENGTH"] = 256 * 1024 * 1024  # 256MB per request
app.config["MAX_FORM_PARTS"] = 5000

RUNS_DIR = Path(os.environ.get("HOB_RUNS_DIR", str(Path(tempfile.gettempdir()) / "hob_runs")))
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

# Startup: release any spend reservations orphaned by a previous process that was
# killed mid-render, so a crash can't permanently inflate a project's spend cap.
# Idempotent + best-effort (must never block boot). Runs once per process import,
# which covers both `python web_app.py` and a gunicorn -w 1 worker.
try:
    from agents import governance as _gov
    _swept = _gov.sweep_stale_reservations(
        ttl_seconds=int(os.environ.get("HOB_RESERVATION_TTL_SEC", "7200")))
    if _swept:
        print(f"[Governance] released {_swept} stale spend reservation(s) on startup")
except Exception as _e:
    print(f"[Governance] startup reservation sweep skipped ({_e})")

# In-memory run state: run_id → {status, log, output_path}
_runs: dict[str, dict] = {}
_runs_lock = threading.Lock()

MOOD_MAP = {
    "warm nostalgic": "warm amber tones, golden hour side-light, slightly desaturated vintage feel",
    "cold struggle": "cool blue-grey palette, overcast diffused light, high contrast deep shadows",
    "triumphant": "rich warm golds and saffron, directional sunlight, high saturation, hopeful energy",
}


# ── Log capture ──────────────────────────────────────────────────────────────

# Per-thread run routing. The old approach wrapped each run in
# contextlib.redirect_stdout(), which sets PROCESS-GLOBAL sys.stdout — so a
# preview running alongside a render (or any overlapping threaded requests under
# gunicorn `--threads 8`) clobbered each other's stdout and cross-wired logs.
# Instead we install ONE stdout tee that routes each line to the run bound to the
# CURRENT thread; non-run threads pass straight through.
_thread_run = threading.local()


class _TeeStdout:
    """Process-global stdout proxy. Routes print() into the log of the run bound
    to the current thread (`_thread_run.run_id`), and always echoes to the real
    stdout. Thread-safe — no per-run sys.stdout swapping."""
    def __init__(self, real):
        self._real = real

    def write(self, text: str):
        run_id = getattr(_thread_run, "run_id", None)
        if run_id:
            stripped = text.rstrip()
            if stripped:
                with _runs_lock:
                    run = _runs.get(run_id)
                    if run is not None:
                        run["log"].append(stripped)
                try:
                    from agents import run_store
                    run_store.append_log(run_id, stripped)
                except Exception:
                    pass
        return self._real.write(text)

    def flush(self):
        self._real.flush()


# Install once, capturing the real stdout. A run thread sets _thread_run.run_id
# (in _execute_pipeline/_execute_preview) so its prints land in that run's log.
sys.stdout = _TeeStdout(sys.__stdout__)


# ── Routes ───────────────────────────────────────────────────────────────────

# ── Operator auth (Gap #1) ─────────────────────────────────────────────────────
# Money/rights routes are gated by @auth.require_operator. The token rides in an
# httpOnly cookie so existing same-origin fetch() calls carry it transparently;
# Bearer is also accepted for API clients. Seed operators with:
#   python -m agents.auth add-operator <id> <email> --role approver
_AUTH_COOKIE = "hob_token"


@app.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    token = auth.authenticate(str(data.get("operator_id", "")), str(data.get("password", "")))
    if not token:
        return jsonify({"error": "invalid credentials"}), 401
    claims = auth.verify_token(token) or {}
    resp = make_response(jsonify({"ok": True, "operator": claims.get("sub"), "role": claims.get("role")}))
    # Secure flag honoured behind TLS in prod; SameSite=Lax keeps the cookie on
    # same-origin XHR while blocking cross-site CSRF on the gated POSTs.
    resp.set_cookie(_AUTH_COOKIE, token, httponly=True, samesite="Lax",
                    secure=bool(os.environ.get("HOB_COOKIE_SECURE")), max_age=auth._TOKEN_TTL)
    return resp


@app.route("/logout", methods=["POST"])
def logout():
    resp = make_response(jsonify({"ok": True}))
    resp.delete_cookie(_AUTH_COOKIE)
    return resp


@app.route("/me")
def whoami():
    claims = auth.verify_token(request.cookies.get(_AUTH_COOKIE, ""))
    if not claims and os.environ.get("HOB_AUTH_DISABLED") == "1":
        claims = {"sub": "dev", "role": "admin"}
    return jsonify({"operator": claims.get("sub"), "role": claims.get("role")} if claims
                   else {"operator": None})


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/brand")
def brand_page():
    return render_template("brand.html")


@app.route("/studio")
def studio_page():
    """Studio Mode (MODE3): type a brief → full reel, with a reusable identity library."""
    return render_template("studio.html")


# ── Studio Mode: identity library + shot planning (MODE3_PLAN) ────────────────

def _talent_public(t: dict) -> dict:
    """Talent row + a /media URL the browser can preview (paths are confined)."""
    return {"id": t["id"], "name": t["name"], "descriptor": t.get("descriptor", ""),
            "ref_url": f"/media?path={t['ref_path']}" if t.get("ref_path") else ""}


def _product_public(p: dict) -> dict:
    return {"id": p["id"], "name": p["name"], "specs": p.get("specs", {}),
            "ref_url": f"/media?path={p['ref_path']}" if p.get("ref_path") else ""}


@app.route("/api/talents", methods=["GET", "POST"])
def api_talents():
    from agents import product_surface as ps
    if request.method == "GET":
        return jsonify({"talents": [_talent_public(t) for t in ps.list_talents()]})
    name = (request.form.get("name") or "").strip()
    descriptor = (request.form.get("descriptor") or "").strip()
    file = request.files.get("photo")
    if not file:
        return jsonify({"error": "a reference photo is required"}), 400
    lib = RUNS_DIR / "_library" / "talents"
    lib.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename).suffix.lower() or ".jpg"
    save_path = lib / f"{uuid.uuid4().hex}{ext}"
    file.save(str(save_path))
    try:
        t = ps.register_talent(name, str(save_path), descriptor=descriptor)
        return jsonify({"talent": _talent_public(t)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/talents/<talent_id>", methods=["DELETE"])
def api_delete_talent(talent_id):
    from agents import product_surface as ps
    return jsonify({"deleted": ps.delete_talent(talent_id)})


@app.route("/api/products", methods=["GET", "POST"])
def api_products():
    from agents import product_surface as ps
    if request.method == "GET":
        return jsonify({"products": [_product_public(p) for p in ps.list_products()]})
    name = (request.form.get("name") or "").strip()
    specs = {}
    raw_specs = (request.form.get("specs") or "").strip()
    if raw_specs:
        try:
            specs = json.loads(raw_specs)
        except Exception:
            specs = {}
    file = request.files.get("photo")
    if not file:
        return jsonify({"error": "a reference photo is required"}), 400
    lib = RUNS_DIR / "_library" / "products"
    lib.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename).suffix.lower() or ".jpg"
    save_path = lib / f"{uuid.uuid4().hex}{ext}"
    file.save(str(save_path))
    try:
        p = ps.register_product(name, str(save_path), specs=specs)
        return jsonify({"product": _product_public(p)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/products/<product_id>", methods=["DELETE"])
def api_delete_product(product_id):
    from agents import product_surface as ps
    return jsonify({"deleted": ps.delete_product(product_id)})


@app.route("/api/studio/plan", methods=["POST"])
def api_studio_plan():
    """Expand a free-text brief into an editable frames[] list (MODE3 P2)."""
    data = request.json or {}
    brief = (data.get("brief") or "").strip()
    if not brief:
        return jsonify({"error": "Enter a brief first."}), 400
    scope = data.get("scope", "general")
    mood = data.get("mood", "")
    from agents import product_surface as ps
    from agents import shot_planner
    talent = ps.get_talent(data["talent_id"]) if data.get("talent_id") else None
    product = ps.get_product(data["product_id"]) if data.get("product_id") else None
    try:
        frames = shot_planner.plan(brief, scope=scope, talent=talent,
                                   product=product, mood=mood)
        return jsonify({"frames": frames})
    except Exception as e:
        return jsonify({"error": str(e), "frames": []}), 500


# ── Director Canvas (AGENTIC_CANVAS_PLAN) ───────────────────────────────────────
# A staged "canvas" surface over the SAME engine: it sequences + gates and reuses
# agents/canvas_run.py, which calls the shared services (pricing/governance/router)
# — it never re-implements cost, routing or rendering (the bright line). Canvas
# state lives inside the run payload (run_store), so there is no parallel store.

# Render threads are in-memory + daemon, so they DON'T survive a process restart.
# A stage left at "generating" by a killed/orphaned thread would otherwise hang the
# board forever (the UI shows a shimmer with no Generate button, polling /rendered
# endlessly). We track the renders THIS process is actually running; on load, any
# "generating" stage whose render isn't live here is treated as orphaned and reset to
# "pending" so the operator can simply re-trigger (the content-hash cache reuses any
# stills that DID finish, so re-generating only fills the gaps — no re-spend).
_ACTIVE_RENDERS: set[str] = set()


def _track_render(render_id: str, target, *args):
    """Spawn a canvas render thread and mark it live for the duration (orphan recovery)."""
    _ACTIVE_RENDERS.add(render_id)

    def _runner():
        try:
            target(*args)
        finally:
            _ACTIVE_RENDERS.discard(render_id)

    threading.Thread(target=_runner, daemon=True).start()


# Transient side-jobs (restore / storyboard-art) set a flag on the canvas while a daemon
# thread works. Like renders, those threads die on restart — track the run_ids running
# here so _canvas_load can clear a stale flag (else the board polls a finished job forever).
_ACTIVE_JOBS: set[str] = set()


def _track_job(run_id: str, target, *args):
    """Spawn a canvas side-job thread and mark its run_id live for the duration."""
    _ACTIVE_JOBS.add(run_id)

    def _runner():
        try:
            target(*args)
        finally:
            _ACTIVE_JOBS.discard(run_id)

    threading.Thread(target=_runner, daemon=True).start()


def _canvas_load(run_id: str) -> dict | None:
    from agents import run_store
    stored = run_store.load(run_id)
    if not stored:
        return None
    state = (stored.get("payload") or {}).get("canvas")
    if state:
        rid = state.get("render_id")
        if rid not in _ACTIVE_RENDERS:                       # not running in THIS process
            recovered = False
            for name, st in (state.get("stages") or {}).items():
                if st.get("status") == "generating":
                    st["status"] = "pending"                 # orphaned → re-triggerable
                    recovered = True
            if recovered:
                print(f"[Canvas] {run_id}: reset orphaned 'generating' stage(s) "
                      f"(render {rid} not live here) → re-triggerable")
                _canvas_save(run_id, state)
        # Transient side-job flags (restore/storyboard) whose thread didn't survive a restart.
        if (state.get("sketching") or state.get("restoring")) and run_id not in _ACTIVE_JOBS:
            state["sketching"] = False
            state["restoring"] = False
            print(f"[Canvas] {run_id}: cleared orphaned side-job flag (no live worker)")
            _canvas_save(run_id, state)
    return state


def _canvas_save(run_id: str, state: dict) -> None:
    from agents import run_store
    run_store.save(run_id, status="canvas", run_dir=str(RUNS_DIR / run_id),
                   payload={"mode": "canvas", "session_id": run_id, "canvas": state})


@app.route("/canvas")
def canvas_page():
    """Director Canvas: a stage-gated board over the shared engine."""
    return render_template("canvas.html")


@app.route("/api/canvas/plan", methods=["POST"])
def api_canvas_plan():
    """Create a canvas and run the free Script stage (reuses shot_planner)."""
    data = request.json or {}
    brief = (data.get("brief") or "").strip()
    if not brief:
        return jsonify({"error": "Enter a brief first."}), 400
    from agents import canvas_run
    run_id = str(uuid.uuid4())
    try:
        target_seconds = int(data.get("target_seconds") or 0)
    except (TypeError, ValueError):
        target_seconds = 0
    state = canvas_run.new_canvas(brief, scope=data.get("scope", "general"),
                                  mood=data.get("mood", ""),
                                  quality=data.get("quality", "dev"),
                                  target_seconds=target_seconds,
                                  story_type=data.get("story_type", "real"))
    _canvas_save(run_id, state)
    return jsonify({"run_id": run_id, "canvas": canvas_run.public_state(state)})


@app.route("/api/canvas/<run_id>/budget")
def api_canvas_budget(run_id: str):
    """Whole-reel cost up front + spend-cap status — surfaced before stage 1 so the
    operator sees the total before committing (parity with galleri5's credit warning,
    but backed by our hard per-stage gate). Fast: no live vendor probe."""
    from agents import governance
    state = _canvas_load(run_id)
    if state is None:
        return jsonify({"error": "Unknown canvas"}), 404
    estimate = round(sum(state.get("costs", {}).values()), 4)
    cap, over = 0.0, False
    try:
        data = {"session_id": run_id}
        cap = governance._spend_cap(data)
        over = bool(governance.check_spend_cap(data, estimate))
    except Exception as e:
        print(f"[Canvas] budget cap check skipped ({e})")
    return jsonify({"estimate_usd": estimate, "spend_cap_usd": round(cap, 2),
                    "over_cap": over, "quality": state.get("quality", "dev")})


@app.route("/api/canvas/list")
def api_canvas_list():
    """Recent saved canvases so the operator can resume one (save/resume)."""
    from agents import run_store
    return jsonify({"canvases": run_store.list_canvases()})


@app.route("/api/canvas/<run_id>/rendered", methods=["POST"])
@auth.require_operator()
def api_canvas_rendered(run_id: str, operator: str):
    """Per-shot rendered media for a canvas's render, read from disk so it survives
    reloads (the live SSE clip_ready reveal only works during the render itself).
    Also syncs the paid stage statuses to the render's ACTUAL status (running →
    generating, done → done) so the rail can't get stuck on 'generating'."""
    from agents import canvas_run, run_store
    state = _canvas_load(run_id)
    if state is None:
        return jsonify({"error": "Unknown canvas"}), 404
    rid = state.get("render_id", "")
    phase = state.get("render_phase", "")
    stills, clips, output_url, status = {}, {}, "", ""
    if rid:
        rdir = RUNS_DIR / rid
        assets = rdir / "assets"
        for f in state.get("frames", []):
            fid = f.get("frame_id")
            if assets.exists():
                imgs = list(assets.glob(f"ai_*_{fid}_*.jpg"))
                if imgs:
                    stills[fid] = str(max(imgs, key=lambda p: p.stat().st_mtime))
            clip = rdir / f"clip_{fid}.mp4"
            if clip.exists():
                clips[fid] = str(clip)           # served via /media (path-confined)
        meta = run_store.load(rid) or {}
        status = meta.get("status", "")
        op = meta.get("output_path", "")
        if op and os.path.exists(op):
            output_url = f"/output/{rid}"
        # Reconcile stage chips by phase (Key Frames render ≠ Video render).
        changed = False

        def _set(stage_ids, val):
            nonlocal changed
            for s in stage_ids:
                cur = state["stages"][s].get("status")
                # NEVER downgrade an operator approval. This reconcile only exists to
                # unstick 'generating' → 'done'; if the operator already approved the
                # stage, a late /rendered poll must not reset it to 'done' (that would
                # silently un-approve Key Frames and make the Video stage 409 forever).
                if cur == "approved":
                    continue
                if cur != val:
                    state["stages"][s]["status"] = val
                    changed = True
        val = {"running": "generating", "done": "done"}.get(status)
        if val and phase == "keyframes":
            _set(["keyframes"], val)
        elif val and phase == "video":
            _set(["video"], val)
        elif val and phase == "full":
            _set(["audio", "finalcut"], val)   # keyframes + video already approved
        if changed:
            _canvas_save(run_id, state)
    return jsonify({"render_id": rid, "stills": stills, "frames": clips,
                    "output_url": output_url, "render_status": status,
                    "render_phase": phase, "canvas": canvas_run.public_state(state)})


@app.route("/api/canvas/<run_id>/state")
def api_canvas_state(run_id: str):
    from agents import canvas_run
    state = _canvas_load(run_id)
    if state is None:
        return jsonify({"error": "Unknown canvas"}), 404
    return jsonify({"run_id": run_id, "canvas": canvas_run.public_state(state)})


@app.route("/api/canvas/<run_id>/advance", methods=["POST"])
@auth.require_operator()
def api_canvas_advance(run_id: str, operator: str):
    """Run the next stage. Free stages execute in-process; paid stages return a
    per-stage cost gate (the anti-wallet-drain) and dispatch to the render pipeline."""
    from agents import canvas_run
    state = _canvas_load(run_id)
    if state is None:
        return jsonify({"error": "Unknown canvas"}), 404
    stage = (request.json or {}).get("stage", "")
    if stage not in canvas_run.STAGES:
        return jsonify({"error": "Unknown stage"}), 400
    if not state["stages"][stage].get("ready"):
        return jsonify({"error": f"Stage '{stage}' is locked — approve the previous stage first."}), 409

    # Paid stages: show the per-stage cost and check the spend cap BEFORE any spend
    # (read-only). This is the explicit fix for the competitor's one-click wallet
    # drain. Actual render reuses _execute_pipeline (wired in the next phase).
    if canvas_run.STAGE_META[stage]["paid"]:
        cost = state.get("costs", {}).get(stage, 0.0)
        blocked = []
        try:
            from agents import governance
            blocked = governance.check_spend_cap({"session_id": run_id}, cost)
        except Exception as e:
            print(f"[Canvas] spend-cap check skipped ({e})")
        return jsonify({"run_id": run_id, "dispatch_required": stage,
                        "estimate_usd": round(cost, 4), "spend_ok": not blocked,
                        "blocked": blocked,
                        "note": "Paid render reuses the existing pipeline (next phase)."})

    # Free stage — run in-process via agents.
    try:
        state = canvas_run.run_stage(state, stage)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    _canvas_save(run_id, state)
    return jsonify({"run_id": run_id, "canvas": canvas_run.public_state(state)})


@app.route("/api/canvas/<run_id>/approve", methods=["POST"])
@auth.require_operator()
def api_canvas_approve(run_id: str, operator: str):
    """Approve a finished stage; unlocks the next stage's Generate button."""
    from agents import canvas_run
    state = _canvas_load(run_id)
    if state is None:
        return jsonify({"error": "Unknown canvas"}), 404
    stage = (request.json or {}).get("stage", "")
    if stage not in canvas_run.STAGES:
        return jsonify({"error": "Unknown stage"}), 400
    state = canvas_run.approve(state, stage)
    _canvas_save(run_id, state)
    return jsonify({"run_id": run_id, "canvas": canvas_run.public_state(state)})


@app.route("/api/canvas/<run_id>/frame", methods=["POST"])
@auth.require_operator()
def api_canvas_edit_frame(run_id: str, operator: str):
    """Edit one shot's text/prompt from the board (the editable prompt box).
    Cascade-invalidates downstream stages so an edit can't ship stale renders."""
    from agents import canvas_run
    state = _canvas_load(run_id)
    if state is None:
        return jsonify({"error": "Unknown canvas"}), 404
    body = request.json or {}
    frame_id = body.get("frame_id", "")
    try:
        state = canvas_run.edit_frame(state, frame_id, body.get("fields") or {})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    _canvas_save(run_id, state)
    return jsonify({"run_id": run_id, "canvas": canvas_run.public_state(state)})


@app.route("/api/canvas/<run_id>/asset", methods=["POST"])
@auth.require_operator()
def api_canvas_asset(run_id: str, operator: str):
    """Attach an operator-uploaded image to shot(s) — Real (passthrough, the moat),
    Reference (AI likeness conditioned on a real face), or Scene. The image is first
    uploaded via /upload-photo; here we validate the path and set the frame keys."""
    from agents import canvas_run
    state = _canvas_load(run_id)
    if state is None:
        return jsonify({"error": "Unknown canvas"}), 404
    body = request.json or {}
    path = (body.get("path") or "").strip()
    if not path or not _path_allowed(path):
        return jsonify({"error": "Image path not allowed"}), 400
    try:
        state = canvas_run.attach_asset(
            state, path=path, mode=body.get("mode", "reference"),
            frame_id=body.get("frame_id"), all_talent=bool(body.get("all_talent")))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    affected = state.pop("last_affected", 1)
    _canvas_save(run_id, state)
    return jsonify({"run_id": run_id, "affected": affected, "mode": body.get("mode", "reference"),
                    "canvas": canvas_run.public_state(state)})


@app.route("/api/canvas/<run_id>/ai-source", methods=["POST"])
@auth.require_operator()
def api_canvas_ai_source(run_id: str, operator: str):
    """Replace a shot's visual with a FULLY AI-generated image (no real footage, no face
    reference) — the escape hatch for a matched real photo the operator dislikes that
    Restore can't fix. Identity-safe: a generic figure/scene from the shot's own prompt.
    (AI-likeness-from-an-uploaded-face goes through /asset in 'reference' mode.)"""
    from agents import canvas_run
    state = _canvas_load(run_id)
    if state is None:
        return jsonify({"error": "Unknown canvas"}), 404
    fid = (request.json or {}).get("frame_id", "")
    try:
        canvas_run.set_ai_generic(state, fid)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    _canvas_save(run_id, state)
    return jsonify({"frame_id": fid, "canvas": canvas_run.public_state(state)})


def _canvas_tempo_bpm(mood: str) -> int:
    """A sensible cut tempo from the mood, used as the beat grid when there's no
    music bed so cutting stays rhythmic (not uniform). Coarse on purpose."""
    m = (mood or "").lower()
    if any(k in m for k in ("triumph", "joy", "upbeat", "energetic", "hope")):
        return 108
    if any(k in m for k in ("somber", "grief", "struggle", "cold", "sad", "loss")):
        return 80
    return 92   # default documentary pacing


_CANVAS_CAPTION_DEFAULT = {"enabled": True, "font": "Montserrat", "size": 24,
                           "position": "bottom", "color": "white", "max_lines": 3}
_CANVAS_ORIENTATIONS = {"portrait", "landscape", "square"}   # 9:16 / 16:9 / 1:1


def _orient_wh(orientation: str) -> tuple[int, int]:
    """Output frame size for the reel by orientation. 9:16 portrait is the default."""
    return {"portrait": (1080, 1920), "landscape": (1920, 1080),
            "square": (1080, 1080)}.get(orientation or "portrait", (1080, 1920))


def _canvas_render_data(state: dict, render_id: str, operator: str) -> dict:
    """Shared payload for the canvas's stills/video render (one builder, two callers)."""
    return {
        "beat_grid_bpm": _canvas_tempo_bpm(state.get("mood", "")),
        "assets_dir": state.get("assets_dir", ""),   # real-photo folder (the moat)
        "mode": "story", "quality": state.get("quality", "prod"),
        "frames": state["frames"], "mood": state.get("mood", ""),
        "subject_name": "", "subject_description": "",
        "video_model": "auto", "image_model": "auto", "music_type": "none",
        # Operator-set caption style + orientation (persisted on the canvas state), with the
        # house defaults when unset. The render engine already burns caption_style + honors
        # orientation, so this is pure surfacing.
        "caption_style": {**_CANVAS_CAPTION_DEFAULT, **(state.get("caption_style") or {})},
        "orientation": state.get("orientation") or "portrait",
        "session_id": render_id, "operator_id": operator,
        "likeness_consent": {"face": True, "voice": True}, "canvas_run_id": "",
        # D1: auto per-speaker face reuse ON by default — every un-anchored shot of a
        # speaker reuses that speaker's FIRST generated portrait, so the same person keeps
        # the same face across the whole reel without anchoring every frame by hand.
        "face_ref": True,
    }


@app.route("/api/canvas/<run_id>/settings", methods=["POST"])
@auth.require_operator()
def api_canvas_settings(run_id: str, operator: str):
    """Persist render settings on the canvas: caption style (on/off, font, size, color,
    position, max-lines = 1/2-line) and orientation (9:16 / 16:9 / 1:1). The engine already
    burns caption_style + honors orientation — this just stores the operator's choice.
    Changing ORIENTATION invalidates rendered stills (they're generated at that aspect)."""
    from agents import canvas_run
    state = _canvas_load(run_id)
    if state is None:
        return jsonify({"error": "Unknown canvas"}), 404
    body = request.json or {}
    if isinstance(body.get("caption_style"), dict):
        cur = {**_CANVAS_CAPTION_DEFAULT, **(state.get("caption_style") or {})}
        for k in ("enabled", "font", "size", "position", "color", "max_lines"):
            if k in body["caption_style"]:
                cur[k] = body["caption_style"][k]
        state["caption_style"] = cur
    if body.get("orientation") in _CANVAS_ORIENTATIONS:
        if state.get("orientation") != body["orientation"]:
            state["orientation"] = body["orientation"]
            # Stills/clips are generated at the chosen aspect → a change makes them stale.
            if state["stages"]["keyframes"].get("status") in ("done", "approved", "generating"):
                canvas_run.invalidate_from(state, "keyframes")
    _canvas_save(run_id, state)
    return jsonify({"canvas": canvas_run.public_state(state)})


@app.route("/api/canvas/<run_id>/character-portrait", methods=["POST"])
@auth.require_operator()
def api_canvas_character_portrait(run_id: str, operator: str):
    """P1 character-sheet-first: generate a CANONICAL portrait for one character from its
    sheet attributes (+ the world style), set it as that character's reference, and link it
    to the character's shots — so every shot conditions on the SAME face (via the pluggable
    identity path). For AI/fiction characters (no real person → no consent gate)."""
    from agents import canvas_run, governance, pricing, image_generator
    state = _canvas_load(run_id)
    if state is None:
        return jsonify({"error": "Unknown canvas"}), 404
    char_id = (request.json or {}).get("char_id", "")
    char = next((c for c in state.get("characters", []) if c.get("id") == char_id), None)
    if not char:
        return jsonify({"error": "Unknown character"}), 400
    appearance = canvas_run._character_appearance(char) or (char.get("name") or char.get("label") or "a person")
    world_clause = canvas_run._world_clause(state.get("world") or {})
    usd = pricing.image_cost("flux")
    one = {"mode": "story", "quality": state.get("quality", "dev"),
           "frames": [{"frame_id": f"char_{char_id}"}], "session_id": run_id, "operator_id": operator}
    spend_missing = governance.reserve_spend(one, usd, run_id=run_id)
    if spend_missing:
        return jsonify({"error": spend_missing[0]}), 400
    out_dir = str(RUNS_DIR / run_id / "characters")
    try:
        portrait = image_generator.generate_character_portrait(
            appearance, out_dir, world_clause=world_clause, char_id=char_id)
        governance.release_reservation(one, run_id=run_id, reason="canvas_char_portrait_done")
        governance.record_cost_event(governance.project_key(one), item="canvas_char_portrait",
                                     usd=usd, run_id=run_id, event_type="estimate")
    except Exception as e:
        try:
            governance.release_reservation(one, run_id=run_id, reason="canvas_char_portrait_failed")
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500
    char["source"] = "ai"
    # Anchor the generated portrait as this character's reference (+ auto-consent: it's a
    # generated character, not a real person). set_character links it to their shots.
    state = canvas_run.set_character(state, char_id, ref_path=portrait, consent=True)
    _canvas_save(run_id, state)
    return jsonify({"char_id": char_id, "portrait": f"/media?path={portrait}",
                    "canvas": canvas_run.public_state(state)})


@app.route("/api/canvas/<run_id>/world", methods=["POST"])
@auth.require_operator()
def api_canvas_world(run_id: str, operator: str):
    """Set the story's World/Context (P2): a global art-direction `style` + `setting` that
    is stamped onto every shot and injected into generation, so the whole reel shares one
    look and world (same palace/forest, one art style). Cascade-invalidates keyframes."""
    from agents import canvas_run
    state = _canvas_load(run_id)
    if state is None:
        return jsonify({"error": "Unknown canvas"}), 404
    body = request.json or {}
    canvas_run.set_world(state, style=body.get("style", ""), setting=body.get("setting", ""))
    _canvas_save(run_id, state)
    return jsonify({"canvas": canvas_run.public_state(state)})


@app.route("/api/canvas/<run_id>/keyframes", methods=["POST"])
@auth.require_operator()
def api_canvas_keyframes(run_id: str, operator: str):
    """Render the cheap stills ONLY (reuses the preview path). Lets the operator
    review / re-roll keyframes before committing to the expensive video — and the
    later full render reuses these stills (shared run dir → content-hash cache)."""
    from agents import canvas_run, governance, run_store
    state = _canvas_load(run_id)
    if state is None:
        return jsonify({"error": "Unknown canvas"}), 404
    render_id = state.get("render_id") or str(uuid.uuid4())
    state["render_id"] = render_id
    data = _canvas_render_data(state, render_id, operator)
    # Stills-only spend gate (images, not video).
    governance.record_consent(data, confirmed_by=operator)
    governance.record_likeness_consent(data, confirmed_by=operator)
    spend_missing = governance.reserve_spend(data, _estimate_payload_cost(data), run_id=render_id)
    if spend_missing:
        return jsonify({"error": "Spend cap exceeded", "missing": spend_missing}), 400
    run_dir = RUNS_DIR / render_id
    run_dir.mkdir(parents=True, exist_ok=True)
    with _runs_lock:
        _runs[render_id] = {"status": "running", "log": [], "stills": None,
                            "clips": {}, "events": []}
    try:
        run_store.save(render_id, status="running", payload=data, run_dir=str(run_dir))
    except Exception as e:
        print(f"[RunStore] canvas keyframes save skipped ({e})")
    _track_render(render_id, _execute_preview, render_id, data, run_dir)
    state["render_phase"] = "keyframes"
    state["stages"]["keyframes"].update(status="generating")
    _canvas_save(run_id, state)
    return jsonify({"render_id": render_id, "canvas": canvas_run.public_state(state)})


@app.route("/api/canvas/<run_id>/recreate", methods=["POST"])
@auth.require_operator()
def api_canvas_recreate(run_id: str, operator: str):
    """Re-create ambient (Reality–Fidelity ladder rung 3): for a NON-person shot,
    generate a cinematic version of the SAME scene, inspired from the real footage
    (image-to-image), at professional quality. Identity-safe — REJECTS person shots
    (those keep Restore, or use the consent-gated person path). Labeled AI · from real."""
    from agents import canvas_run, governance, image_generator
    state = _canvas_load(run_id)
    if state is None:
        return jsonify({"error": "Unknown canvas"}), 404
    fid = (request.json or {}).get("frame_id", "")
    frame = next((f for f in state.get("frames", []) if f.get("frame_id") == fid), None)
    if not frame:
        return jsonify({"error": "Unknown shot"}), 400
    # Identity guard — ambient re-create is ONLY for shots with no real person on screen.
    if frame.get("uses_talent"):
        return jsonify({"error": "This shot shows the person — keep it real (Enhance), "
                                 "or use the consent-gated person re-create.",
                        "person_shot": True}), 409

    # Reference = the real footage; extract a representative frame if it's a video.
    ref = frame.get("visual_path") or frame.get("photo_spec") or ""
    if ref and not ref.startswith("ai_") and os.path.isfile(ref):
        if os.path.splitext(ref)[1].lower() in {".mp4", ".mov", ".avi", ".m4v", ".webm", ".mkv"}:
            rdir = RUNS_DIR / run_id / "recreate"
            rdir.mkdir(parents=True, exist_ok=True)
            refimg = str(rdir / f"{fid}_ref.jpg")
            try:
                subprocess.run(["ffmpeg", "-y", "-ss", "00:00:01", "-i", ref, "-frames:v", "1", refimg],
                               capture_output=True, timeout=60)
                if os.path.exists(refimg):
                    ref = refimg
            except Exception:
                pass
    else:
        ref = ""

    # Identity guard #2 (the real one): the reference must contain NO face. A shot can
    # be tagged "ambient" yet have a person photo attached — recreating that would
    # synthesize a likeness. Refuse if a face is detected.
    if ref:
        from agents import safety
        # Haar (fast) OR the vision LLM (reliable — Haar misses angled/partial faces).
        if safety.face_count(ref) > 0 or safety.has_person(ref):
            return jsonify({"error": "That footage contains a person — ambient re-create "
                                     "can't be used (it would synthesize a likeness). Use "
                                     "Enhance to keep it real, or the consent-gated person path.",
                            "person_in_footage": True}), 409

    assets_dir = str(RUNS_DIR / run_id / "assets")
    os.makedirs(assets_dir, exist_ok=True)
    one = {"mode": "story", "quality": state.get("quality", "dev"),
           "frames": [{**frame, "photo_spec": "ai_symbolic", "visual_path": ""}],
           "session_id": run_id, "operator_id": operator}
    spend_missing = governance.reserve_spend(one, _estimate_payload_cost(one), run_id=run_id)
    if spend_missing:
        return jsonify({"error": spend_missing[0]}), 400
    try:
        frame.setdefault("orig_visual", frame.get("visual_path") or frame.get("photo_spec"))
        frame["photo_spec"] = "ai_symbolic"          # AI scene, no person
        frame.pop("visual_path", None)
        out = image_generator.recreate_ambient(frame, assets_dir, reference_path=ref)
        frame["visual_path"] = out
        frame["recreated_from_real"] = True
        frame.pop("restored", None)                  # re-create supersedes a prior restore
        governance.release_reservation(one, run_id=run_id, reason="canvas_recreate_done")
        governance.record_cost_event(governance.project_key(one), item="canvas_recreate",
                                     usd=_estimate_payload_cost(one), run_id=run_id, event_type="estimate")
    except Exception as e:
        try:
            governance.release_reservation(one, run_id=run_id, reason="canvas_recreate_failed")
        except Exception:
            pass
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    if state["stages"]["keyframes"].get("status") in ("done", "approved", "generating"):
        canvas_run.invalidate_from(state, "keyframes")
    state["board"] = canvas_run.board_cards(state["frames"])
    state["costs"] = canvas_run.stage_costs(state["frames"], quality=state.get("quality", "dev"))
    _canvas_save(run_id, state)
    return jsonify({"frame_id": fid, "canvas": canvas_run.public_state(state)})


@app.route("/api/canvas/<run_id>/upscale", methods=["POST"])
@auth.require_operator()
def api_canvas_upscale(run_id: str, operator: str):
    """Generative upscale of ONE shot's still — the final-render quality lift. Routed by
    asset kind so the moat holds: a REAL shot uses a FAITHFUL super-res (aura_sr, no
    invented detail → a real face stays the real face); an AI shot uses a CREATIVE
    upscaler (clarity, adds detail). Spend-gated per shot; cached output; degrades to the
    original on any failure. Real images only (videos are skipped upstream)."""
    from agents import canvas_run, governance, pricing, upscaler
    state = _canvas_load(run_id)
    if state is None:
        return jsonify({"error": "Unknown canvas"}), 404
    fid = (request.json or {}).get("frame_id", "")
    frame = next((f for f in state.get("frames", []) if f.get("frame_id") == fid), None)
    if not frame:
        return jsonify({"error": "Unknown shot"}), 400
    src = frame.get("visual_path") or ""
    if not src or not os.path.isfile(src):
        ps = frame.get("photo_spec") or ""
        src = ps if (ps and not ps.startswith("ai_") and os.path.isfile(ps)) else ""
    if not src:
        return jsonify({"error": "Nothing to upscale yet — generate Key Frames "
                                 "(or match a photo) on this shot first."}), 400
    if os.path.splitext(src)[1].lower() not in upscaler.IMAGE_EXTS:
        return jsonify({"error": "Only a still image can be upscaled (this shot is a video clip)."}), 400

    kind = canvas_run.asset_kind(frame)
    creative = kind != canvas_run.ASSET_REAL   # real → faithful (protect identity); AI → creative
    # Skip real photos that are already high-res for a 9:16 reel (faithful super-res caps
    # input at 3072px anyway — a 4000px phone photo doesn't need upscaling, it needs
    # downscaling). No spend, friendly message.
    if not creative:
        from agents import restore
        w, h = restore._probe_resolution(src)
        if w and h and max(w, h) > 3072:
            return jsonify({"frame_id": fid, "skipped": True,
                            "message": f"Already high-res ({w}×{h}) — no upscale needed for a 9:16 reel.",
                            "canvas": canvas_run.public_state(state)})
    usd = pricing.upscale_cost(creative)
    one = {"mode": "story", "quality": state.get("quality", "dev"),
           "frames": [frame], "session_id": run_id, "operator_id": operator}
    spend_missing = governance.reserve_spend(one, usd, run_id=run_id)
    if spend_missing:
        return jsonify({"error": spend_missing[0]}), 400
    try:
        out_dir = str(RUNS_DIR / run_id / "upscaled")
        newp = upscaler.upscale_file(src, out_dir, creative=creative)
        if not newp or newp == src:
            governance.release_reservation(one, run_id=run_id, reason="canvas_upscale_noop")
            return jsonify({"error": "Upscale failed (model/endpoint) — left the shot unchanged.",
                            "canvas": canvas_run.public_state(state)}), 502
        frame.setdefault("orig_visual", src)
        frame["visual_path"] = newp
        if not str(frame.get("photo_spec") or "").startswith("ai_"):
            frame["photo_spec"] = newp        # real shot: keep spec pointing at the upscaled file
        frame["upscaled"] = True
        governance.release_reservation(one, run_id=run_id, reason="canvas_upscale_done")
        governance.record_cost_event(governance.project_key(one), item="canvas_upscale",
                                     usd=usd, run_id=run_id, event_type="estimate")
    except Exception as e:
        try:
            governance.release_reservation(one, run_id=run_id, reason="canvas_upscale_failed")
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500
    if state["stages"]["keyframes"].get("status") in ("done", "approved", "generating"):
        canvas_run.invalidate_from(state, "keyframes")
    state["board"] = canvas_run.board_cards(state["frames"])
    _canvas_save(run_id, state)
    return jsonify({"frame_id": fid, "creative": creative, "canvas": canvas_run.public_state(state)})


@app.route("/api/canvas/<run_id>/storyboard-art", methods=["POST"])
@auth.require_operator()
def api_canvas_storyboard_art(run_id: str, operator: str):
    """Render a pencil-sketch STORYBOARD panel per shot — the comic-board planning view
    (galleri5's signature visual). A PLANNING artifact: loose graphite sketches of framing/
    blocking/camera move, NOT the final render and NOT a photoreal likeness, never used in
    the reel itself. Cheap draft model, content-hash cached, threaded (one image per shot).
    Spend-gated up front. Progress on `public_state.sketching/sketch_done/sketch_total`."""
    from agents import canvas_run, governance, pricing
    state = _canvas_load(run_id)
    if state is None:
        return jsonify({"error": "Unknown canvas"}), 404
    frames = state.get("frames", [])
    if not frames:
        return jsonify({"error": "Plan a story first."}), 400
    usd = pricing.storyboard_cost() * len(frames)
    one = {"mode": "story", "quality": "dev", "frames": frames,
           "session_id": run_id, "operator_id": operator}
    spend_missing = governance.reserve_spend(one, usd, run_id=run_id)
    if spend_missing:
        return jsonify({"error": spend_missing[0]}), 400
    out_dir = str(RUNS_DIR / run_id / "storyboard")
    state["sketching"] = True
    state["sketch_total"] = len(frames)
    state["sketch_done"] = 0
    _canvas_save(run_id, state)

    def _job(st):
        # Panels are independent → render them CONCURRENTLY (each ~10s on the draft model;
        # sequential would be N×10s). Pool capped so we don't exceed the model's fal limit.
        from agents import image_generator
        from concurrent.futures import ThreadPoolExecutor
        lock = threading.Lock()
        progress = {"done": 0}

        def _panel(f):
            try:
                p = image_generator.generate_storyboard_panel(f, out_dir)
                if p:
                    f["storyboard_art"] = p
            except Exception as e:
                print(f"[Storyboard] {f.get('frame_id')} ({e})")
            with lock:
                progress["done"] += 1
                st["sketch_done"] = progress["done"]
                st["board"] = canvas_run.board_cards(st["frames"])
                _canvas_save(run_id, st)

        with ThreadPoolExecutor(max_workers=5) as ex:
            list(ex.map(_panel, st["frames"]))
        st["sketching"] = False
        try:
            governance.release_reservation(one, run_id=run_id, reason="canvas_storyboard_done")
            governance.record_cost_event(governance.project_key(one), item="canvas_storyboard",
                                         usd=usd, run_id=run_id, event_type="estimate")
        except Exception:
            pass
        st["board"] = canvas_run.board_cards(st["frames"])
        _canvas_save(run_id, st)

    _track_job(run_id, _job, state)
    return jsonify({"sketching": True, "total": len(frames),
                    "canvas": canvas_run.public_state(state)})


@app.route("/api/canvas/<run_id>/restore", methods=["POST"])
@auth.require_operator()
def api_canvas_restore(run_id: str, operator: str):
    """Restore (Reality–Fidelity ladder rung 1): non-generative cleanup of the matched
    REAL footage — upscale, denoise, sharpen, grade, stabilize — so amateur phone media
    reads cinematic WITHOUT faking anyone. Zero authenticity cost (same identity/claims).
    Threaded (ffmpeg is CPU-bound); progress on the canvas state."""
    from agents import canvas_run
    state = _canvas_load(run_id)
    if state is None:
        return jsonify({"error": "Unknown canvas"}), 404
    targets = [f for f in state["frames"]
               if canvas_run.asset_kind(f) == canvas_run.ASSET_REAL
               and (f.get("visual_path") or
                    (f.get("photo_spec") and not f["photo_spec"].startswith("ai_")))]
    # Per-shot restore (Fidelity selector): scope to one shot if frame_id is given.
    fid = (request.json or {}).get("frame_id") if request.is_json else None
    if fid:
        targets = [f for f in targets if f.get("frame_id") == fid]
    if not targets:
        return jsonify({"error": "No real footage to enhance yet — match your photos first."}), 400
    out_dir = str(RUNS_DIR / run_id / "restored")
    state["restoring"] = True
    state["restore_total"] = len(targets)
    state["restore_done"] = 0
    _canvas_save(run_id, state)

    def _job(st):
        from agents import restore
        for i, f in enumerate(targets, 1):
            src = f.get("visual_path") or f.get("photo_spec")
            f.setdefault("orig_visual", src)   # preserve original for Passthrough revert
            try:
                newp = restore.restore_file(src, out_dir)
                if newp and newp != src:
                    f["visual_path"] = newp
                    f["photo_spec"] = newp
                    f["restored"] = True
                    f.pop("recreated_from_real", None)   # restore supersedes a prior re-create
            except Exception as e:
                print(f"[Restore] {src} ({e})")
            st["restore_done"] = i
            st["board"] = canvas_run.board_cards(st["frames"])
            _canvas_save(run_id, st)
        st["restoring"] = False
        if st["stages"]["keyframes"]["status"] in ("done", "approved", "generating"):
            canvas_run.invalidate_from(st, "keyframes")   # new visuals → re-render downstream
        st["board"] = canvas_run.board_cards(st["frames"])
        st["costs"] = canvas_run.stage_costs(st["frames"], quality=st.get("quality", "dev"))
        _canvas_save(run_id, st)

    _track_job(run_id, _job, state)
    return jsonify({"restoring": True, "total": len(targets),
                    "canvas": canvas_run.public_state(state)})


@app.route("/api/canvas/<run_id>/fidelity-suggest", methods=["POST"])
@auth.require_operator()
def api_canvas_fidelity_suggest(run_id: str, operator: str):
    """Reality–Fidelity auto-suggest (ladder rung 1d): score each REAL shot's quality
    (resolution + sharpness) and recommend a rung — Passthrough (clean), Restore (soft/
    low-res), Re-create (amateur ambient B-roll). Person shots are never pushed past
    Restore. Read-only assessment — no spend, no media changes."""
    from agents import canvas_run
    state = _canvas_load(run_id)
    if state is None:
        return jsonify({"error": "Unknown canvas"}), 404
    suggestions = canvas_run.score_fidelity(state)
    state["board"] = canvas_run.board_cards(state["frames"])
    _canvas_save(run_id, state)
    return jsonify({"suggestions": suggestions, "canvas": canvas_run.public_state(state)})


@app.route("/api/canvas/<run_id>/fidelity", methods=["POST"])
@auth.require_operator()
def api_canvas_fidelity(run_id: str, operator: str):
    """Set a shot's Fidelity rung. Restore/Re-create are dispatched by the UI to the
    existing /restore and /recreate routes (verified paths, reused untouched); this route
    handles **Passthrough** — revert to the untouched original real footage (rung 0)."""
    from agents import canvas_run
    state = _canvas_load(run_id)
    if state is None:
        return jsonify({"error": "Unknown canvas"}), 404
    body = request.json or {}
    fid, rung = body.get("frame_id", ""), body.get("rung", "")
    if rung != "passthrough":
        return jsonify({"error": "Use /restore or /recreate for that rung"}), 400
    try:
        canvas_run.revert_passthrough(state, fid)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if state["stages"]["keyframes"].get("status") in ("done", "approved", "generating"):
        canvas_run.invalidate_from(state, "keyframes")   # visual changed → re-render
    state["board"] = canvas_run.board_cards(state["frames"])
    _canvas_save(run_id, state)
    return jsonify({"frame_id": fid, "canvas": canvas_run.public_state(state)})


@app.route("/api/canvas/<run_id>/characters", methods=["POST"])
@auth.require_operator()
def api_canvas_characters(run_id: str, operator: str):
    """Characters/Assets stage: surface the REAL people in the story (cast detection),
    so the operator can anchor each to a real photo + consent. Our moat-respecting take
    on galleri5's synthetic character sheets."""
    from agents import canvas_run
    state = _canvas_load(run_id)
    if state is None:
        return jsonify({"error": "Unknown canvas"}), 404
    chars = canvas_run.derive_characters(state)
    _canvas_save(run_id, state)
    return jsonify({"characters": chars, "canvas": canvas_run.public_state(state)})


@app.route("/api/canvas/<run_id>/character", methods=["POST"])
@auth.require_operator()
def api_canvas_set_character(run_id: str, operator: str):
    """Anchor a character to a real reference photo (+ consent) and link it to that
    character's shots. The ref is uploaded via /upload-photo or a browseable path."""
    from agents import canvas_run
    state = _canvas_load(run_id)
    if state is None:
        return jsonify({"error": "Unknown canvas"}), 404
    body = request.json or {}
    char_id = body.get("char_id", "")
    ref_path = (body.get("ref_path") or "").strip()
    if ref_path and not _path_allowed(ref_path):
        return jsonify({"error": "Reference path not allowed"}), 400
    attrs = body.get("attrs") if isinstance(body.get("attrs"), dict) else None
    try:
        state = canvas_run.set_character(state, char_id, ref_path=ref_path,
                                         consent=body.get("consent"), attrs=attrs)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    _canvas_save(run_id, state)
    return jsonify({"run_id": run_id, "canvas": canvas_run.public_state(state)})


@app.route("/api/canvas/<run_id>/match-photos", methods=["POST"])
@auth.require_operator()
def api_canvas_match_photos(run_id: str, operator: str):
    """Use REAL photos from a folder for the person shots (the moat) instead of
    AI portraits of a real named person. Clears the `ai_portrait` spec on talent
    shots so `image_matcher.smart_match` can content-match the operator's real
    media → those shots become real passthrough (untouched), AI fills the rest."""
    from agents import canvas_run, image_matcher
    state = _canvas_load(run_id)
    if state is None:
        return jsonify({"error": "Unknown canvas"}), 404
    folder = ((request.json or {}).get("assets_dir") or "").strip()
    if not folder or not os.path.isdir(folder):
        return jsonify({"error": f"Folder not found: {folder}"}), 400
    if not _path_allowed(folder):
        return jsonify({"error": "Folder is outside the allowed root"}), 400
    # Make talent/AI-portrait shots eligible for real-photo matching.
    for f in state["frames"]:
        if f.get("uses_talent") or (f.get("photo_spec") or "").startswith("ai_portrait"):
            f["photo_spec"] = ""
            f.pop("visual_path", None)
    try:
        matched = image_matcher.smart_match(state["frames"], folder, lambda fn: True)
    except Exception as e:
        print(f"[Canvas] photo match failed ({e})")
        matched = False
    state["assets_dir"] = folder
    if state["stages"]["keyframes"]["status"] in ("done", "approved", "generating"):
        canvas_run.invalidate_from(state, "keyframes")
    state["board"] = canvas_run.board_cards(state["frames"])
    state["costs"] = canvas_run.stage_costs(state["frames"], quality=state.get("quality", "dev"))
    _canvas_save(run_id, state)
    real = sum(1 for c in state["board"] if c["asset_kind"] == "real")
    return jsonify({"matched": bool(matched), "real_shots": real,
                    "canvas": canvas_run.public_state(state)})


@app.route("/api/canvas/<run_id>/rematch", methods=["POST"])
@auth.require_operator()
def api_canvas_rematch(run_id: str, operator: str):
    """Re-match ONE shot against the operator's folder (C6) — the role-aware matcher
    auto-picks the best-fitting photo for just this beat (vs manual 🖼 Pick). Clears the
    shot's current media so smart_match re-assigns only it."""
    from agents import canvas_run, image_matcher
    state = _canvas_load(run_id)
    if state is None:
        return jsonify({"error": "Unknown canvas"}), 404
    folder = state.get("assets_dir", "")
    if not folder or not os.path.isdir(folder):
        return jsonify({"error": "Match a photo folder first."}), 400
    fid = (request.json or {}).get("frame_id", "")
    f = next((x for x in state.get("frames", []) if x.get("frame_id") == fid), None)
    if not f:
        return jsonify({"error": "Unknown shot"}), 400
    f["photo_spec"] = ""                     # clear THIS shot only → smart_match re-fills it
    f.pop("visual_path", None)
    try:
        image_matcher.smart_match(state["frames"], folder, lambda fn: True)
    except Exception as e:
        print(f"[Canvas] rematch failed ({e})")
    if state["stages"]["keyframes"].get("status") in ("done", "approved", "generating"):
        canvas_run.invalidate_from(state, "keyframes")
    state["board"] = canvas_run.board_cards(state["frames"])
    _canvas_save(run_id, state)
    return jsonify({"frame_id": fid, "canvas": canvas_run.public_state(state)})


@app.route("/api/canvas/<run_id>/assets", methods=["GET"])
@auth.require_operator()
def api_canvas_assets(run_id: str, operator: str):
    """List the operator's media folder so the board can show a thumbnail PICKER —
    auto-match is never perfect on abstract beats, so let the operator swap any shot to
    the RIGHT real photo in two clicks (instead of re-matching everything). Returns
    {path, name, is_video} for each file; thumbnails are served by /media."""
    state = _canvas_load(run_id)
    if state is None:
        return jsonify({"error": "Unknown canvas"}), 404
    folder = state.get("assets_dir") or ""
    if not folder or not os.path.isdir(folder) or not _path_allowed(folder):
        return jsonify({"assets": [], "folder": folder})
    vid_exts = {".mp4", ".mov", ".avi", ".m4v", ".webm", ".mkv"}
    img_exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".heif"}
    out = []
    try:
        for fn in sorted(os.listdir(folder)):
            ext = os.path.splitext(fn)[1].lower()
            if ext in vid_exts | img_exts:
                p = os.path.join(folder, fn)
                out.append({"path": p, "name": fn, "is_video": ext in vid_exts})
    except Exception as e:
        return jsonify({"assets": [], "folder": folder, "error": str(e)})
    return jsonify({"assets": out, "folder": folder})


@app.route("/api/canvas/<run_id>/video", methods=["POST"])
@auth.require_operator()
def api_canvas_video(run_id: str, operator: str):
    """Video stage — animate the approved Key Frames into clips ONLY (no assembly),
    so the operator reviews the motion before the Final Cut. Reuses the proven
    pipeline with `stop_after='clips'`; the clips are cached so Final Cut reuses
    them (no re-spend). Gated behind Key Frames approval."""
    from agents import canvas_run, governance, run_store
    state = _canvas_load(run_id)
    if state is None:
        return jsonify({"error": "Unknown canvas"}), 404
    if state["stages"]["keyframes"].get("status") != "approved":
        return jsonify({"error": "Generate and approve Key Frames first.",
                        "need_keyframes": True}), 409
    render_id = state.get("render_id") or str(uuid.uuid4())
    data = _canvas_render_data(state, render_id, operator)
    data["stop_after"] = "clips"      # build + cache clips, skip assembly
    governance.record_consent(data, confirmed_by=operator)
    governance.record_likeness_consent(data, confirmed_by=operator)
    spend_missing = governance.reserve_spend(data, _estimate_payload_cost(data), run_id=render_id)
    if spend_missing:
        return jsonify({"error": "Spend cap exceeded", "missing": spend_missing}), 400
    run_dir = RUNS_DIR / render_id
    run_dir.mkdir(parents=True, exist_ok=True)
    with _runs_lock:
        _runs[render_id] = {"status": "running", "log": [], "output_path": None,
                            "clips": {}, "events": []}
    try:
        run_store.save(render_id, status="running", payload=data, run_dir=str(run_dir))
    except Exception as e:
        print(f"[RunStore] canvas video save skipped ({e})")
    _track_render(render_id, _execute_pipeline, render_id, data, run_dir)
    state["render_id"] = render_id
    state["render_phase"] = "video"
    state["stages"]["video"].update(status="generating")
    _canvas_save(run_id, state)
    return jsonify({"render_id": render_id, "canvas": canvas_run.public_state(state)})


@app.route("/api/canvas/<run_id>/render", methods=["POST"])
@auth.require_operator()
def api_canvas_render(run_id: str, operator: str):
    """Final Cut — assemble the approved clips + audio + captions into the finished
    reel by dispatching the EXISTING pipeline (`_run_inner`), which reuses the cached
    stills AND clips (no re-spend) and only does audio + assembly. Same governance
    gates as /run. Gated behind Video approval."""
    from agents import canvas_run, governance, run_store
    state = _canvas_load(run_id)
    if state is None:
        return jsonify({"error": "Unknown canvas"}), 404
    body = request.json or {}
    # Gate: Final Cut needs the Video stage generated AND approved — so you review the
    # clips before the reel is assembled (and nothing whole runs un-reviewed).
    if state["stages"]["video"].get("status") != "approved":
        return jsonify({"error": "Generate and approve Video clips first — then Final Cut.",
                        "need_video": True}), 409
    if body.get("quality"):
        state["quality"] = body["quality"]
    # Reuse the render dir from the Key Frames stage so the stills are reused
    # (content-hash cache) instead of re-spent.
    render_id = state.get("render_id") or str(uuid.uuid4())
    data = _canvas_render_data(state, render_id, operator)
    data["canvas_run_id"] = run_id
    # Audio options (same set as Story mode): generate (Suno), upload a song,
    # ElevenLabs voiceover, or none. Default = generate so beat-aware cutting has a
    # bed; if it can't, the tempo-grid fallback keeps cuts rhythmic anyway.
    data["music_type"] = body.get("music_type") or "generate"
    if data["music_type"] == "upload":
        mp = (body.get("music_path") or "").strip()
        if not mp or not _path_allowed(mp):
            return jsonify({"error": "Upload a song first, or choose a different audio option."}), 400
        data["music_path"] = mp
    if data["music_type"] == "voiceover":
        data["voice_id"] = (body.get("voice_id") or "").strip()
        data["beat_grid_bpm"] = 0   # gentle, uniform cuts under narration (don't beat-cut speech)
    quality = data["quality"]
    # Same gates as /run — money/rights are not bypassed by the canvas surface.
    missing = governance.validate_consent(data)
    if missing:
        return jsonify({"error": "Consent / rights requirements missing", "missing": missing}), 400
    governance.record_consent(data, confirmed_by=operator)
    lk = governance.validate_likeness_consent(data)
    if lk:
        return jsonify({"error": "Likeness consent required", "missing": lk}), 400
    governance.record_likeness_consent(data, confirmed_by=operator)
    spend_missing = governance.reserve_spend(data, _estimate_payload_cost(data), run_id=render_id)
    if spend_missing:
        return jsonify({"error": "Spend cap exceeded", "missing": spend_missing}), 400

    run_dir = RUNS_DIR / render_id
    run_dir.mkdir(parents=True, exist_ok=True)
    with _runs_lock:
        _runs[render_id] = {"status": "running", "log": [], "output_path": None,
                            "clips": {}, "events": []}
    try:
        run_store.save(render_id, status="running", payload=data, run_dir=str(run_dir))
    except Exception as e:
        print(f"[RunStore] canvas render save skipped ({e})")
    _track_render(render_id, _canvas_render_thread, render_id, data, run_dir)

    for s in ("audio", "finalcut"):      # keyframes + video stay approved
        state["stages"][s].update(status="generating")
    state["render_id"] = render_id
    state["render_phase"] = "full"
    _canvas_save(run_id, state)
    return jsonify({"render_id": render_id, "quality": quality,
                    "canvas": canvas_run.public_state(state)})


@app.route("/api/canvas/<run_id>/reroll", methods=["POST"])
@auth.require_operator()
def api_canvas_reroll(run_id: str, operator: str):
    """Re-roll ONE shot — regenerate its still + clip without re-running the whole
    render. Reuses _generate_stills (force) + clip_builder.build_clips (same path as
    /redo-still + /redo-motion), writing the new clip into the render dir so the
    board reveal picks it up. Single-frame spend gate so it can't blow the cap."""
    from agents import canvas_run, governance, model_router
    from agents.clip_builder import build_clips
    state = _canvas_load(run_id)
    if state is None:
        return jsonify({"error": "Unknown canvas"}), 404
    fid = (request.json or {}).get("frame_id", "")
    frame_payload = next((f for f in state.get("frames", []) if f.get("frame_id") == fid), None)
    if not frame_payload:
        return jsonify({"error": "Unknown shot"}), 400

    rid = state.get("render_id") or str(uuid.uuid4())
    state["render_id"] = rid
    quality = state.get("quality", "prod")
    max_frame_dur = 5.0 if quality == "dev" else 9.0
    run_dir = RUNS_DIR / rid
    run_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = str(run_dir / "assets")
    one_data = {"mode": "story", "quality": quality, "frames": [frame_payload],
                "orientation": "portrait", "video_model": "auto", "image_model": "auto",
                "mood": state.get("mood", ""), "session_id": rid, "operator_id": operator,
                "likeness_consent": {"face": True, "voice": True}}
    spend_missing = governance.reserve_spend(one_data, _estimate_payload_cost(one_data), run_id=rid)
    if spend_missing:
        return jsonify({"error": spend_missing[0]}), 400

    try:
        # 1. Fresh still — clear visual_path for AI specs so it regenerates a NEW image.
        _ps = (frame_payload.get("photo_spec") or "").strip()
        clear_vp = _ps.startswith("ai_") or not _ps
        fb = {**frame_payload, "visual_path": "" if clear_vp else frame_payload.get("visual_path", "")}
        frames = _build_frames_from_payload({**one_data, "frames": [fb], "assets_dir": ""}, max_frame_dur)
        frames = _generate_stills(frames, assets_dir, "", "", state.get("mood", ""),
                                  cost_tier=("draft" if quality == "dev" else "premium"),
                                  force_regen_ids={fid})
        frame = frames[0]
        # 2. Rebuild the clip from the new still (same as /redo-motion).
        width, height = 1080, 1920
        tier = model_router.cost_tier_from_quality(quality)
        model_id = model_router.select_model("video", frame, tier,
                                             override=frame.get("video_model_override", ""))
        assignment = {"segment_id": frame["frame_id"], "actual_duration": frame["duration"],
                      "media_path": frame["visual_path"], "text": frame.get("caption", ""),
                      "motion_prompt": frame.get("motion_override") or frame.get("scene", {}).get("motion_prompt", ""),
                      "video_start_sec": frame.get("video_start_sec", 0.0), "model_id": model_id}
        clip_temp = tempfile.mkdtemp(prefix="hob_canvas_reroll_")
        try:
            clips = build_clips([assignment], clip_temp, width, height, 30,
                                force_5s=(quality == "dev"),
                                kling_mode="pro", provider="kling")
            dst = str(run_dir / f"clip_{fid}.mp4")
            shutil.copy2(clips[0]["clip_path"], dst)
        finally:
            shutil.rmtree(clip_temp, ignore_errors=True)
        governance.release_reservation(one_data, run_id=rid, reason="canvas_reroll_done")
        governance.record_cost_event(governance.project_key(one_data), item="canvas_reroll",
                                     usd=_estimate_payload_cost(one_data), run_id=rid, event_type="estimate")
        _canvas_save(run_id, state)
        return jsonify({"frame_id": fid, "clip_path": dst})
    except Exception as e:
        try:
            governance.release_reservation(one_data, run_id=rid, reason="canvas_reroll_failed")
        except Exception:
            pass
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/canvas/<run_id>/chat", methods=["POST"])
@auth.require_operator()
def api_canvas_chat(run_id: str, operator: str):
    """Natural-language command box (Studio-Chat equivalent): refine + re-plan the
    shots via shot_planner. Reuses the brain; never a vendor SDK."""
    from agents import canvas_run
    state = _canvas_load(run_id)
    if state is None:
        return jsonify({"error": "Unknown canvas"}), 404
    message = (request.json or {}).get("message", "")
    state = canvas_run.chat(state, message)
    _canvas_save(run_id, state)
    return jsonify({"run_id": run_id, "canvas": canvas_run.public_state(state),
                    "reply": f"Re-planned {len(state.get('frames', []))} shots."})


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
        from agents.script_parser import extract_caption_block, parse_frame_script
        smart_match = bool(request.json.get("smart_match", False))
        frames = parse_frame_script(tmp.name, assets_dir or "", smart_match=smart_match)
        posting_caption = extract_caption_block(script_text)
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
            "layout":         f.get("layout", {}),
        })
    return jsonify({"frames": result, "cast": cast, "posting_caption": posting_caption})


def _posting_hashtags(text: str, frames: list[dict], limit: int = 12) -> list[str]:
    """Cheap story-mode hashtag helper; avoids ad-copy generation and vendor spend."""
    stop = {
        "about", "after", "again", "also", "and", "because", "before", "being",
        "from", "have", "into", "just", "more", "most", "that", "their", "there",
        "this", "through", "with", "without", "where", "while", "your",
    }
    source = " ".join([text] + [f.get("caption", "") for f in frames])
    words = re.findall(r"[A-Za-z][A-Za-z0-9]{3,}", source.lower())
    ranked = []
    seen = set()
    for w in words:
        if w in stop or w in seen:
            continue
        seen.add(w)
        ranked.append(w)
    base = ["reels", "shorts", "storytelling", "inspiration", "journey"]
    tags = base + ranked
    out = []
    for tag in tags:
        clean = re.sub(r"[^A-Za-z0-9]", "", tag.title())
        if clean and clean.lower() not in {t.lower().lstrip("#") for t in out}:
            out.append("#" + clean)
        if len(out) >= limit:
            break
    return out


@app.route("/posting-kit", methods=["POST"])
def posting_kit():
    data = request.json or {}
    if data.get("mode") == "brand":
        return jsonify({"error": "Posting kit AI copy is story-mode only."}), 400
    frames = data.get("frames") or []
    caption = (data.get("posting_caption") or "").strip()
    if not caption:
        caption = "\n".join(f.get("caption", "") for f in frames if f.get("caption"))
    cover_id = data.get("cover_frame_id") or (frames[0].get("frame_id") if frames else "")
    return jsonify({
        "caption": caption,
        "hashtags": _posting_hashtags(caption, frames),
        "cover_frame_id": cover_id,
    })


def _commercial_gate(data: dict, estimate_usd: float = 0.0) -> tuple[list[str], list[str]]:
    from agents import governance
    return governance.validate_consent(data), governance.check_spend_cap(data, estimate_usd)


@app.route("/story-intake", methods=["POST"])
def story_intake():
    """STR-2: raw story/notes/transcript -> editable Format B frame draft."""
    data = request.json or {}
    consent_missing, spend_missing = _commercial_gate(data, 0.0)
    if consent_missing or spend_missing:
        return jsonify({"error": "Commercial gate blocked", "missing": consent_missing + spend_missing}), 400
    from agents.growth import story_to_draft
    try:
        target_seconds = int(data.get("target_seconds") or 45)
    except Exception:
        target_seconds = 45
    try:
        max_frames = int(data.get("max_frames", 10) or 10)
    except Exception:
        max_frames = 10
    draft = story_to_draft(
        data.get("story", ""),
        max_frames,
        target_seconds=target_seconds,
        tone=data.get("tone", ""),
        audience=data.get("audience", ""),
    )
    return jsonify(draft)


@app.route("/hook-workshop", methods=["POST"])
def hook_workshop():
    """STR-5 pilot: generate low-cost opener candidates before full spend."""
    data = request.json or {}
    consent_missing, spend_missing = _commercial_gate(data, 0.0)
    if consent_missing or spend_missing:
        return jsonify({"error": "Commercial gate blocked", "missing": consent_missing + spend_missing}), 400
    from agents.growth import hook_candidates
    return jsonify({
        "status": "draft_scaffold",
        "confidence": "placeholder",
        "candidates": hook_candidates(data.get("frames") or []),
    })


@app.route("/languages")
def languages_route():
    """Supported output languages for the operator's multi-language picker."""
    from agents.languages import catalogue, DEFAULT_LANGUAGE
    return jsonify({"languages": catalogue(), "default": DEFAULT_LANGUAGE})


@app.route("/caption-variants", methods=["POST"])
def caption_variants():
    """STR-4: translate captions + voiceover into the operator's CHOSEN languages.

    No render spend — this is an LLM text translation only. The operator picks
    languages explicitly (never auto-fanned across all); unknown codes are dropped.
    """
    data = request.json or {}
    consent_missing, spend_missing = _commercial_gate(data, 0.0)
    if consent_missing or spend_missing:
        return jsonify({"error": "Commercial gate blocked", "missing": consent_missing + spend_missing}), 400
    from agents.growth import caption_language_variants
    from agents.languages import normalize_languages, catalogue
    requested = normalize_languages(data.get("languages") or [])
    if not requested:
        return jsonify({
            "error": "Choose at least one supported language",
            "supported": catalogue(),
        }), 400
    return jsonify({
        "status": "translated",
        "languages": requested,
        "variants": caption_language_variants(data.get("frames") or [], requested),
    })


@app.route("/render-variants", methods=["POST"])
def render_variants_route():
    """STR-3b pilot: return governed rerender payloads for variants/cutdowns."""
    data = request.json or {}
    consent_missing, spend_missing = _commercial_gate(data, _estimate_payload_cost(data))
    if consent_missing or spend_missing:
        return jsonify({"error": "Commercial gate blocked", "missing": consent_missing + spend_missing}), 400
    from agents.growth import render_variants
    return jsonify({"variants": render_variants(data)})


@app.route("/asset-library/register", methods=["POST"])
def asset_register():
    data = request.json or {}
    path = data.get("path", "")
    if not path or not _path_allowed(path) or not os.path.exists(path):
        return jsonify({"error": "Asset path not allowed or not found"}), 400
    from agents.product_surface import register_asset
    return jsonify({"asset": register_asset(
        path,
        kind=data.get("kind", ""),
        consent_flag=bool(data.get("consent_flag")),
        owner=data.get("owner", "default"),
    )})


@app.route("/brand-approval", methods=["POST"])
@auth.require_operator("approver")
def brand_approval(operator: str):
    data = request.json or {}
    project_id = data.get("project_id") or data.get("session_id") or "default"
    from agents.product_surface import record_approval
    # Approver identity comes from the verified token, never the request body.
    return jsonify({"approval": record_approval(project_id, data, approver=operator)})


@app.route("/project-version", methods=["POST"])
@auth.require_operator()
def project_version(operator: str):
    data = request.json or {}
    project_id = data.get("project_id") or data.get("session_id") or "default"
    from agents.product_surface import save_version
    return jsonify({"version": save_version(project_id, data.get("payload") or data, data.get("output_path", ""))})


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


@app.route("/suggest-frame", methods=["POST"])
def suggest_frame():
    """Vision-grounded suggestion for ONE frame (triggered post-Preview): looks at
    the generated still + caption → {camera, note}. Cached by image. The client
    sends the still's path (from Preview); we validate it against allowed roots."""
    data = request.json or {}
    image_path = (data.get("image_path") or "").strip()
    caption = data.get("caption", "")
    if not image_path or not _path_allowed(image_path) or not os.path.exists(image_path):
        return jsonify({"error": "No still for this frame yet — run Preview Stills first."}), 400
    try:
        from agents.suggestions import suggest_from_image
        return jsonify(suggest_from_image(image_path, caption))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/ips")
def get_ips():
    """HOB IP/property list for the watermark dropdown (both modes)."""
    try:
        from agents.watermark import list_ips
        return jsonify({"ips": list_ips()})
    except Exception as e:
        return jsonify({"error": str(e), "ips": []}), 500


@app.route("/balances")
def get_balances():
    """Live AI-vendor credit/balance probe (read-only) so the operator knows
    upfront whether a recharge is needed. Each vendor degrades independently."""
    try:
        from agents.balances import all_balances
        rows = all_balances()
        ok = sum(1 for r in rows if r["status"] == "ok")
        return jsonify({"balances": rows, "live_count": ok, "total": len(rows)})
    except Exception as e:
        return jsonify({"error": str(e), "balances": []}), 500


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
    try:
        from agents import governance
        from agents.pricing import music_cost
        spend_missing = governance.reserve_spend(data | {"session_id": session_id}, music_cost(), run_id=session_id)
        if spend_missing:
            return jsonify({"error": spend_missing[0]}), 400
    except Exception as e:
        print(f"[Governance] music spend guard skipped ({e})")

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
        try:
            from agents import governance
            from agents.pricing import music_cost
            governance.release_reservation(data | {"session_id": session_id}, run_id=session_id, reason="music_done")
            governance.record_cost_event(
                governance.project_key(data | {"session_id": session_id}),
                item="music_estimate",
                usd=music_cost(),
                run_id=session_id,
                vendor="suno",
                event_type="estimate",
            )
        except Exception as ge:
            print(f"[Governance] music ledger record skipped ({ge})")
        return jsonify({"music_path": music_path, "session_id": session_id,
                        "prompt_used": prompt})
    except Exception as e:
        try:
            from agents import governance
            governance.release_reservation(data | {"session_id": session_id}, run_id=session_id, reason="music_failed")
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500


def _check_assets_dir(data: dict):
    """Reject payloads whose typed assets_dir escapes the allowed roots."""
    assets_dir = (data.get("assets_dir") or "").strip()
    if assets_dir and not _path_allowed(assets_dir):
        return jsonify({"error": f"Assets folder must be inside {ASSETS_BROWSE_ROOT}"}), 403
    return None


def _estimate_payload_cost(data: dict) -> float:
    """Server-truth estimate used by spend governance before dispatch."""
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
    return float(b.get("total", 0.0))


@app.route("/run", methods=["POST"])
@auth.require_operator()
def run_pipeline(operator: str):
    data = request.json or {}
    data["operator_id"] = operator   # verified identity, not client-supplied
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
    data["session_id"] = session_id
    from agents import governance
    missing = governance.validate_consent(data)
    if missing:
        return jsonify({"error": "Consent / rights requirements missing", "missing": missing}), 400
    governance.record_consent(data, confirmed_by=operator)
    # Gap #4: AI face/voice of a named real person needs explicit, modality-specific
    # consent before any spend — the authenticity-moat gate.
    likeness_missing = governance.validate_likeness_consent(data)
    if likeness_missing:
        return jsonify({"error": "Likeness consent required", "missing": likeness_missing,
                        "needs_likeness_consent": governance.likeness_modalities(data)}), 400
    governance.record_likeness_consent(data, confirmed_by=operator)
    spend_missing = governance.reserve_spend(data, _estimate_payload_cost(data), run_id=session_id)
    if spend_missing:
        return jsonify({"error": "Spend cap exceeded", "missing": spend_missing}), 400

    run_dir = RUNS_DIR / session_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Gap #5: write the provenance/authenticity summary as a run artifact so it can
    # be served, badged in the UI, and shipped in the editor export.
    try:
        from agents import provenance
        (run_dir / "provenance.json").write_text(json.dumps(provenance.summarize(data), indent=2))
    except Exception as e:
        print(f"[Provenance] skipped ({e})")

    with _runs_lock:
        _runs[session_id] = {"status": "running", "log": [], "output_path": None,
                             "clips": {}, "events": []}
    try:
        from agents import run_store
        run_store.save(session_id, status="running", payload=data, run_dir=str(run_dir),
                       log=[], output_path=None)
    except Exception as e:
        print(f"[RunStore] save skipped ({e})")

    thread = threading.Thread(
        target=_execute_pipeline,
        args=(session_id, data, run_dir),
        daemon=True,
    )
    thread.start()

    return jsonify({"run_id": session_id})


@app.route("/retry/<run_id>", methods=["POST"])
@auth.require_operator()
def retry_run(run_id: str, operator: str):
    """Re-dispatch a stored run payload; paid work should hit content caches."""
    from agents import run_store
    stored = run_store.load(run_id)
    if not stored or not stored.get("payload"):
        return jsonify({"error": "No stored payload for run"}), 404
    data = stored["payload"]
    session_id = data.get("session_id", run_id)
    data["session_id"] = session_id
    from agents import governance
    spend_missing = governance.reserve_spend(data, _estimate_payload_cost(data), run_id=session_id)
    if spend_missing:
        return jsonify({"error": "Spend cap exceeded", "missing": spend_missing}), 400
    run_dir = RUNS_DIR / session_id
    run_dir.mkdir(parents=True, exist_ok=True)
    with _runs_lock:
        _runs[session_id] = {"status": "running", "log": [], "output_path": None,
                             "clips": {}, "events": []}
    run_store.save(session_id, status="running", payload=data, run_dir=str(run_dir), log=[])
    threading.Thread(target=_execute_pipeline, args=(session_id, data, run_dir), daemon=True).start()
    return jsonify({"run_id": session_id})


@app.route("/preview", methods=["POST"])
@auth.require_operator()
def preview_stills(operator: str):
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
    data["session_id"] = session_id
    from agents import governance
    missing = governance.validate_consent(data)
    if missing:
        return jsonify({"error": "Consent / rights requirements missing", "missing": missing}), 400
    governance.record_consent(data)

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
            if not run:
                try:
                    from agents import run_store
                    run = run_store.load(run_id) or {}
                except Exception:
                    run = {}
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
    if not path:
        try:
            from agents import run_store
            stored = run_store.load(run_id) or {}
            path = stored.get("output_path")
        except Exception:
            path = None
    if not path or not os.path.exists(path):
        return "Not ready", 404
    return send_file(path, mimetype="video/mp4")


@app.route("/download/<run_id>")
def download(run_id: str):
    with _runs_lock:
        run = _runs.get(run_id, {})
    path = run.get("output_path")
    if not path:
        try:
            from agents import run_store
            stored = run_store.load(run_id) or {}
            path = stored.get("output_path")
        except Exception:
            path = None
    if not path or not os.path.exists(path):
        return "Not ready", 404
    return send_file(path, as_attachment=True, download_name="hobaigabs_reel.mp4")


@app.route("/export/<run_id>")
def export_run(run_id: str):
    """Download a lightweight editor package for one completed run."""
    run_dir = RUNS_DIR / run_id
    manifest = run_dir / "edit_list.json"
    if not manifest.exists():
        return "Export not ready", 404
    zip_path = run_dir / "editor_export.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.write(manifest, "edit_list.json")
        # Importable timeline + standard captions for the editor's own NLE,
        # plus the provenance/authenticity summary (Gap #5) for disclosure.
        for extra in ("timeline.fcpxml", "captions.srt", "provenance.json"):
            p = run_dir / extra
            if p.exists():
                z.write(p, extra)
        for clip_path in sorted(run_dir.glob("clip_*.mp4")):
            z.write(clip_path, f"clips/{clip_path.name}")
        output_path = run_dir / "output.mp4"
        if output_path.exists():
            z.write(output_path, "output.mp4")
    return send_file(zip_path, as_attachment=True, download_name=f"{run_id}_editor_export.zip")


@app.route("/provenance/<run_id>")
def provenance_for_run(run_id: str):
    """Authenticity/provenance summary for a run (Gap #5). Real vs AI-symbolic vs
    AI-likeness-of-a-real-person, with a disclosable label."""
    pf = RUNS_DIR / run_id / "provenance.json"
    if pf.exists():
        try:
            return jsonify(json.loads(pf.read_text()))
        except Exception:
            pass
    # Fall back to computing from the stored payload if the artifact is absent.
    try:
        from agents import run_store, provenance
        stored = run_store.load(run_id) or {}
        if stored.get("payload"):
            return jsonify(provenance.summarize(stored["payload"]))
    except Exception:
        pass
    return jsonify({"label": None}), 404


@app.route("/performance", methods=["GET"])
@auth.require_operator()
def performance_list(operator: str):
    """Completed feedback loop (Gap #3): which reels performed, best first."""
    from agents import run_store
    return jsonify({"runs": run_store.list_performance(), "summary": run_store.performance_summary()})


@app.route("/performance/<run_id>", methods=["POST"])
@auth.require_operator()
def performance_feedback(run_id: str, operator: str):
    """Post-publish feedback loop: capture how a finished reel performed.

    Stored on the existing run row via run_store. Only the finished output panel
    exposes this, so no in-flight save() is racing these fields.
    """
    from agents import run_store
    if not run_store.load(run_id):           # don't create phantom rows for unknown runs
        return jsonify({"error": "unknown run"}), 404
    data = request.get_json(silent=True) or {}   # never 500 on a malformed/empty body

    def _int(v):
        try:
            return max(0, int(v)) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None

    note = str(data.get("note", ""))[:2000]
    run_store.save(
        run_id,
        performance_views=_int(data.get("views")),
        performance_likes=_int(data.get("likes")),
        performance_note=note,
        performance_by=operator,          # verified operator who logged the result
    )
    return jsonify({"ok": True, "run_id": run_id})


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
    data["session_id"] = session_id
    from agents import governance
    # Estimate cost for just this one frame — not the whole payload — so we don't
    # falsely burn the spend cap when only a single image is being regenerated.
    _single_frame_data = {**data, "frames": [frame_payload]}
    spend_missing = governance.reserve_spend(data, _estimate_payload_cost(_single_frame_data), run_id=session_id)
    if spend_missing:
        return jsonify({"error": spend_missing[0]}), 400
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
    # For AI-generate specs (ai_portrait / ai_symbolic) and fresh uploads, clear
    # visual_path so _build_frames_from_payload honours the photo_spec instead of
    # silently reusing the stale cached result that visual_path points at.
    # Real-photo frames (photo_spec="") keep visual_path — the still IS the photo.
    # The normal render path keeps visual_path too, so redo results are reused there.
    _ps = (frame_payload.get("photo_spec") or "").strip()
    _vp_base = os.path.basename(frame_payload.get("visual_path") or "")
    _VIDEO_EXTS_STILL = {".mp4", ".mov", ".avi", ".m4v", ".webm", ".mkv"}
    # Clear visual_path when the user explicitly chose AI generation, uploaded a new
    # photo, OR the existing visual_path is itself an AI-generated file (covers "auto"
    # frames that fell through to the AI fallback — their photo_spec is "" but the
    # file is named ai_portrait_* / ai_symbolic_*).
    # Also clear when visual_path is a VIDEO — a video has no "still" to redo;
    # the only meaningful result is an AI portrait, regardless of whether the user
    # explicitly switched the type selector to AI Portrait first.
    _clear_vp = (_ps.startswith("ai_") or _ps == "uploaded"
                 or _vp_base.startswith("ai_portrait_") or _vp_base.startswith("ai_symbolic_")
                 or os.path.splitext(_vp_base)[1].lower() in _VIDEO_EXTS_STILL)
    frame_for_build = {**frame_payload, "visual_path": "" if _clear_vp else frame_payload.get("visual_path", "")}
    one = dict(data)
    one["frames"] = [frame_for_build]
    try:
        frames = _build_frames_from_payload(one, max_frame_dur)
        # Force a fresh image even if a same-prompt cached file exists — the editor
        # explicitly asked to redo this frame.
        frames = _generate_stills(frames, assets_dir, subject_name, subject_desc, mood,
                                  cost_tier=("draft" if quality == "dev" else "premium"),
                                  face_ref=bool(data.get("face_ref")), brand=brand,
                                  force_regen_ids={frame_payload["frame_id"]})
    except Exception as e:
        try:
            from agents import governance
            governance.release_reservation(data, run_id=session_id, reason="redo_still_failed")
        except Exception:
            pass
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

    f = frames[0]
    vp = f.get("visual_path", "")
    _VIDEO_EXTS = {".mp4", ".mov", ".avi", ".m4v", ".webm"}
    is_video = bool(vp and os.path.splitext(vp)[1].lower() in _VIDEO_EXTS)
    try:
        from agents import governance
        estimate_usd = _estimate_payload_cost(_single_frame_data)
        governance.release_reservation(data, run_id=session_id, reason="redo_still_done")
        governance.record_cost_event(
            governance.project_key(data),
            item="redo_still_estimate",
            usd=estimate_usd,
            run_id=session_id,
            event_type="estimate",
        )
    except Exception as ge:
        print(f"[Governance] redo-still ledger record skipped ({ge})")
    return jsonify({
        "frame_id": f["frame_id"],
        "path":     vp,
        "is_video": is_video,
        "exists":   bool(vp and os.path.exists(vp)),
    })


@app.route("/redo-motion", methods=["POST"])
def redo_motion():
    """Rebuild motion for one approved still; keep the still image unchanged."""
    data = request.json or {}
    err = _check_assets_dir(data)
    if err:
        return err
    frame_payload = data.get("frame")
    if not frame_payload or not frame_payload.get("frame_id"):
        return jsonify({"error": "No frame supplied"}), 400
    if not frame_payload.get("visual_path"):
        return jsonify({"error": "Frame has no generated still yet"}), 400

    session_id = data.get("session_id", str(uuid.uuid4()))
    data["session_id"] = session_id
    from agents import governance
    # Single-frame estimate only — the full payload includes all frames' animation
    # which would massively overstate the reservation and falsely block the redo.
    _single_frame_data_m = {**data, "frames": [frame_payload]}
    spend_missing = governance.reserve_spend(data, _estimate_payload_cost(_single_frame_data_m), run_id=session_id)
    if spend_missing:
        return jsonify({"error": spend_missing[0]}), 400
    run_dir = RUNS_DIR / session_id
    run_dir.mkdir(parents=True, exist_ok=True)
    quality = data.get("quality", "dev")
    max_frame_dur = 5.0 if quality == "dev" else 9.0
    width, height = _orient_wh(data.get("orientation"))   # 9:16 / 16:9 / 1:1
    fps = int(data.get("fps", 30))

    try:
        from agents import model_router
        from agents.clip_builder import build_clips
        one = dict(data)
        one["frames"] = [frame_payload]
        frame = _build_frames_from_payload(one, max_frame_dur)[0]
        tier = model_router.cost_tier_from_quality(quality)
        frame["video_model_override"] = frame.get("video_model_override") or data.get("video_model", "")
        model_id = model_router.select_model("video", frame, tier, override=frame.get("video_model_override", ""))
        assignment = {
            "segment_id": frame["frame_id"],
            "actual_duration": frame["duration"],
            "media_path": frame["visual_path"],
            "text": frame.get("caption", ""),
            "motion_prompt": frame.get("motion_override") or frame.get("scene", {}).get("motion_prompt", ""),
            "video_start_sec": frame.get("video_start_sec", 0.0),
            "model_id": model_id,
        }
        clip_temp = tempfile.mkdtemp(prefix="hob_redo_motion_")
        try:
            clips = build_clips([assignment], clip_temp, width, height, fps,
                                force_5s=(quality == "dev"),
                                kling_mode=data.get("kling_mode", "pro"),
                                provider=data.get("provider", "kling"))
            dst = str(run_dir / f"clip_{frame['frame_id']}.mp4")
            shutil.copy2(clips[0]["clip_path"], dst)
        finally:
            shutil.rmtree(clip_temp, ignore_errors=True)

        with _runs_lock:
            run = _runs.setdefault(session_id, {"status": "running", "log": [], "clips": {}, "events": []})
            run.setdefault("clips", {})[frame["frame_id"]] = dst

        try:
            estimate_usd = _estimate_payload_cost(_single_frame_data_m)
            governance.release_reservation(data, run_id=session_id, reason="redo_motion_done")
            governance.record_cost_event(
                governance.project_key(data),
                item="redo_motion_estimate",
                usd=estimate_usd,
                run_id=session_id,
                event_type="estimate",
            )
        except Exception as ge:
            print(f"[Governance] redo-motion ledger record skipped ({ge})")

        return jsonify({
            "frame_id": frame["frame_id"],
            "url": f"/clip/{session_id}/{frame['frame_id']}",
            "output_ready": False,
        })
    except Exception as e:
        try:
            from agents import governance
            governance.release_reservation(data, run_id=session_id, reason="redo_motion_failed")
        except Exception:
            pass
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ── Shared frame/still helpers (used by both preview and full render) ──────────

def _build_frames_from_payload(data: dict, max_frame_dur: float) -> list[dict]:
    """Build the frames list from the UI payload (shared by preview + render)."""
    input_assets = data.get("assets_dir", "").strip()
    # Optional user-supplied character face(s): {speaker_id: server_path}. Locks
    # that speaker's AI portraits to one chosen face (reuses the talent-ref seam).
    # Honored ONLY with explicit consent — the face may be a real person, so this
    # is the §5 authenticity / AI-likeness gate (consent must precede the render).
    character_refs = data.get("character_refs") if isinstance(data.get("character_refs"), dict) else {}
    if character_refs and not data.get("character_ref_consent"):
        print("[Identity] Character face supplied without consent — ignoring "
              "(consent is required before rendering a person's AI likeness).")
        character_refs = {}
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
        payload_visual = (fd.get("visual_path") or "").strip()

        if payload_visual and _path_allowed(payload_visual) and os.path.exists(payload_visual):
            visual_path = payload_visual
            photo_spec = ""
        elif photo_spec == "uploaded" and photo_tmp and os.path.exists(photo_tmp):
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

        # Studio Mode (MODE3): resolve locked Talent/Product references. A talent
        # gives every uses_talent shot a face to lock to (reference-edit identity);
        # a product gives product beats the REAL product image (passthrough), so
        # micro-detail (gold, diamonds, logos) is never re-invented by a model.
        talent_id = (fd.get("talent_id") or "").strip()
        product_id = (fd.get("product_id") or "").strip()
        talent_ref_path = ""
        if talent_id or product_id:
            try:
                from agents import product_surface as _ps
                if talent_id and fd.get("uses_talent", True):
                    tal = _ps.get_talent(talent_id)
                    if tal and tal.get("ref_path") and os.path.exists(tal["ref_path"]):
                        talent_ref_path = tal["ref_path"]
                if product_id and fd.get("product_beat") and not visual_path:
                    prd = _ps.get_product(product_id)
                    if prd and prd.get("ref_path") and os.path.exists(prd["ref_path"]):
                        visual_path = prd["ref_path"]   # real product → i2v start frame
                        photo_spec = ""
            except Exception as _e:
                print(f"[Studio] identity resolve skipped for {fd.get('frame_id')} ({_e})")

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
            "layout":          fd.get("layout") or {},
            # Per-frame caption overrides (blank = use the global caption style).
            "caption_position":  (fd.get("caption_position") or "").strip(),
            "caption_max_lines": fd.get("caption_max_lines") or "",
            # Studio Mode identity + per-shot controls (harmless in story/brand).
            "talent_id":       talent_id,
            "product_id":      product_id,
            "talent_ref_path": talent_ref_path,
            # Story/Brand supply a per-speaker character_refs dict; the Canvas sets the
            # face reference directly on the frame (Replace → 🎭 AI face, Characters stage).
            # Fall back to the frame's own ref so the uploaded face actually conditions the
            # generation instead of being dropped.
            "character_ref_path": (character_refs.get(_speaker_id)
                                   or fd.get("character_ref_path") or "").strip(),
            "negative_prompt": (fd.get("negative_prompt") or "").strip(),
            "continuity_lock": (fd.get("continuity_lock") or "").strip(),
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

    # LAY-0 pilot extraction: render layout frames through one shared layout seam.
    try:
        from agents import layout as layout_mod
        for f in frames:
            if layout_mod.is_layout_frame(f):
                out = os.path.join(assets_dir, f"layout_{f['frame_id']}.jpg")
                layout_mod.render_layout_frame(f, out)
                f["visual_path"] = out
                f["motion_override"] = f.get("motion_override") or "static"
                f.setdefault("scene", {"motion_prompt": "static"})
                print(f"[Layout] {f['frame_id']}: text card rendered")
    except Exception as e:
        print(f"[Layout] text-card render failed ({e}) — continuing with normal visual generation")

    # Per-frame redo: delete ALL cached stills for these frame_ids so the
    # prompt-hash file-reuse check misses and a fresh image is generated.
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
    design_frames = [f for f in frames if not (f.get("layout") or {}).get("preset")]
    if design_frames:
        design_all_scenes(design_frames, subject_name=subject_name,
                          subject_description=subject_description, mood=mood,
                          extra_context=extra_context)

    # Redo variation seed: stamped AFTER scene design so it survives the f["scene"]
    # assignment inside design_all_scenes. This makes even an identical director-note
    # produce a different prompt hash → a genuinely fresh image each time.
    if force_regen_ids:
        import time
        for f in frames:
            if f.get("frame_id") in force_regen_ids:
                f.setdefault("scene", {})
                f["scene"]["_redo_seed"] = str(int(time.time() * 1000))

    # Apply mood to every AI image prompt
    mood_suffix = MOOD_MAP.get(mood, "")
    if mood_suffix:
        for f in frames:
            ip = f.get("scene", {}).get("image_prompt", "")
            if ip:
                f["scene"]["image_prompt"] = ip + ". " + mood_suffix

    # Studio Mode: a per-shot continuity lock (outfit/styling that must not change)
    # is appended to the image prompt so generation respects it across shots.
    for f in frames:
        lock = (f.get("continuity_lock") or "").strip()
        ip = f.get("scene", {}).get("image_prompt", "")
        if lock and ip:
            f["scene"]["image_prompt"] = ip + ". Keep consistent: " + lock

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
        if (f.get("layout") or {}).get("preset") and f.get("visual_path"):
            continue
        if f.get("product_beat"):
            if not (f.get("visual_path") and os.path.exists(f["visual_path"])):
                print(f"[Brand] {f['frame_id']}: product beat has no real asset — leaving blank")
            continue
        ps = f.get("photo_spec", "")
        img_model = model_router.select_model(
            "image", f, cost_tier, override=f.get("image_model_override", ""))
        mid = "" if img_model == model_router.PASSTHROUGH else img_model
        sid = f.get("speaker_id", "narrator")
        # Studio Mode: an explicit locked Talent reference wins over the auto
        # per-speaker face_ref, so every shot locks to the same chosen face.
        talent_ref = (f.get("talent_ref_path") or "").strip()
        char_ref = (f.get("character_ref_path") or "").strip()  # Story/Brand: user-supplied face
        if talent_ref and os.path.exists(talent_ref):
            ref = talent_ref
        elif char_ref and os.path.exists(char_ref):
            ref = char_ref  # every portrait of this speaker reference-edits to the supplied face
        else:
            # D2: a set-but-missing ref would otherwise be dropped SILENTLY (random face).
            # Surface it in the render log so the operator knows to re-attach the photo.
            if talent_ref or char_ref:
                fallback = "reusing the speaker's first portrait" if first_portrait_by_speaker.get(sid) else "generating a fresh face"
                print(f"[Identity] {f.get('frame_id')}: face reference not found on disk "
                      f"({os.path.basename(talent_ref or char_ref)}) — {fallback}")
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

    _VIDEO_EXTS = {".mp4", ".mov", ".avi", ".m4v", ".webm", ".mkv"}
    # Edit pass — prompt-hashed filename so identical edits are reused (no re-pay).
    # Videos are skipped — the image-edit API only accepts JPEG/PNG/WebP.
    for f in frames:
        prompt = f.get("edit_prompt", "")
        vp = f.get("visual_path", "")
        if os.path.splitext(vp)[1].lower() in _VIDEO_EXTS:
            if prompt:
                print(f"[ImageEditor] {f['frame_id']}: skipping edit on video source (API does not accept video)")
            continue
        if prompt and vp and os.path.exists(vp):
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


def _write_edit_list(run_dir: Path, frames: list[dict], clips: list[dict],
                     frame_times: list[tuple[float, float]], transition: str,
                     output_path: str) -> str:
    """Persist a lightweight editor manifest for STR-6a export."""
    manifest = {
        "version": 1,
        "transition": transition,
        "output_path": output_path,
        "frames": [],
    }
    for i, f in enumerate(frames):
        start, end = frame_times[i] if i < len(frame_times) else (0.0, float(f.get("duration", 0.0)))
        fid = f["frame_id"]
        segment_clips = [
            {
                "segment_id": c.get("segment_id"),
                "clip_path": c.get("clip_path"),
                "duration": c.get("actual_duration"),
            }
            for c in clips
            if c.get("segment_id") == fid or str(c.get("segment_id", "")).startswith(fid + "_")
        ]
        manifest["frames"].append({
            "frame_id": fid,
            "caption": f.get("caption", ""),
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(max(0.0, end - start), 3),
            "source_visual": f.get("visual_path", ""),
            "clips": segment_clips,
        })
    out = run_dir / "edit_list.json"
    out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return str(out)


# ── Pipeline execution ────────────────────────────────────────────────────────

def _execute_pipeline(run_id: str, data: dict, run_dir: Path):
    _thread_run.run_id = run_id   # route this thread's prints into this run's log

    def _finish(status: str):
        with _runs_lock:
            _runs[run_id]["status"] = status
        try:
            from agents import run_store
            run_store.save(run_id, status=status)
        except Exception:
            pass

    try:
        _run_inner(run_id, data, run_dir)
        _finish("done")
    except Exception as e:
        import traceback
        with _runs_lock:
            _runs[run_id]["log"].append(f"✗ Error: {e}")
            _runs[run_id]["log"].append(traceback.format_exc())
        try:
            from agents import run_store
            run_store.save(run_id, status="error", error=str(e))
        except Exception:
            pass
        try:
            from agents import governance
            governance.release_reservation(data, run_id=run_id, reason="render_failed")
        except Exception:
            pass
        _finish("error")
    finally:
        _thread_run.run_id = None   # pooled threads are reused — don't leak the binding


def _canvas_render_thread(run_id: str, data: dict, run_dir: Path):
    """Canvas render = generate a music bed first (so the engine's beat-aware cutting
    has beats to snap cuts to — the anti-slideshow fix, P1), then run the proven
    pipeline. Music is best-effort: on any failure the reel still renders (uniform
    cutting), never a hard fail."""
    _thread_run.run_id = run_id
    try:
        if data.get("music_type") == "generate" and not data.get("music_path"):
            from agents.music_generator import generate_music, compose_music_brief
            music_path = str(run_dir / "music.mp3")
            brief = compose_music_brief([f.get("caption", "") for f in data.get("frames", [])],
                                        mood=data.get("mood", ""))
            print("[Canvas] Generating music bed (enables beat-aware cutting)…")
            generate_music(brief, music_path)
            if os.path.exists(music_path):
                data["music_path"] = music_path
                print("[Canvas] ✓ music bed ready — cuts will land on the beat")
    except Exception as e:
        print(f"[Canvas] music bed skipped ({e}) — uniform cutting")
    finally:
        _thread_run.run_id = None
    _execute_pipeline(run_id, data, run_dir)


def _execute_preview(run_id: str, data: dict, run_dir: Path):
    _thread_run.run_id = run_id   # route this thread's prints into this run's log

    def _finish(status: str):
        with _runs_lock:
            _runs[run_id]["status"] = status
        try:
            from agents import run_store
            run_store.save(run_id, status=status)   # persist so /rendered sees 'done'
        except Exception:
            pass

    try:
        _preview_inner(run_id, data, run_dir)
        _finish("done")
    except Exception as e:
        import traceback
        with _runs_lock:
            _runs[run_id]["log"].append(f"✗ Error: {e}")
            _runs[run_id]["log"].append(traceback.format_exc())
        _finish("error")
    finally:
        _thread_run.run_id = None   # pooled threads are reused — don't leak the binding


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
                "negative_prompt":   f.get("negative_prompt", ""),
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

        # Per-stage gate (canvas): stop after the clips so the operator approves the
        # Video stage before the Final Cut assembles. The clips persist in the run dir
        # AND the content-hash clip cache, so the later Final Cut render reuses them
        # (no re-spend) and only does audio + assembly.
        if data.get("stop_after") == "clips":
            print(f"[Pipeline] Video stage: {len(clips)} clips built — stopping before "
                  f"assembly (approve Video, then run Final Cut).")
            shutil.rmtree(clip_temp, ignore_errors=True)
            return

        # Beat-aware cutting (P1): for music-bed reels, derive per-junction
        # overlaps from the music's beats so cuts land ON the beat (punchy) instead
        # of a uniform dissolve. Voiceover/brand stay uniform — you don't beat-cut
        # narration. None ⇒ today's exact uniform-crossfade behaviour.
        from agents.assembler import beat_overlaps
        _mt = data.get("music_type")
        _music_bed = data.get("music_path") if _mt in ("upload", "generate") else None
        # `beat_grid_bpm` keeps cutting rhythmic even with no music bed (e.g. Suno
        # credits out) — a synthetic tempo grid instead of uniform crossfades.
        overlaps = beat_overlaps(clips, _music_bed, transition,
                                 fallback_bpm=float(data.get("beat_grid_bpm", 0) or 0))

        # Effective per-frame windows in the rendered video — overlaps shift clip
        # starts, so every timing consumer below uses these, not raw durations.
        frame_times = frame_timecodes(frames, clips, transition, overlaps)

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
        # HOB IP/property watermark (both modes): full-frame PNG over the whole reel.
        from agents.watermark import watermark_for
        watermark_path = watermark_for(data.get("ip", ""))

        output_path = str(run_dir / "output.mp4")
        # A post-pass is needed for a brand overlay OR an IP watermark; otherwise
        # assemble straight to the final output (one encode).
        needs_overlay = is_brand or bool(watermark_path)
        assemble_target = str(run_dir / "_raw_output.mp4") if needs_overlay else output_path
        assemble_caption_only(clips, clip_temp, assemble_target,
                              music_path=music_path, srt_path=ass_path,
                              transition=transition, is_voiceover=is_vo,
                              bg_music_path=bg_music_path, overlaps=overlaps)

        # Single overlay post-pass: IP watermark (both modes) + brand disclosure/logo
        # (brand only) composited together so the final video is encoded once.
        if needs_overlay:
            from agents.assembler import apply_brand_overlay
            disc, logo, corner = "", "", "tr"
            if is_brand:
                from agents.brand import disclosure_text
                disc = disclosure_text(brand) if brand.get("disclosure", True) else ""
                logo = brand.get("logo_path", "") if brand.get("logo_bug") else ""
                corner = brand.get("logo_corner", "tr")
            if watermark_path:
                print(f"[Watermark] applying IP layer: {data.get('ip','')}")
            apply_brand_overlay(
                assemble_target, output_path,
                disclosure_text=disc, logo_path=logo, logo_corner=corner,
                watermark_path=watermark_path, width=width, height=height)

        total = sum(f["duration"] for f in frames)
        edit_list_path = _write_edit_list(run_dir, frames, clips, frame_times, transition, output_path)
        print(f"[Export] edit list ready → {edit_list_path}")
        # Editor hand-off: an importable FCPXML timeline (Premiere/Resolve/FCP) + a
        # standard SRT, so the operator can finish the last 10% in their own tool.
        try:
            from agents.fcpxml import build_fcpxml, build_srt
            (run_dir / "timeline.fcpxml").write_text(
                build_fcpxml(frames, width, height, fps), encoding="utf-8")
            (run_dir / "captions.srt").write_text(
                build_srt(frames, frame_times), encoding="utf-8")
            print("[Export] FCPXML timeline + SRT captions ready")
        except Exception as e:
            print(f"[Export] FCPXML/SRT skipped ({e})")
        try:
            from agents import governance
            estimated_usd = _estimate_payload_cost(data)
            governance.release_reservation(data, run_id=run_id, reason="render_done")
            governance.record_cost_event(
                governance.project_key(data),
                item="render_estimate",
                usd=estimated_usd,
                run_id=run_id,
                event_type="estimate",
            )
            print(f"[Governance] ledger recorded render estimate ${estimated_usd:.2f}")
        except Exception as e:
            print(f"[Governance] ledger record skipped ({e})")
        print(f"\n✓ Done! {total:.1f}s → output ready")
        with _runs_lock:
            _runs[run_id]["output_path"] = output_path
            _runs[run_id]["edit_list_path"] = edit_list_path
        try:
            from agents import run_store
            run_store.save(run_id, output_path=output_path, edit_list_path=edit_list_path,
                           run_dir=str(run_dir))
        except Exception:
            pass

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
