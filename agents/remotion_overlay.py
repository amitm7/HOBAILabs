"""T6 — animated caption overlay via Remotion (docs/REMOTION_CAPTIONS_PLAN.md).

Renders the reel's captions as a TRANSPARENT animated track (hero-line word-springs,
serif story lines — the JioStar-style typography libass can't do) using the verified
spike project in tools/remotion-captions, then the pipeline composites it over the
assembled video. STRICTLY best-effort: any failure here must make the caller fall
back to the libass burn (never a dead render) — that contract is why render_overlay
raises instead of degrading silently.

Spike-proven gotcha encoded here: WITHOUT `--pixel-format=yuva444p10le` +
`--image-format=png` the ProRes comes out with NO alpha and composites as opaque
black. Cached by props-hash; re-renders only when captions/timings change.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess

_CFG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "captions.json")


def config() -> dict:
    try:
        with open(os.path.abspath(_CFG_PATH)) as f:
            return json.load(f) or {}
    except Exception:
        return {"engine": "libass"}


def engine_for(caption_style: dict | None) -> str:
    """Per-reel override (canvas settings) wins over the config default."""
    style = caption_style or {}
    eng = (style.get("engine") or config().get("engine") or "libass").strip().lower()
    return eng if eng in ("libass", "remotion") else "libass"


def _is_hero(frame: dict, text: str, max_words: int = 8) -> bool:
    if frame.get("hero_line"):
        return True
    t = text.strip()
    return bool(t) and len(t.split()) <= max_words and t[-1:] in ("!", "…")


def build_props(frames: list[dict], timecodes: list[tuple], total_seconds: float,
                accent: str = "#e0a32e", fade_gap: float = 0.3) -> dict:
    """The overlay props: same caption+timing data the .ass builder consumes."""
    caps = []
    for i, f in enumerate(frames):
        text = (f.get("caption") or "").strip()
        if not text:
            continue
        start, end = timecodes[i]
        start, end = start + fade_gap, end - fade_gap
        if end <= start:
            continue
        kind = "hero" if _is_hero(f, text) else "story"
        cap = {"text": text, "start": round(start, 3), "end": round(end, 3), "kind": kind}
        if kind == "hero":
            cap["accent"] = accent
        caps.append(cap)
    return {"durationSeconds": round(max(total_seconds, 1.0), 3), "captions": caps}


def render_overlay(props: dict, out_mov: str, width: int = 1080,
                   height: int = 1920) -> str:
    """Render the transparent caption track AT THE REEL'S DIMENSIONS. Raises on
    ANY problem (missing node, npm deps, Chromium failure, empty output) — the
    caller falls back to the libass burn.

    width/height override the composition's default 1080×1920 (Root.tsx): the
    layout anchors captions relatively (bottom-px / %-top), so it holds at any
    canvas. Without the override a LANDSCAPE reel composited a portrait-sized
    overlay whose bottom-anchored captions sat ~1700px down — below the visible
    1080px, i.e. captions rendered fine but OFF-SCREEN (found live, 2026-07-19
    Yamraj A/B reel)."""
    cfg = config().get("remotion") or {}
    proj = os.path.abspath(cfg.get("project_dir") or "tools/remotion-captions")
    comp = cfg.get("composition") or "CaptionOverlay"
    if not shutil.which("npx"):
        raise RuntimeError("npx not found — node toolchain unavailable")
    if not os.path.exists(os.path.join(proj, "node_modules", "remotion")):
        raise RuntimeError(f"remotion deps not installed in {proj} (run npm install)")

    # Dims join the cache key — a portrait-cached overlay must never serve a
    # landscape re-render of the same captions.
    key = hashlib.md5(json.dumps(props, sort_keys=True).encode()
                      + f"|{width}x{height}".encode()).hexdigest()[:12]
    cache = os.path.join(os.path.dirname(out_mov), f"capoverlay_{key}.mov")
    if os.path.exists(cache) and os.path.getsize(cache) > 50_000:
        shutil.copy2(cache, out_mov)
        print(f"[Remotion] caption overlay reused from cache ({key})")
        return out_mov

    props_path = os.path.join(proj, f"_props_{key}.json")
    with open(props_path, "w") as f:
        json.dump(props, f, ensure_ascii=False)
    try:
        print(f"[Remotion] rendering caption overlay ({len(props['captions'])} captions, "
              f"{props['durationSeconds']}s, {width}x{height})…")
        r = subprocess.run(
            ["npx", "remotion", "render", "src/index.ts", comp, out_mov,
             "--codec=prores", "--prores-profile=4444",
             "--pixel-format=yuva444p10le", "--image-format=png", "--muted",
             f"--width={width}", f"--height={height}",
             f"--props={props_path}", "--log=error"],
            cwd=proj, capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            raise RuntimeError(f"remotion render failed: {(r.stderr or r.stdout)[-300:]}")
        if not (os.path.exists(out_mov) and os.path.getsize(out_mov) > 50_000):
            raise RuntimeError("remotion produced no usable overlay")
        # verify alpha survived (the spike incident)
        probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                "-show_entries", "stream=pix_fmt", "-of", "csv=p=0", out_mov],
                               capture_output=True, text=True, timeout=30)
        if "yuva" not in probe.stdout:
            raise RuntimeError(f"overlay has NO alpha channel ({probe.stdout.strip()}) — would composite black")
        shutil.copy2(out_mov, cache)
        return out_mov
    finally:
        try:
            os.remove(props_path)
        except OSError:
            pass


def composite(video_in: str, overlay_mov: str, video_out: str) -> str:
    """Overlay the transparent caption track onto the assembled reel (one encode)."""
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", video_in, "-i", overlay_mov,
         "-filter_complex", "[0:v][1:v]overlay=0:0:shortest=1[v]",
         "-map", "[v]", "-map", "0:a?", "-c:a", "copy",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "18",
         video_out],
        check=True, timeout=1800)
    if not (os.path.exists(video_out) and os.path.getsize(video_out) > 10_000):
        raise RuntimeError("caption composite produced no output")
    return video_out
