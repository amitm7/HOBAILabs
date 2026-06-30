# AGENTIC_CANVAS_PLAN.md — staged "director canvas" over the shared engine

> Status: **IN PROGRESS** (branch `feature/agentic-canvas`) · Date: 2026-06-30 · Owner: Amit
> Trigger: competitive teardown of **galleri5 AI Studio** (aistudio.galleri5.com) — a more
> mature competitor whose "Agentic Canvas" runs Script → Assets → Storyboard → Keyframes →
> Audio → Video → Final Cut as a stage-gated, cost-metered node canvas.
> This doc decides **how** (and whether) we answer it, grounded in our actual codebase.
> Companion: `docs/COMPETITOR_GALLERI5_TEARDOWN.md` (raw findings). Read `build-feature/SKILL.md` first.

---

## 0. BLUF (bottom line up front)

**Do not build a 4th, separate galleri5-clone product.** Build the **checkpointed staged-execution
primitive once in the existing engine**, and expose it as an **opt-in "Director Canvas" view that
all three front doors can switch into** — with a *mode-aware* depth of gates. This honours our hard
rule "one engine, many front doors," reuses **~75–80% of the backend and ~50% of the UI shell**, and
keeps our **real-media moat** front-and-centre (the exact thing galleri5 structurally cannot do).

- **The engine is not the gap.** We already have all 15 stages, cost estimation, per-frame approval,
  caching, governance, SSE progressive reveal, and a shared step-wizard shell.
- **The gap is the *interaction model*:** our `_run_inner()` runs all stages in one linear threaded
  pass; galleri5 **pauses between stages**, renders each result, shows a per-stage cost, and waits for
  a human "Create"/approve before spending the next dollar.
- **The single hardest new thing** is turning `_run_inner()` into a **resumable, checkpointed state
  machine** with **per-stage spend reservation**. Everything else is presentation over code we own.
- **Moat nuance (corrected):** galleri5 is *not* purely synthetic — it lets you **upload a real face**
  to condition generation. But that is **reference-conditioned synthesis** (it generates a consistent
  *lookalike* of you), **not real-media passthrough** (the actual footage of the actual person, untouched).
  Our durable edge is the latter **plus consent/likeness governance** — see §2a.

**Recommendation: Option C (shared primitive + mode-aware canvas view), phased.** See §4–§5.

---

## 0.5 BUILD LOG (branch `feature/agentic-canvas`)

**Landed (first vertical slice, P0 + P1 skeleton + P2 board):**
- `agents/canvas_run.py` — the staged orchestrator (state machine, per-stage cost
  via `pricing.estimate`, three-way asset classification via `model_router`,
  structured motion arrows, cascade `invalidate_from`, `PaidStageDispatch`). Holds
  the bright line: reuses agents + services, renders nothing itself.
- `web_app.py` — `/canvas` + `/api/canvas/{plan,<id>/state,<id>/advance,<id>/approve}`;
  paid stages return a **per-stage cost + spend-cap check before any spend** (the
  anti-wallet-drain); advance/approve gated by `require_operator`. Canvas state
  persisted inside the run payload (`run_store`) — no parallel store.
- `web/templates/canvas.html` + `web/static/canvas.js` — the board UI: stage rail
  with cost-gated Generate, storyboard cards with motion-arrow SVGs + the 🟢/🟡/🔴
  real-vs-AI legend (the moat made visible).
- `tests/test_canvas_run.py` — 10 offline tests (state machine, cost slicing,
  classification, cascade). **Verify loop green:** py_compile ✓, `node --check` ✓,
  pytest 10/10 ✓, Flask test-client e2e ✓ (plan→storyboard→approve→paid-gate→lock 409).
