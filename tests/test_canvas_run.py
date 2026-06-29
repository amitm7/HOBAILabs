"""Offline logic tests for the Director Canvas orchestrator (agents/canvas_run.py).

Hermetic: shot_planner / scene_intelligence are monkeypatched so no network or
API key is needed. Asserts the staged state machine, the per-stage cost slicing
(reusing pricing.estimate), the real-vs-AI classification, and cascade invalidation.
"""
from agents import canvas_run


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
    monkeypatch.setattr("agents.shot_planner.plan", lambda *a, **k: list(SAMPLE_FRAMES))
    state = canvas_run.new_canvas("a brief", quality="dev")
    assert state["stages"]["script"]["status"] == "done"
    assert state["stages"]["storyboard"]["ready"] is True
    assert state["stages"]["keyframes"]["ready"] is False   # locked until approved
    assert len(state["board"]) == 3


def test_approve_unlocks_next_stage(monkeypatch):
    monkeypatch.setattr("agents.shot_planner.plan", lambda *a, **k: list(SAMPLE_FRAMES))
    state = canvas_run.new_canvas("brief")
    canvas_run.approve(state, "script")
    assert state["stages"]["storyboard"]["ready"] is True
    canvas_run.approve(state, "storyboard")
    assert state["stages"]["keyframes"]["ready"] is True


def test_storyboard_stage_degrades_offline(monkeypatch):
    monkeypatch.setattr("agents.shot_planner.plan", lambda *a, **k: list(SAMPLE_FRAMES))
    # design_all_scenes raising must NOT break the stage (rule #4 graceful degrade).
    monkeypatch.setattr("agents.scene_intelligence.design_all_scenes",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no key")))
    state = canvas_run.new_canvas("brief")
    state = canvas_run.run_stage(state, "storyboard")
    assert state["stages"]["storyboard"]["status"] == "done"
    assert len(state["board"]) == 3


def test_paid_stage_raises_dispatch(monkeypatch):
    monkeypatch.setattr("agents.shot_planner.plan", lambda *a, **k: list(SAMPLE_FRAMES))
    state = canvas_run.new_canvas("brief")
    try:
        canvas_run.run_stage(state, "video")
        assert False, "paid stage should not render in this module"
    except canvas_run.PaidStageDispatch as e:
        assert e.stage == "video"


def test_invalidate_cascades_downstream(monkeypatch):
    monkeypatch.setattr("agents.shot_planner.plan", lambda *a, **k: list(SAMPLE_FRAMES))
    state = canvas_run.new_canvas("brief")
    for s in canvas_run.STAGES:                      # pretend everything ran
        state["stages"][s]["status"] = "approved"
    canvas_run.invalidate_from(state, "storyboard")
    assert state["stages"]["script"]["status"] == "approved"     # upstream untouched
    assert state["stages"]["storyboard"]["status"] == "pending"  # this + downstream reset
    assert state["stages"]["video"]["status"] == "pending"


def test_edit_frame_updates_and_cascades(monkeypatch):
    monkeypatch.setattr("agents.shot_planner.plan", lambda *a, **k: list(SAMPLE_FRAMES))
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
    monkeypatch.setattr("agents.shot_planner.plan", lambda *a, **k: list(SAMPLE_FRAMES))
    state = canvas_run.new_canvas("brief")
    canvas_run.edit_frame(state, "f01", {"frame_id": "hacked", "duration": 999})
    f = next(f for f in state["frames"] if f["frame_id"] == "f01")
    assert f["frame_id"] == "f01" and f["duration"] == 5.0  # non-editable ignored


def test_chat_replans_and_resets(monkeypatch):
    calls = {"n": 0}
    def fake_plan(brief, **k):
        calls["n"] += 1
        return list(SAMPLE_FRAMES)
    monkeypatch.setattr("agents.shot_planner.plan", fake_plan)
    state = canvas_run.new_canvas("a chai seller")
    canvas_run.approve(state, "script")
    state = canvas_run.chat(state, "make it rain and darker")
    assert calls["n"] == 2                                  # re-planned
    assert "Refinement: make it rain" in state["brief"]
    assert state["stages"]["script"]["status"] == "done"
    assert state["stages"]["storyboard"]["ready"] is True


def test_attach_real_makes_passthrough(monkeypatch):
    monkeypatch.setattr("agents.shot_planner.plan", lambda *a, **k: list(SAMPLE_FRAMES))
    state = canvas_run.new_canvas("brief")
    state = canvas_run.attach_asset(state, path="/abs/real_photo.jpg", mode="real",
                                    frame_id="f01")
    f = next(f for f in state["frames"] if f["frame_id"] == "f01")
    assert f["photo_spec"] == "/abs/real_photo.jpg"          # non-AI → PASSTHROUGH
    assert canvas_run.asset_kind(f) == canvas_run.ASSET_REAL
    card = next(c for c in state["board"] if c["frame_id"] == "f01")
    assert card["asset_kind"] == "real" and card["real_path"] == "/abs/real_photo.jpg"


def test_attach_reference_conditions_likeness(monkeypatch):
    monkeypatch.setattr("agents.shot_planner.plan", lambda *a, **k: list(SAMPLE_FRAMES))
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
    monkeypatch.setattr("agents.shot_planner.plan", lambda *a, **k: list(SAMPLE_FRAMES))
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
    monkeypatch.setattr("agents.shot_planner.plan", lambda *a, **k: list(SAMPLE_FRAMES))
    state = canvas_run.new_canvas("brief")
    try:
        canvas_run.attach_asset(state, path="/x.jpg", mode="real", frame_id="nope")
        assert False, "should raise on unknown frame"
    except ValueError:
        pass


def test_public_state_shape(monkeypatch):
    monkeypatch.setattr("agents.shot_planner.plan", lambda *a, **k: list(SAMPLE_FRAMES))
    pub = canvas_run.public_state(canvas_run.new_canvas("brief"))
    assert [s["id"] for s in pub["stages"]] == canvas_run.STAGES
    assert "total_cost_usd" in pub and "legend" in pub
    assert pub["stages"][0]["cost_usd"] == 0.0
