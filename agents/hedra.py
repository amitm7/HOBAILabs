"""
Hedra character-3 talking portrait provider.

Generates a talking-head video from a portrait image + audio.
Best for: still photo or AI-generated portrait → person appears to speak the narration.

Auth:   X-API-Key header (HEDRA_API_KEY env var)
Base:   https://api.hedra.com/web-app/public
Flow:   upload image asset → upload audio asset → create generation → poll → download
"""

import os
import time
import requests
from pathlib import Path

HEDRA_BASE  = "https://api.hedra.com/web-app/public"
POLL_SEC    = 5
TIMEOUT_SEC = 300


def _headers(content_type: str = "application/json") -> dict:
    key = os.environ.get("HEDRA_API_KEY", "")
    if not key:
        raise RuntimeError("HEDRA_API_KEY not set in .env — add it to enable talking portrait generation")
    return {"X-API-Key": key, "Content-Type": content_type}


def upload_asset(file_path: str, asset_type: str) -> str:
    """
    Upload an image or audio file to Hedra's storage.
    asset_type: "image" or "audio"
    Returns asset_id string.
    """
    name = Path(file_path).name

    # Step 1: get a presigned upload URL
    resp = requests.post(
        f"{HEDRA_BASE}/assets",
        headers=_headers(),
        json={"type": asset_type, "name": name},
        timeout=15,
    )
    if not resp.ok:
        raise RuntimeError(f"Hedra asset init failed {resp.status_code}: {resp.text[:300]}")
    data       = resp.json()
    asset_id   = data.get("id")
    upload_url = data.get("uploadUrl") or data.get("upload_url")
    if not asset_id or not upload_url:
        raise RuntimeError(f"Hedra: unexpected asset response: {data}")

    # Step 2: upload file bytes to the presigned URL
    import mimetypes
    mime = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
    with open(file_path, "rb") as f:
        put_resp = requests.put(upload_url, data=f,
                                headers={"Content-Type": mime}, timeout=120)
    put_resp.raise_for_status()
    print(f"[Hedra] Uploaded {asset_type} {name} → asset {asset_id}")
    return str(asset_id)


def submit(image_asset_id: str, audio_asset_id: str,
           aspect_ratio: str = "9:16") -> str:
    """
    Create a character-3 generation. Returns generation_id immediately (non-blocking).
    """
    payload = {
        "videoType":        "character-3",
        "startingKeyframe": {"assetId": image_asset_id},
        "audioAssetId":     audio_asset_id,
        "aspectRatio":      aspect_ratio,
    }
    resp = requests.post(f"{HEDRA_BASE}/generations",
                         headers=_headers(), json=payload, timeout=30)
    if not resp.ok:
        raise RuntimeError(f"Hedra submit failed {resp.status_code}: {resp.text[:300]}")
    data   = resp.json()
    gen_id = data.get("id") or data.get("jobId") or data.get("generation_id")
    if not gen_id:
        raise RuntimeError(f"Hedra: no generation_id in response: {data}")
    print(f"[Hedra] Submitted → generation {gen_id}")
    return str(gen_id)


def poll_and_download(gen_id: str, output_path: str) -> str:
    """
    Poll GET /generations/{id}/status until status==complete.
    Downloads download_url to output_path. Returns output_path.
    Raises RuntimeError on error. Raises TimeoutError after TIMEOUT_SEC.
    """
    elapsed = 0
    while elapsed < TIMEOUT_SEC:
        time.sleep(POLL_SEC)
        elapsed += POLL_SEC

        resp = requests.get(f"{HEDRA_BASE}/generations/{gen_id}/status",
                            headers=_headers(), timeout=15)
        resp.raise_for_status()
        data   = resp.json()
        status = (data.get("status") or "").lower()

        if status == "complete":
            dl_url = data.get("download_url") or data.get("url") or data.get("videoUrl")
            if not dl_url:
                raise RuntimeError(f"Hedra complete but no download_url: {data}")
            dl = requests.get(dl_url, timeout=120)
            dl.raise_for_status()
            with open(output_path, "wb") as f:
                f.write(dl.content)
            print(f"[Hedra] ✓ {gen_id[:8]}… → {output_path}")
            return output_path

        elif status in ("error", "failed", "cancelled"):
            raise RuntimeError(f"Hedra generation {gen_id} {status}: {data}")

        print(f"[Hedra] {gen_id[:8]}… {status} ({elapsed}s)…")

    raise TimeoutError(f"Hedra generation {gen_id} timed out after {TIMEOUT_SEC}s")
