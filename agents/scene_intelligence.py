"""
Scene Intelligence: converts story beats into cinematic visual + motion prompts.
GPT-4.1 understands the EMOTION behind each story beat and designs the scene.

Optimisations:
- Disk cache: results stored in ~/.hob_cache/scene_designs/ by MD5 of inputs.
  Same story re-renders without any GPT calls.
- Parallel: all frames designed concurrently (I/O-bound → threads ideal).
  10 serial calls (~25s) → ~3s wall-clock.
"""
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from agents import llm

SCENE_CACHE_DIR = Path.home() / ".hob_cache" / "scene_designs"
SCENE_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_key(caption: str, director_note: str, visual_type: str,
               subject_name: str, subject_description: str, has_real_photo: bool) -> str:
    h = hashlib.md5()
    for part in [caption, director_note, visual_type, subject_name,
                 subject_description, str(has_real_photo)]:
        h.update(part.encode())
    return h.hexdigest()


def _cache_load(key: str) -> dict | None:
    p = SCENE_CACHE_DIR / f"{key}.json"
    if p.exists():
        try:
            with open(p) as f:
                return json.load(f)
        except Exception:
            pass
    return None


def _cache_save(key: str, scene: dict):
    with open(SCENE_CACHE_DIR / f"{key}.json", "w") as f:
        json.dump(scene, f)


_SHARED_IMAGE_RULES = """
Image prompt rules (for gpt-image-2 generation):
- 60-90 words, ultra-specific on lighting, color, texture, lens
- State "photorealistic, cinematic, 9:16 vertical frame" in every prompt
- Specify lens/depth: "85mm portrait lens, f/1.8 bokeh", "wide 24mm", "macro"
- Specify light source: "golden hour side-light", "single tungsten lamp", "overcast diffused light"
- Specify color palette: "warm amber tones", "desaturated blue-grey", "deep reds and saffron"
- NO text, NO watermarks, NO logos in the scene
"""

SYSTEM_PROMPT = f"""You are a cinematic director for an Indian storytelling platform (like Humans of Bombay).
Your job: read a story beat and design ONE cinematic shot that EMOTIONALLY represents it.

Filmmaking rules:
- Think like a filmmaker, not a photographer. What shot tells this EMOTION, not this action?
- Use visual metaphors: "I was alone" = person motionless on a packed Assam bus stand, everyone rushing past, shallow focus.
- Ground every scene in real India: bamboo homes, auto stands, chawls, Assam tea estates, government school classrooms, Guwahati streets.
- Subject: Assamese Indian woman, late 20s, natural features — not glamorous.
- For real photos (motion only): design how the camera moves across the existing photo.
{_SHARED_IMAGE_RULES}
Respond in this EXACT JSON:
{{
  "emotion": "one word",
  "scene_description": "2-3 sentences — what is in frame, subject posture, setting, light",
  "image_prompt": "60-90 word gpt-image-2 prompt, photorealistic cinematic 9:16 vertical",
  "motion_prompt": "10-15 words for Kling v3 camera movement",
  "camera_angle": "low angle / eye level / over-shoulder / extreme close-up / wide establishing"
}}"""


CONTEXTUAL_SYSTEM_PROMPT = f"""You are a cinematic director for an Indian storytelling platform (like Humans of Bombay).
Your job: design an AGE-ACCURATE, ERA-ACCURATE portrait that EMOTIONALLY represents this story moment.

Age and era rules:
- Infer exact age: "8th grade" = 13 years old, "12th grade" = 17, "college" = 19-21, "teaching kids" = 17.
- Era: early 2000s India — no smartphones, no earphones, cotton salwar or school uniform, simple bindi.
- Location: Assam/Northeast India — bamboo, corrugated iron roofs, handloom textures, red soil.
- Body language conveys emotion — don't just put a person in a setting, show how they FEEL.
- The face should look innocent, determined, or vulnerable — matching the story beat.
{_SHARED_IMAGE_RULES}
Respond in this EXACT JSON:
{{
  "emotion": "one word",
  "scene_description": "2-3 sentences — subject age, exact setting, body language, light quality",
  "image_prompt": "60-90 word gpt-image-2 prompt — MUST state age explicitly (e.g. '13-year-old Indian girl'), era details, photorealistic cinematic 9:16 vertical",
  "motion_prompt": "10-15 words for Kling v3 camera movement",
  "camera_angle": "low angle / eye level / over-shoulder / extreme close-up / wide establishing"
}}"""


SYMBOLIC_SYSTEM_PROMPT = f"""You are a cinematic director for an Indian storytelling platform (like Humans of Bombay).
Your job: design a SYMBOLIC / METAPHORICAL still — objects, textures, environments ONLY. Absolutely no people or faces.

Symbolic composition rules:
- ZERO people, ZERO faces, ZERO hands (unless disembodied hands as a deliberate metaphor, and only if essential).
- One or two HERO OBJECTS that carry the entire emotional weight of the story beat.
- Examples: crumpled newspaper audition ad on a concrete floor, ₹500 notes fanned out under a kerosene lamp, a worn textbook with someone else's name crossed out on the cover, chalk dust settling on a dark slate board, an auto's side mirror reflecting an empty road at dusk.
- Objects must SUGGEST the emotion — never literally illustrate the caption.
- Ground in India: handloom fabric, red oxide floors, banana leaf, jute, iron trunk, government school items.
- Light is the emotion: kerosene warmth = struggle, morning blue-grey = uncertainty, golden hour = hope.
{_SHARED_IMAGE_RULES}
Respond in this EXACT JSON:
{{
  "emotion": "one word",
  "scene_description": "2-3 sentences — which objects, how arranged, light source, color palette — NO people mentioned",
  "image_prompt": "60-90 word gpt-image-2 prompt — begin with 'No people, no faces.' — then objects, setting, light, photorealistic cinematic 9:16 vertical",
  "motion_prompt": "10-15 words for Kling v3 — slow drift, macro pull-back, dust particles, etc.",
  "camera_angle": "macro close-up / low angle / eye level / wide establishing"
}}"""


