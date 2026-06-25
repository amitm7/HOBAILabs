"""STR-2 golden tests — the thin contract validator + the truncation fix.

The "bad" draft reproduces the four "Last Train Home" failures; the "fixed" draft
must pass clean. See docs/STORY_REVIEW_PLAN.md.
"""

from agents.story_review import contract_validate
from agents.growth import draft_to_format_b, _clean_line


# ── Fixtures: the Last Train Home draft, broken and fixed ──────────────────────

BAD_FRAMES = [
    {"frame_id": "f01", "visual_need": "ai_symbolic",
     "caption": "He missed the last train.",
     "media_query": "Lone figure on an empty platform, rainy night",   # person in symbolic → R1
     "operator_note": "", "verbatim_quote": "", "blocking": ""},
    {"frame_id": "f04", "visual_need": "ai_symbolic",
     "caption": "The chai vendor said something he could not forget.",
     "media_query": "Elderly man handing a clay cup of chai to a younger man",  # R1
     "operator_note": "",
     "verbatim_quote": "People spend their lives chasing the next train.",  # set but never on screen → R4
     "blocking": ""},
    {"frame_id": "f08", "visual_need": "real_photo_preferred",
     "caption": "She ran to him with the same drawing.",
     "media_query": "child runs toward father at the door",  # 2 people + motion → R5; real person, no consent → R3
     "operator_note": "",                                    # no pin, no fallback → R2
     "verbatim_quote": "", "blocking": ""},
]

GOOD_FRAMES = [
    {"frame_id": "f01", "visual_need": "ai_symbolic",
     "caption": "He missed the last train.",
     "media_query": "Red tail lights fading into a dark tunnel, rain on an empty platform bench",
     "operator_note": "", "verbatim_quote": "", "blocking": ""},
    {"frame_id": "f04", "visual_need": "ai_symbolic",
     "caption": "People spend their lives chasing the next train.",   # quote now on screen → R4 ok
     "media_query": "Steaming clay chai cup on a worn wooden bench, dim yellow platform light",
     "operator_note": "",
     "verbatim_quote": "People spend their lives chasing the next train.",
     "blocking": ""},
    {"frame_id": "f08", "visual_need": "real_photo_preferred",
     "caption": "She ran to him with the same drawing.",
     "media_query": "Arjun at the door, child running toward him",
     "operator_note": ("Use real photo only with Arjun's consent; child face needs parental "
                       "consent. If no media, fall back to ai_symbolic drawing-on-table (object only)."),
     "photo_spec": "arjun_homecoming.jpg",                            # pinned → R2 ok
     "verbatim_quote": "",
     "blocking": "daughter runs toward father at the open door; he stands, arms opening; drawing in her hand"},
]


def _ids(result):
    return {(w["frame_id"], w["id"]) for w in result["warnings"]}


def test_bad_draft_flags_all_known_issues():
    r = contract_validate(BAD_FRAMES)
    ids = _ids(r)
    assert ("f01", "symbolic_people_mismatch") in ids
    assert ("f04", "symbolic_people_mismatch") in ids
    assert ("f04", "pivot_quote_missing") in ids
    assert ("f08", "real_no_pin") in ids
    assert ("f08", "consent_ungated") in ids
    assert r["ok"] is False


def test_fixed_draft_passes_clean():
    r = contract_validate(GOOD_FRAMES)
    hard = [w for w in r["warnings"] if w["severity"] == "warn"]
    assert hard == [], hard
    assert r["ok"] is True


def test_long_blocking_note_survives_round_trip():
    long_block = ("daughter runs toward father at the open front door while he stands with arms "
                  "opening; she is holding the same crayon drawing that says Waiting for Papa; warm "
                  "interior light behind them; camera handheld, slow motion on the run toward camera")
    frames = [{"role": "outcome", "caption": "She ran to him.", "visual_need": "real_photo_preferred",
               "media_query": "child runs to father", "motion_hint": "handheld", "duration": 6.0,
               "confidence": "high", "operator_note": "", "verbatim_quote": "", "blocking": long_block}]
    script = draft_to_format_b(frames, "caption")
    # The old 180-char cap cut this mid-word; the full note must now survive.
    assert "Waiting for Papa" in script
    assert "slow motion on the run toward camera" in script


def test_clean_line_never_cuts_mid_word():
    assert _clean_line("alpha beta gamma delta", 9) == "alpha…"   # cut at the space, not inside "gamma"
    assert _clean_line("short text", 200) == "short text"          # under the cap → untouched
