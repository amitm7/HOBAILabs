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
               subject_name: str, subject_description: str, has_real_photo: bool,
               treatment_note: str = "") -> str:
    h = hashlib.md5()
    for part in [caption, director_note, visual_type, subject_name,
                 subject_description, str(has_real_photo), treatment_note]:
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
- Ground every scene in authentic, specific real-world detail. Infer the setting, era, and region FROM THE STORY (a chawl, an auto stand, a tea estate, a classroom — whatever the beat implies); never impose a fixed place.
- Subject: honor the gender, age, and description given in the user message exactly (a quoted child = a child, a father = a man). If no subject is given, INFER who is on screen — their age and gender — from the story beat itself. Never fall back to a fixed stock character.
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
- Infer exact age from the story: "8th grade" = 13 years old, "12th grade" = 17, "college" = 19-21.
- Infer the ERA from the story (clothing, technology, props must match it) — do not assume a fixed decade.
- Infer the LOCATION/region from the story and ground it in authentic local detail — do not impose a fixed place.
- Honor any subject gender/age/description in the user message exactly; if none is given, infer the person from the beat.
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
- Ground objects in the story's own world and era (infer the region/period from the beat) with authentic, specific, lived-in detail.
- Light is the emotion: warm lamplight = struggle, morning blue-grey = uncertainty, golden hour = hope.
{_SHARED_IMAGE_RULES}
Respond in this EXACT JSON:
{{
  "emotion": "one word",
  "scene_description": "2-3 sentences — which objects, how arranged, light source, color palette — NO people mentioned",
  "image_prompt": "60-90 word gpt-image-2 prompt — begin with 'No people, no faces.' — then objects, setting, light, photorealistic cinematic 9:16 vertical",
  "motion_prompt": "10-15 words for Kling v3 — slow drift, macro pull-back, dust particles, etc.",
  "camera_angle": "macro close-up / low angle / eye level / wide establishing"
}}"""


# Strict response schemas — enforced on OpenAI structured outputs, injected as a
# system directive on Bedrock/Gemini. Eliminates truncated/malformed scene JSON.
_SCENE_SCHEMA = {"name": "scene", "schema": {
    "type": "object", "additionalProperties": False,
    "properties": {
        "emotion":           {"type": "string"},
        "scene_description": {"type": "string"},
        "image_prompt":      {"type": "string"},
        "motion_prompt":     {"type": "string"},
        "camera_angle":      {"type": "string"},
    },
    "required": ["emotion", "scene_description", "image_prompt",
                 "motion_prompt", "camera_angle"],
}}

_TREATMENT_SCHEMA = {"name": "treatment", "schema": {
    "type": "object", "additionalProperties": False,
    "properties": {
        "arc":   {"type": "string"},
        "motif": {"type": "string"},
        "frames": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "frame_id":  {"type": "string"},
                "act":       {"type": "string"},
                "palette":   {"type": "string"},
                "shot_size": {"type": "string"},
                "note":      {"type": "string"},
            },
            "required": ["frame_id", "act", "palette", "shot_size", "note"],
        }},
    },
    "required": ["arc", "motif", "frames"],
}}

TREATMENT_SYSTEM_PROMPT = """You are the director planning an ENTIRE Instagram reel BEFORE any single shot is designed.
You receive every story beat in order. Produce a film treatment that makes the shots cut together as ONE film, not a slideshow:
- A three-act emotional arc with a PALETTE PROGRESSION across the reel (e.g. cold blue-grey struggle → neutral overcast turning point → warm golden triumph).
- ONE recurring visual MOTIF (an object, texture, or light quality) that should reappear in 2-3 frames to thread the story.
- A per-frame shot plan that VARIES shot size and angle — never two identical consecutive setups; alternate wide/medium/close rhythm like an editor would.
Keep every choice grounded in real India (the platform's house style). Respond with JSON only:
{"arc": "...", "motif": "...", "frames": [{"frame_id": "f01", "act": "setup|struggle|turning|triumph", "palette": "...", "shot_size": "wide establishing|medium|close-up|extreme close-up|detail insert", "note": "8-15 words of direction for this beat"}]}"""


def design_treatment(frames: list[dict], subject_name: str = "",
                     subject_description: str = "", mood: str = "",
                     extra_context: str = "") -> dict | None:
    """
    ONE LLM call over ALL beats → a whole-reel plan (arc, palette progression,
    motif, per-frame shot plan). Cached by the full beat list. Returns
    {"arc", "motif", "by_id": {frame_id: plan}} or None (degrades gracefully —
    per-frame design then behaves exactly as before).
    """
    beats = [(f["frame_id"], (f.get("caption") or "").strip()) for f in frames]
    if sum(1 for _, c in beats if c) < 3:
        return None   # too short for an arc — not worth a call

    h = hashlib.md5()
    for fid, cap in beats:
        h.update(f"{fid}|{cap}".encode())
    h.update(f"{subject_name}|{subject_description}|{mood}|{extra_context}".encode())
    cache_path = SCENE_CACHE_DIR / f"treatment_{h.hexdigest()}.json"
    if cache_path.exists():
        try:
            with open(cache_path) as f:
                return json.load(f)
        except Exception:
            pass

    beat_list = "\n".join(f"{fid}: {cap or '(silent frame — visual only)'}"
                          for fid, cap in beats)
    if subject_name or subject_description:
        user_msg = "Subject: " + " — ".join(x for x in (subject_name, subject_description) if x)
    else:
        user_msg = "No subject given — infer the people and world from the story itself."
    if mood:
        user_msg += f"\nRequested overall mood: {mood}"
    if extra_context:
        user_msg += f"\n\n{extra_context}"
    user_msg += f"\n\nStory beats in order:\n{beat_list}"

    try:
        text = llm.chat(
            [{"role": "system", "content": TREATMENT_SYSTEM_PROMPT},
             {"role": "user",   "content": user_msg}],
            json_mode=True, json_schema=_TREATMENT_SCHEMA,
            temperature=0.7, max_tokens=3000, model_tier="reasoning",
        )
        raw = llm.json_loads_lenient(text)
        treatment = {
            "arc":   raw.get("arc", ""),
            "motif": raw.get("motif", ""),
            "by_id": {p["frame_id"]: p for p in raw.get("frames", [])
                      if isinstance(p, dict) and p.get("frame_id")},
        }
        if not treatment["by_id"]:
            return None
        with open(cache_path, "w") as f:
            json.dump(treatment, f)
        print(f"[SceneIntelligence] Treatment: {treatment['arc'][:90]}")
        return treatment
    except Exception as e:
        print(f"[SceneIntelligence] Treatment pass failed ({e}) — per-frame design only")
        return None


def _treatment_note(treatment: dict | None, frame_id: str,
                    prev_plan: dict | None) -> str:
    """Render the per-frame slice of the treatment for the design prompt."""
    if not treatment:
        return ""
    plan = treatment["by_id"].get(frame_id)
    if not plan:
        return ""
    lines = ["WHOLE-REEL TREATMENT — follow it so the shots cut together as one film:",
             f"Arc: {treatment['arc']}",
             f"Recurring motif: {treatment['motif']}",
             f"This frame: act={plan.get('act','')}; palette={plan.get('palette','')}; "
             f"shot size={plan.get('shot_size','')}. {plan.get('note','')}"]
    if prev_plan:
        lines.append(f"The PREVIOUS frame is a {prev_plan.get('shot_size','')} shot — "
                     "vary size/angle from it.")
    return "\n".join(lines)


def design_scene(story_beat: str, subject_name: str = "",
                 has_real_photo: bool = False, director_note: str = "",
                 visual_type: str = "portrait", subject_description: str = "",
                 treatment_note: str = "", extra_context: str = "") -> dict:
    """
    Given a story beat, return cinematic visual + motion design.
    Results are cached to disk — identical inputs return instantly on re-runs.
    treatment_note: optional whole-reel plan slice (see design_treatment).
    extra_context: optional brand/campaign framing context (BRAND_PLAN).
    """
    # ── Cache lookup ──────────────────────────────────────────────────────────
    key    = _cache_key(story_beat, director_note, visual_type,
                        subject_name, subject_description, has_real_photo,
                        treatment_note + "|" + extra_context)
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
        subj = f"{subj} — {subject_description}" if subj else subject_description
    user_msg = f'Story beat: "{story_beat}"\n'
    if subj.strip():
        user_msg += f'Subject: {subj}.\n'
    else:
        user_msg += 'No subject given — infer who is on screen (age, gender, look) from the beat itself.\n'
    if director_note:
        user_msg += f'\nDirector note: {director_note}\n'
    if treatment_note:
        user_msg += f'\n{treatment_note}\n'
    if extra_context:
        user_msg += f'\n{extra_context}\n'
    if has_real_photo:
        user_msg += "A real photo exists. Only design the MOTION (how it should move/animate). Skip image_prompt details."

    # ── LLM call (provider-pluggable; default OpenAI gpt-4.1) ──────────────────
    try:
        text = llm.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_msg},
            ],
            json_mode=True, json_schema=_SCENE_SCHEMA,
            temperature=0.8, model_tier="reasoning",
        )
        result = llm.json_loads_lenient(text)
        _cache_save(key, result)
        return result

    except Exception as e:
        print(f"[SceneIntelligence] Error: {e} — using fallback")
        # No fixed stock subject — use the operator's description if given, else
        # let the beat imply who/what is on screen.
        who = (subject_description or subject_name or "the person in this story moment").strip()
        fallback_prompt = (
            f"Cinematic symbolic objects, no people, {story_beat[:60]}, "
            "warm natural lighting, shallow depth of field, photorealistic, vertical 9:16"
            if visual_type == "symbolic" else
            f"Cinematic portrait of {who}, {story_beat[:60]}, "
            "warm natural lighting, shallow depth of field, photorealistic, vertical 9:16"
        )
        return {
            "emotion":          "reflective",
            "scene_description": "Cinematic shot.",
            "image_prompt":     fallback_prompt,
            "motion_prompt":    "Slow gentle push-in, subtle ambient movement",
            "camera_angle":     "eye level",
        }


def design_all_scenes(frames: list[dict], subject_name: str = "",
                      subject_description: str = "", mood: str = "",
                      extra_context: str = "") -> list[dict]:
    """
    Add scene intelligence to all frames in parallel (ThreadPoolExecutor).
    A whole-reel TREATMENT pass runs first (one call, cached) so per-frame
    designs share an arc, palette progression, motif, and shot-size rhythm
    instead of being designed in isolation. 10 serial calls (~25s) → ~3s
    wall-clock. Results cached to disk.
    """
    if not frames:
        return frames
    print(f"[SceneIntelligence] Designing {len(frames)} scenes"
          f"{f' for {subject_name}' if subject_name else ''} (parallel)…")

    treatment = design_treatment(frames, subject_name, subject_description, mood, extra_context)
    # Previous CAPTIONED frame's plan per frame — for shot-variety pressure.
    prev_plans: dict[str, dict | None] = {}
    last_plan = None
    for f in frames:
        prev_plans[f["frame_id"]] = last_plan
        if treatment and (f.get("caption") or "").strip():
            last_plan = treatment["by_id"].get(f["frame_id"]) or last_plan

    def _process(f: dict):
        caption    = f.get("caption", "").strip()
        note       = f.get("director_note", "")
        photo_spec = f.get("photo_spec", "")

        if not caption:
            # A silent beat (no dialogue/VO) still has a SCENE — director_note is
            # exactly that, and was being silently discarded here: image_prompt was
            # always "", so the storyboard sketch and (via the same empty prompt)
            # the real render had nothing to draw and produced unrelated/generic
            # content. Free (no LLM call, unlike a captioned beat's design_scene
            # pass) — director_note is already full prose the image model can use
            # directly. Genuinely contentless beats (no caption, no note either)
            # keep the previous fully-generic behaviour.
            note = note.strip()
            return f, {
                "emotion":      "silence",
                "motion_prompt": "Very slow zoom out, still, contemplative",
                "camera_angle": "eye level",
                "image_prompt": note,
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

        # Subject-aware description: keyed on who's DEPICTED (visual_subject_id),
        # not who's speaking — a quoted speaker (kid, father) is depicted as THAT
        # person, and so is a third-person protagonist the narrator describes but
        # who rarely speaks (e.g. a mythological character). visual_subject_id
        # defaults to speaker_id, so first-person stories are unaffected.
        spk_name, spk_desc = subject_name, subject_description
        visual_subject_id = f.get("visual_subject_id") or f.get("speaker_id", "narrator")
        if visual_subject_id != "narrator":
            from agents import cast as cast_mod
            descriptor = cast_mod.subject_descriptor(f, subject_description)
            spk_name = f.get("visual_subject_label") or f.get("speaker_label") or "speaker"
            spk_desc = (f"ON SCREEN: {descriptor}. Depict THIS person — their stated "
                        "gender and age — consistently, NOT the narrator.")

        scene = design_scene(caption, spk_name,
                             has_real_photo=has_photo,
                             director_note=note,
                             visual_type=visual_type,
                             subject_description=spk_desc,
                             treatment_note=_treatment_note(
                                 treatment, f["frame_id"], prev_plans[f["frame_id"]]),
                             extra_context=extra_context)
        return f, scene

    with ThreadPoolExecutor(max_workers=min(len(frames), 10)) as pool:
        futures = {pool.submit(_process, f): f for f in frames}
        for future in as_completed(futures):
            f, scene = future.result()
            f["scene"] = scene
            print(f"  {f['frame_id']} [{scene.get('emotion','?')}] → {scene.get('motion_prompt','?')}")

    # Restore original order (futures complete out of order)
    return frames


# ── Vision-grounded motion (runs AFTER stills exist) ──────────────────────────
# The motion prompt above is designed before the image is generated, so it can
# reference things that never made it into the frame — the documented cause of
# image-to-video drift. This pass LOOKS at the final still and rewrites the
# motion prompt for what is actually visible. Cheap (fast tier) and cached.

_IMAGE_EXTS_MG = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

_MOTION_GROUND_PROMPT = (
    "This exact image will be animated into a {dur:.0f}-second video clip.\n"
    'Planned camera direction: "{motion}". Emotion of the beat: {emotion}.\n'
    "Write the FINAL image-to-video motion prompt, grounded in what is actually "
    "visible in THIS image: 10-18 words, ONE clear camera move (keep the planned "
    "move unless it fights the composition), plus ambient motion only for "
    "elements present (hair, fabric, steam, rain, foliage, dust, flame, light). "
    "Do NOT describe the subject's appearance, clothing, lighting or colors — "
    "the image already carries those. Reply with the prompt text only."
)


def ground_motion_prompt(image_path: str, motion: str, emotion: str = "",
                         duration: float = 5.0) -> str:
    """Refine a motion prompt by looking at the final still. Cached by
    image-content + planned-motion hash. Returns the original on any failure."""
    try:
        h = hashlib.md5()
        with open(image_path, "rb") as f:
            h.update(f.read())
        h.update(motion.encode())
        cache_path = SCENE_CACHE_DIR / f"motion_{h.hexdigest()}.txt"
        if cache_path.exists():
            return cache_path.read_text().strip() or motion

        text = llm.chat(
            [{"role": "user", "content": [
                {"type": "text", "text": _MOTION_GROUND_PROMPT.format(
                    dur=duration, motion=motion or "slow gentle push-in",
                    emotion=emotion or "neutral")},
                {"type": "image", "path": image_path},
            ]}],
            max_tokens=60, model_tier="fast",
        ).strip().strip('"').strip()
        if not (10 <= len(text) <= 200):
            return motion
        cache_path.write_text(text)
        return text
    except Exception as e:
        print(f"[SceneIntelligence] motion grounding failed ({e}) — keeping planned motion")
        return motion


def ground_all_motions(frames: list[dict], max_workers: int = 8) -> list[dict]:
    """Vision-ground the motion prompt of every eligible frame, in place.
    Skips: explicit [camera:]/[motion:] overrides, lip-sync frames, video
    sources, and frames without a generated/real still on disk."""
    targets = [
        f for f in frames
        if not f.get("motion_override")
        and not f.get("lipsync")
        and f.get("visual_path") and os.path.exists(f["visual_path"])
        and os.path.splitext(f["visual_path"])[1].lower() in _IMAGE_EXTS_MG
        and f.get("scene")
    ]
    if not targets:
        return frames
    print(f"[SceneIntelligence] Grounding motion in the actual stills ({len(targets)} frames)…")

    def _one(f):
        scene = f["scene"]
        refined = ground_motion_prompt(
            f["visual_path"], scene.get("motion_prompt", ""),
            emotion=scene.get("emotion", ""), duration=float(f.get("duration", 5.0)))
        if refined != scene.get("motion_prompt"):
            print(f"  {f['frame_id']} motion → {refined}")
        scene["motion_prompt"] = refined

    with ThreadPoolExecutor(max_workers=min(len(targets), max_workers)) as pool:
        list(pool.map(_one, targets))
    return frames
