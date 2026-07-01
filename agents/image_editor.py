"""
Reference-conditioned image generation / edit — the IDENTITY path, now PLUGGABLE.

Given a reference (a face/character) + a prompt, generate a new image that KEEPS that
person/character in a new scene — the core of character consistency (real face-refs AND
fiction characters). The model is chosen via config/models.json `routing.identity`
(default: Nano Banana on fal → gpt-image-1 fallback). Override the primary with env
IDENTITY_MODEL. All fal calls go through fal_client (per-endpoint circuit breaker), and the
failover means a down/weak model degrades to the next automatically.

Back-compatible: edit_image(image_path, edit_prompt, out_path) — same signature callers use.
"""
import base64
import os

from agents import model_router

_client = None


def _get_openai():
    global _client
    if _client is None:
        from openai import OpenAI
        _client = OpenAI()
    return _client


def _identity_order() -> list[str]:
    """Ordered identity/edit models: env IDENTITY_MODEL first (if set), then the config
    routing.identity default chain, then gpt_image_edit as a last resort."""
    ids = list(model_router.catalog().get("routing", {})
               .get("identity", {}).get("default", []) or [])
    env = os.environ.get("IDENTITY_MODEL", "").strip()
    if env:
        ids = [env] + [m for m in ids if m != env]
    if "gpt_image_edit" not in ids:
        ids.append("gpt_image_edit")
    seen: set = set()
    return [m for m in ids if m and not (m in seen or seen.add(m))]


def edit_image(image_path: str, edit_prompt: str, out_path: str, model_id: str = "") -> str:
    """Generate a new image conditioned on `image_path` (the reference person/character)
    via the configured identity model, with automatic failover. Returns out_path."""
    order = ([model_id] + _identity_order()) if model_id else _identity_order()
    seen: set = set()
    order = [m for m in order if m and not (m in seen or seen.add(m))]
    last_err = None
    for i, m in enumerate(order):
        backend = model_router.model_field(m, "backend")
        try:
            if backend == "fal":
                return _fal_edit(m, image_path, edit_prompt, out_path)
            if backend == "openai":
                return _openai_edit(image_path, edit_prompt, out_path)
            raise RuntimeError(f"unknown edit backend {backend!r} for {m}")
        except Exception as e:
            last_err = e
            nxt = " — trying fallback" if i + 1 < len(order) else ""
            print(f"[ImageEditor] identity model '{m}' failed ({e}){nxt}")
    raise RuntimeError(f"all identity/edit models failed: {last_err}")


def _fal_edit(model_id: str, image_path: str, edit_prompt: str, out_path: str) -> str:
    """Reference-conditioned generation via a fal edit model (Nano Banana / Flux Kontext)."""
    from agents import fal_client
    endpoint = model_router.model_field(model_id, "fal_endpoint")
    if not endpoint:
        raise RuntimeError(f"no fal_endpoint for edit model {model_id!r}")
    ref_key = model_router.model_field(model_id, "ref_input") or "image_urls"
    uri = fal_client.file_to_data_uri(image_path)
    args = {"prompt": edit_prompt, "num_images": 1, "output_format": "jpeg", "sync_mode": True}
    args[ref_key] = [uri] if ref_key == "image_urls" else uri
    print(f"[ImageEditor] {model_id} ({endpoint}): '{edit_prompt[:60]}'…")
    result = fal_client.run_sync(endpoint, args, timeout=180)
    url = fal_client.extract_media_url(result, keys=("images", "image"))
    if not url:
        raise RuntimeError(f"{model_id} returned no image: {str(result)[:200]}")
    saved = fal_client.download_media(url, out_path)
    print(f"[ImageEditor] ✓ Saved → {saved}")
    return saved


def _openai_edit(image_path: str, edit_prompt: str, out_path: str) -> str:
    """OpenAI gpt-image-1 edit (the original path, now the fallback)."""
    print(f"[ImageEditor] gpt-image-1 edit: '{edit_prompt[:60]}' → {os.path.basename(out_path)}")
    with open(image_path, "rb") as img_file:
        resp = _get_openai().images.edit(
            model="gpt-image-1", image=img_file, prompt=edit_prompt, size="1024x1536")
    raw = resp.data[0]
    if not getattr(raw, "b64_json", None):
        raise RuntimeError("gpt-image-1 edit returned no image data")
    with open(out_path, "wb") as f:
        f.write(base64.b64decode(raw.b64_json))
    print(f"[ImageEditor] ✓ Saved → {out_path}")
    return out_path
