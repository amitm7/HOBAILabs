"""
Higgsfield AI image-to-video provider (DoP model).

Per the official docs (docs.higgsfield.ai/docs/guides/video) the DoP model is
invoked through the SDK application id "higgsfield-ai/dop/standard" with a SIMPLE
payload: an image_url + a natural-language motion prompt (+ optional duration).
Camera moves are described in the prompt text — there is NO motion-preset-ID system.

Flow:
  upload image (higgsfield_client.upload_file → CloudFront URL)
  → submit "higgsfield-ai/dop/standard" {image_url, prompt, duration}
  → poll request_id via higgsfield_client.status() / .result()
  → download the output video

Generation is slow (≈1.5–5 min/clip), hence the long timeout.
"""

import os
import time
import requests
from pathlib import Path

HIGGSFIELD_BASE = "https://platform.higgsfield.ai"
POLL_SEC        = 5
TIMEOUT_SEC     = 600   # DoP generation can take 5+ minutes; allow up to 10


# ── Auth ──────────────────────────────────────────────────────────────────────

def _auth_header() -> str:
    kid = os.environ.get("HF_API_KEY") or os.environ.get("HIGGSFIELD_KEY_ID", "")
    sec = os.environ.get("HF_API_SECRET") or os.environ.get("HIGGSFIELD_KEY_SECRET", "")
    if not kid or not sec:
        raise RuntimeError(
            "Higgsfield credentials not set. "
            "Add HF_API_KEY and HF_API_SECRET to .env"
        )
    return f"Key {kid}:{sec}"


def _headers() -> dict:
    return {"Authorization": _auth_header(), "Content-Type": "application/json"}


# ── Image upload ──────────────────────────────────────────────────────────────

def _upload_image(image_path: str) -> str:
    """Upload via Higgsfield SDK → returns CloudFront public URL."""
    # Bridge our env var names to what the SDK expects
    if not os.environ.get("HF_API_KEY"):
        os.environ["HF_API_KEY"]    = os.environ.get("HIGGSFIELD_KEY_ID", "")
    if not os.environ.get("HF_API_SECRET"):
        os.environ["HF_API_SECRET"] = os.environ.get("HIGGSFIELD_KEY_SECRET", "")

    try:
        import higgsfield_client
        url = higgsfield_client.upload_file(image_path)
        print(f"[Higgsfield] Hosted {Path(image_path).name} → {str(url)[:60]}…")
        return str(url)
    except ImportError:
        raise RuntimeError("higgsfield-client not installed. Run: pip install higgsfield-client")
    except Exception as e:
        raise RuntimeError(f"Higgsfield image upload failed: {e}")


# ── Submit ────────────────────────────────────────────────────────────────────

# Per the official docs (docs.higgsfield.ai/docs/guides/video), the DoP
# image-to-video model is invoked through the SDK application id below with a
# SIMPLE payload: an image_url + a natural-language motion prompt (+ optional
# duration). Camera moves are expressed in the prompt text, NOT via preset IDs.
HF_DOP_APP = "higgsfield-ai/dop/standard"


def submit(image_path: str, motion_prompt: str = "",
           aspect_ratio: str = "9:16", duration: int = 5) -> str:
    """
    Upload image and submit a DoP image-to-video job via the official SDK.
    Returns request_id — non-blocking.
    """
    if not os.environ.get("HF_API_KEY"):
        os.environ["HF_API_KEY"]    = os.environ.get("HIGGSFIELD_KEY_ID", "")
    if not os.environ.get("HF_API_SECRET"):
        os.environ["HF_API_SECRET"] = os.environ.get("HIGGSFIELD_KEY_SECRET", "")

    import higgsfield_client as hc
    image_url = _upload_image(image_path)

    prompt = motion_prompt or "Cinematic slow motion, natural ambient movement, emotional depth"
    arguments = {
        "image_url": image_url,
        "prompt":    prompt,
        "duration":  int(duration),
    }

    controller = hc.submit(HF_DOP_APP, arguments)
    request_id = getattr(controller, "request_id", None) or str(controller)
    print(f"[Higgsfield] Submitted → {request_id} (prompt-driven motion)")
    return str(request_id)


# ── Poll & download ───────────────────────────────────────────────────────────

def poll_and_download(request_id: str, output_path: str) -> str:
    """
    Poll until COMPLETED, download video to output_path.
    Uses higgsfield_client.status() / higgsfield_client.result().
    """
    if not os.environ.get("HF_API_KEY"):
        os.environ["HF_API_KEY"]    = os.environ.get("HIGGSFIELD_KEY_ID", "")
    if not os.environ.get("HF_API_SECRET"):
        os.environ["HF_API_SECRET"] = os.environ.get("HIGGSFIELD_KEY_SECRET", "")

    import higgsfield_client

    elapsed = 0
    while elapsed < TIMEOUT_SEC:
        time.sleep(POLL_SEC)
        elapsed += POLL_SEC

        try:
            status_obj  = higgsfield_client.status(request_id=request_id)
            status_name = type(status_obj).__name__.upper()
        except Exception as e:
            print(f"[Higgsfield] Status check error ({e}) — retrying…")
            continue

        if status_name == "COMPLETED":
            result    = higgsfield_client.result(request_id=request_id)
            video_url = _extract_video_url(result)

            if not video_url:
                raise RuntimeError(
                    f"Higgsfield completed but no video URL. Result: {result}"
                )

            dl = requests.get(str(video_url), timeout=120)
            dl.raise_for_status()
            with open(output_path, "wb") as f:
                f.write(dl.content)
            print(f"[Higgsfield] ✓ {request_id[:8]}… → {output_path}")
            return output_path

        elif status_name in ("FAILED", "ERROR", "CANCELLED", "NSFW"):
            raise RuntimeError(
                f"Higgsfield request {request_id} {status_name}: {status_obj}"
            )

        print(f"[Higgsfield] {request_id[:8]}… {status_name} ({elapsed}s)…")

    raise TimeoutError(
        f"Higgsfield request {request_id} timed out after {TIMEOUT_SEC}s"
    )


def _extract_video_url(result) -> str | None:
    """Extract video URL from various result shapes the API may return."""
    if isinstance(result, str):
        return result if result.startswith("http") else None
    if isinstance(result, dict):
        return (
            result.get("video_url")
            or result.get("url")
            or result.get("media_url")
            or (result.get("video") or {}).get("url")
            or (result.get("output") or {}).get("url")
        )
    for attr in ("video_url", "url", "media_url"):
        val = getattr(result, attr, None)
        if val:
            return str(val)
    return None
