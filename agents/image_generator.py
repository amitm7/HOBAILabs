"""
AI image generation for the three-tier visual system.

generate_contextual_image: age/era-accurate portrait (ai_portrait frames)
  → Flux 2 Pro via fal.ai (best photorealism for Indian faces/skin texture)

generate_symbolic_image: objects/settings, no people (ai_symbolic frames)
  → gpt-image-2 via OpenAI (best instruction-following for complex object arrangements)

Requires env vars: FAL_API_KEY, OPENAI_API_KEY
"""
import base64
import hashlib
import os
import requests
from openai import OpenAI

from agents import model_router

FAL_API_BASE = "https://fal.run/fal-ai/flux-2-pro"

_openai_client = None


def _get_openai():
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI()
    return _openai_client


# Per-orientation size specs for each backend. The reel's orientation (canvas
# setting, ridden on every frame dict) must reach STILL generation too — stills
# were hardcoded 9:16, so a 16:9 reel got portrait stills that the assembler then
# center-cropped (found live in the Yamraj A/B test, 2026-07-19).
_SIZES = {
    "portrait":  {"fal": "portrait_16_9",  "openai": "1024x1536", "prose": "9:16 vertical"},
    "landscape": {"fal": "landscape_16_9", "openai": "1536x1024", "prose": "16:9 widescreen"},
    "square":    {"fal": "square_hd",      "openai": "1024x1024", "prose": "1:1 square"},
}


def _size(orientation: str, backend: str) -> str:
    return _SIZES.get(orientation or "portrait", _SIZES["portrait"])[backend]


def _flux_generate(prompt: str, out_path: str, orientation: str = "portrait") -> str:
    """Call Flux 2 Pro via fal.ai REST API and save the result to out_path."""
    fal_key = os.environ.get("FAL_API_KEY", "")
    if not fal_key:
        raise RuntimeError("FAL_API_KEY not set — add it to .env")

    resp = requests.post(
        FAL_API_BASE,
        headers={
            "Authorization": f"Key {fal_key}",
            "Content-Type": "application/json",
        },
        json={
            "prompt": prompt,
            "image_size": _size(orientation, "fal"),
            "output_format": "jpeg",
            "safety_tolerance": "4",          # storytelling content, relax safety slightly
            "sync_mode": True,                # wait for result in single call (no polling)
        },
        timeout=120,
    )
    if not resp.ok:
        raise RuntimeError(f"Flux 2 Pro failed {resp.status_code}: {resp.text[:300]}")

    result = resp.json()
    img_url = result["images"][0]["url"]

    # In sync_mode, fal.ai returns the image inline as a base64 data URI
    # (data:image/jpeg;base64,...) instead of an http URL. Handle both.
    if img_url.startswith("data:"):
        header, b64data = img_url.split(",", 1)
        with open(out_path, "wb") as f:
            f.write(base64.b64decode(b64data))
    else:
        img_resp = requests.get(img_url, timeout=60)
        img_resp.raise_for_status()
        with open(out_path, "wb") as f:
            f.write(img_resp.content)
    return out_path


def _openai_generate(prompt: str, out_path: str, orientation: str = "portrait") -> str:
    """Call gpt-image-2 and save the result to out_path."""
    client = _get_openai()
    resp = client.images.generate(
        model="gpt-image-2",
        prompt=prompt,
        size=_size(orientation, "openai"),
        quality="high",
        n=1,
    )
    with open(out_path, "wb") as f:
        f.write(base64.b64decode(resp.data[0].b64_json))
    return out_path


def _fal_image_generate(model_id: str, prompt: str, out_path: str,
                        orientation: str = "portrait") -> str:
    """Generate an image via any fal.ai-hosted model (Seedream, Nano Banana, …)."""
    from agents import fal_client
    endpoint = model_router.model_field(model_id, "fal_endpoint")
    if not endpoint:
        raise RuntimeError(f"no fal_endpoint configured for image model '{model_id}'")
    result = fal_client.run_sync(endpoint, {
        "prompt":        prompt,
        "image_size":    _size(orientation, "fal"),
        "num_images":    1,
        "output_format": "jpeg",
        "sync_mode":     True,
    })
    url = fal_client.extract_media_url(result, keys=("images", "image"))
    if not url:
        raise RuntimeError(f"fal image model '{model_id}' returned no image: {str(result)[:200]}")
    return fal_client.download_media(url, out_path)


