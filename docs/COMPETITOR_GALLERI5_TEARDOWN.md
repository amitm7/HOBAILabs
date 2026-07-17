# COMPETITOR_GALLERI5_TEARDOWN.md — galleri5 AI Studio

> Companion to `docs/AGENTIC_CANVAS_PLAN.md`. Captured 2026-06-30 via an authenticated
> walkthrough of **aistudio.galleri5.com** (Playwright attached to a logged-in Chrome over CDP).
> Competitive analysis of a product we have legitimate access to — public-facing functionality only.

## 1. Who they are
**galleri5 AI Studio**, by **Collective Artists Network**, Azure AI Foundry–backed. Positioning:
"India's #1 Cinematic AI Studio." Recently launched **"Agentic Canvas."** More mature, broader, and
better-funded than us. Tagline in-app: *"Lights. Camera. Pipeline — your words become scenes, your
scenes become shots, your shots become a movie."*

## 2. Information architecture (full)
`Home` · `Gallery` (`/generations`) · `Model Garden` (`/model-garden`) · `Contexts` (`/contexts`) ·
`Pricing` (`/plans`) · Projects → `Microdramas`, `Movies`. Workspace/seat model, credit wallet,
"Raise a ticket" support. Free trial = 500 credits / 30 days.

## 3. The engine — "Agentic Canvas"
A **node-graph canvas** (Characters → Storyboards → Keyframes → Video → Final Cut) driven by a
right-side **"Studio Chat"** natural-language agent, running a **6-stage pipeline** with **per-stage
approval gates** ("0/4 approved"):

1. **Script & shots** — wrote a clean 4-scene screenplay w/ VO + captions
2. **Assets** — generates **character reference / model sheets** (the "visual DNA")
3. **Storyboard** — **pencil-sketch comic pages with blue motion arrows** + shot grammar
   ("ECU Handheld") + Camera Notes + a Color Palette strip. **Their single best feature.**
4. **Key Frames** — photoreal anchor frames (consistent with the character sheet)
5. **Audio** — per-scene TTS voiceover
6. **Final Cut** — assembly

Behind it: an **"Agent Room"** (Briefing → Discussion → Planning → Execution → Review) that plans
before generating, surfaced as a multi-agent "Creative Discussion."

### Mechanics worth copying
- **Per-node "Create" buttons quote cost+ETA before spend** ("Uses 7 credits · ~3m").
- **Approval gate per node** + a 0/N approved counter.
- **Reference-chaining**: stages feed forward by handle. Leaked video-stage prompt:
  *"@Image1 Chai Seller character ref. @Image2 scene 1 dawn keyframe. @Image3 storyboard. Use @Image3
  as the sequential shot reference… follow the panel progression in order and preserve the choreography."*
- **Leaked keyframe prompt style**: *"handheld 28mm vérité frame, available mixed light with honest
  white-balance drift, muted natural grade, soft 16mm grain, imperfect caught-moment framing with
  breathing headroom, unvarnished intimacy."* (Sophisticated cinematography templating.)
- Playful loaders ("The machines are dreaming…", "Mixing the secret sauce…").
- Per-asset controls: edit / delete / zoom / download / rotate / fullsize — incl. **single-frame re-roll**.

## 4. Pricing & model strategy
- **162 models** across image/video/audio/3D in a **Model Garden**, tiered 1–4: GPT Image 2, Seedance
  2.0, Veo 3.1, Kling V3, Nano Banana 2, FLUX 2 Max, Lyria 3, Sync Lipsync V3, etc. They are a
  **router over every frontier model** — same posture as our `model_router` + `config/models.json`.
- Free trial 500cr/30d → **Starter ₹2,500/seat/mo** → **Growth ₹40,000/seat/mo** → Enterprise.
  Credits never expire, top-up, team roles, priority queue.
- **Contexts** = reusable "brand bible / character sheet / mood board" notebooks that plug into any
  workflow — their version of our reusable identity library / asset intelligence.

## 5. The cost-trust flaw (our wedge)
Script → characters → storyboard → keyframes → audio all ran at **0 credits** (free, delightful).
Then **one "Generate video" click silently consumed the entire 500-credit balance.** 95% of the
journey is free and lovely; the spend is concentrated and irreversible in a single tap. That is a
trust failure our per-stage `pricing.estimate` + per-stage spend reservation can beat.

