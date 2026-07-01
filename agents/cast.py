"""
Cast + per-line speaker detection.

A Humans-of-Bombay story usually has ONE narrator (e.g. the mother) but quotes
others mid-narration ("My son asked, 'Mom, where is father gone?'"). That quoted
beat should show the SON — his face, his age, and (when lip-sync/voiceover is on)
his voice — not the narrator's.

This module:
  · detect_cast(frames, narrator_name, narrator_description)
        ONE LLM pass over all beats → builds a cast (narrator + any quoted
        speakers) and tags each frame with its speaker. Mutates frames in place,
        setting: speaker_id, speaker_label, speaker_gender, speaker_age_bracket.
        Returns the cast list (for the UI dropdown + per-speaker face refs).
        Safe no-op on any error: every frame falls back to the narrator.

  · voice_for_frame(frame, default_voice_id, voice_map)
        Resolve which ElevenLabs voice a frame should use:
        explicit [voice:] override  >  speaker→voice map  >  global default.

The pipeline reads the denormalized speaker_* keys off each frame (consistent
with the "everything flows through the frame dict" design); the returned cast
list is only for the UI and for per-speaker face consistency.
"""

import json
import os

NARRATOR_ID = "narrator"
_AGE_BRACKETS = ("child", "adult", "elderly")
_GENDERS = ("female", "male")

_VOICES_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "voices.json")

_CAST_SCHEMA = {"name": "cast", "schema": {
    "type": "object", "additionalProperties": False,
    "properties": {
        "cast": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "id":          {"type": "string"},
                "label":       {"type": "string"},
                "gender":      {"type": "string", "enum": list(_GENDERS)},
                "age_bracket": {"type": "string", "enum": list(_AGE_BRACKETS)},
            },
            "required": ["id", "label", "gender", "age_bracket"],
        }},
        "by_frame": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "frame_id":  {"type": "string"},
                "speaker_id": {"type": "string"},
            },
            "required": ["frame_id", "speaker_id"],
        }},
    },
    "required": ["cast", "by_frame"],
}}


def _narrator_member(name: str, description: str) -> dict:
    """The default cast member every story has; attributes inferred from the
    operator's subject description (kept loose — the LLM refines gender/age)."""
    desc = (description or "").lower()
    gender = "male" if any(w in desc for w in (" man", "male", " boy", "father", " he ")) else "female"
    age = "child"   if any(w in desc for w in ("child", "boy", "girl", "kid")) else \
          "elderly" if any(w in desc for w in ("elderly", "old ", "grand")) else "adult"
    return {"id": NARRATOR_ID, "label": (name or "Narrator").strip() or "Narrator",
            "gender": gender, "age_bracket": age}


def _apply(frames: list[dict], member: dict):
    for f in frames:
        f["speaker_id"]          = member["id"]
        f["speaker_label"]       = member["label"]
        f["speaker_gender"]      = member["gender"]
        f["speaker_age_bracket"] = member["age_bracket"]


