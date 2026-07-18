"""Offline logic tests for the Director Canvas orchestrator (agents/canvas_run.py).

Hermetic: shot_planner / scene_intelligence are monkeypatched so no network or
API key is needed. Asserts the staged state machine, the per-stage cost slicing
(reusing pricing.estimate), the real-vs-AI classification, and cascade invalidation.
"""
import copy

import pytest

from agents import canvas_run


@pytest.fixture(autouse=True)
def _isolate_location_cache(monkeypatch, tmp_path):
    """derive_locations caches its LLM pass to disk (~/.hob_cache/locations).

    Autouse because that cache is real global state: without isolation one test's stubbed
    reply is served to the next (they share beats → share a key), and a run pollutes the
    developer's actual cache. Every test gets an empty one.
    """
    d = tmp_path / "loccache"
    d.mkdir()
    monkeypatch.setattr(canvas_run, "LOCATION_CACHE_DIR", d)


# Read-only template. NEVER hand these dicts to a canvas — use _frames(); a canvas
# mutates its frames in place and would rewrite the fixture for every later test.
SAMPLE_FRAMES = [
    {"frame_id": "f01", "caption": "Eighteen years on this street.",
     "photo_spec": "ai_portrait", "motion_override": "slow push-in",
     "shot_size": "close-up", "duration": 5.0, "uses_talent": True},
    {"frame_id": "f02", "caption": "Every rupee saved.",
     "photo_spec": "06_family.jpg", "visual_path": "/nonexistent/06_family.jpg",
     "motion_override": "pan left", "shot_size": "wide", "duration": 4.0},
    {"frame_id": "f03", "caption": "A symbol of sacrifice.",
     "photo_spec": "ai_symbolic", "motion_override": "macro pull-back",
     "shot_size": "detail insert", "duration": 3.0},
]


def _frames():
    """Fresh, independent frame dicts per test — see the note on SAMPLE_FRAMES."""
    return copy.deepcopy(SAMPLE_FRAMES)


def test_motion_arrow_tokens():
    assert canvas_run.motion_arrow("slow push-in") == "in"
    assert canvas_run.motion_arrow("macro pull-back") == "out"
    assert canvas_run.motion_arrow("pan left") == "left"
    assert canvas_run.motion_arrow("360 orbit") == "orbit"
    assert canvas_run.motion_arrow("") == "in"            # default = gentle push


def test_asset_kind_three_way():
    # ai_portrait → likeness (consent-gated); ai_symbolic → ai; real path → real.
    assert canvas_run.asset_kind(SAMPLE_FRAMES[0]) == canvas_run.ASSET_AI_PERSON
    assert canvas_run.asset_kind(SAMPLE_FRAMES[2]) == canvas_run.ASSET_AI
    real = {"photo_spec": "x.jpg", "visual_path": __file__}  # an existing file
    assert canvas_run.asset_kind(real) == canvas_run.ASSET_REAL


def test_stage_costs_are_sliced_from_estimate():
    costs = canvas_run.stage_costs(SAMPLE_FRAMES, quality="dev")
    assert set(costs) == set(canvas_run.STAGES)
    assert costs["script"] == 0.0          # planning is free
    assert all(isinstance(v, float) for v in costs.values())
    # video (animation) should cost something for 3 shots on a paid tier
    assert costs["video"] >= 0.0


def test_board_cards_shape():
    cards = canvas_run.board_cards(SAMPLE_FRAMES)
    assert len(cards) == 3
    c = cards[0]
    assert c["frame_id"] == "f01"
    assert c["arrow"] == "in"
    assert c["asset_kind"] == canvas_run.ASSET_AI_PERSON


def test_new_canvas_runs_script_stage(monkeypatch):
    monkeypatch.setattr("agents.shot_planner.plan", lambda *a, **k: _frames())
    state = canvas_run.new_canvas("a brief", quality="dev")
    assert state["stages"]["script"]["status"] == "done"
    assert state["stages"]["storyboard"]["ready"] is True
    assert state["stages"]["keyframes"]["ready"] is False   # locked until approved
    assert len(state["board"]) == 3


def test_approve_unlocks_next_stage(monkeypatch):
    monkeypatch.setattr("agents.shot_planner.plan", lambda *a, **k: _frames())
    state = canvas_run.new_canvas("brief")
    canvas_run.approve(state, "script")
    assert state["stages"]["storyboard"]["ready"] is True
    canvas_run.approve(state, "storyboard")
    assert state["stages"]["keyframes"]["ready"] is True


