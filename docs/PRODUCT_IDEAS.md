# HOBAILabs — Product Backlog (creative + strategic ideas)

**Created:** 2026-06-19 · parked thinking, not yet scheduled
**Companion plans:** [ROADMAP.md](../ROADMAP.md) (P0–P3 feature roadmap) · [SCALE_PLAN.md](SCALE_PLAN.md) (infra/scale phases) · [BRAND_PLAN.md](BRAND_PLAN.md) (B1 done, B2 here) · [WORK_PLAN.md](WORK_PLAN.md) (storage/matching) · [MARKET_FIT_REVIEW.md](MARKET_FIT_REVIEW.md) (OODA review) · [GAP_BACKLOG.md](GAP_BACKLOG.md) (missing-capabilities register)

This is the consolidated wishlist from two design conversations: (1) the **editor's
composition wishlist** (layout / text-as-design / timing / edit-surface) and (2) the
**strategic ideas** (the "top 7"). It is **not** an equal-priority build queue. Items
already tracked elsewhere are cross-linked, not duplicated. Effort is rough:
**S** <= a few days · **M** ~1-2 wk · **L** = a subsystem.

## Must-Have Strategy Filter

Use this filter before scheduling anything below. The product is strongest as a
thin, opinionated orchestration layer over bought generation/editing primitives,
with deep investment only where HOB has a moat.

### Build deeply: 6 remaining

These are worth serious internal investment because they support the real moat:

1. **`STR-2` real story -> script intake.** The first LLM-assisted editable draft
   flow is in scope now: raw story -> frame script -> human edit -> render. Voice
   memo/transcription can follow.
2. **Better media matching / reusable HOB asset intelligence.** Build the asset
   memory that knows which real photos, clips, generated stills, voices, music, and
   brand kits belong to which story beats.
3. **Director brain improvements.** Deepen story arc, emotional beats, shot rhythm,
   opener quality, and motion grounding. This is the IP layer above commodity model
   calls.
4. **Real-media preservation hardening.** Keep real photos/videos untouched and
   better matched; never quietly AI-regenerate a real person or brand product.
5. **Brand compliance.** Strengthen approval/audit history, claim control,
   disclosure records, mandatories, and version context. AI must not write brand ad
   claims.
6. **Consent/spend governance production hardening.** The current consent ledger,
   spend reservations, restart-safe runs, and approval rows are hardened thin slices,
   not final product governance. Operator identity, ownership, durable project
   records, and stronger audit controls still matter.

### Build lightly: mostly done

These were the small trust helpers and are already implemented, or mostly
implemented, as thin slices: safe-zone, timeline preview, text-card pilot, export
package, posting kit, keyword highlight, and redo motion only.

Remaining work here should be **polish / UX hardening only**, not major roadmap
investment.

### Current thin-slice reality

The current codebase includes useful bridges for `/story-intake`, `/hook-workshop`,
`/caption-variants`, `/render-variants`, consent/spend governance, run persistence,
asset records, and brand approval rows. Treat these as **bridges**, not final product
implementations:

- Story intake is an LLM-assisted editable draft path, not voice memo/transcription
  or a direct-to-render automation.
- Hook workshop returns editable draft hooks, not validated virality predictions.
- Caption variants are placeholders, not translations or dubbing.
- Multi-format variants are descriptors, not a full re-render/export product.
- Governance and approval storage are hardened SQLite thin slices, not final
  multi-operator product governance.

### Export / buy / integrate: about 13 items

Do **not** build these deeply as an internal editing product: `LAY-2` half-screen
layout, `LAY-3` split screen, `LAY-4` PIP, `TXT-2` kicker line, `TXT-3`
comic/boom splash, `TXT-4` emoji/sticker drop, `TXT-5` word-by-word kinetic
captions, `TIM-1` beat-synced cuts, `TIM-2` hold/punch timing, `TIM-3` speed
ramp, `EDIT-2` drag reorder, `EDIT-3` clip trim handles, and Brand `B2-*`
kinetic graphics.

Keep these as very light presets only when they directly support the moat, or hand
off finishing to CapCut/Premiere/Descript/Runway/Submagic-style tools via export
and integrations.

