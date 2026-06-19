"""
Lip sync coordinator — orchestrates audio generation, vendor routing, and caching.

For each frame with lipsync=True:
  1. Generate ElevenLabs audio for the caption (MD5-cached by caption+voice_id)
  2. Set frame["duration"] = audio duration (duration flip — audio drives timing)
  3. Route to SyncLabs (video source) or Hedra (image source)
  4. Submit all jobs in parallel, poll in parallel (ThreadPoolExecutor, same pattern as Kling)
  5. Download lip-synced clips, cache them
  6. Set frame["lipsync_clip_path"] on success; clear frame["lipsync"] on failure → Ken Burns fallback

Public API:
  run_lipsync_pass(frames, temp_dir, default_voice_id) -> list[dict]
"""

import hashlib
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from agents.cache_store import BlobCache

# Paid lip-sync artifacts — FS by default, S3-backed when HOB_CACHE_BACKEND=s3
# so they survive container redeploys (see agents/cache_store.py).
_LIPSYNC_CACHE = BlobCache("lipsync_clips", ext=".mp4", min_bytes=10_000)
_AUDIO_CACHE   = BlobCache("lipsync_audio", ext=".mp3", min_bytes=1_000)

_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".m4v", ".webm"}


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _audio_cache_key(caption: str, voice_id: str) -> str:
    h = hashlib.md5()
    h.update(caption.encode())
    h.update(voice_id.encode())
    return h.hexdigest()


def _clip_cache_key(media_path: str, audio_path: str) -> str:
    h = hashlib.md5()
    for p in [media_path, audio_path]:
        try:
            with open(p, "rb") as f:
                h.update(f.read())
        except Exception:
            h.update(p.encode())
    return h.hexdigest()


# ── Image upload (reuse Higgsfield CDN) ──────────────────────────────────────

def _upload_for_lipsync(file_path: str) -> str:
    """
    Upload a local file to Higgsfield's CDN and return a public URL.
    Reuses the same higgsfield_client already integrated for Higgsfield provider.
    """
    if not os.environ.get("HF_API_KEY"):
        os.environ["HF_API_KEY"]    = os.environ.get("HIGGSFIELD_KEY_ID", "")
    if not os.environ.get("HF_API_SECRET"):
        os.environ["HF_API_SECRET"] = os.environ.get("HIGGSFIELD_KEY_SECRET", "")
    try:
        import higgsfield_client
        url = higgsfield_client.upload_file(file_path)
        return str(url)
    except Exception as e:
        raise RuntimeError(f"CDN upload failed for {Path(file_path).name}: {e}")


# ── Per-frame audio generation ────────────────────────────────────────────────

def _generate_audio(caption: str, voice_id: str, audio_dir: str) -> tuple[str, float]:
    """
    Generate ElevenLabs audio for one caption. Returns (path, duration_seconds).
    Uses MD5 cache — identical caption+voice_id reuses the cached file.
    """
    from agents.tts_generator import generate_single_tts, get_audio_duration

    key = _audio_cache_key(caption, voice_id)
    hit = _AUDIO_CACHE.local_path(key)
    if hit:
        print(f"[LipsyncAudio] Cache hit → {Path(hit).name}")
        return hit, get_audio_duration(hit)

    tmp_path = os.path.join(audio_dir, f"ls_audio_{key[:8]}.mp3")
    dur = generate_single_tts(caption, tmp_path, voice_id)
    _AUDIO_CACHE.put(key, tmp_path)
    return tmp_path, dur


# ── Submit one frame ──────────────────────────────────────────────────────────

