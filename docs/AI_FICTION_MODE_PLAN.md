# Real vs AI story — the intake switch + Fiction/Character mode

> Discussion artifact (NO code). Adds a **story-type switch at intake** — *Real story
> (HOB authentic)* vs *AI story (fiction/mythology, e.g. Ramayana or a Bible tale)* — and
> maps exactly what the AI path needs that we don't have, which third-party tools fill each
> gap, and the gpt-image-1 upgrade. Companion to `CHARACTER_RETRIEVAL_PLAN.md`. 2026-07-01.

## 0. The core idea (and why it's not a fork)
One question at the start: **"Is this a real story or a fully-AI story?"** The answer sets
**mode-aware defaults + which capabilities are on** — a `story_type` flag threaded into the
SAME `_run_inner` + `agents/*` (build-feature rule #1). It is **not** a second pipeline.

The moat argument **inverts** across the switch, so the switch is really about *which
consistency problem we're solving*:

| | **Real story** (our moat) | **AI story** (fiction) |
|---|---|---|
| Media | Real photos/video passthrough, matched, restored | 100% generated — no real media |
| The hard problem | *Protect* real identity + claims | *Manufacture* consistent characters + worlds |
| Identity | The real photo IS the person | Character must be the same across 40 shots |
| World | Real locations | Same palace/forest must recur |
| Governance | Consent/likeness gates central | Relaxed for *invented* people; still ON if a real face is used |
| Our readiness | ~95% (strong) | ~60–70% engine, **missing the hard 30%** |

**Governance nuance (important):** the switch does NOT turn governance off. The consent/
likeness gate keys on *"is this a real named person?"*, not on the mode. Invented characters
(generated, no real ref) need no consent; if an operator uploads a real person's face as a
character even in AI mode, the gate still fires. So AI mode = "governance rarely triggers,"
not "governance disabled."

## 1. What AI/fiction mode needs that we don't have

| Gap | Why it matters for Ramayana/Bible | Today |
|---|---|---|
| **A. Character consistency** across poses/angles/lighting/emotion | The same Rama in 40 dramatic shots | Weak — `gpt-image-1` *edit* ("keep this face"); drifts across big variation |
| **B. World / scene consistency** | Same Ayodhya palace, same forest | **None** (galleri5's "Worlds/Contexts") |
| **C. Pose / action control** | Rama drawing a bow; Hanuman leaping | **None** (prompt-only) |
| **D. Style lock** (art direction) | One look — painterly / anime / 3D | Ad-hoc per shot |
| **E. Multi-character voices** | Distinct voice per character | ✅ have (cast→voice); could add per-char clone |
| **F. Shot breakdown for drama** | Establishing/coverage/action beats | ✅ ~reusable (shot_planner + scene_intelligence) |

## 2. Third-party tools that fill each gap (concrete)

**A. Character consistency — the headline upgrade (replaces gpt-image-1 for identity):**
- **Nano Banana / Gemini 2.5 Flash Image** — strong *multi-reference* character consistency; **we already have it** (`config/models.json` `nano_banana` + the Gemini key). **Lowest-effort, biggest win.**
- **Flux.1 Kontext** (Black Forest Labs, via fal) — in-context editing that holds a subject across edits/scenes. Top-tier for "same character, new scene."
- **IP-Adapter / InstantID / PuLID** (via fal / Replicate) — identity *adapters*: lock a face without training. Good middle ground.
- **LoRA-per-character** (Replicate `flux-dev-lora-trainer` / fal LoRA training) — train a tiny model on ~10–20 canonical images of "our Rama" → rock-solid recurrence. **The gold standard for a recurring fictional character** (and moat-safe — no real person).

**B. Worlds/Contexts** — mostly *our* build: a persistent **style + environment reference**
(a locked reference image + a "world bible" prompt fragment injected into every shot), optionally a **style LoRA**. Reuses the character-sheet mechanism we just built (Phase 4), generalized to places.

**C. Pose/action** — **ControlNet** (pose/depth/edge, via fal/Replicate) for stills; for
motion, **Veo 3 / Kling 2.x** (we have these) handle dramatic action + Veo 3 adds native audio.

**D. Style lock** — a **style reference image** or **style LoRA** applied globally (same seam as B).

**E/F** — reuse what we have (ElevenLabs multi-voice; shot_planner/scene_intelligence).

## 3. The gpt-image-1 upgrade (you're open to it — here's the pick)
Identity generation is hardcoded to OpenAI **`gpt-image-1` edit** in `agents/image_editor.py`
(a single point of failure *and* the weakest identity lock). Recommendation:
1. **Make the edit/identity path pluggable** (a small seam, like our other axes) — `config`
   picks the reference-conditioned model.
2. **Default it to Nano Banana** (we have it) or **Flux Kontext** — both beat gpt-image-1 on
   "same character, new scene." Keep gpt-image-1 as a fallback.
This single change lifts character consistency in **both** modes (real refs + fiction) and
removes the single-vendor risk. Low effort, high payoff — I'd do this first.

## 4. Phased plan (MVP → full) — no code yet
- **P0 — Intake switch + identity upgrade — ✅ SHIPPED.** `story_type` (real|ai) at plan
  time (`new_canvas` + `/api/canvas/plan`, exposed in `public_state`); a **📷 Real / 🎭 AI
  story** selector on the canvas; AI mode hides the real-media folder tools (match/enhance/
  pick/re-match) — everything generates, characters defined on the 👥 sheet. Identity path
  made pluggable, defaulting to **Nano Banana** (live-verified) → gpt-image-1 fallback.
  Consent still keys on real-person, so it rarely fires for invented characters. *(Engine
  needed no change — "no real media matched" already generates.)*
- **P1 — Character-sheet-first (medium).** In AI mode, generate a **canonical character**
  (front + 2–3 expressions) once, then use it as the reference for every shot (reuses the
  Phase-4 character sheet + the new ref model). This is what makes Rama *look like Rama*.
- **P2 — Worlds/Contexts — ✅ SHIPPED (v1, descriptor-based).** `set_world(style, setting)`
  (`/api/canvas/<id>/world` + a 🌍 World bar) stamps a global art-direction + setting onto
  every frame (`world_style`), injected into generation so the whole reel shares one look
  and world. *Next (future): a reference-IMAGE world (a locked establishing image
  conditioning shots) beyond the text descriptor.*
- **P3 — LoRA-per-character + ControlNet (larger).** Train a per-character (and per-style)
  LoRA for epic-grade recurrence; add ControlNet pose control for action beats. The gold
  standard — build when the P0–P2 quality bar isn't enough.

## 5. Honest red-team
- **P0 alone (switch + Nano Banana) gets you ~70→85%** on character consistency for a few-
  minute reel — probably enough to *demo* Ramayana. **LoRA (P3) is what makes it production-
  grade** across a long epic; don't promise epic-grade before P3.
- **Worlds (P2) is the sleeper** — audiences forgive a slightly-off face more than a palace
  that changes every shot. Might rank above P1 depending on the story.
- **Don't build a separate app.** The temptation with "AI mode" is a fork. Resist — it's a
  flag + mode-aware defaults on the one engine, or we double the maintenance (we already pay
  a two-path tax between Story and Canvas).
- **Cost/latency:** LoRA training + ControlNet + premium video is materially pricier/slower
  than a real-media reel. Keep the tier discipline (dev drafts, premium finals) and show the
  cost up front (we already do).
- **This does not dilute the HOB moat** — real mode is untouched; AI mode is a *new market*
  (mythology/brand-fiction/explainer), and LoRA-of-a-fictional-character carries none of the
  real-person authenticity risk that made us say "no LoRA" for HOB.

## 6. One-line recommendation
**Ship P0 first** — the intake switch + a pluggable identity path defaulting to **Nano Banana**
(we already have it). It upgrades character consistency for *both* real and fiction modes,
kills the gpt-image-1 single point of failure, and unlocks the AI-story market without a fork
or a training pipeline. Then P2 (Worlds) and P1 (character-sheet), with P3 (LoRA/ControlNet)
only when the quality bar demands epic-grade recurrence.