> **Composition insight, now strategy-filtered.** The editor wishlist still points
> toward a generic per-frame layout/overlay model, but that is no longer a deep
> internal product bet by default. `LAY-1` text-card is the real pilot. Anything
> beyond it must either be a tiny preset, an export/integration path, or directly
> required by one of the six Build Deeply items.

---

## 0. Foundation (strategy-filtered: only extract if a moat item needs it)

- [ ] **LAY-0 · Generic per-frame layout / overlay engine** — **L** — *defer deep build; extract only
  from `LAY-1` if a Build Deeply item proves it necessary.*
  A per-frame `layout` model: image region(s) + text region(s) + timing, composited in a
  single ffmpeg pass (overlay / `drawtext` / ASS). Additive `frame["layout"]` and
  `frame["overlays"]` keys (the latter already reserved in [BRAND_PLAN.md §4](BRAND_PLAN.md)).
  If any §1, §2, or Brand `B2` item is ever kept inside HOB, it should be a narrow
  **preset** of this rather than a one-off compositor.
  **Acceptance gate:** `LAY-1` text-card is the pilot. Do not schedule deep `LAY-0` work just to
  unlock editor features; prefer export/integration unless the need supports the six moat areas.
  **No LAY-2/LAY-3/LAY-4/overlays may ship internally until this extraction is justified.**
  *Dep: LAY-1 pilot. Unlocks: rest of §1, §2, B2-*.*

---

## 1. Layout / composition  *(export/buy/integrate by default)*

- [ ] **LAY-1 · Text card pilot** — **S** — **mostly done as a thin slice.** A frame that is just a bold
  statement on a colour field, no image (the "beat" / breather between scenes). Ship the smallest
  real implementation as a pilot. Remaining work is polish and, only if justified, extraction.
- [ ] **LAY-2 · Half-screen + text block** — **S/M** — **export/buy/integrate.** Useful, but do
  not build deeply; keep as a tiny preset only if a real HOB workflow proves it.
- [ ] **LAY-3 · Split screen** — **M** — **export/buy/integrate.** Valuable for before/after or
  two-speaker arcs, but CapCut/Premiere-style tools already own this surface.
- [ ] **LAY-4 · Picture-in-picture (PIP)** — **M** — **export/buy/integrate.** Story-mode twin
  of Brand `B2-3`; avoid deep internal build unless needed for brand compliance evidence.

## 2. Text as a design element  *(caption trust helper done; effects are export/buy/integrate)*

- [ ] **TXT-1 · Keyword highlight in captions** — **S** — **mostly done as a thin slice.** Colour one word/phrase differently.
  Tiny: we already emit per-line ASS tags, so it's a `{\c}` span. **Quick win.**
- [ ] **TXT-2 · Kicker line** — **S** — **export/buy/integrate.** A small secondary text style
  (location/date/name). Keep internal work tiny or hand off to the finishing editor.
- [ ] **TXT-3 · Comic / boom splash** — **M** — **export/buy/integrate.** A word ("BOOM",
  "₹0 -> ₹1Cr") punches in big. This is commodity motion graphics unless tied to compliance.
- [ ] **TXT-4 · Emoji / sticker drop** — **S/M** — **export/buy/integrate.** Place via finishing
  tools unless a brand/governance need requires a controlled preset.
- [ ] **TXT-5 · Word-by-word kinetic captions** — **L** — **export/buy/integrate.** Submagic,
  CapCut, Descript, and similar tools are better places for this unless it becomes core to the
  director brain.

## 3. Timing & rhythm  *(director-brain signal, not an editor clone)*

- [ ] **TIM-1 · Beat-synced cuts** — **M/L** — **export/buy/integrate unless folded into the
  director brain.** The internal value is deciding rhythm, not recreating an NLE beat editor.
- [ ] **TIM-2 · Per-frame hold vs punch** — **M** — **export/buy/integrate.** Keep as a director
  recommendation or simple preset; avoid a deep timing UI.
- [ ] **TIM-3 · Speed ramp** — **M** — **export/buy/integrate.** Commodity edit tool behavior.

## 4. Edit surface  *(thin trust helpers only; finishing belongs elsewhere)*

- [ ] **EDIT-1 · Read-only timeline strip** — **S/M** — **mostly done as a thin slice.** A horizontal strip of frames with
  durations. Editors think horizontally; the UI presents vertically. Cheapest trust win here.