def _submit_one(frame: dict, temp_dir: str, audio_dir: str,
                default_voice_id: str, voice_map: dict | None = None) -> dict:
    """
    Generate audio, check clip cache, upload, and submit to vendor.
    Returns frame with _ls_pending=True if a job was submitted,
    or frame with lipsync_clip_path set if cache hit.
    Clears frame["lipsync"] and returns quietly on any error.
    """
    fid     = frame["frame_id"]
    caption = (frame.get("caption") or "").strip()
    vpath   = frame.get("visual_path", "")

    if not caption:
        print(f"[LipsyncCoordinator] {fid}: silent frame — skipping lipsync")
        frame["lipsync"] = False
        return frame

    if not vpath or not os.path.exists(vpath):
        print(f"[LipsyncCoordinator] {fid}: no visual file — skipping lipsync")
        frame["lipsync"] = False
        return frame

    from agents.cast import voice_for_frame
    voice_id = voice_for_frame(frame, default_voice_id, voice_map)
    if not voice_id:
        print(f"[LipsyncCoordinator] {fid}: no voice_id — skipping lipsync (set ELEVENLABS_VOICE_ID)")
        frame["lipsync"] = False
        return frame
    if frame.get("speaker_id", "narrator") != "narrator":
        print(f"[LipsyncCoordinator] {fid}: speaker {frame.get('speaker_label','?')} → voice {voice_id[:8]}…")

    # ── 1. Generate audio ──────────────────────────────────────────────────
    try:
        audio_path, audio_dur = _generate_audio(caption, voice_id, audio_dir)
    except Exception as e:
        print(f"[LipsyncCoordinator] {fid}: audio generation failed ({e}) — falling back")
        frame["lipsync"] = False
        return frame

    # ── 2. Duration flip ───────────────────────────────────────────────────
    frame["duration"] = round(audio_dur, 2)

    # ── 3. Clip cache check ────────────────────────────────────────────────
    clip_key = _clip_cache_key(vpath, audio_path)
    cached_clip = _LIPSYNC_CACHE.local_path(clip_key)
    if cached_clip:
        print(f"[LipsyncCoordinator] {fid}: clip cache hit → {Path(cached_clip).name}")
        frame["lipsync_clip_path"] = cached_clip
        return frame

    # ── 4. Upload media + audio to CDN ────────────────────────────────────
    try:
        media_url = _upload_for_lipsync(vpath)
        audio_url = _upload_for_lipsync(audio_path)
    except Exception as e:
        print(f"[LipsyncCoordinator] {fid}: CDN upload failed ({e}) — falling back")
        frame["lipsync"] = False
        return frame

    # ── 5. Route and submit ────────────────────────────────────────────────
    is_video  = Path(vpath).suffix.lower() in _VIDEO_EXTS
    has_sync  = bool(os.environ.get("SYNCLABS_API_KEY"))
    has_hedra = bool(os.environ.get("HEDRA_API_KEY"))

    if is_video and has_sync:
        vendor = "synclabs"
    elif has_hedra:
        vendor = "hedra"
    else:
        missing = "SYNCLABS_API_KEY" if is_video else "HEDRA_API_KEY"
        print(f"[LipsyncCoordinator] {fid}: {missing} not set — skipping lipsync")
        frame["lipsync"] = False
        return frame

    output_path = os.path.join(temp_dir, f"lipsync_{fid}.mp4")

    try:
        if vendor == "synclabs":
            from agents.synclabs import submit as sl_submit
            job_id = sl_submit(media_url, audio_url)
        else:
            from agents.hedra import upload_asset, submit as hedra_submit
            aspect = "9:16"  # portrait default
            img_id = upload_asset(vpath,       "image")
            aud_id = upload_asset(audio_path,  "audio")
            job_id = hedra_submit(img_id, aud_id, aspect)
    except Exception as e:
        print(f"[LipsyncCoordinator] {fid}: {vendor} submit failed ({e}) — falling back")
        frame["lipsync"] = False
        return frame

    frame["_ls_job_id"]      = job_id
    frame["_ls_vendor"]      = vendor
    frame["_ls_output_path"] = output_path
    frame["_ls_cache_key"]   = clip_key
    frame["_ls_audio_path"]  = audio_path
    print(f"[LipsyncCoordinator] {fid}: submitted → {vendor} job {job_id}")
    return {**frame, "_ls_pending": True}


