"""
AI image generation for the three-tier visual system.

generate_contextual_image: age/era-accurate portrait (ai_portrait frames)
  → Flux 2 Pro via fal.ai (best photorealism for Indian faces/skin texture)

generate_symbolic_image: objects/settings, no people (ai_symbolic frames)
  → gpt-image-2 via OpenAI (best instruction-following for complex object arrangements)

Requires env vars: FAL_API_KEY, OPENAI_API_KEY
"""
import base64
import hashlib
import os
import requests
from openai import OpenAI

from agents import model_router

FAL_API_BASE = "https://fal.run/fal-ai/flux-2-pro"

_openai_client = None


def _get_openai():
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI()
    return _openai_client


def _flux_generate(prompt: str, out_path: str) -> str:
    """Call Flux 2 Pro via fal.ai REST API and save the result to out_path."""
    fal_key = os.environ.get("FAL_API_KEY", "")
    if not fal_key:
        raise RuntimeError("FAL_API_KEY not set — add it to .env")

    resp = requests.post(
        FAL_API_BASE,
        headers={
            "Authorization": f"Key {fal_key}",
            "Content-Type": "application/json",
        },
        json={
            "prompt": prompt,
            "image_size": "portrait_16_9",   # 9:16 vertical portrait
            "output_format": "jpeg",
            "safety_tolerance": "4",          # storytelling content, relax safety slightly
            "sync_mode": True,                # wait for result in single call (no polling)
        },
        timeout=120,
    )
    if not resp.ok:
        raise RuntimeError(f"Flux 2 Pro failed {resp.status_code}: {resp.text[:300]}")

    result = resp.json()
    img_url = result["images"][0]["url"]

    # In sync_mode, fal.ai returns the image inline as a base64 data URI
    # (data:image/jpeg;base64,...) instead of an http URL. Handle both.
    if img_url.startswith("data:"):
        header, b64data = img_url.split(",", 1)
        with open(out_path, "wb") as f:
            f.write(base64.b64decode(b64data))
    else:
        img_resp = requests.get(img_url, timeout=60)
        img_resp.raise_for_status()
        with open(out_path, "wb") as f:
            f.write(img_resp.content)
    return out_path


def _openai_generate(prompt: str, out_path: str) -> str:
    """Call gpt-image-2 and save the result to out_path."""
    client = _get_openai()
    resp = client.images.generate(
        model="gpt-image-2",
        prompt=prompt,
        size="1024x1536",
        quality="high",
        n=1,
    )
    with open(out_path, "wb") as f:
        f.write(base64.b64decode(resp.data[0].b64_json))
    return out_path


def _fal_image_generate(model_id: str, prompt: str, out_path: str) -> str:
    """Generate an image via any fal.ai-hosted model (Seedream, Nano Banana, …)."""
    from agents import fal_client
    endpoint = model_router.model_field(model_id, "fal_endpoint")
    if not endpoint:
        raise RuntimeError(f"no fal_endpoint configured for image model '{model_id}'")
    result = fal_client.run_sync(endpoint, {
        "prompt":        prompt,
        "image_size":    "portrait_16_9",   # 9:16 vertical
        "num_images":    1,
        "output_format": "jpeg",
        "sync_mode":     True,
    })
    url = fal_client.extract_media_url(result, keys=("images", "image"))
    if not url:
        raise RuntimeError(f"fal image model '{model_id}' returned no image: {str(result)[:200]}")
    return fal_client.download_media(url, out_path)


def _generate_with_model(model_id: str, prompt: str, out_path: str) -> str:
    """Dispatch image generation to the backend named in config/models.json."""
    backend = model_router.model_field(model_id, "backend")
    if backend == "flux":
        return _flux_generate(prompt, out_path)
    if backend == "openai":
        return _openai_generate(prompt, out_path)
    if backend == "fal":
        return _fal_image_generate(model_id, prompt, out_path)
    raise RuntimeError(f"unknown image backend '{backend}' for model '{model_id}'")


def _generate_image(model_id: str, prompt: str, out_path: str, fallback: str) -> str:
    """
    Try model_id; on failure fall back to a known-reliable model so a single
    flaky provider never breaks a render. `fallback` is a model id (e.g. flux,
    gpt_image) tried if the chosen model errors.
    """
    chosen = model_id or fallback
    try:
        return _generate_with_model(chosen, prompt, out_path)
    except Exception as e:
        if fallback and fallback != chosen:
            print(f"[ImageGen] {chosen} failed ({e}) → falling back to {fallback}")
            return _generate_with_model(fallback, prompt, out_path)
        raise


def _generate_image_checked(model_id: str, prompt: str, out_path: str,
                            fallback: str, frame_id: str,
                            max_retries: int = 2, generator=None) -> str:
    """
    Generate, then run Gate B (fast sanity check) and Gate B2 (vision-LLM
    critique: anachronisms, wrong age, deformed anatomy, baked-in text);
    regenerate up to max_retries times on failure. Living here makes quality
    gating a property of image generation itself — every entry point gets it.
    generator: optional zero-arg callable replacing the default model dispatch
    (used by reference-guided generation).
    """
    from agents.safety import check_face_sanity, critique_image
    gen = generator or (lambda: _generate_image(model_id, prompt, out_path, fallback))
    for attempt in range(max_retries + 1):
        gen()
        if check_face_sanity(out_path, frame_id) and critique_image(out_path, frame_id, prompt):
            return out_path
        if attempt < max_retries:
            print(f"[Safety] Gate B: {frame_id} — regenerating (attempt {attempt + 2}/{max_retries + 1})")
            try:
                os.remove(out_path)
            except OSError:
                pass
    print(f"[Safety] Gate B: {frame_id} — using last result after {max_retries + 1} attempts.")
    return out_path


