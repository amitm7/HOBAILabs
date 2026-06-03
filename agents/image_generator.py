"""
AI image generation for the three-tier visual system.

generate_contextual_image: age/era-accurate portrait (ai_portrait frames)
  → Flux 2 Pro via fal.ai (best photorealism for Indian faces/skin texture)

generate_symbolic_image: objects/settings, no people (ai_symbolic frames)
  → gpt-image-2 via OpenAI (best instruction-following for complex object arrangements)

Requires env vars: FAL_API_KEY, OPENAI_API_KEY
"""
import base64
import os
import requests
from openai import OpenAI

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

    # Download the image
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


def generate_contextual_image(frame: dict, assets_dir: str) -> str:
    """
    Generate an age/era-accurate portrait for a story beat.
    Uses Flux 2 Pro (best photorealism for Indian faces).
    Falls back to gpt-image-2 if FAL_API_KEY is not set.
    """
    scene = frame.get("scene", {})
    prompt = scene.get("image_prompt", "")

    if not prompt:
        caption = frame.get("caption", "")
        note = frame.get("director_note", "")
        prompt = (
            f"Cinematic portrait of a young Assamese Indian woman at the right age: "
            f"{caption[:120]}. "
            f"{('Director note: ' + note + '. ') if note else ''}"
            "Early 2000s India, humble setting, warm natural light, photorealistic, "
            "9:16 vertical, no text, no watermarks, shallow depth of field, 85mm lens."
        )

    frame_id = frame["frame_id"]
    out_path = os.path.join(assets_dir, f"ai_portrait_{frame_id}.jpg")

    print(f"[ImageGen] Portrait ({frame_id}) [{scene.get('emotion', '')}] via Flux 2 Pro...")
    try:
        _flux_generate(prompt, out_path)
    except RuntimeError as e:
        print(f"[ImageGen] Flux 2 Pro unavailable ({e}) → falling back to gpt-image-2")
        _openai_generate(prompt, out_path)
    print(f"[ImageGen] Saved → {out_path}")
    return out_path


def generate_symbolic_image(frame: dict, assets_dir: str) -> str:
    """
    Generate a symbolic/metaphorical image — objects and settings only, no people.
    Uses gpt-image-2 (best instruction-following for complex object arrangements).
    """
    scene = frame.get("scene", {})
    prompt = scene.get("image_prompt", "")

    if not prompt:
        caption = frame.get("caption", "")
        note = frame.get("director_note", "")
        prompt = (
            f"No people, no faces. Cinematic symbolic still life — objects and textures evoking: "
            f"{caption[:120]}. "
            f"{('Director note: ' + note + '. ') if note else ''}"
            "Grounded in real India — Assam textures, humble objects, warm kerosene lamp or monsoon light, "
            "shallow depth of field, photorealistic, 9:16 vertical, no text, no watermarks."
        )

    frame_id = frame["frame_id"]
    out_path = os.path.join(assets_dir, f"ai_symbolic_{frame_id}.jpg")

    print(f"[ImageGen] Symbolic ({frame_id}) [{scene.get('emotion', '')}] via gpt-image-2...")
    _openai_generate(prompt, out_path)
    print(f"[ImageGen] Saved → {out_path}")
    return out_path
