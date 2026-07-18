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
                "visual_subject_id": {"type": "string"},
            },
            "required": ["frame_id", "speaker_id", "visual_subject_id"],
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
        f["visual_subject_id"]           = member["id"]
        f["visual_subject_label"]        = member["label"]
        f["visual_subject_gender"]       = member["gender"]
        f["visual_subject_age_bracket"]  = member["age_bracket"]


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
        "You analyse a story reel script — either FIRST-PERSON (one narrator tells "
        "their own story) or THIRD-PERSON (an unseen narrator describes someone else, "
        "e.g. a mythological/fictional character). Two separate questions per beat:\n"
        f"1. speaker_id — whose VOICE is producing this beat's words? Usually "
        f"'{NARRATOR_ID}' ({narrator.get('label')}), unless the beat is a direct quote "
        "in someone else's mouth (a child, father, friend).\n"
        "2. visual_subject_id — who is DEPICTED on screen in this beat? For a "
        "first-person story this is normally the SAME id as speaker_id. For "
        "third-person narration, a recurring protagonist who is described/narrated "
        "about (not directly quoted) should still get their OWN visual_subject_id — "
        "do not default them to the narrator just because they rarely speak. The "
        "narrator has no face; they never belong in visual_subject_id unless truly "
        "no one else is on screen (e.g. an establishing shot).\n"
        "Build a small cast covering every id used as EITHER speaker_id or "
        "visual_subject_id. Reuse the SAME id when the same person recurs (so their "
        "face and voice stay consistent) — this matters most for visual_subject_id, "
        "since that is what locks a recurring character's face across shots. Use "
        "short, stable ids like 'narrator', 'son', 'father', 'young_self', or a "
        "character's own name lowercased ('hanuman'). gender ∈ {female,male}; "
        "age_bracket ∈ {child,adult,elderly}. When unsure about speaker_id, assign "
        "the narrator; when unsure about visual_subject_id for a third-person beat, "
        "assign it to the story's main recurring character, not the narrator."
    )
    user = (f"Narrator: {narrator.get('label')}"
            + (f" — {narrator_description}" if narrator_description else "")
            + f"\n\nBeats:\n{beat_list}\n\n"
            'Reply ONLY as JSON: {"cast":[{"id","label","gender","age_bracket"}],'
            ' "by_frame":[{"frame_id","speaker_id","visual_subject_id"}]}. Always '
            'include the narrator in cast.')

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

    by_frame = {row.get("frame_id"): row for row in data.get("by_frame", [])
                if row.get("frame_id")}

    def _member(mid: str) -> dict:
        mid = (mid or "").strip() or NARRATOR_ID
        if mid in cast:
            return cast[mid]
        # The model referenced an id it forgot to declare in "cast" (schema doesn't
        # enforce that cross-reference) — synthesize a member rather than silently
        # collapsing a real visual subject back onto the narrator, which is the
        # exact bug this two-field split exists to fix.
        member = {"id": mid, "label": mid.replace("_", " ").title(),
                  "gender": "male", "age_bracket": "adult"}
        cast[mid] = member
        return member

    n_non_narrator = n_visual_only = 0
    for f in frames:
        row = by_frame.get(f["frame_id"], {})
        spk = _member(row.get("speaker_id") or NARRATOR_ID)
        vis = _member(row.get("visual_subject_id") or spk["id"])
        f["speaker_id"]          = spk["id"]
        f["speaker_label"]       = spk["label"]
        f["speaker_gender"]      = spk["gender"]
        f["speaker_age_bracket"] = spk["age_bracket"]
        f["visual_subject_id"]          = vis["id"]
        f["visual_subject_label"]       = vis["label"]
        f["visual_subject_gender"]      = vis["gender"]
        f["visual_subject_age_bracket"] = vis["age_bracket"]
        if spk["id"] != NARRATOR_ID:
            n_non_narrator += 1
        if vis["id"] != spk["id"]:
            n_visual_only += 1

    if n_non_narrator or n_visual_only:
        print(f"[Cast] {len(cast)} speakers; {n_non_narrator} beat(s) reassigned from "
              f"narrator; {n_visual_only} narrated beat(s) given a distinct on-screen subject")
        for f in frames:
            if f.get("speaker_id") != NARRATOR_ID or f.get("visual_subject_id") != f.get("speaker_id"):
                print(f"  {f['frame_id']} → speaks: {f['speaker_label']} "
                      f"({f['speaker_gender']}/{f['speaker_age_bracket']}) "
                      f"| on screen: {f['visual_subject_id']}")
    for f in frames:
        f["_cast_detected"] = True
    return list(cast.values())


def _cast_from_frames(frames: list[dict]) -> list[dict]:
    """Rebuild the cast list from already-tagged frames (idempotent detect_cast path).
    Collects ids from BOTH speaker_id and visual_subject_id, so a narrated-about
    on-screen subject (never a speaker) survives a second, idempotent pass."""
    seen: dict[str, dict] = {}
    for f in frames:
        sid = f.get("speaker_id")
        if sid and sid not in seen:
            seen[sid] = {"id": sid, "label": f.get("speaker_label", sid),
                         "gender": f.get("speaker_gender", "female"),
                         "age_bracket": f.get("speaker_age_bracket", "adult")}
        vid = f.get("visual_subject_id")
        if vid and vid not in seen:
            seen[vid] = {"id": vid, "label": vid.replace("_", " ").title(),
                         "gender": "male", "age_bracket": "adult"}
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
        # Preserve an existing visual_subject_id (carried back from the UI after a
        # prior detect_cast pass); default to speaker_id for older frames that
        # predate this field, so first-person stories are unaffected either way.
        vis_id = f.get("visual_subject_id") or member["id"]
        vis = by_id.get(vis_id, member)
        f["visual_subject_id"]          = vis_id
        f["visual_subject_label"]       = vis.get("label", "")
        f["visual_subject_gender"]      = vis.get("gender", "female")
        f["visual_subject_age_bracket"] = vis.get("age_bracket", "adult")
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
        voices = (data.get("language_voices") or {}).get(lang) or {}
        if not any(str(v).strip() for v in voices.values()):
            # T13c honesty: no native voice configured -> the multilingual default
            # reads the text anyway, but the operator should know (accent may drift).
            from agents import degradation
            degradation.report("voice", "info",
                               f"no {lang}-native voice in config/voices.json -> "
                               f"using the default multilingual voice")
        return voices
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
    frame's VISUAL SUBJECT — who is depicted, which for third-person narration
    (a mythological/fictional protagonist rarely directly quoted) differs from
    speaker_id (whose voice reads the line). Falls back to speaker_* fields for
    frames from before this distinction existed, so old runs are unaffected."""
    subj_id = frame.get("visual_subject_id") or frame.get("speaker_id", NARRATOR_ID)
    if subj_id == NARRATOR_ID:
        return narrator_description or ""
    gender = frame.get("visual_subject_gender") or frame.get("speaker_gender", "female")
    age    = frame.get("visual_subject_age_bracket") or frame.get("speaker_age_bracket", "adult")
    noun = ({("female", "child"): "young girl", ("male", "child"): "young boy",
             ("female", "elderly"): "elderly woman", ("male", "elderly"): "elderly man",
             ("female", "adult"): "woman", ("male", "adult"): "man"}
            .get((gender, age), "person"))
    label = (frame.get("visual_subject_label") or frame.get("speaker_label", "")).strip()
    return f"{noun} ({label})" if label and label.lower() not in noun else noun