_MIN_IMAGE_BYTES = 50_000  # files smaller than this are assumed corrupt/incomplete


def _image_cached(path: str) -> bool:
    """True if a valid generated image already exists at this path."""
    return os.path.exists(path) and os.path.getsize(path) >= _MIN_IMAGE_BYTES


def _prompt_hash(model_id: str, prompt: str) -> str:
    """Short hash of model + prompt for the cache filename — a changed mood,
    director note, or model produces a new file instead of serving a stale one."""
    return hashlib.md5(f"{model_id}|{prompt}".encode()).hexdigest()[:8]


def _file_hash(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()[:8]


def generate_contextual_image(frame: dict, assets_dir: str, model_id: str = "",
                              reference_path: str = "") -> str:
    """
    Generate an age/era-accurate portrait for a story beat.
    model_id selects the image model (router-chosen); defaults to Flux 2 Pro.
    Falls back to gpt-image-2 if the chosen model errors.
    reference_path: optional portrait of the SAME person — generation then goes
    through the image-edit API ("keep this exact face, re-imagine the scene"),
    so the character stays consistent across frames/ages instead of being
    re-invented from a text description each time.
    Skips generation if a valid image for this exact prompt already exists on disk.
    """
    frame_id = frame["frame_id"]
    scene  = frame.get("scene", {})
    prompt = scene.get("image_prompt", "")
    if not prompt:
        # Bare fallback only (scene design absent/failed). No fixed stock person:
        # describe whoever the beat — and the detected speaker — implies.
        caption = frame.get("caption", "")
        note    = frame.get("director_note", "")
        gender  = frame.get("speaker_gender", "")
        age     = frame.get("speaker_age_bracket", "")
        who = (f"{age} " if age and age != "adult" else "") + (gender or "person")
        prompt  = (
            f"Cinematic, era- and setting-accurate portrait of a {who} at the right age "
            f"for this moment: {caption[:120]}. "
            f"{('Director note: ' + note + '. ') if note else ''}"
            "Authentic, grounded, photorealistic, warm natural light, "
            "9:16 vertical, no text, no watermarks, shallow depth of field, 85mm lens."
        )

    use_ref = bool(reference_path) and os.path.exists(reference_path)
    chosen  = "gpt_image_ref" if use_ref else (model_id or "flux")
    hash_src = prompt + (f"|ref:{_file_hash(reference_path)}" if use_ref else "")
    out_path = os.path.join(
        assets_dir, f"ai_portrait_{frame_id}_{_prompt_hash(chosen, hash_src)}.jpg")

    if _image_cached(out_path):
        print(f"[ImageGen] Portrait ({frame_id}) — reusing cached image ({os.path.getsize(out_path)//1024}KB)")
        return out_path

    if use_ref:
        from agents.image_editor import edit_image
        ref_prompt = ("Keep this EXACT person's face and identity — same person, "
                      "photorealistic. Re-imagine the photograph as: " + prompt)
        print(f"[ImageGen] Portrait ({frame_id}) [{scene.get('emotion', '')}] via reference edit "
              f"(face from {os.path.basename(reference_path)})…")
        _generate_image_checked("", prompt, out_path, "", frame_id,
                                generator=lambda: edit_image(reference_path, ref_prompt, out_path))
    else:
        print(f"[ImageGen] Portrait ({frame_id}) [{scene.get('emotion', '')}] via {chosen}…")
        _generate_image_checked(chosen, prompt, out_path, "gpt_image", frame_id)
    print(f"[ImageGen] Saved → {out_path}")
    return out_path


def generate_symbolic_image(frame: dict, assets_dir: str, model_id: str = "") -> str:
    """
    Generate a symbolic/metaphorical image — objects and settings only, no people.
    model_id selects the image model (router-chosen); defaults to gpt-image-2.
    Skips generation if a valid image already exists on disk.
    """
    frame_id = frame["frame_id"]
    scene  = frame.get("scene", {})
    prompt = scene.get("image_prompt", "")
    if not prompt:
        caption = frame.get("caption", "")
        note    = frame.get("director_note", "")
        prompt  = (
            f"No people, no faces. Cinematic symbolic still life — objects and textures evoking: "
            f"{caption[:120]}. "
            f"{('Director note: ' + note + '. ') if note else ''}"
            "Grounded in real India — Assam textures, humble objects, warm kerosene lamp or monsoon light, "
            "shallow depth of field, photorealistic, 9:16 vertical, no text, no watermarks."
        )

    chosen = model_id or "gpt_image"
    out_path = os.path.join(
        assets_dir, f"ai_symbolic_{frame_id}_{_prompt_hash(chosen, prompt)}.jpg")

    if _image_cached(out_path):
        print(f"[ImageGen] Symbolic ({frame_id}) — reusing cached image ({os.path.getsize(out_path)//1024}KB)")
        return out_path

    print(f"[ImageGen] Symbolic ({frame_id}) [{scene.get('emotion', '')}] via {chosen}…")
    _generate_image_checked(chosen, prompt, out_path, "gpt_image", frame_id)
    print(f"[ImageGen] Saved → {out_path}")
    return out_path
