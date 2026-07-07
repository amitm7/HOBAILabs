# PRESENTER_PLAN — podcast/presenter format, scope registry, education scope (S29)

**Status:** AGREED (2026-07-08) — planned, **not started**. This doc is the decision
record from the owner↔agent L99 debate; Phase 0 gates all build work.

---

## 0. The ask (owner, 2026-07-08 → ledger S29)

Given a script + the owner's photo, canvas should produce a **podcast-style episode**
(reference: an AI tech-news YouTube video carrying YouTube's own *"Made with AI —
sounds or visuals were altered or fully generated"* label): the presenter — the
owner's face, animated — speaks the script to camera with lip-sync, and the edit
cuts away to **corresponding B-roll images** beat by beat. No recording by the owner.
Second part of the ask: the canvas type selector (today Story | Commerce) should
grow — podcast, education — **with per-type controls**.

## 1. Decision record (debated + locked 2026-07-08)

| Decision | Call | Note |
|---|---|---|
| First user | **Owner's own channel** | Tool-grade, not product-grade. Validate on real published episodes before any productization. |
| Aspect | **16:9 up front** | Owner's call, against the agent's 9:16-first recommendation — coherent because the target is the owner's YouTube channel. 9:16 stays the default for all existing doors. |
| Education scope | **Bundled** | Once the registry exists it's a prompt pack + one entry. |
| Avatar vendors | **Dual-tier from start** | Hedra = dev/preview tier; Kling Avatar v2 **or** OmniHuman 1.5 (via fal) = prod tier. Prod pick decided by Phase-0 evidence, not by spec sheet. |
| Shape | **Not a new front door** | New *scopes* in `shot_planner` + a `presenter` value on the S28 form axis + run-level format flags. Never fork (CLAUDE.md §3). |

**Rejected options:** (a) bolt-on third hardcoded scope — saves a day, taxes every
future scope; (b) full 16:9 long-form "podcast studio" v1 — premature before quality
evidence; (c) buy-only (HeyGen) — loses the edit; its truth is absorbed instead:
**wrap** commodity avatar models behind our lipsync seam, never build avatar tech.

## 2. Prior-art check (hard rule)

Script→avatar+B-roll is a commoditized category (HeyGen, Argil, Descript). What is
rare and ours: the director-brain **beat-matched B-roll edit**, one engine
(cost/cache/governance/provenance), real-media handling. Build = the edit.
Wrap = the avatar model. Adopt-only = nothing (the edit is the product).

## 3. What already exists (verified in code, 2026-07-08)

- **Lip-sync path SHIPPED:** `agents/lipsync_coordinator.py` → `agents/hedra.py`
  (character-3, takes `aspect_ratio`), `agents/synclabs.py` (video source),
  ElevenLabs TTS, audio-driven shot duration (no 5s cap on lipsync clips),
  assembler mixed-mode (`_assemble_with_lipsync`: speech 100%, music ducked 10%).
  Opt-in: `[lipsync: yes]` tag or 🎙 UI checkbox.
- **Scope seam:** `agents/shot_planner.py` — scope = system prompt + flags;
  hardcoded to 2 (`general` | `commerce`) at the validation (~L183) and the prompt
  pick (~L192). `canvas.html` `#scope` select feeds it.
- **S28 form axis:** `canvas_run.shot_form` / `story_form` / `form_warnings`
  (dialogue | narration | silent → cinematic | narrated | mixed). Detect→declare,
  per-shot override.
- **Aspect:** `clip_builder` derives aspect from dims (L334, L520); Hedra takes an
  aspect param. Hard 1080×1920 lives only in assembler normalization (L133, L215)
  + still-gen sizes + caption safe-areas.
- **Vendor market (July 2026):** Hedra ~$2–3.6/min (720p, integrated); Kling Avatar
  v2 ~$3.4/min std (1080p/48fps, multi-minute, fal); OmniHuman 1.5 ~$9.6/min
  (realism leader, 60s/run, fal); TTS ≈ pennies. Runway Act-Two requires a driving
  performance video → out of scope by definition of the ask. Veo can't take our TTS
  track → voice drift → out.

## 4. Phases

### Phase 0 — Golden-face spike ⟨S, ~half day, <$10⟩ — GATES EVERYTHING
- Owner photo + 30s tech-news script → 3 presenter segments at 16:9 on each of:
  (a) existing Hedra path, (b) Kling Avatar v2 one-off via fal, (c) OmniHuman 1.5
  one-off via fal. **No feature code** — scratch calls only.
- Judge: identity fidelity, lip-sync accuracy, uncanny factor, seam behavior when
  two segments are cut back-to-back.
- Output: prod-vendor pick + a golden reference clip checked into the run library.
- **KILL:** if no vendor is acceptable on the owner's real face after 2 attempts
  each → stop; capability remains per-shot lip-sync only (T7 track); this plan closes.

### Phase 1 — Enablers (behavior-preserving) ⟨1a: S · 1b: M⟩
- **1a Scope registry:** scope → `{system_prompt, planner_flags, ui_controls}`
  (registry in `shot_planner` or `config/scopes.json`, in the spirit of the other
  pluggable seams). Migrate `general`/`commerce`; validation + prompt pick read the
  registry. *Acceptance: zero output diff on existing scopes (cache keys unchanged).*
- **1b Aspect parameterization:** run-level `aspect` (default **9:16**, unchanged);
  assembler target dims from the run; still-gen sizes; caption safe-areas; Hedra
  aspect passthrough. *Acceptance: existing 9:16 behavior identical; a 16:9 test
  run assembles clean end-to-end.*

### Phase 2 — `podcast` scope, 16:9 ⟨L⟩
- **Planner grammar:** alternate **presenter beats** (to-camera line,
  `presenter_beat=true`) with **B-roll beats** (matching visual, VO continues).
  Rule: never two presenter beats adjacent — hides inter-clip seams by construction.
- Presenter beats auto-set: `lipsync=true`, source = presenter photo, voice =
  owner `voice_id` (pre-cloned ElevenLabs PVC, existing seam).
- **S28 extension:** `presenter` shot/story form value + verdict badge;
  `form_warnings`: podcast scope without presenter photo / voice / lipsync key.
- **Dual-tier routing in `lipsync_coordinator`:** dev tier → Hedra; prod tier →
  Phase-0 winner via fal. Tier follows the existing dev/prod quality switch.
- **UI (registry-driven controls):** presenter photo picker, voice, talking-head
  ratio, aspect (16:9 | 9:16).
- **Discipline (T7-derived, adapted):** mandatory **dev-tier full-episode preview
  before any prod render**; per-episode spend cap via the existing 💰 estimate +
  spend gates. (T7's 3-beat cap does NOT transfer — a podcast is presenter-heavy
  by design; the preview gate + spend cap replace it.)
- **Disclosure/provenance:** presenter episodes are synthetic-performance media of
  a real person → provenance record + label per PROVENANCE_PLAN; publishing
  checklist includes ticking YouTube's *altered/synthetic content* disclosure
  (the reference video itself carries this label). Owner's own face → no consent
  gate (canvas AI-likeness decision stands), label + provenance only.
- *Acceptance: one 2–3 min episode the owner judges publishable; dev preview ≤$6;
  prod render within the spend cap (~$10–25).*

### Phase 3 — `education` scope ⟨S⟩
- Registry entry + prompt pack: hook → concept → example → recap beat grammar;
  controls: topic, audience level. Everything else reused. A scope earns a dropdown
  slot only with a **distinct beat grammar** — topic flavors are briefs, not scopes.

### Phase 4 — Productization (explicitly NOT now)
- Triggers (all required): ≥3 real episodes published from Phase 2, AND owner
  decides to make it a Veristory-facing surface. Then: UI polish, GUIDE/onboarding,
  script importer, chapters, long-form conveniences.

## 5. Cost model (16:9, ~3 min episode, ~50% presenter screen-time)

| Stage | Vendor | Est. |
|---|---|---|
| Dev preview (full episode) | Hedra ~1.5 min + stills + TTS | **≤ $6** |
| Prod render | Kling Avatar ≈ $5–6 · OmniHuman ≈ $14–15 (presenter) + B-roll | **~$10–25** |

## 6. Kill / reversal conditions

- **Phase 0 kill** (above) — the plan's primary fuse.
- Post-ship: owner cuts all presenter beats after preview on **2 consecutive
  episodes** → deprioritize (inherits T7's criterion).
- Vendor swap is a `lipsync_coordinator` seam change, never a rewrite.
- Revisit the plan if: a model ships true multi-minute single-take at ≤$3/min
  1080p (collapses the segment-seam constraint), or fal pricing moves >2×.

## 7. Docs-sync checklist (per phase — hard gate)

- `docs/HLD.md`: scope registry, presenter form, aspect parameter.
- `docs/LLD.md`: registry schema; new frame keys (`presenter_beat`, run `aspect`);
  coordinator tier routing.
- `GUIDE.md` + `docs/OPERATOR_GUIDE.html`: podcast/education scopes + controls.
- `docs/L99_ARCH_PLAN.md`: S29 ledger row status.
- `.env.example` / `config/*`: any new keys or registry file.
- This plan: tick shipped items per phase.
