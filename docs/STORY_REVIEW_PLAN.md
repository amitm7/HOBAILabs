# STR-2 — Story Review Gate (PLAN)

> **Status (2026-07-03, per docs/L99_EXECUTION_AUDIT.md):** PARTIAL — P1 module+tests shipped; P2/P3 routing was NEVER wired (dead gate) — being fixed as audit action A2 (2026-07-03).

> Status: **PROPOSED** (artifact-first per build-feature §"Plan-first for big features").
> Confirm scope/phasing before executing. One engine, many front doors — this is a
> read-only check layer over the existing `frames[]`, **not** a new pipeline.

## 1. Problem (from the "Last Train Home" / Arjun draft)

The AI draft path (`story_to_draft` → `draft_to_format_b`) shipped four classes of
failure that a human reviewer caught but the system did not:

| # | Failure | Evidence | Root cause |
|---|---------|----------|------------|
| 1 | **Pipeline mismatch** | f01 media_query "lone figure on empty platform" → generated "abandoned chappal" (no person). Frames 2–7 describe people but are tagged `ai_symbolic`. | `image_generator.generate_symbolic_image` enforces *"no people, no faces."* The brain wrote people into `ai_symbolic` queries. |
| 2 | **Pivot gap** | Frame 4 teases "said something he couldn't forget" but the vendor's verbatim line never appears on screen. | No check that a flagged pivot/quote beat actually renders the words. |
| 3 | **Blocking gap** | Frame 8 "child runs to father" under-specified; no `[photo:]` pin on a `real_photo_preferred` beat. | No check for multi-person blocking + missing asset/fallback. |
| 4 | **Truncated notes** | Frame 5 note ends `…Child's`; Frame 8 ends `…switch visual_need t`. | `draft_to_format_b` caps `media_query`/`operator_note` at **180 chars** via `_clean_line` ([growth.py:196-197](../agents/growth.py#L196)). |

Two halves to the fix: **(A) prevent at the source** (truncation + brain prompt),
**(B) detect what slips through** (an offline review gate + checklist).

## 2. Locked decisions

- **Offline, deterministic heuristics** — no LLM in the v1 review. Fast, free, runs on
  every draft, golden-testable. (LLM "deep review" is a future tier, out of scope.)
- **Soft gate** — it warns and surfaces a checklist; it never blocks Preview/Render.
  The only hard gate remains the existing **§5 consent** gate for real-person renders.
- **Read-only over `frames[]`** — checks read the Format-B-parsed frame dicts and the
  posting caption. They **suggest**, never auto-mutate frames (operator agency + §5).
- **No pipeline fork** — new module `agents/story_review.py` (pure functions) + one
  route + a checklist panel wired into the existing story flow.

## 3. The review module (`agents/story_review.py`)

Pure functions. `review_frames(frames, posting_caption, *, target_seconds=45) -> dict`:

```
{ "score": 0-100, "summary": str,
  "checks": [ {"id","severity":"warn|info","frame_id"|None,"message","fix"} ] }
```

v1 checks (each a small pure function, individually tested):

1. **symbolic_people_mismatch** *(the #1 issue)* — `visual_need/photo_spec == ai_symbolic`
   AND `media_query` contains a person token (man|woman|child|kid|vendor|person|figure|
   hands?|face|he|she|elderly|developer…). Fix: "retag `ai_portrait`/`real_photo_preferred`,
   or rewrite the query object-only."
2. **pivot_quote_missing** — a beat whose note/role marks a quote/pivot ("said","told",
   "couldn't forget", quote marks) but no caption/`text_card` contains the quoted words.
3. **blocking_ambiguity** — ≥2 person tokens + a directed motion verb (run/hand/give/
   reach) with no explicit subject→object in caption/camera. Fix: name who acts on whom.
4. **truncated_note** — `media_query`/`operator_note` hits the cap length or ends
   mid-word / without terminal punctuation. (Also fixed at source — see §4.)
5. **real_no_pin** — `real_photo_preferred` with no `[photo:]` pin and no symbolic
   fallback. Fix: pin an asset or set an object-only `ai_symbolic` fallback.
6. **consent_ungated** — real-person/likeness beat without the consent flag → link to
   the §5 gate (does not block here; the render-time gate does).
7. **human_presence_ratio** *(HOB-specific)* — a people-story rendered with **zero**
   human beats (all `ai_symbolic`/`text_card`) → info: "no human face in a Humans-of-
   Bombay reel; consider `ai_portrait`/real for the key emotional beats."
8. **pacing** — total duration vs `target_seconds` (±25%); >2 consecutive identical
   shot setups (mirrors `scene_intelligence`'s no-repeat intent).

## 4. Code deltas

- **Fix truncation** ([growth.py](../agents/growth.py)): raise the note-class caps
  (`media_query`, `operator_note`) from 180 → ~600 and never cut mid-word; preserve
  verbatim quotes / blocking / consent instructions. Keep a sane ceiling (Format B must
  still parse). Captions stay short (on-screen) — only the *note* fields grow.
- **Brain prompt (prevention, recommended)**: tighten the `story_to_draft` /
  `scene_intelligence` instruction so a beat that needs a person is tagged
  `ai_portrait`/`real_photo_preferred`, and `ai_symbolic` queries are object-only. This
  kills failure #1 at the source — detection (#3 check) is the safety net.
- **Route**: `POST /story-review` → `review_frames(...)`; also call it inside
  `/story-intake` and return `review` alongside the draft.
- **Frame dict**: no new keys. Optional `_runs[run_id]["review"]` cache.

## 5. Phased tickets (sequenced by risk × value)

| Phase | Ticket | Risk | Notes |
|---|---|---|---|
| **P1** | `fix-truncation` + `story-review-module` + `fixture-exemplar` + tests | Low | Pure backend, fully offline-testable. Unblocks the worst bug + delivers the checks. Golden test: the Last-Train fixture must catch all 4 known issues. |
| **P2** | `story-review-route` (+ wire into `/story-intake`) | Low | `web_app.app.test_client()` smoke. |
| **P3** | `ui-checklist-gate` (panel + soft gate before Preview/Render) | Med | `main.js`/`index.html`; collapse passes, surface only fails; dismissable. |
| **P4** | `docs-sync` (GUIDE, OPERATOR_GUIDE, LLD, PRODUCT_IDEAS STR-2 tick) | Low | Hard gate per rule 11. |
| **Adj** | brain-prompt tighten (prevention) | Low | The real leverage; pairs with P1. |

## 6. Non-goals (v1)

LLM deep-review; hard blocking; auto-rewriting frames; any change to the render
pipeline; per-speaker character faces (already covered by the character-face feature).

## 7. Red-team

- **False positives** (person-token match flags "hand of a clock"): keep severities
  `warn`/`info`, tune the token list, make checks dismissable. Better a noisy warn than
  a silent miss — but tune against the fixture.
- **Truncation fix bloating Format B / breaking the parse**: cap at ~600, not unlimited;
  preserve newlines/quotes; re-parse-round-trip test.
- **Gate fatigue**: high-signal only; collapse passing checks; never block (except §5).
- **Over-policing creativity**: advisory by design; the operator always proceeds.
- **Check drift vs pipeline**: `symbolic_people_mismatch` must track
  `generate_symbolic_image`'s "no people" contract — co-locate a comment linking them.

## 8. Verify plan

Offline unit tests per check + a golden test on `tests/fixtures/last_train_home`
(asserts the 4 known issues are flagged); `py_compile`; route via `test_client`;
UI smoke. Report honestly which steps ran.