def _generate_with_model(model_id: str, prompt: str, out_path: str,
                         orientation: str = "portrait") -> str:
    """Dispatch image generation to the backend named in config/models.json."""
    backend = model_router.model_field(model_id, "backend")
    if backend == "flux":
        return _flux_generate(prompt, out_path, orientation)
    if backend == "openai":
        return _openai_generate(prompt, out_path, orientation)
    if backend == "fal":
        return _fal_image_generate(model_id, prompt, out_path, orientation)
    raise RuntimeError(f"unknown image backend '{backend}' for model '{model_id}'")


def _generate_image(model_id: str, prompt: str, out_path: str, fallback: str,
                    orientation: str = "portrait") -> str:
    """
    Try the chosen model, then its full configured cross-vendor fallback chain
    (Gap #8), so a single flaky/over-quota provider never breaks a render. The
    chain comes from config/models.json `fallbacks`; `fallback` is appended as a
    final known-reliable backstop for back-compat with existing call sites.
    """
    chosen = model_id or fallback
    chain = [chosen] + model_router.fallbacks_for(chosen)
    if fallback and fallback not in chain:
        chain.append(fallback)
    seen, ordered = set(), []
    for mid in chain:
        if mid and mid not in seen:
            seen.add(mid)
            ordered.append(mid)
    result, used = model_router.run_with_fallback(
        ordered,
        lambda mid: _generate_with_model(mid, prompt, out_path, orientation),
        axis="image", logger=print,
    )
    if used != chosen:
        print(f"[ImageGen] served by fallback vendor '{used}' (primary '{chosen}' unavailable)")
    return result


def _generate_image_checked(model_id: str, prompt: str, out_path: str,
                            fallback: str, frame_id: str,
                            max_retries: int = 2, generator=None,
                            orientation: str = "portrait",
                            likeness_ref: str = "") -> str:
    """
    Generate, then run Gate B (fast sanity check) and Gate B2 (vision-LLM
    critique: anachronisms, wrong age, deformed anatomy, baked-in text);
    regenerate up to max_retries times on failure. Living here makes quality
    gating a property of image generation itself — every entry point gets it.
    generator: optional zero-arg callable replacing the default model dispatch
    (used by reference-guided generation).
    """
    from agents.safety import check_face_sanity, check_likeness, critique_image
    gen = generator or (lambda: _generate_image(model_id, prompt, out_path, fallback,
                                                orientation))
    for attempt in range(max_retries + 1):
        gen()
        if check_face_sanity(out_path, frame_id, orientation=orientation) \
                and critique_image(out_path, frame_id, prompt) \
                and check_likeness(out_path, likeness_ref, frame_id):
            return out_path
        if attempt < max_retries:
            print(f"[Safety] Gate B: {frame_id} — regenerating (attempt {attempt + 2}/{max_retries + 1})")
            try:
                os.remove(out_path)
            except OSError:
                pass
    print(f"[Safety] Gate B: {frame_id} — using last result after {max_retries + 1} attempts.")
    return out_path


_MIN_IMAGE_BYTES = 50_000  # files smaller than this are assumed corrupt/incomplete


def _image_cached(path: str) -> bool:
    """True if a valid generated image already exists at this path."""
    return os.path.exists(path) and os.path.getsize(path) >= _MIN_IMAGE_BYTES


def _prompt_hash(model_id: str, prompt: str, frame_id: str = "") -> str:
    """Short hash of model + frame_id + prompt for the cache filename.
    frame_id is included so two frames with identical prompts never share a file
    (they're different beats and should produce different images)."""
    return hashlib.md5(f"{model_id}|{frame_id}|{prompt}".encode()).hexdigest()[:8]


