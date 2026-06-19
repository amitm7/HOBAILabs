# HOBAILabs — Product Backlog (creative + strategic ideas)

**Created:** 2026-06-19 · parked thinking, not yet scheduled
**Companion plans:** [ROADMAP.md](../ROADMAP.md) (P0–P3 feature roadmap) · [SCALE_PLAN.md](SCALE_PLAN.md) (infra/scale phases) · [BRAND_PLAN.md](BRAND_PLAN.md) (B1 done, B2 here) · [WORK_PLAN.md](WORK_PLAN.md) (storage/matching)

This is the consolidated wishlist from two design conversations: (1) the **editor's
composition wishlist** (layout / text-as-design / timing / edit-surface) and (2) the
**strategic ideas** (the "top 7"). Items already tracked elsewhere are cross-linked, not
duplicated. Effort is rough: **S** ≤ a few days · **M** ~1–2 wk · **L** = a subsystem.

> **The unifying insight (read first).** Almost every "editor" item below is the same
> shift: *from "one frame = one full-screen shot" to "one frame = a small composition the
> editor can shape."* The end state is **one generic per-frame layout/overlay system** (image
> regions + text regions + timing) where split-screen, half+text, PIP, comic-splash,
> text-cards, and kinetic callouts are all **presets** — exactly how brand mode reused the
> engine instead of forking. **But don't design `LAY-0` speculatively.** Ship the smallest
> real preset first (`LAY-1` text-card pilot), then **extract `LAY-0` from it**, then unlock
> the rest. Hard gate: **no LAY-2/3/4/overlays until `LAY-0` has been extracted.** This avoids
> both failure modes — speculative over-abstraction *and* a pile of one-off compositor hacks.
> See §7 for the governed order.

---

## 0. Foundation (the keystone — but extracted from a real preset, not built speculatively)

- [ ] **LAY-0 · Generic per-frame layout / overlay engine** — **L** — *extract from the `LAY-1`
  pilot; do **not** build first.*
  A per-frame `layout` model: image region(s) + text region(s) + timing, composited in a
  single ffmpeg pass (overlay / `drawtext` / ASS). Additive `frame["layout"]` and
  `frame["overlays"]` keys (the latter already reserved in [BRAND_PLAN.md §4](BRAND_PLAN.md)).
  Everything in §1, §2, and Brand `B2` becomes a **preset** of this.
  **Acceptance gate:** ship `LAY-1` (text card) as a pilot first; once it exists and is used,
  refactor it into `LAY-0`. **No LAY-2/LAY-3/LAY-4/overlays may ship until this extraction is done.**
  *Dep: LAY-1 pilot. Unlocks: rest of §1, §2, B2-*.*

---

## 1. Layout / composition  *(presets of LAY-0)*

- [ ] **LAY-1 · Text card pilot, then extract LAY-0** — **S** — a frame that is just a bold
  statement on a colour field, no image (the "beat" / breather between scenes). Ship the smallest
  real implementation **as a pilot**, then extract the generic `LAY-0` model from it. **Acceptance
  condition:** do not start LAY-2/3/4 until that extraction is complete (see `LAY-0`).
- [ ] **LAY-2 · Half-screen + text block** — **S/M** — image on top half, bold caption on a
  solid colour band below. On-trend, very legible. (The "comic-splash" instinct, productized.)
- [ ] **LAY-3 · Split screen** — **M** — two photos side-by-side or top/bottom (before/after,
  then/now, two speakers). High value for HOB arcs ("the farm" | "the ramp").
- [ ] **LAY-4 · Picture-in-picture (PIP)** — **M** — small inset (reaction face / product)
  over a main shot. Story-mode twin of Brand `B2-3`. *Dep: LAY-0.*

## 2. Text as a design element  *(captions are the floor; callouts are the ceiling)*

- [ ] **TXT-1 · Keyword highlight in captions** — **S** — colour one word/phrase differently.
  Tiny: we already emit per-line ASS tags, so it's a `{\c}` span. **Quick win.**
- [ ] **TXT-2 · Kicker line** — **S** — a small secondary text style (location/date/name, e.g.
  *"Jaipur, 2019"*) alongside the main caption.
