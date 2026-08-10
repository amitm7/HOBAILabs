"""
Shot planner (Studio Mode, MODE3_PLAN P2): turn a free-text brief into an
editable frames[] list — the SAME frame schema the Story/Brand front doors
produce, so the rest of the engine (`_build_frames_from_payload`,
`_generate_stills`, `clip_builder`, `assembler`) runs unchanged.

Two scopes:
- "commerce": one locked subject × N camera setups (intro → lookbook → detail →
  over-shoulder → walking → product hero → final). Mirrors the jewelry/fashion
  ad workflow. Product beats are flagged so the real product image is used.
- "general": emotional beats, like Story mode.

Pluggable + safe:
- One LLM call via agents/llm.py (NEVER a vendor SDK), json_schema-validated.
- Disk-cached by a hash of (brief, scope, talent, product) — re-planning is free.
- Graceful fallback to a sentence/line split so a planning failure never blocks
  the user (build-feature rule #4).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from agents import llm

PLAN_CACHE_DIR = Path.home() / ".hob_cache" / "shot_plans"
PLAN_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Default per-shot negative prompt surfaced in the UI (editable). Mirrors the
# continuity-lock discipline from the masterclass samples.
DEFAULT_NEGATIVE = (
    "blurry, distorted, deformed hands, extra limbs, extra fingers, "
    "morphing face, facial inconsistency, logo distortion, text artifacts, "
    "watermark, low quality, oversaturated, cartoon, anime"
)

_PLAN_SCHEMA = {"name": "shot_plan", "schema": {
    "type": "object", "additionalProperties": False,
    "properties": {
        "frames": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "caption":        {"type": "string"},
                "director_note":  {"type": "string"},
                "motion":         {"type": "string"},
                "shot_size":      {"type": "string"},
                "product_beat":   {"type": "boolean"},
                "uses_talent":    {"type": "boolean"},
                "duration":       {"type": "number"},
            },
            "required": ["caption", "director_note", "motion", "shot_size",
                         "product_beat", "uses_talent", "duration"],
        }},
    },
    "required": ["frames"],
}}

_COMMERCE_SYSTEM = """You are a commercial director planning a vertical 9:16 product/fashion ad reel.
You receive a brief plus (optionally) a locked Talent (the on-screen person) and a locked Product.
Plan ONE subject across a sequence of camera setups that cut together as a premium ad:
intro pose → lookbook/lifestyle → product detail close-up → over-shoulder/angle → motion/walking → PRODUCT HERO → final hero frame.
Rules:
- The SAME person and the SAME product across every shot (continuity is everything).
- Mark product_beat=true on the close-up/hero shots that must show the REAL product crisply.
- Mark uses_talent=true on every shot that shows the locked person.
- caption = the on-screen / spoken line for that beat (you MAY write it; keep it short and natural).
- director_note = visual direction (framing, lighting, posture) — never on-screen text.
- motion = ONE short camera move (e.g. "slow push-in", "lookbook pan", "macro pull-back", "360 orbit").
- shot_size = wide establishing | medium | close-up | extreme close-up | detail insert.
- 6-8 frames. Ground everything in real, premium e-commerce aesthetics."""

_GENERAL_SYSTEM = """You are a cinematic director planning a vertical 9:16 short reel from a free-text brief.
Produce an emotional beat sequence that cuts together as one film (not a slideshow).
Rules:
- caption = the on-screen / spoken line for that beat (short, natural; you MAY write it).
- director_note = visual direction for the shot (framing, light, posture) — never on-screen text.
- motion = ONE short camera move.
- shot_size = wide establishing | medium | close-up | extreme close-up | detail insert. Vary it between consecutive beats.
- product_beat=false and uses_talent=true unless the beat is clearly an object-only/symbolic shot.
- Ground everything in authentic, specific real-world detail."""

# Scope registry (S29/S31): one table both validation and prompt-pick read, so a
# new scope is a dict entry, not two branches that can drift out of sync.
_SCOPE_SYSTEM_PROMPTS = {
    "general":  _GENERAL_SYSTEM,
    "commerce": _COMMERCE_SYSTEM,
}


_FRAME_MARKER_RE = re.compile(
    r"(?im)^\s*(?:(?:FRAME|SCENE|SHOT)\s+\d+|(?-i:OPENING|CLOSING))\b")


def _estimate_auto_shots(brief: str) -> int:
    """Auto-length shot-count guess, used ONLY to size the token budget (never shown
    to the model). A flat 40 undercounts a long, richly-structured brief — a script
    with N explicit FRAME/SCENE markers reliably expands to ~3-4x N shots (observed:
    an 11-marker cinematic brief produced 24 shots from its first 6 markers alone
    before hitting the old budget ceiling). Markerless briefs fall back to a word-
    count density proxy. Floor of 40 preserves existing behaviour for typical briefs."""
    markers = len(_FRAME_MARKER_RE.findall(brief))
    if markers:
        return max(40, markers * 4)
    return max(40, round(len(brief.split()) / 15))


def _length_directive(target_seconds: int) -> str:
    """Turn a target length into shot-count guidance. 0 = auto (let the story decide)."""
    if not target_seconds or target_seconds <= 0:
        return ("\n- LENGTH: plan AS MANY beats as the story genuinely needs — one shot per"
                " distinct moment. A short anecdote may be 5-8 shots; a rich life story may be"
                " 20-40+. Do NOT pad or compress to a fixed length; match the story's real arc."
                " Each shot ~3-6s."
                "\n- COMPLETENESS (hard rule): the brief may describe MANY scenes/frames — you"
                " MUST plan through the brief's actual ending, not stop early at a dramatic"
                " midpoint. If the brief has explicit FRAME/SCENE markers, your last shot must"
                " correspond to its LAST marker. Do not treat an earlier high-energy beat (e.g."
                " a leap, a climax) as the stopping point if the brief continues past it.")
    shots = max(4, round(target_seconds / 4.5))
    return (f"\n- LENGTH: target ~{target_seconds}s total → about {shots} shots averaging ~4-5s"
            f" each. Cover the whole arc within that budget; merge minor beats if needed.")


def _cache_key(brief: str, scope: str, talent: dict | None, product: dict | None,
               target_seconds: int = 0) -> str:
    h = hashlib.md5()
    h.update(f"{scope}|{target_seconds}|{brief}".encode())
    if talent:
        h.update(f"|tal:{talent.get('name','')}|{talent.get('descriptor','')}".encode())
    if product:
        h.update(f"|prd:{product.get('name','')}|{json.dumps(product.get('specs',{}),sort_keys=True)}".encode())
    return h.hexdigest()


def _user_message(brief: str, scope: str, talent: dict | None, product: dict | None) -> str:
    lines = [f"Brief:\n{brief.strip()[:4000]}"]
    if talent:
        d = talent.get("descriptor", "")
        lines.append(f"\nLocked Talent (on screen in every uses_talent shot): "
                     f"{talent.get('name','')}" + (f" — {d}" if d else ""))
    if product and scope == "commerce":
        specs = product.get("specs", {})
        spec_str = ", ".join(f"{k}: {v}" for k, v in specs.items()) if specs else ""
        lines.append(f"\nLocked Product (must appear identical, crisp on product_beat shots): "
                     f"{product.get('name','')}" + (f" ({spec_str})" if spec_str else ""))
    return "\n".join(lines)


def _to_frames(raw_frames: list[dict], scope: str) -> list[dict]:
    """Normalise planner output into the UI frame schema."""
    frames = []
    for i, rf in enumerate(raw_frames, start=1):
        fid = f"f{i:02d}"
        product_beat = bool(rf.get("product_beat"))
        uses_talent = bool(rf.get("uses_talent", not product_beat))
        # Object-only/symbolic shots that don't show the talent → ai_symbolic.
        photo_spec = "ai_symbolic" if (not uses_talent and not product_beat) else "ai_portrait"
        try:
            dur = float(rf.get("duration") or 0) or 5.0
        except (TypeError, ValueError):
            dur = 5.0
        frames.append({
            "frame_id":        fid,
            "caption":         (rf.get("caption") or "").strip(),
            "director_note":   (rf.get("director_note") or "").strip(),
            "motion_override": (rf.get("motion") or "").strip(),
            "photo_spec":      photo_spec,
            "product_beat":    product_beat,
            "uses_talent":     uses_talent,
            "shot_size":       (rf.get("shot_size") or "").strip(),
            "duration":        round(max(2.0, min(15.0, dur)), 1),
            "negative_prompt": DEFAULT_NEGATIVE,
            "continuity_lock": "",
        })
    return frames


def _fallback_frames(brief: str, scope: str) -> list[dict]:
    """No-LLM degradation: split the brief into beats so the user still gets cards."""
    chunks = [c.strip() for c in re.split(r"(?<=[.!?])\s+|\n+", brief or "") if c.strip()]
    if not chunks:
        chunks = ["Opening shot", "Detail shot", "Hero shot"]
    chunks = chunks[:8]
    raw = []
    n = len(chunks)
    for i, c in enumerate(chunks):
        is_hero = (i == n - 1)
        raw.append({
            "caption": c[:120], "director_note": "",
            "motion": "slow push-in" if not is_hero else "macro pull-back",
            "shot_size": "close-up" if (scope == "commerce" and i % 2) else "medium",
            "product_beat": bool(scope == "commerce" and is_hero),
            "uses_talent": True, "duration": 5.0,
        })
    return _to_frames(raw, scope)


# ── Script COMPILE mode (deterministic — no LLM, no reinterpretation) ─────────
# When the brief is an AUTHORED shot list (explicit FRAME/SCENE/SHOT markers), the
# operator has already made the creative decisions. Re-planning it through an LLM
# was the root cause of a whole failure class: dialogue attribution discarded then
# wrongly re-guessed (a line written "SUGRIVA:" rendered as Rama), camera/lighting
# notes paraphrased through 3 generations of reword, [Sound:] cues lost, authored
# structure remixed. Compile mode parses the structure verbatim; the LLM planner is
# reserved for unstructured prose briefs. Everything here is deterministic and
# hermetic — a compile failure falls back to the LLM path (never blocks).

_TIMING_RE = re.compile(r"\((\d+(?:\.\d+)?)\s*[–\-]\s*(\d+(?:\.\d+)?)\s*s\)")
_VISUALS_RE = re.compile(r"(?is)\[\s*VISUALS?\s*:\s*(.*?)\]")
_SOUND_BRACKET_RE = re.compile(r"(?is)\[\s*SOUND[^:\]]*:\s*(.*?)\]")
_SOUND_LINE_RE = re.compile(r"(?im)^\s*Sound(?:\s+design)?\s*:\s*(.+)$")
_SPEAKER_LINE_RE = re.compile(r"^([A-Z][A-Z0-9 .'\-]{1,30}?)\s*(?:\(([^)]{1,120})\))?\s*:\s*(.*)$")
# Indic scripts have no upper case, so the Latin ALL-CAPS speaker guard can't
# apply — a Devanagari/Gurmukhi/Bengali speaker header is a SHORT Indic name +
# optional (delivery) + colon; the QUOTED-dialogue requirement (below) remains
# the guard against direction lines, same as for Latin.
_SPEAKER_LINE_INDIC_RE = re.compile(
    r"^([ऀ-෿][ऀ-෿\s]{0,30}?)\s*(?:\(([^)]{1,120})\))?\s*:\s*(.*)$")
_QUOTES = "\"“”'’"

_SHOT_SIZE_KEYWORDS = (  # first match wins — order matters (extreme before close)
    ("extreme close", "extreme close-up"), ("macro", "detail insert"),
    ("detail", "detail insert"), ("insert", "detail insert"),
    ("close-up", "close-up"), ("close up", "close-up"),
    ("wide", "wide establishing"), ("establishing", "wide establishing"),
    ("aerial", "wide establishing"), ("overhead", "wide establishing"),
    ("two-shot", "medium"), ("medium", "medium"),
)
_MOTION_KEYWORDS = (
    ("locked-off", "static"), ("locked off", "static"), ("camera holds", "static"),
    ("holds steady", "static"), ("static", "static"),
    ("crash zoom", "crash zoom in"), ("whip", "whip pan"),
    ("push", "slow push-in"), ("pull back", "slow pull-back"), ("pull-back", "slow pull-back"),
    ("zoom out", "slow zoom out"), ("zoom in", "slow push-in"),
    ("pan left", "pan left"), ("pan right", "pan right"),
    ("crane", "slow crane up"), ("dolly", "slow dolly forward"), ("track", "tracking shot"),
)


def _kw(text: str, table) -> str:
    low = text.lower()
    for needle, value in table:
        if needle in low:
            return value
    return ""


def _slug(name: str) -> str:
    # \w alone mangles Indic names: combining vowel signs (matras — ा ु ी) are
    # NOT \w, so यमराज became यमर_ज. The explicit Indic block keeps them; Latin
    # behaviour is unchanged.
    return re.sub(r"[^\wऀ-෿]+", "_", name.strip().lower()).strip("_")


def _extract_dialogue(lines: list[str]):
    """Yield (speaker, delivery, line, consumed_indices) for ALL-CAPS speaker headers
    followed by a QUOTED line (same line or next). The quote requirement is what
    keeps 'TONE:' / 'CAMERA WORK:' production-note headers from reading as cast."""
    out, i = [], 0
    while i < len(lines):
        s = lines[i].strip()
        m = _SPEAKER_LINE_RE.match(s)
        if m and m.group(1).strip() != m.group(1).strip().upper():
            m = None                       # Latin rule demands ALL-CAPS
        if m is None:
            m = _SPEAKER_LINE_INDIC_RE.match(s)   # Indic has no case — quote guard applies
        if m:
            speaker, delivery, rest = m.group(1).strip(), (m.group(2) or "").strip(), m.group(3).strip()
            used = [i]
            if not rest and i + 1 < len(lines) and lines[i + 1].strip()[:1] in _QUOTES:
                j = i + 1
                quoted = []
                while j < len(lines):
                    quoted.append(lines[j].strip())
                    used.append(j)
                    if lines[j].strip()[-1:] in _QUOTES and len(" ".join(quoted)) > 1:
                        break
                    j += 1
                rest = " ".join(quoted)
            if rest[:1] in _QUOTES:
                out.append((speaker, delivery, rest.strip(_QUOTES).strip(), used))
                i = used[-1] + 1
                continue
        i += 1
    return out


def _compile_frames(brief: str) -> list[dict]:
    """Deterministically compile an authored FRAME/SCENE/SHOT script into frames[].
    One dialogue line = one shot (speaker + line verbatim); a no-dialogue block =
    one silent shot whose director_note carries the block's visual direction — the
    silent-beat image path renders from director_note, so nothing is lost."""
    # PRODUCTION NOTES / tone-palette tails are DIRECTION for the whole film, not a
    # scene — split them off and fold into the global style context.
    tail_style = ""
    pn = re.search(r"(?m)^\s*PRODUCTION NOTES\b", brief)
    if pn:
        tail_style = re.sub(r"\s+", " ", brief[pn.start():])[:300]
        brief = brief[:pn.start()]

    markers = list(_FRAME_MARKER_RE.finditer(brief))
    if len(markers) < 2:
        return []
    spans = [(m.start(), markers[k + 1].start() if k + 1 < len(markers) else len(brief))
             for k, m in enumerate(markers)]
    blocks = [brief[a:b].strip() for a, b in spans]

    # The preamble (text before the first marker) is DIRECTION, not a scene:
    # "Reference Yamraj from Image 1 (identity)… 90s mythological television look".
    # Its style rides every shot's director_note; its `Reference <Name>` lines
    # register characters who may never speak (a silent antagonist still needs
    # identity lock). Nothing hardcoded — names come from the script itself.
    preamble = brief[:markers[0].start()].strip()
    style = " ".join(s for s in (re.sub(r"\s+", " ", preamble)[:300], tail_style) if s)

    all_speakers: dict[str, str] = {}
    for m in re.finditer(r"(?i)\breference\s+([A-Z][a-zA-Z]+)", preamble):
        all_speakers.setdefault(_slug(m.group(1)), m.group(1).title())
    for block in blocks:
        for speaker, _d, line, _u in _extract_dialogue(block.splitlines()):
            all_speakers.setdefault(_slug(speaker), speaker.title())
            # A vocative inside dialogue ("…, Hanuman.") names a character who may
            # never speak themselves — you only address people, so it's a safe,
            # deterministic cast signal (place names don't get comma-addressed).
            for v in re.finditer(r",\s+([A-Z][a-z]{2,})\s*[.!?…—–-]", line):
                all_speakers.setdefault(_slug(v.group(1)), v.group(1))

    frames, n = [], 0

    def _emit(caption, note, dur, *, speaker="", delivery="", subject="", sounds=""):
        nonlocal n
        n += 1
        sid = _slug(speaker) if speaker else "narrator"
        vid = _slug(subject) if subject else (sid if speaker else "narrator")
        on_screen = vid != "narrator"
        scan = note.split("[style]")[0]   # keyword scan on the shot's OWN direction,
        f = {                             # not the global style preamble
            "frame_id":        f"f{n:02d}",
            "caption":         caption.strip(),
            "director_note":   note.strip()[:1200],
            "motion_override": _kw(scan, _MOTION_KEYWORDS),
            "photo_spec":      "ai_portrait" if on_screen else "ai_symbolic",
            "product_beat":    False,
            "uses_talent":     on_screen,
            "shot_size":       _kw(scan, _SHOT_SIZE_KEYWORDS) or "medium",
            "duration":        round(max(2.0, min(15.0, dur)), 1),
            "negative_prompt": DEFAULT_NEGATIVE,
            "continuity_lock": "",
            # Attribution is COMPILED, not detected — _cast_detected stops the
            # cast LLM pass from re-guessing (and mis-guessing) what the script
            # states. Gender/age default to adult male; the Character sheet edits.
            "speaker_id":          sid,
            "speaker_label":       all_speakers.get(sid, "Narrator"),
            "speaker_gender":      "male",
            "speaker_age_bracket": "adult",
            "visual_subject_id":          vid,
            "visual_subject_label":       all_speakers.get(vid, "Narrator"),
            "visual_subject_gender":      "male",
            "visual_subject_age_bracket": "adult",
            "_cast_detected":  True,
            "compiled":        True,
        }
        if delivery:
            f["voice_direction"] = delivery      # e.g. "ancient, gentle, certain"
        if sounds:
            f["audio_intent"] = sounds           # [Sound:] cues → the SFX driver
        frames.append(f)
        return f

    def _subject_in(text: str) -> str:
        """Most-mentioned registered cast name in `text` ('' → symbolic shot)."""
        best, best_n = "", 0
        for _sid, label in all_speakers.items():
            hits = len(re.findall(rf"(?i)\b{re.escape(label)}\b", text))
            if hits > best_n:
                best, best_n = label, hits
        return best

    # A [VISUALS:] cue this long is an authored SHOT, not stage garnish — it gets
    # its own silent beat, in order (the galleri5 breakdown promotes these too:
    # the lamp close-up, the skeletal hand, the buffalo reveal were exactly such
    # cues, and folding them into a dialogue shot's note lost them as images).
    _VISUAL_SHOT_MIN_CHARS = 60

    for block in blocks:
        sounds = "; ".join(s.strip() for s in
                           _SOUND_BRACKET_RE.findall(block) + _SOUND_LINE_RE.findall(block))
        timing = _TIMING_RE.search(block.splitlines()[0] if block.splitlines() else "")
        span = (float(timing.group(2)) - float(timing.group(1))) if timing else 0.0

        # Mark each [VISUALS:] cue as its own sentinel line so ORDER vs dialogue
        # survives the line scan; strip sound brackets (captured above).
        marked = _SOUND_BRACKET_RE.sub(" ", block)
        marked = _VISUALS_RE.sub(
            lambda m: "\n@@VISUAL@@ " + re.sub(r"\s+", " ", m.group(1)).strip() + "\n",
            marked)
        lines = marked.splitlines()
        dialogue = _extract_dialogue(lines)
        consumed = {i for _s, _d, _l, used in dialogue for i in used}
        dlg_by_start = {used[0]: (spk, dlv, line)
                        for spk, dlv, line, used in dialogue}

        note_lines = [ln.strip() for i, ln in enumerate(lines)
                      if i not in consumed and ln.strip()
                      and not ln.strip().startswith("@@VISUAL@@")
                      and ln.strip().lower() not in ("visual notes:",)]

        # Ordered events: dialogue lines and substantial visual cues, as authored.
        events, small_visuals = [], []
        for i, ln in enumerate(lines):
            s = ln.strip()
            if s.startswith("@@VISUAL@@"):
                cue = s[len("@@VISUAL@@"):].strip()
                if len(cue) >= _VISUAL_SHOT_MIN_CHARS:
                    events.append(("visual", cue))
                elif cue:
                    small_visuals.append(cue)     # garnish → shared context
            elif i in dlg_by_start:
                events.append(("dialogue", dlg_by_start[i]))

        note = "\n".join(note_lines
                         + [f"[on screen] {v}" for v in small_visuals]
                         + ([f"[style] {style}"] if style else []))

        # Delta B — deterministic cinematic grammar for shots whose author wrote
        # no camera direction (keyword scan found nothing → old behaviour was a
        # wall of "medium", galleri5's breakdown varies size by function): first
        # shot of a block establishes wide; dialogue alternates medium ↔ close-up
        # (conversation coverage); promoted visual cues read as detail inserts.
        # An AUTHORED camera note always wins — this only fills genuine gaps.
        def _grammar(kind_: str, first_in_block: bool) -> str:
            if first_in_block:
                return "wide establishing"
            if kind_ == "dialogue":
                return "close-up" if n % 2 else "medium"   # n = shots emitted so far
            return "detail insert"

        if not events:
            f0 = _emit("", note, span or 5.0, subject=_subject_in(block), sounds=sounds)
            if _kw(note.split("[style]")[0], _SHOT_SIZE_KEYWORDS) == "":
                f0["shot_size"] = _grammar("visual", True)
            continue

        per = span / len(events) if span else 0.0
        for k, (kind, payload) in enumerate(events):
            snd = sounds if k == 0 else ""
            if kind == "dialogue":
                speaker, delivery, line = payload
                dur = per or max(3.5, min(9.0, len(line.split()) / 2.0))
                gram = _grammar("dialogue", k == 0)
                f0 = _emit(line, note, dur, speaker=speaker, delivery=delivery, sounds=snd)
            else:
                vis_note = f"[on screen] {payload}\n{note}"
                gram = _grammar("visual", k == 0)
                f0 = _emit("", vis_note, per or 4.0,
                           subject=_subject_in(payload) or _subject_in(block), sounds=snd)
            if f0["shot_size"] == "medium" and \
                    _kw(f0["director_note"].split("[style]")[0], _SHOT_SIZE_KEYWORDS) == "":
                f0["shot_size"] = gram

    return frames


def plan(brief: str, *, scope: str = "general", talent: dict | None = None,
         product: dict | None = None, mood: str = "", target_seconds: int = 0) -> list[dict]:
    """
    Expand a brief into an editable frames[] list. Cached + safe.
    scope: "commerce" | "general". talent/product: optional locked-asset dicts.
    target_seconds: desired reel length (0 = auto — let the story decide the shot count,
    so a rich life story can run 2-4 min instead of a forced ~30s).
    """
    brief = (brief or "").strip()
    if not brief:
        return _fallback_frames("", scope)

    scope = scope if scope in _SCOPE_SYSTEM_PROMPTS else "general"
    key = _cache_key(brief, scope, talent, product, target_seconds)
    cache_path = PLAN_CACHE_DIR / f"{key}.json"
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text())
        except Exception:
            pass

    # COMPILE mode: an authored FRAME/SCENE/SHOT script is followed verbatim —
    # no LLM, no reinterpretation, free. Fails open to the LLM planner.
    try:
        compiled = _compile_frames(brief)
    except Exception as e:
        print(f"[ShotPlanner] compile mode failed ({e}) — falling back to LLM planning")
        compiled = []
    if compiled:
        cache_path.write_text(json.dumps(compiled))
        print(f"[ShotPlanner] compile mode: authored script → {len(compiled)} shots "
              f"(deterministic, verbatim, no LLM)")
        try:
            from agents import degradation
            degradation.report("plan", "info",
                               f"structured script compiled verbatim ({len(compiled)} shots) "
                               f"— dialogue, camera and sound cues followed exactly")
        except Exception:
            pass
        return compiled

    system = _SCOPE_SYSTEM_PROMPTS[scope]
    system += _length_directive(target_seconds)
    if mood:
        system += f"\nOverall mood: {mood}."
    # Scale the token budget with the target length — a 3-4 min reel is 30-50 shots
    # and would otherwise truncate. Auto sizes off the brief itself (_estimate_auto_shots)
    # rather than a flat guess — a long, richly-structured brief needs real headroom.
    est_shots = round(target_seconds / 4.5) if target_seconds > 0 else _estimate_auto_shots(brief)
    # 380 tok/shot (not 130, not 220): measured directly from a real truncation —
    # a 24-shot response that filled an 8800-token budget exactly averaged ~367
    # tok/shot for rich director_note content (camera/lighting/composition detail),
    # not the short-fragment captions 130 was calibrated for. That truncation was
    # SILENT: valid, parseable JSON with no error, just missing the back half of the
    # story — worse than the earlier hard-crash truncation this file already fixed,
    # since nothing told the operator content was dropped. A bigger call is far
    # cheaper than a truncated one silently losing half the story.
    max_toks = max(2500, min(28000, est_shots * 380))
    try:
        text = llm.chat(
            [{"role": "system", "content": system},
             {"role": "user", "content": _user_message(brief, scope, talent, product)}],
            json_mode=True, json_schema=_PLAN_SCHEMA,
            temperature=0.8, max_tokens=max_toks, model_tier="reasoning",
        )
        raw = llm.json_loads_lenient(text)
        frames = _to_frames(raw.get("frames", []), scope)
        if not frames:
            frames = _fallback_frames(brief, scope)
            from agents import degradation
            degradation.report("plan", "alert",
                               f"planner returned no usable shots -> {len(frames)} generic "
                               f"fallback shots (S27) — re-Plan before spending")
        cache_path.write_text(json.dumps(frames))
        print(f"[ShotPlanner] {scope}: planned {len(frames)} shots")
        return frames
    except Exception as e:
        print(f"[ShotPlanner] plan failed ({e}) — falling back to a sentence split")
        from agents import degradation
        degradation.report("plan", "alert",
                           f"planner LLM failed ({str(e)[:100]}) -> sentence-split fallback "
                           f"shots (S27) — re-Plan before spending")
        return _fallback_frames(brief, scope)
