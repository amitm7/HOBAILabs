"""
Content safety gates for the HOBAILabs pipeline.

Gate A — moderate_script(): OpenAI Moderation API on script text (policy floor).
Gate B — check_face_sanity(): Image validity + face count check after generation (quality gate).

These are intentionally separate concerns:
  Gate A catches policy violations (NSFW text prompts).
  Gate B catches quality failures (deformed faces, corrupt files) — which moderation never sees.
"""
import os
from openai import OpenAI

_openai_client = None


def _get_openai() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI()
    return _openai_client


def moderate_script(script_text: str) -> None:
    """
    Gate A: Run OpenAI content moderation on the script text.
    Raises ValueError if flagged. Logs and continues if the API is unavailable.
    """
    try:
        resp = _get_openai().moderations.create(input=script_text[:4000])
        result = resp.results[0]
        if result.flagged:
            flagged_cats = [k for k, v in result.categories.model_dump().items() if v]
            raise ValueError(f"Script flagged by content policy: {', '.join(flagged_cats)}")
        print("[Safety] Gate A: Script passed content moderation. ✓")
    except ValueError:
        raise
    except Exception as e:
        print(f"[Safety] Gate A: Moderation API unavailable ({e}) — skipping.")


def moderate_frames(frames: list[dict]) -> None:
    """
    Gate A for frame-based entry points: moderate every user-editable text field
    that feeds a generation prompt (caption, director note, edit prompt).
    Raises ValueError if flagged, same as moderate_script.
    """
    text = "\n".join(
        " ".join(filter(None, (f.get("caption", ""), f.get("director_note", ""),
                               f.get("edit_prompt", ""))))
        for f in frames
    ).strip()
    if text:
        moderate_script(text)


def check_face_sanity(image_path: str, frame_id: str) -> bool:
    """
    Gate B: Validate a generated image before it enters the clip-build step.
    Returns True if the image passes; False means regenerate.

    Checks (in order):
      1. File exists and size > 10 KB (not empty/corrupt)
      2. PIL can open it and dimensions are portrait (h > w)
      3. [optional] OpenCV face count: 0–3 faces (>3 = likely deformed generation)

    OpenCV check is silently skipped when cv2 is not installed.
    No-face result is NOT a failure — symbolic frames have no people by design.
    """
    if not os.path.exists(image_path):
        print(f"[Safety] Gate B: {frame_id} — image file missing.")
        return False

    size = os.path.getsize(image_path)
    if size < 10_000:
        print(f"[Safety] Gate B: {frame_id} — file too small ({size} B), likely corrupt.")
        return False

    try:
        from PIL import Image
        img = Image.open(image_path)
        w, h = img.size
        if w == 0 or h == 0:
            print(f"[Safety] Gate B: {frame_id} — zero image dimensions.")
            return False
        if w > h:
            print(f"[Safety] Gate B: {frame_id} — landscape orientation ({w}×{h}), expected portrait.")
            return False
    except Exception as e:
        print(f"[Safety] Gate B: {frame_id} — PIL cannot open image: {e}")
        return False

    try:
        import cv2
        img_cv = cv2.imread(image_path)
        if img_cv is not None:
            gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
            cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
            faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(40, 40))
            count = len(faces)
            if count > 3:
                print(f"[Safety] Gate B: {frame_id} — {count} faces detected (likely deformed). Flagging for regeneration.")
                return False
            label = f"{count} face(s)" if count > 0 else "no faces (symbolic frame)"
            print(f"[Safety] Gate B: {frame_id} — {label}. ✓")
    except ImportError:
        pass  # cv2 not installed — PIL checks above are sufficient for P0

    print(f"[Safety] Gate B: {frame_id} — image passed sanity check. ✓")
    return True
