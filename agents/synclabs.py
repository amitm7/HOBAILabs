"""
SyncLabs v2 lip sync provider.

Syncs mouth movements in an existing video to a new audio track.
Best for: real talking-head video of the subject → replace voice with polished ElevenLabs narration.

Auth:   x-api-key header (SYNCLABS_API_KEY env var)
Submit: POST https://api.sync.so/v2/generate
Poll:   GET  https://api.sync.so/v2/generate/{id}
"""

import os
import time
import requests
from pathlib import Path

SYNCLABS_BASE = "https://api.sync.so"
POLL_SEC      = 5
TIMEOUT_SEC   = 300


def _headers() -> dict:
    key = os.environ.get("SYNCLABS_API_KEY", "")
    if not key:
        raise RuntimeError("SYNCLABS_API_KEY not set in .env — add it to enable video lip sync")
    return {"x-api-key": key, "Content-Type": "application/json"}


def submit(video_url: str, audio_url: str) -> str:
    """
    Submit a lip sync job. Returns job_id immediately (non-blocking).
    video_url and audio_url must be publicly accessible.
    """
    payload = {
        "model": "lipsync-2",
        "input": [
            {"type": "video", "url": video_url},
            {"type": "audio", "url": audio_url},
        ],
    }
    resp = requests.post(f"{SYNCLABS_BASE}/v2/generate",
                         headers=_headers(), json=payload, timeout=30)
    if not resp.ok:
        raise RuntimeError(f"SyncLabs submit failed {resp.status_code}: {resp.text[:300]}")
    data   = resp.json()
    job_id = data.get("id") or data.get("jobId")
    if not job_id:
        raise RuntimeError(f"SyncLabs: no job id in response: {data}")
    print(f"[SyncLabs] Submitted → job {job_id}")
    return str(job_id)


def poll_and_download(job_id: str, output_path: str) -> str:
    """
    Poll until COMPLETED, download the synced video to output_path.
    Raises RuntimeError on FAILED/REJECTED. Raises TimeoutError after TIMEOUT_SEC.
    """
    elapsed = 0
    while elapsed < TIMEOUT_SEC:
        time.sleep(POLL_SEC)
        elapsed += POLL_SEC

        resp = requests.get(f"{SYNCLABS_BASE}/v2/generate/{job_id}",
                            headers=_headers(), timeout=15)
        resp.raise_for_status()
        data   = resp.json()
        status = data.get("status", "").upper()

        if status == "COMPLETED":
            video_url = data.get("outputUrl") or data.get("output_url")
            if not video_url:
                raise RuntimeError(f"SyncLabs completed but no outputUrl: {data}")
            dl = requests.get(video_url, timeout=120)
            dl.raise_for_status()
            with open(output_path, "wb") as f:
                f.write(dl.content)
            print(f"[SyncLabs] ✓ {job_id[:8]}… → {output_path}")
            return output_path

        elif status in ("FAILED", "REJECTED", "ERROR"):
            raise RuntimeError(f"SyncLabs job {job_id} {status}: {data.get('error', data)}")

        print(f"[SyncLabs] {job_id[:8]}… {status} ({elapsed}s)…")

    raise TimeoutError(f"SyncLabs job {job_id} timed out after {TIMEOUT_SEC}s")
