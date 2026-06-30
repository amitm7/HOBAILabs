# REAL_MEDIA_QUALITY_LADDER.md — resolving the moat's quality tension

> Status: **STRATEGY / artifact (no code yet)** · Date: 2026-06-30 · Owner: Amit
> Ties to `docs/MARKET_FIT_REVIEW.md`, the creative-liberty stance, and the canvas
> roadmap (`docs/AGENTIC_CANVAS_PLAN.md §0.6`). Captures a fault line in the moat and
> the plan to turn it from a liability into a deeper advantage.

## 1. The tension (the insight)
HOB's moat is **real** — the storyteller's actual photos and videos. But that footage
is usually **shot on a phone by a non-professional**: low light, shake, low resolution,
awkward framing, vertical-but-not-9:16. **Passing amateur footage through untouched
undercuts the reel's *cinematic* quality — and cinematic quality is *also* HOB's brand.**

So two things HOB stands for can pull against each other:
- **Authenticity** ("it's really her") — argues for untouched real media.
- **Craft** ("it's beautiful and moving") — argues for polish the amateur footage lacks.

If we ignore this, the moat becomes a **quality liability**: the most authentic reels
look the least produced. Competitors (galleri5 et al.) sidestep it by generating
*everything* synthetically — gorgeous, but fake. We need the third path.

## 2. The resolution — protect identity & claims, NOT pixels
We refuse the binary "real vs. synthetic." Instead we climb a **Reality–Fidelity
ladder, per shot**, lifting quality as high as the shot allows while never crossing the
only two lines that actually carry the moat:
- the **real person's IDENTITY** (their face/voice as a named individual), and
- the **story's CLAIMS** (what actually happened).

Everything else — light, grain, framing, ambience, the *look* of a place — is fair game
for craft. This operationalizes the creative-liberty stance: **ambient/quality liberty is
free; person-touching liberty is consent-gated and labeled.**

