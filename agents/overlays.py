"""Frame Composer (T14, docs/FRAME_COMPOSER_PLAN.md Part A).

Per-shot STATIC image overlays composited onto the shot's clip: speaker chips,
polaroid memory insets, comic thought-bubbles, and image-free stickers. Zero model
spend — PIL styles the overlay into an alpha PNG, ffmpeg composites it per-clip
(clip-local time, so crossfade timecode drift can never touch it).

frame["overlays"] = [{
  "id": "ov1", "kind": "speaker|memory|thought|sticker",
  "image": "<path>" | "@char:<char_id>" | "",     # sticker needs no image
  "style": "chip|polaroid|rounded|bubble",
  "pos": "tl|tc|tr|ml|mr|bl|bc|br", "size": "s|m|l", "when": "all|first-half|second-half",
}]

Presets only (red-teamed): 9-grid + S/M/L keeps every composite title-safe and
consistent; max 2 overlays/shot is enforced at the route. Composites are cached by
(clip content + overlays json) — editing an overlay re-composites in seconds and
NEVER re-renders the base clip. Failure = un-overlaid clip + degradation warn.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess

_SIZES = {"s": 0.18, "m": 0.26, "l": 0.36}
_MARGIN = 0.045          # title-safe margin as a fraction of the frame width


def _prep_dir(run_dir: str) -> str:
    d = os.path.join(run_dir, "overlays")
    os.makedirs(d, exist_ok=True)
    return d


def _hash(*parts: str) -> str:
    return hashlib.md5("|".join(parts).encode()).hexdigest()[:12]


# ── Style pass: source image → styled RGBA PNG ─────────────────────────────────

def style_overlay(image_path: str, style: str, px: int, out_dir: str) -> str:
    """Render ONE styled overlay tile (alpha PNG), cached by content+style+size."""
    from PIL import Image, ImageDraw, ImageOps, ImageFilter

    key = _hash(image_path if image_path else "none",
                str(os.path.getmtime(image_path)) if image_path and os.path.exists(image_path) else "0",
                style, str(px))
    out = os.path.join(out_dir, f"ov_{style}_{key}.png")
    if os.path.exists(out) and os.path.getsize(out) > 500:
        return out

    if style == "sticker" or not image_path:
        # Image-free comic device: thought-dots bubble drawn from pure shapes.
        img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        w = px
        d.ellipse([w*0.08, w*0.10, w*0.92, w*0.66], fill=(255, 255, 255, 235),
                  outline=(15, 23, 42, 255), width=max(2, px // 64))
        for i, (cx, cy, r) in enumerate([(0.30, 0.78, 0.05), (0.22, 0.88, 0.035), (0.16, 0.95, 0.022)]):
            d.ellipse([w*(cx-r), w*(cy-r), w*(cx+r), w*(cy+r)], fill=(255, 255, 255, 235),
                      outline=(15, 23, 42, 255), width=max(1, px // 90))
        for i in range(3):
            x = w * (0.36 + i * 0.14)
            d.ellipse([x, w*0.34, x + w*0.06, w*0.40], fill=(15, 23, 42, 220))
        img.save(out)
        return out

    src = Image.open(image_path).convert("RGB")
    src = ImageOps.exif_transpose(src)
    src = ImageOps.fit(src, (px, px), Image.LANCZOS)

    if style == "chip":                       # circular speaker chip with ring
        img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        mask = Image.new("L", (px, px), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, px, px], fill=255)
        img.paste(src, (0, 0), mask)
        ring = ImageDraw.Draw(img)
        rw = max(3, px // 28)
        ring.ellipse([rw // 2, rw // 2, px - rw // 2, px - rw // 2],
                     outline=(255, 255, 255, 240), width=rw)
    elif style == "polaroid":                 # white border + caption strip + tilt
        b = max(6, px // 18)
        card = Image.new("RGBA", (px + 2 * b, px + 2 * b + px // 5), (250, 250, 248, 255))
        card.paste(src, (b, b))
        img = card.rotate(-4, expand=True, resample=Image.BICUBIC)
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).rectangle([6, 8, img.size[0], img.size[1]], fill=(0, 0, 0, 70))
        sh = sh.filter(ImageFilter.GaussianBlur(6))
        img = Image.alpha_composite(sh, img)
    elif style == "bubble":                   # thought bubble containing the image
        pad = px // 5
        W = px + 2 * pad
        img = Image.new("RGBA", (W, W + px // 3), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse([2, 2, W - 2, W - 2], fill=(255, 255, 255, 240),
                  outline=(15, 23, 42, 255), width=max(2, px // 48))
        mask = Image.new("L", (px, px), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, px, px], fill=255)
        img.paste(src, (pad, pad), mask)
        for cx, cy, r in [(0.28, 0.90, 0.045), (0.20, 0.97, 0.03)]:
            d.ellipse([W*(cx-r), (W+px//3)*(cy-r), W*(cx+r), (W+px//3)*(cy+r)],
                      fill=(255, 255, 255, 240), outline=(15, 23, 42, 255),
                      width=max(1, px // 80))
    else:                                     # "rounded" default inset
        img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        mask = Image.new("L", (px, px), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, px, px], radius=px // 8, fill=255)
        img.paste(src, (0, 0), mask)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([1, 1, px - 1, px - 1], radius=px // 8,
                            outline=(255, 255, 255, 220), width=max(2, px // 40))

    img.save(out)
    return out


def _resolve_image(spec: str, characters: list[dict] | None) -> str:
    """'@char:<id>' → that character's portrait path; plain paths pass through."""
    spec = (spec or "").strip()
    if spec.startswith("@char:"):
        cid = spec[6:]
        for c in characters or []:
            if c.get("id") == cid:
                return c.get("ref_path") or ""
        return ""
    return spec