def design_scene(story_beat: str, subject_name: str = "Surabhi",
                 has_real_photo: bool = False, director_note: str = "",
                 visual_type: str = "portrait", subject_description: str = "") -> dict:
    """
    Given a story beat, return cinematic visual + motion design.
    Results are cached to disk — identical inputs return instantly on re-runs.
    """
    # ── Cache lookup ──────────────────────────────────────────────────────────
    key    = _cache_key(story_beat, director_note, visual_type,
                        subject_name, subject_description, has_real_photo)
    cached = _cache_load(key)
    if cached:
        return cached

    # ── Build prompt ──────────────────────────────────────────────────────────
    if visual_type == "symbolic":
        system_prompt = SYMBOLIC_SYSTEM_PROMPT
    elif visual_type == "contextual":
        system_prompt = CONTEXTUAL_SYSTEM_PROMPT
    else:
        system_prompt = SYSTEM_PROMPT

    # Optional: bias scene design toward the lab's hand-made house style.
    from agents import style_exemplars
    if style_exemplars.enabled():
        extra = "\n\n".join(x for x in (style_exemplars.style_preamble(),
                                        style_exemplars.scene_examples()) if x)
        if extra:
            system_prompt = system_prompt + "\n\n" + extra

    subj = subject_name
    if subject_description:
        subj += f" — {subject_description}"
    user_msg = f'Story beat: "{story_beat}"\nSubject: {subj}.\n'
    if director_note:
        user_msg += f'\nDirector note: {director_note}\n'
    if has_real_photo:
        user_msg += "A real photo exists. Only design the MOTION (how it should move/animate). Skip image_prompt details."

    # ── LLM call (provider-pluggable; default OpenAI gpt-4.1) ──────────────────
    try:
        text = llm.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_msg},
            ],
            json_mode=True, temperature=0.8, model_tier="reasoning",
        )
        result = llm.json_loads_lenient(text)
        _cache_save(key, result)
        return result

    except Exception as e:
        print(f"[SceneIntelligence] Error: {e} — using fallback")
        fallback_prompt = (
            f"Cinematic symbolic objects, no people, {story_beat[:60]}, "
            "warm natural lighting, shallow depth of field, photorealistic, vertical 9:16"
            if visual_type == "symbolic" else
            f"Cinematic portrait of a young Assamese Indian woman, {story_beat[:60]}, "
            "warm natural lighting, shallow depth of field, photorealistic, vertical 9:16"
        )
        return {
            "emotion":          "reflective",
            "scene_description": "Cinematic shot.",
            "image_prompt":     fallback_prompt,
            "motion_prompt":    "Slow gentle push-in, subtle ambient movement",
            "camera_angle":     "eye level",
        }


def design_all_scenes(frames: list[dict], subject_name: str = "Surabhi",
                      subject_description: str = "") -> list[dict]:
    """
    Add scene intelligence to all frames in parallel (ThreadPoolExecutor).
    10 serial calls (~25s) → ~3s wall-clock. Results cached to disk.
    """
    print(f"[SceneIntelligence] Designing {len(frames)} scenes for {subject_name} (parallel)…")

    def _process(f: dict):
        caption    = f.get("caption", "").strip()
        note       = f.get("director_note", "")
        photo_spec = f.get("photo_spec", "")

        if not caption:
            return f, {
                "emotion":      "silence",
                "motion_prompt": "Very slow zoom out, still, contemplative",
                "camera_angle": "eye level",
                "image_prompt": "",
            }

        has_photo = (
            os.path.exists(f.get("visual_path", ""))
            or (photo_spec and not photo_spec.startswith("ai_"))
        )
        visual_type = (
            "symbolic"    if photo_spec == "ai_symbolic"  else
            "contextual"  if photo_spec == "ai_portrait"  else
            "portrait"
        )

        scene = design_scene(caption, subject_name,
                             has_real_photo=has_photo,
                             director_note=note,
                             visual_type=visual_type,
                             subject_description=subject_description)
        return f, scene

    with ThreadPoolExecutor(max_workers=min(len(frames), 10)) as pool:
        futures = {pool.submit(_process, f): f for f in frames}
        for future in as_completed(futures):
            f, scene = future.result()
            f["scene"] = scene
            cached_tag = "[cached]" if _cache_load(
                _cache_key(f.get("caption",""), f.get("director_note",""),
                           "symbolic" if f.get("photo_spec")=="ai_symbolic" else
                           "contextual" if f.get("photo_spec")=="ai_portrait" else "portrait",
                           subject_name, subject_description,
                           os.path.exists(f.get("visual_path","")) or
                           bool(f.get("photo_spec","") and not f.get("photo_spec","").startswith("ai_")))
            ) else ""
            print(f"  {f['frame_id']} [{scene.get('emotion','?')}] {cached_tag} → {scene.get('motion_prompt','?')}")

    # Restore original order (futures complete out of order)
    return frames
