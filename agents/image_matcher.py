"""
Content-aware image → frame matcher (OpenAI GPT-4o vision).

Replaces blind alphabetical auto-matching for frames that have no explicit
[photo:] pin. Two stages, both cheap and cached:

  1. describe_images()  — one GPT-4o vision call per image (who/what is shown,
     any TEXT or names visible, setting, mood). Cached FOREVER by image content
     hash in ~/.hob_cache/image_descriptions.json — so each image is described
     once, even across stories or after a rename.

  2. assign_images()    — ONE GPT-4o reasoning call: given every candidate
     image's description + every frame that needs a visual, return a global
     frame → image assignment. Because it reads the descriptions (which include
     text in the image), it can place a photo captioned "Nima Denzongpa" onto
     the "I became TV's Nima Denzongpa" beat, and reason about emotional fit.

smart_match() is gated and SAFE:
  · only fills frames with no [photo:] pin, no ai_portrait/ai_symbolic, no video
  · excludes images already pinned by other frames
  · returns None (→ caller keeps positional matching) if OPENAI_API_KEY is
    missing, there are no candidates, or anything errors
  · NEVER touches the animation stage (it only chooses WHICH still to use)
"""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from agents import llm
from agents._kv import KVStore

CACHE_DB     = Path.home() / ".hob_cache" / "image_descriptions.db"
_LEGACY_JSON = Path.home() / ".hob_cache" / "image_descriptions.json"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}   # vision-safe formats
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".m4v", ".webm"}

_store: KVStore | None = None


def _img_hash(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def _cache() -> KVStore:
    """Lazily open the SQLite description cache, importing any legacy JSON once.

    Per-key writes under WAL are concurrency-safe, replacing the old whole-file
    JSON rewrite that raced under describe_images()'s thread pool.
    """
    global _store
    if _store is None:
        _store = KVStore(CACHE_DB, table="descriptions")
        _migrate_legacy_json(_store)
    return _store


def _migrate_legacy_json(store: KVStore) -> None:
    if not _LEGACY_JSON.exists():
        return
    try:
        data = json.loads(_LEGACY_JSON.read_text())
        for k, v in data.items():
            if isinstance(v, str) and k not in store:
                store.set(k, v)
        _LEGACY_JSON.rename(_LEGACY_JSON.with_suffix(".json.bak"))
        print(f"[Matcher] Migrated {len(data)} cached descriptions JSON → SQLite")
    except Exception as e:
        print(f"[Matcher] description cache migration skipped ({e})")


# ── Video keyframes → vision description ──────────────────────────────────────

_VIDEO_DESCRIBE_PROMPT = (
    "These are 1-3 keyframes sampled from a single REAL VIDEO CLIP, in order. "
    "Describe the clip in 1-2 sentences for a video editor: who or what is shown, "
    "ANY text/names/captions visible (quote them), the setting, the action/motion, "
    "and the mood. Be concrete."
)


def _probe_duration(path: str) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nk=1:nw=1", path],
            capture_output=True, text=True, timeout=30,
        )
        return float((out.stdout or "0").strip() or 0.0)
    except Exception:
        return 0.0


def _extract_keyframes(path: str, tmpdir: str, n: int = 3) -> list[str]:
    """Sample up to n keyframes (start/middle/end) as small JPEGs."""
    dur = _probe_duration(path)
    if dur and dur > 0.5:
        ts = [round(dur * f, 2) for f in (0.15, 0.5, 0.85)[:n]]
    else:
        ts = [0.0]
    frames = []
    for i, t in enumerate(ts):
        out = os.path.join(tmpdir, f"kf_{i}.jpg")
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-ss", str(t), "-i", path, "-frames:v", "1",
                 "-q:v", "3", "-vf", "scale=512:-1", out],
                capture_output=True, timeout=60,
            )
            if os.path.exists(out) and os.path.getsize(out) > 2000:
                frames.append(out)
        except Exception:
            pass
    return frames


def _describe_video(path: str) -> str:
    """Describe a video clip from its keyframes via the vision LLM."""
    tmp = tempfile.mkdtemp(prefix="hob_kf_")
    try:
        frames = _extract_keyframes(path, tmp)
        if not frames:
            return "(real video clip) " + os.path.basename(path)
        content = [{"type": "text", "text": _VIDEO_DESCRIBE_PROMPT}]
        for f in frames:
            content.append({"type": "image", "path": f})
        desc = llm.chat([{"role": "user", "content": content}],
                        max_tokens=160, model_tier="fast").strip()
        return "(real video clip) " + desc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── Stage 1: describe each image once (cached) ────────────────────────────────

_DESCRIBE_PROMPT = (
    "Describe this photo in 1-2 sentences for a video editor choosing shots. "
    "Include: who or what is shown, ANY text, names, or captions visible in the "
    "image (quote them), the setting, and the mood/emotion. Be concrete."
)


def describe_images(paths: list[str]) -> dict:
    """Return {path: description} for images AND videos. Caches by content hash.
    Images are described directly; videos via sampled keyframes."""
    store = _cache()
    out = {}

    for p in paths:
        try:
            key = _img_hash(p)
        except Exception:
            continue
        hit = store.get(key)
        if hit is not None:
            out[p] = hit
            continue
        is_video = os.path.splitext(p)[1].lower() in VIDEO_EXTS
        try:
            if is_video:
                desc = _describe_video(p)
            else:
                desc = llm.chat(
                    [{"role": "user", "content": [
                        {"type": "text", "text": _DESCRIBE_PROMPT},
                        {"type": "image", "path": p},
                    ]}],
                    max_tokens=150, model_tier="fast",
                ).strip()
        except Exception as e:
            # Do NOT cache failures — use the filename as a weak signal for THIS
            # run only, so a later run with a working key retries the image.
            out[p] = os.path.basename(p)
            print(f"[Matcher] describe failed for {os.path.basename(p)} ({e})")
            continue
        store.set(key, desc)   # per-key write — safe under the thread pool
        out[p] = desc
        print(f"[Matcher] described {os.path.basename(p)}")

    return out