def _xy(pos: str, W: int, H: int, ow: int, oh: int) -> tuple[int, int]:
    m = int(W * _MARGIN)
    xs = {"l": m, "c": (W - ow) // 2, "r": W - ow - m}
    ys = {"t": int(H * 0.06), "m": (H - oh) // 2, "b": H - oh - int(H * 0.16)}
    p = (pos or "tr").lower()
    return xs.get(p[1] if len(p) > 1 else "r", xs["r"]), ys.get(p[0], ys["t"])


def composite_clip(clip_path: str, overlays: list[dict], out_path: str, *,
                   run_dir: str, characters: list[dict] | None = None,
                   duration: float = 0.0) -> str:
    """Composite ≤2 styled overlays onto ONE clip (clip-local timing). Returns
    out_path on success; raises on failure (caller degrades to the plain clip)."""
    od = _prep_dir(run_dir)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
         "-select_streams", "v:0", "-of", "csv=p=0", clip_path],
        capture_output=True, text=True, timeout=30)
    W, H = (int(x) for x in probe.stdout.strip().split(",")[:2])
    dur = duration
    if not dur:
        p2 = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                             "-of", "csv=p=0", clip_path], capture_output=True, text=True, timeout=30)
        dur = float(p2.stdout.strip() or 5.0)

    inputs = ["-i", clip_path]
    chains, last = [], "0:v"
    for i, ov in enumerate(overlays[:2]):
        img = _resolve_image(ov.get("image", ""), characters)
        px = max(64, int(W * _SIZES.get(ov.get("size", "m"), 0.26)))
        tile = style_overlay(img, ov.get("style") or ("sticker" if not img else "rounded"), px, od)
        inputs += ["-i", tile]
        from PIL import Image as _I
        with _I.open(tile) as t:
            ow, oh = t.size
        x, y = _xy(ov.get("pos", "tr"), W, H, ow, oh)
        when = ov.get("when", "all")
        en = ""
        if when == "first-half":
            en = f":enable='lt(t,{dur/2:.2f})'"
        elif when == "second-half":
            en = f":enable='gte(t,{dur/2:.2f})'"
        nxt = f"v{i+1}"
        chains.append(f"[{last}][{i+1}:v]overlay={x}:{y}{en}[{nxt}]")
        last = nxt
    cmd = ["ffmpeg", "-y", "-v", "error", *inputs,
           "-filter_complex", ";".join(chains),
           "-map", f"[{last}]"]
    # keep any audio (lipsync clips carry it)
    cmd += ["-map", "0:a?", "-c:a", "copy",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "18",
            out_path]
    subprocess.run(cmd, check=True, timeout=300)
    if not (os.path.exists(out_path) and os.path.getsize(out_path) > 10_000):
        raise RuntimeError("composite produced no output")
    return out_path


def overlays_cache_key(clip_path: str, overlays: list[dict], characters: list[dict] | None) -> str:
    """Cache key for a composited clip: base clip content + resolved overlay spec.
    Changing an overlay re-composites; the base clip cache is untouched (plan A3.2)."""
    try:
        st = os.stat(clip_path)
        base = f"{clip_path}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        base = clip_path
    spec = json.dumps([{**o, "image": _resolve_image(o.get("image", ""), characters)}
                       for o in overlays[:2]], sort_keys=True)
    return _hash(base, spec)
