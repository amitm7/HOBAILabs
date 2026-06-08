"""
Apply natural-language edits to existing images using gpt-image-1.

V1 — prompt-only (no mask). Works well for global changes:
  "add thunderstorm", "more trees on the left", "make the sky bluer"

V2 (future) — mask-based inpainting for precise spatial edits.
"""
import base64
import os
from openai import OpenAI

_client = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


def edit_image(image_path: str, edit_prompt: str, out_path: str) -> str:
    """
    Edit an image with a natural-language prompt.
    Returns out_path. Raises RuntimeError on API failure.
    """
    print(f"[ImageEditor] '{edit_prompt[:70]}' → {os.path.basename(out_path)}")

    with open(image_path, "rb") as img_file:
        resp = _get_client().images.edit(
            model="gpt-image-1",
            image=img_file,
            prompt=edit_prompt,
            size="1024x1536",
        )

    raw = resp.data[0]
    if not hasattr(raw, "b64_json") or not raw.b64_json:
        raise RuntimeError("gpt-image-1 edit returned no image data")

    with open(out_path, "wb") as f:
        f.write(base64.b64decode(raw.b64_json))

    print(f"[ImageEditor] ✓ Saved → {out_path}")
    return out_path