## 6. The authenticity finding (the core strategic point)
Their consistency engine produces a **synthesised person** — verified by generating our exact use case
(a Humans-of-Bombay chai-seller story): the output was a gorgeous, photoreal, **fabricated** chai
seller. They **also let you upload your own face**, but that is **reference-conditioned synthesis** (a
generated *lookalike*), **not** real-media passthrough.

**The three-way distinction (see AGENTIC_CANVAS_PLAN §2a):**
1. **Real-media passthrough** (real footage, untouched) — galleri5 has **none**. **Our moat.**
2. **Reference-conditioned likeness** (upload face → lookalike) — they have it; **so do we**. Parity.
3. **Consent + likeness governance** (named real person, recorded consent, AI-labeled) — they appear
   to have **none**; we have `governance.validate_likeness_consent`. **Our second, durable moat.**

For *Humans of Bombay* — real, named people, true stories — a generated lookalike with no consent
gate is the exact authenticity/IP risk our CLAUDE.md refuses to ship by default. Their "upload your
face" doesn't close our moat; it locates them **inside the zone we deliberately gate**.

## 7. Honest scorecard
| Dimension | galleri5 | HOB today |
|---|---|---|
| Pipeline (script→video→cut) | Mature, staged, gated | Mature engine, **linear/un-gated UX** |
| Storyboard stage | **Best-in-class** (boards + motion arrows) | None |
| Model breadth | 162, tiered | Routed subset (extensible via config) |
| Reusable context library | Contexts | Talent/Product identity library (Studio) |
| Cost transparency | **Per-stage quote**, but wallet-drain flaw | Whole-run estimate; per-stage = planned |
| Real-media passthrough | **None** | **Yes (the moat)** |
| Consent / likeness governance | None observed | **Yes** (governance.py, Gap #4 deepens) |
| Output craft (stills) | Excellent | Competitive |
| UX polish | Higher | Lower (the gap we're closing) |

## 8. What we adopt vs. refuse
**Adopt (pattern, not pixels):** stage-gated canvas with per-stage approval; storyboard-with-motion-arrows;
per-node cost+ETA "Create"; reference-chaining; Contexts-style reusable notebooks; playful progress.
**Refuse:** synthetic-person-by-default; "free everything then surprise charge"; ungated likeness of
real people; a 4th forked product.

## 9. Method note (reproducible)
Launched Chrome with `--remote-debugging-port=9222 --user-data-dir=…`; operator logged in; attached
`playwright-core` via `connectOverCDP`; crawled routes and drove one job through the pipeline,
capturing DOM + screenshots + CDN image pulls. No credentials passed through tooling; no access beyond
ordinary authenticated use. Artifacts (screenshots, raw JSON, leaked prompts) in the session scratchpad.

---

## 10. Live-run addendum (2026-07-14) — Yamraj run + bundle internals (L99 decision record)

Evidence: operator drove a full run ("The Man Who Refused to Die", the S28 Yamraj script) on
their Agentic Canvas; operator pasted the generated prompts verbatim; the output clip was
inspected locally (`output/gallari5.mp4`, ffprobe + contact sheet). Separately their 25MB
production JS bundle was pulled and mined (public asset fetch, no auth). → Ledger row **S30**.

### 10.1 Observe — verified facts (all NEW vs. §1–9)

**Internals (bundle):** canvas = **tldraw** (rented, CDN). Backend `agentic-v2/sessions/*` on
Azure Container Apps: `/run` (always `multi_agent:true`), `/classify`, `/frames`,
`/estimate-cost` → `/run-confirmation` (spend gate), `/rerun-step` (+`cascade`),
`/smart-run-all`, `auto_approve`/`auto_execute`, `bulk_mode`+`bulk_count` (ad-variant fan-out),
`/report` + per-step `/feedback`, `/subscribe` stream. Step states: `awaiting_config → queued →
running → completed|failed` + `needs_review`, `awaiting_verification`. Sub-agent roster in the
UI: Research · Script · Scene/Shot Breakdown · Shot Sequence · Storyboard · Character Designer ·
Locations & Sets · Props & Objects · BG Plates · Photobash · Asset Catalog/Sheet · Image ·
Video · **Critique**.

**Audio stack (operator-confirmed UI):** SFX = **MMAudioV2 (video→audio)**; music = Lyria 2 /
Lyria 3 / Lyria 3 Pro (same tech as our `config/music.json`); speech = ChatterBox Multilingual
(23-lang) / ChatterBox Turbo / ElevenLabs Expressive / ElevenLabs Indian+English.

**Live-run behaviour:**
- Correct decomposition: screenplay → shots with sane sizes/durations; exactly 2 locations
  deduped from sluglines. But the refine step **dropped speaker attributions** from dialogue.
- Character assets (5 cr each): **single frontal portrait**, flat neutral light, pose-neutral,
  explicit anti-turnaround negative prompt ("NOT a pose sheet, turnaround, grid"). Identity
  lock = one canonical ref + reference-conditioning; no DNA-sheet turnaround observed in this
  flow (weaker than §3 suggested).
- Location plates generated **empty** ("no characters"), with deliberate negative space ("vast
  empty floor space at center for characters to occupy") and lighting headroom ("room for
  divine golden radiance to flood the space later") → confirms plate→composite architecture
  (BG Plates / Photobash agents).
- **Style token:** an identical house-style preamble ("90s Indian mythological television
  look… painted backdrop… devotional melodrama") was injected verbatim into every asset
  prompt — **overriding the operator's written brief** ("Doctor Strange, deep blues/blacks,
  high contrast"). The video model then largely **ignored** the token and rendered cinematic
  dark-fantasy anyway. So: cross-asset consistency via style token, but (a) it tramples
  director intent and (b) it's leaky at the video stage.
- Output clip: 15.2s, **720p 16:9** 24fps. Auto audio is a real mix (mean −15.6 dB, peak
  −3.5 dB ≈ 12 dB dynamics): music bed + SFX + a stinger **synced to a push-in on the face
  reveal**. Genuinely strong 3 seconds.
- Per-asset review state machine on every image: approve / production-ready / pending /
  rejected / needs-review + edit-in-place.

**Assumptions (flagged, not facts):** "no real-media path" is inferred from absence in bundle
+ UI, not proven. Cross-shot identity consistency **unproven** — one clip, and a skeletal
Yamraj is the most forgiving possible subject (no skin, no stable human face, no hands).
Composite not observed end-to-end.

### 10.2 Orient — what changed in the mental model

1. **Moat refinement (the red-team's main yield):** real-media passthrough *as a feature* is
   trivially copyable — galleri5 could add an "insert real clip" node in weeks. The durable
   part of moat #1 is **HOB's archive + subject consent relationships + the governance
   workflow around them**, not the passthrough bit itself. Consequence: the **asset library
   (GAP #6)** is more load-bearing than its P2 slot implies — it is the moat's compounding
   half. Recommend the owner ratify elevating it once the RDS cutover (GAP #2 follow-on) lands.
2. **Their identity approach is behind our diagnosis.** Single-frontal-ref locking is exactly
   the architecture that produced our S19 (clothing drift) and S20 (tail morph) failures — we
   have already hit, named, and ticketed (T11) the failure mode their design walks into.
3. **The craft gap is real but rentable.** Everything that impressed (SFX, music, beat-sync
   feel) is rented model output (MMAudioV2, Lyria) + assembly logic — nothing proprietary.
4. **Evidence quality caveat:** one run, one 15s clip, a forgiving subject, 100% synthetic
   content. It measures the craft layer only; both moat axes are structurally invisible here.

### 10.3 Decide — options red-teamed

- **A. Craft-parity sprint on their turf** — rejected: fights a funded team on synthetic
  cinema and abandons the governing filter (value ∝ 1/visible-AI-on-real-human).
- **B. Moat-only, ignore craft** — rejected: the north star judges the finished reel; the
  owner's S28 cinematic mode would stay visibly worse than a live competitor.
- **C. Adopt/wrap galleri5 itself for synthetic segments** (the prior-art rule forces this to
  be weighed honestly) — rejected as a *product* dependency: splitting one story across two
  tools breaks the consent/spend/provenance ledger, cost opacity (§5) is their known flaw, and
  it feeds the benchmark rival. **Partially adopted as components:** rent the same primitives
  (MMAudio-class SFX, Lyria, tldraw/react-flow) through our config seams.
- **D. Selective adoption, ordered by the S28 track, rent-first** — **chosen.**

### 10.4 Act — adoption ledger (→ S30)

**Validated (already planned; now competitor-proven — do not reorder away):**
1. **Location anchoring** (S28 "character sheet for places") — their shipped version works;
   stays top of the remaining S28 order.
2. **SFX/atmosphere layer** (S28 "new") — rent an MMAudioV2-class video→audio model via the
   `models.json` seam; it's what made their clip feel finished.
3. **Beat-synced motion** (continuity roadmap P1/P2) — the face-push-on-stinger is the proof.

**New adoptions (small):**
4. Per-asset review state machine (approve / production-ready / pending / rejected /
   needs-review + edit-in-place) — asset-level editorial QA under our pipeline-level gates.
5. Prompt discipline: characters flat-lit/pose-neutral; location plates empty with negative
   space + lighting headroom; relight at composite.
6. A **look token** for cross-asset consistency — *subordinate to the operator's brief* (their
   director-intent override is the anti-pattern: brief wins, token serves).
7. Infinite canvas — **coupled to the asset-graph work** (when locations/characters become
   first-class nodes), rented (tldraw license-check vs react-flow MIT), with a fit-to-overview
   escape hatch. Not a standalone rewrite of the current grid board.
8. Sticky context-sensitive action bar on the canvas (reuse the shell `.action-bar` pattern;
   per-selected-node 🎬/🎙️/🎵/🔊 actions + 💰 estimate inline).

**Refuse:** house-style override of the operator's stated aesthetic; single-frontal-only
identity (keep/extend turnaround + attribute propagation, T11); 720p/16:9 default (we are
9:16); wrapping galleri5 as a dependency.

### 10.4b Contexts, decoded (owner-supplied product copy, 2026-07-14)

Their **Contexts** feature ("teach your pipelines to stay on-brand") is: multimodal
capture (images/video/PDFs/links/notes) → an LLM **distillation** pass → one concise
compiled reference **injected into every generation** in the canvas. Not per-query RAG —
a persistent, user-authored system-prompt-for-a-brand, compiled by AI. The verbatim
"90s Indian mythological TV" preamble observed on every Yamraj asset prompt (§10.1) is
what a Context compiles down to — so we have field-tested this mechanism, including its
failure mode (it overrode the operator's brief).

**Disposition:** Contexts = the product face of OUR asset-library moat (GAP #6 /
STR-7 / SCALE_PLAN brand kits) — the second independent confirmation (after §10.2's
red-team) that GAP #6 is under-prioritized. We already own the injection half
(`set_world` → `_inject_world`, location clauses); the missing half is
build-once-reuse-everywhere + capture→synthesize. Adopt as a **"Style Context"**: a
per-brand/world distilled artifact (look clauses, tone, caption defaults, voice roles,
ref images, negatives) attached at Plan time — near-term value is SERIES consistency
(episode 2 reuses episode 1's world), later value is Veristory multi-client brand kits.
Hard rule from their bug: context FILLS AND SUGGESTS (T3 ✨ pattern); the operator's
brief always wins. Build it as the front door of GAP #6 after the RDS cutover — not as
a second storage story now. → S30 plan Phase 5 (ticketed).

### 10.5 Reversal conditions

- **galleri5 ships real-media passthrough + any consent surface** → moat #1 repositions onto
  archive + relationships; GAP #6 asset library jumps to the front. Watch their changelog.
- **S17 measurement** (same-story comparison, still owed) shows their cross-shot identity
  *collapses* on non-frontal / multi-shot human faces → downgrade the craft threat; do not
  accelerate items 1–3 beyond the S28 order.
- **Operator continuity at HOB changes** (handoff) → freeze adoption items; this addendum
  repurposes as competitive-positioning material.
- **MMAudio-class rental unavailable or poor on our real-footage clips** → SFX drops below
  D5/T7 in the S28 order.