## 3. The ladder (each shot picks a rung)
| Rung | What it does | Identity cost | Use it for |
|---|---|---|---|
| **0 · Passthrough** | Real pixels, untouched (today's behaviour). | None | Hero real moments that already look good. |
| **1 · Restore** | **Non-generative** cleanup of the real footage: denoise, stabilize, upscale (Real-ESRGAN), colour-grade, deflicker, 9:16 safe-crop. Same content & identity, just cleaner. | **None** — it *is* the real footage. | **Default lift for ANY real shot**, especially the person's face. The safest quality win. |
| **2 · Re-light / Re-frame** | Light generative touch *on the real frame*: relight, outpaint to fill 9:16, motion-interpolate for smoothness. Still them, conditioned on their pixels. | Low | Amateur low-light / wrong-aspect / too-short clips. |
| **3 · Re-create (ambient) — "inspired from real"** | AI generates a **new, cinematic shot of the SAME scene** where **no real person's face is the subject** — the field, the street, the kettle, the ramp, hands, B-roll — conditioned on the real footage so the *truth of the scene* is preserved. | **None** (no identity touched) — *ambient liberty, free* | **The headline move.** Amateur establishing/B-roll → professional, at zero authenticity cost. |
| **4 · Re-create (person) — "inspired from real"** | AI cinematic shot that **depicts the real person** (likeness), conditioned on their footage. | **High** | Sparingly. **Consent-gated + labeled** (existing `governance.likeness_consent`). Prefer rungs 0-1 for the person. |
| **5 · Generate (synthetic)** | Fully invented person/scene. | — | **Refused** for named real people by default. |

**Why rung 3 is the unlock (your idea):** most of the "amateur kills the reel" problem
is in the *ambient* shots — the wobbly establishing pan of the lane, the dim B-roll of the
kettle. Recreating those cinematically, *inspired from* the real footage, restores the
production value **without anyone's face being synthesized.** The person stays real
(rung 0-1); the world around them gets the cinematic lift (rung 3). Tension resolved.

## 3a. Is authenticity lost? — the precise answer
Authenticity for HOB = the real person's **identity** + the story's **claims**. The
ladder keeps the AI *off* those and *on* everything else:
- **Restore (1) & Re-create ambient (3) — the rungs that do the quality lift —
  preserve identity and claims 100%.** Restore is literally her footage, cleaned; ambient
  recreation rebuilds the *scene* (field, kettle, ramp, hands — **no face**), so the
  irreplaceable thing (her face/identity) is **never synthesized**. The quality comes from
  the *world around her*, not from faking *her*.
- **Only rung 4 (synthesizing the person's likeness) touches authenticity** — and that is
  not part of the quality lift; it's a separate, **consent-gated, AI-labeled** exception,
  used sparingly.
- **Provenance keeps it verifiable:** every reel can declare how much is real / restored /
  recreated, so "real" stays a checkable promise, not a vibe.

**Bottom line: no authenticity is lost on the dimensions that define it.** Keep the person
real (rungs 0-1); give the world the cinematic lift (rung 3).

## 4. Per-shot UX (when we build it)
- Each canvas shot gets a **Fidelity** selector: **Passthrough · Restore · Re-create**.
- The director brain + a **quality score** on the matched real clip **auto-suggests** the
  rung: an amateur establishing/B-roll shot → *Re-create (ambient)*; a precious real
  close-up of the person → *Restore* (keep them real, just clean it).
- Default policy: **a named real person's face stays real (rung 0-1)** unless the operator
  explicitly consents to rung 4. Ambience defaults to whatever best serves the reel.
- Cost-aware: Restore is cheap; Re-create costs image/video gen — shown in the per-stage
  cost gate we already have.

## 5. Consolidated roadmap (single source of truth)
Reconciles the OODA round-3 **parity-gap** list (catch up to galleri5) with this
**moat-deepening** quality ladder (pull ahead). Two axes:
- **Close-the-gap (parity):** Characters stage, storyboard art, upfront cost, Agent Room.
- **Widen-the-moat (only we can/need to):** the Reality–Fidelity ladder.

**Deciding logic (from the round-3 red-team):** the race now turns almost entirely on
**real vs. synthetic**, and galleri5's *one* remaining real advantage is that their
synthetic output looks more *polished*. The Quality Ladder attacks exactly that edge
(makes our *real* reels cinematic); the Characters stage only matches a feature that
doesn't change the axis. So moat-deepening out-ranks most parity work.

| # | Item | Axis | Threatens moat? | Effort |
|---|---|---|:--:|:--:|
| **1** | **Restore** (ladder 1a) — denoise/stabilize/upscale/grade real footage | Moat | No | S |
| **2** | **Upfront cost + balance** display (surface `pricing.estimate`) | Parity | No | S |
| **3** | **Re-create "ambient"** (ladder 1b) — cinematic scene/B-roll *inspired from real*, no face | Moat | No | M |
| **4** | **Characters/Assets stage** — real-identity from real photos, consent-gated (their stage 2, our way) | Parity | No | M |
| **5** | **Re-create person** (1c) + **Fidelity selector + auto-suggest** (1d) | Moat | No (consent-gated) | M |
| **6** | **Storyboard art** (pencil board) | Parity | No | M |
| **7** | **Agent Room** (multi-agent discussion) | Parity | No | L — last |
| — | **(ongoing)** continuity P2 motion-chaining; polish; cloud scale | — | No | — |

**Open judgment call:** #1/#3 (Quality Ladder) vs #4 (Characters stage). Ranked quality
first because it removes galleri5's last advantage on our terms; swap Characters up if
the team will *feel* its absence more in a demo. Product-feel call for the owner.

## 6. Guardrails (unchanged, restated)
- **Identity:** any rung ≥4 (synthesizing the real person's face/voice) requires recorded
  consent and a visible AI label. Default for a named person's face is rung 0-1.
- **Claims:** AI never invents events or claims. Recreation matches the real moment; it
  doesn't add a yacht the chai-seller never had.
- **Realness signal:** a reel should be able to declare how much is real vs. enhanced vs.
  recreated (provenance) — so "real" stays a verifiable promise, not a vibe.

## 7. One-line summary
> Real footage is the moat, but amateur footage is a quality risk. The fix isn't to fake
> the person — it's to **climb a per-shot ladder that lifts craft (light, framing, ambience,
> B-roll) while keeping identity and claims untouched.** "Inspired from real" recreation of
> the *scene* (not the face) is the rung that turns the moat's weakness into a strength.