- [ ] **EDIT-2 · Drag-to-reorder frames** — **M** — **export/buy/integrate.** Do not build a
  full timeline editor; export to the editor's preferred tool.
- [ ] **EDIT-3 · Clip in/out trim handles** — **M** — **export/buy/integrate.** Keep any internal
  controls minimal; trimming is mature elsewhere.
- [ ] **EDIT-4 · Redo motion only (keep the still)** — **S** — **mostly done as a thin slice.** Re-roll the animation without
  regenerating the approved image. Extends the per-frame redo we just shipped. **Quick win.**

---

## 5. Strategic bets  *(the "top 7" — beyond the edit surface)*

- [ ] **STR-1 · Caption safe-zone (near-bug, ship first)** — **S** — **mostly done as a thin slice.** Instagram's bottom ~250px
  is covered by its own UI, so bottom captions sit *behind* it on a real phone. Add a safe-zone
  overlay in preview + a smarter raised-bottom default (~320px margin). Quietly improves **every**
  reel. *Pairs with the caption position work already shipped.*
- [ ] **STR-2 · Story → script intake (the missing engine)** — **L** — **Build Deeply.** Paste a 2,000-word story
  (or drop a voice memo / interview audio → transcribe) → AI segments it into a frame-by-frame
  reel script with suggested beats. Widens the funnel 10×; matches how HOB actually sources
  content. **Highest-leverage single bet.** The current `/story-intake` route now creates
  an editable LLM-assisted Format B draft; voice memo/transcription, deeper media matching,
  and richer beat confidence remain future deepening. *Dep: none (upstream of the whole pipeline).*
- [ ] **STR-3a · Posting kit (cheap, no new render)** — **S** — **mostly done as a thin slice.** Generate the Instagram caption +
  hashtags from the already-parsed `Caption:` block (story-mode only — the one place AI copy is
  welcome) and pick a cover/thumbnail frame. **No extra render spend, no real-person risk** → ships
  early on the creative track.
