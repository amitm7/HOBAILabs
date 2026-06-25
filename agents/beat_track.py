"""Lightweight beat/onset detection for rhythm-aware cutting (P1).

No heavy deps (deliberately NOT librosa): decode the audio with ffmpeg to mono
PCM, build a time-domain onset-strength envelope (positive RMS-energy jumps over
short frames), pick spaced local-maxima peaks, return their timestamps in seconds.

Best-effort by design — returns [] on ANY failure (no audio, ffmpeg missing,
silence, no clear peaks) so the assembler falls back to its uniform crossfade
(P1 red-team: "beat detection is unreliable on vague-tempo music → graceful
fallback, don't hard-depend on it").
"""

from __future__ import annotations

import subprocess

_SR = 22050      # decode sample rate
_HOP = 512       # ~23ms analysis frame


def beat_times(audio_path: str, *, min_gap: float = 0.25) -> list[float]:
    """Return onset/beat timestamps (seconds), spaced ≥ min_gap. [] on any failure."""
    if not audio_path:
        return []
    try:
        import numpy as np
        raw = subprocess.run(
            ["ffmpeg", "-v", "quiet", "-i", audio_path,
             "-ac", "1", "-ar", str(_SR), "-f", "f32le", "-"],
            capture_output=True, check=True,
        ).stdout
        x = np.frombuffer(raw, dtype=np.float32)
        if x.size < _SR:                      # less than ~1s of audio
            return []
        n = x.size // _HOP
        frames = x[: n * _HOP].reshape(n, _HOP)
        energy = np.sqrt((frames ** 2).mean(axis=1) + 1e-9)
        onset = np.maximum(np.diff(energy, prepend=energy[:1]), 0.0)  # positive jumps
        peak = float(onset.max())
        if peak <= 0:
            return []
        onset = onset / peak
        thr = max(0.15, float(onset.mean() + onset.std()))
        sec_per_frame = _HOP / _SR
        times: list[float] = []
        last = -1e9
        for i in range(1, len(onset) - 1):
            if onset[i] >= thr and onset[i] >= onset[i - 1] and onset[i] >= onset[i + 1]:
                t = i * sec_per_frame
                if t - last >= min_gap:
                    times.append(round(t, 3))
                    last = t
        return times
    except Exception:
        return []
