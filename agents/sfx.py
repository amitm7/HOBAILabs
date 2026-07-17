"""
SFX / atmosphere layer — S30 Phase 2 seam (the S28 "SFX/atmosphere" item,
competitor-validated in COMPETITOR_GALLERI5_TEARDOWN.md §10: their auto-SFX bed is
what made a raw clip feel finished).

ONE capability, rented: an MMAudio-class video→audio model takes one CLIP + a short
text hint and returns an atmosphere/foley track synced to that clip's motion (wind as
the mist rolls, cloth as the figure moves). Model + endpoint live in
config/models.json ("mmaudio"), the price in config/pricing.json ("sfx") — swapping
vendors is a config edit, not Python (build-feature rule 3).

    generate_sfx(video_path, prompt, out_path, variant=0) -> str
        Returns the path to a WAV synced to the clip, or "" on ANY failure —
        callers must treat "" as "no SFX" and mix nothing (rule 4: a missing
        atmosphere track can never break a render). Content-hash cached
        (video bytes + prompt + variant): re-rendering the same clip re-spends
        nothing; variant obeys rule 12 (0 = reuse, else fresh sample).

Consumption (NOT here): the Final Cut mix — adding this track as a low stem under
voiceover/music with the existing ducking — is the ticketed follow-up in
docs/S30_ADOPTION_PLAN.md Phase 2. Audio-mix changes get their own focused verify
loop (the S1 silent-reel lesson); this module stays independently testable.
"""

import hashlib
import json
import os
import subprocess
from pathlib import Path

_MODELS_PATH = Path(__file__).parent.parent / "config" / "models.json"


def _model_cfg() -> dict:
    try:
        with open(_MODELS_PATH) as f:
            return json.load(f)["models"]["mmaudio"]
    except Exception:
        return {"fal_endpoint": "fal-ai/mmaudio-v2"}


def _cache_key(video_path: str, prompt: str, variant: int) -> str:
    h = hashlib.md5()
    with open(video_path, "rb") as f:
        h.update(f.read(1 << 20))          # first 1MB is plenty to fingerprint a clip
    h.update(f"|{os.path.getsize(video_path)}|{prompt}|v{variant}".encode())
    return h.hexdigest()[:10]


def generate_sfx(video_path: str, prompt: str, out_path: str, *, variant: int = 0) -> str:
    """Video + text hint → synced atmosphere/foley WAV at out_path. '' on failure."""
    if not (video_path and os.path.exists(video_path)):
        return ""
    cached = f"{os.path.splitext(out_path)[0]}_{_cache_key(video_path, prompt, variant)}.wav"
    if os.path.exists(cached) and os.path.getsize(cached) > 10_000:
        print(f"[SFX] reusing cached track ({os.path.basename(cached)})")
        return cached
    # BEFORE the paid call, not after: neither download_media (bare open()) nor ffmpeg
    # creates parent dirs, so a missing run subdir failed the write *after* fal had
    # already billed — and the except below swallowed it and returned "" (paid, no track,
    # only an info-level ledger line). Sibling generate_location_plate makedirs too.
    out_dir = os.path.dirname(cached)
    if out_dir:
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception as e:
            print(f"[SFX] degraded — cannot create {out_dir} ({e}); skipping before any spend")
            return ""
    try:
        from agents import fal_client
        cfg = _model_cfg()
        result = fal_client.run_sync(cfg.get("fal_endpoint", "fal-ai/mmaudio-v2"), {
            "video_url": fal_client.file_to_data_uri(video_path),
            "prompt": prompt or "natural ambient atmosphere and subtle foley matching the scene",
        }, timeout=180)
        # MMAudio-family endpoints return either the merged video (extract audio) or a
        # bare audio url — handle both shapes via the shared extractor.
        url = fal_client.extract_media_url(result, ("audio", "audio_url", "video"))
        if not url:
            raise RuntimeError(f"no media in result keys={list(result)[:6]}")
        tmp = cached + ".dl"
        fal_client.download_media(url, tmp)
        # Normalize to WAV (and strip video if the endpoint returned a merged mp4).
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", tmp,
                        "-vn", "-acodec", "pcm_s16le", "-ar", "44100", cached],
                       check=True, timeout=120)
        os.remove(tmp)
        if os.path.getsize(cached) < 10_000:
            raise RuntimeError("SFX track suspiciously small")
        return cached
    except Exception as e:
        print(f"[SFX] degraded — no atmosphere track ({e})")
        try:
            from agents import degradation
            degradation.report("sfx", "info",
                               f"SFX/atmosphere unavailable for {os.path.basename(video_path)} ({e}); "
                               "clip mixes without it")
        except Exception:
            pass
        return ""