- **Editable prompt box + command box (parity with the competitor's Studio Chat):**
  `canvas_run.edit_frame` + `/api/canvas/<id>/frame` (edit caption/motion/image_prompt/
  negative per shot, cascade-invalidate downstream) and `canvas_run.chat` +
  `/api/canvas/<id>/chat` (natural-language refine → re-plan via `shot_planner`). Board
  cards render inline editable fields; a sticky command bar drives re-planning.
- **Attach-your-image Assets flow (the moat, surfaced):** `canvas_run.attach_asset`
  + `/api/canvas/<id>/asset` — upload a photo (reuses `/upload-photo`) and assign it
  per-shot or to all people-shots as **real** (PASSTHROUGH, untouched), **reference**
  (AI likeness conditioned on the real face), or **scene**. Board renders the real
  thumbnail + live 🟢/🟡/🔴 badge; cascade-invalidates downstream. Verified by browser
  e2e (real upload → REAL badge + thumbnail; character ref → 5 REF tags on people-shots).
- Docs synced: HLD (front-door table), LLD (module map + route table + auth list),
  GUIDE (§3e), this plan.

**Script + Storyboard stages are live** (cheap text, reuse `shot_planner` +
`scene_intelligence`, degrade offline). **Paid stages** (Key Frames/Audio/Video/
Final Cut) currently show server-truth cost + spend gate; **next:** wire their
execution to the existing `_execute_pipeline` (reuse, never re-implement), add SSE
`stage_done` events, per-stage spend reservation, and the storyboard art renderer.

## 0.6 NEXT ACTIONS (sequenced — "move together", updated after render landed)

**Closed this milestone:** finished render (was the #1 gap) — `/api/canvas/<id>/render`
reuses `_execute_pipeline`; produced a verified 30s prod reel ($1.60, distinct shots,
3-speaker VO). Output quality now on par. The two biggest competitor gaps are addressed.

**Shipped (this session, on branch):** ✅ full render wiring (`/render` → `_execute_pipeline`,
verified prod reel) · ✅ **#1 per-card live reveal** (`/rendered`: clips on cards, persists on
reload, overlays kept, stage-status reconciled — no more stuck 'generating') · ✅ **#3 per-shot
re-roll** (`/reroll`: new still+clip for one shot, verified replacing the phone-artifact with a
better shot) · ✅ **ETA per stage ("~Nm") + shimmer loaders** · ✅ save/resume + recents picker ·
✅ **Key Frames ↔ Video gate** (`/keyframes` renders cheap stills only via `_execute_preview`;
review/re-roll; then `/render` reuses those stills from the shared run dir — content-hash cache;
verified Key Frames done while Video stays gated. Also fixed `_execute_preview` not persisting
'done' to `run_store`. *Open: stills reuse is partial when scene prompts drift; Final Cut bundled
into the Video render — full 4-way per-stage split is the remaining increment.*)

**Remaining gaps → ordered plan (each row = one shippable step):**

| # | Action | Closes | Reuse / approach | Effort |
|---|---|---|---|---|
| 1 | **Per-card live reveal + keyframe-on-card** — show each shot's still/clip on its card as it renders (replace the ref-chip placeholder with the real generated still/clip). Browser-validate the `clip_ready` SSE wiring already built. | Kills "same image" perception for good; matches galleri5's per-shot reveal | existing `/progress` `clip_ready` events + run_dir stills | S–M |
| 2 | **Per-stage render granularity** — let Key Frames / Video / Final Cut each render + gate independently (not one whole-pipeline dispatch). | #8 finished-render → fully on par (per-stage like galleri5) | the hard P1 checkpoint primitive (AGENTIC_CANVAS_PLAN §5a); reuse stage fns | L |
| 3 | **Per-asset re-roll** — regenerate ONE shot's still/clip without re-running the stage (fixes one-off artifacts like the frame-5 phone). | their nicest small touch | reuse `/redo-still` + `/redo-motion` | S |
| 4 | **ETA per stage (~Nm) + styled loaders** | the "~3m" gap + polish | timing from pricing/model + CSS | S |
| 5 | **Storyboard comic-art render (optional)** — the pencil board page. | their best visual wow | reuse `image_generator` w/ board prompt | M |
| 6 | **Assets stage as first-class** — character DNA sheets from REAL refs surfaced as a canvas stage. | their stage-2; our moat version | reuse Studio talent/identity library | M |
| 7 | **Reference-chaining UI + Model Garden view + render Gallery** | parity breadth | `model_router`/`/models`, `run_store` | M |
| 8 | **Agent Room** (multi-agent creative discussion) | flashy, low operator ROI | reuse `llm.py` tiers | L — last |

**Parallel track:** ✅ P1 **beat-aware cutting** wired for the canvas — the engine
(`assembler.transition_plan`/`beat_overlaps`) already does hard-cut-on-beat vs dissolve-off-beat;
the canvas render now generates a music bed (`_canvas_render_thread`) so cuts snap to the beat
instead of uniform 0.4s crossfades (the slideshow root cause). Engine verified by `test_beat_cutting.py`
+ a synth-beat run (2 hard cuts on-beat + 2 dissolves off-beat). **Suno-independent:** when no music
bed is available (e.g. Suno credits out), `beat_overlaps(fallback_bpm=)` snaps cuts to a mood-derived
**tempo grid** (`_canvas_tempo_bpm`: 80 somber / 92 default / 108 upbeat) so cutting stays rhythmic
instead of degrading to uniform crossfades. With music, real beats are used. Other modes unaffected
(no `beat_grid_bpm` → None → uniform). Verified: 56 tests incl. tempo-grid mix + uniform-preservation.
- ✅ **Audio options (parity with Story mode):** the canvas render takes `music_type` =
  **generate** (Suno) / **upload a song** (your track, via `/upload-photo`) / **voiceover**
  (ElevenLabs, 21 voices from `/voices`) / **none**. Backend validates (upload-without-song → 400);
  voiceover switches to gentle uniform cuts. So music is no longer hard-coded — and the anti-slideshow
  works across all four. (Suno credits will be topped up; upload/voiceover work today regardless.)

**Recommended order:** 1 → 3 → 4 (quick, high-perception wins) → 2 (the big one) → 5/6 → 7 → 8.

## 1. OBSERVE

### 1a. What galleri5 does (verified by live walkthrough)
- 6 visible stages on a pan/zoom **node canvas**: Script & Shots → Assets (character "DNA" sheets) →
  Storyboard (pencil pages **with motion arrows**) → Key Frames → Video Clips → Final Cut.
- A multi-agent **"Agent Room"** (Briefing → Discussion → Planning → Execution → Review) plans first.
- **Per-node "Create" buttons** show **"Uses N credits · ~3m"** *before* spend. Approval gates per
  node ("0/4 approved"). Stages **chain by reference** (`@Image1` char-ref + `@Image2` keyframe +
  `@Image3` storyboard feed the video model: "follow the panel progression").
- Script → characters → storyboard → keyframes → audio all ran **free**; **video drained the wallet
  in one click** (their cost-trust flaw).
- **Their consistency comes from a synthesised character** (a fabricated chai-seller) propagated across
  stages — **or from a real face you upload**, which it turns into a consistent *generated lookalike*.
  Either way the on-screen person in the final frames is **AI-generated**, not the real footage.
  For *Humans of Bombay* (real, named people telling true stories), a generated lookalike of the real
  person is precisely the authenticity risk our CLAUDE.md flags — **disqualifying as a default**.

### 1b. What we already have (codebase map — see the three exploration reports)
| Capability | Where | State |
|---|---|---|
| 15-stage linear pipeline | `web_app.py::_run_inner()` (L1765–2040) | **Exists**, atomic stages |
| Brief → editable shots | `agents/shot_planner.py::plan()` | **Exists** (Studio) |
| Per-frame director (emotion/prompt/motion) | `agents/scene_intelligence.py` | **Exists**, cached |
| Image / video / lipsync / TTS / assembly | `image_generator`, `clip_builder`, `lipsync_coordinator`, `tts_generator`, `assembler` | **Exists** |
| Server-truth cost estimate | `agents/pricing.py::estimate()` + `/api/estimate` | **Exists**, multi-shot aware |
| **Per-frame approval gate** | `_run_inner` `approved_frame_ids` → free Ken Burns | **Exists** |
| Content-hash caches (scene/still/clip), S3-backed | `agents/cache_store.py` | **Exists** |
| Real-media passthrough (never AI-regen) | `model_router::_is_real_media()` → `PASSTHROUGH` | **Exists** (the moat) |
| Consent + spend-cap + likeness gates | `agents/governance.py` | **Exists** |
| SSE progress + **progressive clip reveal** | `/progress/<run_id>`, `on_clip_ready` events | **Exists** |
| Shared shell: step wizard + preview panel + action bar | `web/templates/_base.html`, `web/static/shell.js` | **Exists** |
| Three doors on one engine via `mode` flag | `/`, `/brand`, `/studio` → `_run_inner` | **Exists** |
| Queued continuity work (beat-aware cutting P1) | `reel-continuity-quality-roadmap` | **Planned, not shipped** |

### 1c. What we do NOT have (the real backlog)
1. **Checkpointed / resumable runs** — pause after a stage, persist intermediate artifacts, hold spend
   reservation across human-time, resume on approval. `_run_inner` is one thread that `rmtree`s its
   temp dir at the end.
2. **Per-stage cost gate UX** — we estimate the *whole* run up front; galleri5 quotes **the next stage**.
3. **Storyboard visualisation** — we have no boards; galleri5's storyboard stage is its best asset.
4. **Reference-chaining with cascade invalidation** — editing an upstream stage must invalidate
   downstream caches (our caches are independent today).
5. **Canvas/board UI** — our preview panel shows a timeline + final video, not stage cards.
6. **(Optional) Agent Room** — multi-agent "creative discussion" surface.

---

## 2. ORIENT

### 2a. Strategic frame
- **We are behind on UX maturity and breadth; we are ahead on authenticity and (potentially) cost-trust.**
  We will not out-polish a better-funded competitor head-on. We win by **out-positioning**: real people,
  honest cost, governance as a feature.
- **Their gorgeous output is our argument.** The chai-seller (teardown img_5) is exactly what HOB must
  never ship. The canvas must make **real assets first-class and visually distinct** from AI.
- **The "upload your face" feature does NOT close our moat — it locates galleri5 inside our gated zone.**
  Three distinct things, only the first is our moat:
  1. **Real-media passthrough** — the *actual* photo/video of the *actual* person appears untouched in
     the cut. galleri5 has **no** equivalent; everything routes through a model. **This is the moat.**
  2. **Reference-conditioned likeness** — upload a face, generate a consistent lookalike. galleri5 has
     this; **so do we** (`ai_portrait` + `--face-ref`, `talent_id`, `character_ref_path`). Parity, not gap.
  3. **Consent + likeness governance** — binding face/voice use to a *named real person's* recorded
     consent, labeled as AI. galleri5 appears to have **none**; we have `governance.validate_likeness_consent`
     (Gap #4 deepens it). **This is the second, more durable moat** as raw capability commoditises.
- **Implication:** capability parity on likeness is closing. Our defensible ground is **(1) real footage
  in the cut + (3) consent/labeling governance** — *not* "we can/can't generate a lookalike."
- **Their cost flaw is our wedge.** "95% free, then one click empties your wallet" is a trust failure.
  Our `pricing.estimate()` + per-stage spend reservation can make cost *predictable and reversible* —
  but only if we implement **per-stage reservation**, not whole-run.

### 2b. The "build vs adopt" read on UX
The canvas is a **presentation + orchestration** layer, not new generative capability. We already own the
generative capability. So this is **mostly a front-end + a state-machine refactor**, which is the cheap
half of galleri5's moat to replicate — and the half that doesn't compromise our positioning.

### 2c. Design principle (L99 PM/designer lens)
**Depth on demand, speed by default.** Different doors want different things:
- **Story** (HOB team, fast personal reels): wants *speed*. A forced 6-gate canvas is friction.
- **Brand** (paid collabs): wants *governance*. Stage gates are a **gift** (sign-off, audit, no surprise spend).
- **Studio** (prompt → film): wants the **full director canvas**. This is the canvas's natural home.

So the canvas is **one primitive, surfaced at mode-appropriate depth** — not a monolith forced on everyone.

---

## 3. DECIDE — integrate vs. new front door

### Options considered
| Option | What it is | Verdict |
|---|---|---|
| **A. New siloed `/canvas` product** | A 4th front door that clones galleri5 end-to-end | ❌ **Reject.** Tempts a pipeline fork (violates hard rule #1), duplicates UI, and structurally pulls us toward synthetic-first. High build, dilutes moat. |
| **B. Bolt staged-canvas into all 3 doors equally** | Every door gets the same 6 gates | ❌ **Reject.** One-size gates punish Story-mode speed; over-engineers the common case. |
| **C. Shared primitive + mode-aware canvas view** | Build checkpointed execution once in the engine; expose an opt-in "Director Canvas" with depth tuned per mode; home base = Studio, progressive rollout to Brand then Story | ✅ **Recommend.** Reuses the engine, respects "one engine/many doors," matches each audience, protects the moat. |

### Recommendation: **Option C**, phased — implemented as a **new orchestration flow, not an in-place refactor**.
- **Decision (refined 2026-06-30):** Do **NOT** refactor the battle-tested linear `_run_inner` in place —
  that risks regressing the three stabilized doors. Instead add a **new `canvas_run` orchestrator**
  that **reuses every existing agent AND every engine service**, and runs the stages **one gate at a
  time**. `_run_inner` stays untouched (the fast linear path); `canvas_run` is the staged path. Both are
  thin sequencers over the *same* shared functions.
- **THE BRIGHT LINE (non-negotiable):** the canvas flow **sequences and gates; it never re-implements.**
  It MUST call — never fork — the shared services that carry the moat and the money:
  `pricing.estimate` (cost = server truth), `agents/cache_store` (content-hash caches),
  `agents/governance` (consent/spend/likeness), `model_router` (routing **+ real-media passthrough**),
  `assembler` (one assembly path). Re-deciding any of these inside the canvas flow = **forking the moat**.
- **Real-media passthrough is called, never re-judged.** The "Assets" stage routes through
  `model_router._is_real_media() → PASSTHROUGH`; the canvas never decides image-gen itself (else it can
  silently AI-regenerate a real person — the one unacceptable failure).
- **New agents are allowed but disciplined:** add one only when it changes a decision that improves the
  finished reel; each must use `agents/llm.py`, cache by input hash, degrade gracefully, and never author
  regulated ad claims. **No "Agent Room" of demo-impressive agents for their own sake** (defer P6).
- **`frame`/run keys** are additive (per rule #2); the canvas becomes a **second consumer** of the same
  stage functions, gated by approval.
- **Surface:** a **"Director Canvas" view** (board of stage cards) that **Studio enters by default**,
  **Brand offers as an approval workflow**, and **Story exposes as opt-in "slow mode."** Same engine,
  same `frame` dict, same agents.
- **Moat-preserving twist:** our **"Assets" stage is real-photo-first** — real media shows as *locked,
  green, passthrough*; AI assets are *amber, labeled, consent-gated*. The inverse of galleri5's default.

---

## 4. REUSE MAP & EFFORT ESTIMATE

### 4a. Reuse by layer (grounded in the code map)
| Layer | Reuse | Net-new work |
|---|---|---|
| **Generative stages** (shot_planner, scene_intelligence, image_generator, clip_builder, lipsync, tts, assembler) | **~95%** — call the *same* functions, just gated | thin per-stage wrappers |
| **Cost** (`pricing.estimate`) | **~80%** — already multi-shot/approval aware | add *per-stage* slicing + per-stage reservation |
| **Caching** (`cache_store`) | **~90%** | add **dependency graph + cascade invalidation** for reference-chaining |
| **Governance** (`governance.py`) | **~85%** | move spend reservation from **whole-run → per-stage** |
| **SSE / progressive reveal** | **~85%** | add `stage_done` / `awaiting_approval` event types |
| **Run state** (`run_store`) | **~70%** | persist **intermediate artifacts + stage status + resume token** |
| **`frame` dict** | **~100%** | additive keys only (`canvas_stage`, `stage_status`, `stage_deps`, `stage_cost`) |
| **UI shell** (`_base.html`, `shell.js`) | **~50%** | new **stage-board component**, per-stage cost-gated "Generate", approval toggles, reference-chain view |
| **`_run_inner` orchestration** | **~40%** | **the hard part: refactor to a checkpointed state machine** |

### 4b. Effort estimate (engineering-weeks, 1 strong full-stack dev; ranges, not promises)
| Phase | Scope | Est. | Risk |
|---|---|---|---|
| **P0 — Spike + lock decisions** | Prove checkpoint/resume on 2 stages; finalize state model | **0.5–1 wk** | Low |
| **P1 — New `canvas_run` orchestrator** | New staged flow (NOT a refactor of `_run_inner`, which stays untouched); reuses all agents + engine services; persist artifacts; per-stage spend reservation; resume token; cascade cache invalidation | **2–3.5 wk** | **High** (state + governance across human-time) |
| **P2 — Canvas UI (Studio first)** | Stage board, per-stage cost-gated Generate, approval toggles, real-vs-AI asset coloring, progressive reveal reuse | **2–3 wk** | Medium |
| **P3 — Storyboard stage** | New board renderer (keyframe + motion-arrow overlay + shot grammar); reuse scene_intelligence motion | **1–2 wk** | Medium (quality bar) |
| **P4 — Reference-chaining UX** | "f02 inherits f01 look"; cascade invalidation surfaced in UI | **1–1.5 wk** | Medium |
| **P5 — Roll out to Brand (governance) + Story (opt-in)** | Mode-aware gate sets; docs-sync | **1–1.5 wk** | Low |
| **P6 — (Optional) Agent Room** | Multi-agent creative discussion panel (reuse `llm.py` tiers) | **1.5–2.5 wk** | Low value/high flash — **defer** |
| **Continuity dependency** | P1 beat-aware cutting from the queued roadmap (separate doc) — **must ship in parallel** or the prettier canvas exposes a slideshow output | **(tracked separately)** | — |

**Total to a credible Studio canvas (P0–P4): ~6.5–11 engineering-weeks.** Brand/Story rollout +1–1.5.
Agent Room deferred. ~75–80% backend reuse, ~50% UI reuse — the spend is concentrated in **P1 (engine
state machine)** and **P2/P3 (board UI)**, not in re-implementing generation.

---

## 5. DETAILED PLAN

### 5a. New engine primitive — checkpointed run (P1)
- Add a **`canvas_run`** orchestrator alongside `_run_inner` (shared stage functions, not a fork):
  - Stage table: `["script","assets","storyboard","keyframes","audio","video","finalcut"]`
    mapped to existing functions (`shot_planner.plan`, `_generate_stills`/`scene_intelligence`,
    new storyboard renderer, `image_generator`, `tts_generator`, `clip_builder`, `assembler`).
  - **Run as a state machine:** execute one stage → persist artifacts under `run_dir/stage_<n>/` →
    emit `awaiting_approval` SSE → **stop**. Resume on `POST /api/canvas/<run_id>/advance`.
  - **Do NOT `rmtree` between stages** (current cleanup is end-of-run only — must become per-run lifecycle).
- **Per-stage spend reservation** (governance): reserve at **stage start**, release/settle at stage end.
  Fixes galleri5's wallet-drain: each "Generate" reserves only that stage; cap checked per stage.
- **New `frame`/run keys** (additive, per rule #2): `canvas_stage`, `stage_status`
  (`pending|generating|done|approved`), `stage_deps` (upstream stage ids), `stage_cost_usd`,
  `ref_chain` (e.g. keyframe → {char_sheet_id, storyboard_id}).
- **Cascade invalidation:** editing/re-approving stage N marks N+1… `pending` and busts their caches
  (a small dependency-graph walk; cache keys already content-addressed).

### 5b. New routes (`web_app.py`, mirror Studio pattern — no fork)
```
GET  /canvas                      → canvas UI shell (or ?mode= param on existing doors)
POST /api/canvas/plan             → shot_planner.plan() + stage scaffold  (cheap, text)
POST /api/canvas/<run_id>/advance → run next stage (reserves spend, returns cost first if dry=1)
POST /api/canvas/<run_id>/approve → mark stage approved; unlock next Generate
POST /api/canvas/<run_id>/redo    → re-run one stage/frame; cascade-invalidate downstream
GET  /api/canvas/<run_id>/state   → full board state (stages, costs, statuses, artifacts)
```
All `@require_operator()`; all inherit Gates A/B/B2/consent/spend; `/media` stays `_path_allowed()`.

### 5c. UI (P2/P3, reuse `shell.js` + preview panel)
- **Stage board** in the preview panel: one card per stage with status, thumbnails, and a
  **cost-gated "Generate (≈$X / N credits)"** button (server-truth from `pricing.estimate` slice).
- **Real-vs-AI coloring** (moat made visible): real/passthrough assets = green "REAL · locked";
  AI assets = amber "AI · labeled"; AI-of-real-person = red until `likeness_consent`.
- **Storyboard renderer:** keyframe thumb + motion-arrow overlay + shot-grammar caption
  (reuse `scene_intelligence` motion + `shot_size`). This is the highest-delight, most-adoptable piece.
- **Reference-chain affordance:** "this keyframe follows f01's look / the storyboard panel."
- Progressive reveal: reuse `on_clip_ready` → `stage_done` events.

### 5d. Config / docs (rule #11 — same unit of work)
- `config/pricing.json` / `models.json`: no new vendors expected; add a **storyboard render** price key
  if we generate board images.
- **Docs to update when this ships:** `HLD.md` (new canvas orchestrator + route class + storyboard
  stage), `LLD.md` (new run keys, routes, cascade-invalidation cache rule, resume token),
  `OPERATOR_GUIDE.html` + `GUIDE.md` (the canvas workflow), this `_PLAN.md` (tick phases),
  `GAP_BACKLOG.md` (closes the "no storyboard / no staged approval" gap).

---

## 6. RED TEAM (adversarial pass on this plan)

1. **Moat erosion via UX gravity.** A board that foregrounds "generate characters → keyframes" nudges
   operators toward synthetic-first, exactly galleri5's trap. → **Mitigation:** Assets stage is
   real-photo-first; AI assets are visibly labeled and consent-gated; default copy says "use the real
   photo." Make the moat the *path of least resistance*, not a setting.
2. **"Just add gates" undersells P1.** Pausing a run means: persisting intermediate artifacts, **holding
   a spend reservation across hours/days**, and resuming work whose thread is long dead. That's a state
   redesign, not a sprinkle. → Estimate already flags P1 **High**; reserve **per stage**, not per run,
   so a paused canvas isn't holding budget hostage.
3. **Cache cascade is sneaky.** Reference-chaining means an upstream edit must invalidate downstream, or
   we ship inconsistent boards (new script, stale keyframes). → Build the dependency walk in P1; treat
   "edited upstream but stale downstream" as a first-class UI state.
4. **The slideshow trap.** A pro-looking canvas **raises expectations**; if assembly stays uniform
   0.4s crossfades, the polished UI makes the slideshow output *more* jarring. → **Hard dependency:**
   ship P1 beat-aware cutting (queued continuity roadmap) **in parallel**, not after.
5. **Inheriting galleri5's wallet bug.** Copy the canvas but not per-stage reservation and we replicate
   "one click drains the wallet." → Per-stage reservation is non-negotiable in P1; show "this stage
   ≈$X, cap remaining $Y" before every Generate.
6. **Scope creep toward the flashy bit.** The Agent Room demos beautifully but is **lowest ROI** for
   operators who want a finished reel, not 10 explorations. → **Defer P6**; ship director value first.
7. **Forcing depth on the wrong door.** A 6-gate canvas in Story-mode kills the team's speed. →
   Mode-aware depth; canvas is **opt-in** for Story, default only for Studio.
8. **Estimate optimism in general.** Ranges assume the stage functions are as cleanly separable as the
   map suggests; `_run_inner` shares temp dirs and a single governance reservation. → P0 spike exists
   precisely to de-risk this before committing P1.
9. **"New flow" decaying into a fork (the central risk of the chosen approach).** A second orchestrator
   is safe only while it stays a thin sequencer over shared services. The slow failure: the canvas flow
   accumulates its own cost math / its own assembly / its own passthrough decision "just for canvas," the
   two paths drift, and the moat logic now lives in two places that disagree (quote ≠ bill; real photo
   silently regenerated; consent gate skipped on the new routes). → Enforce **the §3 bright line in code
   review**: any cost/cache/governance/routing/assembly logic appearing *inside* `canvas_run` is a defect,
   not a feature. New routes must carry the same `@require_operator()` / `_path_allowed()` / governance
   pre-checks as the stabilized doors — a fresh flow is where these get forgotten.
10. **Divergence maintenance tax.** Two flows means every future pipeline change must be considered for
    both. Kept cheap ONLY if both stay thin — a change to a shared agent/service then propagates to both
    for free. Budget for a doubled verify surface (compile→offline→live→e2e on the canvas flow too).

---

## 7. NON-GOALS (what we deliberately will NOT copy)
- **No synthetic-person-by-default.** Real media is the anchor; AI fills gaps, labeled.
- **No 4th siloed product / pipeline fork.**
- **No "free everything then surprise charge."** Cost is shown and reserved per stage.
- **No mandatory canvas.** Speed stays the default for Story.
- **Agent Room is deferred,** not a launch requirement.

## 8. OPEN QUESTIONS (need a decision before P1)
1. **Home base:** confirm Studio-first rollout (recommended) vs Brand-first (governance-led)?
2. **Pause longevity:** how long may a canvas sit paused holding artifacts (hours? days?) — sets the
   reservation + storage policy.
3. **Storyboard images:** render actual board art (costs image gen) or vector/CSS motion-arrow overlays
   on keyframes (free)? (Recommend free overlay first; art later.)
4. **Free-planning funnel:** do we make script+storyboard cheap/free (text-tier) to match galleri5's
   delightful free run, then gate at image/video?

## 9. DOCS-SYNC CHECKLIST (when code lands)
- [ ] `docs/HLD.md` — canvas orchestrator, route class, storyboard stage
- [ ] `docs/LLD.md` — run keys, new routes, cascade-invalidation rule, resume token
- [ ] `docs/OPERATOR_GUIDE.html` + `GUIDE.md` — canvas workflow (user-visible)
- [ ] this `_PLAN.md` — tick shipped phases
- [ ] `docs/GAP_BACKLOG.md` — close staged-approval / storyboard gap
- [ ] `config/*.json` — storyboard price key if board art is generated