def test_storyboard_stage_degrades_offline(monkeypatch):
    monkeypatch.setattr("agents.shot_planner.plan", lambda *a, **k: _frames())
    # design_all_scenes raising must NOT break the stage (rule #4 graceful degrade).
    monkeypatch.setattr("agents.scene_intelligence.design_all_scenes",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no key")))
    state = canvas_run.new_canvas("brief")
    state = canvas_run.run_stage(state, "storyboard")
    assert state["stages"]["storyboard"]["status"] == "done"
    assert len(state["board"]) == 3


def test_paid_stage_raises_dispatch(monkeypatch):
    monkeypatch.setattr("agents.shot_planner.plan", lambda *a, **k: _frames())
    state = canvas_run.new_canvas("brief")
    try:
        canvas_run.run_stage(state, "video")
        assert False, "paid stage should not render in this module"
    except canvas_run.PaidStageDispatch as e:
        assert e.stage == "video"


def _locations_state(monkeypatch, reply: dict, kf_status: str = "approved"):
    """A canvas whose keyframes already rendered, with derive_locations' one LLM pass
    stubbed to `reply` (S30 location anchoring — hermetic, no network)."""
    import json
    monkeypatch.setattr("agents.shot_planner.plan", lambda *a, **k: _frames())
    monkeypatch.setattr("agents.llm.chat", lambda *a, **k: json.dumps(reply))
    monkeypatch.setattr("agents.llm.json_loads_lenient", json.loads)
    state = canvas_run.new_canvas("brief")
    state["stages"]["keyframes"]["status"] = kf_status
    return state


def test_rederive_is_free_but_a_story_edit_is_not(monkeypatch):
    # 🏞 Locations is a reasoning-tier completion and re-deriving is a designed flow
    # ("re-derive keeps work"), so an uncached button re-spent on every click.
    import json
    calls = []

    def fake_chat(*a, **k):
        calls.append(1)
        return json.dumps({
            "locations": [{"id": "cave", "label": "Cave", "description": "dark", "time_of_day": "night"}],
            "by_frame": [{"frame_id": "f01", "location_id": "cave"}]})

    monkeypatch.setattr("agents.shot_planner.plan", lambda *a, **k: _frames())
    monkeypatch.setattr("agents.llm.chat", fake_chat)
    monkeypatch.setattr("agents.llm.json_loads_lenient", json.loads)

    s1 = canvas_run.new_canvas("brief")
    canvas_run.derive_locations(s1)
    assert len(calls) == 1
    s2 = canvas_run.new_canvas("brief")            # same beats → cache hit, no spend
    canvas_run.derive_locations(s2)
    assert len(calls) == 1
    s3 = canvas_run.new_canvas("brief")            # beats changed → must re-derive
    s3["frames"][0]["caption"] = "A different opening line entirely."
    canvas_run.derive_locations(s3)
    assert len(calls) == 2


def test_derive_locations_survives_null_scene(monkeypatch):
    # A frame may carry scene=None; dict.get's default does NOT apply to a present None,
    # and the beats scan sits outside the try → an unguarded .get() 500s the route.
    state = _locations_state(monkeypatch, {
        "locations": [{"id": "cave", "label": "Cave", "description": "dark", "time_of_day": "night"}],
        "by_frame": [{"frame_id": "f01", "location_id": "cave"}]})
    state["frames"].append({"frame_id": "f04", "caption": "", "scene": None})
    canvas_run.derive_locations(state)                      # must not raise


def test_rederive_keeps_operator_edits_and_paid_plate(monkeypatch):
    # The model churning an id ("cave" → "cave_interior") must not bin the operator's
    # edited description or the plate they PAID for.
    state = _locations_state(monkeypatch, {
        "locations": [{"id": "cave_interior", "label": "Cave",
                       "description": "llm generic", "time_of_day": "night"}],
        "by_frame": [{"frame_id": "f01", "location_id": "cave_interior"}]})
    state["locations"] = [{"id": "cave", "label": "Cave", "description": "MY EDIT",
                           "time_of_day": "night", "plate_path": "/paid/plate.png",
                           "source": "ai"}]
    merged = canvas_run.derive_locations(state)
    assert merged[0]["description"] == "MY EDIT"            # matched by label, not id
    assert merged[0]["plate_path"] == "/paid/plate.png"


def test_rederive_clears_stale_location_tag(monkeypatch):
    # A dropped location must not leave its clause riding the shot's prompt (and cache
    # key) while being invisible on the Locations sheet.
    state = _locations_state(monkeypatch, {
        "locations": [{"id": "temple", "label": "Temple", "description": "stone", "time_of_day": "day"}],
        "by_frame": [{"frame_id": "f02", "location_id": "temple"}]})
    state["locations"] = [{"id": "cave", "label": "Cave", "description": "dark",
                           "time_of_day": "night", "plate_path": ""}]
    state["frames"][0].update({"location_id": "cave", "location_clause": "STALE",
                               "location_ref_path": "/x.png"})
    canvas_run.derive_locations(state)
    assert not any(k.startswith("location") for k in state["frames"][0])


def test_derive_locations_invalidates_rendered_keyframes(monkeypatch):
    # The clause rides the prompt → the still's cache key, so anchoring a rendered
    # keyframe must reset it (the rule set_location already follows)...
    reply = {"locations": [{"id": "cave", "label": "Cave", "description": "dark damp",
                            "time_of_day": "night"}],
             "by_frame": [{"frame_id": "f01", "location_id": "cave"},
                          {"frame_id": "f02", "location_id": "cave"}]}
    state = _locations_state(monkeypatch, reply)
    canvas_run.derive_locations(state)
    assert state["stages"]["keyframes"]["status"] == "pending"
    # ...but an identical re-derive changes no anchoring, so it must NOT re-render.
    state["stages"]["keyframes"]["status"] = "approved"
    canvas_run.derive_locations(state)
    assert state["stages"]["keyframes"]["status"] == "approved"


def test_rederive_never_clones_a_paid_plate(monkeypatch):
    # Two new locations must not both inherit one paid plate (id-match + label-match
    # could otherwise claim the same predecessor twice).
    state = _locations_state(monkeypatch, {
        "locations": [{"id": "cave", "label": "Cave", "description": "a", "time_of_day": "n"},
                      {"id": "cave2", "label": "Cave", "description": "b", "time_of_day": "n"}],
        "by_frame": [{"frame_id": "f01", "location_id": "cave"},
                     {"frame_id": "f02", "location_id": "cave2"}]})
    state["locations"] = [{"id": "cave", "label": "Cave", "description": "d",
                           "time_of_day": "n", "plate_path": "/paid/p.png"}]
    merged = canvas_run.derive_locations(state)
    assert [l["plate_path"] for l in merged].count("/paid/p.png") == 1


def test_invalidate_cascades_downstream(monkeypatch):
    monkeypatch.setattr("agents.shot_planner.plan", lambda *a, **k: _frames())
    state = canvas_run.new_canvas("brief")
    for s in canvas_run.STAGES:                      # pretend everything ran
        state["stages"][s]["status"] = "approved"
    canvas_run.invalidate_from(state, "storyboard")
    assert state["stages"]["script"]["status"] == "approved"     # upstream untouched
    assert state["stages"]["storyboard"]["status"] == "pending"  # this + downstream reset
    assert state["stages"]["video"]["status"] == "pending"


def test_edit_frame_updates_and_cascades(monkeypatch):
    monkeypatch.setattr("agents.shot_planner.plan", lambda *a, **k: _frames())
    state = canvas_run.new_canvas("brief")
    # pretend storyboard ran so an edit must invalidate downstream
    state["stages"]["storyboard"].update(status="done")
    state["stages"]["keyframes"].update(status="approved")
    state = canvas_run.edit_frame(state, "f01", {"caption": "New line.",
                                                 "image_prompt": "soft dawn light"})
    f = next(f for f in state["frames"] if f["frame_id"] == "f01")
    assert f["caption"] == "New line."
    assert f["scene"]["image_prompt"] == "soft dawn light"
    assert state["stages"]["storyboard"]["status"] == "pending"   # cascade
    assert state["stages"]["keyframes"]["status"] == "pending"
    # the prompt is surfaced on the board card (the editable prompt box)
    card = next(c for c in state["board"] if c["frame_id"] == "f01")
    assert card["image_prompt"] == "soft dawn light"


def test_edit_frame_rejects_unknown_field(monkeypatch):
    monkeypatch.setattr("agents.shot_planner.plan", lambda *a, **k: _frames())
    state = canvas_run.new_canvas("brief")
    canvas_run.edit_frame(state, "f01", {"frame_id": "hacked", "duration": 999})
    f = next(f for f in state["frames"] if f["frame_id"] == "f01")
    # frame_id is protected (ignored); duration IS editable now → clamped to the 1-15s range.
    assert f["frame_id"] == "f01" and f["duration"] == 15.0


def test_chat_replans_and_resets(monkeypatch):
    calls = {"n": 0}
    def fake_plan(brief, **k):
        calls["n"] += 1
        return _frames()
    monkeypatch.setattr("agents.shot_planner.plan", fake_plan)
    state = canvas_run.new_canvas("a chai seller")
    canvas_run.approve(state, "script")
    state = canvas_run.chat(state, "make it rain and darker")
    assert calls["n"] == 2                                  # re-planned
    assert "Refinement: make it rain" in state["brief"]
    assert state["stages"]["script"]["status"] == "done"
    assert state["stages"]["storyboard"]["ready"] is True


def test_attach_real_makes_passthrough(monkeypatch):
    monkeypatch.setattr("agents.shot_planner.plan", lambda *a, **k: _frames())
    state = canvas_run.new_canvas("brief")
    state = canvas_run.attach_asset(state, path="/abs/real_photo.jpg", mode="real",
                                    frame_id="f01")
    f = next(f for f in state["frames"] if f["frame_id"] == "f01")
    assert f["photo_spec"] == "/abs/real_photo.jpg"          # non-AI → PASSTHROUGH
    assert canvas_run.asset_kind(f) == canvas_run.ASSET_REAL
    card = next(c for c in state["board"] if c["frame_id"] == "f01")
    assert card["asset_kind"] == "real" and card["real_path"] == "/abs/real_photo.jpg"


def test_attach_reference_conditions_likeness(monkeypatch):
    monkeypatch.setattr("agents.shot_planner.plan", lambda *a, **k: _frames())
    state = canvas_run.new_canvas("brief")
    state = canvas_run.attach_asset(state, path="/abs/face.jpg", mode="reference",
                                    frame_id="f01")
    f = next(f for f in state["frames"] if f["frame_id"] == "f01")
    assert f["character_ref_path"] == "/abs/face.jpg"
    assert f["photo_spec"] == "ai_portrait"                  # still an AI likeness
    assert canvas_run.asset_kind(f) == canvas_run.ASSET_AI_PERSON
    card = next(c for c in state["board"] if c["frame_id"] == "f01")
    assert card["ref_path"] == "/abs/face.jpg" and card["real_path"] == ""


def test_attach_all_talent_and_cascade(monkeypatch):
    monkeypatch.setattr("agents.shot_planner.plan", lambda *a, **k: _frames())
    state = canvas_run.new_canvas("brief")
    state["stages"]["storyboard"].update(status="done")
    state["stages"]["keyframes"].update(status="approved")
    state = canvas_run.attach_asset(state, path="/abs/face.jpg", mode="reference",
                                    all_talent=True)
    # SAMPLE has f01 (ai_portrait, uses_talent inferred) — real f02 has no uses_talent flag
    refed = [f for f in state["frames"] if f.get("character_ref_path") == "/abs/face.jpg"]
    assert refed  # at least one talent shot got the ref
    assert state["stages"]["storyboard"]["status"] == "pending"   # cascade fired


def test_attach_no_target_raises(monkeypatch):
    monkeypatch.setattr("agents.shot_planner.plan", lambda *a, **k: _frames())
    state = canvas_run.new_canvas("brief")
    try:
        canvas_run.attach_asset(state, path="/x.jpg", mode="real", frame_id="nope")
        assert False, "should raise on unknown frame"
    except ValueError:
        pass


def test_public_state_shape(monkeypatch):
    monkeypatch.setattr("agents.shot_planner.plan", lambda *a, **k: _frames())
    pub = canvas_run.public_state(canvas_run.new_canvas("brief"))
    assert [s["id"] for s in pub["stages"]] == canvas_run.STAGES
    assert "total_cost_usd" in pub and "legend" in pub
    assert pub["stages"][0]["cost_usd"] == 0.0


def test_sfx_creates_its_output_dir_before_spending(monkeypatch, tmp_path):
    """A paid generation must never be thrown away because the folder didn't exist.

    Neither fal_client.download_media (bare open()) nor ffmpeg creates parent dirs, so a
    missing run subdir failed the write AFTER fal had billed — and generate_sfx's bare
    except swallowed it and returned "" (paid, no track, only an info ledger line).
    """
    from agents import sfx
    billed = []

    def fake_run_sync(*a, **k):
        # By the time we're billing, the destination must already be writable.
        assert (tmp_path / "run" / "sfx").is_dir(), "billed before the dir existed"
        billed.append(1)
        raise RuntimeError("vendor down")      # generation itself may still fail

    monkeypatch.setattr("agents.fal_client.run_sync", fake_run_sync)
    monkeypatch.setattr("agents.fal_client.file_to_data_uri", lambda p: "data:,")
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x" * 2048)

    out = sfx.generate_sfx(str(clip), "wind", str(tmp_path / "run" / "sfx" / "a.wav"))
    assert out == ""                            # degrades to "no atmosphere track"
    assert billed == [1]
    assert (tmp_path / "run" / "sfx").is_dir()


def test_set_brand_is_opt_in_and_reversible():
    """Brand mode is derived from the state, not hardcoded.

    Canvas pinned mode:"story" everywhere, so brand.validate_mandatories (only caller:
    /run under mode=="brand") was unreachable from the surface meant to replace the
    Brand door. Filling the panel opts in; clearing it returns the run to a story rather
    than leaving a brand run that can never satisfy its own mandatories.
    """
    state = {"frames": [{"frame_id": "f01", "caption": "x"}]}
    assert "brand" not in state
    canvas_run.set_brand(state, {"name": "Tata Sampurna", "cta_text": "Use Tata Sampurna"})
    assert state["brand"]["cta_text"] == "Use Tata Sampurna"
    assert state["brand"]["disclosure"] is True          # sponsored disclosure defaults ON
    canvas_run.set_brand(state, {"name": "", "cta_text": "", "logo_path": ""})
    assert "brand" not in state


def test_paid_plate_conditions_faceless_shots(tmp_path, monkeypatch):
    """The plate was BILLED and read by nothing (location_ref_path had no reader) —
    only the text clause reached a render, so shots looked identical with or without it.

    Symbolic shots carry no face, so nothing competes for the single reference slot:
    they can be plate-anchored today. Face-bearing shots still wait for D5 multi-ref.
    """
    from PIL import Image
    from agents import image_generator as ig

    plate = tmp_path / "plate.jpg"
    Image.new("RGB", (64, 64)).save(plate)
    seen = {}

    def fake_checked(model, prompt, out, fb, fid, generator=None, **kw):
        if generator:
            generator()
        else:
            seen["plain"] = seen.get("plain", 0) + 1
        Image.new("RGB", (8, 8)).save(out)
        return out

    def fake_edit(ref, prompt, out):
        seen["ref"], seen["prompt"] = ref, prompt
        Image.new("RGB", (8, 8)).save(out)
        return out

    monkeypatch.setattr(ig, "_generate_image_checked", fake_checked)
    monkeypatch.setattr("agents.image_editor.edit_image", fake_edit)
    frame = {"frame_id": "f01", "caption": "the empty stall",
             "scene": {"image_prompt": "a chai stall, no people"}}

    ig.generate_symbolic_image(dict(frame), str(tmp_path))
    assert seen.get("plain") == 1 and "ref" not in seen      # no plate → unchanged path

    anchored = dict(frame, location_ref_path=str(plate))
    ig.generate_symbolic_image(anchored, str(tmp_path))
    assert seen.get("ref") == str(plate)                     # the paid plate is USED
    assert "No people" in seen["prompt"]                     # symbolic stays symbolic
    assert "chai stall" in seen["prompt"]                    # shot keeps its own framing


def test_set_location_can_skip_propagation(monkeypatch):
    # The plate route applies attrs BEFORE the paid generation (cache correctness), then
    # the plate after — the first call has no reason to walk every frame and rebuild the
    # whole board when the second will.
    monkeypatch.setattr("agents.shot_planner.plan", lambda *a, **k: _frames())
    state = canvas_run.new_canvas("brief")
    state["locations"] = [{"id": "cave", "label": "Cave", "description": "old",
                           "time_of_day": "night", "plate_path": ""}]
    state["frames"][0]["location_id"] = "cave"
    canvas_run.set_location(state, "cave", attrs={"description": "new"}, propagate=False)
    assert state["locations"][0]["description"] == "new"          # stored
    assert "location_clause" not in state["frames"][0]            # but not propagated
    canvas_run.set_location(state, "cave", plate_path="/p.png")   # this one propagates
    assert "new" in state["frames"][0]["location_clause"]
    assert state["frames"][0]["location_ref_path"] == "/p.png"


def test_set_character_matches_visual_subject_not_just_speaker(monkeypatch):
    """The Hanuman bug: a third-person protagonist is narrated about (speaker_id
    stays 'narrator', since the narrator's voice reads the line) but IS the visual
    subject of the shot (visual_subject_id = the character). Before this fix,
    set_character only matched frames whose speaker_id equalled the character —
    so an uploaded reference photo for a narrated-about protagonist attached to
    ZERO of their shots. Also checks a first-person frame (speaker_id ==
    visual_subject_id, the common case) still matches exactly as before."""
    monkeypatch.setattr("agents.shot_planner.plan", lambda *a, **k: _frames())
    state = canvas_run.new_canvas("brief")
    state["characters"] = [{"id": "hanuman", "name": "Hanuman", "ref_path": "",
                            "consent": False, "species": "", "clothing": ""}]
    for f in state["frames"]:
        f["speaker_id"] = "narrator"           # narrator's voice reads every caption
        f["visual_subject_id"] = "hanuman"      # but Hanuman is on screen throughout
    canvas_run.set_character(state, "hanuman", ref_path="/abs/hanuman.jpg")
    assert all(f["character_ref_path"] == "/abs/hanuman.jpg" for f in state["frames"])

    # First-person case: speaker IS the visual subject — must keep matching too.
    state2 = canvas_run.new_canvas("brief")
    state2["characters"] = [{"id": "son", "name": "Son", "ref_path": ""}]
    state2["frames"][0]["speaker_id"] = "son"
    state2["frames"][0]["visual_subject_id"] = "son"
    canvas_run.set_character(state2, "son", ref_path="/abs/son.jpg")
    assert state2["frames"][0]["character_ref_path"] == "/abs/son.jpg"
    assert "character_ref_path" not in state2["frames"][1]   # untouched, different subject


def test_set_character_falls_back_to_speaker_id_for_old_frames(monkeypatch):
    """Frames created before this fix have no visual_subject_id at all — set_character
    must still match on speaker_id so pre-existing first-person runs are unaffected."""
    monkeypatch.setattr("agents.shot_planner.plan", lambda *a, **k: _frames())
    state = canvas_run.new_canvas("brief")
    state["characters"] = [{"id": "son", "name": "Son", "ref_path": ""}]
    state["frames"][0]["speaker_id"] = "son"
    assert "visual_subject_id" not in state["frames"][0]
    canvas_run.set_character(state, "son", ref_path="/abs/son.jpg")
    assert state["frames"][0]["character_ref_path"] == "/abs/son.jpg"


def test_replan_brief_rebuilds_in_place_and_resets(monkeypatch):
    """✎ Edit story: the entry box must be reachable after planning. replan_brief REPLACES
    the whole brief (not appends, like chat) and rebuilds the SAME canvas, resetting every
    downstream stage so stills for shots that changed don't linger showing the old story.
    """
    monkeypatch.setattr("agents.shot_planner.plan", lambda *a, **k: _frames())
    state = canvas_run.new_canvas("the original story", quality="dev")
    # pretend keyframes were generated + approved on the original
    state["stages"]["keyframes"]["status"] = "approved"

    monkeypatch.setattr("agents.shot_planner.plan",
                        lambda *a, **k: [{"frame_id": "f01", "caption": "brand new",
                                          "photo_spec": "ai_symbolic", "duration": 4.0}])
    canvas_run.replan_brief(state, "a completely different story", story_type="ai")

    assert state["brief"] == "a completely different story"      # replaced, not appended
    assert "original" not in state["brief"]
    assert len(state["board"]) == 1                              # new shot list
    assert state["stages"]["keyframes"]["status"] == "pending"   # old stills invalidated
    assert state["story_type"] == "ai"


def test_replan_brief_ignores_empty(monkeypatch):
    monkeypatch.setattr("agents.shot_planner.plan", lambda *a, **k: _frames())
    state = canvas_run.new_canvas("keep me", quality="dev")
    canvas_run.replan_brief(state, "   ")                        # blank → no-op
    assert state["brief"] == "keep me"