def detect_cast(frames: list[dict], narrator_name: str = "",
                narrator_description: str = "") -> list[dict]:
    """
    Tag each frame with its speaker. Returns the cast list. On any failure every
    frame is assigned to the narrator (current single-subject behaviour).
    """
    # Idempotency: if this frame set was already detected this run (e.g. smart_match ran
    # cast-before-match), don't re-LLM — rebuild the cast list from the existing tags.
    # (The matcher C1 fix + run_caption both call this; the second call is now a no-op.)
    if frames and frames[0].get("_cast_detected"):
        return _cast_from_frames(frames)

    narrator = _narrator_member(narrator_name, narrator_description)
    _apply(frames, narrator)   # default everyone to narrator first

    captioned = [f for f in frames if (f.get("caption") or "").strip()]
    if len(captioned) < 1:
        for f in frames:
            f["_cast_detected"] = True
        return [narrator]

    beat_list = "\n".join(f"{f['frame_id']}: {f.get('caption','').strip()}"
                          for f in captioned)
    sys = (
        "You analyse a first-person story reel script. ONE person narrates "
        f"(id '{NARRATOR_ID}', {narrator.get('label')}). Most beats are the narrator. "
        "But a beat may QUOTE someone else (a child, father, friend) or depict a "
        "DIFFERENT person/age of the subject (e.g. the narrator as a young child). "
        "For each such beat, the on-screen person should be that speaker, not the "
        "narrator.\n"
        "Build a small cast and assign every beat to a speaker. Reuse the SAME "
        "speaker id when the same person recurs (so their face stays consistent). "
        "Use short, stable ids like 'narrator', 'son', 'father', 'young_self'. "
        "gender ∈ {female,male}; age_bracket ∈ {child,adult,elderly}. "
        "When unsure, assign the narrator."
    )
    user = (f"Narrator: {narrator.get('label')}"
            + (f" — {narrator_description}" if narrator_description else "")
            + f"\n\nBeats:\n{beat_list}\n\n"
            'Reply ONLY as JSON: {"cast":[{"id","label","gender","age_bracket"}],'
            ' "by_frame":[{"frame_id","speaker_id"}]}. Always include the narrator in cast.')

    try:
        from agents import llm
        text = llm.chat([{"role": "system", "content": sys},
                         {"role": "user", "content": user}],
                        json_mode=True, json_schema=_CAST_SCHEMA,
                        max_tokens=1500, model_tier="reasoning")
        data = llm.json_loads_lenient(text)
    except Exception as e:
        print(f"[Cast] detection failed ({e}) — single-narrator fallback")
        return [narrator]

    cast = {NARRATOR_ID: narrator}
    for m in data.get("cast", []):
        mid = (m.get("id") or "").strip()
        if not mid:
            continue
        if mid == NARRATOR_ID:
            # keep narrator label/name from the operator, refine gender/age
            narrator["gender"] = m.get("gender", narrator["gender"])
            narrator["age_bracket"] = m.get("age_bracket", narrator["age_bracket"])
            continue
        cast[mid] = {
            "id": mid,
            "label": (m.get("label") or mid).strip(),
            "gender": m.get("gender") if m.get("gender") in _GENDERS else "female",
            "age_bracket": m.get("age_bracket") if m.get("age_bracket") in _AGE_BRACKETS else "adult",
        }

    by_frame = {row.get("frame_id"): row.get("speaker_id")
                for row in data.get("by_frame", []) if row.get("frame_id")}
    n_non_narrator = 0
    for f in frames:
        sid = by_frame.get(f["frame_id"], NARRATOR_ID)
        member = cast.get(sid, narrator)
        f["speaker_id"]          = member["id"]
        f["speaker_label"]       = member["label"]
        f["speaker_gender"]      = member["gender"]
        f["speaker_age_bracket"] = member["age_bracket"]
        if member["id"] != NARRATOR_ID:
            n_non_narrator += 1

    if n_non_narrator:
        print(f"[Cast] {len(cast)} speakers; {n_non_narrator} beat(s) reassigned from narrator")
        for f in frames:
            if f.get("speaker_id") != NARRATOR_ID:
                print(f"  {f['frame_id']} → {f['speaker_label']} "
                      f"({f['speaker_gender']}/{f['speaker_age_bracket']})")
    for f in frames:
        f["_cast_detected"] = True
    return list(cast.values())


def _cast_from_frames(frames: list[dict]) -> list[dict]:
    """Rebuild the cast list from already-tagged frames (idempotent detect_cast path)."""
    seen: dict[str, dict] = {}
    for f in frames:
        sid = f.get("speaker_id")
        if sid and sid not in seen:
            seen[sid] = {"id": sid, "label": f.get("speaker_label", sid),
                         "gender": f.get("speaker_gender", "female"),
                         "age_bracket": f.get("speaker_age_bracket", "adult")}
    return list(seen.values()) or [_narrator_member("", "")]


