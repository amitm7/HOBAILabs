"""P1 beat-aware cutting — pure-logic tests (no ffmpeg, no audio).

Covers the two invariants that matter most: (1) the uniform/legacy path is
byte-identical (no regression to existing renders), and (2) beat-driven
per-junction overlaps land cuts on beats and keep captions in sync via the same
overlaps list. See docs/STORY_REVIEW_PLAN.md sibling — reel-continuity roadmap.
"""

from agents.assembler import (
    effective_timecodes, transition_plan, beat_overlaps, TRANSITION_DUR,
)
from agents.beat_track import beat_times


# ── (1) no regression: uniform path identical to legacy behaviour ─────────────

def test_uniform_crossfade_matches_legacy():
    tc = effective_timecodes([4.0, 5.0, 3.0], "crossfade")
    assert tc[0] == (0.0, 4.0)
    assert abs(tc[1][0] - 3.6) < 1e-6 and abs(tc[1][1] - 8.6) < 1e-6   # 4 - 0.4
    assert abs(tc[2][0] - 8.2) < 1e-6 and abs(tc[2][1] - 11.2) < 1e-6


def test_none_transition_has_no_overlap():
    tc = effective_timecodes([4.0, 5.0], "none")
    assert tc[1][0] == 4.0


# ── (2) per-junction overlaps + planner ───────────────────────────────────────

def test_per_junction_overlaps_shift_starts():
    tc = effective_timecodes([4.0, 5.0, 3.0], overlaps=[0.06, 0.4])
    assert abs(tc[1][0] - 3.94) < 1e-6                      # 4 - 0.06 (cut)
    assert abs(tc[2][0] - (tc[1][0] + 5 - 0.4)) < 1e-6      # then a dissolve


def test_planner_cuts_on_beats_dissolves_offbeat():
    # boundaries at 4.0 and 9.0; a beat near the first, none near the second.
    ov = transition_plan([4.0, 5.0, 3.0], beats=[4.05, 1.0, 13.0], near=0.25)
    assert ov[0] == 0.06          # cut on the beat
    assert ov[1] == TRANSITION_DUR  # off-beat → dissolve


def test_planner_no_beats_is_all_dissolve():
    assert transition_plan([4.0, 5.0, 3.0], beats=[]) == [TRANSITION_DUR, TRANSITION_DUR]


def test_caption_uses_same_overlaps_as_video():
    # The whole point: captions are computed from the SAME overlaps the planner
    # feeds the video graph, so a cut never desyncs from its caption.
    durs = [4.0, 5.0, 3.0]
    beats = [4.05, 13.0]
    ov = transition_plan(durs, beats, near=0.25)
    tc = effective_timecodes(durs, overlaps=ov)
    assert abs(tc[1][0] - (4.0 - ov[0])) < 1e-6


# ── graceful fallbacks (red-team) ─────────────────────────────────────────────

def test_beat_overlaps_none_without_music_or_beats():
    clips = [{"actual_duration": 4.0}, {"actual_duration": 5.0}]
    assert beat_overlaps(clips, music_path=None) is None        # no bed
    assert beat_overlaps(clips, music_path="x.mp3", transition="none") is None
    assert beat_overlaps([{"actual_duration": 4.0}], "x.mp3") is None  # <2 clips


def test_beat_times_graceful_on_bad_input():
    assert beat_times("") == []
    assert beat_times("/nope/missing.mp3") == []
