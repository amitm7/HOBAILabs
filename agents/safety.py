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
        from agents import degradation
        degradation.report("safety", "warn", f"Gate A moderation skipped ({str(e)[:80]})")


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


def face_count(image_path: str) -> int:
    """Number of frontal faces in an image — best-effort. Returns the count, or -1 if
    detection couldn't run (no cv2 / unreadable). Used to keep the ambient re-create
    OFF real people: a non-person shot whose reference contains a face is NOT ambient,
    and recreating it would synthesize a likeness (an authenticity violation)."""
    try:
        import cv2
        img = cv2.imread(image_path)
        if img is None:
            return -1
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(40, 40))
        return len(faces)
    except Exception:
        return -1


def has_person(image_path: str) -> bool:
    """Reliable 'is a real human person/face a subject here?' check via the vision LLM
    (fast tier) — the moat guard for ambient re-create, where the cheap Haar cascade
    misses angled/partial faces (it did, in testing). Returns True to BLOCK when a
    person is seen; False on a clear 'no' OR any failure (the caller still has the
    `uses_talent` + Haar guards as backup)."""
    try:
        from agents import llm
        schema = {"name": "person_check", "schema": {
            "type": "object", "additionalProperties": False,
            "properties": {"has_person": {"type": "boolean"}}, "required": ["has_person"]}}
        text = llm.chat(
            [{"role": "user", "content": [
                {"type": "text", "text": "Does this image show a recognizable real HUMAN "
                 "PERSON or face as a subject — even partially, at any angle? Strict JSON."},
                {"type": "image", "path": image_path}]}],
            json_mode=True, json_schema=schema, max_tokens=50, model_tier="fast")
        return bool(llm.json_loads_lenient(text).get("has_person"))
    except Exception as e:
        print(f"[Safety] has_person vision check unavailable ({e}) — relying on other guards")
        return False


def check_face_sanity(image_path: str, frame_id: str,
                      orientation: str = "portrait") -> bool:
    """
    Gate B: Validate a generated image before it enters the clip-build step.
    Returns True if the image passes; False means regenerate.

    Checks (in order):
      1. File exists and size > 10 KB (not empty/corrupt)
      2. PIL can open it and dimensions MATCH the requested orientation —
         portrait expects h>w, landscape expects w>h, square skips the check.
         (Was hardcoded portrait: a deliberate 16:9 reel had every landscape
         still rejected 3x before being grudgingly accepted — found live in the
         Yamraj A/B test, 2026-07-19.)
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
        if orientation == "portrait" and w > h:
            print(f"[Safety] Gate B: {frame_id} — landscape orientation ({w}×{h}), expected portrait.")
            return False
        if orientation == "landscape" and h > w:
            print(f"[Safety] Gate B: {frame_id} — portrait orientation ({w}×{h}), expected landscape.")
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


_CRITIQUE_PROMPT = (
    "You are a strict QC reviewer for a photorealistic Indian documentary reel "
    "set in the specified era. This image was generated from the prompt below. "
    "Check ONLY for these failure modes:\n"
    "1. ANACHRONISMS vs the era in the prompt (smartphones, earbuds, modern "
    "cars/logos/clothing in a period scene)\n"
    "2. Deformed anatomy: malformed hands/fingers, warped faces, extra/missing limbs\n"
    "3. Any rendered text, watermark, or logo in the image\n"
    "4. GROSS prompt mismatch: clearly wrong age for an age-specific prompt, "
    "people present when the prompt demands none, or a blank/abstract/empty "
    "image when the prompt requires a real subject or scene\n"
    "Minor stylistic deviations are fine — only flag real failures.\n\n"
    'GENERATION PROMPT:\n"{prompt}"\n\n'
    'Reply with ONLY compact one-line JSON, reason ≤10 words and no inner quotes '
    'or newlines: {{"ok": true|false, "reason": "short reason when not ok"}}'
)


def critique_image(image_path: str, frame_id: str, prompt: str) -> bool:
    """
    Gate B2: vision-LLM critique on the cheap 'fast' tier — catches what the
    OpenCV check can't (anachronisms, wrong age, malformed hands, baked-in text).
    Returns True (pass) on any API failure so the gate degrades, never blocks.
    Disable with HOB_VISION_QC=0.
    """
    if os.environ.get("HOB_VISION_QC", "1") == "0":
        return True
    try:
        from agents import llm
        text = llm.chat(
            [{"role": "user", "content": [
                {"type": "text", "text": _CRITIQUE_PROMPT.format(prompt=prompt[:600])},
                {"type": "image", "path": image_path},
            ]}],
            json_mode=True, max_tokens=256, model_tier="fast",
        )
        verdict = llm.json_loads_lenient(text)
        ok = bool(verdict.get("ok", True))
        if not ok:
            print(f"[Safety] Gate B2: {frame_id} — vision QC failed: "
                  f"{verdict.get('reason', 'unspecified')}")
        else:
            print(f"[Safety] Gate B2: {frame_id} — vision QC passed. ✓")
        return ok
    except Exception as e:
        print(f"[Safety] Gate B2: vision QC unavailable ({e}) — skipping.")
        from agents import degradation
        degradation.report("safety", "warn", f"Gate B2 vision QC skipped on {frame_id} ({str(e)[:80]})")
        return True


def critique_brand(image_path: str, frame_id: str, brand: dict) -> bool:
    """
    Brand-safety gate for GENERATED frames in a branded ad: catch the things that
    embarrass a brand. Product/logo shots are real-only (never generated), so
    this guards the AI stills around them. Returns True (pass) on any API failure
    so it degrades, never blocks. Disable with HOB_VISION_QC=0.
    """
    if os.environ.get("HOB_VISION_QC", "1") == "0" or not brand:
        return True
    brand_name = (brand.get("name") or "the advertised brand").strip()
    product    = (brand.get("product") or "").strip()
    try:
        from agents import llm
        rules = (
            f"This frame is part of a paid ad for {brand_name}"
            + (f" ({product})" if product else "") + ". Flag ONLY:\n"
            "1. A visible COMPETITOR brand/logo/product.\n"
            "2. A fabricated or inaccurate depiction of the advertised product "
            "(it should NOT be invented here — real product shots are separate).\n"
            "3. Any rendered text, claim, price, or logo baked into the image "
            "(ad copy is added separately and must be brand-supplied).\n"
            'Reply ONLY as JSON: {"ok": true|false, "reason": "short reason if not ok"}'
        )
        text = llm.chat(
            [{"role": "user", "content": [
                {"type": "text", "text": rules},
                {"type": "image", "path": image_path},
            ]}],
            json_mode=True, max_tokens=256, model_tier="fast",
        )
        verdict = llm.json_loads_lenient(text)
        ok = bool(verdict.get("ok", True))
        if not ok:
            print(f"[Safety] Brand gate: {frame_id} — {verdict.get('reason','flagged')}")
        return ok
    except Exception as e:
        print(f"[Safety] Brand gate: unavailable ({e}) — skipping.")
        return True