- [ ] **STR-3b · Multi-format + cutdowns (spend ×N)** — **M** — **export/buy/integrate unless a
  specific HOB workflow proves otherwise.** Aspect variants (1:1, 16:9) and
  auto-cutdowns (60s → 15s teaser → 6s hook loop). These **re-render**, so they sit behind the
  commercial gate. Current `/render-variants` returns governed payload descriptors only, not a
  finished multi-format product. *Aspect/reframe overlaps [ROADMAP #9](../ROADMAP.md).*
- [ ] **STR-4 · Multi-language / dubbing** — **M/L** — **not a current deep-build item; integrate
  vendors if pursued.** One story → Hindi / Marathi / Tamil with
  regional voices + auto-translated captions. ~5× reach for an Indian audience from already-rendered
  work. Dubbing/voice-change tooling is already in the stack. The current `/caption-variants`
  route is a labelled placeholder, not translation. *Highest reach-per-effort.*
- [ ] **STR-5 · Hook workshop (frame 1 is a different species)** — **M** — **fold into Director
  Brain only.** Generate 3 alternative
  openers (line + image + motion) and **score them before full spend** using the `virality_predictor`
  tool already in the stack. Fits the test-cheap/finish-expensive philosophy; the one place a
  predictive signal changes a decision. The current `/hook-workshop` route is a no-score draft
  scaffold; do not fake virality scoring. *Dep: lightweight on preview path.*
- [ ] **STR-6a · Lightweight export (one finished run)** — **M** — **mostly done as a thin slice.** Export a single completed run as
  clips + a JSON edit list (and/or CapCut/Premiere XML) so a human finishes the last 10% in their
  tool. **Reuses already-rendered clips → no new spend, no DB** → ships on the creative track.
  Reframes "editors find it complex" — be the **best first-draft machine**, not a second-rate editor.
- [ ] **STR-6b · Full versioned project export** — **defer.** Needs the DB: project history +
  re-editable versions. It is only a must-have when tied to brand compliance/version audit or
  reusable asset intelligence. *Post-DB ([SCALE_PLAN Phase 2](SCALE_PLAN.md)).*
- [ ] **STR-7 · Asset library (the quiet moat)** — **L** — **Build Deeply as reusable HOB asset intelligence.** Store, tag, and reuse generated stills,
  clips, voices, music, and approved brand kits. Each future reel gets cheaper and more on-brand;
  over a year of HOB volume this proprietary library **is** a moat and makes STR-1…6 compound.
  This should include better media matching, not just file storage. *Already scoped in
  [SCALE_PLAN.md Phase 2](SCALE_PLAN.md); needs the DB.*
- [ ] **STR-8 · Brand approval / audit trail** — **M** — **Build Deeply as brand compliance.** A
  shareable preview link for the brand to review + an immutable record of *what they signed off*:
  the approved claims, disclosure, and version. Both a trust feature **and** legal CYA for paid
  deals — under-rated when this was a hobby tool, important now that deals carry legal exposure.
  The current approval rows are a lightweight audit slice; full claim control, disclosure records,
  shareable approval, and version context still need product work. *Full versioning needs the DB
  ([SCALE_PLAN Phase 2](SCALE_PLAN.md)). Pairs with the consent/PII policy (Floor F1).*

---

## 6. Brand B2 — kinetic graphics layer  *(export/buy/integrate unless compliance requires a preset)*

Deferred when B1 shipped. This converges with `LAY-0` / `TXT-5`, but under the must-have
filter it is **not** a deep internal build by default. Prefer export/integration or narrow
brand-compliance presets.

- [ ] **B2-1 · Timed overlay elements** — **export/buy/integrate.** Text, badge, sticker, price callout — with in/out
  animations. *= LAY-0 + TXT-3/TXT-4 in brand styling.*
- [ ] **B2-2 · Per-beat placement UI / overlay timeline** — **M** — **export/buy/integrate.** The operator UI to place and
  time overlays. *Shares EDIT-1 timeline.*
- [ ] **B2-3 · Product PIP / pack-shot overlay** — **M** — **export/buy/integrate.** Brand twin of `LAY-4`. (BRAND_PLAN
  decision #10: full-frame product beats in B1 → PIP in B2.)
- [ ] **B2-4 · Word-by-word VO sync** — **L** — **export/buy/integrate.** Kinetic typography synced to the announcer track.
  *= TXT-5.*
- [ ] **B2-5 · Brand-styled compositor pass** — **export/buy/integrate unless required for claim/disclosure control.** Kit colours/fonts applied across overlays via the
  ffmpeg overlay/`drawtext`/ASS-animation pass. *= LAY-0 rendering, brand theme.*

---

## 7. Governed Sequence — must-have execution order

> **Framing:** *Moat before editor surface. Trust before wow. Reliability before scale.
> Governance before cost-multiplying growth.* Context: **multi-operator, paying brand deals,
> scaling volume** — so the platform floor and moat features are now the critical path.
> Creative helpers can continue as polish, but advanced editing belongs in export/integration
> unless it directly supports one of the six Build Deeply items.

### Track A — Platform floor (critical path; existential risk)
*These are existential the moment one paying brand deal involves real credits or a real person.
Owner + a one-line "done" each — lightweight, not a process doc.*

- [ ] **F1 · Consent / likeness / content-rights policy.** *Thin slice exists; must-have remains
  production governance.* Done-final = stored consent per subject, operator identity, data handling,
  and rights checklist enforced before paid/real-person renders. Pairs with `STR-8`.
- [ ] **F2 · Spend governance — per-project cost attribution + hard caps.** *Hardened thin slice
  exists; must-have remains production governance.* Done-final = project-owned caps, operator
  attribution, actual/estimated vendor event separation, and durable reporting.
- [ ] **F3 · Restart-safe runs.** *Thin slice exists.* Done-final = an in-flight render survives
  restart with enough durable project/run state for production operators.
- [ ] **F4 · Right-sized tests** — money-and-rights paths first (pricing/attribution,
  consent/disclosure gating, model routing, real-media preservation); not exhaustive coverage of
  stable creative code. *Hygiene, not existential.*
- [ ] **F5 · Operator identity / who-did-what** — phase in *as operator count grows* ([SCALE_PLAN](SCALE_PLAN.md) Phase 1); light at 2–3 trusted people, required at 10+.

### Track B — Creative wins (mostly done; polish only)
*Ship small trust helpers, but do not let this become a general video editor.*

- [ ] **B-now:** `STR-1` caption safe-zone · `EDIT-1` timeline strip · `EDIT-4`
  redo-motion-only · `TXT-1` keyword highlight · `STR-3a` posting kit. **Status: thin slices
  implemented; remaining work is polish/UX hardening.**
- [ ] **B-layout:** `LAY-1` text-card pilot. **Status: thin slice implemented.** Do not continue
  into `LAY-2/3/4`, `TXT-2/3/4/5`, or Brand `B2-*` as deep internal work; export/buy/integrate.
- [ ] **B-export:** `STR-6a` lightweight export of a finished run (clips + JSON edit list).
  **Status: thin slice implemented.** Improve export formats only if it helps handoff to editors.

### The gate (where the tracks meet) — two tiers, don't conflate them

- **Hard commercial gate — `F1` consent + `F2` spend governance.** A feature may not ship *at all*
  to paid / external / real-person use until consent records + per-project spend caps/ledger are live.
- **Production-readiness floor — `F3` restart-safety + `F4` tests.** Gated features may pilot once the
  commercial gate clears, but must not become **default or high-volume** until restart-safety + the
  money/rights tests land. *(This resolves the earlier F1+F2 vs F3+F4 ambiguity: commercial gate =
  may it exist; readiness floor = may it go default/at scale.)*
- **`F5` identity** grows with operator count — light at 2–3 trusted people, required at 10+.

Behind the gate: scaled `STR-2` real story intake, any production multi-language/dubbing, any hook
workshop that creates paid image/motion variants, and any multi-format/cutdown workflow that
re-renders. Today these are editable draft/payload routes, not direct-to-render products.

### Must-have order from here
1. `STR-2` real story -> script intake.
2. Better media matching / reusable HOB asset intelligence (`STR-7` plus matching logic).
3. Director brain improvements: story arc, shot rhythm, opener quality, motion grounding.
4. Real-media preservation hardening.
5. Brand compliance (`STR-8` full approval/audit, claim control, disclosure records).
6. Consent/spend governance production hardening (`F1/F2/F3/F4/F5` beyond thin slices).

### After the DB lands ([SCALE_PLAN](SCALE_PLAN.md) Phase 2)
Prioritize `STR-7` asset intelligence and `STR-8` full brand approval/audit trail. `STR-6b`
full versioned project export is useful only if it supports compliance/versioning or editor
handoff. Brand `B2-*` kinetic layer remains export/buy/integrate unless narrowed to compliance
presets.

**One-bet pick:** `STR-2` (real story -> script) — sits behind the commercial gate and should
ship with consent + ledger.
**Polish-only pick:** improve the already-shipped light helpers only where they reduce operator
friction.

---

## 8. Non-goals / cautions / standing risks

- **Wishlist, not build queue.** A checkbox below means "captured idea," not "should be built
  deeply." The six Build Deeply items are the must-have internal work; everything else must earn
  its place as a light helper, export, or integration.
- **No CapCut clone.** Don't design `LAY-0` speculatively *or* build §1/§2/B2 as a parallel
  editor. `LAY-1` exists as a text-card trust helper; beyond that, prefer handoff to mature tools
  unless a preset directly supports story intake, media matching, director brain, real-media
  preservation, brand compliance, or governance.
- **Reliability before scale.** `STR-7`, Brand `B2` placement persistence, and `STR-6b` versioned
  export all want the durable store — don't start broad versions before [SCALE_PLAN Phase 0/2](SCALE_PLAN.md).
  And a gated feature may *pilot* once the commercial gate (`F1`+`F2`) clears, but must not become
  **default or high-volume** before the production-readiness floor (`F3` restart-safety + `F4` tests).
- **Governance before cost-multiplying growth.** Intake brings volume; `STR-3b` multi-format,
  `STR-4` multi-language, and `STR-5` hook workshop multiply spend. Track A's `F2` spend caps +
  ledger + estimator coverage must arrive *with* those features (they're behind the commercial gate),
  not after — or unit economics break and bills can't be attributed.
- **Drafts are not final products.** `/story-intake`, `/hook-workshop`, `/caption-variants`,
  `/render-variants`, lightweight approvals, and SQLite governance are useful bridges, but they
  should not be sold internally as completed moat features.
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