def apply_cast(frames: list[dict], cast: list[dict],
               narrator_name: str = "", narrator_description: str = "") -> None:
    """
    Set each frame's speaker_* fields from its existing speaker_id using a cast
    list (e.g. carried back from the UI through the run payload, after any manual
    overrides). Used instead of a second LLM call on the render path. Frames
    whose speaker_id is unknown fall back to the narrator.
    """
    by_id = {m["id"]: m for m in (cast or []) if m.get("id")}
    if NARRATOR_ID not in by_id:
        by_id[NARRATOR_ID] = _narrator_member(narrator_name, narrator_description)
    for f in frames:
        member = by_id.get(f.get("speaker_id"), by_id[NARRATOR_ID])
        f["speaker_id"]          = member["id"]
        f["speaker_label"]       = member.get("label", "")
        f["speaker_gender"]      = member.get("gender", "female")
        f["speaker_age_bracket"] = member.get("age_bracket", "adult")
        f["_cast_detected"]      = True   # operator-applied cast → detect_cast won't override


# ── Voice resolution ──────────────────────────────────────────────────────────

def _load_voice_map() -> dict:
    try:
        with open(os.path.abspath(_VOICES_PATH)) as f:
            return (json.load(f) or {}).get("roles", {}) or {}
    except Exception:
        return {}


def _load_language_voices(lang: str) -> dict:
    """Native regional voice ids for `lang` (config/voices.json → language_voices).
    Empty dict when the language is unset/unknown or has no ids — caller falls back."""
    if not lang:
        return {}
    try:
        with open(os.path.abspath(_VOICES_PATH)) as f:
            data = json.load(f) or {}
        return (data.get("language_voices") or {}).get(lang) or {}
    except Exception:
        return {}


def _voice_role_key(gender: str, age_bracket: str) -> str:
    if age_bracket == "child":
        return "child"
    if age_bracket == "elderly":
        return "elderly_male" if gender == "male" else "elderly_female"
    return "male_adult" if gender == "male" else "female_adult"


def voice_for_frame(frame: dict, default_voice_id: str = "",
                    voice_map: dict | None = None, lang: str | None = None) -> str:
    """
    Resolve the ElevenLabs voice for a frame, in priority order:
      1. explicit [voice:] / per-frame override
      2. voice_map[speaker_id]      — the UI 'Cast voices' panel assigns per speaker
      3. role map (gender/age)      — config/voices.json + any role-keyed voice_map
      4. global default
    When `lang` is set (a multi-language re-render), that language's native regional
    voices (config/voices.json → language_voices[lang]) override the base role map so
    a Hindi reel is read by a Hindi-native voice. lang=None keeps the original
    behaviour exactly. Empty values fall through, so partial mapping is always safe.
    """
    override = (frame.get("voice_override") or "").strip()
    if override:
        return override

    voice_map = voice_map or {}
    sid = frame.get("speaker_id", NARRATOR_ID)
    if voice_map.get(sid):                       # per-speaker assignment (UI)
        return voice_map[sid]

    roles = dict(_load_voice_map())              # role-keyed config defaults
    roles.update({k: v for k, v in _load_language_voices(lang).items() if v})  # native lang voices
    roles.update({k: v for k, v in voice_map.items() if v})  # role-keyed UI overrides win

    if sid == NARRATOR_ID:
        return roles.get("narrator") or default_voice_id

    key = _voice_role_key(frame.get("speaker_gender", "female"),
                          frame.get("speaker_age_bracket", "adult"))
    return roles.get(key) or roles.get("narrator") or default_voice_id


def subject_descriptor(frame: dict, narrator_description: str = "") -> str:
    """A short 'who is on screen' phrase for the image prompt, derived from the
    frame's speaker. Narrator frames keep the operator's rich description; quoted
    speakers get a gender/age-accurate descriptor."""
    if frame.get("speaker_id", NARRATOR_ID) == NARRATOR_ID:
        return narrator_description or ""
    gender = frame.get("speaker_gender", "female")
    age    = frame.get("speaker_age_bracket", "adult")
    noun = ({("female", "child"): "young girl", ("male", "child"): "young boy",
             ("female", "elderly"): "elderly woman", ("male", "elderly"): "elderly man",
             ("female", "adult"): "woman", ("male", "adult"): "man"}
            .get((gender, age), "person"))
    label = frame.get("speaker_label", "").strip()
    return f"{noun} ({label})" if label and label.lower() not in noun else noun
