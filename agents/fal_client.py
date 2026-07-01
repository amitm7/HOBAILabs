"""
Generic fal.ai REST caller.

fal.ai is an aggregator that hosts many image and video models behind one REST
pattern. This module generalizes the call already proven in
image_generator._flux_generate so BOTH image models (Seedream, Nano Banana) and
video models (Seedance, Veo, Hailuo, Kling-via-fal) can reuse it.

Two ways to call:
  run_sync(endpoint, args)         — POST fal.run/<endpoint>, block for result.
                                     Good for images (sync_mode returns inline).
  submit/poll_result(endpoint,...) — fal queue API for slow video generations.

Helpers:
  file_to_data_uri(path)           — inline a local image as image_url input.
  extract_media_url(result, keys)  — pull the output URL from varied shapes.
  download_media(url, out_path)    — save an http URL or data: URI to disk.

Requires env var FAL_API_KEY.
"""

import base64
import mimetypes
import os
import time

import requests

FAL_RUN_BASE   = "https://fal.run"
FAL_QUEUE_BASE = "https://queue.fal.run"


def _key() -> str:
    k = os.environ.get("FAL_API_KEY", "")
    if not k:
        raise RuntimeError("FAL_API_KEY not set — add it to .env")
    return k


def _headers() -> dict:
    return {"Authorization": f"Key {_key()}", "Content-Type": "application/json"}


# ── Inputs ──────────────────────────────────────────────────────────────────

def file_to_data_uri(path: str) -> str:
    """Encode a local image file as a data: URI usable as a fal image_url input."""
    mime = mimetypes.guess_type(path)[0] or "image/jpeg"
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{b64}"


# ── Synchronous call (images) ───────────────────────────────────────────────

def run_sync(endpoint: str, arguments: dict, timeout: int = 180) -> dict:
    """POST to fal.run/<endpoint> and return the parsed JSON result. Wrapped in a
    per-endpoint circuit breaker: after repeated failures/timeouts on an endpoint, it
    fails fast (CircuitOpen) so the caller's fallback runs instead of hanging for the full
    timeout each time — one down model doesn't stall the others."""
    from agents import circuit

    def _do():
        resp = requests.post(
            f"{FAL_RUN_BASE}/{endpoint}",
            headers=_headers(),
            json=arguments,
            timeout=timeout,
        )
        if not resp.ok:
            raise RuntimeError(f"fal {endpoint} failed {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    return circuit.call(f"fal:{endpoint}", _do)


# ── Queue call (slow video) ─────────────────────────────────────────────────

def submit(endpoint: str, arguments: dict) -> dict:
    """
    Submit a job to the fal queue. Returns a handle dict with request_id and the
    status/response URLs fal hands back (used by poll_result).
    """
    from agents import circuit

    def _do():
        resp = requests.post(
            f"{FAL_QUEUE_BASE}/{endpoint}",
            headers=_headers(),
            json=arguments,
            timeout=60,
        )
        if not resp.ok:
            raise RuntimeError(f"fal submit {endpoint} failed {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    body = circuit.call(f"fal:{endpoint}", _do)
    return {
        "endpoint":     endpoint,
        "request_id":   body.get("request_id"),
        "status_url":   body.get("status_url"),
        "response_url": body.get("response_url"),
    }


def poll_result(handle: dict, timeout: int = 600, poll_sec: int = 5) -> dict:
    """Poll a queued job until COMPLETED, then fetch and return the result JSON."""
    status_url   = handle.get("status_url")
    response_url = handle.get("response_url")
    if not status_url or not response_url:
        raise RuntimeError(f"fal handle missing status/response URL: {handle}")

    auth = {"Authorization": f"Key {_key()}"}
    elapsed = 0
    while elapsed < timeout:
        time.sleep(poll_sec)
        elapsed += poll_sec
        s = requests.get(status_url, headers=auth, timeout=30)
        if not s.ok:
            continue
        status = (s.json().get("status") or "").upper()
        if status == "COMPLETED":
            r = requests.get(response_url, headers=auth, timeout=60)
            r.raise_for_status()
            return r.json()
        if status in ("FAILED", "ERROR", "CANCELLED"):
            raise RuntimeError(f"fal job {handle.get('request_id')} {status}: {s.text[:300]}")
    raise TimeoutError(f"fal job {handle.get('request_id')} timed out after {timeout}s")


# ── Outputs ─────────────────────────────────────────────────────────────────

def extract_media_url(result, keys=("video", "image", "images")) -> str | None:
    """Pull an output media URL from the many shapes fal models return."""
    if isinstance(result, str):
        return result if result.startswith(("http", "data:")) else None
    if not isinstance(result, dict):
        return None
    for k in keys:
        v = result.get(k)
        if isinstance(v, dict) and v.get("url"):
            return v["url"]
        if isinstance(v, list) and v and isinstance(v[0], dict) and v[0].get("url"):
            return v[0]["url"]
        if isinstance(v, str) and v.startswith(("http", "data:")):
            return v
    # last resort: any nested {"url": ...}
    for v in result.values():
        if isinstance(v, dict) and v.get("url"):
            return v["url"]
    return None


def download_media(url: str, out_path: str) -> str:
    """Save an http(s) URL or a data: URI to out_path."""
    if url.startswith("data:"):
        _, b64 = url.split(",", 1)
        with open(out_path, "wb") as f:
            f.write(base64.b64decode(b64))
    else:
        r = requests.get(url, timeout=120)
        r.raise_for_status()
        with open(out_path, "wb") as f:
            f.write(r.content)
    return out_path
