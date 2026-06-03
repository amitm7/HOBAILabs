import os
import json
from pathlib import Path

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".heif"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".m4v"}
DESCRIPTIONS_CACHE = "assets/descriptions.json"


def load_media_files(assets_dir: str) -> list[str]:
    files = []
    for f in sorted(os.listdir(assets_dir)):
        ext = os.path.splitext(f)[1].lower()
        if ext in IMAGE_EXTS | VIDEO_EXTS:
            files.append(os.path.join(assets_dir, f))
    if not files:
        raise ValueError(f"No media files found in {assets_dir}")
    print(f"[Matcher] Found {len(files)} media files")
    return files


def _gemini_client():
    from google import genai
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"])


def _describe_images_gemini(media_files: list[str]) -> dict:
    """Analyze each image with Gemini Vision. Caches descriptions to disk."""
    from google import genai
    from google.genai import types
    from PIL import Image

    client = _gemini_client()
    cache_path = Path(DESCRIPTIONS_CACHE)
    descriptions = {}

    if cache_path.exists():
        descriptions = json.loads(cache_path.read_text())
        print(f"[Matcher] Loaded {len(descriptions)} cached descriptions")

    image_files = [f for f in media_files if os.path.splitext(f)[1].lower() in IMAGE_EXTS]
    new_files = [f for f in image_files if f not in descriptions]

    if new_files:
        print(f"[Matcher] Analyzing {len(new_files)} images with Gemini Vision...")
        for path in new_files:
            try:
                img = Image.open(path).convert("RGB")
                response = client.models.generate_content(
                    model="gemini-2.0-flash",
                    contents=[
                        img,
                        "Describe this image in 2 sentences. Focus on: "
                        "who is in it, mood/expression, setting, colors, emotional tone."
                    ]
                )
                descriptions[path] = response.text.strip()
                print(f"[Matcher] Described: {os.path.basename(path)}")
            except Exception as e:
                descriptions[path] = os.path.basename(path)
                print(f"[Matcher] Skipped {os.path.basename(path)}: {e}")

        cache_path.parent.mkdir(exist_ok=True)
        cache_path.write_text(json.dumps(descriptions, indent=2))
        print(f"[Matcher] Cached to {DESCRIPTIONS_CACHE}")

    return descriptions


def _match_with_gemini(segment_text: str, descriptions: dict, used: set) -> str:
    """Pick the best unused image for a segment using Gemini text reasoning."""
    client = _gemini_client()

    available = {k: v for k, v in descriptions.items() if k not in used}
    if not available:
        available = descriptions

    desc_list = "\n".join(
        f"{i+1}. [{os.path.basename(k)}]: {v}"
        for i, (k, v) in enumerate(available.items())
    )
    prompt = (
        f"Script segment: \"{segment_text}\"\n\n"
        f"Available images:\n{desc_list}\n\n"
        "Which image number best matches the mood and content of this segment? "
        "Reply with ONLY the number."
    )
    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[prompt]
        )
        idx = int("".join(filter(str.isdigit, response.text.strip()))) - 1
        keys = list(available.keys())
        return keys[max(0, min(idx, len(keys) - 1))]
    except Exception:
        return list(available.keys())[0]


def _match_sequential(audio_segments: list[dict], media_files: list[str]) -> list[dict]:
    assignments = []
    for i, seg in enumerate(audio_segments):
        media = media_files[i % len(media_files)]
        ext = os.path.splitext(media)[1].lower()
        assignments.append({
            "segment_id": seg["segment_id"],
            "audio_path": seg["audio_path"],
            "actual_duration": seg["actual_duration"],
            "media_path": media,
            "media_type": "image" if ext in IMAGE_EXTS else "video",
        })
    return assignments


def match_media(audio_segments: list[dict], media_files: list[str]) -> list[dict]:
    """Match media to segments. Uses Gemini Vision if key present, else sequential."""
    use_gemini = bool(os.environ.get("GEMINI_API_KEY"))

    if not use_gemini:
        print("[Matcher] No GEMINI_API_KEY — using sequential matching")
        assignments = _match_sequential(audio_segments, media_files)
    else:
        print("[Matcher] Using Gemini Vision for smart matching...")
        # Delete stale cache from failed previous run
        cache = Path(DESCRIPTIONS_CACHE)
        if cache.exists():
            cached = json.loads(cache.read_text())
            # If most descriptions are just filenames (failed), re-analyze
            failures = sum(1 for v in cached.values() if not any(c in v for c in [" ", "."]))
            if failures > len(cached) * 0.5:
                print("[Matcher] Stale cache detected — re-analyzing...")
                cache.unlink()

        descriptions = _describe_images_gemini(media_files)
        assignments = []
        used = set()
        for seg in audio_segments:
            best = _match_with_gemini(seg["text"], descriptions, used)
            used.add(best)
            ext = os.path.splitext(best)[1].lower()
            assignments.append({
                "segment_id": seg["segment_id"],
                "audio_path": seg["audio_path"],
                "actual_duration": seg["actual_duration"],
                "media_path": best,
                "media_type": "image" if ext in IMAGE_EXTS else "video",
            })

    for a in assignments:
        print(f"[Matcher] {a['segment_id']} → {os.path.basename(a['media_path'])} ({a['media_type']}, {a['actual_duration']:.1f}s)")

    return assignments
