"""
Generative upscaler — Reality–Fidelity ladder, final-render quality lift.

Two fal.ai-hosted models, routed by INTENT so the moat stays safe:
  · faithful  → aura_sr        — a super-resolution model that does NOT invent detail,
                                  so a REAL person's face stays the real face. Use for
                                  real footage / real-photo passthrough.
  · creative  → clarity_upscaler — adds/hallucinates detail. Great for AI stills and
                                  ambient B-roll; NEVER point it at a real named person
                                  (it would alter their face — an authenticity violation).

The route picks the mode from the shot's asset kind (real → faithful, AI → creative).
All fal calls go through agents/fal_client (same plumbing as the image/video models) and
the model FACTS live in config/models.json (model_router.model_field → fal_endpoint).

Best-effort: returns the ORIGINAL path on any failure / unsupported type, so a caller can
use the result unconditionally (build-feature rule #4 — graceful degradation).
"""
from __future__ import annotations

import os

from agents import model_router

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

# Model ids in config/models.json (kind="upscale").
FAITHFUL_MODEL = "aura_sr"          # real footage — no hallucination
CREATIVE_MODEL = "clarity_upscaler"  # AI stills — adds detail


def _args_for(model_id: str, image_uri: str) -> dict:
    """Per-endpoint argument shape (the two upscalers take different inputs)."""
    if model_id == CREATIVE_MODEL:
        return {
            "image_url":      image_uri,
            "upscale_factor": 2,
            "creativity":     0.3,     # modest — sharpen/detail, not a re-imagining
            "resemblance":    1.0,     # stay as close to the source as the model allows (max)
            "sync_mode":      True,
        }
    # aura_sr — pure super-resolution, faithful
    return {"image_url": image_uri, "upscaling_factor": 4, "sync_mode": True}


def upscale_file(in_path: str, out_dir: str, *, creative: bool = False,
                 model_id: str | None = None) -> str:
    """Upscale one still. creative=True → clarity (AI shots); else aura_sr (real, faithful).
    Returns the upscaled path, or the ORIGINAL on any failure / non-image input."""
    if not in_path or not os.path.isfile(in_path):
        return in_path
    if os.path.splitext(in_path)[1].lower() not in IMAGE_EXTS:
        return in_path                      # videos / unknown types: skip (no-op)
    mid = model_id or (CREATIVE_MODEL if creative else FAITHFUL_MODEL)
    endpoint = model_router.model_field(mid, "fal_endpoint")
    if not endpoint:
        print(f"[Upscale] no fal_endpoint for '{mid}' — skipping")
        return in_path
    base = os.path.splitext(os.path.basename(in_path))[0]
    out_path = os.path.join(out_dir, f"{base}_up.jpg")
    try:
        from agents import fal_client
        os.makedirs(out_dir, exist_ok=True)
        uri = fal_client.file_to_data_uri(in_path)
        result = fal_client.run_sync(endpoint, _args_for(mid, uri), timeout=180)
        url = fal_client.extract_media_url(result, keys=("image", "images"))
        if not url:
            raise RuntimeError(f"no image in result: {str(result)[:200]}")
        return fal_client.download_media(url, out_path)
    except Exception as e:
        print(f"[Upscale] {os.path.basename(in_path)} skipped ({e}) — using original")
        return in_path
