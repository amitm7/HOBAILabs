"""Editor hand-off serializers — turn a finished run into files a real NLE opens.

The product model is "generate 80–90% in-app, finish in the editor's own licensed
tool." That only works if the hand-off carries the *timeline structure*, not a flat
MP4. So we emit:

  · timeline.fcpxml — one asset-clip per frame, placed back-to-back at the right
    timecodes, that imports into Final Cut / Premiere / DaVinci Resolve as an
    editable timeline (clips already laid out → editor starts at ~80%, not 0%).
  · captions.srt — standard subtitles, universally importable, kept SEPARATE so a
    caption quirk can never break the (high-value) clip timeline import.

Both reference `clips/clip_<frame_id>.mp4` — the per-frame files the export zip
bundles — so paths resolve after unzip (the NLE may still prompt to relink media,
which is normal for any interchange package).

Crossfades are flattened (clips back-to-back); the editor re-applies transitions as
part of the final craft. Music/VO lives in the bundled output.mp4 for reference.
"""

from __future__ import annotations

import html
import re


def _rt(frame_count: int, fps: int) -> str:
    """FCPXML rational time, frame-aligned to fps (e.g. 120/30s = 4s)."""
    return f"{frame_count}/{fps}s" if frame_count else "0s"


def build_fcpxml(frames: list[dict], width: int = 1080, height: int = 1920,
                 fps: int = 30, project_name: str = "HOBAILabs Reel") -> str:
    """One asset-clip per frame on the spine → an importable editable timeline."""
    fps = int(fps) or 30
    res_lines, spine_lines = [], []
    offset_f, aid = 0, 0
    for f in frames:
        fid = str(f.get("frame_id") or "").strip()
        if not fid:
            continue
        df = max(1, round(float(f.get("duration") or 0) * fps))
        aid += 1
        name = html.escape(fid)
        src = f"file://clips/clip_{fid}.mp4"   # relative to the unzipped package
        res_lines.append(
            f'<asset id="a{aid}" name="{name}" start="0s" duration="{_rt(df, fps)}" '
            f'hasVideo="1" hasAudio="1" videoSources="1" audioSources="1" format="r1">'
            f'<media-rep kind="original-media" src="{src}"/></asset>')
        spine_lines.append(
            f'<asset-clip ref="a{aid}" offset="{_rt(offset_f, fps)}" '
            f'duration="{_rt(df, fps)}" name="{name}" start="0s" tcFormat="NDF"/>')
        offset_f += df

    total = _rt(offset_f, fps)
    fmt = (f'<format id="r1" name="FFVideoFormat{height}x{width}p{fps}" '
           f'frameDuration="1/{fps}s" width="{width}" height="{height}" '
           f'colorSpace="1-1-1 (Rec. 709)"/>')
    nl = "\n"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE fcpxml>\n'
        '<fcpxml version="1.9">\n'
        '  <resources>\n'
        f'    {fmt}\n'
        + nl.join("    " + l for l in res_lines) + (nl if res_lines else "") +
        '  </resources>\n'
        '  <library>\n'
        '    <event name="HOBAILabs Export">\n'
        f'      <project name="{html.escape(project_name)}">\n'
        f'        <sequence format="r1" duration="{total}" tcStart="0s" tcFormat="NDF">\n'
        '          <spine>\n'
        + nl.join("            " + l for l in spine_lines) + (nl if spine_lines else "") +
        '          </spine>\n'
        '        </sequence>\n'
        '      </project>\n'
        '    </event>\n'
        '  </library>\n'
        '</fcpxml>\n'
    )


def _srt_ts(sec: float) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600); m = int((sec % 3600) // 60); s = int(sec % 60); ms = int(round((sec % 1) * 1000))
    if ms == 1000:
        s += 1; ms = 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(frames: list[dict], timecodes: list[tuple[float, float]] | None = None) -> str:
    """Standard SRT. Uses effective timecodes when given; strips ==highlight== marks."""
    out, idx, cursor = [], 1, 0.0
    for i, f in enumerate(frames):
        cap = (f.get("caption") or "").strip()
        dur = float(f.get("duration") or 0)
        start, end = (timecodes[i] if timecodes and i < len(timecodes) else (cursor, cursor + dur))
        if cap:
            clean = re.sub(r"==(.+?)==", r"\1", cap)   # drop keyword-highlight markers
            out.append(f"{idx}\n{_srt_ts(start)} --> {_srt_ts(end)}\n{clean}\n")
            idx += 1
        cursor += dur
    return "\n".join(out)
