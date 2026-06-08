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

import base64
import hashlib
import json
import mimetypes
import os
from pathlib import Path

CACHE_PATH   = Path.home() / ".hob_cache" / "image_descriptions.json"
IMAGE_EXTS   = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}   # OpenAI-vision safe
VISION_MODEL = "gpt-4o"


def _client():
    from openai import OpenAI
    return OpenAI()


def _img_hash(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def _data_uri(path: str) -> str:
    mime = mimetypes.guess_type(path)[0] or "image/jpeg"
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _load_cache() -> dict:
    try:
        return json.loads(CACHE_PATH.read_text())
    except Exception:
        return {}


def _save_cache(cache: dict):
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2))


# ── Stage 1: describe each image once (cached) ────────────────────────────────

_DESCRIBE_PROMPT = (
    "Describe this photo in 1-2 sentences for a video editor choosing shots. "
    "Include: who or what is shown, ANY text, names, or captions visible in the "
    "image (quote them), the setting, and the mood/emotion. Be concrete."
)


def describe_images(paths: list[str]) -> dict:
    """Return {path: description}. Caches by image content hash."""
    cache = _load_cache()
    out, changed, client = {}, False, None

    for p in paths:
        try:
            key = _img_hash(p)
        except Exception:
            continue
        if key in cache:
            out[p] = cache[key]
            continue
        if client is None:
            client = _client()
        try:
            resp = client.chat.completions.create(
                model=VISION_MODEL,
                max_tokens=150,
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": _DESCRIBE_PROMPT},
                    {"type": "image_url", "image_url": {"url": _data_uri(p)}},
                ]}],
            )
            desc = resp.choices[0].message.content.strip()
        except Exception as e:
            # Do NOT cache failures — use the filename as a weak signal for THIS
            # run only, so a later run with a working key retries the image.
            out[p] = os.path.basename(p)
            print(f"[Matcher] describe failed for {os.path.basename(p)} ({e})")
            continue
        cache[key] = desc
        out[p] = desc
        changed = True
        print(f"[Matcher] described {os.path.basename(p)}")

    if changed:
        _save_cache(cache)
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
    frm_list = "\n".join(f"{f['frame_id']}: {f.get('caption', '') or '(no caption)'}"
                         for f in frames)
    prompt = (
        "You are a film editor placing real photos onto the beats of a story.\n\n"
        f"IMAGES (number, [filename], description):\n{img_list}\n\n"
        f"STORY FRAMES (id: caption):\n{frm_list}\n\n"
        "For each frame, choose the IMAGE NUMBER that best matches its meaning — "
        "use names/text visible in an image as a strong signal, then emotional "
        "tone and setting. Avoid reusing an image unless there are fewer images "
        "than frames.\n"
        'Reply ONLY as JSON mapping each frame id to an image number, e.g. '
        '{"f01": 3, "f02": 7}.'
    )

    resp = _client().chat.completions.create(
        model=VISION_MODEL,
        max_tokens=600,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt}],
    )
    mapping = json.loads(resp.choices[0].message.content)

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
    if not os.environ.get("OPENAI_API_KEY") or not assets_dir:
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
            and os.path.splitext(fn)[1].lower() in IMAGE_EXTS
            and fn not in pinned
        ]
    except Exception:
        return False
    if not candidates:
        return False

    try:
        print(f"[Matcher] Smart-matching {len(need)} frames against "
              f"{len(candidates)} images via {VISION_MODEL}…")
        descriptions = describe_images(candidates)
        mapping = assign_images(
            [{"frame_id": f["frame_id"], "caption": f.get("caption", "")} for f in need],
            descriptions,
        )
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
