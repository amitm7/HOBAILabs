import os
from pathlib import Path
from openai import OpenAI

WORDS_PER_CAPTION = 4   # words shown on screen at once


def _format_srt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _segment_level_srt(audio_segments: list[dict]) -> list[dict]:
    """Fallback: one caption per segment."""
    entries = []
    current = 0.0
    for seg in audio_segments:
        words = seg["text"].split()
        lines = []
        for i in range(0, len(words), 6):
            lines.append(" ".join(words[i:i + 6]))
        entries.append({
            "start": current,
            "end": current + seg["actual_duration"],
            "text": "\n".join(lines),
        })
        current += seg["actual_duration"]
    return entries


def _whisper_word_level_srt(audio_segments: list[dict]) -> list[dict]:
    """Use Whisper API to get per-word timestamps, group into short on-screen phrases."""
    client = OpenAI()
    entries = []
    cumulative_offset = 0.0

    for seg in audio_segments:
        try:
            with open(seg["audio_path"], "rb") as f:
                result = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=f,
                    response_format="verbose_json",
                    timestamp_granularities=["word"],
                )
            words = result.words or []
            if not words:
                raise ValueError("No word timestamps returned")

            # Group into chunks of WORDS_PER_CAPTION
            for i in range(0, len(words), WORDS_PER_CAPTION):
                chunk = words[i:i + WORDS_PER_CAPTION]
                text = " ".join(w.word.strip() for w in chunk)
                start = cumulative_offset + chunk[0].start
                end = cumulative_offset + chunk[-1].end
                entries.append({"start": start, "end": end, "text": text})

        except Exception as e:
            print(f"[Captions] Whisper failed for {seg['segment_id']}: {e} — using segment fallback")
            entries.append({
                "start": cumulative_offset,
                "end": cumulative_offset + seg["actual_duration"],
                "text": seg["text"],
            })

        cumulative_offset += seg["actual_duration"]

    return entries


def _write_srt(entries: list[dict], output_path: str):
    lines = []
    for i, e in enumerate(entries, 1):
        lines.append(str(i))
        lines.append(f"{_format_srt_time(e['start'])} --> {_format_srt_time(e['end'])}")
        lines.append(e["text"])
        lines.append("")
    Path(output_path).write_text("\n".join(lines), encoding="utf-8")


def generate_srt(audio_segments: list[dict], output_path: str) -> str:
    """Generate SRT. Uses Whisper word-level if OpenAI key available, else segment-level."""
    if os.environ.get("OPENAI_API_KEY"):
        print("[Captions] Using Whisper for word-level timing...")
        entries = _whisper_word_level_srt(audio_segments)
    else:
        print("[Captions] Using segment-level captions...")
        entries = _segment_level_srt(audio_segments)

    _write_srt(entries, output_path)
    print(f"[Captions] {len(entries)} caption entries → {output_path}")
    return output_path
