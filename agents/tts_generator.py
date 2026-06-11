import os
import subprocess
import json
import tempfile
from pathlib import Path
from openai import OpenAI


def get_audio_duration(path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", path],
        capture_output=True, text=True, check=True
    )
    data = json.loads(result.stdout)
    return float(data["streams"][0]["duration"])


def get_voices() -> list[dict]:
    """Fetch available voices from ElevenLabs. Returns [{voice_id, name, category}]."""
    from elevenlabs import ElevenLabs
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        return []
    client = ElevenLabs(api_key=api_key)
    try:
        resp = client.voices.get_all()
        voices = []
        for v in resp.voices:
            voices.append({
                "voice_id": v.voice_id,
                "name": v.name,
                "category": getattr(v, "category", ""),
            })
        # Sort: premade first, then cloned, alphabetical within each group
        voices.sort(key=lambda x: (x["category"] != "premade", x["name"].lower()))
        return voices
    except Exception as e:
        print(f"[TTS] Could not fetch voices: {e}")
        return []


def _generate_elevenlabs(text: str, path: str, voice_id: str):
    from elevenlabs import ElevenLabs
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    client = ElevenLabs(api_key=api_key)
    for output_format in ["mp3_44100_128", "mp3_44100_64"]:
        try:
            audio = client.text_to_speech.convert(
                voice_id=voice_id,
                text=text,
                model_id="eleven_multilingual_v2",
                output_format=output_format,
            )
            break
        except Exception as e:
            if "output_format_not_allowed" in str(e) or "subscription_required" in str(e):
                continue
            raise
    else:
        raise RuntimeError("ElevenLabs: no supported output format for this subscription tier")
    with open(path, "wb") as f:
        for chunk in audio:
            f.write(chunk)


def _generate_openai(text: str, path: str):
    client = OpenAI()
    response = client.audio.speech.create(model="tts-1-hd", voice="nova", input=text)
    response.stream_to_file(path)


def _generate_silence(duration: float, path: str):
    """Generate a silent MP3 of the given duration using ffmpeg."""
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"anullsrc=r=44100:cl=stereo",
        "-t", str(duration),
        "-q:a", "9", "-acodec", "libmp3lame",
        path,
    ], check=True, capture_output=True)


def generate_single_tts(text: str, path: str, voice_id: str) -> float:
    """
    Generate a single TTS audio file for one caption.
    Returns the audio duration in seconds.
    Used by the lip sync coordinator for per-frame audio generation.
    Raises RuntimeError if ElevenLabs key is not set or generation fails.
    """
    if not text.strip():
        raise ValueError("generate_single_tts called with empty text")
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key or not voice_id:
        raise RuntimeError("ELEVENLABS_API_KEY and voice_id are required for lip sync audio")
    _generate_elevenlabs(text, path, voice_id)
    return get_audio_duration(path)


def generate_voiceover_track(frames: list[dict], out_path: str, voice_id: str) -> str:
    """
    Generate a voice-over audio track timed to match the video.
    Each frame caption is read by ElevenLabs; silent frames get silence.
    The result is one concatenated MP3 at out_path.
    Returns out_path.
    """
    use_elevenlabs = bool(os.environ.get("ELEVENLABS_API_KEY")) and bool(voice_id)
    provider = "ElevenLabs" if use_elevenlabs else "OpenAI TTS"
    print(f"[Voiceover] Provider: {provider} | Voice: {voice_id or 'nova'}")

    tmp_dir = tempfile.mkdtemp(prefix="hob_vo_")
    segment_files = []

    def _silence_seg(seconds: float, path: str):
        """Exact-length silence at uniform params (44.1k stereo mp3)."""
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-t", f"{seconds:.3f}",
            "-i", "anullsrc=r=44100:cl=stereo",
            "-c:a", "libmp3lame", "-q:a", "4", path,
        ], check=True, capture_output=True)

    def _fit_seg(raw: str, path: str, seconds: float):
        """Pad spoken audio with trailing silence (or trim) to EXACTLY `seconds`
        so the voice for each frame lines up with that frame's caption + visuals."""
        subprocess.run([
            "ffmpeg", "-y", "-i", raw,
            "-af", f"apad=whole_dur={seconds:.3f},atrim=0:{seconds:.3f}",
            "-ar", "44100", "-ac", "2", "-c:a", "libmp3lame", "-q:a", "4", path,
        ], check=True, capture_output=True)

    for i, frame in enumerate(frames):
        caption  = (frame.get("caption") or "").strip()
        duration = float(frame.get("duration", 5.0))
        seg_path = os.path.join(tmp_dir, f"seg_{i:03d}.mp3")

        if not caption:
            _silence_seg(duration, seg_path)
            print(f"  {frame.get('frame_id','?')} → silence ({duration:.1f}s)")
        else:
            raw_path = os.path.join(tmp_dir, f"raw_{i:03d}.mp3")
            try:
                if use_elevenlabs:
                    _generate_elevenlabs(caption, raw_path, voice_id)
                else:
                    _generate_openai(caption, raw_path)
                spoken = get_audio_duration(raw_path)
                _fit_seg(raw_path, seg_path, duration)   # pad/trim to frame length
                note = "trimmed" if spoken > duration + 0.05 else "padded"
                print(f"  {frame.get('frame_id','?')} → {spoken:.1f}s spoken, {note} to {duration:.1f}s")
            except Exception as e:
                print(f"  {frame.get('frame_id','?')} → TTS failed ({e}), using silence")
                _silence_seg(duration, seg_path)

        segment_files.append(seg_path)

    # Concatenate (segments are uniform params + exact-length → safe stream copy).
    # Total track length == sum of frame durations == the caption timeline.
    list_file = os.path.join(tmp_dir, "concat.txt")
    with open(list_file, "w") as f:
        for sf in segment_files:
            f.write(f"file '{sf}'\n")

    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", list_file, "-c", "copy", out_path,
    ], check=True, capture_output=True)

    print(f"[Voiceover] Track saved → {out_path}")
    return out_path


def generate_tts(segments: list[dict], temp_dir: str, voice: str = "rachel") -> list[dict]:
    """Generate TTS audio per segment. ElevenLabs if key present, else OpenAI TTS."""
    temp_path = Path(temp_dir)
    use_elevenlabs = bool(os.environ.get("ELEVENLABS_API_KEY"))
    provider = "ElevenLabs" if use_elevenlabs else "OpenAI TTS"
    print(f"[TTS] Provider: {provider} | Voice: {voice}")

    results = []
    el_failed = False

    for seg in segments:
        audio_path = str(temp_path / f"audio_{seg['id']}.mp3")

        if use_elevenlabs and not el_failed:
            try:
                _generate_elevenlabs(seg["text"], audio_path, voice)
            except Exception as e:
                err = str(e)
                if "paid_plan_required" in err or "payment_required" in err:
                    print(f"[TTS] ElevenLabs requires paid plan — switching to OpenAI TTS")
                    el_failed = True
                    _generate_openai(seg["text"], audio_path)
                else:
                    raise
        else:
            _generate_openai(seg["text"], audio_path)

        actual_duration = get_audio_duration(audio_path)
        results.append({
            "segment_id": seg["id"],
            "text": seg["text"],
            "audio_path": audio_path,
            "actual_duration": actual_duration,
        })
        print(f"[TTS] {seg['id']} → {actual_duration:.1f}s")

    return results
