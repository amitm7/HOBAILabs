"""Story Review — thin, DETERMINISTIC contract checks over a story-frame draft.

Catches the HARD pipeline-contract violations an LLM draft can regress on, before
any render spend. This is NOT an editorial reviewer (narrative craft is the
director brain's job) — only unambiguous failures, checked against STRUCTURED
fields where possible so there is no brittle NLP. See docs/STORY_REVIEW_PLAN.md.

Pure functions, no LLM, no network — safe to run on every draft. Reads either the
structured story-draft frame (`visual_need`/`media_query`/`verbatim_quote`/
`blocking`) or a parsed Format B frame (`photo_spec`/`director_note`) — fields are
resolved defensively so the same checks work at /story-intake and /parse-script.
"""

from __future__ import annotations

import re

# Tokens that mean a person is in the frame — used ONLY to catch the ai_symbolic
# contract violation. Keep in sync with image_generator.generate_symbolic_image,
# which forces "No people, no faces."
# Concrete person NOUNS only. Pronouns (he/she/his/her) and bare "hand" are
# deliberately excluded — they are weak signals that false-positive on object
# queries ("his laptop", "hand of a clock", "child's drawing"). Per the red-team,
# this is a soft warn, so we favour precision over recall here.
_PERSON_TOKENS = (
    r"man|men|woman|women|boy|girl|kid|child|children|person|people|figure|"
    r"silhouette|vendor|crowd|family|father|mother|son|daughter|elderly|"
    r"engineer|developer|face|faces|hands"
)
_PERSON_RE = re.compile(rf"\b(?:{_PERSON_TOKENS})\b", re.IGNORECASE)

# "face" is a person noun AND a landscape/architecture noun, and this is a story
# pipeline full of cliffs and mountains — "a cave mouth barely visible in the rock
# face" false-positived a pure Himalayan landscape beat as "describes a person" and
# told the operator to retag a frame that was already correct. Same precision-over-
# recall logic that excluded bare "hand" ("hand of a clock"): strip the known
# non-person compounds BEFORE person-matching, rather than dropping the token and
# losing "a face in the crowd". Mirrors the negated-clause strip below.
_NON_PERSON_FACE_RE = re.compile(
    r"\b(?:rock|cliff|stone|granite|ice|glacier|mountain|canyon|quarry|wall|brick|"
    r"clock|watch|coin|card|dial|north|south|east|west)[- ]faces?\b",
    re.IGNORECASE,
)


def _field(frame: dict, *names: str, default: str = "") -> str:
    for n in names:
        v = frame.get(n)
        if v not in (None, ""):
            return str(v)
    return default


def _norm(s) -> str:
    """Lowercase, strip punctuation, collapse whitespace — for substring matching."""
    return re.sub(r"[^a-z0-9 ]", "", re.sub(r"\s+", " ", str(s or "").lower())).strip()


def _has_real_pin(frame: dict) -> bool:
    ps = _field(frame, "photo_spec").strip().lower()
    if ps and not ps.startswith("ai_") and ps != "uploaded":
        return True
    return bool(_field(frame, "visual_path", "talent_ref_path", "character_ref_path"))


def contract_validate(frames: list[dict], posting_caption: str = "") -> dict:
    """Return {"ok": bool, "warnings": [{id, severity, frame_id, message, fix}]}.

    ok is False only when a hard `warn` fires; `info` items never fail the gate.
    """
    warnings: list[dict] = []

    def add(fid, cid, msg, fix, severity="warn"):
        warnings.append({"id": cid, "severity": severity, "frame_id": fid,
                         "message": msg, "fix": fix})

    # On-screen text (captions + posting caption) for the pivot-quote check.
    screen_text = _norm(" ".join(_field(f, "caption") for f in frames)
                        + " " + str(posting_caption or ""))

    for i, f in enumerate(frames, 1):
        fid = f.get("frame_id") or f"f{i:02d}"
        vneed = _field(f, "visual_need", "photo_spec").strip().lower()
        mq = _field(f, "media_query", "director_note")
        note = _field(f, "operator_note", "director_note")
        # Person/action detection runs on the MEDIA_QUERY only — that's the visual
        # intent the generator actually renders. The operator_note legitimately
        # names people for context ("replace with a real photo of Arjun"), so
        # scanning it produced false positives on object-only symbolic frames.
        # Drop NEGATED clauses before person-matching: a hardened brain emphasises
        # object-only ("...red tail lights, no people or hands"), and a naive match
        # on "people" inside "no people" would false-positive on exactly the queries
        # we want. Split on clause punctuation; keep only clauses with no negation.
        visual = " ".join(
            c for c in re.split(r"[,;]", mq)
            if not re.search(r"\b(no|without|zero|devoid|empty of)\b", c, re.IGNORECASE)
        )
        # Drop landscape/object "face" compounds ("rock face", "north face", "clock
        # face") so geology stops reading as anatomy — see _NON_PERSON_FACE_RE.
        visual = _NON_PERSON_FACE_RE.sub(" ", visual)
        is_symbolic = vneed == "ai_symbolic"
        is_real = vneed in ("real_photo_preferred", "real_video_preferred")

        # R1 — ai_symbolic must be object-only (the generator forbids people/faces).
        if is_symbolic and _PERSON_RE.search(visual):
            add(fid, "symbolic_people_mismatch",
                "ai_symbolic beat describes a person, but generate_symbolic_image forces "
                "'no people, no faces' — the person silently drops (script lies to the operator).",
                "Retag visual_need=real_photo_preferred, or rewrite the query to objects/settings only.")

        # R2 — real media preferred but nothing pinned and no fallback stated.
        if is_real and not _has_real_pin(f) and "symbolic" not in _norm(note):
            add(fid, "real_no_pin",
                "real media preferred but no asset is pinned and no fallback is stated — "
                "this beat will hallucinate or fail at render.",
                "Pin a real photo/video, or state an object-only ai_symbolic fallback in the note.")

        # R3 — real-person/likeness beat should carry a consent path (§5).
        if is_real and _PERSON_RE.search(visual) and "consent" not in _norm(note):
            add(fid, "consent_ungated",
                "real-person beat without a consent note — §5 requires consent before a "
                "real-person/likeness render.",
                "Add the consent path in the note; the render-time gate enforces it.",
                severity="info")

        # R4 — a flagged pivot quote must actually appear on screen (structured field).
        vq = _norm(_field(f, "verbatim_quote"))
        if vq:
            probe = " ".join(vq.split()[:8])  # opening of the quote tolerates on-screen trimming
            if probe and probe not in screen_text:
                add(fid, "pivot_quote_missing",
                    "a verbatim_quote is set but its words never appear in a caption or text card — "
                    "the pivot is teased, not shown.",
                    "Put the quote on this frame's caption or a dedicated text_card.")

        # NOTE: detecting "multi-person action beat needs blocking" deterministically
        # is brittle NLP (body-parts counted as people, "hands" reads as motion) — it
        # belongs in the director brain / an LLM critique, not here. The `blocking`
        # field is still produced (prevention); we do not regex-enforce it.

    return {"ok": not any(w["severity"] == "warn" for w in warnings), "warnings": warnings}
