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
