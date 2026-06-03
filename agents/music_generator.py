"""
Suno AI music generator — creates situation-specific instrumental background music.

Flow: POST /generate → taskId → poll /record-info until SUCCESS → download audioUrl.
Status progression: PENDING → TEXT_SUCCESS → SUCCESS (audio ready) | FAILED
"""
import os
import time
import requests
from pathlib import Path

SUNO_BASE = "https://api.sunoapi.org"
SUNO_POLL_SEC = 10
SUNO_TIMEOUT = 360  # 6 minutes


def _headers() -> dict:
    key = os.environ.get("SUNO_API_KEY", "")
    if not key:
        raise RuntimeError("SUNO_API_KEY not set in environment")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _extract_audio_url(data: dict) -> str | None:
    """Pull the audio URL from a poll response."""
    suno_list = data.get("data", {}).get("response", {}).get("sunoData", [])
    if suno_list:
        clip = suno_list[0]
        return clip.get("audioUrl") or clip.get("streamAudioUrl") or None
    return None


def generate_music(prompt: str, output_path: str,
                   model: str = "V5_5", instrumental: bool = True) -> str:
    """
    Generate music from a text prompt and download to output_path.
    Returns output_path on success.
    """
    print(f"[MusicGen] Generating: {prompt[:80]}...")

    resp = requests.post(
        f"{SUNO_BASE}/api/v1/generate",
        headers=_headers(),
        json={
            "prompt": prompt,
            "model": model,
            "instrumental": instrumental,
            "customMode": False,
            "callBackUrl": "https://example.com/noop",
        },
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"Suno generate failed {resp.status_code}: {resp.text[:300]}")

    body = resp.json()
    if body.get("code") != 200:
        raise RuntimeError(f"Suno error: {body.get('msg')} — {body}")

    task_id = body.get("data", {}).get("taskId")
    if not task_id:
        raise RuntimeError(f"No taskId in response: {body}")
    print(f"[MusicGen] Task ID: {task_id}")

    # Poll until audio is ready
    elapsed = 0
    while elapsed < SUNO_TIMEOUT:
        time.sleep(SUNO_POLL_SEC)
        elapsed += SUNO_POLL_SEC

        poll = requests.get(
            f"{SUNO_BASE}/api/v1/generate/record-info",
            headers=_headers(),
            params={"taskId": task_id},
            timeout=15,
        )
        poll.raise_for_status()
        data = poll.json()
        status = str(data.get("data", {}).get("status", "")).upper()
        print(f"[MusicGen] Status: {status} ({elapsed}s)...")

        if status == "SUCCESS":
            audio_url = _extract_audio_url(data)
            if not audio_url:
                raise RuntimeError(f"SUCCESS but no audio URL in: {data}")
            print(f"[MusicGen] Downloading audio...")
            audio_resp = requests.get(audio_url, timeout=120)
            audio_resp.raise_for_status()
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(audio_resp.content)
            print(f"[MusicGen] Saved → {output_path} ({len(audio_resp.content)//1024}KB)")
            return output_path

        elif status == "FAILED":
            raise RuntimeError(f"Suno task failed: {data}")
        # TEXT_SUCCESS = text metadata ready, audio still rendering — keep polling

    raise TimeoutError(f"Suno task {task_id} timed out after {SUNO_TIMEOUT}s")


def generate_story_music(story_emotion: str, output_path: str,
                         model: str = "V5_5") -> str:
    """
    Generate background music matched to the emotional arc of an Indian story.
    story_emotion: e.g. 'bittersweet struggle to triumph, Assamese roots'
    """
    prompt = (
        f"Emotional Bollywood instrumental background score. {story_emotion}. "
        "Sitar, tabla, soft piano, strings. Starts gentle and melancholic, "
        "builds to hopeful and triumphant. Indian classical undertones. "
        "No lyrics, pure instrumental. Cinematic, 2-4 minutes."
    )
    return generate_music(prompt, output_path, model=model)
