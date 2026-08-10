"""
Sarvam AI TTS backend — native Indic voices (hi/mr/bn/pa/…) behind the
config/tts.json provider seam. ElevenLabs remains the default provider and the
only voice-clone path; Sarvam exists because ElevenLabs has ZERO native
mr/bn/pa voices (config/voices.json audit 2026-07-17), so those languages read
with a foreign accent no matter which ElevenLabs voice is picked.

API contract (VERIFY on docs.sarvam.ai before the first paid run — marked in
config/tts.json): POST https://api.sarvam.ai/text-to-speech with header
`api-subscription-key: SARVAM_API_KEY`; returns base64 audio. This module is
STRICTLY best-effort: any failure raises and the caller falls back to
ElevenLabs (accented but working) — never a broken render.
"""
from __future__ import annotations

import base64
import json
import os

import requests

_CFG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "tts.json")
SARVAM_URL = "https://api.sarvam.ai/text-to-speech"


def config() -> dict:
    try:
        with open(os.path.abspath(_CFG_PATH)) as f:
            return json.load(f) or {}
    except Exception:
        return {}


def provider_for_lang(lang: str | None) -> str:
    """Which TTS provider serves this language ('elevenlabs' | 'sarvam').
    Missing config / unknown language → the default (elevenlabs)."""
    table = config().get("providers_by_language") or {}
    return table.get((lang or "").lower() or "default",
                     table.get("default", "elevenlabs"))


def generate(text: str, path: str, lang: str, speaker: str = "") -> str:
    """Synthesize `text` in the given language's native voice → audio file at
    `path`. Raises on any problem (no key, HTTP error, empty audio) — caller
    falls back to ElevenLabs."""
    key = os.environ.get("SARVAM_API_KEY", "")
    if not key:
        raise RuntimeError("SARVAM_API_KEY not set — add it to .env")
    cfg = config().get("sarvam") or {}
    payload = {
        "text": text,
        "target_language_code": f"{(lang or 'hi').lower()}-IN",
        "model": cfg.get("model", "bulbul:v2"),
        "speaker": speaker or cfg.get("speaker", ""),
    }
    r = requests.post(SARVAM_URL, json=payload,
                      headers={"api-subscription-key": key,
                               "Content-Type": "application/json"},
                      timeout=60)
    if not r.ok:
        raise RuntimeError(f"Sarvam TTS failed {r.status_code}: {r.text[:200]}")
    body = r.json()
    audios = body.get("audios") or []
    if not audios:
        raise RuntimeError(f"Sarvam TTS returned no audio: {str(body)[:200]}")
    with open(path, "wb") as f:
        f.write(base64.b64decode(audios[0]))
    return path