- [ ] **TXT-3 · Comic / boom splash** — **M** — a word ("BOOM", "₹0 → ₹1Cr") punches in big,
  rotated, impact-styled, on one beat. *Dep: LAY-0 overlay + simple in-anim.*
- [ ] **TXT-4 · Emoji / sticker drop** — **S/M** — place an emoji or sticker asset on a frame.
- [ ] **TXT-5 · Word-by-word kinetic captions** — **L** — typography that pops per word, synced
  to VO. **Same subsystem as Brand `B2-1/B2-4`** — build once, expose in both modes.

## 3. Timing & rhythm  *(what separates "AI slideshow" from "edited reel")*

- [ ] **TIM-1 · Beat-synced cuts** — **M/L** — tap-tempo or auto-detect music beats; snap each
  cut to the nearest beat. Highest *wow*. *Already in [ROADMAP #10](../ROADMAP.md); foundation exists via `effective_timecodes`.*
- [ ] **TIM-2 · Per-frame hold vs punch** — **M** — "freeze for impact, then whip to next"; a
  punch-in-on-the-beat toggle. *Dep: TIM-1 for beat awareness.*
- [ ] **TIM-3 · Speed ramp** — **M** — slow-mo a moment / speed through a montage.

## 4. Edit surface  *(kills the "black box / I think horizontally" complaint)*

- [ ] **EDIT-1 · Read-only timeline strip** — **S/M** — a horizontal strip of frames with
  durations. Editors think horizontally; the UI presents vertically. Cheapest trust win here.
- [ ] **EDIT-2 · Drag-to-reorder frames** — **M** — today order = script order only.
- [ ] **EDIT-3 · Clip in/out trim handles** — **M** — visual scrubber for `video_start_sec`
  plus an out-point (we have the in-point already).
- [ ] **EDIT-4 · Redo motion only (keep the still)** — **S** — re-roll the animation without
  regenerating the approved image. Extends the per-frame redo we just shipped. **Quick win.**

---

## 5. Strategic bets  *(the "top 7" — beyond the edit surface)*

- [ ] **STR-1 · Caption safe-zone (near-bug, ship first)** — **S** — Instagram's bottom ~250px
  is covered by its own UI, so bottom captions sit *behind* it on a real phone. Add a safe-zone
  overlay in preview + a smarter raised-bottom default (~320px margin). Quietly improves **every**
  reel. *Pairs with the caption position work already shipped.*
- [ ] **STR-2 · Story → script intake (the missing engine)** — **L** — paste a 2,000-word story
  (or drop a voice memo / interview audio → transcribe) → AI segments it into a frame-by-frame
  reel script with suggested beats. Widens the funnel 10×; matches how HOB actually sources
  content. **Highest-leverage single bet.** *Dep: none (upstream of the whole pipeline).*
- [ ] **STR-3a · Posting kit (cheap, no new render)** — **S** — generate the Instagram caption +
  hashtags from the already-parsed `Caption:` block (story-mode only — the one place AI copy is
  welcome) and pick a cover/thumbnail frame. **No extra render spend, no real-person risk** → ships
  early on the creative track.
- [ ] **STR-3b · Multi-format + cutdowns (spend ×N)** — **M** — aspect variants (1:1, 16:9) and
  auto-cutdowns (60s → 15s teaser → 6s hook loop). These **re-render**, so they sit behind the
  commercial gate. *Aspect/reframe overlaps [ROADMAP #9](../ROADMAP.md).*
- [ ] **STR-4 · Multi-language / dubbing** — **M/L** — one story → Hindi / Marathi / Tamil with
  regional voices + auto-translated captions. ~5× reach for an Indian audience from already-rendered
  work. Dubbing/voice-change tooling is already in the stack. *Highest reach-per-effort.*
- [ ] **STR-5 · Hook workshop (frame 1 is a different species)** — **M** — generate 3 alternative
  openers (line + image + motion) and **score them before full spend** using the `virality_predictor`
  tool already in the stack. Fits the test-cheap/finish-expensive philosophy; the one place a
  predictive signal changes a decision. *Dep: lightweight on preview path.*
- [ ] **STR-6a · Lightweight export (one finished run)** — **M** — export a single completed run as
  clips + a JSON edit list (and/or CapCut/Premiere XML) so a human finishes the last 10% in their
  tool. **Reuses already-rendered clips → no new spend, no DB** → ships on the creative track.
  Reframes "editors find it complex" — be the **best first-draft machine**, not a second-rate editor.
- [ ] **STR-6b · Full versioned project export** — needs the DB: project history + re-editable
  versions. *Post-DB ([SCALE_PLAN Phase 2](SCALE_PLAN.md)).*
- [ ] **STR-7 · Asset library (the quiet moat)** — **L** — store, tag, and reuse generated stills,
  clips, voices, music, and approved brand kits. Each future reel gets cheaper and more on-brand;
  over a year of HOB volume this proprietary library **is** a moat and makes STR-1…6 compound.
  *Already scoped in [SCALE_PLAN.md Phase 2](SCALE_PLAN.md); needs the DB. Pure infra dependency.*
- [ ] **STR-8 · Brand approval / audit trail** — **M** — ⬆ **bumped (paying-brand context).** A
  shareable preview link for the brand to review + an immutable record of *what they signed off*:
  the approved claims, disclosure, and version. Both a trust feature **and** legal CYA for paid
  deals — under-rated when this was a hobby tool, important now that deals carry legal exposure.
  *Lightweight sign-off record can start early; full versioning needs the DB ([SCALE_PLAN Phase 2](SCALE_PLAN.md)).
  Pairs with the consent/PII policy (Floor F1).*

---

## 6. Brand B2 — kinetic graphics layer  *(from [BRAND_PLAN.md §6](BRAND_PLAN.md))*

Deferred when B1 shipped. **Converges with LAY-0 / TXT-5 — build the generic engine once.**

- [ ] **B2-1 · Timed overlay elements** — text, badge, sticker, price callout — with in/out
  animations. *= LAY-0 + TXT-3/TXT-4 in brand styling.*
- [ ] **B2-2 · Per-beat placement UI / overlay timeline** — **M** — the operator UI to place and
  time overlays. *Shares EDIT-1 timeline.*
- [ ] **B2-3 · Product PIP / pack-shot overlay** — **M** — brand twin of `LAY-4`. (BRAND_PLAN
  decision #10: full-frame product beats in B1 → PIP in B2.)
- [ ] **B2-4 · Word-by-word VO sync** — **L** — kinetic typography synced to the announcer track.
  *= TXT-5.*
- [ ] **B2-5 · Brand-styled compositor pass** — kit colours/fonts applied across overlays via the
  ffmpeg overlay/`drawtext`/ASS-animation pass. *= LAY-0 rendering, brand theme.*

---

## 7. Governed Sequence — two parallel tracks, one real gate

> **Framing:** *Trust before wow. Reliability before scale. One real preset before abstraction.
> Governance before cost-multiplying growth.* Context: **multi-operator, paying brand deals, scaling
> volume** — so the platform floor is now on the critical path. But run it as **two parallel tracks**,
> not a six-step waterfall: the creative wins don't touch money or rights, so they ship *alongside* the
> floor. There is exactly **one real gate** — where a product feature touches spend or real people.

### Track A — Platform floor (critical path; existential risk)
*These are existential the moment one paying brand deal involves real credits or a real person.
Owner + a one-line "done" each — lightweight, not a process doc.*

- [ ] **F1 · Consent / likeness / content-rights policy.** *Done = a stored consent record per subject
  + a rights checklist enforced before any brand render.* Pairs with `STR-8`.
- [ ] **F2 · Spend governance — per-project cost attribution + hard caps.** *Done = cost ledger keyed
  to project + a per-project ceiling that blocks spend.* Wiring on top of `pricing.estimate()`.
- [ ] **F3 · Restart-safe runs.** *Done = an in-flight render survives a server restart* ([SCALE_PLAN](SCALE_PLAN.md) Phase 0 minimum).
- [ ] **F4 · Right-sized tests** — money-and-rights paths first (pricing/attribution, consent/disclosure
  gating, model routing); not exhaustive coverage of stable creative code. *Hygiene, not existential.*
- [ ] **F5 · Operator identity / who-did-what** — phase in *as operator count grows* ([SCALE_PLAN](SCALE_PLAN.md) Phase 1); light at 2–3 trusted people, required at 10+.

### Track B — Creative wins (parallel; no gating — touches no money or rights)
*Ship continuously to keep velocity and morale; every item held to the one-click / progressive-disclosure
ease-of-use bar (simplify as you add power).*

- [ ] **B-now (days each):** `STR-1` caption safe-zone · `EDIT-1` timeline strip · `EDIT-4` redo-motion-only · `TXT-1` keyword highlight · `STR-3a` posting kit (caption/hashtags/cover).
- [ ] **B-layout (from one real case):** `LAY-1` text-card pilot → **extract `LAY-0`** → *then* `LAY-2` half+text · `LAY-3` split · `LAY-4` PIP · `TXT-2` kicker · `TXT-3` boom splash. **Hard rule: no LAY-2/3/4/overlays before `LAY-0` is extracted.**
- [ ] **B-export:** `STR-6a` lightweight export of a finished run (clips + JSON edit list) — reuses rendered clips, no new spend, no DB.

### ⛓ The gate (where the tracks meet) — two tiers, don't conflate them

- **Hard commercial gate — `F1` consent + `F2` spend governance.** A feature may not ship *at all*
  to paid / external / real-person use until consent records + per-project spend caps/ledger are live.
- **Production-readiness floor — `F3` restart-safety + `F4` tests.** Gated features may pilot once the
  commercial gate clears, but must not become **default or high-volume** until restart-safety + the
  money/rights tests land. *(This resolves the earlier F1+F2 vs F3+F4 ambiguity: commercial gate =
  may it exist; readiness floor = may it go default/at scale.)*
- **`F5` identity** grows with operator count — light at 2–3 trusted people, required at 10+.

Behind the gate: `STR-2` story→script intake (volume) · `STR-3b` multi-format + cutdowns (spend ×N) ·
`STR-4` multi-language / dubbing (spend ×N **and** likeness) · `STR-5` hook workshop (spend ×3).

### After the DB lands ([SCALE_PLAN](SCALE_PLAN.md) Phase 2)
`STR-7` asset library · `STR-8` brand approval/audit trail (full versioning) · `STR-6b` full versioned
project export · Brand `B2-*` kinetic layer · collaboration.

**One-bet pick:** `STR-2` (story→script) — sits behind the commercial gate; ships *with* consent + ledger.
**Tiny-first pick:** `STR-1` (safe-zone) — creative track, nearly free, quietly degrading every reel already shipped.

---

## 8. Non-goals / cautions / standing risks

- **One real preset before abstraction.** Don't design `LAY-0` speculatively *or* build §1/§2/B2
  as one-off hacks. Pilot `LAY-1`, extract `LAY-0`, then presets. (Same discipline as "one engine,
  many front doors.")
- **Reliability before scale.** `STR-7`, Brand `B2` placement persistence, and `STR-6b` versioned
  export all want the durable store — don't start them before [SCALE_PLAN Phase 0/2](SCALE_PLAN.md).
  And a gated feature may *pilot* once the commercial gate (`F1`+`F2`) clears, but must not become
  **default or high-volume** before the production-readiness floor (`F3` restart-safety + `F4` tests).
- **Governance before cost-multiplying growth.** Intake brings volume; `STR-3b` multi-format,
  `STR-4` multi-language, and `STR-5` hook workshop multiply spend. Track A's `F2` spend caps +
  ledger + estimator coverage must arrive *with* those features (they're behind the commercial gate),
  not after — or unit economics break and bills can't be attributed.
- **Consent / PII / content-rights is a policy gate, not a feature.** HOB's raw material is real
  people's faces, voices, and stories; dubbing synthesizes a real person's words in new languages;
  brand deals carry legal exposure; lip-sync media transits an external CDN (HLD privacy boundary).
  A consent + data-handling policy must exist before `STR-2` scales intake and before `STR-4` dubbing.
- **Vendor concentration (watch).** Suno runs via an *unofficial* wrapper; multi-language leans harder
  on ElevenLabs/dubbing. Keep a real fallback on every critical axis (image/video/voice/music).
- **No success metric yet (watch).** We optimize output but have no post-publish performance loop.
  Pair `STR-5`'s pre-spend prediction with at least a manual "did it perform?" signal eventually.
- **Keep AI out of ad copy** (BRAND_PLAN §5). `STR-3`'s generated IG caption is **story-mode only**;
  brand on-screen/spoken copy stays operator-supplied.