# ── Stage 2: one batched global assignment ────────────────────────────────────

def assign_images(frames: list[dict], descriptions: dict) -> dict:
    """
    frames: [{frame_id, caption}].  descriptions: {path: description}.
    Returns {frame_id: image_path} via a single GPT-4o reasoning call.
    """
    items = list(descriptions.items())
    if not items or not frames:
        return {}

    img_list = "\n".join(
        f"{i+1}. [{os.path.basename(p)}] {d}" for i, (p, d) in enumerate(items)
    )

    def _frline(f):
        # Richer signal than the bare caption: the storyboard's description of what the
        # shot DEPICTS (who/what is on screen) + its emotional tone. An abstract caption
        # like "But we never felt 'less'" matches far better with "depicts: a modest 1RK
        # room, mother and daughter" attached.
        bits = [(f.get("caption") or "").strip() or "(no caption)"]
        depicts = (f.get("depicts") or "").strip()
        if depicts:
            bits.append("depicts: " + depicts[:200])
        if f.get("emotion"):
            bits.append("mood: " + str(f["emotion"]))
        return f"{f['frame_id']}: " + " | ".join(bits)

    frm_list = "\n".join(_frline(f) for f in frames)
    prompt = (
        "You are a film editor placing real photos onto the beats of a story.\n\n"
        f"IMAGES (number, [filename], description):\n{img_list}\n\n"
        f"STORY FRAMES (id: caption | depicts | mood):\n{frm_list}\n\n"
        "For each frame choose the IMAGE NUMBER that best matches it. Priority order:\n"
        "  1. Named people/text visible in an image vs named in the frame (strongest).\n"
        "  2. The 'depicts' line — match who/what is literally on screen (a person vs an "
        "object vs a place; the SAME person across beats about that person).\n"
        "  3. Emotional tone and setting.\n"
        "Do NOT match on loose word overlap with the caption alone — a beat whose caption "
        "is abstract should still go to the image whose CONTENT fits the 'depicts' line. "
        "Items marked '(real video clip)' are REAL FOOTAGE — prefer them when they fit a "
        "beat about as well, since real footage beats an animated still. Avoid reusing an "
        "item unless there are fewer items than frames.\n"
        'Reply ONLY as JSON mapping each frame id to an item number, e.g. '
        '{"f01": 3, "f02": 7}.'
    )

    # Optional: prepend the lab's house-style matching examples.
    from agents import style_exemplars
    if style_exemplars.enabled():
        examples = style_exemplars.matching_examples()
        if examples:
            prompt = examples + "\n\n" + prompt

    text = llm.chat([{"role": "user", "content": prompt}],
                    json_mode=True, max_tokens=600, model_tier="reasoning")
    mapping = llm.json_loads_lenient(text)

    paths = [p for p, _ in items]
    out = {}
    for f in frames:
        n = mapping.get(f["frame_id"])
        try:
            idx = int(n) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(paths):
            out[f["frame_id"]] = paths[idx]
    return out


# ── Orchestrator ──────────────────────────────────────────────────────────────

def smart_match(frames: list[dict], assets_dir: str, is_source_media) -> bool:
    """
    Fill auto-match frames with content-matched images, in place.
    Returns True if smart matching ran and assigned at least one frame, else
    False (caller falls back to positional matching). Respects [photo:] pins,
    ai_* specs, and videos (those frames are left untouched).
    """
    # No provider key gate here: all calls go through the pluggable llm brain
    # (bedrock uses IAM, not OPENAI_API_KEY); failures fall back gracefully below.
    if not assets_dir:
        return False

    need = [f for f in frames
            if not f.get("visual_path") and not f.get("photo_spec")]
    if not need:
        return False

    pinned = {f["photo_spec"] for f in frames
              if f.get("photo_spec") and not f["photo_spec"].startswith("ai_")}
    try:
        candidates = [
            os.path.join(assets_dir, fn)
            for fn in sorted(os.listdir(assets_dir))
            if is_source_media(fn)
            and os.path.splitext(fn)[1].lower() in (IMAGE_EXTS | VIDEO_EXTS)
            and fn not in pinned
        ]
    except Exception:
        return False
    if not candidates:
        return False

    try:
        print(f"[Matcher] Smart-matching {len(need)} frames against "
              f"{len(candidates)} images via {llm._provider()}…")
        descriptions = describe_images(candidates)

        def _match_view(f):
            sc = f.get("scene") or {}
            return {
                "frame_id": f["frame_id"],
                "caption": f.get("caption", ""),
                # what the shot literally shows (storyboard) — the key matching signal
                "depicts": sc.get("scene_description") or sc.get("image_prompt") or "",
                "emotion": sc.get("emotion") or "",
            }

        mapping = assign_images([_match_view(f) for f in need], descriptions)
    except Exception as e:
        print(f"[Matcher] smart match failed ({e}) — positional fallback")
        return False

    assigned = 0
    for f in need:
        p = mapping.get(f["frame_id"])
        if p and os.path.exists(p):
            f["visual"]      = os.path.basename(p)
            f["visual_path"] = p
            f["photo_spec"]  = os.path.basename(p)   # expose to UI for review/override
            assigned += 1
            print(f"[Matcher] {f['frame_id']} → {os.path.basename(p)}")
    return assigned > 0
