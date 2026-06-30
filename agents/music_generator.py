"""
Music generator — pluggable engine behind ONE seam (config/music.json).

  · lyria  → Google Lyria 3 (Gemini API, POST v1beta/interactions). Vocal songs AND
             instrumental score; cleaner commercial licensing + SynthID watermark.
  · suno   → Suno wrapper API (the original). Vocal-song alternative / fallback.

generate_music() dispatches to the configured provider and falls back to the other on
failure (build-feature rule #4). Beds under narration stay INSTRUMENTAL (the prompt is
told 'no vocals'); a future lyrics control unlocks full vocal songs. All providers go
through plain `requests` — no vendor SDK (same pattern as fal_client). The pipeline that
consumes the result (beat-track, ducking, assembly) only sees an audio file, so swapping
the engine is transparent.
"""
import base64
import json
import os
import time
from pathlib import Path

import requests

# ── Provider seam ────────────────────────────────────────────────────────────
_MUSIC_CFG_PATH = Path(__file__).parent.parent / "config" / "music.json"
_MUSIC_CFG = None


def _music_cfg() -> dict:
    global _MUSIC_CFG
    if _MUSIC_CFG is None:
        try:
            with open(_MUSIC_CFG_PATH) as f:
                _MUSIC_CFG = json.load(f)
        except Exception:
            _MUSIC_CFG = {"provider": "suno", "fallback": None}
    return _MUSIC_CFG


def generate_music(prompt: str, output_path: str, *, instrumental: bool = True) -> str:
    """Generate music from a text prompt → download to output_path. Routes to the
    configured provider (env MUSIC_PROVIDER overrides config/music.json), with graceful
    fallback to the other engine on failure. Returns output_path."""
    cfg = _music_cfg()
    provider = (os.environ.get("MUSIC_PROVIDER") or cfg.get("provider") or "suno").lower()
    fallback = (cfg.get("fallback") or "").lower()
    order = [provider] + ([fallback] if fallback and fallback != provider else [])
    last_err: Exception | None = None
    for i, prov in enumerate(order):
        try:
            if prov == "lyria":
                return _lyria_generate(prompt, output_path, instrumental=instrumental)
            return _suno_generate(prompt, output_path, instrumental=instrumental)
        except Exception as e:
            last_err = e
            nxt = f" — falling back to {order[i+1]}" if i + 1 < len(order) else ""
            print(f"[MusicGen] provider '{prov}' failed ({e}){nxt}")
    raise last_err if last_err else RuntimeError("no music provider configured")


# ── Lyria 3 (Google, Gemini API) ─────────────────────────────────────────────
LYRIA_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
LYRIA_TIMEOUT = 300  # full songs (pro) can take a while; clip is fast


def _extract_lyria_audio(body: dict) -> str | None:
    """Find the base64 audio in a Lyria interaction response. Robust to the documented
    shapes — top-level `output_audio.data`, or nested `steps[].content[]` audio blocks,
    or an inlineData/audio mimeType block — so a minor schema change won't break us."""
    oa = body.get("output_audio") or body.get("outputAudio")
    if isinstance(oa, dict) and oa.get("data"):
        return oa["data"]

    def walk(o):
        if isinstance(o, dict):
            t = str(o.get("type") or "")
            mime = str(o.get("mimeType") or o.get("mime_type") or "")
            if o.get("data") and (t in ("audio", "output_audio") or mime.startswith("audio")):
                return o["data"]
            inline = o.get("inlineData") or o.get("inline_data")
            if isinstance(inline, dict) and inline.get("data"):
                return inline["data"]
            for v in o.values():
                r = walk(v)
                if r:
                    return r
        elif isinstance(o, list):
            for v in o:
                r = walk(v)
                if r:
                    return r
        return None

    return walk(body)


def _lyria_generate(prompt: str, output_path: str, *, instrumental: bool = True,
                    model: str | None = None) -> str:
    """Generate one track with Lyria 3 via the Gemini Interactions API (synchronous).
    Vocals/lyrics come from [Verse]/[Chorus] tags in the prompt; for a background bed we
    append an explicit instrumental instruction so vocals don't fight the narration."""
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set in environment")
    model = model or _music_cfg().get("lyria", {}).get("model", "lyria-3-pro-preview")
    p = prompt
    if instrumental and "instrumental" not in p.lower():
        p += " Instrumental only, no vocals."
    print(f"[MusicGen] Lyria ({model}): {p[:80]}...")
    resp = requests.post(
        LYRIA_URL,
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
        json={"model": model, "input": p, "response_format": {"type": "audio"}},
        timeout=LYRIA_TIMEOUT,
    )
    if not resp.ok:
        raise RuntimeError(f"Lyria failed {resp.status_code}: {resp.text[:300]}")
    b64 = _extract_lyria_audio(resp.json())
    if not b64:
        raise RuntimeError(f"Lyria returned no audio: {str(resp.json())[:300]}")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(base64.b64decode(b64))
    print(f"[MusicGen] Saved → {output_path} ({os.path.getsize(output_path)//1024}KB)")
    return output_path