# ── Poll one frame ────────────────────────────────────────────────────────────

def _poll_one(frame: dict) -> dict:
    """
    Poll vendor until complete, download result, cache it.
    On failure: clear lipsync flag → falls back to Ken Burns/Kling.
    """
    fid    = frame["frame_id"]
    vendor = frame["_ls_vendor"]
    try:
        if vendor == "synclabs":
            from agents.synclabs import poll_and_download as sl_poll
            sl_poll(frame["_ls_job_id"], frame["_ls_output_path"])
        else:
            from agents.hedra import poll_and_download as hedra_poll
            hedra_poll(frame["_ls_job_id"], frame["_ls_output_path"])

        # Cache the result (local + S3 when enabled)
        _LIPSYNC_CACHE.put(frame["_ls_cache_key"], frame["_ls_output_path"])
        frame["lipsync_clip_path"] = frame["_ls_output_path"]
        print(f"[LipsyncCoordinator] {fid}: ✓ lipsync clip ready")

    except Exception as e:
        print(f"[LipsyncCoordinator] {fid}: {vendor} poll failed ({e}) — falling back to Ken Burns")
        frame["lipsync"] = False
        frame.pop("lipsync_clip_path", None)

    return frame


# ── Public entry point ────────────────────────────────────────────────────────

def run_lipsync_pass(frames: list[dict], temp_dir: str,
                     default_voice_id: str = "", voice_map: dict | None = None) -> list[dict]:
    """
    Process all frames with lipsync=True.

    Phase 1 (parallel): generate audio + upload + submit to SyncLabs or Hedra.
    Phase 2 (parallel): poll all pending jobs and download results.

    Mutates frames in-place: sets lipsync_clip_path, updates duration.
    Frames that fail at any step have lipsync cleared → pipeline fallback is Ken Burns.

    Returns the updated frames list.
    """
    lipsync_frames = [f for f in frames if f.get("lipsync")]
    if not lipsync_frames:
        return frames

    voice_id  = default_voice_id or os.environ.get("ELEVENLABS_VOICE_ID", "")
    audio_dir = tempfile.mkdtemp(prefix="hob_ls_audio_", dir=temp_dir)

    print(f"[LipsyncCoordinator] Processing {len(lipsync_frames)} lipsync frame(s)…")

    # ── Phase 1: parallel submit ───────────────────────────────────────────
    submitted = []
    ready     = []

    # Use a frame_id index for merging results back
    frame_by_id = {f["frame_id"]: f for f in frames}

    with ThreadPoolExecutor(max_workers=min(len(lipsync_frames), 6)) as pool:
        future_map = {
            pool.submit(_submit_one, f, temp_dir, audio_dir, voice_id, voice_map): f["frame_id"]
            for f in lipsync_frames
        }
        for future in as_completed(future_map):
            result = future.result()
            fid    = result["frame_id"]
            # Merge updated fields back into the original frame dict
            frame_by_id[fid].update(result)
            if result.get("_ls_pending"):
                submitted.append(frame_by_id[fid])
            elif result.get("lipsync_clip_path"):
                ready.append(fid)  # cache hit — already done

    if ready:
        print(f"[LipsyncCoordinator] {len(ready)} frame(s) served from cache")

    # ── Phase 2: parallel poll ─────────────────────────────────────────────
    if submitted:
        print(f"[LipsyncCoordinator] Polling {len(submitted)} pending job(s)…")
        with ThreadPoolExecutor(max_workers=len(submitted)) as pool:
            future_map = {
                pool.submit(_poll_one, f): f["frame_id"]
                for f in submitted
            }
            for future in as_completed(future_map):
                result = future.result()
                fid    = result["frame_id"]
                frame_by_id[fid].update(result)

    return frames