def _file_hash(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()[:8]


def _inject_world(frame: dict, prompt: str) -> str:
    """Prepend the story's World/Context clause (art direction + setting) so every shot
    shares one look and world. Part of the prompt → part of the cache hash. P2 Worlds."""
    world = (frame.get("world_style") or "").strip()
    if world and world.lower() not in prompt.lower():
        return f"{world}. {prompt}"
    return prompt


def generate_contextual_image(frame: dict, assets_dir: str, model_id: str = "",
                              reference_path: str = "", sketch_path: str = "") -> str:
    """
    Generate an age/era-accurate portrait for a story beat.
    model_id selects the image model (router-chosen); defaults to Flux 2 Pro.
    Falls back to gpt-image-2 if the chosen model errors.
    reference_path: optional portrait of the SAME person — generation then goes
    through the image-edit API ("keep this exact face, re-imagine the scene"),
    so the character stays consistent across frames/ages instead of being
    re-invented from a text description each time.
    Skips generation if a valid image for this exact prompt already exists on disk.
    """
    frame_id = frame["frame_id"]
    scene  = frame.get("scene", {})
    prompt = scene.get("image_prompt", "")
    if not prompt:
        # Bare fallback only (scene design absent/failed). No fixed stock person:
        # describe whoever the beat — and the detected speaker — implies.
        caption = frame.get("caption", "")
        note    = frame.get("director_note", "")
        gender  = frame.get("speaker_gender", "")
        age     = frame.get("speaker_age_bracket", "")
        who = (f"{age} " if age and age != "adult" else "") + (gender or "person")
        prompt  = (
            f"Cinematic, era- and setting-accurate portrait of a {who} at the right age "
            f"for this moment: {caption[:120]}. "
            f"{('Director note: ' + note + '. ') if note else ''}"
            "Authentic, grounded, photorealistic, warm natural light, "
            "9:16 vertical, no text, no watermarks, shallow depth of field, 85mm lens."
        )

    # Story-level character appearance (A2 resolver): keep a character's look — age/
    # gender/skin/hair/wardrobe — consistent across ALL their shots. Injected into the
    # prompt (so it's part of the cache hash → a change regenerates). Frame's own
    # image_prompt still leads; this augments it.
    appearance = (frame.get("character_appearance") or "").strip()
    if appearance and appearance.lower() not in prompt.lower():
        prompt = f"The person on screen is {appearance}. " + prompt
    # S30 location anchoring: the shot's location clause rides EVERY prompt as an
    # invariant (T11 lesson), so the place stays the same place across the reel.
    # Part of the prompt → part of the cache hash (edit a location → shots regenerate).
    location = (frame.get("location_clause") or "").strip()
    if location and location.lower() not in prompt.lower():
        prompt = f"{prompt} {location}."
    prompt = _inject_world(frame, prompt)   # P2: global art-direction / world

    use_ref = bool(reference_path) and os.path.exists(reference_path)
    # Sketch-as-composition (probe-verified 2026-07-20): an APPROVED storyboard
    # sketch rides as a second conditioning image — composition/framing from the
    # sketch, identity from the face ref, photoreal output (no pencil bleed).
    use_sketch = bool(sketch_path) and os.path.exists(sketch_path)
    chosen  = "gpt_image_ref" if (use_ref or use_sketch) else (model_id or "flux")
    seed = frame.get("scene", {}).get("_redo_seed", "")
    # Orientation joins the hash only when non-default, so every pre-existing
    # portrait cache stays valid while landscape/square (previously WRONG — stills
    # were hardcoded 9:16) regenerate correctly.
    orientation = frame.get("orientation") or "portrait"
    ohash = f"|or:{orientation}" if orientation != "portrait" else ""
    hash_src = (prompt + (f"|ref:{_file_hash(reference_path)}" if use_ref else "")
                + (f"|sk:{_file_hash(sketch_path)}" if use_sketch else "")
                + seed + ohash)
    out_path = os.path.join(
        assets_dir, f"ai_portrait_{frame_id}_{_prompt_hash(chosen, hash_src, frame_id)}.jpg")

    if _image_cached(out_path):
        print(f"[ImageGen] Portrait ({frame_id}) — reusing cached image ({os.path.getsize(out_path)//1024}KB)")
        return out_path

    if use_ref or use_sketch:
        from agents.image_editor import edit_image
        # Character-consistency phrasing on purpose: "keep this EXACT person's face and
        # identity" reads as a deepfake instruction to vendor content checkers (fal 422
        # content_policy_violation → silent fallback → no face conditioning at all).
        refs, parts = [], []
        if use_ref:
            refs.append(reference_path)
            parts.append("The FIRST image shows a recurring character from this "
                         "story. Depict the SAME character — consistent facial "
                         "features, the SAME outfit and wardrobe as the reference, "
                         "consistent anatomy and character design.")
        if use_sketch:
            refs.append(sketch_path)
            parts.append(f"The {'SECOND' if use_ref else 'FIRST'} image is a rough "
                         "pencil STORYBOARD sketch — follow its COMPOSITION, framing "
                         "and blocking exactly (where the subject sits in frame, the "
                         "camera angle, the negative space), but render a finished "
                         "PHOTOREALISTIC cinematic still — no pencil lines, no sketch "
                         "style, no monochrome, no arrows.")
        ref_prompt = " ".join(parts) + " Photorealistic. New scene: " + prompt
        srcs = " + ".join(os.path.basename(p) for p in refs)
        print(f"[ImageGen] Portrait ({frame_id}) [{scene.get('emotion', '')}] via reference edit "
              f"({srcs})…")
        _generate_image_checked("", prompt, out_path, "", frame_id,
                                generator=lambda: edit_image(refs, ref_prompt, out_path),
                                orientation=orientation,
                                likeness_ref=reference_path if use_ref else "")
    else:
        print(f"[ImageGen] Portrait ({frame_id}) [{scene.get('emotion', '')}] via {chosen}…")
        _generate_image_checked(chosen, prompt, out_path, "gpt_image", frame_id,
                                orientation=orientation)
    print(f"[ImageGen] Saved → {out_path}")
    return out_path


def recreate_ambient(frame: dict, assets_dir: str, reference_path: str = "",
                     model_id: str = "") -> str:
    """Reality–Fidelity ladder rung 3 (docs/REAL_MEDIA_QUALITY_LADDER.md): re-create a
    NON-person scene cinematically, INSPIRED FROM the real footage (image-to-image),
    preserving setting/composition/action at professional quality. **Identity-safe** —
    only for ambient/B-roll shots with no real face (the caller enforces that); there is
    deliberately NO 'keep the face' instruction, and the prompt forbids adding people.
    Falls back to symbolic text-to-image when no usable reference is given.
    """
    frame_id = frame["frame_id"]
    scene = frame.get("scene", {})
    ctx = scene.get("image_prompt") or frame.get("director_note") or frame.get("caption", "")
    prompt = (
        "Re-imagine THIS EXACT scene as a cinematic film frame — keep the same setting, "
        "composition, objects and action; do NOT add, remove or change any people. "
        "Professional cinematography, filmic colour grade, crisp focus, natural light, "
        "9:16 vertical, no text, no watermark. Context: " + ctx[:300]
    )
    if not (reference_path and os.path.exists(reference_path)):
        return generate_symbolic_image(frame, assets_dir, model_id)   # no ref → text-to-image, still no person
    out_path = os.path.join(
        assets_dir,
        f"ai_recreated_{frame_id}_{_prompt_hash('recreate_ambient', prompt + '|ref:' + _file_hash(reference_path), frame_id)}.jpg")
    if _image_cached(out_path):
        print(f"[ImageGen] Ambient recreate ({frame_id}) — reusing cached image")
        return out_path
    from agents.image_editor import edit_image
    print(f"[ImageGen] Ambient recreate ({frame_id}) — cinematic, inspired from "
          f"{os.path.basename(reference_path)} (no identity touched)…")
    _generate_image_checked("", prompt, out_path, "", frame_id,
                            generator=lambda: edit_image(reference_path, prompt, out_path))
    return out_path


def generate_symbolic_image(frame: dict, assets_dir: str, model_id: str = "") -> str:
    """
    Generate a symbolic/metaphorical image — objects and settings only, no people.
    model_id selects the image model (router-chosen); defaults to gpt-image-2.
    Skips generation if a valid image already exists on disk.
    """
    frame_id = frame["frame_id"]
    scene  = frame.get("scene", {})
    prompt = scene.get("image_prompt", "")
    if not prompt:
        caption = frame.get("caption", "")
        note    = frame.get("director_note", "")
        prompt  = (
            f"No people, no faces. Cinematic symbolic still life — objects and textures evoking: "
            f"{caption[:120]}. "
            f"{('Director note: ' + note + '. ') if note else ''}"
            "Grounded in real India — Assam textures, humble objects, warm kerosene lamp or monsoon light, "
            "shallow depth of field, photorealistic, 9:16 vertical, no text, no watermarks."
        )

    # S30 location anchoring: the invariant setting clause rides symbolic prompts too.
    location = (frame.get("location_clause") or "").strip()
    if location and location.lower() not in prompt.lower():
        prompt = f"{prompt} {location}."
    prompt = _inject_world(frame, prompt)   # P2: global art-direction / world

    # The generated PLATE as an image-to-image reference. Symbolic shots carry no face,
    # so nothing competes for the single reference slot — which is why the plate can
    # anchor these TODAY, while face-bearing shots still wait for D5 multi-ref (identity
    # beats place, S19/S20). Until this, the plate was billed and read by nothing: only
    # the text clause reached a render, so shots looked identical with or without it.
    plate = (frame.get("location_ref_path") or "").strip()
    use_plate = bool(plate) and os.path.exists(plate)
    chosen = "gpt_image_ref" if use_plate else (model_id or "gpt_image")
    # _redo_seed is injected by redo-still so even the same prompt produces a new file.
    seed = frame.get("scene", {}).get("_redo_seed", "")
    orientation = frame.get("orientation") or "portrait"
    ohash = f"|or:{orientation}" if orientation != "portrait" else ""
    hash_src = prompt + (f"|plate:{_file_hash(plate)}" if use_plate else "") + seed + ohash
    out_path = os.path.join(
        assets_dir, f"ai_symbolic_{frame_id}_{_prompt_hash(chosen, hash_src, frame_id)}.jpg")

    if _image_cached(out_path):
        print(f"[ImageGen] Symbolic ({frame_id}) — reusing cached image ({os.path.getsize(out_path)//1024}KB)")
        return out_path

    if use_plate:
        from agents.image_editor import edit_image
        # Anchor the PLACE, not the composition: the plate is an empty plate of the set,
        # and this shot still needs its own framing/subject. "No people" is restated
        # because that is what makes a symbolic shot symbolic.
        plate_prompt = ("The reference image is an empty plate of the LOCATION this shot "
                        "takes place in. Keep the SAME place — same architecture, "
                        "surfaces, props and light direction — but compose a NEW shot in "
                        "it as described. No people, no faces. Shot: " + prompt)
        print(f"[ImageGen] Symbolic ({frame_id}) [{scene.get('emotion', '')}] via plate edit "
              f"(location from {os.path.basename(plate)})…")
        _generate_image_checked("", prompt, out_path, "", frame_id,
                                generator=lambda: edit_image(plate, plate_prompt, out_path),
                                orientation=orientation)
    else:
        print(f"[ImageGen] Symbolic ({frame_id}) [{scene.get('emotion', '')}] via {chosen}…")
        _generate_image_checked(chosen, prompt, out_path, "gpt_image", frame_id,
                                orientation=orientation)
    print(f"[ImageGen] Saved → {out_path}")
    return out_path


# ── Storyboard panel (planning sketch, not a final render) ──────────────────────

STORYBOARD_STYLE = (
    "Black-and-white graphite pencil STORYBOARD sketch — a rough hand-drawn film "
    "storyboard panel, loose gestural lines, light cross-hatching, soft shading, with "
    "BLUE pencil motion arrows indicating the camera move. It is a planning sketch: focus "
    "on COMPOSITION and BLOCKING (where subjects and objects sit in the frame, the framing, "
    "the camera angle) — deliberately loose, NOT photoreal, NOT a portrait likeness of any "
    "real person. Tall vertical portrait composition. No text, no numbers, no captions, no "
    "words, no aspect-ratio labels anywhere in the image."
)


def generate_character_portrait(appearance: str, out_dir: str, *, world_clause: str = "",
                                char_id: str = "char", model_id: str = "",
                                variant: int = 0, reference_path: str = "") -> str:
    """Generate a CANONICAL character reference portrait from the sheet attributes (P1
    character-sheet-first). Used once per character in AI/fiction mode; the result becomes
    that character's reference so every shot conditions on the SAME face (via the pluggable
    identity path). Front-facing, neutral background — a clean face lock.

    reference_path (likeness chain, 2026-07-20): when a REAL photo of the character
    exists, the canonical portrait is generated FROM it via the identity path
    (edit_image) instead of from text — same recipe, but the face is the person's,
    not an invention. Candid crops make weak refs (angle, blur, expression baked
    in); this converts them into the flat-lit front-facing sheet that conditions
    downstream shots best, WITHOUT losing the identity."""
    # Recipe upgraded 2026-07-19 from the galleri5 pipeline anatomy (their actual
    # Yamraj sheet prompt): single-figure FULL BODY, explicit anti-grid negatives,
    # photoreal material texture. Deliberately KEPT flat/even lighting over their
    # dramatic side light — S30 confirmed flat-lit single-frontal refs condition
    # downstream generation better.
    prompt = (
        "ONE clean single-figure full-body portrait of a character, standing in a "
        "neutral front-facing pose on a plain uncluttered background, full body "
        "visible from head to feet, centered, soft even lighting, clear "
        f"unobstructed face, looking at camera. {appearance}. {world_clause}. "
        "Photorealistic rendering — real skin texture, natural material surfaces "
        "on clothing and props. Consistent character design, highly detailed face "
        "and wardrobe. NOT a multi-pose grid, NOT a turnaround sheet, NOT a panel "
        "layout — one single clean portrait. No labels, no text, no watermarks, "
        "no borders. Vertical 9:16."
    )
    chosen = model_id or "flux"           # face-strong model for a clean canonical portrait
    os.makedirs(out_dir, exist_ok=True)
    use_ref = bool(reference_path) and os.path.exists(reference_path)
    # variant participates in the cache key: 0 reuses a prior identical portrait (bulk
    # flow); any other value forces a FRESH sample — the operator's "↻ New face" redo.
    # The real-photo ref joins the key too — swapping the photo must re-derive the sheet.
    key_extra = f"{char_id}_v{variant}" + (f"|ref:{_file_hash(reference_path)}" if use_ref else "")
    out_path = os.path.join(out_dir, f"charref_{char_id}_{_prompt_hash(chosen, prompt, key_extra)}.jpg")
    if _image_cached(out_path):
        return out_path
    if use_ref:
        from agents.image_editor import edit_image
        ref_prompt = (
            "The attached image shows a REAL person — this exact person, same face, "
            "same identity. Re-render them as: " + prompt +
            " Keep the face IDENTICAL to the reference — same bone structure, eyes, "
            "nose, mouth, skin tone, age. Do not beautify, de-age, or stylize the face."
        )
        print(f"[ImageGen] Canonical portrait ({char_id}) FROM real photo (identity path)…")
        _generate_image_checked("", ref_prompt, out_path, "", char_id,
                                generator=lambda: edit_image(reference_path, ref_prompt, out_path),
                                likeness_ref=reference_path)
    else:
        print(f"[ImageGen] Canonical character portrait ({char_id}) via {chosen}…")
        _generate_image_checked(chosen, prompt, out_path, "gpt_image", char_id)
    return out_path


def generate_location_plate(description: str, out_dir: str, *, world_clause: str = "",
                            time_of_day: str = "", loc_id: str = "loc",
                            model_id: str = "", variant: int = 0,
                            lighting_arc: str = "", orientation: str = "portrait") -> str:
    """Generate a CANONICAL location plate (S30 Phase 1 — the 'character sheet for
    places'). Plate discipline stolen from the galleri5 teardown (§10.4 item 5): the
    environment is generated EMPTY — no people, clear negative space at center for the
    characters, lighting headroom for later drama — so shots condition on the place
    without fighting a baked-in subject. The plate becomes the location's reference;
    the invariant clause (canvas_run._location_clause) rides every shot's prompt.
    lighting_arc (galleri5 anatomy, 2026-07-19): the plate ANTICIPATES the story's key
    light moment (e.g. 'room for divine golden radiance to flood the space later') so
    late-story lighting reads as revealed, not repainted. orientation follows the reel."""
    _prose = _SIZES.get(orientation or "portrait", _SIZES["portrait"])["prose"]
    prompt = (
        "Location reference plate — a single clean establishing view of ONE place, "
        f"{_prose}. {description.strip()}"
        + (f" Time of day: {time_of_day.strip()}." if time_of_day.strip() else "")
        + (f" {world_clause.strip()}." if world_clause.strip() else "")
        + " EMPTY environment: no people, no characters, no animals. Keep clear space "
          "at the center of frame for characters to occupy later, and lighting headroom "
          "(don't blow out highlights)."
        + (f" Leave room for this later in the story: {lighting_arc.strip()}."
           if lighting_arc.strip() else "")
        + " Consistent, memorable geometry. "
          "No text, no labels, no borders, no watermark."
    )
    chosen = model_id or "flux"
    os.makedirs(out_dir, exist_ok=True)
    # Rule 12 (redo ≠ cache hit): variant participates in the cache key — 0 reuses a
    # prior identical plate; any other value is the operator's "↻ New plate".
    out_path = os.path.join(
        out_dir, f"locplate_{loc_id}_{_prompt_hash(chosen, prompt, f'{loc_id}_v{variant}')}.jpg")
    if _image_cached(out_path):
        return out_path
    print(f"[ImageGen] Location plate ({loc_id}) via {chosen}…")
    # Checked path on purpose: Gate B treats no-face images as valid (symbolic frames
    # have no people by design) and Gate B2 catches baked-in text / era mismatches —
    # both real failure modes for an establishing plate.
    _generate_image_checked(chosen, prompt, out_path, "gpt_image", loc_id,
                            orientation=orientation)
    return out_path


def generate_storyboard_panel(frame: dict, assets_dir: str, model_id: str = "") -> str:
    """Render a pencil-sketch STORYBOARD panel for a shot — a planning artifact (framing +
    blocking + camera move), NOT the final render and NOT a photoreal likeness. Uses a
    cheap draft model and the RAW generate path (no photoreal QC gates — a loose sketch
    would falsely fail face-sanity/critique). Content-hash cached; degrades to '' on error
    so the caller can fall back to the SVG-arrow placeholder."""
    frame_id = frame["frame_id"]
    scene = frame.get("scene", {})
    shot = frame.get("shot_size", "") or scene.get("shot_size", "")
    cam = scene.get("camera_angle", "")
    motion = frame.get("motion_override") or scene.get("motion_prompt") or ""
    action = (scene.get("scene_description") or scene.get("image_prompt")
              or frame.get("caption", ""))
    prompt = (
        f"{STORYBOARD_STYLE}\n"
        f"SHOT: {shot or 'medium shot'}{(' · ' + cam) if cam else ''}. "
        f"CAMERA MOVE: {motion or 'slow push in'}. "
        f"SCENE (sketch the composition loosely, no real faces): {str(action)[:200]}."
    )
    chosen = model_id or "seedream"   # cheap draft model — it's only a sketch
    os.makedirs(assets_dir, exist_ok=True)
    out_path = os.path.join(
        assets_dir, f"storyboard_{frame_id}_{_prompt_hash(chosen, prompt, frame_id)}.jpg")
    if _image_cached(out_path):
        return out_path
    print(f"[ImageGen] Storyboard panel ({frame_id}) via {chosen}…")
    _generate_image(chosen, prompt, out_path, "gpt_image")   # raw — no photoreal gates
    return out_path