# ── Suno (wrapper API) ───────────────────────────────────────────────────────
SUNO_BASE = "https://api.sunoapi.org"
SUNO_POLL_SEC = 10
SUNO_TIMEOUT = 360  # 6 minutes


def _suno_headers() -> dict:
    key = os.environ.get("SUNO_API_KEY", "")
    if not key:
        raise RuntimeError("SUNO_API_KEY not set in environment")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _extract_audio_url(data: dict) -> str | None:
    """Pull the audio URL from a Suno poll response."""
    suno_list = data.get("data", {}).get("response", {}).get("sunoData", [])
    if suno_list:
        clip = suno_list[0]
        return clip.get("audioUrl") or clip.get("streamAudioUrl") or None
    return None


def _suno_generate(prompt: str, output_path: str, *, instrumental: bool = True,
                   model: str | None = None) -> str:
    """Generate music via Suno: POST /generate → taskId → poll /record-info → download."""
    model = model or _music_cfg().get("suno", {}).get("model", "V5_5")
    print(f"[MusicGen] Suno ({model}): {prompt[:80]}...")
    resp = requests.post(
        f"{SUNO_BASE}/api/v1/generate",
        headers=_suno_headers(),
        json={"prompt": prompt, "model": model, "instrumental": instrumental,
              "customMode": False, "callBackUrl": "https://example.com/noop"},
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

    elapsed = 0
    while elapsed < SUNO_TIMEOUT:
        time.sleep(SUNO_POLL_SEC)
        elapsed += SUNO_POLL_SEC
        poll = requests.get(
            f"{SUNO_BASE}/api/v1/generate/record-info",
            headers=_suno_headers(), params={"taskId": task_id}, timeout=15)
        poll.raise_for_status()
        data = poll.json()
        status = str(data.get("data", {}).get("status", "")).upper()
        print(f"[MusicGen] Status: {status} ({elapsed}s)...")
        if status == "SUCCESS":
            audio_url = _extract_audio_url(data)
            if not audio_url:
                raise RuntimeError(f"SUCCESS but no audio URL in: {data}")
            audio_resp = requests.get(audio_url, timeout=120)
            audio_resp.raise_for_status()
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(audio_resp.content)
            print(f"[MusicGen] Saved → {output_path} ({len(audio_resp.content)//1024}KB)")
            return output_path
        if status == "FAILED":
            raise RuntimeError(f"Suno task failed: {data}")
    raise TimeoutError(f"Suno task {task_id} timed out after {SUNO_TIMEOUT}s")


def generate_story_music(story_emotion: str, output_path: str) -> str:
    """Background score matched to the emotional arc of an Indian story."""
    prompt = (
        f"Emotional Bollywood instrumental background score. {story_emotion}. "
        "Sitar, tabla, soft piano, strings. Starts gentle and melancholic, "
        "builds to hopeful and triumphant. Indian classical undertones. "
        "No lyrics, pure instrumental. Cinematic, 2-4 minutes."
    )
    return generate_music(prompt, output_path)


def compose_music_brief(captions: list[str], mood: str = "") -> str:
    """
    LLM-composed music prompt derived from the STORY itself — works for either engine
    (15-30 descriptors covering style + mood + tempo + instruments + an emotion ARC).
    Falls back to the house default on any failure.
    """
    fallback = ("emotional Indian cinematic instrumental, documentary score, "
                "sitar, tabla, bansuri, warm strings, soft piano, slow tempo "
                "building gradually, starts sparse and melancholic, swells to "
                "warm triumphant strings, organic, intimate, instrumental only, no vocals")
    story = " / ".join(c.strip() for c in captions if c and c.strip())[:1500]
    if not story:
        return fallback
    try:
        from agents import llm
        text = llm.chat(
            [{"role": "user", "content": (
                "Compose ONE music prompt for the background score of an "
                "emotional Indian documentary reel.\n"
                f"Story beats, in order: {story}\n"
                f"Mood hint: {mood or 'derive from the story'}\n"
                "Rules: 15-30 comma-separated descriptors. Include: genre, tempo "
                "feel, 3-4 core instruments (prefer Indian textures — sitar, tabla, "
                "bansuri, harmonium, strings), production style, and an explicit "
                "emotion ARC phrased like 'starts sparse and melancholic, builds to "
                "warm triumphant strings' matched to THIS story's journey. End with "
                "'instrumental only, no vocals'. Reply with ONLY the prompt."
            )}],
            max_tokens=140, model_tier="fast",
        ).strip().strip('"')
        return text if 40 <= len(text) <= 600 else fallback
    except Exception as e:
        print(f"[MusicGen] brief composition failed ({e}) — using default brief")
        return fallback
